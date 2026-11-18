"""Default-off bounded native adapter; source proposal, not runtime approval.

Root's existing reviewed control must authorize the dedicated process and inject
its check callback/native pins. SavedInputAssembly's caller handles Owner Auth.
Inherits the original log2 supervisor/OwnedTasks/cleanup; no new supervisor.
"""
from dataclasses import dataclass,field
from pathlib import Path
import hashlib,json,re,sys,time
from uuid import UUID
from . import log2_matrix_runner as shared,limma_bound_contract as contract

PROJECT=Path('/home/user01/Cy_Omics_v2.0')
LIBRARY=PROJECT/'audit/R-LIMMA-ENV-003/staged-library'
SCRIPT_PIN=shared.Pin(12103,'9807467ba2ae54efd5ef77e5c15dbe0525d205f4d2988ba24995ea932024c908')
# Filled from the already reviewed static manifest, never by package discovery.
LIBRARY_FILES={'limma/CITATION': {'bytes': 791, 'sha256': '9c4d916b7c7a7703e2c61420635e9016badd2f6b37f0f3132cce76c5acd45b54'}, 'limma/DESCRIPTION': {'bytes': 4634, 'sha256': '45740bc22236fac00d7ca1de8428d512a6a8a8bd033a0d02f6b2adc0fdea640b'}, 'limma/INDEX': {'bytes': 12636, 'sha256': '8e2463a8a9b8708f0f5d354a152307c9db1422e18c774b1bb9365e13d3f48532'}, 'limma/Meta/Rd.rds': {'bytes': 8074, 'sha256': '60b72fa91f5aaa7165a59d1b0ae37dd768329625b54320422db2a6c9bfcb1180'}, 'limma/Meta/features.rds': {'bytes': 123, 'sha256': 'b4f074bc0f45cfe56d976c0d67a1453067f4b34693828ed02d674a6641b3e332'}, 'limma/Meta/hsearch.rds': {'bytes': 7649, 'sha256': 'b159d0ef38fa40686c68422692e24de973d0272707e7e20d730b12155e17d957'}, 'limma/Meta/links.rds': {'bytes': 3750, 'sha256': '14da8250a6190ea5f17ba1f6d34ad59c34b5e440e338a783d3e6e8f6a6796bc0'}, 'limma/Meta/nsInfo.rds': {'bytes': 1308, 'sha256': '3758cf93936197939d22e870eb241bdca4c2e3530277d0200fb6b10b8fbdfc01'}, 'limma/Meta/package.rds': {'bytes': 1891, 'sha256': 'aca1ac303fe21e2cc83e27e03891fd2ecbac749a162d7401f8c68c242472173d'}, 'limma/Meta/vignette.rds': {'bytes': 209, 'sha256': '19ae3b722005aefd160cda2e653c7a08f63e2668d018a106bb9e6eb55f145f89'}, 'limma/NAMESPACE': {'bytes': 4808, 'sha256': '7aec816f42ef3d70cc4a0709fe4e076662e2f1a5583040be73f525d1b0830973'}, 'limma/NEWS.Rd': {'bytes': 77763, 'sha256': 'f73df67a6c38180a3f15a8f7f1318a42b0fe1b7910fd2cfa0a44c8647c31ed15'}, 'limma/R/limma': {'bytes': 1058, 'sha256': '570ca456b280cdeb201ef5ebdf22dc8f80092e2c0c68e33c7f73340e420f3759'}, 'limma/R/limma.rdb': {'bytes': 962424, 'sha256': 'aee82cbc8cd05936fb76f6fc2ca3c31a598fbffd12a2207fcf53e87ea46c91a9'}, 'limma/R/limma.rdx': {'bytes': 5536, 'sha256': 'c5a366dba6dfe16b028d1a085804123e0e1e7d007942e21ea0a93b4b77f9d00b'}, 'limma/doc/changelog.txt': {'bytes': 199787, 'sha256': '39d8d75922ecc737e7583510115afe03d3906d7c236fdb8a45877c11185a9fcf'}, 'limma/doc/index.html': {'bytes': 1588, 'sha256': 'bd505b1435f9fba2d310c5c8130273ed8fa89fad63406c8b7156fb20f6e411d6'}, 'limma/doc/usersguide.Rnw': {'bytes': 202, 'sha256': '66a38015785ade2aed8bbd52fdd92e72c9ef44ee532459ce8a9bbf12ddb1315d'}, 'limma/libs/limma.so': {'bytes': 54408, 'sha256': 'b2fbbe74a3ed5dda833081298c2256581259ae6bb0bdbac383799ce67ee5c38a'}, 'statmod/CITATION': {'bytes': 3531, 'sha256': 'effadab1ee1a2125880eb1981b7f5da149b8f3539ac3df16dfb78796569c60c1'}, 'statmod/DESCRIPTION': {'bytes': 1781, 'sha256': '78df2466dbb91e25379a08c3b648c9b7942313f1d8d2a911fe410a623cee2f7c'}, 'statmod/INDEX': {'bytes': 2148, 'sha256': '80fe7744e002db35b88b42dad78a1a938f62aec6b39853cea62e3128e2d04fa8'}, 'statmod/Meta/Rd.rds': {'bytes': 1713, 'sha256': '41ece762cd036409d6964f6dd209861c56dc54ca0ca9600cb5020d708f21e96a'}, 'statmod/Meta/data.rds': {'bytes': 117, 'sha256': 'd507e7540c3bad6c4b60ae96d86c1bb1f783299d924e6fa324a1d3ed7d352e24'}, 'statmod/Meta/features.rds': {'bytes': 123, 'sha256': 'b4f074bc0f45cfe56d976c0d67a1453067f4b34693828ed02d674a6641b3e332'}, 'statmod/Meta/hsearch.rds': {'bytes': 1681, 'sha256': '8920759769437ffb69cd2a0e63131dd96bbfd483b0d3c61c00a67e93c4babf18'}, 'statmod/Meta/links.rds': {'bytes': 714, 'sha256': 'ff73e719d037196ae6bc2095c0fed125a556b7a82fc6528e329db86cd6584058'}, 'statmod/Meta/nsInfo.rds': {'bytes': 899, 'sha256': '5743579be5bc6567538ca6a8d1460e1fd35b17804070e6ac4b9a6da0022df448'}, 'statmod/Meta/package.rds': {'bytes': 1176, 'sha256': 'c5a5dba8e8061a1b4bb84f795135cd0e2d7ab1566fa84b62f6954cb17536eca4'}, 'statmod/NAMESPACE': {'bytes': 1495, 'sha256': '167189bb507181955d95ca92c3b2b8a0b903bd8ca4ffbf7662129f1f77b6b1e6'}, 'statmod/NEWS': {'bytes': 18355, 'sha256': '9a89e1969025cdf6075a95a1bd4df6f8fefe49698a59890192b23267d2e83e5d'}, 'statmod/R/statmod': {'bytes': 1058, 'sha256': '570ca456b280cdeb201ef5ebdf22dc8f80092e2c0c68e33c7f73340e420f3759'}, 'statmod/R/statmod.rdb': {'bytes': 151155, 'sha256': '1d3b053977a9db27dc734233925531ada99b1061f0f06e7b199c0772ab5e2b81'}, 'statmod/R/statmod.rdx': {'bytes': 1070, 'sha256': '8d5ecaf6c99f2796ffc331ed4b21fdfd16238baa4c67595624895abe1f8d5c51'}, 'statmod/data/welding.rdata': {'bytes': 1252, 'sha256': '206af06f9a6b9c8ce3ffc42b15b09e4eca8048dcbae65989aeb90d4e27b507b6'}, 'statmod/libs/statmod.so': {'bytes': 79216, 'sha256': '2ce4e42cd18e2981207ba4f8fd8e885bed1b273875c97075dcf2323618ef6998'}}
CONTRACT_PIN=shared.Pin(10301,'3534ef4ddf70bf1830abed33ccf05ad8273941343edf225f44006872d44a4047')
SHARED_PIN=shared.Pin(30207,'6f7839b4a867cea8fd9aef63b684ac8c9dbf63dbc7a95bb289080e8aa8074a3e')

