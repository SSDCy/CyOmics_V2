"""New Root-controlled, no-service SDK/worker bootstrap template; public off.

Not api_entry.activate, not an HTTP/environment switch or credential copier.
Root must review/provide the new verifier and process-recorder control kind.
Offline tests never arm this entry or set the original production flags.
"""
from dataclasses import dataclass,field
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import sys
import threading
import time
from . import preprocess_contract as c
from . import settings
from .settings import Fault

PROJECT=Path('/home/user01/Cy_Omics_v2.0')
APPROVAL_PATH=PROJECT/'runtime/preprocess-execution-001/Root-approval.json'
_ORIGINAL_GATE=settings.production_gate
_ORIGINAL_LOADER=settings.load_runtime_password
_SEAL=object()
REQUIRED_SOURCES=frozenset(('preprocess_contract.py','preprocess_repository.py','preprocess_execution.py',
    'preprocess_dev_probe.py','log2_matrix_runner.py','log2_matrix_validator.py','settings.py','repository.py',
    'raw_qc_repository.py','raw_qc_checkpoint.py','raw_qc_artifacts.py','raw_qc_worker_pool.py',
    'protein_groups_parser.py','import_report.py','import_persistence.py','r_log2_matrix/log2_matrix.R',
    'r_raw_qc/__init__.py','r_raw_qc/raw_qc_runner.py','r_raw_qc/output_contract.py','r_raw_qc/process_policy.py'))
SCOPE_KEYS=frozenset(('schemaVersion','kind','epoch_id','registration_id','issued_at_ms','expires_at_ms','max_jobs',
    'allow_confirmed_ack','project_id','qa_refs','proposal_json','profile_json','source_manifest','expected_namespaces',
    'native_directory_pins','db_target','auth_policy_evidence_sha256','row_budget'))


def require(ok):
    if not ok:raise Fault(503,'preprocessing_execution_unavailable')


@dataclass(frozen=True,slots=True)
class ProbeConfig:
    """Trusted Root operator argument, never hydrated from wire/env/client JSON."""
    approval_bytes:int
    approval_sha256:str


def _read_fixed(path,expected_bytes,expected_sha):
    owned=[];unknown=False
    def own(fd):owned.append(fd);return fd
    try:
        parent=own(os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC))
        for name in path.parts[1:-1]:
            parent=own(os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent))
            s=os.fstat(parent);require(stat.S_ISDIR(s.st_mode) and s.st_uid in (0,1000) and not s.st_mode&0o022)
        private_parent=os.fstat(parent);require(private_parent.st_uid==1000 and stat.S_IMODE(private_parent.st_mode)==0o700)
        fd=own(os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,dir_fd=parent));before=os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid==1000 and before.st_nlink==1
            and stat.S_IMODE(before.st_mode)==0o600 and before.st_size==expected_bytes and 1<=expected_bytes<=32768)
        raw=bytearray()
        while len(raw)<expected_bytes:
            part=os.pread(fd,min(65536,expected_bytes-len(raw)),len(raw));require(bool(part));raw.extend(part)
        require(not os.pread(fd,1,len(raw)) and hashlib.sha256(raw).hexdigest()==expected_sha)
        def identity(s):return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        require(identity(before)==identity(os.fstat(fd))==identity(os.stat(path.name,dir_fd=parent,follow_symlinks=False)))
        return bytes(raw)
    finally:
        while owned:
            fd=owned.pop()
            try:os.close(fd)
            except BaseException:unknown=True
        if unknown:raise Fault(503,'preprocessing_execution_unavailable') from None


