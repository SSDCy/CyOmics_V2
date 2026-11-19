"""Original service Auth/Owner boundary for already saved volcano bytes only."""
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
from . import volcano_owner_read as plots

def owns(scope):
    path=scope.get('path','')
    return type(path) is str and re.match(r'^/api/v1/projects/[^/]+/derived-classifications/[^/]+/volcano(?:/|$)',re.sub('/+','/',unquote(path))) is not None

def error_response(error):
    status,code=getattr(error,'status',None),getattr(error,'code',None)
    if (status,code) in ((401,'unauthenticated'),(403,'auth_origin_rejected')):
        return classification_error(error)
    if isinstance(error,TimeoutError) or status==504:status,code=504,'r_volcano_read_timeout'
    elif status in (400,413,415):status,code=400,'invalid_r_volcano_request'
    elif status==404:code='not_found'
    elif status==405:code='method_not_allowed'
    else:status,code=503,'r_volcano_unavailable'
    return JSONResponse({'error':{'code':code}},status_code=status,headers=SECURITY)

def install(app,authority,reader=None):
    if reader is None:return # Preserve original unconfigured route behavior.
    async def endpoint(request:Request):
        budget=request.scope['cyomics.budget']
        try:
            p=request.path_params;project=p['project_id'];version=p['derived_version_id'];name=p.get('name')
            if not canonical_uuid(project) or not canonical_uuid(version) or name is not None and name not in plots.MEDIA:raise Fault(404,'not_found')
            expected='/api/v1/projects/'+project+'/derived-classifications/'+version+'/volcano'+('/artifacts/'+name if name else '')
            if request.scope.get('raw_path')!=expected.encode('ascii') or request.scope.get('root_path',''):raise Fault(404,'not_found')
            if request.method!='GET':raise Fault(405,'method_not_allowed')
            if request.scope.get('query_string',b''):raise Fault(400,'invalid_r_volcano_request')
            plots.need(type(reader) is plots.OwnerReader)
            validate_headers(request.scope,'GET');subject=await authority.verify(request,budget)
            left=jwt_payload(request.headers.getlist('authorization')[0][7:])['exp']-time.time()
            if not math.isfinite(left) or left<=0:raise Fault(401,'unauthenticated')
            budget.deadline=min(budget.deadline,time.monotonic()+left)
            if await request.body():raise Fault(400,'invalid_r_volcano_request')
            descriptor,body=await reader.read(subject,project,version,budget,name);budget.check()
            if name is None:
                response=JSONResponse(descriptor,headers=SECURITY)
                plots.need(len(response.body)<=plots.MAX_DESCRIPTOR);return response
            plots.need(type(body) is bytes and 0<len(body)<=plots.MAX_FILE)
            pin=next(p for p in descriptor['artifacts'] if p['name']==name)
            request.scope['cyomics.classification_artifact']=True
            return Response(body,headers={**SECURITY,'Content-Type':pin['media_type'],'Content-Length':str(len(body)),
                'X-Cyomics-File-SHA256':pin['sha256'],'Content-Disposition':('inline' if name=='volcano.png' else 'attachment')+'; filename="'+name+'"'})
        except asyncio.CancelledError:raise
        except Exception as error:return error_response(TimeoutError() if budget.remaining()<=0 else error)
    prefix='/api/v1/projects/{project_id}/derived-classifications/{derived_version_id}/volcano'
    app.add_api_route(prefix,endpoint,methods=METHODS)
    app.add_api_route(prefix+'/artifacts/{name}',endpoint,methods=METHODS)
