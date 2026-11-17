"""Source-only raw QC job API. Repository injection is server-owned; default off.
No worker, R, file access, downloads or connection creation.
"""
import asyncio
import math
import re
import time
from urllib.parse import unquote
from starlette.requests import Request
from starlette.responses import JSONResponse
from .identity import jwt_payload
from .import_http import headers, import_budget, send_error_without_wait, Gone, SECURITY
from .settings import Fault, consume_task
from .validation import canonical_uuid, strict_json
from . import raw_qc_contract as contract
from .raw_qc_contract import MAX_REQUEST, MAX_RESPONSE
MAX_ERROR = 4096

UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
COLLECTION = re.compile(rb'/api/v1/datasets/('+UUID.encode()+rb')/raw-qc-jobs')
DETAIL = re.compile(rb'/api/v1/raw-qc-jobs/('+UUID.encode()+rb')')
ERRORS = {400:{'invalid_raw_qc_request'},401:{'unauthenticated'},403:{'auth_origin_rejected'},
 404:{'not_found','dataset_not_found','raw_qc_job_not_found'},405:{'method_not_allowed'},
 409:{'raw_qc_request_conflict'},413:{'raw_qc_request_too_large'},415:{'unsupported_media_type'},
 422:{'raw_qc_family_unavailable'},429:{'raw_qc_capacity'},
 503:{'raw_qc_unavailable','raw_qc_persisted_invalid'},504:{'raw_qc_timeout','raw_qc_submission_unknown'}}

# Existing BE transaction contract: these typed repository failures precede
# uncertain COMMIT; submission_unknown is deliberately absent. Never apply this
# classification to watcher/protocol/response errors, even if codes coincide.
REPOSITORY_REJECTIONS = {400:{'invalid_raw_qc_request'},401:{'unauthenticated'},
 404:{'dataset_not_found','raw_qc_job_not_found'},409:{'raw_qc_request_conflict'},
 422:{'raw_qc_family_unavailable'},429:{'raw_qc_capacity'},
 503:{'raw_qc_unavailable','raw_qc_persisted_invalid'},504:{'raw_qc_timeout'}}

def route_input(scope):
    raw=scope.get('raw_path',b'')
    c=COLLECTION.fullmatch(raw) if type(raw) is bytes else None
    d=DETAIL.fullmatch(raw) if type(raw) is bytes else None
    if not (c or d) or scope.get('path')!=raw.decode('ascii') or scope.get('root_path',''):
        raise Fault(404,'not_found')
    method=scope.get('method')
    if method not in (('GET','POST') if c else ('GET',)):
        raise Fault(405,'method_not_allowed')
    query=scope.get('query_string',b'')
    if type(query) is not bytes or len(query)>160:raise Fault(400,'invalid_raw_qc_request')
    values={}
    for item in query.split(b'&'):
        pair=item.split(b'=')
        if len(pair)!=2 or pair[0] not in (b'project_id',b'request_id') or pair[0] in values:
            raise Fault(400,'invalid_raw_qc_request')
        try:value=pair[1].decode('ascii')
        except UnicodeError:raise Fault(400,'invalid_raw_qc_request') from None
        if not canonical_uuid(value):raise Fault(400,'invalid_raw_qc_request')
        values[pair[0]]=value
    expected={b'project_id',b'request_id'} if c and method=='GET' else {b'project_id'}
    if set(values)!=expected:raise Fault(400,'invalid_raw_qc_request')
    return {'method':method,'project':values[b'project_id'],'dataset':c[1].decode() if c else None,
            'job':d[1].decode() if d else None,'request':values.get(b'request_id')}

