"""Owner preprocessing TaskV2, list, versions and fixed11 bounded downloads.

Shares the original Authority and request cancellation/budget boundary.
No claim, engine, worker, source read or publication occurs in this handler.
"""
import asyncio
from datetime import datetime
import math
import re
import time
from urllib.parse import unquote
from starlette.requests import Request
from starlette.responses import JSONResponse,Response
from .identity import jwt_payload
from .import_http import headers, import_budget, send_error_without_wait, Gone, SECURITY
from .settings import Fault, consume_task
from .validation import canonical_uuid, TIME_PATTERN
from .raw_qc_http import UUID, MAX_RESPONSE
from . import preprocess_contract as contract
from . import preprocess_repository as persisted

MAX_REQUEST = contract.MAX_REQUEST_BYTES
COLLECTION = re.compile(rb'/api/v1/datasets/('+UUID.encode()+rb')/preprocessing-jobs')
DETAIL = re.compile(rb'/api/v1/preprocessing-jobs/('+UUID.encode()+rb')')
VERSION = re.compile(rb'/api/v1/processed-dataset-versions/('+UUID.encode()+rb')')
ARTIFACT = re.compile(rb'/api/v1/processed-dataset-versions/('+UUID.encode()+rb')/artifacts/([A-Za-z0-9_.-]{1,64})')
PUBLIC_KEYS = contract.TASK_KEYS
ERRORS = {400:{'invalid_preprocessing_request'},401:{'unauthenticated'},403:{'auth_origin_rejected'},
    404:{'not_found','dataset_not_found','preprocessing_job_not_found','processed_dataset_version_not_found','preprocessing_artifact_not_found'},405:{'method_not_allowed'},
    409:{'preprocessing_request_conflict','preprocessing_attempt_conflict','preprocessing_not_ready'},413:{'preprocessing_request_too_large'},415:{'unsupported_media_type'},
    422:{'preprocessing_family_unavailable','preprocessing_flags_unavailable'},
    503:{'preprocessing_unavailable','preprocessing_persisted_invalid','preprocessing_execution_unavailable','preprocessing_artifact_integrity_failed'},
    504:{'preprocessing_timeout','preprocessing_submission_unknown'}}
# Only a typed error from the repository await establishes a definite rejection.
# A watcher/protocol error after submission must never borrow this classification.
REPOSITORY_REJECTIONS = {k:set(v) for k,v in ERRORS.items() if k in (400,401,404,409,413,422,503)}
REPOSITORY_REJECTIONS[404].discard('not_found')
REPOSITORY_REJECTIONS[504] = {'preprocessing_timeout'}


