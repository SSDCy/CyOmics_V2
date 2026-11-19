"""Separate Root Median service capability. Original global operator path untouched.
No CLI/HTTP/ENV approval; no credential copy, roots provisioning or broad queue scan.
"""
from dataclasses import dataclass,field
from pathlib import Path
from datetime import datetime,timezone
import asyncio,fcntl,hashlib,json,os,stat,sys,time
from uuid import UUID
from . import preprocess_dev_probe as original,preprocess_contract as c,preprocess_repository as p
from .settings import Fault
from . import settings

ROOT=Path('/home/user01/Cy_Omics_v2.0/runtime/preprocess-median-service-001')
APPROVAL=ROOT/'Root-service-approval.json'
SEAL=object()
KEYS=frozenset(('schemaVersion','contract','scope_id','scope_sha256','project_id','owner_id','dataset_version_id',
    'parent_version_id','parent_manifest_sha256','parameters_json','source_json','proposal_json','profile_json','source_manifest',
    'expected_namespaces','native_directory_pins','db_target','auth_policy_evidence_sha256','issued_at_ms','expires_at_ms',
    'max_registrations','slots','process_spec','startup_pin','python_pin','attestation_json'))


def need(ok):
    if not ok:raise Fault(503,'preprocessing_execution_unavailable')


@dataclass(frozen=True,slots=True)
class ServiceWorkerConfig:
    approval_bytes:int
    approval_sha256:str
    backend:Path
    startup:Path
    launches:Path=ROOT/'launches'
    approval_path:Path=APPROVAL


def prepare(config):
    need(type(config) is ServiceWorkerConfig and type(config.approval_bytes) is int and 1<=config.approval_bytes<=32768
        and c.hashed(config.approval_sha256) and config.approval_path==APPROVAL and config.launches==ROOT/'launches')
    need(all(isinstance(v,Path) and v.is_absolute() and '..' not in v.parts for v in (config.backend,config.startup)))
    need(config.backend.is_relative_to(original.PROJECT) and config.backend.name=='backend' and config.startup.is_relative_to(original.PROJECT))
    return config


def read_private(path,cap):
    need(not any(p.is_symlink() for p in (path,*path.parents)))
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        before=os.fstat(fd);need(stat.S_ISREG(before.st_mode) and before.st_uid==1000 and before.st_nlink==1 and stat.S_IMODE(before.st_mode)==0o600 and 1<=before.st_size<=cap)
        raw=os.read(fd,cap+1)
        def signature(info):return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
        need(len(raw)==before.st_size and len(raw)<=cap and signature(os.fstat(fd))==signature(before)==signature(path.lstat()))
        value=c.document(raw,cap);need(c.canonical(value)+b'\n'==raw);return value
    finally:os.close(fd)


def write_once(path,value):
    """Only preprovisioned0700 safe parents; never create directories/retry close."""
    fds=[];uncertain=False
    try:
        fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC);fds.append(fd)
        for part in path.parent.parts[1:]:
            fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd);fds.append(fd)
            s=os.fstat(fd);need(stat.S_ISDIR(s.st_mode) and s.st_uid in (0,1000) and not s.st_mode&0o022)
        parent=fd;s=os.fstat(parent);need(s.st_uid==1000 and stat.S_IMODE(s.st_mode)==0o700)
        raw=c.canonical(value)+b'\n';need(len(raw)<=32768)
        fd=os.open(path.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=parent);fds.append(fd)
        used=0
        while used<len(raw):n=os.write(fd,raw[used:]);need(n>0);used+=n
        os.fsync(fd);os.fsync(parent)
    finally:
        while fds:
            try:os.close(fds.pop())
            except BaseException:uncertain=True
        if uncertain:raise Fault(503,'preprocessing_execution_unavailable') from None


