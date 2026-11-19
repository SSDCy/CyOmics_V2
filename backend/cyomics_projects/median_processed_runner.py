"""Median saved-parent adapter over original native process/owned-task controller.
Original None runner is byte-identical. No approval, credential, API or publish.
"""
from dataclasses import dataclass,field
from pathlib import Path
from uuid import UUID
import hashlib,json,sys,time
from . import log2_matrix_runner as old
from . import log2_matrix_validator as legacy
from . import median_processed_validator as validator
from . import median_processed_profile as profile
from . import preprocess_contract as contract
Pin=old.Pin
Log2RunFailure=old.Log2RunFailure
need=old.need
RunResult=old.RunResult
_canonical=old._canonical
_pin=old._pin
_valid_pin=old._valid_pin
_runtime=old._runtime

@dataclass(frozen=True,slots=True)
class Config:
    enabled:bool=False
    job_id:str|None=None
    attempt_id:str|None=None
    source:Path|None=field(default=None,repr=False)
    source_pin:Pin|None=None
    selected:Path|None=field(default=None,repr=False)
    selected_pin:Pin|None=None
    parameters_pin:Pin|None=None
    script:Path|None=field(default=None,repr=False)
    staging_root:Path|None=field(default=None,repr=False)
    family:str|None=None
    flags:tuple=()
    native_directory_pins:tuple=field(default=(),repr=False)
    extras:tuple=field(default=(),repr=False)
    parent_metadata_json:bytes=field(default=b'',repr=False)
    parameters_json:bytes=field(default=b'',repr=False)

def configuration_binding(config):
    if type(config) is not Config:return None
    def pin(value):return {'bytes':value.bytes,'sha256':value.sha256}
    return contract.bytes_sha(contract.canonical({'contract':profile.STRATEGY,'job_id':config.job_id,
        'attempt_id':config.attempt_id,'family':config.family,'flags':list(config.flags),
        'source':pin(config.source_pin),'selected':pin(config.selected_pin),'parameters':pin(config.parameters_pin),
        'parent_metadata_sha256':contract.bytes_sha(config.parent_metadata_json),
        'extras':[{ 'name':name,**pin(p)} for name,_,p,_ in config.extras]}))