def route_input(scope):
    raw=scope.get('raw_path',b'');query=scope.get('query_string',b'')
    if type(raw) is not bytes or type(query) is not bytes:raise Fault(400,'invalid_preprocessing_request')
    if len(raw)+(1+len(query) if query else 0)>contract.MAX_RAW_TARGET_BYTES:raise Fault(404,'not_found')
    if len(query)>contract.MAX_QUERY_BYTES:raise Fault(400,'invalid_preprocessing_request')
    collection=COLLECTION.fullmatch(raw);detail=DETAIL.fullmatch(raw);version=VERSION.fullmatch(raw);artifact=ARTIFACT.fullmatch(raw)
    if not any((collection,detail,version,artifact)) or scope.get('path')!=raw.decode('ascii') or scope.get('root_path',''):
        raise Fault(404,'not_found')
    method=scope.get('method')
    if method not in (('GET','POST') if collection else ('GET',)):raise Fault(405,'method_not_allowed')
    values={}
    for item in query.split(b'&'):
        pair=item.split(b'=')
        if len(pair)!=2 or pair[0] not in (b'project_id',b'request_id',b'limit',b'cursor') or pair[0] in values:raise Fault(400,'invalid_preprocessing_request')
        try:values[pair[0]]=pair[1].decode('ascii')
        except UnicodeError:raise Fault(400,'invalid_preprocessing_request') from None
    if not canonical_uuid(values.get(b'project_id')):raise Fault(400,'invalid_preprocessing_request')
    kind='submit' if method=='POST' else 'lookup' if collection and b'request_id' in values else 'list' if collection else 'job' if detail else 'version' if version else 'artifact'
    expected={b'project_id',b'request_id'} if kind=='lookup' else {b'project_id'}
    if kind=='list':
        if not expected<=set(values)<=expected|{b'limit',b'cursor'}:raise Fault(400,'invalid_preprocessing_request')
    elif set(values)!=expected:raise Fault(400,'invalid_preprocessing_request')
    if kind=='lookup' and not canonical_uuid(values[b'request_id']):raise Fault(400,'invalid_preprocessing_request')
    limit=10;cursor=values.get(b'cursor')
    if b'limit' in values:
        if re.fullmatch('[1-9]|10',values[b'limit']) is None:raise Fault(400,'invalid_preprocessing_request')
        limit=int(values[b'limit'])
    if cursor is not None:
        try:contract.decode_cursor(cursor)
        except Exception:raise Fault(400,'invalid_preprocessing_request') from None
    canonical='project_id='+values[b'project_id']
    for key in (b'request_id',b'limit',b'cursor'):
        if key in values:canonical+='&'+key.decode()+'='+values[key]
    if query!=canonical.encode():raise Fault(400,'invalid_preprocessing_request')
    name=artifact[2].decode() if artifact else None
    if name is not None and name not in contract.ARTIFACT_NAMES:raise Fault(404,'preprocessing_artifact_not_found')
    return {'method':method,'kind':kind,'project':values[b'project_id'],'dataset':collection[1].decode() if collection else None,
        'job':detail[1].decode() if detail else None,'version':(version or artifact)[1].decode() if version or artifact else None,
        'name':name,'request':values.get(b'request_id'),'limit':limit,'cursor':cursor}


def response_value(value,route,request=None):
    try:
        if route['kind']=='list':return contract.list_value(value,project=route['project'],dataset=route['dataset'],limit=route['limit'])
        if route['kind']=='version':return contract.version_value(value,project=route['project'],version=route['version'])
        return contract.task_value(value,project=route['project'],dataset=route['dataset'],job=route['job'],request_id=route['request'],request=request)
    except Exception:raise Fault(503,'preprocessing_persisted_invalid') from None


def failure(error=None,post=False):
    if post:
        return Fault(504,'preprocessing_submission_unknown')
    return Fault(504,'preprocessing_timeout') if isinstance(error,TimeoutError) else Fault(503,'preprocessing_unavailable')


def validate_headers(scope,method):
    def one(name):
        values = headers(scope,name)
        if len(values)>1:
            auth = name in ('host','origin','sec-fetch-site')
            raise Fault(403 if auth else 400,'auth_origin_rejected' if auth else 'invalid_preprocessing_request')
        return values[0] if values else None
    if one('host')!='127.0.0.1:57430':
        raise Fault(403,'auth_origin_rejected')
    origin = one('origin')
    if (origin is not None and origin!='http://127.0.0.1:4206'
            or method=='POST' and (origin is None or one('x-cyomics-request')!='1')):
        raise Fault(403,'auth_origin_rejected')
    if one('sec-fetch-site') not in (None,'same-origin'):
        raise Fault(403,'auth_origin_rejected')
    for name in ('cookie','x-user-id','x-owner-id','x-cyomics-account-context'):
        if headers(scope,name):
            raise Fault(403,'auth_origin_rejected')
    if one('content-encoding') not in (None,'identity'):
        raise Fault(415,'unsupported_media_type')
    if method=='POST' and (one('content-type') or '').lower() not in ('application/json','application/json; charset=utf-8'):
        raise Fault(415,'unsupported_media_type')
    length,transfer = one('content-length'),one('transfer-encoding')
    if transfer not in (None,'chunked') or transfer is not None and (length is not None or method=='GET'):
        raise Fault(400,'invalid_preprocessing_request')
    if length is not None:
        if re.fullmatch('[0-9]{1,10}',length) is None:
            raise Fault(400,'invalid_preprocessing_request')
        if int(length)>(MAX_REQUEST if method=='POST' else 0):
            raise Fault(413 if method=='POST' else 400,
                'preprocessing_request_too_large' if method=='POST' else 'invalid_preprocessing_request')
    return int(length) if length is not None else None


