"""Trusted preprocessing once/controller and atomic DB publication; default off.

One official R1/R2 native runner, unchanged process policy, no HTTP or generic
command framework. Only the separately approved bootstrap may create a runtime.
"""
import asyncio
from dataclasses import dataclass,field
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import stat
import time
from uuid import uuid4
from . import preprocess_contract as c
from . import preprocess_repository as p
from .settings import Budget,Fault


def require(ok,code='preprocessing_persisted_invalid'):
    if not ok:raise Fault(503,code)


def stamp_now():return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def proposal_bytes(runner_pin):
    """Unregistered immutable admission metadata, never a runtime permission."""
    value={'contract':'log2_local_source_proposal_v0.1','entry_kind':'native_R','r_version':'R version 4.6.1',
        **{k:dict(v) for k,v in p.FIXED_PINS.items()},'runner':dict(runner_pin),'limits':dict(p.LIMITS),
        'process_policy_sha256':p.POLICY_SHA256,'registered':False}
    raw=p.canonical(value).encode();p.execution_value(raw.decode());return raw


def profile_bytes(proposal,source_manifest_sha256,environment_sha256):
    require(type(proposal) is bytes and c.hashed(source_manifest_sha256) and c.hashed(environment_sha256))
    value=p.execution_value(proposal.decode('utf8'))
    profile={'contract':'preprocessing_runtime_profile_v1','proposal_sha256':c.bytes_sha(proposal),
        'source_manifest_sha256':source_manifest_sha256,'environment_sha256':environment_sha256,
        'r_version':value['r_version'],'script':value['script'],'validator':value['validator'],
        'runner':value['runner'],'native':value['executable'],'limits':value['limits'],
        'validation_limits':{'per_file':c.MAX_ARTIFACT,'total':c.MAX_ARTIFACT_TOTAL},
        'process_policy_sha256':value['process_policy_sha256']}
    return c.canonical(profile)


def validate_receipt(capsule,bundle,receipt,*,config=None):
    from .log2_matrix_runner import configuration_binding
    if capsule.task()['parameters']['normalization']=='median':
        from .median_processed_runner import configuration_binding
    if capsule.task()['parameters']['normalization']=='total':
        from .total_processed_runner import configuration_binding
    task=capsule.task();record=capsule.record();proposal=p.execution_value(record['admission']['execution_json'])
    require(type(receipt) is dict and receipt.get('job_id')==task['job_id'] and receipt.get('attempt_id')==task['attempt_id'])
    require(receipt.get('family')==task['quant_family'] and receipt.get('flags')==[task['parameters']['filter_flags'][k] for k in ('reverse','contaminant','site_only')])
    require(receipt.get('source')=={'bytes':task['source']['source_bytes'],'sha256':task['source']['source_sha256']})
    require(bundle.source_sha256==task['source']['source_sha256'] and bundle.parameters_sha256==task['parameters_sha256'])
    require(receipt.get('selected',{}).get('sha256')==bundle.selected_sha256 and type(receipt.get('selected',{}).get('bytes')) is int)
    require(receipt.get('parameters')=={'bytes':len(c.canonical(task['parameters'])),'sha256':task['parameters_sha256']})
    if task['parameters']['normalization']=='median':
        from .median_processed_profile import CORE_PIN
        require(receipt.get('median_parent')==task['parameters']['normalization_parent'] and receipt.get('median_core')==CORE_PIN)
    if task['parameters']['normalization']=='total':
        from .total_processed_profile import CORE_PIN
        require(receipt.get('total_parent')==task['parameters']['normalization_parent'] and receipt.get('total_core')==CORE_PIN)
    require(receipt.get('script')==proposal['script'] and receipt.get('validator')==proposal['validator'] and receipt.get('native')==proposal['executable'])
    require(receipt.get('process_policy_sha256')==p.POLICY_SHA256 and receipt.get('limits')==p.LIMITS)
    require(type(receipt.get('exit_code')) is int and receipt['exit_code']==0 and receipt.get('stderr_bytes')==0
        and receipt.get('one_native_child') is True and receipt.get('all_11_outputs_verified') is True and receipt.get('close_confirmed') is True)
    require(receipt.get('manifest_sha256')==bundle.manifest_sha256 and c.hashed(receipt.get('configuration_sha256')))
    require(receipt.get('published') is False and receipt.get('Owner_or_API_or_DB_authorized') is False)
    evidence=receipt.get('process_control',{}).get('owned_tasks',{})
    require(evidence.get('cleanup',{}).get('complete_for_observed_identities') is True and evidence.get('uncertainty')==[])
    if config is not None:
        require(receipt['configuration_sha256']==configuration_binding(config))
        require(receipt['selected']=={'bytes':config.selected_pin.bytes,'sha256':config.selected_pin.sha256})
    return receipt


