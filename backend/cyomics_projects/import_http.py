"""Default-off API004 boundary. No connections, workers or file reads on import."""
import asyncio
from contextvars import ContextVar
from datetime import datetime
import math
import re
import threading
import time
from urllib.parse import unquote
from starlette.requests import Request
from starlette.responses import JSONResponse
from .identity import jwt_payload
from .import_persistence import (ImportFault, SourceRef, REPORT_MAX_BYTES, CONFIG_MAX_BYTES,
                                 canonical_json, configuration, decode_version, digest, version_key)
from .import_repository import ImportRepository
from .import_service import ImportService, CooperativeImportRunner
from .settings import Budget, Fault, consume_task
from .validation import canonical_uuid, strict_json

MAX_SUCCESS = 1114112
MAX_ERROR = 4096
PUBLIC_ERROR_SEND_SECONDS = 1.0
SECURITY = {'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'}
ERRORS = {400:{'invalid_import_request'},401:{'unauthenticated'},403:{'auth_origin_rejected'},
          404:{'project_not_found','file_not_found','dataset_version_not_found'},405:{'method_not_allowed'},
          409:{'project_context_changed'},413:{'import_report_too_large','import_request_too_large'},415:{'unsupported_media_type'},
          422:{'txt_import_failed'},429:{'import_busy','import_rate_limited'},
          503:{'import_unavailable','import_executor_unavailable','import_source_integrity_failed','parser_release_mismatch',
               'import_persisted_report_invalid','import_control_failed'},504:{'import_timeout','import_result_unknown'}}
ISSUES = frozenset({'INVALID_SOURCE_REF','INVALID_LIMITS','INVALID_CONTROL','CONTROL_FAILED','INPUT_READ_FAILED','INVALID_CHUNK',
                   'SOURCE_INTEGRITY_MISMATCH','INVALID_UTF8','NUL_BYTE','EMPTY_FILE','TXT_HEADER_REQUIRED',
                   'TXT_PROTEIN_IDS_HEADER_COUNT','TXT_ROW_WIDTH','TXT_QUOTE_SYNTAX','RESOURCE_LIMIT','CANCELLED'})
LIMIT_KEYS = {'max_bytes','max_records','max_columns','max_cells','max_field_units','max_member_occurrences','max_id_units'}
UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
PATH = re.compile(rb'/api/v1/uploads/('+UUID.encode()+rb')/complete')


def owns_import(scope):
    path = scope.get('path','')
    if not isinstance(path,str):return False
    return re.sub('/+','/',unquote(path)).startswith('/api/v1/uploads')


def import_route(scope):
    raw = scope.get('raw_path',b'')
    match = PATH.fullmatch(raw) if isinstance(raw,bytes) else None
    if match is None or scope.get('path') != raw.decode('ascii') or scope.get('root_path',''):
        raise ImportFault(400,'invalid_import_request')
    method = scope.get('method')
    if method not in ('POST','GET'):raise ImportFault(405,'method_not_allowed')
    query = scope.get('query_string',b'')
    result = {'method':method,'file_id':match[1].decode(),'project_id':None,'version_id':None}
    if method == 'POST':
        if query:raise ImportFault(400,'invalid_import_request')
    else:
        values = {}
        if type(query) is not bytes or len(query)>160:raise ImportFault(400,'invalid_import_request')
        for part in query.split(b'&'):
            pieces = part.split(b'=')
            if len(pieces)!=2 or pieces[0] not in (b'project_id',b'version_id') or pieces[0] in values:
                raise ImportFault(400,'invalid_import_request')
            try:value=pieces[1].decode('ascii')
            except UnicodeError:raise ImportFault(400,'invalid_import_request') from None
            if not canonical_uuid(value):raise ImportFault(400,'invalid_import_request')
            values[pieces[0]]=value
        if b'project_id' not in values:raise ImportFault(400,'invalid_import_request')
        result.update(project_id=values[b'project_id'],version_id=values.get(b'version_id'))
    return result


def headers(scope,name):
    return [value.decode('latin1') for key,value in scope.get('headers',[]) if key.lower()==name.encode()]


