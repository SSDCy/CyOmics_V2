"""Explicit Owner run/result/five-download reads; never claim or native execution."""
import asyncio,math,time
from fastapi import Request
from starlette.responses import JSONResponse,Response
from .identity import jwt_payload
from .sample_http import validate_headers
from .import_http import SECURITY
from .settings import Fault
from .validation import canonical_uuid
from .quant_diff_http import METHODS
from .differential_worker_once import ResultReader,ARTIFACT_COLUMNS,pin

def install(app,authority,reader=None):
    async def endpoint(request:Request):
        budget=request.scope['cyomics.budget']
        try:
            p=request.path_params;project=p['project_id'];job=p.get('job_id');result=p.get('result_id');name=p.get('name')
            if not canonical_uuid(project) or any(not canonical_uuid(v) for v in (job,result) if v is not None):raise Fault(404,'not_found')
            path='/api/v1/projects/'+project+('/differential-runs/'+job if job else '/differential-results'+('/'+result if result else '')+('/artifacts/'+name if name else ''))
            if request.scope.get('raw_path')!=path.encode() or request.scope.get('root_path',''):raise Fault(404,'not_found')
            if request.method!='GET':raise Fault(405,'method_not_allowed')
            query=request.scope.get('query_string',b'');lookup=None
            if not job and not result:
                if not query.startswith(b'job_id=') or len(query)!=43:raise Fault(400,'invalid_differential_result_request')
                try:lookup=query[7:].decode('ascii')
                except UnicodeError:raise Fault(400,'invalid_differential_result_request') from None
                if not canonical_uuid(lookup):raise Fault(400,'invalid_differential_result_request')
            elif query:raise Fault(400,'invalid_differential_result_request')
            if name is not None and name not in ARTIFACT_COLUMNS:raise Fault(404,'differential_artifact_not_found')
            if type(reader) is not ResultReader or reader.owner.pool is None:raise Fault(503,'differential_results_unavailable')
            validate_headers(request.scope,'GET');subject=await authority.verify(request,budget)
            left=jwt_payload(request.headers.getlist('authorization')[0][7:])['exp']-time.time()
            if not math.isfinite(left) or left<=0:raise Fault(401,'unauthenticated')
            budget.deadline=min(budget.deadline,time.monotonic()+left)
            if await request.body():raise Fault(400,'invalid_differential_result_request')
            if job:value=await reader.read_run(subject,project,job,budget)
            else:value=await reader.read(subject,project,budget,job_id=lookup,result_id=result,artifact=name)
            budget.check()
            if name is None:return JSONResponse(value,headers=SECURITY)
            request.scope['cyomics.differential_artifact']=True # Authenticated bounded output, never an Auth flag.
            return Response(value,headers={**SECURITY,'Content-Type':'application/octet-stream',
                'X-Cyomics-File-SHA256':pin(value)['sha256'],
                'Content-Disposition':'attachment; filename="'+name+'"'})
        except asyncio.CancelledError:raise
        except Fault as error:return JSONResponse({'error':{'code':error.code}},status_code=error.status,headers=SECURITY)
        except Exception:return JSONResponse({'error':{'code':'differential_results_unavailable'}},status_code=503,headers=SECURITY)
    app.add_api_route('/api/v1/projects/{project_id}/differential-runs/{job_id}',endpoint,methods=METHODS)
    app.add_api_route('/api/v1/projects/{project_id}/differential-results',endpoint,methods=METHODS)
    app.add_api_route('/api/v1/projects/{project_id}/differential-results/{result_id}',endpoint,methods=METHODS)
    app.add_api_route('/api/v1/projects/{project_id}/differential-results/{result_id}/artifacts/{name}',endpoint,methods=METHODS)
