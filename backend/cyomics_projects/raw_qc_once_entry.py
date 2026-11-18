"""Dedicated one-job process entry. Default off; trusted startup objects only.

No configuration/secret reader, environment switch, alternate pool, R script,
registration, service, polling or ACK is implemented here. The ordinary fixed
dispatcher owns those existing component checks and its one WorkerOnce call.
"""
import asyncio
from dataclasses import dataclass
import json
import os
import signal
import sys
import threading
from uuid import UUID

_attempted = False
_uncertain_latched = False
_retained = ()  # private unresolved loop/task references until this process exits
_DISPATCH_EXTRA = frozenset(('dispatch_unconfigured','invalid_dispatch_action','invalid_dispatch_ids',
    'invalid_dispatch_config','dispatch_loop_not_exclusive','dispatch_configuration_unavailable','dispatch_action_unconfirmed'))


@dataclass(frozen=True, slots=True)
class EntryOutcome:
    status: str
    reason: str
    exit_code: int
    dispatch_called: bool = False
    dispatch_status: str | None = None
    dispatch_reason: str | None = None
    attempt_id: str | None = None
    job_id: str | None = None
    teardown: str = 'confirmed'
    loop_created: bool = False
    loop_closed: bool | None = None
    pending_tasks_seen: bool = False
    async_resources_remaining: bool = False
    interrupted: bool = False

    def to_payload(self):
        return {'schemaVersion':1,'action':'run_once','status':self.status,'reason':self.reason,
            'exit_code':self.exit_code,'dispatch_called':self.dispatch_called,
            'dispatch_status':self.dispatch_status,'dispatch_reason':self.dispatch_reason,
            'attempt_id':self.attempt_id,'job_id':self.job_id,'teardown':self.teardown,
            'loop_created':self.loop_created,'loop_closed':self.loop_closed,
            'pending_tasks_seen':self.pending_tasks_seen,'async_resources_remaining':self.async_resources_remaining,
            'interrupted':self.interrupted,'automatic_retry':False,'ACK_calls':0,'next_jobs':0,
            'Owner_or_API_E2E':False}


class _Interrupted(BaseException):
    pass


class _State:
    def __init__(self):
        self.interrupted = self.loop_error = self.cleanup_error = False


class _Signals:
    def __init__(self,state):
        self.state=state;self.saved=[]

    def handler(self,*unused):
        # Further signals do not interrupt the one best-effort cleanup path.
        first=not self.state.interrupted;self.state.interrupted=True
        if first:raise _Interrupted()

    def install(self):
        for number in (signal.SIGINT,signal.SIGTERM):
            previous=signal.getsignal(number)
            self.saved.append((number,previous))
            signal.signal(number,self.handler)

    def close(self):
        while self.saved:
            number,previous=self.saved.pop()
            try:signal.signal(number,previous)
            except BaseException:self.state.cleanup_error=True


def _load_dispatcher():
    # Fixed adjacent public module only. No arbitrary module/path/import string.
    from . import raw_qc_dispatch
    return raw_qc_dispatch


def _eligible_context():
    if not (os.name=='posix' and sys.platform.startswith('linux') and sys.dont_write_bytecode
        and os.getuid()==os.geteuid()==1000 and threading.current_thread() is threading.main_thread()
        and threading.active_count()==1):return False
    try:asyncio.get_running_loop()
    except RuntimeError:return True
    return False


def _uuid(value):
    try:return type(value) is str and str(UUID(value))==value
    except (ValueError,TypeError,AttributeError):return False


def _validated(value,module):
    if (type(value) is not module.DispatchOutcome or value.action!='run_once'
        or type(value.status) is not str or value.status not in ('off','busy','idle','confirmed','reconciliation','unknown')
        or type(value.reason) is not str or value.reason not in module.RUN_REASONS|_DISPATCH_EXTRA
        or type(value.teardown) is not str or value.teardown not in ('confirmed','unknown')
        or not all(v is None or _uuid(v) for v in (value.attempt_id,value.job_id))):return None
    if value.status=='confirmed' and not (value.reason in ('succeeded','failed','terminal_readback_confirmed')
        and _uuid(value.attempt_id) and _uuid(value.job_id)):return None
    if value.status=='idle' and not (value.reason=='confirmed_no_job' and value.attempt_id is None and value.job_id is None):return None
    return value


def _consume(task):
    try:
        if not task.cancelled():task.exception()
    except BaseException:pass


def _new_loop():
    return asyncio.new_event_loop()


def _settle_and_close(loop,state):
    """One cancellation/scheduling turn, no shutdown gather or retry loop.

    Any pending task at this boundary is uncertainty even if that turn consumes
    it. Do not wait indefinitely for a driver suppressing cancellation.
    """
    global _retained
    pending_seen=resources=False;closed=False
    try:
        pending=tuple(asyncio.all_tasks(loop));pending_seen=bool(pending)
        for task in pending:
            task.add_done_callback(_consume)
            task.cancel()
        if pending:
            loop.call_soon(loop.stop)
            loop.run_forever()
        remaining=tuple(asyncio.all_tasks(loop))
        if remaining:_retained=remaining+(loop,)
        # These are fixed stdlib loop implementation details on the reviewed
        # Python runtime, not a claim about arbitrary custom event-loop types.
        missing=object()
        generators=getattr(loop,'_asyncgens',missing)
        executor=getattr(loop,'_default_executor',missing)
        resources=(generators is missing or executor is missing or bool(generators) or executor is not None)
        if resources:_retained=_retained+(loop,)
    except BaseException:
        state.cleanup_error=True;pending_seen=True;_retained=_retained+(loop,)
    try:
        loop.close() # exactly one attempt; an exception is never washed by is_closed
        closed=loop.is_closed() is True
        if not closed:state.cleanup_error=True
    except BaseException:
        state.cleanup_error=True;_retained=_retained+(loop,)
    return pending_seen,resources,closed