def validate_headers(scope,method):
    def one(name):
        values=headers(scope,name)
        if len(values)>1:raise ImportFault(403 if name in ('host','origin','sec-fetch-site') else 400,'auth_origin_rejected' if name in ('host','origin','sec-fetch-site') else 'invalid_import_request')
        return values[0] if values else None
    if one('host')!='127.0.0.1:57430':raise ImportFault(403,'auth_origin_rejected')
    origin=one('origin')
    if (origin is not None and origin!='http://127.0.0.1:4206') or method=='POST' and (origin is None or one('x-cyomics-request')!='1'):
        raise ImportFault(403,'auth_origin_rejected')
    if one('sec-fetch-site') not in (None,'same-origin'):raise ImportFault(403,'auth_origin_rejected')
    for name in ('cookie','x-user-id','x-owner-id','x-cyomics-account-context'):
        if headers(scope,name):raise ImportFault(403,'auth_origin_rejected')
    if one('content-encoding') not in (None,'identity'):raise ImportFault(415,'unsupported_media_type')
    if method=='POST' and (one('content-type') or '').lower() not in ('application/json','application/json; charset=utf-8'):
        raise ImportFault(415,'unsupported_media_type')
    length,transfer=one('content-length'),one('transfer-encoding')
    if transfer not in (None,'chunked') or transfer is not None and (length is not None or method=='GET'):
        raise ImportFault(400,'invalid_import_request')
    if length is not None:
        if re.fullmatch('[0-9]{1,10}',length) is None:raise ImportFault(400,'invalid_import_request')
        if int(length)>(1024 if method=='POST' else 0):raise ImportFault(413 if method=='POST' else 400,'import_request_too_large' if method=='POST' else 'invalid_import_request')
    return int(length) if length is not None else None


def import_budget(scope,method):
    start,wall=time.monotonic(),time.time()
    if not math.isfinite(start) or not math.isfinite(wall):raise ImportFault(503,'import_unavailable')
    seconds=50.0 if method=='POST' else 9.0
    left,deadline=headers(scope,'x-cyomics-remaining-ms'),headers(scope,'x-cyomics-deadline-unix-ms')
    if left or deadline:
        if len(left)!=1 or len(deadline)!=1 or re.fullmatch('[1-9][0-9]{0,4}',left[0]) is None or re.fullmatch('[1-9][0-9]{12}',deadline[0]) is None or int(left[0])>(55000 if method=='POST' else 12000):
            raise ImportFault(400,'invalid_import_request')
        seconds=min(seconds,int(left[0])/1000,(int(deadline[0])-wall*1000)/1000)
    if seconds<=0:raise ImportFault(504,'import_timeout')
    budget=Budget(seconds);budget.deadline=min(budget.deadline,start+seconds);budget.check();return budget


def error_response(fault):
    code,status=getattr(fault,'code',None),getattr(fault,'status',None)
    if code not in ERRORS.get(status,set()):code,status='import_unavailable',503
    error={'code':code}
    key=getattr(fault,'version_key',None)
    if code=='import_result_unknown' and canonical_uuid(key):error['version_key']=key
    if code=='txt_import_failed':
        issues=getattr(fault,'issues',())
        safe=[]
        for issue in issues[:8] if isinstance(issues,(list,tuple)) else ():
            if (type(issue) is not dict or set(issue)!={'code','record_number','line','column_ordinal','limit_key'}
                    or issue['code'] not in ISSUES or issue['limit_key'] is not None and issue['limit_key'] not in LIMIT_KEYS
                    or any(v is not None and (type(v) is not int or not 0<=v<=9007199254740991) for k,v in issue.items() if k not in ('code','limit_key'))):
                safe=[];break
            safe.append(dict(issue))
        if safe:error['issues']=safe
    response=JSONResponse({'error':error},status_code=status,headers=SECURITY)
    if len(response.body)>MAX_ERROR:return JSONResponse({'error':{'code':'import_unavailable'}},status_code=503,headers=SECURITY)
    return response


def success_response(value,route,budget):
    try:
        if type(value) is not dict or set(value)!={'datasetVersion','importReport'}:raise ValueError()
        v=value['datasetVersion'];source=SourceRef(**v['source'])
        if (source.project_id!=route['project_id'] or source.raw_file_id!=route['file_id']
                or source.raw_version_key!='raw_bytea_v1:'+route['file_id']+':'+source.expected_raw_sha256):raise ValueError()
        cfg=canonical_json(configuration(),CONFIG_MAX_BYTES,budget.check)
        key=version_key(route['file_id'],cfg)
        if route['version_id'] is not None and route['version_id']!=key:raise ValueError()
        text=canonical_json(value['importReport'],REPORT_MAX_BYTES,budget.check)
        row=(v['dataset_version_id'],v['project_id'],v['file_id'],v['parser_contract'],v['config_sha256'],canonical_json(v['configuration'],CONFIG_MAX_BYTES),
             source.expected_bytes,source.expected_raw_sha256,v['representation'],v['parsed_table_materialized'],text,digest(text),datetime.fromisoformat(v['createdAt'].replace('Z','+00:00')))
        if decode_version(row,source,cfg,key)!=value:raise ValueError()
        response=JSONResponse(value,status_code=200,headers=SECURITY)
        if len(response.body)>MAX_SUCCESS:raise ValueError()
        budget.check();return response
    except (TimeoutError,ImportFault):raise
    except Exception:raise ImportFault(503,'import_persisted_report_invalid') from None


