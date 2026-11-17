import asyncio
import math
import re
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from .identity import Authority
from .upload_validation import classify
from .raw_uploads import RawUploads
from .import_http import CompleteImports
from .sample_http import SampleDesigns
from .repository import Repository, FixedPool
from .settings import Budget, Fault, consume_task, production_gate, load_approved_anon_key, unavailable
from .validation import canonical_uuid, list_query, project_input


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


class ProjectBoundary:
    """Bound streamed input, watch disconnects and withhold late success bodies."""
    def __init__(self, app, uploads=None, imports=None, samples=None):
        self.app = app
        self.uploads = uploads
        self.imports = imports
        self.samples = samples

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        if self.samples is not None and self.samples.owns(scope):
            return await self.samples(scope, receive, send)
        if self.imports is not None and self.imports.owns(scope):
            return await self.imports(scope,receive,send)
        if self.uploads is not None and self.uploads.owns(scope):
            return await self.uploads(scope, receive, send)
        try:
            budget = entry_budget(scope)
        except Fault as fault:
            return await error_response(fault)(scope, receive, send)
        except Exception:
            return await error_response(unavailable())(scope, receive, send)
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
                if output_bytes > 1048576:
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
                await error_response(fault)(scope, bounded_receive, send)
        except Exception:
            if not sent:
                await error_response(budget.create.failure())(scope, bounded_receive, send)
        finally:
            for task in (worker,watcher):
                if not task.done():
                    task.cancel()
                task.add_done_callback(consume_task)
            # Let cancellation close/discard an active lease; do not await an
            # unbounded cancellation handshake beyond the request deadline.
            await asyncio.sleep(0)


def create_app(*, auth_transport=None, pool=None, anon_key_loader=None, uploads_enabled=False, imports_enabled=False, import_runner=None, sample_designs_enabled=False):
    """DI changes transports or CPU scheduling only; every request uses Authority.verify.

    No preverified-user, test header, environment URL or identity bypass exists.
    The default production factory fails before clients, files or sockets open.
    """
    if auth_transport is None or pool is None or anon_key_loader is None:
        production_gate()
        # Activation requires a later source review and approved ANON loader.
        pool = FixedPool()
        anon_key_loader = load_approved_anon_key
    authority = Authority(auth_transport, anon_key_loader)
    repository = Repository(pool)
    imports = CompleteImports(authority,pool,enabled=imports_enabled,runner=import_runner)

    @asynccontextmanager
    async def lifespan(app):
        try:
            imports.start()
            yield
        finally:
            imports.close()
            await authority.close()
            pool.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False, debug=False, lifespan=lifespan)
    app.state.authority = authority
    app.state.repository = repository
    app.state.complete_imports = imports
    samples = SampleDesigns(authority, pool, enabled=sample_designs_enabled)
    app.state.sample_designs = samples
    app.add_middleware(ProjectBoundary, uploads=RawUploads(authority, pool, entry_budget, enabled=uploads_enabled), imports=imports, samples=samples)

    @app.exception_handler(HTTPException)
    async def framework_error(request, exc):
        return error_response(Fault(405,'method_not_allowed') if exc.status_code == 405 else Fault(404,'not_found'))

    @app.exception_handler(RequestValidationError)
    async def framework_validation(request, exc):
        return error_response(Fault(400,'invalid_project'))

    async def endpoint(request: Request):
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

    methods = ['GET','POST','HEAD','PUT','PATCH','DELETE','OPTIONS','TRACE','CONNECT']
    app.add_api_route('/api/v1/projects', endpoint, methods=methods)
    app.add_api_route('/api/v1/projects/{tail:path}', endpoint, methods=methods)
    return app
