"""Default-off Owner-only readonly DEP/FASTA exports. No route installation.

Every request uses the ORIGINAL app Authority and fresh original five-Repo
helper assembly, within original GET9s budget. No task, write, publication,
worker/native/BLAST or client identity/config shortcut. Operator50s proof does
not establish actual9s HTTP performance. Future app/BFF integration is separate.
"""
import asyncio,hashlib,json,math,re,time
from urllib.parse import unquote
from starlette.requests import Request
from starlette.responses import Response,JSONResponse
from .identity import jwt_payload
from .import_http import import_budget,send_error_without_wait,Gone,SECURITY
from .sample_http import validate_headers
from .settings import Fault,consume_task
from .validation import canonical_uuid
from .dep_fasta_handoff import OwnerFastaHandoff,Config,Bundle

UUID=rb'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
ROUTE=re.compile(rb'/api/v1/projects/('+UUID+rb')/differential-classifications/('+UUID+rb')/fasta-handoff(?:/artifacts/(queries\.fasta|membership\.tsv))?')
MEDIA={'membership.tsv':'text/tab-separated-values; charset=utf-8','queries.fasta':'text/plain; charset=us-ascii'}
CAP={'membership.tsv':33554432,'queries.fasta':26214400}
ERRORS={400:{'invalid_dep_fasta_request'},401:{'unauthenticated'},403:{'auth_origin_rejected'},404:{'not_found'},405:{'method_not_allowed'},
 409:{'dep_fasta_not_classified','dep_fasta_source_unsupported','dep_fasta_output_changed'},413:{'dep_fasta_too_large'},
 503:{'dep_fasta_unavailable','dep_fasta_binding_invalid'},504:{'dep_fasta_read_timeout'}}

def need(ok,code='dep_fasta_binding_invalid',status=503):
    if not ok:raise Fault(status,code)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def error_response(error):
    status,code=getattr(error,'status',None),getattr(error,'code',None)
    if isinstance(error,TimeoutError) or status==504:status,code=504,'dep_fasta_read_timeout'
    elif status==404:code='not_found'
    elif status in (400,415):status,code=400,'invalid_dep_fasta_request'
    elif status==413 and code!='dep_fasta_too_large':status,code=400,'invalid_dep_fasta_request'
    elif code not in ERRORS.get(status,set()):status,code=503,'dep_fasta_unavailable'
    return JSONResponse({'error':{'code':code}},status_code=status,headers=SECURITY)

def route_input(scope):
    raw=scope.get('raw_path');match=ROUTE.fullmatch(raw) if type(raw) is bytes else None
    if match is None or scope.get('path')!=raw.decode('ascii') or scope.get('root_path',''):raise Fault(404,'not_found')
    project,derived=(x.decode('ascii') for x in match.group(1,2));name=match[3].decode('ascii') if match[3] else None
    if not canonical_uuid(project) or not canonical_uuid(derived):raise Fault(404,'not_found')
    if scope.get('method')!='GET':raise Fault(405,'method_not_allowed')
    query=scope.get('query_string',b'');need(type(query) is bytes and 1<=len(query)<=160,'invalid_dep_fasta_request',400)
    values={}
    for item in query.split(b'&'):
        parts=item.split(b'=');need(len(parts)==2,'invalid_dep_fasta_request',400)
        try:key,value=(p.decode('ascii') for p in parts)
        except UnicodeError:raise Fault(400,'invalid_dep_fasta_request') from None
        need(key not in values,'invalid_dep_fasta_request',400);values[key]=value
    expected={'mapping_version_id'} if name is None else {'mapping_version_id','expected_sha256'}
    need(set(values)==expected and canonical_uuid(values.get('mapping_version_id')),'invalid_dep_fasta_request',400)
    if name is not None:need(re.fullmatch('[0-9a-f]{64}',values['expected_sha256']) is not None,'invalid_dep_fasta_request',400)
    return project,derived,name,values