class Gone(Exception):pass


def send_error_without_wait(send,message):
    """Expired-budget public error only: allow synchronous completion, never yield.

    ASGI send is an awaitable. Drive it once without scheduling a task; any wait
    terminates this attempt. No new time budget, retry or success body is allowed.
    """
    iterator=None;yielded=None
    try:
        iterator=send(message).__await__()
        try:yielded=next(iterator)
        except StopIteration:return True
        return False
    except (Exception,asyncio.CancelledError):return False
    finally:
        if isinstance(yielded,asyncio.Future):yielded._asyncio_future_blocking=False
        if iterator is not None:
            try:iterator.close()
            except (Exception,asyncio.CancelledError):pass


CURRENT_LEASE=ContextVar('cyomics_import_http_lease',default=None)
class Lease:
    def __init__(self):self.subject=None;self.worker=None;self.future=None;self.stop=threading.Event()


class TrackedRunner:
    """Observe only the worker reached by this request, never a refused admission."""
    def __init__(self,runner):self.runner=runner
    async def run(self,operation,budget,*,key=None):
        lease=CURRENT_LEASE.get()
        eligible=not getattr(self.runner,'_busy',False) or getattr(self.runner,'_key',None)==key
        try:return await self.runner.run(operation,budget,key=key)
        finally:
            if eligible and lease is not None and lease.future is None:
                lease.future=getattr(self.runner,'_wrapped',None)


