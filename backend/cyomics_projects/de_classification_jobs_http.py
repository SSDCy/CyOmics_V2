"""Default-off Owner admission POST/original-request GET. No execution route."""
import asyncio,math,re,time
from urllib.parse import unquote
from fastapi import Request
from starlette.responses import JSONResponse
from .identity import jwt_payload
from .import_http import SECURITY
from .sample_http import validate_headers
from .quant_diff_http import METHODS
from .settings import Fault
from .validation import canonical_uuid
from . import de_classification_jobs as jobs

MAX_REQUEST=8192
MARKER='cyomics.classification_post_inflight'
def owns(scope):
    path=scope.get('path','')
    return type(path) is str and re.match(r'^/api/v1/projects/[^/]+/classification-jobs(?:/|$)',re.sub('/+','/',unquote(path))) is not None

def error_response(error,*,pending=False,post=False):
    status,code=getattr(error,'status',None),getattr(error,'code',None)
    if pending:status,code=504,'de_classification_submission_unknown'
    elif isinstance(error,TimeoutError) or status==504:
        status,code=(503,'de_classification_jobs_unavailable') if post else (504,'de_classification_read_timeout')
    elif status in (400,413,415):status,code=400,'invalid_de_classification_request'
    elif status==404:
        if code not in ('de_classification_source_not_found','de_classification_job_not_found'):code='de_classification_job_not_found'
    elif status==405:code='method_not_allowed'
    elif (status,code) in ((401,'unauthenticated'),(403,'auth_origin_rejected'),(409,'de_classification_request_conflict')):pass
    elif status==503 and code in ('de_classification_persisted_invalid','de_parent_binding_mismatch'):pass
    else:status,code=503,'de_classification_jobs_unavailable'
    return JSONResponse({'error':{'code':code}},status_code=status,headers=SECURITY)

def install(app,authority,repository=None):
    async def endpoint(request:Request):
        budget=request.scope['cyomics.budget'];post=request.method=='POST'
        try:
            project=request.path_params['project_id']
            if not canonical_uuid(project):raise Fault(404,'de_classification_job_not_found')
            expected='/api/v1/projects/'+project+'/classification-jobs'
            if request.scope.get('raw_path')!=expected.encode('ascii') or request.scope.get('root_path',''):raise Fault(404,'de_classification_job_not_found')
            if request.method not in ('GET','POST'):raise Fault(405,'method_not_allowed')
            query=request.scope.get('query_string',b'');request_id=None
            if post:
                if query:raise Fault(400,'invalid_de_classification_request')
            else:
                if not query.startswith(b'request_id=') or len(query)!=47:raise Fault(400,'invalid_de_classification_request')
                try:request_id=query[11:].decode('ascii')
                except UnicodeError:raise Fault(400,'invalid_de_classification_request') from None
                if not canonical_uuid(request_id):raise Fault(400,'invalid_de_classification_request')
            if type(repository) is not jobs.ClassificationAdmissionRepository or repository.enabled is not True or repository.owner.pool is None:
                raise Fault(503,'de_classification_jobs_unavailable')
            length=validate_headers(request.scope,request.method)
            if length is not None and length>MAX_REQUEST:raise Fault(400,'invalid_de_classification_request')
            subject=await authority.verify(request,budget)
            left=jwt_payload(request.headers.getlist('authorization')[0][7:])['exp']-time.time()
            if not math.isfinite(left) or left<=0:raise Fault(401,'unauthenticated')
            budget.deadline=min(budget.deadline,time.monotonic()+left)
            raw=await request.body()
            if len(raw)>MAX_REQUEST or length is not None and length!=len(raw) or not post and raw:
                raise Fault(400,'invalid_de_classification_request')
            if post:
                body,spec=jobs.request_input(raw)
                budget.check()
                # Diagnostic disposition only, after validated explicit input
                # and Auth. Retain through response delivery, including Cancel.
                request.scope[MARKER]=True
                try:queued=await repository.enqueue(subject,project,raw,budget)
                except Fault as error:
                    if error.code!='de_classification_submission_unknown' and not repository.write_unknown:request.scope[MARKER]=False
                    raise
                if type(queued) is not jobs.Queued or type(queued.stored) is not jobs.StoredJob or queued.commit_confirmed is not True or queued.teardown_confirmed is not True:
                    raise Fault(504,'de_classification_submission_unknown')
                value=jobs.dto(queued.stored.public())
                if value['project_id']!=project or value['request_id']!=body['request_id'] or value['created_by']!=subject:
                    raise Fault(504,'de_classification_submission_unknown')
                status=201 if queued.created else 200
            else:
                value=jobs.dto(await repository.read_request(subject,project,request_id,budget));status=200
                if value['project_id']!=project or value['request_id']!=request_id or value['created_by']!=subject:
                    raise Fault(503,'de_classification_persisted_invalid')
            budget.check();response=JSONResponse(value,status_code=status,headers=SECURITY)
            if len(response.body)>8192:raise Fault(503,'de_classification_persisted_invalid')
            return response
        except asyncio.CancelledError:raise
        except Exception as error:
            return error_response(TimeoutError() if budget.remaining()<=0 else error,
                pending=request.scope.get(MARKER) is True,post=post)
    app.add_api_route('/api/v1/projects/{project_id}/classification-jobs',endpoint,methods=METHODS)