def scope_value(raw):
    v=c.document(raw,32768);need(type(v) is dict and set(v)==KEYS and c.canonical(v)==raw)
    need(type(v['schemaVersion']) is int and v['schemaVersion']==1 and v['contract']=='root_median_service_scope_v1')
    for k in ('scope_id','project_id','owner_id','dataset_version_id','parent_version_id'):need(p.identifier(v[k]))
    need(v['project_id']=='47278eaf-c156-4b66-b60a-287ca4b23805' and v['dataset_version_id']=='28bc9473-173f-5c48-bc61-7edc7c40f584'
        and v['parent_version_id']=='2fc0a8e6-ef3d-4afe-a15c-f27043b00227' and c.hashed(v['parent_manifest_sha256']))
    policy={k:value for k,value in v.items() if k not in ('scope_sha256','attestation_json')}
    need(c.digest(policy)==v['scope_sha256'] and c.hashed(v['auth_policy_evidence_sha256']))
    need(type(v['issued_at_ms']) is int and type(v['expires_at_ms']) is int and 0<v['expires_at_ms']-v['issued_at_ms']<=43200000
        and type(v['max_registrations']) is int and v['max_registrations']==4)
    need(v['db_target']=={'host':'172.19.0.2','port':5432,'database':'cyomicsv2_business_dev','app_role':'cyomicsv2_app_runtime','worker_role':'cyomicsv2_raw_qc_worker_runtime'})
    proposal=p.execution_value(v['proposal_json']);from . import median_processed_profile as median
    need(proposal['contract']==median.PROPOSAL and v['proposal_json'].encode()==median.proposal_bytes())
    params=p.document(v['parameters_json'],4096);median.validate_parameters(params)
    need(params['normalization_parent']==dict(processed_dataset_version_id=v['parent_version_id'],manifest_sha256=v['parent_manifest_sha256']))
    source=p.source_value(p.document(v['source_json'],4096))
    need((source['project_id'],source['dataset_version_id'])==(v['project_id'],v['dataset_version_id']))
    manifest=v['source_manifest'];need(type(manifest) is dict and original.REQUIRED_SOURCES|{'preprocess_median_service_bootstrap.py','median_on_demand.py','classification_on_demand.py',
        'median_processed_profile.py','median_processed_io.py','median_processed_runner.py','median_processed_validator.py','r_median_processed/median_processed.R','r_normalization_median/median_core.R'}<=set(manifest) and len(manifest)<=64)
    for name,pin in manifest.items():
        need(type(name) is str and Path(name).as_posix()==name and not name.startswith('/') and '..' not in Path(name).parts and Path(name).suffix in ('.py','.R'))
        c.exact(pin,('bytes','sha256'));need(type(pin['bytes']) is int and 1<=pin['bytes']<=262144 and c.hashed(pin['sha256']))
    need(manifest['median_processed_runner.py']==proposal['runner'] and manifest['median_processed_validator.py']==proposal['validator']
        and manifest['r_median_processed/median_processed.R']==proposal['script'] and manifest['r_normalization_median/median_core.R']==median.CORE_PIN)
    profile=p.document(v['profile_json'],32768)
    expected=dict(contract='preprocessing_runtime_profile_v1',proposal_sha256=p.digest(v['proposal_json']),source_manifest_sha256=c.digest(manifest),
        environment_sha256=profile.get('environment_sha256'),r_version=proposal['r_version'],script=proposal['script'],validator=proposal['validator'],runner=proposal['runner'],
        native=proposal['executable'],limits=proposal['limits'],validation_limits={'per_file':c.MAX_ARTIFACT,'total':c.MAX_ARTIFACT_TOTAL},process_policy_sha256=proposal['process_policy_sha256'])
    need(profile==expected and c.hashed(profile['environment_sha256']))
    att=p.document(v['attestation_json'],32768);need(att.get('metadata_origin')=='actual_A_root_verified' and att.get('uid')==1000
        and att.get('namespaces')==v['expected_namespaces'] and att.get('source_manifest_sha256')==profile['source_manifest_sha256'])
    c.exact(v['expected_namespaces'],('pid','net','mnt'));need(v['expected_namespaces']=={'pid':'pid:[4026532213]','net':'net:[4026531833]','mnt':'mnt:[4026532211]'})
    need(type(v['native_directory_pins']) is list and len(v['native_directory_pins'])==5)
    slots=v['slots'];need(type(slots) is list and len(slots)==4)
    for i,slot in enumerate(slots):
        c.exact(slot,('root','device','inode','uid','mode'));need(slot['root']==str(ROOT/'slots'/str(i)) and slot['uid']==1000 and slot['mode']==0o700
            and all(type(slot[k]) is int and slot[k]>0 for k in ('device','inode')))
    for pin in (v['startup_pin'],v['python_pin']):
        c.exact(pin,('bytes','sha256'));need(type(pin['bytes']) is int and 1<=pin['bytes']<=33554432 and c.hashed(pin['sha256']))
    c.exact(v['process_spec'],('uid','cwd','executable','argv'));spec=v['process_spec']
    need(spec['uid']==1000 and spec['executable']=='/home/user01/Cy_Omics_v2.0/releases/backend-review-20261116-v1-projects-owner/venv/bin/python'
        and type(spec['argv']) is list and len(spec['argv'])==5 and spec['argv'][:3]==[spec['executable'],'-I','-B'] and spec['argv'][-1]=='--once')
    return v