def validate_scope(raw):
    """Pure shape/pin policy only, NOT Root approval, A identity or an armed gate."""
    value=c.document(raw,32768);c.exact(value,SCOPE_KEYS)
    require(type(value['schemaVersion']) is int and value['schemaVersion']==1)
    require(value['kind'] in ('preprocess_dev_sdk','preprocess_worker'))
    require(all(c._canonical_uuid(value[k]) for k in ('epoch_id','registration_id','project_id')))
    for k in ('issued_at_ms','expires_at_ms','max_jobs'):require(type(value[k]) is int)
    require(0<value['expires_at_ms']-value['issued_at_ms']<=900000 and 1<=value['max_jobs']<=256)
    require(type(value['allow_confirmed_ack']) is bool)
    if value['kind']=='preprocess_dev_sdk':require(value['max_jobs']==1)
    refs=value['qa_refs'];c.exact(refs,('owner_id','workspace_id','project_id','file_id','dataset_version_id','request_id'))
    require(all(c._canonical_uuid(v) for v in refs.values()) and refs['project_id']==value['project_id'])
    c.exact(value['expected_namespaces'],('pid','net','mnt'))
    for k,v in value['expected_namespaces'].items():require(type(v) is str and __import__('re').fullmatch(k+r':\[[0-9]+\]',v) is not None)
    require(value['db_target']=={'host':'172.19.0.2','port':5432,'database':'cyomicsv2_business_dev','app_role':'cyomicsv2_app_runtime','worker_role':'cyomicsv2_raw_qc_worker_runtime'})
    require(c.hashed(value['auth_policy_evidence_sha256']))
    if value['kind']=='preprocess_dev_sdk':
        require(value['row_budget']=={'maximum':10,'registration':1,'workspace':1,'project':1,'raw_upload':1,'raw_dataset':1,'preprocessing_job':1,'job_run':1,'processed_version':1,'artifact_bundle':1,'default_delete':False,'reuse_real_owner':False})
    else:
        # Worker consumes independently admitted jobs; parent/registration/admission
        # creation belongs to separate explicit Root scopes and row budgets.
        n=value['max_jobs']
        require(value['allow_confirmed_ack'] is True and value['row_budget']=={'maximum':3*n,'registration':0,'workspace':0,'project':0,'raw_upload':0,'raw_dataset':0,'preprocessing_job':0,'job_run':n,'processed_version':n,'artifact_bundle':n,'default_delete':False,'reuse_real_owner':False})
    manifest=value['source_manifest'];require(type(manifest) is dict and REQUIRED_SOURCES<=set(manifest) and len(manifest)<=64)
    for name,pin in manifest.items():
        require(type(name) is str and Path(name).as_posix()==name and not name.startswith('/') and '..' not in Path(name).parts and Path(name).suffix in ('.py','.R'))
        c.exact(pin,('bytes','sha256'));require(type(pin['bytes']) is int and 1<=pin['bytes']<=262144 and c.hashed(pin['sha256']))
    from . import preprocess_repository as p
    require(type(value['proposal_json']) is str and type(value['profile_json']) is str)
    proposal=p.execution_value(value['proposal_json']);profile=p.document(value['profile_json'],32768)
    expected={'contract':'preprocessing_runtime_profile_v1','proposal_sha256':p.digest(value['proposal_json']),
        'source_manifest_sha256':c.digest(manifest),'environment_sha256':profile.get('environment_sha256'),
        'r_version':proposal['r_version'],'script':proposal['script'],'validator':proposal['validator'],'runner':proposal['runner'],
        'native':proposal['executable'],'limits':proposal['limits'],'validation_limits':{'per_file':c.MAX_ARTIFACT,'total':c.MAX_ARTIFACT_TOTAL},
        'process_policy_sha256':proposal['process_policy_sha256']}
    require(profile==expected and c.hashed(profile['environment_sha256']))
    if proposal['contract']=='median_saved_parent_source_proposal_v1':
        from .median_processed_profile import CORE_PIN
        extra={'median_processed_profile.py','median_processed_io.py','median_processed_runner.py',
            'median_processed_validator.py','r_median_processed/median_processed.R','r_normalization_median/median_core.R'}
        require(extra<=set(manifest))
        require(manifest['median_processed_runner.py']==proposal['runner']
            and manifest['median_processed_validator.py']==proposal['validator']
            and manifest['r_median_processed/median_processed.R']==proposal['script']
            and manifest['r_normalization_median/median_core.R']==CORE_PIN)
    else:
        require(manifest['log2_matrix_runner.py']==proposal['runner'] and manifest['log2_matrix_validator.py']==proposal['validator']
            and manifest['r_log2_matrix/log2_matrix.R']==proposal['script'])
    require(type(value['native_directory_pins']) is list and len(value['native_directory_pins'])==5)
    return value


