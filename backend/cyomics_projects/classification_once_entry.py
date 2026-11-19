"""Trusted fixed-shim child entry, default off. No gate or credential reader.
The existing Root Control supplies activate_factory/configure/verify; wire IPC
contains four UUIDs only and is never authentication or runtime permission.
"""
import asyncio,sys
from .settings import production_gate as _ORIGINAL_GATE,load_runtime_password as _ORIGINAL_LOADER
from .classification_on_demand import parse_notification,canonical
from .raw_qc_once_entry import _eligible_context,_State,_Signals,_settle_and_close,_Interrupted
from .raw_qc_dispatch import _context


def _payload(notification=None,*,disposition='off',reason='off',outcome=None,teardown='confirmed'):
    observation=None if outcome is None else outcome.observation
    return {'schemaVersion':1,'classification_job_id':None if notification is None else notification['classification_job_id'],
        'request_id':None if notification is None else notification['request_id'],
        'attempt_id':None if outcome is None else outcome.attempt_id,'disposition':disposition,'reason':reason,
        'business_state':None if observation is None else observation.state,
        'scientific_status':None if observation is None else observation.scientific_status,'teardown':teardown,
        'loop_closed':None,'pending_tasks_seen':False,'async_resources_remaining':False,'interrupted':False,
        'exit_code':0 if disposition=='confirmed' else 2 if disposition=='off' else 1}

def need(value):
    if not value:raise RuntimeError('classification_child_entry_refused')

async def run_from_server(raw=None,*,activate_factory=None,configure=None,verify=None,cancelled=lambda:False):
    if raw is None or activate_factory is None or configure is None or verify is None:return _payload()
    from . import app as server,settings,repository,de_classification_jobs as jobs,de_classification_once as once
    from . import de_classification_repository as saved,de_classification_runner as runner
    from .de_classification_worker_once import ClassificationOnceWorker,Outcome
    from .settings import Budget
    notification=None;application=None;outcome=None;closed=True;factory_called=False
    original_factory=server.create_app;original_pool=repository.FixedPool
    def bindings():
        need(settings.production_gate is _ORIGINAL_GATE and server.production_gate is _ORIGINAL_GATE
            and settings.load_runtime_password is _ORIGINAL_LOADER and repository.load_runtime_password is _ORIGINAL_LOADER
            and server.create_app is original_factory and server.FixedPool is original_pool and repository.FixedPool is original_pool)
    try:
        notification=parse_notification(raw)
        need(callable(verify) and callable(configure) and callable(activate_factory) and callable(cancelled))
        need(_context(1000) and not any(task is not asyncio.current_task() and not task.done() for task in asyncio.all_tasks()))
        need(verify() is None);bindings()
        need(settings.AUTH_POLICY_VERIFIED is False and settings.RUNTIME_START_APPROVED is False)
        def factory():
            nonlocal application,factory_called
            need(not factory_called);factory_called=True;need(verify() is None);bindings();_ORIGINAL_GATE()
            application=original_factory() # No transport/pool/loader DI; features default off.
            return application
        returned=activate_factory(factory)
        need(factory_called and returned is application and application is not None)
        need(verify() is None);bindings();_ORIGINAL_GATE()
        pool=application.state.repository.pool;need(type(pool) is original_pool)
        dto=jobs.dto(await jobs.ClassificationAdmissionRepository(pool,enabled=True).read_job(
            notification['owner_id'],notification['project_id'],notification['classification_job_id'],Budget(40)))
        need((dto['created_by'],dto['project_id'],dto['request_id'],dto['classification_job_id'])==tuple(notification[k] for k in
            ('owner_id','project_id','request_id','classification_job_id')))
        need(verify() is None)
        config,scope_sha256=configure(dto) # Trusted fixed shim only, never IPC/module/env input.
        need(type(config) is runner.Config and config.enabled is True and callable(config.verify) and config.verify() is None)
        need((config.owner_id,config.project_id,config.job_id,config.source_result_id,config.source_binding_sha256,config.parameters_sha256)==
            (dto['created_by'],dto['project_id'],dto['classification_job_id'],dto['source_result_id'],dto['source_binding_sha256'],dto['parameters_sha256']))
        worker=ClassificationOnceWorker(config,scope_sha256,once.OnceRepository(pool,enabled=True),saved.ClassificationRepository(pool,saved.Config(True,config.verify)))
        outcome=await worker.run_once(cancelled=cancelled)
        need(type(outcome) is Outcome and outcome.job_id==dto['classification_job_id'] and outcome.request_id==dto['request_id'])
        need(verify() is None);bindings()
    except asyncio.CancelledError:raise
    except Exception:outcome=None
    finally:
        if application is not None:
            try:application.state.complete_imports.close()
            except BaseException:closed=False
            try:
                await application.state.authority.close();need(application.state.authority._client.is_closed is True)
            except asyncio.CancelledError:closed=False;raise
            except BaseException:closed=False
            finally:
                try:
                    pool=application.state.repository.pool;handles=tuple(pool._idle)+tuple(pool._active)
                    pool.close();need(not pool._active and all(c.closed is True for c in handles))
                except BaseException:closed=False
    if outcome is None:return _payload(notification,disposition='unknown',reason='entry_unknown',teardown='confirmed' if closed else 'unknown')
    return _payload(notification,disposition=outcome.disposition if closed else 'unknown',reason=outcome.reason if closed else 'server_resources_teardown_unknown',
        outcome=outcome,teardown='confirmed' if closed else 'unknown')

def main_from_server(raw=None,*,activate_factory=None,configure=None,verify=None,cancelled=lambda:False):
    """One synchronous MainThread entry called by Root's separately pinned shim."""
    notification=None;value=None;state=_State();loop=None;fence=_Signals(state)
    pending=resources=False;closed=None
    if raw is None or activate_factory is None or configure is None or verify is None:value=_payload()
    elif not _eligible_context():value=_payload(disposition='unknown',reason='dedicated_context_required',teardown='unknown')
    else:
        try:
            notification=parse_notification(raw);fence.install();loop=asyncio.new_event_loop()
            def quiet(_loop,_context):state.loop_error=True
            loop.set_exception_handler(quiet)
            value=loop.run_until_complete(run_from_server(raw,activate_factory=activate_factory,configure=configure,verify=verify,cancelled=cancelled))
        except BaseException as error:
            if isinstance(error,(asyncio.CancelledError,KeyboardInterrupt,_Interrupted)):state.interrupted=True
            state.loop_error=True;value=_payload(notification,disposition='unknown',reason='entry_unknown',teardown='unknown')
        finally:
            if loop is not None:pending,resources,closed=_settle_and_close(loop,state)
            fence.close()
    value.update(loop_closed=closed,pending_tasks_seen=pending,async_resources_remaining=resources,interrupted=state.interrupted)
    if loop is not None and (closed is not True or pending or resources or state.interrupted or state.loop_error or state.cleanup_error):
        value.update(disposition='unknown',reason='entry_teardown_unknown',teardown='unknown',exit_code=1)
    encoded=canonical(value)+b'\n'
    need(len(encoded)<=8192);sys.stdout.write(encoded.decode('utf8'))
    return value['exit_code']
