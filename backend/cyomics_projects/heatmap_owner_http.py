"""Original service Auth/Owner boundary for already saved heatmap bytes only."""
import asyncio,math,re,time
from urllib.parse import unquote
from fastapi import Request
from starlette.responses import JSONResponse,Response
from .identity import jwt_payload
from .import_http import SECURITY
from .sample_http import validate_headers
from .quant_diff_http import METHODS
from .settings import Fault
from .validation import canonical_uuid
from .de_classification_http import error_response as classification_error
from . import heatmap_owner_read as plots
POST_MARKER='cyomics.heatmap_post_inflight'

def owns(scope):
    path=scope.get('path','')
    return type(path) is str and re.match(r'^/api/v1/projects/[^/]+/derived-classifications/[^/]+/heatmap(?:/|$)',re.sub('/+','/',unquote(path))) is not None

def owns_render(scope):
    return owns(scope) and re.search(r'/heatmap/render(?:/|$)',re.sub('/+','/',unquote(scope.get('path','')))) is not None

def error_response(error,*,render=False,pending=False):
    status,code=getattr(error,'status',None),getattr(error,'code',None)
    if (status,code) in ((401,'unauthenticated'),(403,'auth_origin_rejected')):
        return classification_error(error)
    if render:
        if type(code) is str and code.startswith('r_volcano_'):code='r_heatmap_'+code[len('r_volcano_'):]
        if pending:status,code=504,'r_heatmap_submission_unknown'
        elif status==409 and code=='r_heatmap_capacity_busy':pass
        elif status==504 and code=='r_heatmap_submission_unknown':pass
        elif isinstance(error,TimeoutError) or status==504:status,code=504,'r_heatmap_read_timeout'
        elif status in (400,413,415):status,code=400,'invalid_r_heatmap_request'
        elif status==404:code='not_found'
        elif status==405:code='method_not_allowed'
        else:status,code=503,'r_heatmap_unavailable'
        return JSONResponse({'error':{'code':code}},status_code=status,headers=SECURITY)
    if isinstance(error,TimeoutError) or status==504:status,code=504,'r_heatmap_read_timeout'
    elif status in (400,413,415):status,code=400,'invalid_r_heatmap_request'
    elif status==404:code='not_found'
    elif status==405:code='method_not_allowed'
    else:status,code=503,'r_heatmap_unavailable'
    return JSONResponse({'error':{'code':code}},status_code=status,headers=SECURITY)

def install(app,authority,reader=None,renderer=None):
    if reader is None:return # Preserve original unconfigured route behavior.
    async def endpoint(request:Request):
        budget=request.scope['cyomics.budget']
        try:
            p=request.path_params;project=p['project_id'];version=p['derived_version_id'];name=p.get('name')
            if not canonical_uuid(project) or not canonical_uuid(version) or name is not None and name not in plots.MEDIA:raise Fault(404,'not_found')
            expected='/api/v1/projects/'+project+'/derived-classifications/'+version+'/heatmap'+('/artifacts/'+name if name else '')
            if request.scope.get('raw_path')!=expected.encode('ascii') or request.scope.get('root_path',''):raise Fault(404,'not_found')
            if request.method!='GET':raise Fault(405,'method_not_allowed')
            if request.scope.get('query_string',b''):raise Fault(400,'invalid_r_heatmap_request')
            plots.need(type(reader) is plots.OwnerReader)
            validate_headers(request.scope,'GET');subject=await authority.verify(request,budget)
            left=jwt_payload(request.headers.getlist('authorization')[0][7:])['exp']-time.time()
            if not math.isfinite(left) or left<=0:raise Fault(401,'unauthenticated')
            budget.deadline=min(budget.deadline,time.monotonic()+left)
            if await request.body():raise Fault(400,'invalid_r_heatmap_request')
            descriptor,body=await reader.read(subject,project,version,budget,name);budget.check()
            if name is None:
                response=JSONResponse(descriptor,headers=SECURITY)
                plots.need(len(response.body)<=plots.MAX_DESCRIPTOR);return response
            plots.need(type(body) is bytes and 0<len(body)<=plots.MAX_FILE)
            pin=next(p for p in descriptor['artifacts'] if p['name']==name)
            request.scope['cyomics.classification_artifact']=True
            return Response(body,headers={**SECURITY,'Content-Type':pin['media_type'],'Content-Length':str(len(body)),
                'X-Cyomics-File-SHA256':pin['sha256'],'Content-Disposition':('inline' if name=='heatmap.png' else 'attachment')+'; filename="'+name+'"'})
        except asyncio.CancelledError:raise
        except Exception as error:return error_response(TimeoutError() if budget.remaining()<=0 else error)
    prefix='/api/v1/projects/{project_id}/derived-classifications/{derived_version_id}/heatmap'
    if renderer is not None:
        from .heatmap_on_demand import Controller,request_input
        if type(renderer) is not Controller:raise Fault(503,'r_heatmap_unavailable')
        async def render_endpoint(request:Request):
            budget=request.scope['cyomics.budget']
            try:
                p=request.path_params;project=p['project_id'];version=p['derived_version_id']
                if not canonical_uuid(project) or not canonical_uuid(version):raise Fault(404,'not_found')
                expected='/api/v1/projects/'+project+'/derived-classifications/'+version+'/heatmap/render'
                if request.scope.get('raw_path')!=expected.encode('ascii') or request.scope.get('root_path',''):raise Fault(404,'not_found')
                if request.method not in ('POST','GET'):raise Fault(405,'method_not_allowed')
                validate_headers(request.scope,request.method);subject=await authority.verify(request,budget)
                left=jwt_payload(request.headers.getlist('authorization')[0][7:])['exp']-time.time()
                if not math.isfinite(left) or left<=0:raise Fault(401,'unauthenticated')
                budget.deadline=min(budget.deadline,time.monotonic()+left)
                query=request.scope.get('query_string',b'');raw=await request.body()
                if request.method=='POST':
                    if query:raise Fault(400,'invalid_r_heatmap_request')
                    body=request_input(raw)
                    def mark():request.scope[POST_MARKER]=True
                    value,created=await renderer.submit(subject,project,version,body,budget,mark_consumed=mark)
                    status=201 if created else 200
                else:
                    if raw or not query.startswith(b'request_id=') or len(query)!=47:raise Fault(400,'invalid_r_heatmap_request')
                    try:request_id=query[11:].decode('ascii')
                    except UnicodeError:raise Fault(400,'invalid_r_heatmap_request') from None
                    if not canonical_uuid(request_id):raise Fault(400,'invalid_r_heatmap_request')
                    value=await renderer.status(subject,project,version,request_id,budget);status=200
                budget.check();return JSONResponse(value,status_code=status,headers=SECURITY)
            except asyncio.CancelledError:raise
            except Exception as error:
                return error_response(error,render=True,pending=request.scope.get(POST_MARKER) is True)
        app.add_api_route(prefix+'/render',render_endpoint,methods=METHODS)
    app.add_api_route(prefix,endpoint,methods=METHODS)
    app.add_api_route(prefix+'/artifacts/{name}',endpoint,methods=METHODS)