def _refused(reason,*,off=False):
    if _uncertain_latched:return EntryOutcome('unknown','prior_entry_disposition_unknown',1,teardown='unknown')
    return EntryOutcome('off' if off else 'refused',reason,2)


def run_configured_once(config=None):
    """Terminal foreground-process action; no public dependency injection seam.

    A valid original dispatch.Config really reaches ordinary dispatch_once.
    Caller-created config is not startup approval, Owner proof or a claim filter.
    A new process MUST NOT be respawned after unknown without disposition.
    """
    global _attempted,_uncertain_latched,_retained
    if _attempted:return _refused('entry_already_used')
    if config is None:return _refused('entry_unconfigured',off=True)
    module=None
    try:
        module=_load_dispatcher()
        if (type(config) is not module.Config or type(config.expected_uid) is not int or config.expected_uid!=1000
            or config.runtime is None or config.roots is None):return _refused('invalid_entry_config')
        if not _eligible_context():return _refused('dedicated_linux_MainThread_process_required')
    except BaseException:return _refused('entry_configuration_unavailable')
    _attempted=True
    state=_State();fence=_Signals(state);loop=None;root_task=None;called=False;value=None
    loop_created=False;loop_closed=None;pending=resources=False
    try:
        fence.install()
        loop=_new_loop();loop_created=True
        def quiet(_loop,_context):state.loop_error=True
        loop.set_exception_handler(quiet)
        # Direct sole task: wait_for/timeout wrappers would break dispatch's
        # exclusive-loop gate. Existing RPC/R limits remain inside the chain.
        operation=module.dispatch_once(config,action='run_once')
        try:root_task=loop.create_task(operation)
        except BaseException:
            # Fresh standard loop: a dispatch coroutine not accepted as a task
            # has not executed its body. Close it, never invoke a second copy.
            if asyncio.iscoroutine(operation):operation.close()
            raise
        called=True
        value=_validated(loop.run_until_complete(root_task),module)
    except BaseException as error:
        if isinstance(error,(asyncio.CancelledError,KeyboardInterrupt,_Interrupted)):state.interrupted=True
        state.loop_error=True
    finally:
        if loop is not None:
            pending,resources,loop_closed=_settle_and_close(loop,state)
        try:fence.close()
        except BaseException:state.cleanup_error=True
    unknown=(value is None or value.teardown!='confirmed' or state.interrupted or state.loop_error
        or state.cleanup_error or pending or resources or loop_closed is not True)
    fields=dict(dispatch_called=called,dispatch_status=None if value is None else value.status,
        dispatch_reason=None if value is None else value.reason,attempt_id=None if value is None else value.attempt_id,
        job_id=None if value is None else value.job_id,teardown='unknown' if unknown else 'confirmed',
        loop_created=loop_created,loop_closed=loop_closed,pending_tasks_seen=pending,
        async_resources_remaining=resources,interrupted=state.interrupted)
    if unknown:
        _uncertain_latched=True
        return EntryOutcome('cancelled' if state.interrupted else 'unknown',
            'entry_interrupted' if state.interrupted else 'entry_or_dispatch_teardown_unconfirmed',1,**fields)
    if value.status=='confirmed':
        # Recovery's terminal_readback_confirmed does not encode succeeded vs
        # failed in DispatchOutcome. Do not invent that missing business fact.
        status={'succeeded':'confirmed_succeeded','failed':'confirmed_failed',
            'terminal_readback_confirmed':'confirmed_terminal'}[value.reason]
        return EntryOutcome(status,value.reason,0 if status=='confirmed_succeeded' else 4,**fields)
    if value.status=='idle':return EntryOutcome('idle','confirmed_no_job',3,**fields)
    return EntryOutcome('refused' if value.status in ('off','busy') else 'unconfirmed',
        value.reason,4,**fields)


def _terminate_unknown():
    # Only this process. Never kill a numeric worker/R/PG PID or claim their exit.
    # Avoid interpreter shutdown joining a new executor or finalizing unresolved
    # cancellation-ignoring tasks after a fixed UNKNOWN receipt has been flushed.
    os._exit(1)


def main(argv=None,*,config=None):
    args=tuple(sys.argv[1:]) if argv is None else argv
    if type(args) not in (list,tuple) or any(type(v) is not str for v in args) or tuple(args) not in ((),('--once',)):
        outcome=_refused('invalid_entry_arguments')
    elif not args and config is not None:
        outcome=_refused('explicit_once_required')
    else:
        outcome=run_configured_once(config)
    try:
        sys.stdout.write(json.dumps(outcome.to_payload(),sort_keys=True,separators=(',',':'))+'\n')
        sys.stdout.flush()
    except BaseException:
        _terminate_unknown()
        return 1
    if outcome.teardown=='unknown':_terminate_unknown()
    return outcome.exit_code


if __name__=='__main__':
    raise SystemExit(main())
