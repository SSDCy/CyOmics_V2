"""Fixed original volcano.R adapter; original process/failure supervisor only.

No Auth, database, installation, classification or publication. Root supplies a
known public-library/font profile, physical verifier and fresh staging root.
"""
from dataclasses import dataclass,field
from pathlib import Path
from uuid import UUID
import hashlib,json,sys,time
from . import log2_matrix_runner as shared
from .limma_bound_runner import LimmaBoundRunner
from .r_de_volcano import contract

SCRIPT=shared.Pin(13931,'5062b985000fb2cd44c93f8f0d760000bf671f3e0226ee70c773bf3b1f917fe2')
SUPERVISOR=shared.Pin(30207,'6f7839b4a867cea8fd9aef63b684ac8c9dbf63dbc7a95bb289080e8aa8074a3e')
OBSERVER=shared.Pin(19031,'14a330bd1c1c84879ed8f7ad90bb48debc72449e5475402b18db6b0411a6d7d5')
NATIVE=shared.Pin(14488,'9c572a708d444e0ec91c59935bf9b5c3fd426a75fe2b7101b2a65f4783155283')
VALIDATOR=shared.Pin(15561,'bae153a7c162c5ea0883df37ef20cf688a67d29792b033bc0906a08d053cb5ab')
INPUT_NAMES=('classification.tsv','classification-parameters.tsv','classification-audit.tsv')
PROFILE_KEYS={'contract','library_root','library_metadata','font','versions','adapter'}
KNOWN_METADATA=(('/usr/local/lib/R/site-library/ggplot2/DESCRIPTION',7209,'badda19848b3cc26b937cebeefc13fb4fc1470b255661fe635d7d8784aaeee99'),
    ('/usr/local/lib/R/site-library/digest/DESCRIPTION',7267,'0a9586e98f8fd853752f8cfb7220e98c8622288b15e342f8c174f35168f92901'))
KNOWN_FONT={'path':'/home/user01/.fonts/msyh.ttc','bytes':19704352,'sha256':'d79c55e68b1131eea0cc1c47be4f572d964f28c682e143db2ad09c1e4cb07a3f'}

def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def uuid(v):
    try:return type(v) is str and str(UUID(v))==v
    except (ValueError,TypeError):return False
def profile(raw):
    from .validation import strict_json
    shared.need(type(raw) is bytes and 0<len(raw)<=16384,'invalid_volcano_profile')
    v=strict_json(raw)
    shared.need(type(v) is dict and set(v)==PROFILE_KEYS and canonical(v)==raw
        and v['contract']=='r_volcano_profile_v0.1' and v['library_root']=='/usr/local/lib/R/site-library'
        and v['library_metadata']==[dict(path=p,bytes=n,sha256=s) for p,n,s in KNOWN_METADATA]
        and v['font']==KNOWN_FONT and v['versions']=={'R':'4.6.1','ggplot2':'4.0.3','digest':'0.6.39'}
        and type(v['adapter']) is dict and set(v['adapter'])=={'bytes','sha256'}
        and shared._valid_pin(shared.Pin(**v['adapter']),262144),'invalid_volcano_profile')
    return v

@dataclass(frozen=True,slots=True)
class Config:
    enabled:bool=False
    render_id:str|None=None
    attempt_id:str|None=None
    binding_sha256:str|None=None
    staging_root:Path|None=field(default=None,repr=False)
    profile_json:bytes|None=field(default=None,repr=False)
    native_directory_pins:tuple=field(default=(),repr=False)
    verify:object=field(default=None,repr=False)

@dataclass(frozen=True,slots=True)
class Output:
    files:dict=field(repr=False)
    receipt:dict=field(repr=False)

class _OutputLimits:
    OUTPUTS=contract.OUTPUTS
    MAX_FILE=27262976
    MAX_TOTAL=134217728

