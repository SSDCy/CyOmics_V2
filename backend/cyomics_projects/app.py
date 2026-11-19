import asyncio
import math
import re
import time
from contextlib import asynccontextmanager
from urllib.parse import unquote
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from .identity import Authority
from .upload_validation import classify
from .raw_uploads import RawUploads
from .import_http import CompleteImports
from .sample_http import SampleDesigns
from .raw_qc_http import RawQCJobs
from .preprocess_http import PreprocessingJobs
from .preprocess_api_assembly import prepare_preprocess_api_config,bind_preprocess_api
from .raw_qc_artifact_http import RawQCArtifactHTTP
from .raw_qc_api_assembly import (prepare_raw_qc_api_config, bind_raw_qc_api, APIAssemblyRefused,
    prepare_raw_qc_launcher_config, bind_raw_qc_submit, close_raw_qc_live_resources)
from .repository import Repository, FixedPool
from .settings import Budget, Fault, consume_task, production_gate, load_approved_anon_key, unavailable
from .validation import canonical_uuid, list_query, project_input
from .de_classification_http import (owns as owns_classification, error_response as classification_error_response,
    ARTIFACT_LIMIT as CLASSIFICATION_ARTIFACT_LIMIT)
from .de_classification_jobs_http import (owns as owns_classification_jobs, error_response as classification_jobs_error_response,
    MARKER as CLASSIFICATION_POST_MARKER)


def error_response(fault):
    return JSONResponse({'error':{'code':fault.code}}, status_code=fault.status, headers={'Cache-Control':'no-store'})


class Disconnected(Exception):
    pass


def entry_budget(scope):
    """Internal timing hints only shorten the budget anchored at HTTP entry."""
    started = time.monotonic()
    if not math.isfinite(started):
        raise unavailable()
    wall = time.time()
    if not math.isfinite(wall):
        raise unavailable()
    names = (b'x-cyomics-remaining-ms', b'x-cyomics-deadline-unix-ms')
    values = {name: [] for name in names}
    for name, value in scope.get('headers', []):
        if isinstance(name, bytes) and name.lower() in values:
            values[name.lower()].append(value)
    profile = classify(scope)
    long = profile is not None and profile['long']
    seconds = 50.0 if long else 9.0
    max_hint = 55000 if long else 12000
    remaining, deadline = (values[name] for name in names)
    if remaining or deadline:
        if len(remaining) != 1 or len(deadline) != 1:
            raise Fault(400, 'invalid_project_query')
        remaining, deadline = remaining[0], deadline[0]
        if (not isinstance(remaining, bytes) or not isinstance(deadline, bytes)
                or re.fullmatch(rb'[1-9][0-9]{0,4}', remaining) is None
                or re.fullmatch(rb'[1-9][0-9]{12}', deadline) is None
                or int(remaining) > max_hint):
            raise Fault(400, 'invalid_project_query')
        seconds = min(seconds, int(remaining) / 1000, (int(deadline) - wall * 1000) / 1000)
    if seconds <= 0:
        raise unavailable()
    budget = Budget(seconds)
    if not math.isfinite(budget.deadline):
        raise unavailable()
    budget.deadline = min(budget.deadline, started + seconds)
    current = time.monotonic()
    if not math.isfinite(current) or current >= budget.deadline:
        raise unavailable()
    return budget


def _owns_raw_qc_artifact(scope):
    """Broad artifact-family dispatch only; the handler validates exact raw paths."""
    path = scope.get('path', '')
    if type(path) is not str:
        return False
    path = re.sub('/+', '/', unquote(path))
    return re.match(r'^/api/v1/raw-qc-jobs/[^/]+/artifacts(?:/|$)', path) is not None


