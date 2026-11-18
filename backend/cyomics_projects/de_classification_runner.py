"""Default-off, one-shot classification adapter, no DB/Auth/HTTP/publication.

Inherits the ORIGINAL shared run/failure trace, spawn, OwnedTasks and IO/close
methods. Only fixed input/output staging and scientific contract glue differ.
Root must separately authorize the classification child and exact runtime pins.
"""
from dataclasses import dataclass,field
from pathlib import Path
import hashlib,json,sys,time
from . import log2_matrix_runner as shared,de_classification_contract as contract
from .limma_bound_runner import LimmaBoundRunner,uuid

SCRIPT_PIN=shared.Pin(10335,'65e24d287a1546dd7a9fa0480f2a899d4d91eb4b11cda7a4802256fb90f3c421')
SHARED_PIN=shared.Pin(30207,'6f7839b4a867cea8fd9aef63b684ac8c9dbf63dbc7a95bb289080e8aa8074a3e')
CONTRACT_PIN=shared.Pin(12074,'2d37d97fcbbe6bb5c323c887b8c168e68c19945904dd3fe5bc9b247174a5d584')
NATIVE_PIN=shared.Pin(14488,'9c572a708d444e0ec91c59935bf9b5c3fd426a75fe2b7101b2a65f4783155283')
TMP_OBSERVER_PIN=shared.Pin(19031,'14a330bd1c1c84879ed8f7ad90bb48debc72449e5475402b18db6b0411a6d7d5')

@dataclass(frozen=True,slots=True)
class Config:
    enabled:bool=False
    epoch_id:str|None=None
    job_id:str|None=None # NEW derived invocation, never parent job identity.
    attempt_id:str|None=None
    owner_id:str|None=None
    project_id:str|None=None
    source_result_id:str|None=None
    source_binding_sha256:str|None=None
    parameters_sha256:str|None=None
    staging_root:Path|None=field(default=None,repr=False)
    adapter_pin:shared.Pin|None=None # Trusted Root's independently pinned module.
    environment_sha256:str|None=None
    limits_sha256:str|None=None
    process_policy_sha256:str|None=None
    native_directory_pins:tuple=field(default=(),repr=False)
    verify:object=field(default=None,repr=False)

@dataclass(frozen=True,slots=True)
class RunResult:
    bundle:contract.DerivedClassificationBundle=field(repr=False)
    _receipt:bytes=field(repr=False)
    @property
    def receipt(self):return json.loads(self._receipt)