def validate_version_for_claim(capsule,metadata,bundle,receipt):
    task=capsule.task();record=capsule.record();c.version_value(metadata)
    expected_runtime={**task['runtime'],'receipt_sha256':c.bytes_sha(c.canonical(receipt))}
    require(metadata['job_id']==task['job_id'] and metadata['attempt_id']==task['attempt_id']
        and metadata['source']==task['source'] and metadata['created_by']==str(record['admission']['owner_id'])
        and metadata['quant_family']==task['quant_family'] and metadata['parameters']==task['parameters']
        and metadata['parameters_sha256']==task['parameters_sha256'] and metadata['proposal_sha256']==task['proposal_sha256']
        and metadata['runtime']==expected_runtime and metadata['manifest_sha256']==bundle.manifest_sha256)
    require(metadata['tool']==p.public_tool(p.execution_value(record['admission']['execution_json'])))
    require(c.stamp(metadata['created_at'])>=c.stamp(task['started_at']));return metadata


def make_version(capsule,bundle,receipt,*,clock=stamp_now):
    task=capsule.task();source=task['source'];proposal=p.execution_value(capsule.record()['admission']['execution_json'])
    value={'schemaVersion':2,'processed_dataset_version_id':str(uuid4()),'project_id':source['project_id'],
        'parent_dataset_version_id':source['dataset_version_id'],'file_id':source['file_id'],
        'created_by':str(capsule.record()['admission']['owner_id']),'created_at':clock(),'job_id':task['job_id'],
        'attempt_id':task['attempt_id'],'source':source,'quant_family':task['quant_family'],'steps':list(task['parameters']['steps']),
        'parameters':task['parameters'],'parameters_sha256':task['parameters_sha256'],'proposal_sha256':task['proposal_sha256'],
        'runtime':{**task['runtime'],'receipt_sha256':c.bytes_sha(c.canonical(receipt))},'tool':p.public_tool(proposal),
        'manifest_sha256':bundle.manifest_sha256,'artifacts':[{'name':a.name,'bytes':a.length,'sha256':a.sha256,'media_type':a.media_type} for a in bundle.artifacts],
        'analysis_ready':False}
    return validate_version_for_claim(capsule,value,bundle,receipt)


@dataclass(frozen=True,slots=True)
class PreparedInput:
    source:Path=field(repr=False)
    selected:Path=field(repr=False)
    source_bytes:int
    source_sha256:str
    selected_bytes:int
    selected_sha256:str
    parameters_bytes:int
    parameters_sha256:str


def _snapshot(root,name,data):
    from .raw_qc_artifacts import open_root
    from .raw_qc_checkpoint import write_all
    parent=fd=None;unknown=False
    try:
        parent=open_root(root)
        fd=os.open(name,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=parent)
        write_all(fd,data);os.fchmod(fd,0o400);os.fsync(fd)
        s=os.fstat(fd);require(stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_uid==1000 and stat.S_IMODE(s.st_mode)==0o400)
        require(s.st_size==len(data));offset=0
        while offset<len(data):
            part=os.pread(fd,min(65536,len(data)-offset),offset)
            require(bool(part) and part==data[offset:offset+len(part)]);offset+=len(part)
        require(os.pread(fd,1,offset)==b'');os.fsync(parent)
    finally:
        for value in (fd,parent):
            if value is not None:
                try:os.close(value)
                except BaseException:unknown=True
        if unknown:raise Fault(503,'preprocessing_execution_unavailable') from None
    return root/name