class ProjectBoundary:
    """Bound streamed input, watch disconnects and withhold late success bodies."""
    def __init__(self, app, uploads=None, imports=None, samples=None, raw_qc=None, raw_qc_artifacts=None, preprocessing=None):
        self.app = app
        self.uploads = uploads
        self.imports = imports
        self.samples = samples
        self.raw_qc = raw_qc
        self.raw_qc_artifacts = raw_qc_artifacts
        self.preprocessing = preprocessing

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        classification=owns_classification(scope)
        classification_jobs=owns_classification_jobs(scope)
        if not (classification or classification_jobs) and self.raw_qc_artifacts is not None and _owns_raw_qc_artifact(scope):
            return await self.raw_qc_artifacts(scope, receive, send)
        if not (classification or classification_jobs) and self.raw_qc is not None and self.raw_qc.owns(scope):
            return await self.raw_qc(scope, receive, send)
        if not (classification or classification_jobs) and self.preprocessing is not None and self.preprocessing.owns(scope):
            return await self.preprocessing(scope, receive, send)
        if not (classification or classification_jobs) and self.samples is not None and self.samples.owns(scope):
            return await self.samples(scope, receive, send)
        if not (classification or classification_jobs) and self.imports is not None and self.imports.owns(scope):
            return await self.imports(scope,receive,send)
        if not (classification or classification_jobs) and self.uploads is not None and self.uploads.owns(scope):
            return await self.uploads(scope, receive, send)
        response_error=(lambda error: classification_jobs_error_response(error,
            pending=scope.get(CLASSIFICATION_POST_MARKER) is True,post=scope.get('method')=='POST')) if classification_jobs else classification_error_response if classification else error_response
        try:
            budget = entry_budget(scope)
        except Fault as fault:
            return await response_error(fault)(scope, receive, send)
        except Exception:
            return await response_error(unavailable())(scope, receive, send)
        scope['cyomics.budget'] = budget
        body_ready = asyncio.get_running_loop().create_future()
        output = []
        sent = False
        body_delivered = False
        output_bytes = 0

        async def pump():
            data = bytearray()
            while True:
                budget.check()
                message = await receive()
                if message['type'] == 'http.disconnect':
                    raise Disconnected()
                if message['type'] != 'http.request':
                    raise unavailable()
                data.extend(message.get('body', b''))
                if len(data) > 8192:
                    raise Fault(413,'project_body_too_large')
                if not message.get('more_body', False) and not body_ready.done():
                    body_ready.set_result(bytes(data))

        async def bounded_receive():
            nonlocal body_delivered
            if not body_delivered:
                value = await body_ready
                body_delivered = True
                return {'type':'http.request','body':value,'more_body':False}
            await asyncio.Future()  # disconnect is handled by the pump task

        async def buffered_send(message):
            nonlocal output_bytes
            if message['type'] == 'http.response.body':
                output_bytes += len(message.get('body', b''))
                limit=(CLASSIFICATION_ARTIFACT_LIMIT if classification and scope.get('cyomics.classification_artifact') is True
                    else 26214400 if scope.get('cyomics.differential_artifact') is True else 1048576)
                if output_bytes > limit:
                    raise unavailable()
            output.append(message)

        watcher = asyncio.create_task(pump())
        worker = asyncio.create_task(self.app(scope, bounded_receive, buffered_send))
        try:
            done, _ = await budget.run(lambda: asyncio.wait({watcher,worker}, return_when=asyncio.FIRST_COMPLETED))
            if watcher in done:
                await watcher
                raise unavailable()
            await worker
            budget.check()
            if not output or output[0]['type'] != 'http.response.start':
                raise unavailable()
            for message in output:
                await budget.run(lambda: send(message))
                sent = True
        except Disconnected:
            pass  # no late private response to a disconnected BFF
        except asyncio.CancelledError:
            raise
        except Fault as fault:
            if not sent:
                if classification_jobs and budget.remaining()<=0:fault=TimeoutError()
                await response_error(Fault(504,'differential_submission_unknown') if scope.get('cyomics.differential_post_inflight') is True else fault)(scope, bounded_receive, send)
        except Exception as error:
            if not sent:
                if classification_jobs:
                    await response_error(TimeoutError() if budget.remaining()<=0 or isinstance(error,TimeoutError) else unavailable())(scope, bounded_receive, send)
                elif classification:
                    await response_error(TimeoutError() if budget.remaining()<=0 else error if isinstance(error,TimeoutError) else unavailable())(scope, bounded_receive, send)
                else:
                    await response_error(Fault(504,'differential_submission_unknown') if scope.get('cyomics.differential_post_inflight') is True else budget.create.failure())(scope, bounded_receive, send)
        finally:
            for task in (worker,watcher):
                if not task.done():
                    task.cancel()
                task.add_done_callback(consume_task)
            # Let cancellation close/discard an active lease; do not await an
            # unbounded cancellation handshake beyond the request deadline.
            await asyncio.sleep(0)