def _physical(scope):
    require(sys.platform=='linux' and sys.dont_write_bytecode and os.getuid()==os.geteuid()==1000
        and threading.current_thread() is threading.main_thread() and threading.active_count()==1
        and hasattr(os,'pidfd_open') and hasattr(signal,'pidfd_send_signal'))
    require({k:os.readlink('/proc/self/ns/'+k) for k in ('pid','net','mnt')}==scope['expected_namespaces'])
    require(signal.getsignal(signal.SIGCHLD)==signal.SIG_DFL and signal.getitimer(signal.ITIMER_REAL)==(0.0,0.0))


def _identity():
    fd=os.open('/proc/self/stat',os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
    try:
        raw=os.read(fd,16384);require(len(raw)<16384)
    finally:os.close(fd)
    tail=raw.decode().rsplit(')',1)[1].split()
    return {'pid':os.getpid(),'starttick':int(tail[19]),'uid':os.getuid()}


def _verify_sources(scope):
    from .raw_qc_runtime import _file
    package=Path(__file__).absolute().parent
    require(package.is_relative_to(PROJECT))
    for parent in (package,*package.parents):
        s=parent.lstat();require(stat.S_ISDIR(s.st_mode) and s.st_uid in (0,1000) and not s.st_mode&0o022)
    for name,pin in scope['source_manifest'].items():_file(package/name,pin['bytes'],pin['sha256'],1000,(0o444,0o600,0o644))
    from .r_raw_qc.raw_qc_runner import ENV,NATIVE_ENV,validate_native_directory_pins,NATIVE_EXECUTABLE,NATIVE_EXECUTABLE_SHA256
    _file(NATIVE_EXECUTABLE,14488,NATIVE_EXECUTABLE_SHA256,0,(0o755,))
    require(c.digest(dict(ENV,**NATIVE_ENV,TZ='UTC'))==json.loads(scope['profile_json'])['environment_sha256'])
    from .r_raw_qc.raw_qc_runner import DirectoryPin
    pins=[]
    for item in scope['native_directory_pins']:
        c.exact(item,('env_key','path','device','inode','owner','mode'))
        pins.append(DirectoryPin(item['env_key'],Path(item['path']),item['device'],item['inode'],item['owner'],item['mode']))
    validate_native_directory_pins(tuple(pins))
    return tuple(pins)


@dataclass(frozen=True,slots=True)
class ApprovedRuntime:
    _scope_json:bytes=field(repr=False)
    scope_sha256:str
    _process:tuple
    native_directory_pins:tuple=field(repr=False)
    roots:object=field(repr=False)
    _seal:object=field(repr=False)
    _monotonic_start:float=field(default_factory=time.monotonic,repr=False)
    @property
    def scope(self):return json.loads(self._scope_json)
    @property
    def proposal(self):return self.scope['proposal_json'].encode()
    @property
    def registration_id(self):return self.scope['registration_id']
    @property
    def max_jobs(self):return self.scope['max_jobs']
    @property
    def allow_confirmed_ack(self):return self.scope['allow_confirmed_ack']
    def expired(self):
        scope=self.scope;now=time.time()*1000;elapsed=time.monotonic()-self._monotonic_start
        return not (scope['issued_at_ms']<=now<scope['expires_at_ms'] and 0<=elapsed<(scope['expires_at_ms']-scope['issued_at_ms'])/1000)
    def check(self,*,registration=None,task=None):
        require(self._seal is _SEAL and settings.production_gate is _ORIGINAL_GATE and settings.load_runtime_password is _ORIGINAL_LOADER)
        settings.production_gate() # Original gate/loader are not replaced.
        scope=self.scope;_physical(scope);identity=_identity()
        require((identity['pid'],identity['starttick'],identity['uid'])==self._process)
        now=int(time.time()*1000);require(scope['issued_at_ms']<=now<scope['expires_at_ms'])
        elapsed=time.monotonic()-self._monotonic_start
        require(0<=elapsed<(scope['expires_at_ms']-scope['issued_at_ms'])/1000)
        _verify_sources(scope);self.roots.verify()
        if registration is not None:
            require(str(registration['registration_id'])==self.registration_id and str(registration['project_id'])==scope['project_id']
                and registration['startup_scope_sha256']==self.scope_sha256 and registration['profile_json']==scope['profile_json']
                and registration['proposal_sha256']==c.bytes_sha(self.proposal))
            require(registration['profile_sha256']==c.bytes_sha(scope['profile_json'].encode()))
            att=json.loads(registration['attestation_json'])
            require(c.bytes_sha(registration['attestation_json'].encode())==registration['attestation_sha256']
                and att.get('metadata_origin')=='actual_A_root_verified' and att.get('uid')==1000
                and att.get('namespaces')==scope['expected_namespaces'] and att.get('startup_scope_sha256')==self.scope_sha256
                and att.get('source_manifest_sha256')==c.digest(scope['source_manifest']))
        if task is not None:
            c.task_value(task);require(task['source']['project_id']==scope['project_id'])
            if scope['kind']=='preprocess_dev_sdk':require(task['source']['dataset_version_id']==scope['qa_refs']['dataset_version_id'])


def bootstrap(config=None,*,verify_probe_start=None,save_probe_process=None):
    """Root-only explicit arm, unavailable without the NEW approved callbacks.

    No tests call this with an actual approval. The Control extension must be
    source-reviewed/approved before A uses it; old api/node receipts are rejected.
    """
    if config is None:return None
    require(type(config) is ProbeConfig and type(config.approval_bytes) is int and c.hashed(config.approval_sha256))
    require(callable(verify_probe_start) and callable(save_probe_process))
    # Dedicated fresh process only: never change gates of a running API service.
    require(settings.AUTH_POLICY_VERIFIED is False and settings.RUNTIME_START_APPROVED is False)
    raw=_read_fixed(APPROVAL_PATH,config.approval_bytes,config.approval_sha256);scope=validate_scope(raw)
    _physical(scope);now=int(time.time()*1000);require(scope['issued_at_ms']<=now<scope['expires_at_ms'])
    pins=_verify_sources(scope);identity=_identity()
    expected={'kind':scope['kind'],'epoch_id':scope['epoch_id'],'scope_sha256':config.approval_sha256,
        'source_manifest_sha256':c.digest(scope['source_manifest']),**identity}
    verified=verify_probe_start(dict(expected),scope)
    require(type(verified) is dict and verified=={**expected,'verified':True})
    # This callback must atomically record a new nonce/identity and refuse replay.
    # A boolean, legacy api receipt or an unsaved process cannot enable the gate.
    saved=save_probe_process(dict(expected))
    require(type(saved) is dict and saved=={**expected,'recorded':True})
    from .raw_qc_checkpoint import WorkerRoots
    base=PROJECT/'runtime/preprocess-execution-001'/scope['epoch_id']
    roots=WorkerRoots(*(base/name for name in ('control','inputs','staging','published-scratch','receipts')))
    roots.verify() # Root provisioned fresh approved0700 directories, never here.
    approved=ApprovedRuntime(c.canonical(scope),config.approval_sha256,(identity['pid'],identity['starttick'],identity['uid']),pins,roots,_SEAL)
    # Normal activation AFTER new Root verification+recording+native/source/roots.
    # This private code path is neither an HTTP/test flag nor a gate replacement.
    settings.AUTH_POLICY_VERIFIED=True
    settings.RUNTIME_START_APPROVED=True
    try:
        settings.production_gate()
        approved.check();return approved
    except BaseException:
        settings.AUTH_POLICY_VERIFIED=False
        settings.RUNTIME_START_APPROVED=False
        raise


async def _check_sdk_identity(repository,*,worker=False):
    async def operation(query):
        row=await query("SELECT current_database(),current_user,current_setting('cyomics.cluster_kind',true),current_setting('server_version_num')")
        require(type(row) in (tuple,list) and len(row)==4 and row[0]=='cyomicsv2_business_dev'
            and row[1]==('cyomicsv2_raw_qc_worker_runtime' if worker else 'cyomicsv2_app_runtime')
            and row[2]=='businessdev' and int(row[3])//10000==17)
        return {'database':row[0],'role':row[1],'cluster':row[2],'server_major':17,'transaction_teardown_confirmed':True}
    from .settings import Budget
    if worker:return await repository._run(Budget(),operation)
    return await repository._run(repository._probe_subject,Budget(),operation)


async def run_probe(approved=None):
    """No parent provisioning here. Root separately creates the exact synthetic4
    parents and1registration under the SAME explicit9-of10-row campaign budget.
    This bounded SDK phase adds only job/run/version/bundle, never DELETE/Auth.
    """
    if approved is None:return {'status':'off'}
    require(type(approved) is ApprovedRuntime and approved.scope['kind']=='preprocess_dev_sdk')
    approved.check();scope=approved.scope;refs=scope['qa_refs']
    require(approved.scope_sha256 not in _PROBES_USED)
    _PROBES_USED.add(approved.scope_sha256) # Consume before any connection/write.
    from .preprocess_execution import create_runtime
    from .settings import Budget
    runtime=create_runtime(approved);unknown=False
    try:
        runtime.owner._probe_subject=refs['owner_id']
        identities=[await _check_sdk_identity(runtime.owner),await _check_sdk_identity(runtime.worker.repository,worker=True)]
        # Preexisting here means Root's explicitly approved new synthetic parents,
        # never a real Owner's project/file/report or an arbitrary user ref.
        source=await runtime.owner._run(refs['owner_id'],Budget(),lambda query:runtime.owner._source(query,refs['owner_id'],refs['project_id'],refs['dataset_version_id'],'LFQ intensity',{'contaminant':False,'reverse':False,'site_only':False}))
        require(source['file_id']==refs['file_id'] and source['row_count']<=6 and source['source_bytes']==58 and source['source_sha256']=='5f918b3dc1d7e82a2257f03b35170d3a684c4aff0ff7eb0734ad57890ec82374')
        request={'request_id':refs['request_id'],'quant_family':'LFQ intensity','parameters':json.loads(c.canonical_parameters({'contaminant':False,'reverse':False,'site_only':False}))}
        observation=await runtime.owner.enqueue(refs['owner_id'],refs['project_id'],refs['dataset_version_id'],c.canonical(request),Budget())
        require(observation.created is True and observation.commit_confirmed is True and observation.teardown_confirmed is True)
        task=observation.job_public();outcomes=await runtime.controller.drain(expected_job_id=task['job_id'])
        require(len(outcomes)==1 and outcomes[0].status=='confirmed' and outcomes[0].task()['status']=='succeeded')
        # Fresh independent Owner read lane after worker commit and cleanup.
        recovered=await runtime.owner.read_request(refs['owner_id'],refs['project_id'],refs['dataset_version_id'],refs['request_id'],Budget())
        require(recovered['status']=='succeeded' and recovered['job_id']==task['job_id'])
        vid=recovered['result']['processed_dataset_version_id'];version=await runtime.owner.read_version(refs['owner_id'],refs['project_id'],vid,Budget())
        downloaded=[]
        for name in c.ARTIFACT_NAMES:
            item=await runtime.owner.read_artifact(refs['owner_id'],refs['project_id'],vid,name,Budget())
            downloaded.append({'name':name,'bytes':len(item.body),'sha256':item.sha256})
        return {'status':'sdk_confirmed','job_id':task['job_id'],'version_id':vid,'identities':identities,'artifacts':downloaded,
            'row_campaign_expected':9,'row_campaign_maximum':10,'actual_Owner_Auth_E2E':False,'analysis_ready':version['analysis_ready']}
    except BaseException:
        unknown=True
        raise
    finally:
        closed=runtime.close()
        if not closed or unknown:
            # Never turn a failed cleanup into a success or clear worker latches.
            if not closed:raise Fault(503,'preprocessing_execution_unavailable') from None


_PROBES_USED=set()


async def run_worker(approved=None):
    if approved is None:return {'status':'off'}
    require(type(approved) is ApprovedRuntime and approved.scope['kind']=='preprocess_worker')
    approved.check()
    from .preprocess_execution import create_runtime
    runtime=create_runtime(approved)
    def continuing():
        try:approved.check();return True
        except BaseException:return False
    try:
        outcomes=await runtime.controller.serve(continue_approved=continuing)
        return {'status':'reconciliation_pending' if runtime.controller.quarantined else 'stopped',
            'completed':runtime.controller.completed,'outcomes':[{'status':o.status,'reason':o.reason,'job_id':o.job_id,'attempt_id':o.attempt_id} for o in outcomes]}
    finally:
        if not runtime.close():raise Fault(503,'preprocessing_execution_unavailable')


if __name__=='__main__':
    # Intentional inert public entry. Root's new approved Control must call the
    # typed bootstrap/SDK functions; no CLI/env JSON can self-activate this module.
    print('preprocessing_dev_probe_off_requires_new_Root_scope')
