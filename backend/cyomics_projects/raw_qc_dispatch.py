"""One explicit server action. No CLI, global runtime, polling or default IO."""
import asyncio
from dataclasses import asdict,dataclass
import os
import sys
import threading
from types import SimpleNamespace
from uuid import UUID

@dataclass(frozen=True,slots=True)
class Config:
    runtime:object
    roots:object
    expected_uid:int

@dataclass(frozen=True,slots=True)
class DispatchOutcome:
    action:str
    status:str
    reason:str
    attempt_id:str|None=None
    job_id:str|None=None
    teardown:str='confirmed'

RUN_REASONS=frozenset(('worker_unconfigured','dedicated_MainThread_required','invalid_server_roots',
 'execution_configuration_not_ready','confirmed_no_job','invalid_claim_receipt',
 'claim_outcome_requires_review','unresolved_attempt_never_resumes_R','terminal_readback_confirmed',
 'read_only_recovery_unresolved','succeeded','failed','executor_cleanup_unconfirmed',
 'worker_lock_held','durable_checkpoint_or_export_unconfirmed','attempt_requires_reconciliation'))
ACK_REASONS=frozenset(('worker_unconfigured','dedicated_MainThread_required','invalid_expected_ids',
 'invalid_server_roots','confirmed_checkpoint_reset_idle','acknowledgement_unconfirmed',
 'worker_lock_held','confirmed_ack_refused'))

def _id(value):
    try:return type(value) is str and str(UUID(value))==value
    except (ValueError,TypeError,AttributeError):return False

def _context(uid):
    return (os.name=='posix' and sys.platform.startswith('linux')
        and os.getuid()==os.geteuid()==uid and uid>0
        and threading.current_thread() is threading.main_thread() and threading.active_count()==1)

def _configuration_modules():
    # These do not import native runner code. Factory validates its complete
    # sibling bundle before the next stage imports worker/POSIX runner modules.
    from . import raw_qc_runtime as runtime,raw_qc_checkpoint as checkpoint
    return runtime,checkpoint

def _worker_modules():
    from .raw_qc_worker import WorkerOnce,OnceOutcome,AckOutcome
    from .raw_qc_worker_pool import WorkerPool,Config as PoolConfig
    from .raw_qc_repository import RawQCWorkerRepository
    from .raw_qc_worker_source import WorkerBoundSource
    from .raw_qc_input_export import BoundRaw,InputExporter
    from .raw_qc_artifacts import RawQCArtifacts
    from .r_raw_qc.raw_qc_runner import RawQCRunner,limits_metadata
    from .raw_qc_contract import ExecutionRegistration,execution_value
    from .settings import Budget
    return SimpleNamespace(**locals())

def _require(value):
    if not value:raise ValueError('dispatch_configuration_unavailable')

def _closed_pool(pool):
    # Current reviewed WorkerPool has no public close receipt. None from close()
    # alone proves nothing. No handle/credential is exposed by this predicate.
    return (pool._closing is True and pool._held is False
        and pool._lease is None and pool._uncertain is False)

