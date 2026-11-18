"""Default-off Owner GETs only. Original Authority/budget/repository boundaries.
No write Config, execution, source upload, scheduler or alternate identity.
"""
import asyncio,math,re,time
from urllib.parse import unquote
from fastapi import Request
from starlette.responses import JSONResponse,Response
from .identity import jwt_payload
from .import_http import SECURITY
from .sample_http import validate_headers
from .settings import Fault
from .validation import canonical_uuid
from .quant_diff_http import METHODS
from .de_classification_repository import ClassificationRepository,ARTIFACT_COLUMNS
from .de_classification_contract import pin

ARTIFACT_LIMIT=26*1024*1024
def owns(scope):
    path=scope.get('path','')
    return type(path) is str and re.match(r'^/api/v1/projects/[^/]+/(?:differential-classifications|de-member-sets)(?:/|$)',
        re.sub('/+','/',unquote(path))) is not None

def error_response(error):
    status,code=getattr(error,'status',None),getattr(error,'code',None)
    if isinstance(error,TimeoutError) or status==504:status,code=504,'de_classification_read_timeout'
    elif status in (400,413,415):status,code=400,'invalid_de_classification_request'
    elif status==404:
        if code not in ('not_found','de_classification_version_not_found','de_classification_source_not_found','de_classification_artifact_not_found'):
            code='de_classification_version_not_found'
    elif status==405:code='method_not_allowed'
    elif (status,code) in ((401,'unauthenticated'),(403,'auth_origin_rejected')):pass
    elif status==503 and code in ('de_classification_persisted_invalid','de_parent_binding_mismatch'):pass
    else:status,code=503,'de_classification_results_unavailable'
    return JSONResponse({'error':{'code':code}},status_code=status,headers=SECURITY)

def install(app,authority,repository=None):
    async def endpoint(request:Request):
        budget=request.scope['cyomics.budget']
        try:
            p=request.path_params;project=p['project_id'];version=p.get('derived_version_id');member=p.get('member_set_id');name=p.get('name')
            if not canonical_uuid(project) or any(not canonical_uuid(v) for v in (version,member) if v is not None):raise Fault(404,'not_found')
            if name is not None and name not in ARTIFACT_COLUMNS:raise Fault(404,'de_classification_artifact_not_found')
            suffix=('/de-member-sets/'+member) if member else '/differential-classifications'+('/'+version if version else '')+('/files/'+name if name else '')
            expected='/api/v1/projects/'+project+suffix
            if request.scope.get('raw_path')!=expected.encode('ascii') or request.scope.get('root_path',''):raise Fault(404,'not_found')
            if request.method!='GET':raise Fault(405,'method_not_allowed')
            query=request.scope.get('query_string',b'');lookup=None
            if version is None and member is None:
                if not query.startswith(b'request_id=') or len(query)!=47:raise Fault(400,'invalid_de_classification_request')
                try:lookup=query[11:].decode('ascii')
                except UnicodeError:raise Fault(400,'invalid_de_classification_request') from None
                if not canonical_uuid(lookup):raise Fault(400,'invalid_de_classification_request')
            elif query:raise Fault(400,'invalid_de_classification_request')
            if type(repository) is not ClassificationRepository or repository.owner.pool is None:raise Fault(503,'de_classification_results_unavailable')
            validate_headers(request.scope,'GET')
            subject=await authority.verify(request,budget)
            left=jwt_payload(request.headers.getlist('authorization')[0][7:])['exp']-time.time()
            if not math.isfinite(left) or left<=0:raise Fault(401,'unauthenticated')
            budget.deadline=min(budget.deadline,time.monotonic()+left)
            if await request.body():raise Fault(400,'invalid_de_classification_request')
            if member:
                value=await repository.read_member_set(subject,project,member,budget)
                value=value['member_set'] # Verified bytes deliberately NEVER JSON encoded.
            elif name:value=await repository.read_artifact(subject,project,version,name,budget)
            elif version:value=await repository.read_version(subject,project,version,budget)
            else:value=await repository.read_request(subject,project,lookup,budget)
            budget.check()
            if name is None:return JSONResponse(value,headers=SECURITY)
            if type(value) is not bytes or not 0<len(value)<=ARTIFACT_LIMIT:raise Fault(503,'de_classification_results_unavailable')
            # Internal response capability only AFTER Auth + Owner read + pins.
            request.scope['cyomics.classification_artifact']=True
            return Response(value,headers={**SECURITY,'Content-Type':'application/octet-stream',
                'Content-Disposition':'attachment; filename="'+name+'"','X-Cyomics-File-SHA256':pin(value)['sha256']})
        except asyncio.CancelledError:raise
        except Exception as error:return error_response(TimeoutError() if budget.remaining()<=0 else error)
    app.add_api_route('/api/v1/projects/{project_id}/differential-classifications',endpoint,methods=METHODS)
    app.add_api_route('/api/v1/projects/{project_id}/differential-classifications/{derived_version_id}',endpoint,methods=METHODS)
    app.add_api_route('/api/v1/projects/{project_id}/differential-classifications/{derived_version_id}/files/{name}',endpoint,methods=METHODS)
    app.add_api_route('/api/v1/projects/{project_id}/de-member-sets/{member_set_id}',endpoint,methods=METHODS)