def load_service(config,*,child):
    prepare(config);raw=original._read_fixed(config.approval_path,config.approval_bytes,config.approval_sha256);v=scope_value(raw)
    now=int(time.time()*1000);need(v['issued_at_ms']<=now<v['expires_at_ms'])
    spec=v['process_spec'];need(spec['cwd']==str(config.backend) and spec['argv'][3]==str(config.startup))
    need(Path(__file__).absolute().parent==config.backend/'cyomics_projects')
    need(settings.production_gate is original._ORIGINAL_GATE and settings.load_runtime_password is original._ORIGINAL_LOADER)
    from .classification_on_demand import _read_public
    for path,pin in ((config.startup,v['startup_pin']),(Path(spec['executable']).resolve(strict=True),v['python_pin'])):
        raw=_read_public(path,33554432);need(len(raw)==pin['bytes'] and c.bytes_sha(raw)==pin['sha256'])
    if child:original._physical(v)
    pins=original._verify_sources(v)
    for slot in v['slots']:
        path=Path(slot['root']);s=path.lstat();need(not any(p.is_symlink() for p in (path,*path.parents))
            and (s.st_dev,s.st_ino,s.st_uid,stat.S_IMODE(s.st_mode))==(slot['device'],slot['inode'],slot['uid'],slot['mode']))
    # Profile is a parsed convenience key returned internally, never in policy hash.
    return dict(v,profile=p.document(v['profile_json'],32768),physical_native_pins=tuple(pins))


def parse_notification(raw):
    from .median_on_demand import IPC_KEYS
    need(type(raw) is bytes and 0<len(raw)<=1024 and raw.endswith(b'\n'))
    value=c.document(raw,1024);need(type(value) is dict and set(value)==IPC_KEYS and c.canonical(value)+b'\n'==raw)
    for k in IPC_KEYS-{'slot','scope_sha256','request_sha256','binding_sha256'}:need(p.identifier(value[k]))
    for k in ('scope_sha256','request_sha256','binding_sha256'):need(c.hashed(value[k]))
    need(type(value['slot']) is int and 0<=value['slot']<4);return value