class MedianProcessedRunner(old.Log2MatrixRunner):
    def __init__(self,config=None):
        need(config is None or type(config) is Config,'invalid_log2_config')
        super().__init__();self._config=config

    def _configuration(self):
        c=self._config
        need(type(c) is Config and type(c.enabled) is bool and c.enabled,'log2_runner_disabled')
        need(contract._canonical_uuid(c.job_id) and contract._canonical_uuid(c.attempt_id),'invalid_log2_config')
        need(c.family in ('Intensity','LFQ intensity') and type(c.flags) is tuple and len(c.flags)==3
            and all(type(v) is bool for v in c.flags),'invalid_log2_config')
        need(all(isinstance(p,Path) and p.is_absolute() and '..' not in p.parts
            for p in (c.source,c.selected,c.script,c.staging_root)),'invalid_log2_config')
        need(len({c.source,c.selected,c.script})==3 and all(not p.is_relative_to(c.staging_root)
            for p in (c.source,c.selected,c.script)),'invalid_log2_config')
        need(_valid_pin(c.source_pin,legacy.MAX_INPUT) and _valid_pin(c.selected_pin,legacy.MAX_SELECTED)
            and _valid_pin(c.parameters_pin,4096),'invalid_log2_config')
        params=contract.document(c.parameters_json,4096);profile.validate_parameters(params)
        need(params['filter_flags']==dict(zip(('reverse','contaminant','site_only'),c.flags))
            and _pin(c.parameters_json)==c.parameters_pin and contract.canonical(params)==c.parameters_json,'parameters_pin_mismatch')
        parent=contract.document(c.parent_metadata_json,32768);contract.version_value(parent)
        profile.parent_metadata(parent,parent['source'],params,c.family,parent['created_by'])
        need(parent['source']['source_bytes']==c.source_pin.bytes and parent['source']['source_sha256']==c.source_pin.sha256,'selected_binding_mismatch')
        names={'parent-'+n for n in contract.ARTIFACT_NAMES}|{'parent-provenance.R','request-parameters.json','median_core.R'}
        need(type(c.extras) is tuple and len(c.extras)==len(names),'invalid_log2_config')
        need({x[0] for x in c.extras}==names,'invalid_log2_config')
        for name,path,pin,private in c.extras:
            need(isinstance(path,Path) and path.is_absolute() and '..' not in path.parts
                and not path.is_relative_to(c.staging_root) and _valid_pin(pin,legacy.MAX_FILE)
                and type(private) is bool,'invalid_log2_config')
            expected=profile.CORE_PIN if name=='median_core.R' else next((a for a in parent['artifacts'] if name=='parent-'+a['name']),None)
            if name=='request-parameters.json':expected={'bytes':c.parameters_pin.bytes,'sha256':c.parameters_pin.sha256}
            if name=='parent-provenance.R':
                from .median_processed_io import provenance_bytes
                body=provenance_bytes(parent);expected={'bytes':len(body),'sha256':contract.bytes_sha(body)}
            if expected is not None:need(pin.bytes==expected['bytes'] and pin.sha256==expected['sha256'],'log2_file_pin_mismatch')
        return c

    def _outputs(self):return validator
    def _invocation(self,b,job):
        return [str(b.NATIVE_EXECUTABLE),'--vanilla','--slave','--file='+str(job/'script.R'),'--args',str(job)]
    def _failure_binding(self):
        c=self._config
        return {'job_id':None if c is None else c.job_id,'attempt_id':None if c is None else c.attempt_id,
            'configuration_sha256':None if c is None else configuration_binding(c)}
    def _validate_outputs(self,content,outputs,check):
        c=self._config;parent=contract.document(c.parent_metadata_json,32768)
        bodies={name[7:]:content[path] for name,path,_,_ in c.extras if name.startswith('parent-') and name!='parent-provenance.R'}
        return validator.validate_bundle(content[c.source],content[c.selected],c.family,c.flags,outputs,check=check,
            parent=parent,parent_outputs=bodies,parameters=contract.document(c.parameters_json,4096),owner=parent['created_by'])

    def _run_native(self, *, cancelled=lambda:False):
        need(not self._quarantined, 'log2_runner_quarantined')
        need(not self._active, 'log2_runner_busy')
        c=self._configuration()
        need(callable(cancelled), 'invalid_log2_cancellation')
        need(sys.platform=='linux' and sys.dont_write_bytecode, 'dedicated_linux_worker_required')
        b=_runtime()
        need(b.os.getuid()==b.os.geteuid()==1000 and b.threading.current_thread() is b.threading.main_thread()
             and b.threading.active_count()==1, 'dedicated_linux_worker_required')
        need(b.signal.getsignal(b.signal.SIGCHLD)==b.signal.SIG_DFL
             and b.signal.getitimer(b.signal.ITIMER_REAL)==(0.0,0.0), 'dedicated_signal_context_required')
        need(hasattr(b.os,'pidfd_open') and hasattr(b.signal,'pidfd_send_signal'), 'linux_pidfd_required')
        limits=b.Limits(); deadline=time.monotonic()+limits.wall_seconds
        self._active=True; old_handler=b.signal.getsignal(b.signal.SIGALRM); result=None; failure=None
        def check():
            value=cancelled()
            need(type(value) is bool, 'invalid_log2_cancellation')
            need(not value, 'log2_cancelled')
            need(time.monotonic()<deadline, 'log2_timeout')
        def expired(*_):
            raise Log2RunFailure('log2_timeout')
        try:
            if self._failure_trace is not None:
                self._failure_trace['handler_pending']=True
                self._failure_trace['timer_pending']=True
            b.signal.signal(b.signal.SIGALRM,expired);b.signal.setitimer(b.signal.ITIMER_REAL,limits.wall_seconds)
            check();b.validate_native_directory_pins(c.native_directory_pins)
            self._parents(b,c.staging_root)
            root_info=c.staging_root.lstat()
            need(b.stat.S_ISDIR(root_info.st_mode) and root_info.st_uid==1000 and not root_info.st_mode&0o077,
                 'uncontrolled_log2_staging_root')
            fixed = ((c.source,c.source_pin,legacy.MAX_INPUT),(c.selected,c.selected_pin,legacy.MAX_SELECTED),
                     (c.script,Pin(**profile.PINS['script']),profile.PINS['script']['bytes']),
                     (Path(validator.__file__),Pin(**profile.PINS['validator']),profile.PINS['validator']['bytes']),
                     (b.NATIVE_EXECUTABLE,Pin(14488,b.NATIVE_EXECUTABLE_SHA256),14488))
            fixed += tuple((path,pin,legacy.MAX_FILE) for _,path,pin,_ in c.extras)
            private_paths={c.source,c.selected}|{path for _,path,_,private in c.extras if private}
            native_info=b.NATIVE_EXECUTABLE.lstat()
            need(b.stat.S_ISREG(native_info.st_mode) and native_info.st_uid==0
                 and b.stat.S_IMODE(native_info.st_mode)==0o755, 'unsafe_log2_native_executable')
            content={}; identities={}
            for path,expected,cap in fixed:
                content[path],identities[path]=self._read(b,path,cap,check,expected,
                                                         private=path in private_paths)
            need(b.os.access(b.NATIVE_EXECUTABLE,b.os.X_OK), 'native_log2_not_executable')
            job=c.staging_root/(c.job_id+'.partial')
            need(not b.os.path.lexists(job), 'log2_job_already_exists')
            job.mkdir(mode=0o700)
            staged={name:self._write_snapshot(b,job/name,content[path],check)
                    for name,path in (('input.tsv',c.source),('selected.tsv',c.selected),('script.R',c.script))}
            for name,path,_,_ in c.extras:
                staged[name]=self._write_snapshot(b,job/name,content[path],check)
            self._scan(b,job,staged,check)
            # Validate the complete original None parent before the native child.
            parent=contract.document(c.parent_metadata_json,32768)
            parent_bodies={name[7:]:content[path] for name,path,_,_ in c.extras if name.startswith('parent-') and name!='parent-provenance.R'}
            checked=legacy.validate(content[c.source],content[c.selected],c.family,c.flags,parent_bodies,check=check)
            need(checked.manifest_sha256==parent['manifest_sha256'],'selected_binding_mismatch')
            rc,stdout,stderr,process=self._spawn_and_wait(b,job,staged,check,deadline,limits)
            need(stderr==b'', 'log2_executor_stderr')
            need(rc==0 and stdout==b'MEDIAN_PROCESSED_COMPLETE\n', 'log2_executor_failed')
            outputs,output_ids=self._collect(b,job,staged,check,validator.OUTPUTS)
            bundle=self._validate_outputs(content,outputs,check)
            # Full original/fixed bytes and output identities again after pure
            # verification. The bundle itself owns detached immutable bytes.
            for path,expected,cap in fixed:
                _,current=self._read(b,path,cap,check,expected,private=path in private_paths)
                need(current==identities[path], 'log2_fixed_identity_changed')
            b.validate_native_directory_pins(c.native_directory_pins)
            current_root=c.staging_root.lstat()
            # mkdir legitimately changes directory mtime/ctime/nlink; anchor its
            # inode/device/owner/mode, not child-count timestamps.
            need((current_root.st_dev,current_root.st_ino,current_root.st_uid,current_root.st_mode)==
                 (root_info.st_dev,root_info.st_ino,root_info.st_uid,root_info.st_mode), 'log2_staging_root_changed')
            self._scan(b,job,staged,check,validator.OUTPUTS)
            for name,identity in output_ids.items():
                need(b.identity((job/'output'/name).lstat())==identity, 'log2_output_changed')
            check()
            receipt={'schemaVersion':1,'job_id':c.job_id,'family':c.family,'flags':list(c.flags),
                'attempt_id':c.attempt_id,'configuration_sha256':configuration_binding(c),
                'median_parent':contract.document(c.parameters_json,4096)['normalization_parent'],'median_core':dict(profile.CORE_PIN),
                'source':{'bytes':c.source_pin.bytes,'sha256':bundle.source_sha256},
                'selected':{'bytes':c.selected_pin.bytes,'sha256':bundle.selected_sha256},
                'parameters':{'bytes':c.parameters_pin.bytes,'sha256':bundle.parameters_sha256},
                'script':dict(profile.PINS['script']),
                'validator':dict(profile.PINS['validator']),
                'native':{'bytes':14488,'sha256':b.NATIVE_EXECUTABLE_SHA256},
                'limits':b.asdict(limits),'limits_configuration_reused_from_raw_qc':True,
                'new_execution_profile_registered':False,'process_policy_sha256':b.process_policy.POLICY_SHA256,
                'process_control':process,'one_native_child':True,'exit_code':0,
                'stdout_sha256':hashlib.sha256(stdout).hexdigest(),'stderr_bytes':0,
                'manifest_sha256':bundle.manifest_sha256,'all_11_outputs_verified':True,
                'close_confirmed':True,'published':False,'Owner_or_API_or_DB_authorized':False,
                'analysis_ready':False}
            result=RunResult(bundle,_canonical(receipt))
        except BaseException as error:
            self._quarantined=True
            code=error.code if isinstance(error,Log2RunFailure) else (
                'log2_output_invalid' if isinstance(error,validator.ValidationFailure) else 'log2_run_unconfirmed')
            failure=Log2RunFailure(code)
        finally:
            try:
                b.signal.setitimer(b.signal.ITIMER_REAL,0)
                if self._failure_trace is not None:self._failure_trace['timer_pending']=False
            except BaseException:
                if self._failure_trace is not None:self._failure_trace['timer_disable_unknown']=True
                failure=Log2RunFailure('log2_cleanup_unconfirmed');self._quarantined=True
            try:
                b.signal.signal(b.signal.SIGALRM,old_handler)
                if self._failure_trace is not None:self._failure_trace['handler_pending']=False
            except BaseException:
                if self._failure_trace is not None:self._failure_trace['handler_restore_unknown']=True
                failure=Log2RunFailure('log2_cleanup_unconfirmed');self._quarantined=True
            self._active=False
        if failure is not None:
            raise failure from None
        need(result is not None, 'log2_run_unconfirmed')
        return result