def prepare_input(bound,roots):
    from dataclasses import asdict
    from .import_report import SourceRef
    from .protein_groups_parser import parse_protein_groups
    require(type(bound) is p.BoundInput)
    task=bound.capsule.task();source=task['source'];data=bound.content
    require(type(data) is bytes and len(data)==source['source_bytes'] and c.bytes_sha(data)==source['source_sha256'])
    ref=SourceRef(source['project_id'],source['file_id'],'raw_bytea_v1:'+source['file_id']+':'+source['source_sha256'],len(data),source['source_sha256'])
    parsed=parse_protein_groups(ref,(data[i:i+65536] for i in range(0,len(data),65536)))
    require(parsed.ok)
    selected=[asdict(col) for col in parsed.parsed.columns if col.quant_family==task['quant_family']]
    require(selected and c.digest(selected)==source['selected_columns_sha256']
        and len(parsed.parsed.rows)==source['row_count'] and len(parsed.parsed.columns)==source['column_count'])
    lines=['column_ordinal\tname_raw\tquant_family\tsample_label_raw\n']
    for col in selected:
        fields=(str(col['ordinal']),col['name_raw'],col['quant_family'],col['sample_label_raw'])
        require(all(type(x) is str and not any(ch in x for ch in ('\t','\r','\n','\0')) for x in fields),'preprocessing_execution_unavailable')
        lines.append('\t'.join(fields)+'\n')
    descriptors=''.join(lines).encode('utf8');require(1<=len(descriptors)<=131072)
    params=c.canonical(task['parameters']);require(c.bytes_sha(params)==task['parameters_sha256'])
    attempt=task['attempt_id'];require(c._canonical_uuid(attempt))
    original=_snapshot(roots.inputs,attempt+'.source.tsv',data)
    selection=_snapshot(roots.inputs,attempt+'.selected.tsv',descriptors)
    return PreparedInput(original,selection,len(data),c.bytes_sha(data),len(descriptors),c.bytes_sha(descriptors),len(params),c.bytes_sha(params))


def runner_config(capsule,prepared,roots,native_directory_pins):
    from .log2_matrix_runner import Config,Pin
    task=capsule.task()
    if task['parameters']['normalization']=='median':
        from .median_processed_io import median_runner_config
        return median_runner_config(capsule,prepared,roots,native_directory_pins)
    if task['parameters']['normalization']=='total':
        from .total_processed_io import total_runner_config
        return total_runner_config(capsule,prepared,roots,native_directory_pins)
    return Config(enabled=True,job_id=task['job_id'],attempt_id=task['attempt_id'],source=prepared.source,
        source_pin=Pin(prepared.source_bytes,prepared.source_sha256),selected=prepared.selected,
        selected_pin=Pin(prepared.selected_bytes,prepared.selected_sha256),parameters_pin=Pin(prepared.parameters_bytes,prepared.parameters_sha256),
        script=Path(__file__).absolute().parent/'r_log2_matrix/log2_matrix.R',staging_root=roots.staging,
        family=task['quant_family'],flags=tuple(task['parameters']['filter_flags'][k] for k in ('reverse','contaminant','site_only')),
        native_directory_pins=native_directory_pins)


