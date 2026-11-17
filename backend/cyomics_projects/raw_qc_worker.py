"""One claimed raw-QC job, no service/CLI/daemon/factory or default execution.

Use only in a dedicated single-thread MainThread process outside ASGI. Only
repository/source-loader DB methods are awaited; registered R runs synchronously.
Recovery never claims, exports, executes, rebuilds artifacts or retries a CAS.
"""
from dataclasses import asdict,dataclass
import inspect
import os
import threading
from uuid import uuid4
from . import raw_qc_contract as c
from .raw_qc_artifacts import RawQCArtifacts,ArtifactFailure,checked_job
from .raw_qc_checkpoint import Journal,JournalFailure,WorkerBusy,WorkerRoots
from .raw_qc_input_export import InputExporter,InputIntegrityFailure
from .r_raw_qc.raw_qc_runner import JobRequest,RunnerFailure,limits_metadata

V3_SHA='fe1e05ac949ed027969d4ae4475e0e66bbc44cdc1d9310486d33aa334f40aad0'
UNRESOLVED=frozenset(('claim_intent','claimed','source_ready','executing','result_ready','commit_intent','reconciliation'))
INPUT_ERRORS=frozenset(('input_size_mismatch','input_digest_mismatch','input_changed','input_path_changed','input_rows_mismatch','invalid_input_metadata','input_header_mismatch'))
RESOURCE_ERRORS=frozenset(('wall_timeout','stdout_limit','stderr_limit','output_file_limit','output_total_limit','job_tasks_exceeded','uid_admission_tasks_exceeded','uid_nproc_inherited_hard_low'))
OUTPUT_ERRORS=frozenset(('output_protocol_invalid','missing_completion_marker','unexpected_or_missing_output','missing_outputs','invalid_output_file','invalid_output_directory'))
EXEC_ERRORS=frozenset(('server_file_pin_mismatch','server_file_changed','native_directory_pin_mismatch','unapproved_script','executable_not_executable'))


@dataclass(frozen=True,slots=True)
class OnceOutcome:
    status:str
    reason:str
    attempt_id:str|None=None
    job_id:str|None=None


@dataclass(frozen=True,slots=True)
class AckOutcome:
    status:str
    reason:str
    attempt_id:str|None=None
    job_id:str|None=None


def require(ok,reason='binding_mismatch'):
    if not ok:raise ValueError(reason)


def main_thread():
    require(os.name=='posix' and os.geteuid()>0
        and threading.current_thread() is threading.main_thread()
        and threading.active_count()==1,'dedicated_MainThread_required')


def failure_code(code):
    if code in INPUT_ERRORS:return 'input_integrity_failed'
    if code in RESOURCE_ERRORS:return 'resource_limit'
    if code in OUTPUT_ERRORS:return 'output_invalid'
    if code in EXEC_ERRORS:return 'execution_unavailable'
    return 'runner_failed'