async def dispatch_once(config=None,*,action='run_once',expected_attempt_id=None,expected_job_id=None):
    """Return a fixed outcome; never interpret it as permission for another action.

    Confirmed business state is preserved if teardown is unknown. Both fields
    must be checked by a controller. Caller owns its foreground event-loop/OS
    lifecycle; this callable does not implement hard process shutdown bounds.
    """
    safe_action=action if type(action) is str and action in ('run_once','ack_confirmed') else 'none'
    def out(status,reason,attempt=None,job=None,teardown='confirmed'):
        return DispatchOutcome(safe_action,status,reason,attempt,job,teardown)
    if config is None:return out('off','dispatch_unconfigured')
    if type(action) is not str or action not in ('run_once','ack_confirmed'):
        return out('off','invalid_dispatch_action')
    if (action=='run_once' and (expected_attempt_id is not None or expected_job_id is not None)
        or action=='ack_confirmed' and not (_id(expected_attempt_id) and _id(expected_job_id))):
        return out('off','invalid_dispatch_ids')
    if (type(config) is not Config or type(config.expected_uid) is not int or config.expected_uid<=0
        or config.runtime is None or config.roots is None):return out('off','invalid_dispatch_config')
    if not _context(config.expected_uid):return out('off','dedicated_MainThread_required')
    before=asyncio.all_tasks()
    if any(t is not asyncio.current_task() and not t.done() for t in before):
        # Do not build a replacement pool alongside unresolved prior work or an
        # ASGI/shared-loop controller. This callable owns no external tasks.
        return out('off','dispatch_loop_not_exclusive',teardown='unknown')
    pool=None;result=None;setup_unconfirmed=False
    try:
        runtime,checkpoint=_configuration_modules()
        _require(type(config.runtime) is runtime.Config and type(config.roots) is checkpoint.WorkerRoots)
        _require(config.runtime.expected_uid==config.expected_uid)
        roots=config.roots
        _require((config.runtime.input_root,config.runtime.staging_root,config.runtime.publish_root)==
                 (roots.inputs,roots.staging,roots.published))
        # These fixed helpers can acquire descriptors before raising, including
        # a close exception. No Pool yet does not prove their cleanup.
        setup_unconfirmed=True
        roots.verify()
        runner,registration=runtime.create_runtime(config.runtime)
        # A normal return completes this setup stage under the fixed helper
        # contracts; later Pool/task teardown remains independently checked.
        setup_unconfirmed=False
        m=_worker_modules()
        _require(type(runner) is m.RawQCRunner and type(registration) is m.ExecutionRegistration)
        execution=registration.snapshot();m.execution_value(execution)
        _require((runner.config.input_root,runner.config.staging_root,runner.config.publish_root)==
                 (roots.inputs,roots.staging,roots.published))
        _require(runner._quarantined is False)
        _require(asdict(runner.config.script_pin)==execution['script']
                 and asdict(runner.config.executable_pin)==execution['executable']
                 and runner.config.entry_kind==execution['entry_kind']
                 and runner.config.expected_r_version==execution['r_version'])
        metadata=m.limits_metadata(runner.config.limits)
        _require(metadata['limits_profile']==execution['limits_profile']
                 and metadata['limits_sha256']==execution['limits_sha256'])
        pool=m.WorkerPool(m.PoolConfig(expected_uid=config.expected_uid))
        worker=m.WorkerOnce(repository=m.RawQCWorkerRepository(pool),registration=registration,executor=runner,
            artifacts=m.RawQCArtifacts(roots.published,registration),
            source_loader=m.WorkerBoundSource(pool=pool,bound_raw_factory=m.BoundRaw),
            exporter=m.InputExporter(roots.inputs),roots=roots,budget_factory=m.Budget)
        if action=='run_once':
            value=await worker.run_once();expected_type=m.OnceOutcome
            statuses=('off','busy','idle','confirmed','reconciliation');reasons=RUN_REASONS
        else:
            value=await worker.acknowledge_confirmed(expected_attempt_id=expected_attempt_id,expected_job_id=expected_job_id)
            expected_type=m.AckOutcome;statuses=('off','refused','busy','unknown','acknowledged');reasons=ACK_REASONS
        _require(type(value) is expected_type and type(value.status) is str and value.status in statuses
                 and type(value.reason) is str and value.reason in reasons)
        _require(all(x is None or _id(x) for x in (value.attempt_id,value.job_id)))
        if action=='run_once' and value.status=='confirmed':
            _require(value.reason in ('succeeded','failed','terminal_readback_confirmed')
                     and _id(value.attempt_id) and _id(value.job_id))
        if action=='run_once' and value.status=='idle':
            _require(value.reason=='confirmed_no_job' and value.attempt_id is None and value.job_id is None)
        if action=='ack_confirmed':
            _require((value.attempt_id is None and value.job_id is None)
                or (value.attempt_id==expected_attempt_id and value.job_id==expected_job_id))
            if value.status=='acknowledged':_require(value.reason=='confirmed_checkpoint_reset_idle'
                and value.attempt_id==expected_attempt_id and value.job_id==expected_job_id)
        result=out(value.status,value.reason,value.attempt_id,value.job_id)
    except BaseException:
        # Never stringify exceptions, config, handles or source/credential data.
        result=out('off' if pool is None else 'unknown',
                   'dispatch_configuration_unavailable' if pool is None else 'dispatch_action_unconfirmed')
    finally:
        clean=not setup_unconfirmed
        if pool is not None:
            try:pool.close();clean=clean and _closed_pool(pool)
            except BaseException:clean=False
        try:
            await asyncio.sleep(0)  # one bounded scheduling turn, no cleanup loop
            clean=clean and not any(t not in before and not t.done() for t in asyncio.all_tasks())
        except BaseException:clean=False
        if result is not None and not clean:
            result=out(result.status,result.reason,result.attempt_id,result.job_id,'unknown')
    return result