@dataclass(frozen=True,slots=True)
class Config:
    enabled:bool=False # Trusted server construction; never an Auth assertion.
    epoch_id:str|None=None
    job_id:str|None=None
    attempt_id:str|None=None
    native_directory_pins:tuple=field(default=(),repr=False)
    verify:object=field(default=None,repr=False) # Existing Root control check.

@dataclass(frozen=True,slots=True)
class RunResult:
    bundle:contract.Bundle=field(repr=False)
    _receipt:bytes=field(repr=False)
    @property
    def receipt(self):return json.loads(self._receipt)

def uuid(value):
    try:return type(value) is str and str(UUID(value))==value and UUID(value).int!=0
    except ValueError:return False

def validate_exit(exit_code,stdout,stderr):
    shared.need(type(exit_code) is int and exit_code in (0,3,4),'limma_executor_failed')
    shared.need(type(stdout) is bytes and type(stderr) is bytes and stdout==stderr==b'',
        'limma_executor_diagnostic')

class LimmaBoundRunner(shared.Log2MatrixRunner):
    def __init__(self,config=None):
        shared.need(config is None or type(config) is Config,'invalid_limma_config')
        super().__init__()
        self._config=config;self._bound=None;self._used=False

    def run(self,bound=None,*,cancelled=lambda:False):
        if self._config is None or (type(self._config.enabled) is bool and not self._config.enabled):return None
        shared.need(not self._active,'log2_runner_busy')
        shared.need(not self._quarantined,'log2_runner_quarantined')
        shared.need(not self._used,'limma_attempt_already_consumed')
        self._bound=bound;self._used=True # One attempt; never retry/delete/latch reset.
        return super().run(cancelled=cancelled)

    def _configuration(self):
        c=self._config
        shared.need(type(c) is Config and type(c.enabled) is bool and c.enabled
            and all(uuid(v) for v in (c.epoch_id,c.job_id,c.attempt_id))
            and type(c.native_directory_pins) is tuple and len(c.native_directory_pins)==5
            and callable(c.verify),'invalid_limma_config')
        try:contract.inputs(self._bound)
        except contract.InvalidOutput as error:raise shared.Log2RunFailure(error.code) from None
        return c

    def _failure_binding(self):
        c=self._config;b=self._bound
        try:
            pins={name:contract.pin(getattr(b,field)) for name,field in
                (('matrix','matrix_tsv'),('design','design_tsv'),('preset','preset_tsv'))} if type(b) is contract.BoundInput else None
        except (TypeError,ValueError,AttributeError):pins=None
        value={'epoch_id':c.epoch_id,'job_id':c.job_id,'attempt_id':c.attempt_id,'inputs':pins}
        return {'job_id':c.job_id,'attempt_id':c.attempt_id,
            'configuration_sha256':hashlib.sha256(contract.canonical(value)).hexdigest()}

    def _outputs(self):return contract
    def _job_directories(self):return frozenset(('output','tmp'))
    def _invocation(self,b,job):
        return [str(b.NATIVE_EXECUTABLE),'--vanilla','--slave','--file='+str(job/'script.R'),'--args',
            str(job/'matrix.tsv'),str(job/'design.tsv'),str(job/'preset.tsv'),str(job/'output')]

    def _environment(self,b,job):
        # Per-child overlay; never mutate original ENV/NATIVE_ENV globals.
        return dict(super()._environment(b,job),R_LIBS=str(LIBRARY),R_LIBS_SITE=str(LIBRARY),
            R_LIBS_USER=str(LIBRARY),R_ENVIRON='/dev/null',R_ENVIRON_USER='/dev/null',
            R_PROFILE='/dev/null',R_PROFILE_USER='/dev/null',TMPDIR=str(job/'tmp'))

    def _temp_scan(self,b,path,check):
        total=0;count=0
        def walk(directory,depth):
            nonlocal total,count
            check();shared.need(depth<=8,'limma_temp_limit')
            s=directory.lstat();shared.need(b.stat.S_ISDIR(s.st_mode) and s.st_uid==1000 and not s.st_mode&0o077,'limma_temp_unsafe')
            with b.os.scandir(directory) as entries:
                for entry in entries:
                    check();count+=1;shared.need(count<=256,'limma_temp_limit')
                    p=directory/entry.name;s=p.lstat()
                    if b.stat.S_ISDIR(s.st_mode):walk(p,depth+1)
                    else:
                        shared.need(b.stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_uid==1000
                            and not s.st_mode&0o077 and s.st_size<=contract.MAX_FILE,'limma_temp_unsafe')
                        total+=max(s.st_size,getattr(s,'st_blocks',0)*512)
                        shared.need(total<=contract.MAX_TOTAL,'limma_temp_limit')
        if self._failure_trace is not None:self._failure_trace['scan_unconfirmed']=True
        walk(path,0)
        if self._failure_trace is not None:self._failure_trace['scan_unconfirmed']=False
        return total

    def _scan(self,b,job,staged,check,expected=None):
        total=super()._scan(b,job,staged,check,expected)+self._temp_scan(b,job/'tmp',check)
        shared.need(total<=contract.MAX_TOTAL,'limma_output_total_limit');return total

    def _library(self,b,check):
        self._parents(b,LIBRARY)
        root=LIBRARY.lstat();shared.need(b.stat.S_ISDIR(root.st_mode) and root.st_uid==1000
            and b.stat.S_IMODE(root.st_mode)==0o700,'limma_library_unsafe')
        directories={LIBRARY}
        for name in LIBRARY_FILES:
            directories.update((LIBRARY/name).parents[i] for i in range(len(Path(name).parts)-1))
        identities={};seen=set()
        def walk(directory):
            check();s=directory.lstat()
            shared.need(directory in directories and b.stat.S_ISDIR(s.st_mode) and s.st_uid==1000
                and not s.st_mode&0o022,'limma_library_unsafe')
            identities[str(directory)]=b.identity(s)
            with b.os.scandir(directory) as entries:
                for entry in entries:
                    check();p=directory/entry.name;s=p.lstat()
                    if b.stat.S_ISDIR(s.st_mode):walk(p)
                    else:
                        name=p.relative_to(LIBRARY).as_posix()
                        shared.need(name in LIBRARY_FILES and name not in seen and s.st_uid==1000,'limma_library_changed')
                        seen.add(name);pin=LIBRARY_FILES[name]
                        _,identities[str(p)]=self._read(b,p,2*1024*1024,check,shared.Pin(pin['bytes'],pin['sha256']))
            shared.need(b.identity(directory.lstat())==identities[str(directory)],'limma_library_changed')
        if self._failure_trace is not None:self._failure_trace['scan_unconfirmed']=True
        walk(LIBRARY)
        shared.need(seen==set(LIBRARY_FILES) and len(seen)==36,'limma_library_changed')
        if self._failure_trace is not None:self._failure_trace['scan_unconfirmed']=False
        return identities

    def _run_native(self,*,cancelled=lambda:False):
        c=self._configuration()
        shared.need(callable(cancelled),'invalid_limma_cancellation')
        # Root check is caller-owned control, never a client boolean or approval
        # generated here. No actual execution is permitted by this source packet.
        shared.need(c.verify() is None,'limma_root_control_rejected')
        shared.need(sys.platform=='linux' and sys.dont_write_bytecode,'dedicated_linux_worker_required')
        b=shared._runtime()
        shared.need(b.os.getuid()==b.os.geteuid()==1000 and b.threading.current_thread() is b.threading.main_thread()
            and b.threading.active_count()==1,'dedicated_linux_worker_required')
        shared.need(b.signal.getsignal(b.signal.SIGCHLD)==b.signal.SIG_DFL and b.signal.getitimer(b.signal.ITIMER_REAL)==(0.0,0.0)
            and hasattr(b.os,'pidfd_open') and hasattr(b.signal,'pidfd_send_signal'),'dedicated_signal_context_required')
        limits=b.Limits();deadline=time.monotonic()+limits.wall_seconds
        self._active=True;old_handler=b.signal.getsignal(b.signal.SIGALRM);result=None;failure=None
        def check():
            shared.need(c.verify() is None,'limma_root_control_rejected')
            value=cancelled();shared.need(type(value) is bool,'invalid_limma_cancellation')
            shared.need(not value,'limma_cancelled');shared.need(time.monotonic()<deadline,'limma_timeout')
        def expired(*_):raise shared.Log2RunFailure('limma_timeout')
        try:
            trace=self._failure_trace
            if trace is not None:trace['handler_pending']=True;trace['timer_pending']=True
            b.signal.signal(b.signal.SIGALRM,expired);b.signal.setitimer(b.signal.ITIMER_REAL,limits.wall_seconds)
            check();b.validate_native_directory_pins(c.native_directory_pins)
            root=PROJECT/'runtime/limma-bound-input-001'/c.epoch_id/'staging'
            self._parents(b,root);root_info=root.lstat()
            shared.need(b.stat.S_ISDIR(root_info.st_mode) and root_info.st_uid==1000 and not root_info.st_mode&0o077,'limma_staging_unsafe')
            script=Path(__file__).parent/'r_limma_core/limma_core.R'
            fixed=((script,SCRIPT_PIN),(Path(contract.__file__),CONTRACT_PIN),(Path(shared.__file__),SHARED_PIN),
                (b.NATIVE_EXECUTABLE,shared.Pin(14488,b.NATIVE_EXECUTABLE_SHA256)))
            native=b.NATIVE_EXECUTABLE.lstat()
            shared.need(b.stat.S_ISREG(native.st_mode) and native.st_uid==0 and b.stat.S_IMODE(native.st_mode)==0o755
                and b.os.access(b.NATIVE_EXECUTABLE,b.os.X_OK),'unsafe_limma_native')
            content={};identities={}
            for path,pin in fixed:content[path],identities[path]=self._read(b,path,pin.bytes,check,pin)
            library_before=self._library(b,check)
            job=root/(c.job_id+'.'+c.attempt_id+'.partial')
            shared.need(not b.os.path.lexists(job),'limma_attempt_already_exists');job.mkdir(mode=0o700)
            (job/'output').mkdir(mode=0o700);(job/'tmp').mkdir(mode=0o700)
            staged={name:self._write_snapshot(b,job/name,data,check) for name,data in (
                ('matrix.tsv',self._bound.matrix_tsv),('design.tsv',self._bound.design_tsv),
                ('preset.tsv',self._bound.preset_tsv),('script.R',content[script]))}
            self._scan(b,job,staged,check)
            rc,stdout,stderr,process=self._spawn_and_wait(b,job,staged,check,deadline,limits)
            validate_exit(rc,stdout,stderr)
            outputs,output_ids=self._collect(b,job,staged,check,contract.OUTPUTS)
            bundle=contract.validate(self._bound,rc,outputs,check=check)
            for path,pin in fixed:
                _,current=self._read(b,path,pin.bytes,check,pin)
                shared.need(current==identities[path],'limma_fixed_identity_changed')
            shared.need(self._library(b,check)==library_before,'limma_library_changed')
            b.validate_native_directory_pins(c.native_directory_pins)
            after=root.lstat();shared.need((after.st_dev,after.st_ino,after.st_uid,after.st_mode)==
                (root_info.st_dev,root_info.st_ino,root_info.st_uid,root_info.st_mode),'limma_staging_changed')
            self._scan(b,job,staged,check,contract.OUTPUTS)
            for name,identity in output_ids.items():shared.need(b.identity((job/'output'/name).lstat())==identity,'limma_output_changed')
            check()
            receipt={'schemaVersion':1,**self._failure_binding(),'epoch_id':c.epoch_id,'exit_code':rc,'status':bundle.status,
                'warning_count':bundle.warning_count,'review_required':rc==4 or bundle.warning_count>0,
                'inputs':{name:contract.pin(data) for name,data in (('matrix',self._bound.matrix_tsv),('design',self._bound.design_tsv),('preset',self._bound.preset_tsv))},
                'script':{'bytes':SCRIPT_PIN.bytes,'sha256':SCRIPT_PIN.sha256},'packages':{'limma':'3.68.5','statmod':'1.5.2'},
                'library_manifest_sha256':hashlib.sha256(contract.canonical(LIBRARY_FILES)).hexdigest(),
                'native':{'bytes':14488,'sha256':b.NATIVE_EXECUTABLE_SHA256},'limits':b.asdict(limits),
                'resource_supervisor_reused':True,'process_policy_sha256':b.process_policy.POLICY_SHA256,'process_control':process,
                'environment_sha256':hashlib.sha256(contract.canonical(self._environment(b,job))).hexdigest(),
                'stdout_bytes':0,'stderr_bytes':0,'manifest_sha256':bundle.manifest_sha256,'all_five_outputs_verified':True,
                'close_confirmed':True,'published':False,'new_profile_runtime_approved':False,'Owner_Auth_asserted':False,
                'source_analysis_ready':self._bound.source_analysis_ready}
            result=RunResult(bundle,contract.canonical(receipt))
        except BaseException as error:
            self._quarantined=True
            failure=shared.Log2RunFailure(error.code if isinstance(error,(shared.Log2RunFailure,contract.InvalidOutput)) else 'limma_run_unconfirmed')
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
        shared.need(result is not None,'limma_run_unconfirmed');return result