def failure_receipt(capsule,error,config):
    from .log2_matrix_runner import Log2RunFailure,FailureFacts,configuration_binding
    from .median_processed_runner import Config as MedianConfig
    if type(config) is MedianConfig:
        from .median_processed_runner import configuration_binding
    from .total_processed_runner import Config as TotalConfig
    if type(config) is TotalConfig:
        from .total_processed_runner import configuration_binding
    if type(error) is not Log2RunFailure or type(error.facts) is not FailureFacts:return None
    facts=error.facts.snapshot();task=capsule.task()
    keys=set(json.loads((error.facts._json).decode()))
    expected={'schemaVersion','job_id','attempt_id','configuration_sha256','popen_attempted','launch_state','process_identity','exit_code','child_reaped','observed_tasks_stopped','owned_fds_closed','timer_disabled','signal_handler_restored','process_evidence_sha256','cleanup_known'}
    if keys!=expected or facts.get('schemaVersion')!=1 or facts.get('job_id')!=task['job_id'] or facts.get('attempt_id')!=task['attempt_id'] or facts.get('configuration_sha256')!=configuration_binding(config):return None
    booleans=('popen_attempted','child_reaped','observed_tasks_stopped','owned_fds_closed','timer_disabled','signal_handler_restored','cleanup_known')
    if any(type(facts[k]) is not bool for k in booleans) or facts['cleanup_known'] is not True:return None
    if not all(facts[k] for k in booleans[1:]):return None
    if facts['launch_state']=='not_started':
        if facts['popen_attempted'] or facts['process_identity'] is not None or facts['exit_code'] is not None or facts['process_evidence_sha256'] is not None:return None
    elif facts['launch_state']=='started':
        identity=facts['process_identity']
        if not facts['popen_attempted'] or type(identity) is not dict or set(identity)!={'pid','starttick','proc_inode','uid'}:return None
        if any(type(identity[k]) is not int or identity[k]<=0 for k in identity) or identity['uid']!=1000:return None
        if type(facts['exit_code']) is not int or not -255<=facts['exit_code']<=255 or not c.hashed(facts['process_evidence_sha256']):return None
    else:return None
    return {'kind':'log2_failure_receipt_v1','job_id':task['job_id'],'attempt_id':task['attempt_id'],
        'runtime_binding_sha256':task['runtime']['runtime_binding_sha256'],'code':failure_code(error.code),'facts':facts}


def failure_code(code):
    if code=='empty_not_analyzable':return code
    if code in ('log2_file_pin_mismatch','selected_binding_mismatch','log2_short_or_changed_read'):return 'input_integrity_failed'
    if code in ('log2_timeout','log2_stdout_limit','log2_stderr_limit','log2_output_total_limit'):return 'resource_limit'
    if code in ('log2_output_invalid','log2_invalid_empty_diagnostic'):return 'output_invalid'
    if code in ('log2_runner_disabled','invalid_log2_config','unsafe_log2_ancestry'):return 'execution_unavailable'
    return 'runner_failed'


@dataclass(frozen=True,slots=True)
class Outcome:
    status:str
    reason:str
    job_id:str|None=None
    attempt_id:str|None=None
    task_json:bytes|None=field(default=None,repr=False)
    def task(self):return None if self.task_json is None else c.document(self.task_json)