class WorkerOnce:
    def __init__(self,repository=None,registration=None,executor=None,artifacts=None,
                 source_loader=None,exporter=None,roots=None,budget_factory=None):
        self.repository=repository;self.registration=registration;self.executor=executor
        self.artifacts=artifacts;self.source_loader=source_loader;self.exporter=exporter
        self.roots=roots;self.budget_factory=budget_factory

    def _present(self):
        return all(x is not None for x in (self.repository,self.registration,self.executor,
            self.artifacts,self.source_loader,self.exporter,self.roots,self.budget_factory))

    def _ready(self):
        self.roots.verify()
        # A compatible v3 contract + fixed verified factory are prerequisites,
        # not supplied/approved by this source-only coordinator.
        execution=self.registration.snapshot();c.execution_value(execution)
        require(execution['limits_profile']=='raw_qc_limits_v3' and execution['limits_sha256']==V3_SHA,'v3_contract_required')
        config=self.executor.config
        require((config.input_root,config.staging_root,config.publish_root)==
            (self.roots.inputs,self.roots.staging,self.roots.published),'executor_root_mismatch')
        require(asdict(config.script_pin)==execution['script'] and asdict(config.executable_pin)==execution['executable']
            and config.entry_kind==execution['entry_kind']=='native_R'
            and config.expected_r_version==execution['r_version']=='R version 4.6.1','executor_registration_mismatch')
        metadata=limits_metadata(config.limits)
        require(metadata['limits_profile']==execution['limits_profile'] and metadata['limits_sha256']==execution['limits_sha256'],'executor_limits_mismatch')
        require(type(self.executor._quarantined) is bool and self.executor._quarantined is False,'executor_quarantined')
        require(type(self.artifacts) is RawQCArtifacts and self.artifacts.root==self.roots.published
            and self.artifacts.registration.snapshot()==execution,'artifact_registration_mismatch')
        require(type(self.exporter) is InputExporter and self.exporter.root==self.roots.inputs,'exporter_root_mismatch')
        require(callable(self.source_loader.load_bound_source) and callable(self.budget_factory),'source_export_unconfigured')
        return execution

    def _out(self,status,reason,state=None):
        state=state or {}
        return OnceOutcome(status,reason,state.get('attempt_id'),state.get('job_id'))

    @staticmethod
    def _terminal(current,state):
        before=state['job'];intent=state['transition'];target=intent['target']
        c.exact(current,c.PUBLIC_KEYS)
        mutable={'status','revision','finished_at','observation','error','result'}
        require(all(c.canonical(current[k])==c.canonical(before[k]) for k in c.PUBLIC_KEYS-mutable))
        require(current['status']==target and type(current['revision']) is int
            and current['revision']==before['revision']+1 and current['observation']=='confirmed')
        require(c.parse_stamp(current['finished_at'])>=c.parse_stamp(before['started_at']))
        if target=='succeeded':
            require(current['error'] is None and current['result']==intent['result'])
            c.result_value(current['result'],before)
        elif target=='failed':
            require(current['result'] is None and current['error']=={'code':intent['error_code']}
                and intent['error_code'] in c.ERROR_CODES)
        else:raise ValueError()

    def _verified_terminal(self,journal,state,current,attempt):
        self._terminal(current,state)
        private=journal.read_private(attempt,state['private_record'])
        require(private['attempt_id']==attempt and private['job']==state['job']
            and private['transition']==state['transition'],'private_intent_mismatch')
        return private

    async def acknowledge_confirmed(self,*,expected_attempt_id,expected_job_id):
        """Explicit server action only. Unknown stops controller; never retry/next.

        This acknowledges history, not Owner authority or executor availability.
        No private receipt/job/output, closed-state or quarantine reset occurs.
        """
        if not self._present():return AckOutcome('off','worker_unconfigured')
        try:main_thread()
        except Exception:return AckOutcome('off','dedicated_MainThread_required')
        try:
            c.identifier(expected_attempt_id);c.identifier(expected_job_id)
        except Exception:return AckOutcome('refused','invalid_expected_ids')
        def outcome(status,reason):
            return AckOutcome(status,reason,expected_attempt_id,expected_job_id)
        if type(self.roots) is not WorkerRoots:return outcome('off','invalid_server_roots')
        journal=Journal(self.roots);idle_write_started=False
        try:
            with journal.locked(require_existing=True):
                state=journal.load()
                required={'phase','attempt_id','job_id','job','private_record','transition','confirmed_job'}
                require(type(state) is dict and required<=set(state)<=required|{'input'})
                require(state['phase']=='confirmed','confirmed_checkpoint_required')
                require(state['attempt_id']==expected_attempt_id and state['job_id']==expected_job_id
                    and state['job']['job_id']==expected_job_id,'expected_ids_mismatch')
                checked_job(state['job'],self.registration)
                self._terminal(state['confirmed_job'],state)
                sequence=journal.sequence
                current=await self.repository.read_attempt(expected_job_id,expected_attempt_id,self.budget_factory())
                self.roots.verify();journal._locked_path()
                private=self._verified_terminal(journal,state,current,expected_attempt_id)
                c.exact(private,('schemaVersion','attempt_id','job','runner_receipt','transition'))
                require(type(private['schemaVersion']) is int and private['schemaVersion']==1)
                require(c.canonical(private['job'])==c.canonical(state['job'])
                    and c.canonical(private['transition'])==c.canonical(state['transition']))
                require(c.canonical(current)==c.canonical(state['confirmed_job']),'confirmed_history_changed')
                self.roots.verify();journal._locked_path()
                idle_write_started=True
                journal.save({'phase':'idle'})
                require(journal.load()=={'phase':'idle'} and journal.sequence==sequence+1)
                self.roots.verify();journal._locked_path()
            # Context teardown is part of the success boundary.
            return outcome('acknowledged','confirmed_checkpoint_reset_idle')
        except WorkerBusy:
            return outcome('unknown' if idle_write_started else 'busy',
                'acknowledgement_unconfirmed' if idle_write_started else 'worker_lock_held')
        except BaseException:
            return outcome('unknown' if idle_write_started else 'refused',
                'acknowledgement_unconfirmed' if idle_write_started else 'confirmed_ack_refused')

    async def _recover(self,journal,state):
        # The only recovery network operations are these existing read-only RPCs.
        try:
            attempt=state['attempt_id'];c.identifier(attempt)
            if state.get('job_id') is None:
                current=await self.repository.recover_claim(attempt,self.budget_factory())
                return self._out('reconciliation','claim_outcome_requires_review',
                    {**state,'job_id':None if current is None else current.get('job_id')})
            c.identifier(state['job_id'])
            current=await self.repository.read_attempt(state['job_id'],attempt,self.budget_factory())
            if state['phase'] not in ('commit_intent','confirmed'):
                return self._out('reconciliation','unresolved_attempt_never_resumes_R',state)
            self._verified_terminal(journal,state,current,attempt)
            journal.save({**state,'phase':'confirmed','confirmed_job':current})
            return self._out('confirmed','terminal_readback_confirmed',state)
        except BaseException:
            return self._out('reconciliation','read_only_recovery_unresolved',state)

    async def _commit(self,journal,state,*,result=None,error_code=None,receipt=None):
        target='succeeded' if result is not None else 'failed'
        require((result is not None and error_code is None)
            or (result is None and error_code in c.ERROR_CODES),'invalid_terminal_intent')
        intent={'target':target,'result':result,'error_code':error_code}
        private=journal.private_record(state['attempt_id'],{'schemaVersion':1,
            'attempt_id':state['attempt_id'],'job':state['job'],
            'runner_receipt':receipt,'transition':intent})
        state={**state,'phase':'result_ready','private_record':private,'transition':intent}
        journal.save(state)
        state={**state,'phase':'commit_intent'};journal.save(state)
        # Durable target/result exists before any CAS dispatch. Any failure from
        # dispatch through COMMIT readback remains this exact intent, never R again.
        try:
            current=await self.repository.transition(state['job_id'],state['attempt_id'],
                state['job']['revision'],target,self.budget_factory(),
                result_bytes=None if result is None else c.canonical(result).encode('utf-8'),
                error_code=error_code)
            self._terminal(current,state)
        except BaseException:return await self._recover(journal,state)
        journal.save({**state,'phase':'confirmed','confirmed_job':current})
        return self._out('confirmed',target,state)

    async def run_once(self):
        if not self._present():return self._out('off','worker_unconfigured')
        try:main_thread()
        except Exception:return self._out('off','dedicated_MainThread_required')
        if type(self.roots) is not WorkerRoots:return self._out('off','invalid_server_roots')
        journal=Journal(self.roots);state={};handle=None
        try:
            with journal.locked():
                state=journal.load()
                if state['phase'] in UNRESOLVED or state['phase']=='confirmed':
                    return await self._recover(journal,state)
                try:execution=self._ready()
                except Exception:return self._out('off','execution_configuration_not_ready')
                # No claimed job or R side effect can precede this fsync barrier.
                state={'phase':'claim_intent','attempt_id':str(uuid4()),'job_id':None}
                journal.save(state)
                try:claim=await self.repository.claim(state['attempt_id'],self.budget_factory())
                except BaseException:return await self._recover(journal,state)
                if claim is None:
                    journal.save({'phase':'idle'})
                    return self._out('idle','confirmed_no_job')
                try:
                    c.exact(claim,('job','attempt_id'))
                    require(claim['attempt_id']==state['attempt_id'])
                    job=checked_job(claim['job'],self.registration)
                    require(job['execution']==execution)
                except Exception:return self._out('reconciliation','invalid_claim_receipt',state)
                state={**state,'phase':'claimed','job_id':job['job_id'],'job':job}
                journal.save(state)
                current=await self.repository.read_attempt(job['job_id'],state['attempt_id'],self.budget_factory())
                require(c.canonical(current)==c.canonical(job),'attempt_reverse_read_mismatch')
                # This server-only seam has no Owner/client-path/config parameter.
                bound=await self.source_loader.load_bound_source(job['job_id'],state['attempt_id'],self.budget_factory())
                try:exported=self.exporter.prepare(job,state['attempt_id'],bound,self.registration)
                except InputIntegrityFailure:
                    return await self._commit(journal,state,error_code='input_integrity_failed')
                state={**state,'phase':'source_ready','input':{'server_key':exported.server_key,
                    'bytes':exported.version.bytes,'sha256':exported.version.sha256,
                    'selected_columns_sha256':exported.selected_columns_sha256}}
                journal.save(state)
                current=await self.repository.read_attempt(job['job_id'],state['attempt_id'],self.budget_factory())
                require(c.canonical(current)==c.canonical(job),'attempt_changed_before_execution')
                require(self._ready()==execution,'execution_changed_before_execution')
                main_thread();journal._locked_path()
                handle=self.executor.open_authorized_input(exported.server_key,exported.version)
                state={**state,'phase':'executing'};journal.save(state)
                try:
                    # Deliberately blocking MainThread; no async/thread-pool seam.
                    run=self.executor.run(JobRequest(job['job_id'],job['quant_family'],handle))
                    if inspect.isawaitable(run):
                        if inspect.iscoroutine(run):run.close()
                        raise ValueError('executor_must_be_synchronous')
                except RunnerFailure as error:
                    if getattr(self.executor,'_quarantined',None) is not False:
                        return self._out('reconciliation','executor_cleanup_unconfirmed',state)
                    handle.close();handle=None
                    return await self._commit(journal,state,error_code=failure_code(error.code))
                # Any other interruption leaves the durable executing marker.
                require(getattr(self.executor,'_quarantined',None) is False,'executor_cleanup_unconfirmed')
                handle.close();handle=None
                require(run.published_dir==self.roots.published/job['job_id'],'unexpected_publication_path')
                try:result=self.artifacts.build_result(job,run.receipt)
                except ArtifactFailure:
                    return await self._commit(journal,state,error_code='publish_integrity_failed',receipt=run.receipt)
                return await self._commit(journal,state,result=result,receipt=run.receipt)
        except WorkerBusy:return self._out('busy','worker_lock_held')
        except JournalFailure:return self._out('reconciliation','durable_checkpoint_or_export_unconfirmed',state)
        except BaseException:return self._out('reconciliation','attempt_requires_reconciliation',state)
        finally:
            if handle is not None:
                try:handle.close()
                except Exception:pass # Failed attempt remains unresolved; never CAS here.
