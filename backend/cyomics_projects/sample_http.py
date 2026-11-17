"""Default-off ASGI boundary; always uses the existing Authority.verify chain."""
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
from .sample_persistence import MAX_REQUEST, MAX_RESPONSE, FAMILIES, failure, response_value
from .sample_repository import SampleDesignRepository

UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
PATH = re.compile(rb'/api/v1/datasets/('+UUID.encode()+rb')/sample-designs')
ERRORS = {400:{'invalid_sample_design_request'},401:{'unauthenticated'},403:{'auth_origin_rejected'},
          404:{'not_found','dataset_not_found','sample_design_not_found'},405:{'method_not_allowed'},
          409:{'sample_design_head_conflict','sample_design_request_conflict'},
          413:{'sample_design_request_too_large'},415:{'unsupported_media_type'},
          422:{'invalid_sample_design','sample_design_version_limit'},
          503:{'sample_design_unavailable','sample_design_persisted_invalid'},
          504:{'sample_design_timeout','sample_design_result_unknown'}}


def route_input(scope):
    raw = scope.get('raw_path',b'')
    match = PATH.fullmatch(raw) if type(raw) is bytes else None
    if match is None or scope.get('path') != raw.decode('ascii') or scope.get('root_path',''): raise Fault(404,'not_found')
    method = scope.get('method')
    if method not in ('GET','POST'): raise Fault(405,'method_not_allowed')
    query = scope.get('query_string',b'')
    if type(query) is not bytes or len(query)>240: raise Fault(400,'invalid_sample_design_request')
    values = {}
    for item in query.split(b'&'):
        parts = item.split(b'=')
        if len(parts)!=2 or parts[0] not in (b'project_id',b'quant_family',b'request_id') or parts[0] in values: raise Fault(400,'invalid_sample_design_request')
        try: values[parts[0]] = parts[1].decode('ascii')
        except UnicodeError: raise Fault(400,'invalid_sample_design_request') from None
    if not canonical_uuid(values.get(b'project_id')) or values.get(b'quant_family') not in FAMILIES: raise Fault(400,'invalid_sample_design_request')
    request = values.get(b'request_id')
    if request is not None and (method != 'GET' or not canonical_uuid(request)): raise Fault(400,'invalid_sample_design_request')
    return {'method':method,'dataset':match[1].decode(),'project':values[b'project_id'],
            'family':FAMILIES[values[b'quant_family']], 'request':request}


def validate_headers(scope, method):
    def one(name):
        values = headers(scope,name)
        if len(values)>1: raise Fault(403 if name in ('host','origin','sec-fetch-site') else 400,'auth_origin_rejected' if name in ('host','origin','sec-fetch-site') else 'invalid_sample_design_request')
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
    if transfer not in (None,'chunked') or transfer is not None and (length is not None or method=='GET'): raise Fault(400,'invalid_sample_design_request')
    if length is not None:
        if re.fullmatch('[0-9]{1,10}',length) is None: raise Fault(400,'invalid_sample_design_request')
        if int(length)>(MAX_REQUEST if method=='POST' else 0): raise Fault(413 if method=='POST' else 400,'sample_design_request_too_large' if method=='POST' else 'invalid_sample_design_request')
    return int(length) if length is not None else None


def error_response(fault):
    status,code = getattr(fault,'status',None),getattr(fault,'code',None)
    if code not in ERRORS.get(status,set()): status,code=503,'sample_design_unavailable'
    return JSONResponse({'error':{'code':code}},status_code=status,headers=SECURITY)


class SampleDesigns:
    def __init__(self, authority, pool, *, enabled=False):
        self.authority=authority; self.repository=SampleDesignRepository(pool); self.enabled=enabled

    @staticmethod
    def owns(scope):
        path=scope.get('path','')
        return type(path) is str and re.sub('/+','/',unquote(path)).startswith('/api/v1/datasets')

    async def __call__(self, scope, receive, send):
        budget=None; start_attempted=False
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
            if not self.enabled:return await reject(Fault(503,'sample_design_unavailable'))
            length=validate_headers(scope,route['method'])
            try:
                # Validate the actual bridge method's header limits while retaining
                # this service's nine-second maximum processing budget.
                service_deadline = time.monotonic() + 9.0
                budget = import_budget(scope, route['method'])
                budget.deadline = min(budget.deadline, service_deadline)
                budget.check()
            except TimeoutError:raise Fault(504,'sample_design_timeout') from None
            except Fault as e:
                raise Fault(504 if e.status==504 else 400 if e.status==400 else 503,
                            'sample_design_timeout' if e.status==504 else 'invalid_sample_design_request' if e.status==400 else 'sample_design_unavailable') from None
        except Fault as fault:return await reject(fault)
        except Exception:return await reject(Fault(503,'sample_design_unavailable'))
        ready=asyncio.get_running_loop().create_future()
        async def pump():
            raw=bytearray();cap=MAX_REQUEST if route['method']=='POST' else 0
            while True:
                message=await receive();budget.check()
                if message['type']=='http.disconnect':raise Gone()
                if message['type']!='http.request' or ready.done():raise Fault(400,'invalid_sample_design_request')
                chunk=message.get('body',b'')
                if type(chunk) is not bytes:raise Fault(400,'invalid_sample_design_request')
                if len(raw)+len(chunk)>cap:raise Fault(413 if cap else 400,'sample_design_request_too_large' if cap else 'invalid_sample_design_request')
                raw.extend(chunk)
                if not message.get('more_body',False):
                    if length is not None and length!=len(raw):raise Fault(400,'invalid_sample_design_request')
                    ready.set_result(bytes(raw));raw.clear()
        async def work():
            request=Request(scope)
            subject=await self.authority.verify(request,budget)
            left=jwt_payload(request.headers.getlist('authorization')[0][7:])['exp']-time.time()
            if not math.isfinite(left) or left<=0:raise Fault(401,'unauthenticated')
            budget.deadline=min(budget.deadline,time.monotonic()+left)
            raw=await ready;budget.check()
            repo=self.repository
            if route['method']=='POST':
                try:body=strict_json(raw)
                except (ValueError,UnicodeError,RecursionError):raise Fault(400,'invalid_sample_design_request') from None
                value=await repo.save(subject,route['project'],route['dataset'],route['family'],body,budget)
            else:value=await repo.read(subject,route['project'],route['dataset'],route['family'],budget,route['request'])
            value=response_value(value,route['project'],route['dataset'],route['family'])
            response=JSONResponse(value,status_code=200,headers=SECURITY)
            if len(response.body)>MAX_RESPONSE:raise Fault(503,'sample_design_persisted_invalid')
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
                await send({'type':'http.response.start','status':200,'headers':response.raw_headers})
            await budget.run(start)
            if watcher.done():await watcher
            await budget.run(lambda:send({'type':'http.response.body','body':response.body,'more_body':False}))
        except Gone:pass
        except asyncio.CancelledError:raise
        except Fault as fault:await reject(failure(budget) if budget.create.commit_started else fault)
        except Exception as error:await reject(failure(budget,error))
        finally:
            for task in (watcher,worker):
                if not task.done():task.cancel()
                task.add_done_callback(consume_task)
            await asyncio.sleep(0)