class PreprocessWorker:
    """One synchronous native call, original Journal barriers, no unknown retries."""
    def __init__(self,repository=None,roots=None,*,verify=None,executor_factory=None,prepare=prepare_input,
                 registration_id=None,scope_sha256=None,native_directory_pins=(),budget_factory=Budget,clock=stamp_now,cancelled=None):
        self.repository=repository;self.roots=roots;self.verify=verify;self.executor_factory=executor_factory;self.prepare=prepare
        self.registration_id=registration_id;self.scope_sha256=scope_sha256;self.native_directory_pins=native_directory_pins
        self.budget_factory=budget_factory;self.clock=clock;self.quarantined=False
        self.cancelled=cancelled

    def _ready(self):
        require(not self.quarantined and self.repository is not None and self.roots is not None and callable(self.verify)
            and callable(self.executor_factory) and c._canonical_uuid(self.registration_id) and c.hashed(self.scope_sha256),'preprocessing_execution_unavailable')
        self.verify();self.roots.verify()

    def _out(self,status,reason,state=None,task=None):
        state=state or {};return Outcome(status,reason,state.get('job_id'),state.get('attempt_id'),None if task is None else c.canonical(task))

    def _unknown(self,journal,state,reason):
        self.quarantined=True
        try:journal.save({**state,'phase':'reconciliation','reason':reason})
        except BaseException:pass # No success/next claim follows even if marker close is unknown.
        return self._out('reconciliation_pending',reason,state)

    async def _commit(self,journal,state,capsule,*,version=None,bundle=None,receipt=None,failure=None):
        intent={'target':'succeeded' if version is not None else 'failed','version':version,'failure':failure}
        private=journal.private_record(state['attempt_id'],{'schemaVersion':2,'job':capsule.task(),'receipt':receipt,'intent':intent})
        state={**state,'phase':'result_ready','private_record':private,'intent':intent};journal.save(state)
        state={**state,'phase':'commit_intent'};journal.save(state)
        # From dispatch through teardown, any uncertainty preserves this exact intent.
        try:
            self._ready()
            if version is not None:current=await self.repository.publish(capsule,version,bundle,receipt,self.budget_factory())
            else:current=await self.repository.finish(capsule,'failed',failure['code'],self.budget_factory(),failure_receipt=failure)
            task=current.task();require(task['job_id']==state['job_id'] and task['attempt_id']==state['attempt_id'] and task['status']==intent['target'])
            if version is not None:c.version_value(version,task=task)
            else:require(task['runtime']['receipt_sha256']==c.bytes_sha(c.canonical(failure)) and task['error']=={'code':failure['code']})
            journal.save({**state,'phase':'confirmed','confirmed_job':task})
            return self._out('confirmed',task['status'],state,task)
        except BaseException:
            # Never mark a committed successful version running/failed to match
            # private unknown. Independent Owner reads can prove actual success.
            return self._unknown(journal,state,'publication_disposition_unknown')

    async def run_once(self,*,expected_job_id=None):
        if self.repository is None or self.roots is None:return self._out('off','worker_unconfigured')
        from .raw_qc_checkpoint import Journal,WorkerBusy
        from .log2_matrix_runner import Log2RunFailure
        if self.quarantined:return self._out('reconciliation_pending','worker_quarantined')
        state={};journal=Journal(self.roots)
        try:
            self._ready()
            with journal.locked():
                state=journal.load()
                if state['phase']!='idle':return self._out('reconciliation_pending','existing_attempt_read_only_recovery_required',state)
                attempt=str(uuid4());state={'phase':'claim_intent','attempt_id':attempt,'job_id':expected_job_id};journal.save(state)
                try:capsule=await self.repository.claim(self.registration_id,self.scope_sha256,attempt,self.budget_factory(),job_id=expected_job_id)
                except BaseException:return self._unknown(journal,state,'claim_disposition_unknown')
                if capsule is None:
                    journal.save({'phase':'idle'});return self._out('idle','no_eligible_job')
                task=capsule.task();self._claim_matches(capsule,attempt,expected_job_id)
                state={**state,'phase':'claimed','job_id':task['job_id'],'job':task};journal.save(state)
                bound=await self.repository.load_bound_source(task['job_id'],attempt,self.budget_factory())
                require(bound.capsule.task()==task)
                if task['parameters']['normalization']=='median':
                    from .median_processed_io import prepare_median_input
                    parent=await self.repository.load_median_parent(capsule,self.budget_factory())
                    prepared=prepare_median_input(bound,self.roots,parent,check=self.budget_factory().check)
                elif task['parameters']['normalization']=='total':
                    from .total_processed_io import prepare_total_input
                    parent=await self.repository.load_total_parent(capsule,self.budget_factory())
                    prepared=prepare_total_input(bound,self.roots,parent,check=self.budget_factory().check)
                else:prepared=self.prepare(bound,self.roots)
                state={**state,'phase':'source_ready','input':{'source_sha256':prepared.source_sha256,'selected_sha256':prepared.selected_sha256,'parameters_sha256':prepared.parameters_sha256}};journal.save(state)
                current=await self.repository.read_attempt(task['job_id'],attempt,self.budget_factory());require(current is not None and current.task()==task)
                self._ready();config=runner_config(capsule,prepared,self.roots,self.native_directory_pins)
                executor=self.executor_factory(config)
                state={**state,'phase':'executing'};journal.save(state)
                try:result=executor.run() if self.cancelled is None else executor.run(cancelled=self.cancelled)
                except Log2RunFailure as error:
                    proof=failure_receipt(capsule,error,config)
                    if proof is not None:return await self._commit(journal,state,capsule,failure=proof)
                    # One diagnostic CAS is permitted only before any publication
                    # intent. Failure to persist it remains private unknown.
                    try:await self.repository.finish(capsule,'reconciliation_pending',None,self.budget_factory())
                    except BaseException:pass
                    return self._unknown(journal,state,'runner_cleanup_unknown')
                require(executor.quarantined is False)
                receipt=result.receipt;validate_receipt(capsule,result.bundle,receipt,config=config)
                metadata=make_version(capsule,result.bundle,receipt,clock=self.clock)
                return await self._commit(journal,state,capsule,version=metadata,bundle=result.bundle,receipt=receipt)
        except WorkerBusy:return self._out('busy','worker_lock_held',state)
        except BaseException:
            self.quarantined=True
            return self._out('reconciliation_pending','worker_source_or_checkpoint_unknown',state)

    def _claim_matches(self,capsule,attempt,expected):
        require(type(capsule) is p.ClaimedJob)
        task=capsule.task();record=capsule.record();reg=record['registration']
        require(task['attempt_id']==attempt and task['status']=='running' and task['observation']=='confirmed'
            and task['runtime']['registration_id']==self.registration_id and reg['startup_scope_sha256']==self.scope_sha256)
        require(expected is None or task['job_id']==expected)
        require(record['admission']['execution_json'] and p.digest(record['admission']['execution_json'])==task['proposal_sha256'])
        self.verify(registration=reg,task=task)

    async def acknowledge_confirmed(self,expected_job_id,expected_attempt_id):
        if self.quarantined:return self._out('reconciliation_pending','worker_quarantined')
        from .raw_qc_checkpoint import Journal
        state={};idle_write=False
        try:
            self._ready()
            with Journal(self.roots).locked(require_existing=True) as journal:
                state=journal.load();require(state['phase']=='confirmed' and state['job_id']==expected_job_id and state['attempt_id']==expected_attempt_id)
                private=journal.read_private(expected_attempt_id,state['private_record'])
                require(private['job']==state['job'] and private['intent']==state['intent'])
                current=await self.repository.read_attempt(expected_job_id,expected_attempt_id,self.budget_factory())
                require(current is not None and current.task()==state['confirmed_job'])
                task=current.task();require(task['status'] in ('succeeded','failed') and task['observation']=='confirmed')
                if task['status']=='succeeded':c.version_value(private['intent']['version'],task=task)
                else:require(task['runtime']['receipt_sha256']==c.bytes_sha(c.canonical(private['intent']['failure'])))
                self._ready();idle_write=True;journal.save({'phase':'idle'});require(journal.load()=={'phase':'idle'})
            return self._out('acknowledged','confirmed_exact_history_and_close',state,task)
        except BaseException:
            self.quarantined=True
            return self._out('reconciliation_pending','acknowledgement_unknown' if idle_write else 'acknowledgement_refused',state)

    async def recover_read_only(self,attempt_id,job_id=None):
        # Explicit recovery entry, never calls R/claim/CAS/ACK or resets a latch.
        try:
            current=await self.repository.read_attempt(job_id,attempt_id,self.budget_factory())
            if current is None:return Outcome('reconciliation_pending','attempt_not_found',job_id,attempt_id)
            task=current.task();return Outcome('observed','database_history_only',task['job_id'],attempt_id,c.canonical(task))
        except BaseException:return Outcome('reconciliation_pending','read_only_recovery_unconfirmed',job_id,attempt_id)


