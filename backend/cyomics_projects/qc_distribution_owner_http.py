"""Only authenticated reads of a separately registered existing QC package."""
import asyncio, math, re, time
from urllib.parse import unquote
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from .identity import jwt_payload
from .import_http import import_budget, send_error_without_wait, Gone
from .sample_http import validate_headers
from .settings import Fault, consume_task
from .validation import canonical_uuid
from . import qc_distribution_owner_read as plots

UUID=rb'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
ROUTE=re.compile(rb'/api/v1/projects/('+UUID+rb')/processed-datasets/('+UUID+rb')/qc-distributions(?:/artifacts/([A-Za-z0-9_.-]{1,64}))?')
ERRORS={400:{'invalid_qc_distribution_request'},401:{'unauthenticated'},403:{'auth_origin_rejected'},
 404:{'not_found','qc_distribution_not_ready'},405:{'method_not_allowed'},409:{'qc_distribution_unsupported_parent'},
 503:{'qc_distribution_unavailable','qc_distribution_integrity_failed'},504:{'qc_distribution_read_timeout'}}
def error_response(error):
    status,code=getattr(error,'status',None),getattr(error,'code',None)
    if isinstance(error,TimeoutError) or status==504:status,code=504,'qc_distribution_read_timeout'
    elif status==404 and code not in ERRORS[404]:code='not_found'
    elif status in (400,413,415):status,code=400,'invalid_qc_distribution_request'
    elif code not in ERRORS.get(status,set()):status,code=503,'qc_distribution_unavailable'
    return JSONResponse({'error':{'code':code}},status_code=status,headers=plots.SECURITY)

class QCDistributionHTTP:
    def __init__(self,authority,reader=None):self.authority=authority;self.reader=reader
    @staticmethod
    def owns(scope):
        path=scope.get('path','')
        return type(path) is str and re.match(r'^/api/v1/projects/[^/]+/processed-datasets/[^/]+/qc-distributions(?:/|$)',re.sub('/+','/',unquote(path))) is not None
    async def __call__(self,scope,receive,send):
        budget=None;started=False
        async def reject(error):
            if started:return
            response=error_response(error)
            for message in ({'type':'http.response.start','status':response.status_code,'headers':response.raw_headers},
                            {'type':'http.response.body','body':response.body,'more_body':False}):
                if budget is None or budget.remaining()<=0:
                    if not send_error_without_wait(send,message):return
                else:
                    try:await budget.run(lambda:send(message))
                    except asyncio.CancelledError:raise
                    except Exception:return
        try:
            if self.reader is None:return await reject(Fault(503,'qc_distribution_unavailable'))
            raw=scope.get('raw_path',b'');match=ROUTE.fullmatch(raw) if type(raw) is bytes else None
            if match is None or scope.get('path')!=raw.decode('ascii') or scope.get('root_path',''):raise Fault(404,'not_found')
            project,version=(x.decode() for x in match.group(1,2));name=match[3].decode() if match[3] else None
            if not canonical_uuid(project) or not canonical_uuid(version) or name is not None and name not in plots.MEDIA:raise Fault(404,'not_found')
            if scope.get('method')!='GET':raise Fault(405,'method_not_allowed')
            if scope.get('query_string',b''):raise Fault(400,'invalid_qc_distribution_request')
            length=validate_headers(scope,'GET');budget=import_budget(scope,'GET');budget.check()
        except asyncio.CancelledError:raise
        except Exception as error:return await reject(error)
        ready=asyncio.get_running_loop().create_future()
        async def pump():
            while True:
                message=await receive();budget.check()
                if message['type']=='http.disconnect':raise Gone()
                if message['type']!='http.request' or ready.done() or message.get('body',b'')!=b'':raise Fault(400,'invalid_qc_distribution_request')
                if not message.get('more_body',False):
                    if length not in (None,0):raise Fault(400,'invalid_qc_distribution_request')
                    ready.set_result(None)
        async def work():
            request=Request(scope);subject=await self.authority.verify(request,budget)
            left=jwt_payload(request.headers.getlist('authorization')[0][7:])['exp']-time.time()
            if not math.isfinite(left) or left<=0:raise Fault(401,'unauthenticated')
            budget.deadline=min(budget.deadline,time.monotonic()+left)
            await ready;budget.check()
            value,body=await self.reader.read(subject,project,version,budget,name);budget.check()
            if name is None:
                response=JSONResponse(value,headers=plots.SECURITY);plots.need(len(response.body)<=plots.MAX_DESCRIPTOR);return response
            pin=next(p for p in value['artifacts'] if p['name']==name)
            plots.need(type(body) is bytes and len(body)==pin['bytes'] and plots.sha(body)==pin['sha256'])
            return Response(body,headers={**plots.SECURITY,'Content-Type':pin['media_type'],'Content-Length':str(len(body)),
                'X-Cyomics-File-SHA256':pin['sha256'],'Content-Disposition':'attachment; filename="'+name+'"'})
        watcher=asyncio.create_task(pump());worker=asyncio.create_task(work())
        try:
            done,_=await budget.run(lambda:asyncio.wait({watcher,worker},return_when=asyncio.FIRST_COMPLETED))
            if watcher in done:await watcher
            response=await worker;budget.check()
            if watcher.done():await watcher
            async def start():
                nonlocal started
                started=True;await send({'type':'http.response.start','status':response.status_code,'headers':response.raw_headers})
            await budget.run(start)
            body=response.body
            for at in range(0,len(body),65536):
                budget.check()
                if watcher.done():await watcher
                await budget.run(lambda at=at:send({'type':'http.response.body','body':body[at:at+65536],'more_body':at+65536<len(body)}))
        except Gone:pass
        except asyncio.CancelledError:raise
        except Exception as error:await reject(error)
        finally:
            for task in (watcher,worker):
                if not task.done():task.cancel()
                task.add_done_callback(consume_task)
            await asyncio.sleep(0)
