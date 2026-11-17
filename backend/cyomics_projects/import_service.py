"""Raw-backed import application service. No HTTP routes or import-time workers/I/O."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

from .import_report import CONTRACT_VERSION
from .import_persistence import (
    CONFIG_MAX_BYTES, ImportFault, build_candidate, canonical_json, check_ids,
    configuration, decode_version, digest, trusted_source, verify_parser_release, version_key,
    CURRENT_PROFILE, LEGACY_PROFILE,
)


class CooperativeImportRunner:
    """One worker, no queued job; at most two waiters may share one identical job.

    Create/use/close on one event loop. Only run() lazily creates a worker thread.
    Admission stays occupied until the underlying job actually finishes, including
    after its caller times out/disconnects. HTTP integration must own this lifecycle.
    """
    def __init__(self):
        self._executor = self._loop = self._stop = None
        self._busy = self._closed = False
        self._key = self._wrapped = None
        self._waiters = 0
        self._worker_done = False

    async def run(self, operation, budget, *, key=None):
        loop = asyncio.get_running_loop()
        if self._closed or self._loop is not None and self._loop is not loop:
            raise ImportFault(503,'import_executor_unavailable')
        if self._busy:
            if key is None or key != self._key or self._waiters >= 2 or self._stop.is_set():
                raise ImportFault(429,'import_busy')
            budget.check()
            wrapped, stopped = self._wrapped, self._stop
            self._waiters += 1
            return await self._wait(wrapped,stopped,budget)
        budget.check()
        self._loop = loop
        self._busy = True
        stopped = threading.Event()
        self._stop = stopped
        self._key = key
        self._waiters = 1
        self._worker_done = False
        try:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix='cyomics-import')
            future = self._executor.submit(operation,stopped.is_set)
        except Exception:
            self._busy = False; self._stop = self._key = None; self._waiters = 0
            raise ImportFault(503,'import_executor_unavailable') from None
        wrapped = asyncio.wrap_future(future)
        self._wrapped = wrapped
        def finished(_):
            def release():
                self._worker_done = True
                self._release_finished()
            try:loop.call_soon_threadsafe(release)
            except RuntimeError:pass  # Closing loop: never log a worker exception or resume work.
        future.add_done_callback(finished)
        def consume(value):
            if not value.cancelled():
                value.exception()
        wrapped.add_done_callback(consume)
        return await self._wait(wrapped,stopped,budget)

    async def _wait(self, wrapped, stopped, budget):
        try:
            return await budget.run(lambda:asyncio.shield(wrapped),abort=stopped.set)
        except asyncio.CancelledError:
            stopped.set(); raise
        except TimeoutError:
            stopped.set()
            raise ImportFault(504,'import_timeout') from None
        finally:
            self._waiters -= 1
            self._release_finished()

    def _release_finished(self):
        if self._worker_done and self._waiters == 0:
            self._busy = False; self._stop = self._key = self._wrapped = None

    def close(self):
        self._closed = True
        if self._stop is not None:
            self._stop.set()
        if self._executor is not None:
            self._executor.shutdown(wait=False,cancel_futures=True)


class ImportService:
    """Caller MUST supply Authority-verified subject. No caller provenance/config.

    Production runner is CooperativeImportRunner, shared across requests, never an
    inline ASGI callback. Test-only runners may execute synthetic work directly.
    cancelled(), if supplied, must be a thread-safe nonblocking bool predicate.
    """
    def __init__(self, repository, runner):
        self.repository, self.runner = repository, runner

    async def _run(self, subject, project_id, file_id, budget, cancelled, publish, profiles=(CURRENT_PROFILE,)):
        check_ids(subject,project_id,file_id)
        if cancelled is not None and not callable(cancelled):
            raise ImportFault(400,'invalid_import_request')
        def checkpoint(stopped=lambda:False):
            if stopped():
                raise asyncio.CancelledError()
            if budget.remaining() <= 0:
                raise ImportFault(504,'import_timeout')
            if cancelled is not None:
                try:
                    result = cancelled()
                except Exception:
                    raise ImportFault(503,'import_control_failed') from None
                if type(result) is not bool:
                    raise ImportFault(503,'import_control_failed')
                if result:
                    raise asyncio.CancelledError()
        checkpoint()
        config_json = canonical_json(configuration(),CONFIG_MAX_BYTES)
        raw = await self.repository.load_raw(subject,project_id,file_id,budget)
        existing = None
        for profile in profiles:
            selected_config = canonical_json(configuration(profile),CONFIG_MAX_BYTES)
            existing = await self.repository.lookup(subject,project_id,file_id,CONTRACT_VERSION,digest(selected_config),budget)
            if existing is not None:
                config_json = selected_config
                break
        key = version_key(file_id,config_json)
        def work(stopped):
            check = lambda:checkpoint(stopped)
            check(); verify_parser_release(check)
            source = trusted_source(raw,project_id,file_id,check)
            if existing is not None:
                result = decode_version(existing,source,config_json,key); check(); return result
            if not publish:
                raise ImportFault(404,'dataset_version_not_found')
            # Parser's control callback returns bool; checkpoint exceptions remain
            # safely mapped after parsing and never become publication permission.
            def control():
                check(); return False
            try:
                candidate = build_candidate(raw,source,config_json,key,check,control)
            except Exception:
                check()
                raise
            check(); return candidate
        result = await self.runner.run(work,budget,key=(subject,project_id,file_id,CONTRACT_VERSION,digest(config_json),publish))
        checkpoint()
        if type(result) is dict:
            return result
        return await self.repository.publish(subject,result,budget,checkpoint)

    async def _safe(self, operation):
        try:
            return await operation()
        except (ImportFault,asyncio.CancelledError):
            raise
        except TimeoutError:
            raise ImportFault(504,'import_timeout') from None
        except Exception:
            raise ImportFault(503,'import_unavailable') from None

    async def complete(self, subject, project_id, file_id, budget, cancelled=None):
        return await self._safe(lambda:self._run(subject,project_id,file_id,budget,cancelled,True))

    async def read(self, subject, project_id, file_id, budget, version_id=None, cancelled=None):
        return await self._safe(lambda:self._read(subject,project_id,file_id,budget,version_id,cancelled))

    async def _read(self, subject, project_id, file_id, budget, version_id, cancelled):
        check_ids(subject,project_id,file_id)
        profiles = (CURRENT_PROFILE, LEGACY_PROFILE)
        if version_id is not None:
            profiles = tuple(profile for profile in profiles if version_id == version_key(file_id,canonical_json(configuration(profile),CONFIG_MAX_BYTES)))
        if not profiles:
            # Authorize the project/file before exposing whether any version exists.
            check_ids(subject,project_id,file_id)
            await self.repository.load_raw(subject,project_id,file_id,budget)
            raise ImportFault(404,'dataset_version_not_found')
        return await self._run(subject,project_id,file_id,budget,cancelled,False,profiles)