class PreprocessController:
    """Bounded approved continuation; exact ACK after every confirmed terminal.

    Different jobs continue without user latch resets. Any unknown stops the
    controller permanently. This never resets prior RawQC or unknown checkpoints.
    """
    def __init__(self,worker=None,*,max_jobs=0,allow_confirmed_ack=False):
        require(type(max_jobs) is int and 0<=max_jobs<=256 and type(allow_confirmed_ack) is bool)
        self.worker=worker;self.max_jobs=max_jobs;self.allow_ack=allow_confirmed_ack;self.quarantined=False;self.completed=0
    async def drain(self,*,expected_job_id=None):
        if self.worker is None or not self.max_jobs:return []
        require(not self.quarantined)
        outcomes=[]
        for _ in range(self.max_jobs-self.completed):
            out=await self.worker.run_once(expected_job_id=expected_job_id);outcomes.append(out)
            if out.status=='idle':break
            if out.status!='confirmed':self.quarantined=True;break
            self.completed+=1
            if not self.allow_ack:break
            ack=await self.worker.acknowledge_confirmed(out.job_id,out.attempt_id)
            if ack.status!='acknowledged':self.quarantined=True;outcomes.append(ack);break
            if expected_job_id is not None:break
        return outcomes

    async def serve(self,*,continue_approved=None,poll_seconds=1.0):
        # Dedicated Root scope only; no listener or unbounded polling lifetime.
        require(callable(continue_approved) and 0.05<=poll_seconds<=5)
        history=[]
        while self.completed<self.max_jobs and not self.quarantined:
            if continue_approved() is not True:break
            batch=await self.drain();history.extend(batch)
            if not self.allow_ack or self.quarantined:break
            if batch and batch[-1].status=='idle':await asyncio.sleep(poll_seconds)
        return history