class CompleteImports:
    """Two whole requests, one per subject, before raw load; no admission queue.

    Pins bind the reviewed runner's _wrapped field. Only its completion is observed
    for quarantine; no runner internals or accepted core source are modified.
    """
    owns=staticmethod(owns_import)
    def __init__(self,authority,pool,enabled=False,runner=None):
        self.authority,self.pool,self.enabled=authority,pool,enabled is True
        self.runner=runner;self.service=None;self.closed=False
        self.leases=set();self.subjects={};self.attempts={}
    def start(self):
        if self.enabled and not self.closed and self.service is None:
            self.runner=self.runner or CooperativeImportRunner()
            self.service=ImportService(ImportRepository(self.pool),TrackedRunner(self.runner))
    def close(self):
        self.closed=True
        for lease in list(self.leases):
            lease.stop.set()
            if lease.worker is not None:lease.worker.cancel()
        if self.runner is not None:self.runner.close()
    def release(self,lease):
        if lease not in self.leases:return
        pending=lease.worker if lease.worker is not None and not lease.worker.done() else lease.future if lease.future is not None and not lease.future.done() else None
        if pending is not None:
            pending.add_done_callback(lambda _:self.release(lease));return
        self.leases.discard(lease)
        if self.subjects.get(lease.subject) is lease:self.subjects.pop(lease.subject,None)
    def admit_subject(self,lease,subject,method):
        if subject in self.subjects:raise ImportFault(429,'import_busy')
        now=time.monotonic()
        self.attempts={k:v for k,v in self.attempts.items() if v[0]>now}
        key=(subject,method);until,count=self.attempts.get(key,(now+(600 if method=='POST' else 60),0))
        if count>=(10 if method=='POST' else 60):raise ImportFault(429,'import_rate_limited')
        if key not in self.attempts and len(self.attempts)>=256:raise ImportFault(429,'import_busy')
        self.attempts[key]=(until,count+1);lease.subject=subject;self.subjects[subject]=lease
    async def __call__(self,scope,receive,send):
        budget=None;sent=False;start_attempted=False
        async def reject(fault):
            nonlocal sent,start_attempted
            # Never start a second response, even if an attempted start raised
            # after the server may already have accepted some headers.
            if sent or start_attempted:return
            sending_budget=budget if budget is not None else Budget(0 if getattr(fault,'code',None)=='import_timeout' else PUBLIC_ERROR_SEND_SECONDS)
            response=error_response(fault)
            start_message={'type':'http.response.start','status':response.status_code,'headers':response.raw_headers}
            body_message={'type':'http.response.body','body':response.body,'more_body':False}
            if sending_budget.remaining()<=0:
                start_attempted=True
                if send_error_without_wait(send,start_message):
                    sent=True
                    send_error_without_wait(send,body_message)
                return
            async def start_error():
                nonlocal start_attempted
                start_attempted=True
                await send(start_message)
            try:
                # The original deadline also bounds error transport. At expiry
                # only non-yielding best effort is permitted, never a new window.
                await sending_budget.run(start_error)
                sent=True
                if sending_budget.remaining()<=0:send_error_without_wait(send,body_message)
                else:await sending_budget.run(lambda:send(body_message))
            except asyncio.CancelledError:raise
            except Exception:return  # Transport/deadline failure: release or quarantine, no retry.
        if not self.enabled or self.closed or self.service is None:return await reject(ImportFault(503,'import_unavailable'))
        try:
            route=import_route(scope);length=validate_headers(scope,route['method']);budget=import_budget(scope,route['method'])
        except Fault as fault:return await reject(fault)
        except Exception:return await reject(ImportFault(503,'import_unavailable'))
        if len(self.leases)>=2:return await reject(ImportFault(429,'import_busy'))
        lease=Lease();self.leases.add(lease)
        ready=asyncio.get_running_loop().create_future();called=False
        async def pump():
            raw=bytearray();cap=1024 if route['method']=='POST' else 0
            while True:
                message=await receive();budget.check()
                if message['type']=='http.disconnect':raise Gone()
                if message['type']!='http.request' or ready.done():raise ImportFault(400,'invalid_import_request')
                chunk=message.get('body',b'')
                if type(chunk) is not bytes:raise ImportFault(400,'invalid_import_request')
                if len(raw)+len(chunk)>cap:raise ImportFault(413 if cap else 400,'import_request_too_large' if cap else 'invalid_import_request')
                raw.extend(chunk)
                if not message.get('more_body',False):
                    if length is not None and length!=len(raw):raise ImportFault(400,'invalid_import_request')
                    ready.set_result(bytes(raw));raw.clear()
        async def work():
            nonlocal called
            request=Request(scope);subject=await self.authority.verify(request,budget)
            token=request.headers.getlist('authorization')[0][7:]
            left=jwt_payload(token)['exp']-time.time()
            if not math.isfinite(left) or left<=0:raise ImportFault(401,'unauthenticated')
            budget.deadline=min(budget.deadline,time.monotonic()+left);budget.check()
            self.admit_subject(lease,subject,route['method'])
            raw=await ready;budget.check()
            if route['method']=='POST':
                try:value=strict_json(raw)
                except Exception:raise ImportFault(400,'invalid_import_request') from None
                if type(value) is not dict or set(value)!={'project_id'} or not canonical_uuid(value['project_id']):raise ImportFault(400,'invalid_import_request')
                route['project_id']=value['project_id']
            called=True
            token=CURRENT_LEASE.set(lease)
            try:
                if route['method']=='POST':value=await self.service.complete(subject,route['project_id'],route['file_id'],budget,cancelled=lease.stop.is_set)
                else:value=await self.service.read(subject,route['project_id'],route['file_id'],budget,route['version_id'],cancelled=lease.stop.is_set)
            finally:CURRENT_LEASE.reset(token)
            budget.check();return success_response(value,route,budget)
        watcher=asyncio.create_task(pump());lease.worker=asyncio.create_task(work())
        try:
            done,_=await budget.run(lambda:asyncio.wait({watcher,lease.worker},return_when=asyncio.FIRST_COMPLETED),abort=lease.stop.set)
            if watcher in done:await watcher
            response=await lease.worker;budget.check()
            if watcher.done():await watcher
            async def start_success():
                nonlocal start_attempted
                start_attempted=True
                await send({'type':'http.response.start','status':response.status_code,'headers':response.raw_headers})
            await budget.run(start_success);sent=True
            if watcher.done():await watcher
            await budget.run(lambda:send({'type':'http.response.body','body':response.body,'more_body':False}))
        except Gone:pass
        except asyncio.CancelledError:raise
        except Fault as fault:
            if not sent:await reject(fault)
        except Exception:
            pending=getattr(self.runner,'_wrapped',None)
            unknown=route['method']=='POST' and called and not (pending is not None and not pending.done())
            if not sent:await reject(ImportFault(504,'import_result_unknown' if unknown else 'import_timeout'))
        finally:
            lease.stop.set()
            for task in (watcher,lease.worker):
                if not task.done():task.cancel()
                task.add_done_callback(consume_task)
            self.release(lease)
            await asyncio.sleep(0)