class ClassificationRunner(shared.Log2MatrixRunner):
    def __init__(self,config=None):
        shared.need(config is None or type(config) is Config,'invalid_de_config')
        super().__init__();self._config=config;self._source=self._parameters=None;self._used=False
    def run(self,source=None,parameters=None,*,cancelled=lambda:False):
        if self._config is None or self._config.enabled is False:return None
        shared.need(not self._active,'log2_runner_busy');shared.need(not self._quarantined,'log2_runner_quarantined')
        shared.need(not self._used,'de_attempt_consumed');self._used=True
        self._source=source;self._parameters=parameters
        return super().run(cancelled=cancelled)
    def _configuration(self):
        c=self._config
        shared.need(type(c) is Config and type(c.enabled) is bool and c.enabled
            and all(uuid(getattr(c,k)) for k in ('epoch_id','job_id','attempt_id','owner_id','project_id','source_result_id'))
            and all(type(getattr(c,k)) is str and contract.admission.pc.hashed(getattr(c,k)) for k in
                ('source_binding_sha256','parameters_sha256','environment_sha256','limits_sha256','process_policy_sha256'))
            and type(c.native_directory_pins) is tuple and len(c.native_directory_pins)==5 and callable(c.verify)
            and isinstance(c.staging_root,Path) and c.staging_root.is_absolute() and '..' not in c.staging_root.parts
            and shared._valid_pin(c.adapter_pin,262144),'invalid_de_config')
        shared.need(c.verify() is None,'de_Root_control_rejected')
        binding=contract.verify_source(self._source,check=lambda:shared.need(c.verify() is None,'de_Root_control_rejected'))
        shared.need(type(self._parameters) is contract.Parameters and contract.parameters(self._parameters.body)==self._parameters
            and contract.sha(self._parameters.body)==c.parameters_sha256,'de_parameters_invalid')
        shared.need(self._source.binding_sha256==c.source_binding_sha256
            and (binding['owner_id'],binding['project_id'],binding['source_result_id'])==(c.owner_id,c.project_id,c.source_result_id)
            and c.job_id!=binding['source_job_id'] and c.attempt_id!=binding['source_attempt_id'],'de_parent_binding_invalid')
        return c
    def _failure_binding(self):
        c=self._config
        try:
            pins=[{k:str(getattr(p,k)) if k=='path' else getattr(p,k) for k in
                ('env_key','path','device','inode','owner','mode')} for p in c.native_directory_pins]
            value={k:getattr(c,k) for k in ('epoch_id','job_id','attempt_id','owner_id','project_id','source_result_id',
                'source_binding_sha256','parameters_sha256','environment_sha256','limits_sha256','process_policy_sha256')}
            value.update(native_directory_pins=pins,staging_root=str(c.staging_root),
                script={'bytes':SCRIPT_PIN.bytes,'sha256':SCRIPT_PIN.sha256},
                validator={'bytes':CONTRACT_PIN.bytes,'sha256':CONTRACT_PIN.sha256},
                adapter={'bytes':c.adapter_pin.bytes,'sha256':c.adapter_pin.sha256},
                supervisor={'bytes':SHARED_PIN.bytes,'sha256':SHARED_PIN.sha256},
                tmp_observer={'bytes':TMP_OBSERVER_PIN.bytes,'sha256':TMP_OBSERVER_PIN.sha256},
                native={'bytes':NATIVE_PIN.bytes,'sha256':NATIVE_PIN.sha256})
            digest=contract.sha(contract.canonical(value))
        except BaseException:digest=None
        return {'job_id':getattr(c,'job_id',None),'attempt_id':getattr(c,'attempt_id',None),'configuration_sha256':digest}
    def _outputs(self):return contract
    def _job_directories(self):return frozenset(('output','tmp'))
    def _invocation(self,b,job):
        return [str(b.NATIVE_EXECUTABLE),'--vanilla','--slave','--file='+str(job/'script.R'),'--args',
            *(str(job/n) for n in (*contract.SOURCES,'parameters.tsv')),str(job/'output')]
    def _environment(self,b,job):return dict(super()._environment(b,job),TMPDIR=str(job/'tmp'))
    _temp_scan=LimmaBoundRunner._temp_scan # Exact already-reviewed tmp observer, no copy.
    def _scan(self,b,job,staged,check,expected=None):
        total=super()._scan(b,job,staged,check,expected)+self._temp_scan(b,job/'tmp',check)
        shared.need(total<=contract.MAX_TOTAL,'de_total_limit');return total
    def _complete(self,rc,stdout,stderr,outputs,process,limits,policy,environment,*,check):
        shared.need(type(rc) is int and rc in (0,3,4) and stdout==stderr==b'','de_executor_failed')
        owned=process.get('owned_tasks',{})
        shared.need(owned.get('cleanup',{}).get('complete_for_observed_identities') is True
            and owned.get('uncertainty')==[],'de_cleanup_unconfirmed')
        c=self._config
        shared.need(contract.sha(contract.canonical(limits))==c.limits_sha256 and policy==c.process_policy_sha256
            and environment==c.environment_sha256,'de_runtime_profile_mismatch')
        bundle=contract.validate(self._source,self._parameters,rc,outputs,check=check)
        receipt={'schemaVersion':1,'contract':'de_classification_receipt_v0.1',**self._failure_binding(),
            'epoch_id':c.epoch_id,'source_binding':self._source.binding,'source_binding_sha256':bundle.source_binding_sha256,
            'parameters':contract.pin(self._parameters.body),'script':{'bytes':SCRIPT_PIN.bytes,'sha256':SCRIPT_PIN.sha256},
            'validator':{'bytes':CONTRACT_PIN.bytes,'sha256':CONTRACT_PIN.sha256},'adapter':{'bytes':c.adapter_pin.bytes,'sha256':c.adapter_pin.sha256},
            'supervisor':{'bytes':SHARED_PIN.bytes,'sha256':SHARED_PIN.sha256},'native':{'bytes':NATIVE_PIN.bytes,'sha256':NATIVE_PIN.sha256},
            'process_control':process,'limits':limits,'process_policy_sha256':policy,'environment_sha256':environment,
            'contract_per_file_bytes':contract.MAX_FILE,'contract_total_bytes':contract.MAX_TOTAL,
            'exit_code':rc,'classification_status':bundle.status,'DE_members_evaluated':bundle.DE_members_evaluated,
            'manifest_sha256':bundle.manifest_sha256,'all_six_outputs_verified':True,'resource_supervisor_reused':True,
            'close_confirmed':True,'published':False,'Owner_Auth_asserted':False,'new_profile_runtime_approved':False,
            'statistics_recomputed':False}
        check();return RunResult(bundle,contract.canonical(receipt))
    def _run_native(self,*,cancelled=lambda:False):
        c=self._configuration();shared.need(callable(cancelled),'invalid_de_cancellation')
        shared.need(sys.platform=='linux' and sys.dont_write_bytecode,'dedicated_linux_worker_required')
        b=shared._runtime()
        shared.need(b.os.getuid()==b.os.geteuid()==1000 and b.threading.current_thread() is b.threading.main_thread()
            and b.threading.active_count()==1,'dedicated_linux_worker_required')
        shared.need(b.signal.getsignal(b.signal.SIGCHLD)==b.signal.SIG_DFL and b.signal.getitimer(b.signal.ITIMER_REAL)==(0.0,0.0)
            and hasattr(b.os,'pidfd_open') and hasattr(b.signal,'pidfd_send_signal'),'dedicated_signal_context_required')
        limits=b.Limits();deadline=time.monotonic()+limits.wall_seconds
        self._active=True;old_handler=b.signal.getsignal(b.signal.SIGALRM);result=None;failure=None
        def check():
            shared.need(c.verify() is None,'de_Root_control_rejected')
            value=cancelled();shared.need(type(value) is bool,'invalid_de_cancellation')
            shared.need(not value,'de_cancelled');shared.need(time.monotonic()<deadline,'de_timeout')
        def expired(*_):raise shared.Log2RunFailure('de_timeout')
        try:
            trace=self._failure_trace
            if trace is not None:trace['handler_pending']=True;trace['timer_pending']=True
            b.signal.signal(b.signal.SIGALRM,expired);b.signal.setitimer(b.signal.ITIMER_REAL,limits.wall_seconds)
            check();b.validate_native_directory_pins(c.native_directory_pins)
            root=c.staging_root;self._parents(b,root);root_info=root.lstat()
            shared.need(b.stat.S_ISDIR(root_info.st_mode) and root_info.st_uid==1000 and not root_info.st_mode&0o077,'de_staging_unsafe')
            script=Path(__file__).parent/'r_de_classification/classify_de.R'
            fixed=((script,SCRIPT_PIN),(Path(contract.__file__),CONTRACT_PIN),(Path(__file__),c.adapter_pin),
                (Path(sys.modules[LimmaBoundRunner.__module__].__file__),TMP_OBSERVER_PIN),
                (Path(shared.__file__),SHARED_PIN),(b.NATIVE_EXECUTABLE,NATIVE_PIN))
            native=b.NATIVE_EXECUTABLE.lstat()
            shared.need(b.NATIVE_EXECUTABLE_SHA256==NATIVE_PIN.sha256 and b.stat.S_ISREG(native.st_mode) and native.st_uid==0
                and b.stat.S_IMODE(native.st_mode)==0o755 and b.os.access(b.NATIVE_EXECUTABLE,b.os.X_OK),'unsafe_de_native')
            content={};identities={}
            for path,pin in fixed:content[path],identities[path]=self._read(b,path,pin.bytes,check,pin)
            job=root/(c.job_id+'.'+c.attempt_id+'.partial')
            shared.need(not b.os.path.lexists(job),'de_attempt_already_exists');job.mkdir(mode=0o700)
            (job/'output').mkdir(mode=0o700);(job/'tmp').mkdir(mode=0o700)
            files=self._source.files();files.update({'parameters.tsv':self._parameters.body,'script.R':content[script]})
            staged={name:self._write_snapshot(b,job/name,data,check) for name,data in files.items()}
            environment=contract.sha(contract.canonical(self._environment(b,job)))
            shared.need(environment==c.environment_sha256 and contract.sha(contract.canonical(b.asdict(limits)))==c.limits_sha256
                and b.process_policy.POLICY_SHA256==c.process_policy_sha256,'de_runtime_profile_mismatch')
            self._scan(b,job,staged,check)
            rc,stdout,stderr,process=self._spawn_and_wait(b,job,staged,check,deadline,limits)
            shared.need(type(rc) is int and rc in (0,3,4) and stdout==stderr==b'','de_executor_failed')
            outputs,output_ids=self._collect(b,job,staged,check,contract.OUTPUTS)
            for path,pin in fixed:
                _,current=self._read(b,path,pin.bytes,check,pin)
                shared.need(current==identities[path],'de_fixed_identity_changed')
            b.validate_native_directory_pins(c.native_directory_pins)
            after=root.lstat();shared.need((after.st_dev,after.st_ino,after.st_uid,after.st_mode)==
                (root_info.st_dev,root_info.st_ino,root_info.st_uid,root_info.st_mode),'de_staging_changed')
            self._scan(b,job,staged,check,contract.OUTPUTS)
            for name,identity in output_ids.items():shared.need(b.identity((job/'output'/name).lstat())==identity,'de_output_changed')
            result=self._complete(rc,stdout,stderr,outputs,process,b.asdict(limits),b.process_policy.POLICY_SHA256,environment,check=check)
        except BaseException as error:
            self._quarantined=True
            failure=shared.Log2RunFailure(error.code if isinstance(error,(shared.Log2RunFailure,contract.InvalidClassification)) else 'de_run_unconfirmed')
        finally:
            try:
                b.signal.setitimer(b.signal.ITIMER_REAL,0)
                if self._failure_trace is not None:self._failure_trace['timer_pending']=False
            except BaseException:
                if self._failure_trace is not None:self._failure_trace['timer_disable_unknown']=True
                failure=shared.Log2RunFailure('log2_cleanup_unconfirmed');self._quarantined=True
            try:
                b.signal.signal(b.signal.SIGALRM,old_handler)
                if self._failure_trace is not None:self._failure_trace['handler_pending']=False
            except BaseException:
                if self._failure_trace is not None:self._failure_trace['handler_restore_unknown']=True
                failure=shared.Log2RunFailure('log2_cleanup_unconfirmed');self._quarantined=True
            self._active=False
        if failure is not None:raise failure from None
        shared.need(result is not None,'de_run_unconfirmed');return result