def error_response(fault):
    status,code = getattr(fault,'status',None),getattr(fault,'code',None)
    if type(status) is not int or type(code) is not str or code not in ERRORS.get(status,set()):
        status,code = 503,'preprocessing_unavailable'
    return JSONResponse({'error':{'code':code}},status_code=status,headers=SECURITY)


class PreprocessingJobs:
    def __init__(self,authority,repository=None):
        self.authority = authority
        self.repository = repository

    @staticmethod
    def owns(scope):
        path = scope.get('path','')
        if type(path) is not str:
            return False
        path = re.sub('/+','/',unquote(path))
        return (path.startswith('/api/v1/preprocessing-jobs') or path.startswith('/api/v1/processed-dataset-versions')
            or re.match(r'^/api/v1/datasets/[^/]+/preprocessing-jobs(?:/|$)',path) is not None)

    async def __call__(self,scope,receive,send):
        budget = None
        start_attempted = submitted = False
        repository_rejection = None
        async def reject(fault):
            if start_attempted:
                return
            response = error_response(fault)
            messages = [{'type':'http.response.start','status':response.status_code,'headers':response.raw_headers},
                {'type':'http.response.body','body':response.body,'more_body':False}]
            for message in messages:
                if budget is None or budget.remaining()<=0:
                    if not send_error_without_wait(send,message):
                        return
                else:
                    try:
                        await budget.run(lambda:send(message))
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        return
        try:
            route = route_input(scope)
            length = validate_headers(scope,route['method'])
            try:
                service_deadline = time.monotonic()+9.0
                budget = import_budget(scope,route['method'])
                budget.deadline = min(budget.deadline,service_deadline)
                budget.check()
            except TimeoutError:
                raise Fault(504,'preprocessing_timeout') from None
            except Fault as error:
                raise Fault(504 if error.status==504 else 400 if error.status==400 else 503,
                    'preprocessing_timeout' if error.status==504 else
                    'invalid_preprocessing_request' if error.status==400 else 'preprocessing_unavailable') from None
        except Fault as fault:
            return await reject(fault)
        except Exception:
            return await reject(Fault(503,'preprocessing_unavailable'))
        ready = asyncio.get_running_loop().create_future()
        async def pump():
            raw = bytearray()
            cap = MAX_REQUEST if route['method']=='POST' else 0
            while True:
                message = await receive()
                budget.check()
                if message['type']=='http.disconnect':
                    raise Gone()
                if message['type']!='http.request' or ready.done():
                    raise Fault(400,'invalid_preprocessing_request')
                chunk = message.get('body',b'')
                if type(chunk) is not bytes:
                    raise Fault(400,'invalid_preprocessing_request')
                if len(raw)+len(chunk)>cap:
                    raise Fault(413 if cap else 400,
                        'preprocessing_request_too_large' if cap else 'invalid_preprocessing_request')
                raw.extend(chunk)
                if not message.get('more_body',False):
                    if length is not None and length!=len(raw):
                        raise Fault(400,'invalid_preprocessing_request')
                    ready.set_result(bytes(raw))
                    raw.clear()
        async def work():
            nonlocal submitted,repository_rejection
            request = Request(scope)
            subject = await self.authority.verify(request,budget)
            left = jwt_payload(request.headers.getlist('authorization')[0][7:])['exp']-time.time()
            if not math.isfinite(left) or left<=0:
                raise Fault(401,'unauthenticated')
            budget.deadline = min(budget.deadline,time.monotonic()+left)
            raw = await ready
            budget.check()
            body = None
            if route['method']=='POST':
                try:
                    body = contract.request_input(raw,median_enabled=True,total_enabled=True)
                except contract.RequestError as error:
                    raise Fault(413 if error.code=='preprocessing_request_too_large' else 400,error.code) from None
            if self.repository is None:
                raise Fault(503,'preprocessing_unavailable')
            created = False
            if route['method']=='POST':
                submitted = True
                try:
                    observation = await self.repository.enqueue(subject,route['project'],route['dataset'],raw,budget)
                except Fault as error:
                    if error.code in REPOSITORY_REJECTIONS.get(error.status,set()):
                        repository_rejection = error
                    raise
                if (type(observation) is not persisted.QueueObservation or type(observation.created) is not bool
                        or observation.commit_confirmed is not True or observation.teardown_confirmed is not True):
                    raise Fault(503,'preprocessing_persisted_invalid')
                created = observation.created
                value = observation.job_public()
            elif route['kind']=='lookup':
                value=await self.repository.read_request(subject,route['project'],route['dataset'],route['request'],budget)
            elif route['kind']=='list':
                value=await self.repository.list_jobs(subject,route['project'],route['dataset'],budget,limit=route['limit'],cursor=route['cursor'])
            elif route['kind']=='version':
                value=await self.repository.read_version(subject,route['project'],route['version'],budget)
            elif route['kind']=='artifact':
                value=await self.repository.read_artifact(subject,route['project'],route['version'],route['name'],budget)
            else:
                value=await self.repository.read_job(subject,route['project'],route['job'],budget)
            if route['kind']=='artifact':
                if (type(value) is not persisted.StoredArtifact or value.name!=route['name'] or type(value.body) is not bytes
                        or not 1<=len(value.body)<=contract.MAX_ARTIFACT or value.sha256!=contract.bytes_sha(value.body)):
                    raise Fault(503,'preprocessing_artifact_integrity_failed')
                media='application/json' if value.name.endswith('.json') else 'text/plain; charset=utf-8' if value.name.endswith('.txt') else 'text/tab-separated-values; charset=utf-8'
                if value.media_type!=media:raise Fault(503,'preprocessing_artifact_integrity_failed')
                response=Response(value.body,status_code=200,headers={**SECURITY,'Content-Type':'application/octet-stream',
                    'Content-Disposition':'attachment; filename="'+value.name+'"','X-Cyomics-File-SHA256':value.sha256})
            else:
                value=response_value(value,route,body)
                response=JSONResponse(value,status_code=201 if created else 200,headers=SECURITY)
                if len(response.body)>MAX_RESPONSE:raise Fault(503,'preprocessing_persisted_invalid')
            budget.check()
            return response
        watcher = asyncio.create_task(pump())
        worker = asyncio.create_task(work())
        try:
            done,_ = await budget.run(lambda:asyncio.wait({watcher,worker},return_when=asyncio.FIRST_COMPLETED))
            if watcher in done:
                await watcher
            response = await worker
            budget.check()
            if watcher.done():
                await watcher
            async def start():
                nonlocal start_attempted
                start_attempted = True
                await send({'type':'http.response.start','status':response.status_code,'headers':response.raw_headers})
            await budget.run(start)
            if watcher.done():
                await watcher
            await budget.run(lambda:send({'type':'http.response.body','body':response.body,'more_body':False}))
        except Gone:
            pass
        except asyncio.CancelledError:
            raise
        except Fault as fault:
            await reject(repository_rejection if submitted and repository_rejection is not None
                else failure(post=True) if submitted else fault)
        except Exception as error:
            await reject(repository_rejection if submitted and repository_rejection is not None
                else failure(error,post=submitted))
        finally:
            for task in (watcher,worker):
                if not task.done():
                    task.cancel()
                task.add_done_callback(consume_task)
            await asyncio.sleep(0)