def response_value(value, route, request=None):
    """Validate BE01's existing public single-job shape, without artifact access."""
    try:
        contract.exact(value,contract.PUBLIC_KEYS)
        if type(value['schemaVersion']) is not int or value['schemaVersion']!=1:raise ValueError()
        for k in ('job_id','request_id'):contract.identifier(value[k])
        source=value['source'];contract.source_value(source);contract.execution_value(value['execution'])
        if source['project_id']!=route['project'] or route['dataset'] is not None and source['dataset_version_id']!=route['dataset']:raise ValueError()
        if route['job'] is not None and value['job_id']!=route['job']:raise ValueError()
        if route['request'] is not None and value['request_id']!=route['request']:raise ValueError()
        if request and (value['request_id']!=request['request_id'] or value['quant_family']!=request['quant_family']):raise ValueError()
        if value['quant_family'] not in contract.FAMILIES.values() or value['params']!={}:raise ValueError()
        rh=contract.request_sha(source['project_id'],source['dataset_version_id'],value['quant_family'])
        if value['request_sha256']!=rh or value['binding_sha256']!=contract.binding_sha(rh,source,value['execution'],value['quant_family']):raise ValueError()
        contract.integer(value['revision'],1)
        created=contract.parse_stamp(value['created_at']);started=None if value['started_at'] is None else contract.parse_stamp(value['started_at'])
        finished=None if value['finished_at'] is None else contract.parse_stamp(value['finished_at'])
        if started is not None and started<created or finished is not None and finished<(started or created):raise ValueError()
        status=value['status'];observation=value['observation']
        if status not in ('queued','running','succeeded','failed') or observation not in ('confirmed','reconciliation_pending'):raise ValueError()
        if observation!='confirmed' and status!='running':raise ValueError()
        if status=='queued' and (started is not None or finished is not None):raise ValueError()
        if status=='running' and (started is None or finished is not None):raise ValueError()
        if status in ('succeeded','failed') and finished is None or status=='succeeded' and started is None:raise ValueError()
        if status=='failed':
            contract.exact(value['error'],('code',))
            if value['error']['code'] not in contract.ERROR_CODES:raise ValueError()
        elif value['error'] is not None:raise ValueError()
        if status=='succeeded':
            contract.result_value(value['result'],value)
            if contract.parse_stamp(value['result']['verified_at'])>finished:raise ValueError()
        elif value['result'] is not None:raise ValueError()
        if len(contract.canonical(value).encode('utf-8'))>MAX_RESPONSE:raise ValueError()
        return value
    except Exception:raise Fault(503,'raw_qc_persisted_invalid') from None

def failure(budget,error=None,post=False):
    if post:return Fault(504,'raw_qc_submission_unknown')
    return Fault(504,'raw_qc_timeout') if isinstance(error,TimeoutError) else Fault(503,'raw_qc_unavailable')

def validate_headers(scope, method):
    def one(name):
        values = headers(scope,name)
        if len(values)>1: raise Fault(403 if name in ('host','origin','sec-fetch-site') else 400,'auth_origin_rejected' if name in ('host','origin','sec-fetch-site') else 'invalid_raw_qc_request')
        return values[0] if values else None
    if one('host') != '127.0.0.1:57430': raise Fault(403,'auth_origin_rejected')
    origin = one('origin')
    if origin is not None and origin != 'http://127.0.0.1:4206' or method=='POST' and (origin is None or one('x-cyomics-request')!='1'): raise Fault(403,'auth_origin_rejected')
    if one('sec-fetch-site') not in (None,'same-origin'): raise Fault(403,'auth_origin_rejected')
    for name in ('cookie','x-user-id','x-owner-id','x-cyomics-account-context'):
        if headers(scope,name): raise Fault(403,'auth_origin_rejected')
    if one('content-encoding') not in (None,'identity'): raise Fault(415,'unsupported_media_type')
    if method=='POST' and (one('content-type') or '').lower() not in ('application/json','application/json; charset=utf-8'): raise Fault(415,'unsupported_media_type')
    length, transfer = one('content-length'), one('transfer-encoding')
    if transfer not in (None,'chunked') or transfer is not None and (length is not None or method=='GET'): raise Fault(400,'invalid_raw_qc_request')
    if length is not None:
        if re.fullmatch('[0-9]{1,10}',length) is None: raise Fault(400,'invalid_raw_qc_request')
        if int(length)>(MAX_REQUEST if method=='POST' else 0): raise Fault(413 if method=='POST' else 400,'raw_qc_request_too_large' if method=='POST' else 'invalid_raw_qc_request')
    return int(length) if length is not None else None


def error_response(fault):
    status,code = getattr(fault,'status',None),getattr(fault,'code',None)
    if code not in ERRORS.get(status,set()): status,code=503,'raw_qc_unavailable'
    return JSONResponse({'error':{'code':code}},status_code=status,headers=SECURITY)