@dataclass(frozen=True,slots=True)
class ServiceApprovedRuntime:
    config:ServiceWorkerConfig
    _scope:dict=field(repr=False)
    _ticket:dict=field(repr=False)
    _process:tuple
    roots:object=field(repr=False)
    native_directory_pins:tuple=field(repr=False)
    _seal:object=field(repr=False)
    _mono:float=field(default_factory=time.monotonic,repr=False)
    @property
    def proposal(self):return self._scope['proposal_json'].encode()
    @property
    def registration_id(self):return self._ticket['registration_id']
    @property
    def scope_sha256(self):return self._scope['scope_sha256']
    @property
    def max_jobs(self):return 1
    @property
    def allow_confirmed_ack(self):return True
    def expired(self):
        return time.time()>=self._ticket['expires_at'].timestamp() or time.monotonic()-self._mono>=self._ticket['lifetime_seconds']
    def check(self,*,registration=None,task=None):
        need(self._seal is SEAL and not self.expired());s=load_service(self.config,child=True)
        need(s['scope_sha256']==self.scope_sha256 and original._identity()==dict(zip(('pid','starttick','uid'),self._process)))
        settings.production_gate();self.roots.verify()
        if registration is not None:
            t=self._ticket
            need(str(registration['registration_id'])==t['registration_id'] and str(registration['epoch_id'])==t['epoch_id']
                and str(registration['project_id'])==s['project_id'] and registration['max_jobs']==1
                and registration['startup_scope_sha256']==s['scope_sha256'] and registration['profile_json']==s['profile_json']
                and registration['proposal_sha256']==p.digest(s['proposal_json']) and registration['profile_sha256']==p.digest(s['profile_json'])
                and registration['attestation_json']==t['attestation_json'] and registration['attestation_sha256']==p.digest(t['attestation_json']))
        if task is not None:
            c.task_value(task);t=self._ticket
            need(task['job_id']==t['planned_job_id'] and task['request_id']==t['request_id'] and task['request_sha256']==t['request_sha256']
                and task['binding_sha256']==t['binding_sha256'] and task['parameters']==p.document(s['parameters_json'],4096)
                and task['source']==p.document(s['source_json'],4096) and task['quant_family']=='LFQ intensity')


