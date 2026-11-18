"""Queued differential admission/read under the ORIGINAL project boundary/Auth."""
import asyncio,math,time
from fastapi import Request
from starlette.responses import JSONResponse
from .identity import jwt_payload
from .sample_http import validate_headers
from .import_http import SECURITY
from .quant_diff_http import METHODS,response_value as review_response
from .settings import Fault
from .validation import canonical_uuid
from . import differential_jobs as jobs

def install(app,authority,service=None):
    async def endpoint(request:Request):
        budget=request.scope['cyomics.budget'];post=False
        try:
            project=request.path_params['project_id'];job=request.path_params.get('job_id')
            if not canonical_uuid(project) or job is not None and not canonical_uuid(job):raise Fault(404,'not_found')
            expected='/api/v1/projects/'+project+'/differential-jobs'+('/'+job if job else '')
            if request.scope.get('raw_path')!=expected.encode() or request.scope.get('root_path',''):raise Fault(404,'not_found')
            if request.method not in (('GET',) if job else ('GET','POST')):raise Fault(405,'method_not_allowed')
            post=request.method=='POST';query=request.scope.get('query_string',b'');request_id=None
            if post or job:
                if query:raise Fault(400,'invalid_differential_request')
            else:
                if not query.startswith(b'request_id=') or len(query)!=47:raise Fault(400,'invalid_differential_request')
                try:request_id=query[11:].decode('ascii')
                except UnicodeError:raise Fault(400,'invalid_differential_request') from None
                if not canonical_uuid(request_id):raise Fault(400,'invalid_differential_request')
            if type(service) is not jobs.DifferentialService or service.repository is None or service.assembly is None:
                raise Fault(503,'differential_unavailable')
            validate_headers(request.scope,request.method)
            subject=await authority.verify(request,budget)
            left=jwt_payload(request.headers.getlist('authorization')[0][7:])['exp']-time.time()
            if not math.isfinite(left) or left<=0:raise Fault(401,'unauthenticated')
            budget.deadline=min(budget.deadline,time.monotonic()+left)
            if not post and await request.body():raise Fault(400,'invalid_differential_request')
            if post:
                raw=await request.body();body,selection=jobs.request_input(raw)
                # Diagnostic disposition only, never identity/permission. Keep it
                # set through response delivery: an acknowledged DB commit alone
                # cannot prove the client received its queued response.
                request.scope['cyomics.differential_post_inflight']=True
                try:result=await service.submit(subject,project,raw,budget)
                except Fault as error:
                    if error.code!='differential_submission_unknown':request.scope['cyomics.differential_post_inflight']=False
                    raise
                if type(result) is not jobs.Admission:raise Fault(504,'differential_submission_unknown')
                if result.queued is None:
                    request.scope['cyomics.differential_post_inflight']=False
                    value=review_response(project,selection,result.review)
                    return JSONResponse({**value,'queued':False,'request_id':body['request_id']},status_code=422,headers=SECURITY)
                queued=result.queued
                if type(queued) is not jobs.Queued or not queued.commit_confirmed or not queued.teardown_confirmed:raise Fault(504,'differential_submission_unknown')
                value=jobs.task_value(queued.stored.public(),project=project,request_id=body['request_id'])
                status=201 if queued.created else 200
            elif job:
                value=await service.repository.read_job(subject,project,job,budget);status=200
            else:
                value=await service.repository.read_request(subject,project,request_id,budget);status=200
            budget.check();return JSONResponse(value,status_code=status,headers=SECURITY)
        except asyncio.CancelledError:raise
        except Fault as error:
            return JSONResponse({'error':{'code':error.code}},status_code=error.status,headers=SECURITY)
        except Exception:
            pending=request.scope.get('cyomics.differential_post_inflight') is True
            return JSONResponse({'error':{'code':'differential_submission_unknown' if pending else 'differential_unavailable'}},
                status_code=504 if pending else 503,headers=SECURITY)
    app.add_api_route('/api/v1/projects/{project_id}/differential-jobs',endpoint,methods=METHODS)
    app.add_api_route('/api/v1/projects/{project_id}/differential-jobs/{job_id}',endpoint,methods=METHODS)