def create_app(*, auth_transport=None, pool=None, anon_key_loader=None, uploads_enabled=False, imports_enabled=False, import_runner=None, sample_designs_enabled=False, raw_qc_repository=None, raw_qc_artifact_reader=None, raw_qc_api_config=None, raw_qc_launcher_config=None, preprocessing_repository=None, preprocessing_api_config=None, differential_inputs_enabled=False, differential_jobs_enabled=False, differential_results_enabled=False, classification_results_enabled=False, classification_jobs_enabled=False, classification_dispatch_config=None, differential_dispatch_config=None):
    """DI changes transports or CPU scheduling only; every request uses Authority.verify.

    No preverified-user, test header, environment URL or identity bypass exists.
    The default production factory fails before clients, files or sockets open.
    """
    from .classification_on_demand import prepare as prepare_classification_dispatch,Controller as ClassificationDispatcher,Capacity
    from .differential_on_demand import prepare as prepare_differential_dispatch,DifferentialController
    prepared_differential_dispatch=prepare_differential_dispatch(differential_dispatch_config)
    if prepared_differential_dispatch is not None and differential_jobs_enabled is not True:raise APIAssemblyRefused()
    dispatch_capacity=Capacity()
    differential_dispatch=None if prepared_differential_dispatch is None else DifferentialController(prepared_differential_dispatch,capacity=dispatch_capacity)
    prepared_classification_dispatch=prepare_classification_dispatch(classification_dispatch_config)
    if prepared_classification_dispatch is not None and classification_jobs_enabled is not True:raise APIAssemblyRefused()
    classification_dispatch=None if prepared_classification_dispatch is None else ClassificationDispatcher(prepared_classification_dispatch,capacity=dispatch_capacity)
    prepared_raw_qc = prepare_raw_qc_api_config(raw_qc_api_config)
    prepared_preprocessing = prepare_preprocess_api_config(preprocessing_api_config)
    if prepared_preprocessing is not None and preprocessing_repository is not None:
        raise APIAssemblyRefused()
    prepared_launcher = prepare_raw_qc_launcher_config(raw_qc_launcher_config)
    if prepared_launcher is not None and prepared_raw_qc is None:
        raise APIAssemblyRefused()
    if prepared_raw_qc is not None and (raw_qc_repository is not None or raw_qc_artifact_reader is not None):
        raise APIAssemblyRefused()
    if auth_transport is None or pool is None or anon_key_loader is None:
        production_gate()
        # Activation requires a later source review and approved ANON loader.
        pool = FixedPool()
        anon_key_loader = load_approved_anon_key
    if prepared_raw_qc is not None:
        if prepared_launcher is None:
            raw_qc_repository, raw_qc_artifact_reader = bind_raw_qc_api(prepared_raw_qc, pool)
        else:
            raw_qc_repository, raw_qc_artifact_reader = bind_raw_qc_api(prepared_raw_qc, pool, enqueue_observation_enabled=True)
    raw_qc_submit_repository, raw_qc_launcher = bind_raw_qc_submit(raw_qc_repository, prepared_launcher)
    if prepared_preprocessing is not None:
        preprocessing_repository=bind_preprocess_api(prepared_preprocessing,pool)
    if type(differential_inputs_enabled) is not bool or (differential_inputs_enabled and
            (preprocessing_repository is None or imports_enabled is not True or sample_designs_enabled is not True)):
        raise APIAssemblyRefused()
    if type(differential_jobs_enabled) is not bool or (differential_jobs_enabled and not differential_inputs_enabled):
        raise APIAssemblyRefused()
    if type(differential_results_enabled) is not bool:
        raise APIAssemblyRefused()
    if type(classification_results_enabled) is not bool:
        raise APIAssemblyRefused()
    if type(classification_jobs_enabled) is not bool:
        raise APIAssemblyRefused()
    authority = Authority(auth_transport, anon_key_loader)
    repository = Repository(pool)
    imports = CompleteImports(authority,pool,enabled=imports_enabled,runner=import_runner)

    @asynccontextmanager
    async def lifespan(app):
        try:
            imports.start()
            yield
        finally:
            try:
                try:
                    if differential_dispatch is not None:await differential_dispatch.close()
                finally:
                    if classification_dispatch is not None:await classification_dispatch.close()
            finally:
                if raw_qc_launcher is None:
                    imports.close()
                    await authority.close()
                    pool.close()
                else:
                    await close_raw_qc_live_resources(raw_qc_launcher, imports, authority, pool)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False, debug=False, lifespan=lifespan)
    app.state.authority = authority
    app.state.repository = repository
    app.state.complete_imports = imports
    app.state.raw_qc_single_launcher = raw_qc_launcher
    samples = SampleDesigns(authority, pool, enabled=sample_designs_enabled)
    app.state.sample_designs = samples
    raw_qc = RawQCJobs(authority, raw_qc_submit_repository)
    app.state.raw_qc_jobs = raw_qc
    artifacts = RawQCArtifactHTTP(authority, raw_qc_repository, raw_qc_artifact_reader)
    app.state.raw_qc_artifacts = artifacts
    preprocessing = PreprocessingJobs(authority, preprocessing_repository)
    app.state.preprocessing_jobs = preprocessing
    app.add_middleware(ProjectBoundary, uploads=RawUploads(authority, pool, entry_budget, enabled=uploads_enabled), imports=imports, samples=samples, raw_qc=raw_qc, raw_qc_artifacts=artifacts, preprocessing=preprocessing)

    @app.exception_handler(HTTPException)
    async def framework_error(request, exc):
        respond=classification_jobs_error_response if owns_classification_jobs(request.scope) else classification_error_response if owns_classification(request.scope) else error_response
        return respond(Fault(405,'method_not_allowed') if exc.status_code == 405 else Fault(404,'not_found'))

    @app.exception_handler(RequestValidationError)
    async def framework_validation(request, exc):
        respond=classification_jobs_error_response if owns_classification_jobs(request.scope) else classification_error_response if owns_classification(request.scope) else error_response
        return respond(Fault(400,'invalid_project'))

    async def endpoint(request: Request):
        if owns_classification_jobs(request.scope):
            return classification_jobs_error_response(Fault(404,'de_classification_job_not_found'))
        if owns_classification(request.scope):
            return classification_error_response(Fault(404,'not_found'))
        budget = request.scope['cyomics.budget']
        try:
            tail = request.path_params.get('tail')
            detail = tail is not None
            if detail and (not tail or '/' in tail):
                raise Fault(404,'not_found')
            if request.method not in (('GET',) if detail else ('GET','POST')):
                raise Fault(405,'method_not_allowed')
            raw_path = request.scope.get('raw_path')
            if not detail and raw_path != b'/api/v1/projects':
                raise Fault(404,'not_found')
            query = request.scope.get('query_string',b'')
            if detail:
                if query:
                    raise Fault(400,'invalid_project_query')
                if not canonical_uuid(tail) or raw_path != b'/api/v1/projects/' + tail.encode('ascii'):
                    raise Fault(404,'project_not_found')
            elif request.method == 'POST' and query:
                raise Fault(400,'invalid_project_query')
            body = await request.body()
            if request.method != 'POST' and body:
                raise Fault(400,'invalid_project_query')
            if request.method == 'POST':
                content_types = request.headers.getlist('content-type')
                if len(content_types) != 1 or content_types[0].lower() not in ('application/json','application/json; charset=utf-8'):
                    raise Fault(415,'unsupported_media_type')
            subject = await authority.verify(request, budget)
            if detail:
                value = await repository.detail(subject, tail, budget)
            elif request.method == 'GET':
                limit, cursor = list_query(query)
                value = await repository.list(subject, limit, cursor, budget)
            else:
                values = project_input(body)
                value = await repository.create(subject, values, budget)
            budget.check()
            return JSONResponse(value, status_code=201 if request.method == 'POST' else 200, headers={'Cache-Control':'no-store'})
        except Fault as fault:
            return error_response(fault)
        except asyncio.CancelledError:
            raise
        except Exception:
            return error_response(budget.create.failure())

    from .quant_diff_http import install as install_differential_input
    differential_input = None
    if differential_inputs_enabled:
        from .quant_diff_saved_input import SavedInputAssembly
        from .import_repository import ImportRepository
        differential_input = SavedInputAssembly(preprocessing_repository, ImportRepository(pool), samples.repository)
    app.state.differential_input = differential_input
    install_differential_input(app, authority, differential_input)
    from .differential_http import install as install_differential_jobs
    differential_jobs = None
    if differential_jobs_enabled:
        from .differential_jobs import DifferentialRepository, DifferentialService
        differential_jobs = DifferentialService(differential_input, DifferentialRepository(pool))
    app.state.differential_jobs = differential_jobs
    app.state.differential_dispatch=differential_dispatch
    install_differential_jobs(app, authority, differential_jobs,differential_dispatch)
    from .differential_result_http import install as install_differential_results
    differential_results = None
    if differential_results_enabled:
        from .differential_worker_once import ResultReader
        differential_results = ResultReader(pool)
    app.state.differential_results = differential_results
    install_differential_results(app, authority, differential_results)
    from .de_classification_http import install as install_classification_results
    classification_results = None
    if classification_results_enabled:
        from .de_classification_repository import ClassificationRepository
        classification_results = ClassificationRepository(pool) # NO write Config.
    app.state.classification_results = classification_results
    install_classification_results(app, authority, classification_results)
    from .de_classification_jobs_http import install as install_classification_jobs
    classification_jobs = None
    if classification_jobs_enabled:
        from .de_classification_jobs import ClassificationAdmissionRepository
        classification_jobs = ClassificationAdmissionRepository(pool, enabled=True)
    app.state.classification_jobs = classification_jobs
    app.state.classification_dispatch=classification_dispatch
    install_classification_jobs(app, authority, classification_jobs,classification_dispatch)

    methods = ['GET','POST','HEAD','PUT','PATCH','DELETE','OPTIONS','TRACE','CONNECT']
    app.add_api_route('/api/v1/projects', endpoint, methods=methods)
    app.add_api_route('/api/v1/projects/{tail:path}', endpoint, methods=methods)
    return app