class RawQCJobs:
    def __init__(self, authority, repository=None):
        self.authority=authority; self.repository=repository

    @staticmethod
    def owns(scope):
        path=scope.get('path','')
        if type(path) is not str:return False
        path=re.sub('/+','/',unquote(path))
        return path.startswith('/api/v1/raw-qc-jobs') or re.match(r'^/api/v1/datasets/[^/]+/raw-qc-jobs(?:/|$)',path) is not None

    async def __call__(self, scope, receive, send):
        budget=None; start_attempted=False; submitted=False; repository_rejection=None
        async def reject(fault):
            if start_attempted:return
            response=error_response(fault)
            messages=[{'type':'http.response.start','status':response.status_code,'headers':response.raw_headers},
                      {'type':'http.response.body','body':response.body,'more_body':False}]
            for message in messages:
                if budget is None or budget.remaining()<=0:
                    if not send_error_without_wait(send,message):return
                else:
                    try: await budget.run(lambda:send(message))
                    except asyncio.CancelledError:raise
                    except Exception:return
        try:
            route=route_input(scope)
            if self.repository is None:return await reject(Fault(503,'raw_qc_unavailable'))
            length=validate_headers(scope,route['method'])
            try:
                # Validate the actual bridge method's header limits while retaining
                # this service's nine-second maximum processing budget.
                service_deadline = time.monotonic() + 9.0
                budget = import_budget(scope, route['method'])
                budget.deadline = min(budget.deadline, service_deadline)
                budget.check()
            except TimeoutError:raise Fault(504,'raw_qc_timeout') from None
            except Fault as e:
                raise Fault(504 if e.status==504 else 400 if e.status==400 else 503,
                            'raw_qc_timeout' if e.status==504 else 'invalid_raw_qc_request' if e.status==400 else 'raw_qc_unavailable') from None
        except Fault as fault:return await reject(fault)
        except Exception:return await reject(Fault(503,'raw_qc_unavailable'))
        ready=asyncio.get_running_loop().create_future()
        async def pump():
            raw=bytearray();cap=MAX_REQUEST if route['method']=='POST' else 0
            while True:
                message=await receive();budget.check()
                if message['type']=='http.disconnect':raise Gone()
                if message['type']!='http.request' or ready.done():raise Fault(400,'invalid_raw_qc_request')
                chunk=message.get('body',b'')
                if type(chunk) is not bytes:raise Fault(400,'invalid_raw_qc_request')
                if len(raw)+len(chunk)>cap:raise Fault(413 if cap else 400,'raw_qc_request_too_large' if cap else 'invalid_raw_qc_request')
                raw.extend(chunk)
                if not message.get('more_body',False):
                    if length is not None and length!=len(raw):raise Fault(400,'invalid_raw_qc_request')
                    ready.set_result(bytes(raw));raw.clear()
        async def work():
            nonlocal submitted,repository_rejection
            request=Request(scope)
            subject=await self.authority.verify(request,budget)
            left=jwt_payload(request.headers.getlist('authorization')[0][7:])['exp']-time.time()
            if not math.isfinite(left) or left<=0:raise Fault(401,'unauthenticated')
            budget.deadline=min(budget.deadline,time.monotonic()+left)
            raw=await ready;budget.check()
            repo=self.repository
            body=None
            if route['method']=='POST':
                body=contract.request_input(raw)
                submitted=True
                try:
                    value=await repo.enqueue(subject,route['project'],route['dataset'],raw,budget)
                except Fault as error:
                    # Only the repository await can establish a definite precommit
                    # rejection. A watcher Fault is never a repository receipt.
                    if error.code in REPOSITORY_REJECTIONS.get(error.status,set()):
                        repository_rejection=error
                    raise
            elif route['dataset'] is not None:
                value=await repo.read_request(subject,route['project'],route['dataset'],route['request'],budget)
            else:
                value=await repo.read_job(subject,route['project'],route['job'],budget)
            value=response_value(value,route,body)
            response=JSONResponse(value,status_code=202 if route['method']=='POST' and value['status'] in ('queued','running') else 200,headers=SECURITY)
            if len(response.body)>MAX_RESPONSE:raise Fault(503,'raw_qc_persisted_invalid')
            budget.check();return response
        watcher=asyncio.create_task(pump());worker=asyncio.create_task(work())
        try:
            done,_=await budget.run(lambda:asyncio.wait({watcher,worker},return_when=asyncio.FIRST_COMPLETED))
            if watcher in done:await watcher
            response=await worker;budget.check()
            if watcher.done():await watcher
            async def start():
                nonlocal start_attempted
                start_attempted=True
                await send({'type':'http.response.start','status':response.status_code,'headers':response.raw_headers})
            await budget.run(start)
            if watcher.done():await watcher
            await budget.run(lambda:send({'type':'http.response.body','body':response.body,'more_body':False}))
        except Gone:pass
        except asyncio.CancelledError:raise
        except Fault as fault:
            await reject(repository_rejection if submitted and repository_rejection is not None
                         else failure(budget,post=True) if submitted else fault)
        except Exception as error:
            await reject(repository_rejection if submitted and repository_rejection is not None
                         else failure(budget,error,post=submitted))
        finally:
            for task in (watcher,worker):
                if not task.done():task.cancel()
                task.add_done_callback(consume_task)
            await asyncio.sleep(0)