def create_runtime(approval=None):
    if approval is None:return None
    # The only actual construction path requires the separate new Root bootstrap;
    # imports and every resource/credential action stay behind its verified gate.
    from .preprocess_dev_probe import ApprovedRuntime
    require(type(approval) is ApprovedRuntime,'preprocessing_execution_unavailable')
    approval.check()
    from .repository import FixedPool
    from .raw_qc_worker_pool import WorkerPool,Config as PoolConfig
    from .log2_matrix_runner import Log2MatrixRunner
    app_pool=FixedPool();worker_pool=WorkerPool(PoolConfig(1000))
    from .total_processed_profile import PROPOSAL as TOTAL_PROPOSAL
    from .median_processed_profile import PROPOSAL
    if p.execution_value(approval.proposal.decode())['contract']==PROPOSAL:
        legacy=(Path(__file__).absolute().parent/'log2_matrix_runner.py').read_bytes()
        owner=p.PreprocessRepository(app_pool,proposal_bytes({'bytes':len(legacy),'sha256':c.bytes_sha(legacy)}),
            enabled=True,median_execution_json=approval.proposal)
    elif p.execution_value(approval.proposal.decode())['contract']==TOTAL_PROPOSAL:
        legacy=(Path(__file__).absolute().parent/'log2_matrix_runner.py').read_bytes()
        owner=p.PreprocessRepository(app_pool,proposal_bytes({'bytes':len(legacy),'sha256':c.bytes_sha(legacy)}),
            enabled=True,total_execution_json=approval.proposal)
    else:owner=p.PreprocessRepository(app_pool,approval.proposal,enabled=True)
    repo=p.PreprocessWorkerRepository(worker_pool,enabled=True)
    worker=PreprocessWorker(repo,approval.roots,verify=approval.check,executor_factory=runner_factory,
        registration_id=approval.registration_id,scope_sha256=approval.scope_sha256,native_directory_pins=approval.native_directory_pins,cancelled=approval.expired)
    controller=PreprocessController(worker,max_jobs=approval.max_jobs,allow_confirmed_ack=approval.allow_confirmed_ack)
    return ExecutionRuntime(owner,worker,controller,app_pool,worker_pool)


@dataclass(slots=True)
class ExecutionRuntime:
    owner:p.PreprocessRepository
    worker:PreprocessWorker
    controller:PreprocessController
    app_pool:object=field(repr=False)
    worker_pool:object=field(repr=False)
    closed:bool=False
    cleanup_unknown:bool=False
    def close(self):
        if self.closed:return not self.cleanup_unknown
        self.closed=True
        for pool in (self.worker_pool,self.app_pool):
            try:pool.close()
            except BaseException:self.cleanup_unknown=True
        try:
            if self.worker_pool._lease is not None or self.worker_pool._held or self.worker_pool._uncertain:self.cleanup_unknown=True
            if self.app_pool._active or any(connection.closed is not True for connection in self.app_pool._idle):self.cleanup_unknown=True
        except BaseException:self.cleanup_unknown=True
        return not self.cleanup_unknown


def runner_factory(config):
    from .total_processed_runner import Config as TotalConfig,TotalProcessedRunner
    if type(config) is TotalConfig:return TotalProcessedRunner(config)
    from .median_processed_runner import Config,MedianProcessedRunner
    if type(config) is Config:return MedianProcessedRunner(config)
    from .log2_matrix_runner import Log2MatrixRunner
    return Log2MatrixRunner(config)