async def bootstrap_service(config,notification):
    """A real dedicated service process, with DB ticket + Root proof + identity once.
    Original pools are activated ONLY after physical Root service checks/recording.
    DB ticket is then read independently before the capability can reach a worker.
    """
    need(settings.AUTH_POLICY_VERIFIED is False and settings.RUNTIME_START_APPROVED is False)
    s=load_service(config,child=True);n=notification
    need((n['owner_id'],n['project_id'],n['dataset_version_id'],n['scope_sha256'])==(s['owner_id'],s['project_id'],s['dataset_version_id'],s['scope_sha256']))
    spec=s['process_spec'];identity=original._identity()
    need(os.readlink('/proc/self/cwd')==spec['cwd'] and sys.executable==spec['executable']
        and Path('/proc/self/cmdline').read_bytes().removesuffix(b'\0').split(b'\0')==[v.encode() for v in spec['argv']])
    from .raw_qc_checkpoint import WorkerRoots
    base=Path(s['slots'][n['slot']]['root']);roots=WorkerRoots(*(base/name for name in ('control','inputs','staging','published-scratch','receipts')));roots.verify()
    lock_fd=os.open(ROOT/'service.lock',os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC);st=os.fstat(lock_fd)
    need(stat.S_ISREG(st.st_mode) and st.st_uid==1000 and st.st_nlink==1 and stat.S_IMODE(st.st_mode)==0o600)
    try:fcntl.flock(lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BaseException:os.close(lock_fd);raise
    # A known nonce is never removed/reused, including failures before DB access.
    try:
        write_once(base/'control'/'service-process.once',dict(contract='median_service_process_once_v1',notification=n,identity=identity,approval_sha256=config.approval_sha256))
        settings.AUTH_POLICY_VERIFIED=True;settings.RUNTIME_START_APPROVED=True;settings.production_gate()
        from .repository import FixedPool
        pool=FixedPool();owner=p.PreprocessRepository(pool,s['proposal_json'].encode(),enabled=True)
        try:
            async def operation(q):
                await owner._owner(q,n['owner_id'],n['project_id'])
                return await q('SELECT expires_at,attestation_json FROM cyomics_app.read_median_service_ticket(%s,%s,%s)',
                    (UUID(n['registration_id']),UUID(n['planned_job_id']),n['scope_sha256']))
            row=await owner._run(n['owner_id'],settings.Budget(9),operation)
            need(row is not None and len(row)==2 and type(row[0]) is datetime and row[0].utcoffset() is not None and row[0].timestamp()>time.time())
            att=p.document(row[1],32768)
            expected=dict(metadata_origin='root_service_scope_derived',service_scope_id=s['scope_id'],startup_scope_sha256=s['scope_sha256'],
                root_attestation_sha256=p.digest(s['attestation_json']),source_manifest_sha256=s['profile']['source_manifest_sha256'],
                owner_id=n['owner_id'],project_id=n['project_id'],dataset_version_id=n['dataset_version_id'],request_id=n['request_id'],
                request_sha256=n['request_sha256'],binding_sha256=n['binding_sha256'],planned_job_id=n['planned_job_id'],
                registration_id=n['registration_id'],epoch_id=n['epoch_id'],slot=n['slot'])
            need(att==expected);ticket=dict(n,expires_at=row[0],attestation_json=row[1],lifetime_seconds=min(900,row[0].timestamp()-time.time()))
            approved=ServiceApprovedRuntime(config,s,ticket,(identity['pid'],identity['starttick'],identity['uid']),roots,s['physical_native_pins'],SEAL)
            approved.check();return approved,lock_fd
        finally:pool.close()
    except BaseException:
        os.close(lock_fd);raise


def main_from_server(config=None,raw=None):
    """Original exact-job runner and original event-loop/signal teardown fences."""
    from .raw_qc_once_entry import _eligible_context,_State,_Signals,_settle_and_close,_Interrupted
    from . import preprocess_execution as execution
    result=dict(schemaVersion=1,job_id=None,request_id=None,registration_id=None,attempt_id=None,result_id=None,
        disposition='unknown',reason='entry_unknown',acknowledged=False,teardown='unknown',loop_closed=None,
        pending_tasks_seen=False,async_resources_remaining=False,interrupted=False,exit_code=1)
    state=_State();fence=_Signals(state);loop=None;lock=None;runtime=None
    async def run():
        nonlocal runtime,lock
        n=parse_notification(raw);result.update(job_id=n['planned_job_id'],request_id=n['request_id'],registration_id=n['registration_id'])
        approval,lock=await bootstrap_service(config,n);runtime=execution.create_runtime(approval)
        try:
            outcomes=await runtime.controller.drain(expected_job_id=n['planned_job_id'])
            need(len(outcomes)==2 and outcomes[0].status=='confirmed' and outcomes[0].reason in ('succeeded','failed')
                and outcomes[1].status=='acknowledged' and outcomes[0].job_id==outcomes[1].job_id==n['planned_job_id'])
            first=outcomes[0];result.update(attempt_id=first.attempt_id,reason=first.reason,result_id=first.task()['result']['processed_dataset_version_id'] if first.reason=='succeeded' else None,
                disposition='confirmed',acknowledged=True)
        finally:need(runtime.close())
    try:
        need(config is not None and raw is not None and _eligible_context());fence.install();loop=asyncio.new_event_loop()
        def quiet(_loop,_context):state.loop_error=True
        loop.set_exception_handler(quiet);loop.run_until_complete(run())
    except BaseException as error:
        if isinstance(error,(asyncio.CancelledError,KeyboardInterrupt,_Interrupted)):state.interrupted=True
        state.loop_error=True
    finally:
        if loop is not None:
            pending,resources,closed=_settle_and_close(loop,state);result.update(loop_closed=closed,pending_tasks_seen=pending,async_resources_remaining=resources)
        fence.close();result['interrupted']=state.interrupted
        if lock is not None:
            try:os.close(lock)
            except BaseException:state.cleanup_error=True
        clean=result['acknowledged'] and result['loop_closed'] is True and not any((result['pending_tasks_seen'],result['async_resources_remaining'],state.interrupted,state.loop_error,state.cleanup_error))
        if clean:result.update(teardown='confirmed',exit_code=0)
        else:result.update(disposition='unknown',teardown='unknown',exit_code=1)
    sys.stdout.write(c.canonical(result).decode()+'\n');return result['exit_code']