class DEPFastaHandoffHTTP:
    def __init__(self,app=None,*,enabled=False):
        need(type(enabled) is bool,'dep_fasta_unavailable')
        self.app=app;self.enabled=enabled
        self.authority=None if not enabled or app is None else app.state.authority
        self.helper=OwnerFastaHandoff(app,Config(True)) if enabled and app is not None else None
    @staticmethod
    def owns(scope):
        path=scope.get('path','')
        return type(path) is str and re.match(r'^/api/v1/projects/[^/]+/differential-classifications/[^/]+/fasta-handoff(?:/|$)',re.sub('/+','/',unquote(path))) is not None
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
            if not self.enabled or self.helper is None or self.authority is None:return await reject(Fault(503,'dep_fasta_unavailable'))
            project,derived,name,values=route_input(scope)
            length=validate_headers(scope,'GET')
            service_deadline=time.monotonic()+9
            budget=import_budget(scope,'GET');budget.deadline=min(budget.deadline,service_deadline);budget.check()
        except asyncio.CancelledError:raise
        except Exception as error:return await reject(error)
        ready=asyncio.get_running_loop().create_future()
        async def pump():
            while True:
                message=await receive();budget.check()
                if message['type']=='http.disconnect':raise Gone()
                if message['type']!='http.request' or ready.done() or message.get('body',b'')!=b'':raise Fault(400,'invalid_dep_fasta_request')
                if not message.get('more_body',False):
                    if length not in (None,0):raise Fault(400,'invalid_dep_fasta_request')
                    ready.set_result(None)
        async def work():
            request=Request(scope);subject=await self.authority.verify(request,budget)
            need(canonical_uuid(subject),'unauthenticated',401)
            left=jwt_payload(request.headers.getlist('authorization')[0][7:])['exp']-time.time()
            if not math.isfinite(left) or left<=0:raise Fault(401,'unauthenticated')
            budget.deadline=min(budget.deadline,time.monotonic()+left)
            await ready;budget.check()
            bundle=await self.helper.assemble(subject,project,derived,values['mapping_version_id'],budget);budget.check()
            need(type(bundle) is Bundle and type(bundle.descriptor_json) is bytes and len(bundle.descriptor_json)>0)
            need(len(bundle.descriptor_json)<=65536,'dep_fasta_too_large',413)
            descriptor=json.loads(bundle.descriptor_json)
            need(descriptor['contract']=='dep_fasta_handoff_v0.1' and descriptor['created_by']==subject and descriptor['project_id']==project
                and descriptor['classification']['derived_version_id']==derived and descriptor['mapping']['mapping_version_id']==values['mapping_version_id'])
            bodies={'membership.tsv':bundle.membership_tsv,'queries.fasta':bundle.queries_fasta}
            need(type(descriptor['outputs']) is list and [v['name'] for v in descriptor['outputs']]==['membership.tsv','queries.fasta'])
            need(all(type(body) is bytes for body in bodies.values()))
            need(all(len(body)<=CAP[n] for n,body in bodies.items()) and sum(map(len,bodies.values()))<=33554432,'dep_fasta_too_large',413)
            for item in descriptor['outputs']:
                n=item['name'];body=bodies[n]
                need(item['media_type']==MEDIA[n] and type(item['bytes']) is int and item['bytes']==len(body) and item['sha256']==sha(body))
            budget.check()
            if name is None:
                return Response(bundle.descriptor_json,headers={**SECURITY,'Content-Type':'application/json','Content-Length':str(len(bundle.descriptor_json)),
                    'X-Cyomics-File-SHA256':sha(bundle.descriptor_json)})
            body=bodies[name];item=next(v for v in descriptor['outputs'] if v['name']==name)
            if item['sha256']!=values['expected_sha256']:raise Fault(409,'dep_fasta_output_changed')
            return Response(body,headers={**SECURITY,'Content-Type':MEDIA[name],'Content-Length':str(len(body)),
                'X-Cyomics-File-SHA256':item['sha256'],'Content-Disposition':'attachment; filename="'+name+'"'})
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
            # A zero-byte query export still needs one terminal ASGI body frame.
            for at in range(0,max(1,len(body)),65536):
                budget.check()
                if watcher.done():await watcher
                await budget.run(lambda at=at:send({'type':'http.response.body','body':body[at:at+65536],'more_body':at+65536<len(body)}))
        except Gone:pass
        except asyncio.CancelledError:raise
        except Exception as error:await reject(TimeoutError() if budget.remaining()<=0 else error)
        finally:
            for task in (watcher,worker):
                if not task.done():task.cancel()
                task.add_done_callback(consume_task)
            await asyncio.sleep(0)