class VolcanoRunner(shared.Log2MatrixRunner):
    def __init__(self,config=None):
        shared.need(config is None or type(config) is Config,'invalid_volcano_config')
        super().__init__();self._config=config;self._input=None;self._used=False
    def run(self,inputs=None,*,cancelled=lambda:False):
        if self._config is None or self._config.enabled is False:return None
        shared.need(not self._used,'volcano_attempt_consumed');self._used=True;self._input=inputs
        return super().run(cancelled=cancelled)
    def _configuration(self):
        c=self._config
        shared.need(type(c) is Config and c.enabled is True and uuid(c.render_id) and uuid(c.attempt_id)
            and type(c.binding_sha256) is str and len(c.binding_sha256)==64
            and all(x in '0123456789abcdef' for x in c.binding_sha256)
            and isinstance(c.staging_root,Path) and c.staging_root.is_absolute() and '..' not in c.staging_root.parts
            and type(c.native_directory_pins) is tuple and len(c.native_directory_pins)==5 and callable(c.verify)
            and c.verify() is None,'invalid_volcano_config')
        profile(c.profile_json)
        shared.need(type(self._input) is dict and set(self._input)==set(INPUT_NAMES),'invalid_volcano_inputs')
        self._validated=contract.validate_inputs(*(self._input[n] for n in INPUT_NAMES))
        return c
    def _failure_binding(self):
        c=self._config
        return {'job_id':getattr(c,'render_id',None),'attempt_id':getattr(c,'attempt_id',None),
            'configuration_sha256':None if c is None else sha(canonical({'binding_sha256':c.binding_sha256,'profile_sha256':sha(c.profile_json)}))}
    def _job_directories(self):return frozenset(('output','tmp'))
    def _outputs(self):return _OutputLimits
    def _invocation(self,b,job):
        return [str(b.NATIVE_EXECUTABLE),'--vanilla','--slave','--file='+str(job/'script.R'),'--args',
            *(str(job/n) for n in INPUT_NAMES),str(job/'output')]
    def _environment(self,b,job):
        library=profile(self._config.profile_json)['library_root']
        return dict(super()._environment(b,job),R_LIBS=library,R_LIBS_SITE=library,R_LIBS_USER=library,
            FONTCONFIG_FILE=str(job/'fonts.conf'),XDG_CACHE_HOME=str(job/'tmp'),TMPDIR=str(job/'tmp'))
    _temp_scan=LimmaBoundRunner._temp_scan
    def _scan(self,b,job,staged,check,expected=None):
        total=super()._scan(b,job,staged,check,expected)+self._temp_scan(b,job/'tmp',check)
        shared.need(total<=134217728,'volcano_total_limit');return total
    def _run_native(self,*,cancelled=lambda:False):
        c=self._configuration();p=profile(c.profile_json);b=shared._runtime()
        shared.need(sys.platform=='linux' and sys.dont_write_bytecode and callable(cancelled)
            and b.os.getuid()==b.os.geteuid()==1000 and b.threading.current_thread() is b.threading.main_thread()
            and b.threading.active_count()==1 and b.signal.getsignal(b.signal.SIGCHLD)==b.signal.SIG_DFL
            and b.signal.getitimer(b.signal.ITIMER_REAL)==(0.0,0.0),'dedicated_volcano_context_required')
        limits=b.Limits();deadline=time.monotonic()+limits.wall_seconds
        self._active=True;old=b.signal.getsignal(b.signal.SIGALRM);result=None;failure=None
        def check():
            shared.need(c.verify() is None and cancelled() is False and time.monotonic()<deadline,'volcano_execution_unconfirmed')
        def expired(*_):raise shared.Log2RunFailure('volcano_timeout')
        try:
            trace=self._failure_trace
            if trace is not None:trace['handler_pending']=True;trace['timer_pending']=True
            b.signal.signal(b.signal.SIGALRM,expired);b.signal.setitimer(b.signal.ITIMER_REAL,limits.wall_seconds)
            check();b.validate_native_directory_pins(c.native_directory_pins);root=c.staging_root;self._parents(b,root);before=root.lstat()
            shared.need(b.stat.S_ISDIR(before.st_mode) and before.st_uid==1000 and b.stat.S_IMODE(before.st_mode)==0o700,'unsafe_volcano_staging')
            script=Path(__file__).parent/'r_de_volcano/volcano.R'
            fixed=[(script,SCRIPT),(Path(contract.__file__),VALIDATOR),(Path(shared.__file__),SUPERVISOR),
                (Path(sys.modules[LimmaBoundRunner.__module__].__file__),OBSERVER),(b.NATIVE_EXECUTABLE,NATIVE),
                (Path(__file__),shared.Pin(**p['adapter']))]
            fixed += [(Path(x['path']),shared.Pin(x['bytes'],x['sha256'])) for x in [*p['library_metadata'],p['font']]]
            native=b.NATIVE_EXECUTABLE.lstat()
            shared.need(b.NATIVE_EXECUTABLE_SHA256==NATIVE.sha256 and native.st_uid==0 and b.stat.S_IMODE(native.st_mode)==0o755,'unsafe_volcano_native')
            contents={};identities={}
            for path,pin in fixed:contents[path],identities[path]=self._read(b,path,pin.bytes,check,pin)
            job=root/(c.render_id+'.'+c.attempt_id+'.partial');shared.need(not b.os.path.lexists(job),'volcano_attempt_consumed')
            job.mkdir(mode=0o700);(job/'output').mkdir(mode=0o700);(job/'tmp').mkdir(mode=0o700)
            fontconfig=('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig><dir>/home/user01/.fonts</dir>'
                '<cachedir>'+str(job/'tmp/font-cache')+'</cachedir><alias><family>sans-serif</family><prefer><family>Microsoft YaHei</family></prefer></alias></fontconfig>').encode()
            files={**self._input,'script.R':contents[script],'fonts.conf':fontconfig}
            staged={n:self._write_snapshot(b,job/n,raw,check) for n,raw in files.items()}
            self._scan(b,job,staged,check);rc,out,err,process=self._spawn_and_wait(b,job,staged,check,deadline,limits)
            shared.need(rc==0 and out==b'R_VOLCANO_COMPLETE\n' and err==b'','volcano_executor_failed')
            outputs,output_ids=self._collect(b,job,staged,check,contract.OUTPUTS)
            shared.need(all(len(raw)<=27262976 for raw in outputs.values()),'volcano_output_limit')
            summary=contract.validate_outputs(self._validated,outputs)
            versions=contract._versions(outputs['R-session.txt'])
            shared.need(versions[0].startswith('R version '+p['versions']['R']) and versions[1:]==[p['versions']['ggplot2'],p['versions']['digest']],'volcano_runtime_versions_mismatch')
            shared.need(process.get('owned_tasks',{}).get('cleanup',{}).get('complete_for_observed_identities') is True
                and process.get('owned_tasks',{}).get('uncertainty')==[],'volcano_cleanup_unconfirmed')
            for path,pin in fixed:
                _,current=self._read(b,path,pin.bytes,check,pin);shared.need(current==identities[path],'volcano_fixed_identity_changed')
            b.validate_native_directory_pins(c.native_directory_pins);after=root.lstat()
            shared.need((before.st_dev,before.st_ino,before.st_mode,before.st_uid)==(after.st_dev,after.st_ino,after.st_mode,after.st_uid),'volcano_staging_changed')
            self._scan(b,job,staged,check,contract.OUTPUTS)
            for n,ident in output_ids.items():shared.need(b.identity((job/'output'/n).lstat())==ident,'volcano_output_changed')
            result=Output(outputs,{'contract':'r_volcano_native_receipt_v0.1','render_id':c.render_id,'attempt_id':c.attempt_id,
                'binding_sha256':c.binding_sha256,'profile_sha256':sha(c.profile_json),'script':{'bytes':SCRIPT.bytes,'sha256':SCRIPT.sha256},
                'environment_sha256':sha(canonical(self._environment(b,job))),'limits':b.asdict(limits),'process':process,
                'all_five_outputs_verified':True,'statistical_recomputation':False,'summary':summary})
        except BaseException as error:
            failure=shared.Log2RunFailure(error.code if isinstance(error,shared.Log2RunFailure) else 'volcano_execution_unconfirmed')
        finally:
            try:
                b.signal.setitimer(b.signal.ITIMER_REAL,0)
                if self._failure_trace is not None:self._failure_trace['timer_pending']=False
            except BaseException:
                if self._failure_trace is not None:self._failure_trace['timer_disable_unknown']=True
                failure=shared.Log2RunFailure('volcano_cleanup_unconfirmed')
            try:
                b.signal.signal(b.signal.SIGALRM,old)
                if self._failure_trace is not None:self._failure_trace['handler_pending']=False
            except BaseException:
                if self._failure_trace is not None:self._failure_trace['handler_restore_unknown']=True
                failure=shared.Log2RunFailure('volcano_cleanup_unconfirmed')
            self._active=False
        if failure is not None:raise failure from None
        shared.need(type(result) is Output,'volcano_execution_unconfirmed');return result
