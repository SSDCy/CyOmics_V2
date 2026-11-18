"""Read-only differential input projection behind the existing ProjectBoundary.

No R, scheduler, persistence, input TSV response or alternative identity path.
The server factory supplies repositories; browser selection is never authority.
"""
import asyncio
import hashlib
import math
import time
from fastapi import Request
from starlette.responses import JSONResponse
from .identity import jwt_payload
from .import_http import SECURITY
from .sample_http import validate_headers
from .settings import Fault
from .validation import canonical_uuid, strict_json
from .quant_diff_saved_input import Selection, SavedInputAssembly
from .quant_diff_input import Result, BoundInput

CONTRACT = 'differential_input_review_v0.1'
KEYS = frozenset(('processed_dataset_version_id', 'sample_design_version_id',
                  'sample_design_request_id', 'contrast', 'replicate_model'))
METHODS = ['GET', 'POST', 'HEAD', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE', 'CONNECT']


def selection_input(raw):
    if type(raw) is not bytes or not 1 <= len(raw) <= 8192:
        raise Fault(400, 'invalid_differential_input')
    try:
        body = strict_json(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise Fault(400, 'invalid_differential_input') from None
    if (type(body) is not dict or set(body) != KEYS
            or not all(canonical_uuid(body[k]) for k in (
                'processed_dataset_version_id', 'sample_design_version_id', 'sample_design_request_id'))
            or type(body['contrast']) is not dict
            or type(body['replicate_model']) is not str
            or not 1 <= len(body['replicate_model']) <= 64):
        raise Fault(400, 'invalid_differential_input')
    # The original binder validates contrast and model; no guessed/default model.
    return Selection(**body)


def response_value(project, selection, result):
    if (type(result) is not Result or type(result.model_input_satisfied) is not bool
            or result.source_analysis_ready is not False
            or type(result.reasons) is not tuple
            or not all(type(code) is str and 1 <= len(code) <= 100 for code in result.reasons)):
        raise Fault(503, 'differential_input_unavailable')
    summary = None
    if result.model_input_satisfied:
        bound = result.bound
        if type(bound) is not BoundInput or result.reasons or bound.source_analysis_ready is not False:
            raise Fault(503, 'differential_input_unavailable')
        def pin(body):
            return {'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
        summary = {
            'row_count': len(bound.rows), 'sample_count': len(bound.samples),
            'design_rank': bound.design_rank,
            'contrast': bound.contrast.to_payload(),
            'input_pins': {'matrix': pin(bound.matrix_tsv), 'design': pin(bound.design_tsv),
                           'preset': pin(bound.preset_tsv)},
        }
    elif result.bound is not None or not result.reasons:
        raise Fault(503, 'differential_input_unavailable')
    return {'schemaVersion': 1, 'contract': CONTRACT, 'project_id': project,
            'processed_dataset_version_id': selection.processed_dataset_version_id,
            'sample_design_version_id': selection.sample_design_version_id,
            'sample_design_request_id': selection.sample_design_request_id,
            'model_input_satisfied': result.model_input_satisfied,
            'reasons': list(result.reasons), 'source_analysis_ready': False,
            'input_summary': summary, 'calculation_started': False}


def install(app, authority, assembly=None):
    async def endpoint(request: Request):
        budget = request.scope['cyomics.budget']
        try:
            project = request.path_params['project_id']
            if not canonical_uuid(project):
                raise Fault(404, 'not_found')
            expected = ('/api/v1/projects/' + project + '/differential-input').encode('ascii')
            if (request.scope.get('raw_path') != expected
                    or request.scope.get('root_path', '') or request.scope.get('query_string', b'')):
                raise Fault(404, 'not_found')
            if request.method != 'POST':
                raise Fault(405, 'method_not_allowed')
            if assembly is None:
                raise Fault(503, 'differential_input_unavailable')
            if type(assembly) is not SavedInputAssembly:
                raise Fault(503, 'differential_input_unavailable')
            # Existing bridge-origin, Host, content and forbidden identity headers.
            validate_headers(request.scope, 'POST')
            subject = await authority.verify(request, budget)
            left = jwt_payload(request.headers.getlist('authorization')[0][7:])['exp'] - time.time()
            if not math.isfinite(left) or left <= 0:
                raise Fault(401, 'unauthenticated')
            budget.deadline = min(budget.deadline, time.monotonic() + left)
            selection = selection_input(await request.body())
            result = await assembly.assemble(subject, project, selection, budget)
            budget.check()
            return JSONResponse(response_value(project, selection, result), headers=SECURITY)
        except Fault as fault:
            return JSONResponse({'error': {'code': fault.code}}, status_code=fault.status, headers=SECURITY)
        except asyncio.CancelledError:
            raise
        except Exception:
            return JSONResponse({'error': {'code': 'differential_input_unavailable'}}, status_code=503, headers=SECURITY)

    # Registered before the existing generic project-tail route, under the same
    # streamed 8KiB body/deadline/disconnect/late-output ProjectBoundary.
    app.add_api_route('/api/v1/projects/{project_id}/differential-input', endpoint, methods=METHODS)
