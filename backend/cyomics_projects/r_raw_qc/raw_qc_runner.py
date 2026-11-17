"""Linux/POSIX dedicated single-thread worker adapter, not an API or task queue.

Only trusted constructor configuration and caller-authorized input handles are
accepted. Aggregate output is actively monitored, not a filesystem hard quota.
"""
from contextlib import contextmanager, suppress
import ctypes
from dataclasses import asdict, dataclass
import errno
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import selectors
import signal
import stat
import subprocess
import threading
import time
from types import MappingProxyType
from uuid import UUID, uuid4
from .output_contract import OUTPUTS, ProtocolError, input_header, validate_outputs
from . import process_policy

APPROVED_SCRIPT_SHA256 = '2072a20e9dca53ab9ff03ef5a0a5d6aa9556dcee8004b5f28ce2fa4345624d64'
LIMITS_PROFILE_ID = 'raw_qc_limits_v3'
ENV = {'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
       'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1',
       'BLIS_NUM_THREADS':'1','VECLIB_MAXIMUM_THREADS':'1','NUMEXPR_NUM_THREADS':'1'}
NATIVE_EXECUTABLE = Path('/usr/lib/R/bin/exec/R')
NATIVE_EXECUTABLE_SHA256 = '9c572a708d444e0ec91c59935bf9b5c3fd426a75fe2b7101b2a65f4783155283'
NATIVE_ENV = MappingProxyType({'R_HOME':'/usr/lib/R','R_SHARE_DIR':'/usr/share/R/share',
              'R_INCLUDE_DIR':'/usr/share/R/include','R_DOC_DIR':'/usr/share/R/doc',
              'LD_LIBRARY_PATH':'/usr/lib/R/lib'})


class RunnerFailure(RuntimeError):
    def __init__(self, code, diagnostic_dir=None):
        self.code, self.diagnostic_dir = code, diagnostic_dir
        super().__init__(code)


def require(ok, code):
    if not ok: raise RunnerFailure(code)


def canonical_uuid(value):
    if type(value) is not str: return False
    try: return str(UUID(value)) == value
    except ValueError: return False


@dataclass(frozen=True, slots=True)
class Pin:
    bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class Limits:
    wall_seconds: float = 60
    cpu_seconds: int = 45
    address_space_bytes: int = 2147483648
    per_file_bytes: int = 67108864
    output_bytes: int = 268435456
    stdout_bytes: int = 65536
    stderr_bytes: int = 65536
    monitor_seconds: float = 0.05
    cleanup_seconds: float = 2
    open_files: int = 256
    uid_nproc_limit: int = 512
    core_bytes: int = 0
    job_tasks: int = 8
    uid_admission_tasks: int = 480


def limits_metadata(limits):
    """SHA256 of UTF-8 canonical JSON: sorted keys, compact, no NaN/newline."""
    value={'limits_profile':LIMITS_PROFILE_ID,'limits':asdict(limits)}
    encoded=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
    return {**value,'limits_sha256':hashlib.sha256(encoded).hexdigest()}


@dataclass(frozen=True, slots=True)
class DirectoryPin:
    env_key: str
    path: Path
    device: int
    inode: int
    owner: int
    mode: int


@dataclass(frozen=True, slots=True)
class Config:
    rscript: Path
    executable_pin: Pin
    script: Path
    script_pin: Pin
    input_root: Path
    staging_root: Path
    publish_root: Path
    limits: Limits = Limits()
    executor_label: str = 'registered_Rscript'
    expected_r_version: str = 'R version 4.6.1'
    entry_kind: str = 'rscript'
    native_directory_pins: tuple[DirectoryPin, ...] = ()


@dataclass(frozen=True, slots=True)
class InputVersion:
    dataset_version_id: str
    file_id: str
    bytes: int
    sha256: str
    row_count: int
    columns: tuple[str, ...]


class AuthorizedInput:
    """Created only by the server-side registered-key factory, not a wire DTO."""
    __slots__ = ('_token','_fd','_key','_version','_identity')
    def __init__(self, token, fd, key, version, identity):
        self._token, self._fd, self._key = token, fd, key
        self._version, self._identity = version, identity
    @property
    def version(self):
        return self._version
    def close(self):
        if self._fd is not None:
            os.close(self._fd); self._fd = None


@dataclass(frozen=True, slots=True)
class JobRequest:
    job_id: str
    quant_family: str
    input_handle: AuthorizedInput


@dataclass(frozen=True, slots=True)
class RunResult:
    published_dir: Path
    receipt: dict


def identity(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def pin_fd(fd, check=lambda:None):
    start = identity(os.fstat(fd)); digest = hashlib.sha256(); offset = 0
    while True:
        check(); chunk = os.pread(fd,65536,offset)
        if not chunk: break
        digest.update(chunk); offset += len(chunk)
    require(identity(os.fstat(fd)) == start and offset == start[4], 'file_changed_during_hash')
    return Pin(offset,digest.hexdigest())


def safe_absolute(path):
    require(type(path) is Path or isinstance(path,Path), 'invalid_server_path')
    require(path.is_absolute() and '..' not in path.parts and str(path) == os.path.normpath(str(path)), 'invalid_server_path')
    require(not any(p.is_symlink() for p in (path,*path.parents)), 'server_path_symlink')


def open_regular(path):
    safe_absolute(path)
    fd = os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'not_single_regular_file')
        return fd
    except BaseException: os.close(fd); raise


def capture_native_directory_pins():
    """Trusted-server preparation only: exact five approved public directories."""
    pins=[]
    for key,value in NATIVE_ENV.items():
        path=Path(value);safe_absolute(path);info=path.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid==0
                and info.st_mode&0o022==0,'native_directory_unsafe')
        pins.append(DirectoryPin(key,path,info.st_dev,info.st_ino,info.st_uid,stat.S_IMODE(info.st_mode)))
    return tuple(pins)


def validate_native_directory_pins(pins):
    require(type(pins) is tuple and len(pins)==5
            and all(type(p) is DirectoryPin and type(p.env_key) is str for p in pins)
            and {p.env_key for p in pins}==set(NATIVE_ENV),'native_directory_pins_invalid')
    for pin in pins:
        require(type(pin.env_key) is str and pin.path==Path(NATIVE_ENV[pin.env_key])
                and all(type(v) is int for v in (pin.device,pin.inode,pin.owner,pin.mode))
                and pin.owner==0, 'native_directory_pins_invalid')
    require({p.env_key:p for p in pins}=={p.env_key:p for p in capture_native_directory_pins()},'native_directory_pin_mismatch')


def open_registered(root, key):
    # key is a trusted server registry value, never a JobRequest/client path.
    require(type(key) is str and key and '\\' not in key and '\x00' not in key
            and all(p not in ('','.','..') for p in key.split('/')), 'invalid_registered_key')
    safe_absolute(root)
    fd = os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|os.O_NOFOLLOW)
    try:
        parts = key.split('/')
        for part in parts[:-1]:
            child = os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd); fd = child
        result = os.open(parts[-1],os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        info = os.fstat(result)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(result); raise RunnerFailure('not_single_regular_file')
        return result
    finally: os.close(fd)


def rename_noreplace(source, target):
    """Same-filesystem Linux atomic publication; never os.rename overwrite fallback."""
    libc = ctypes.CDLL(None,use_errno=True)
    fn = getattr(libc,'renameat2',None)
    require(fn is not None,'atomic_noreplace_unavailable')
    fn.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
    fn.restype=ctypes.c_int
    if fn(-100,os.fsencode(source),-100,os.fsencode(target),1) != 0:
        raise RunnerFailure('publication_collision' if ctypes.get_errno()==errno.EEXIST else 'publication_failed')


class RawQCRunner:
    def __init__(self, config):
        require(type(config) is Config and type(config.limits) is Limits, 'invalid_server_config')
        self.config=config; self._token=object(); self._active=False;self._quarantined=False
        require(type(config.entry_kind) is str and config.entry_kind in ('rscript','native_R'),'unknown_entry_kind')
        if config.entry_kind=='native_R':
            require(config.rscript==NATIVE_EXECUTABLE
                    and config.executable_pin==Pin(14488,NATIVE_EXECUTABLE_SHA256),'unapproved_native_executable')
            validate_native_directory_pins(config.native_directory_pins)
        else:require(config.native_directory_pins==(),'native_pins_on_rscript_entry')
        limits=config.limits
        for value,approved in ((limits.open_files,256),(limits.uid_nproc_limit,512),
                               (limits.core_bytes,0),(limits.job_tasks,8),(limits.uid_admission_tasks,480)):
            require(type(value) is int and value==approved,'invalid_fixed_resource_limit')
        for value in (limits.cpu_seconds,limits.address_space_bytes,limits.per_file_bytes,limits.output_bytes,limits.stdout_bytes,limits.stderr_bytes):
            require(type(value) is int and value>0, 'invalid_limits')
        for value in (limits.wall_seconds,limits.monitor_seconds,limits.cleanup_seconds):
            require(type(value) in (int,float) and not isinstance(value,bool) and math.isfinite(value) and value>0,'invalid_limits')
        require(limits.monitor_seconds<=0.05 and limits.cleanup_seconds<=5,'invalid_limits')
        require(config.script_pin.sha256==APPROVED_SCRIPT_SHA256 and config.script_pin.bytes==4949,'unapproved_script')
        require(type(config.executor_label) is str and type(config.expected_r_version) is str,'invalid_server_config')
        for root in (config.input_root,config.staging_root,config.publish_root):
            safe_absolute(root); info=root.stat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid==os.getuid() and info.st_mode&0o022==0,'uncontrolled_root')
        roots=(config.input_root,config.staging_root,config.publish_root)
        require(all(a!=b and not a.is_relative_to(b) for a in roots for b in roots if a is not b),'overlapping_roots')
        require(config.staging_root.stat().st_dev==config.publish_root.stat().st_dev,'publication_cross_filesystem')
        for path,expected in ((config.rscript,config.executable_pin),(config.script,config.script_pin)):
            fd=open_regular(path)
            try: require(pin_fd(fd)==expected,'server_file_pin_mismatch')
            finally: os.close(fd)
        require(os.access(config.rscript,os.X_OK),'executable_not_executable')

    def open_authorized_input(self, registered_server_key, version):
        """Caller must already authorize the fixed version/key; this is not auth."""
        require(type(version) is InputVersion and canonical_uuid(version.dataset_version_id)
                and canonical_uuid(version.file_id),'invalid_input_metadata')
        require(type(version.bytes) is int and 1<=version.bytes<=26214400
                and type(version.row_count) is int and version.row_count>=1
                and type(version.columns) is tuple and len(version.columns)>=2
                and version.row_count*len(version.columns)<=2000000
                and all(type(n) is str for n in version.columns)
                and type(version.sha256) is str and len(version.sha256)==64
                and all(c in '0123456789abcdef' for c in version.sha256),'invalid_input_metadata')
        try: fd=open_registered(self.config.input_root,registered_server_key)
        except OSError: raise RunnerFailure('registered_input_unavailable') from None
        info=os.fstat(fd)
        if info.st_size!=version.bytes:
            os.close(fd); raise RunnerFailure('input_size_mismatch')
        return AuthorizedInput(self._token,fd,registered_server_key,version,identity(info))

    def _input_unchanged(self, handle, check):
        require(identity(os.fstat(handle._fd))==handle._identity,'input_changed')
        current=open_registered(self.config.input_root,handle._key)
        try: require(identity(os.fstat(current))==handle._identity,'input_path_changed')
        finally: os.close(current)
        require(pin_fd(handle._fd,check)==Pin(handle.version.bytes,handle.version.sha256),'input_digest_mismatch')

    def _fixed_unchanged(self, fixed, check):
        for path,expected,original in fixed:
            fd=open_regular(path)
            try: require(identity(os.fstat(fd))==original and pin_fd(fd,check)==expected,'server_file_changed')
            finally: os.close(fd)
        if self.config.entry_kind=='native_R':
            check();validate_native_directory_pins(self.config.native_directory_pins)

    def _invocation(self, job, family):
        if self.config.entry_kind=='native_R':
            validate_native_directory_pins(self.config.native_directory_pins)
            return ([str(self.config.rscript),'--vanilla','--slave','--file='+str(job/'script.R'),
                     '--args',str(job/'input.tsv'),family,str(job/'output')],dict(ENV,**NATIVE_ENV,TZ='UTC'))
        return ([str(self.config.rscript),'--vanilla',str(job/'script.R'),str(job/'input.tsv'),
                 family,str(job/'output')],dict(ENV))

    def _snapshot(self, fd, path, expected, check):
        out=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC|os.O_NOFOLLOW,0o600)
        try:
            offset=0
            while True:
                check(); data=os.pread(fd,65536,offset)
                if not data: break
                offset+=len(data); view=memoryview(data)
                while view: written=os.write(out,view);view=view[written:]
            os.fsync(out);os.fchmod(out,0o400)
        finally: os.close(out)
        verify=open_regular(path)
        try: require(pin_fd(verify,check)==expected,'snapshot_digest_mismatch');return identity(os.fstat(verify))
        finally: os.close(verify)

    def _scan(self, job, staged, *, final=False):
        """During execution: only the two snapshots and one fixed output directory."""
        for name,expected in staged.items():
            require(identity((job/name).lstat())==expected,'snapshot_changed')
        for path in job.iterdir():
            require(path.name in set(staged)|{'output'},'unexpected_job_file')
        output=job/'output'
        if not output.exists():
            require(not output.is_symlink() and not final,'missing_outputs');return 0
        require(not output.is_symlink() and stat.S_ISDIR(output.lstat().st_mode),'invalid_output_directory')
        entries=[]
        for path in output.iterdir():
            require(path.name in OUTPUTS and len(entries)<7,'unexpected_or_missing_output')
            entries.append(path)
        require(not final or {p.name for p in entries}==OUTPUTS,'unexpected_or_missing_output')
        total=0
        for path in entries:
            info=path.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_nlink==1,'invalid_output_file')
            require(info.st_size<=self.config.limits.per_file_bytes,'output_file_limit')
            total+=max(info.st_size,info.st_blocks*512)
        require(total<=self.config.limits.output_bytes,'output_total_limit')
        return total

    def run(self, request, *, cancelled=lambda:False):
        require(not self._quarantined,'runner_quarantined')
        require(not self._active,'runner_busy')
        require(threading.current_thread() is threading.main_thread() and threading.active_count()==1,'dedicated_single_thread_worker_required')
        require(os.geteuid()!=0 and hasattr(os,'pidfd_open') and hasattr(signal,'pidfd_send_signal'),'unprivileged_linux_pidfd_required')
        require(signal.getsignal(signal.SIGCHLD)==signal.SIG_DFL and signal.getitimer(signal.ITIMER_REAL)==(0.0,0.0),'dedicated_signal_context_required')
        require(type(request) is JobRequest and canonical_uuid(request.job_id)
                and request.quant_family in ('Intensity','LFQ intensity'),'invalid_job_request')
        handle=request.input_handle
        require(type(handle) is AuthorizedInput and handle._token is self._token and handle._fd is not None,'invalid_input_handle')
        require(fcntl.fcntl(handle._fd,fcntl.F_GETFL)&os.O_ACCMODE==os.O_RDONLY,'input_handle_not_readonly')
        require(callable(cancelled),'invalid_cancel_control')
        self._active=True;limits=self.config.limits;deadline=time.monotonic()+limits.wall_seconds
        job=child=pidfd=watch=None;published=False;result=None;stopped=False;stdout=bytearray();stderr=bytearray();peak=0;runtime_quota_checks=0
        process_evidence={}
        old_handler=signal.getsignal(signal.SIGALRM)
        def check():
            value=cancelled()
            require(type(value) is bool,'invalid_cancel_control')
            require(not value,'cancelled');require(time.monotonic()<deadline,'wall_timeout')
        def expired(*args):raise RunnerFailure('wall_timeout')
        def kill_owned():
            nonlocal stopped
            if child is None:return True
            if stopped:return not self._quarantined
            stopped=True
            if watch is not None:
                try:complete=watch.stop()
                except Exception:complete=False
            else:
                # A failed pidfd/identity acquisition has no numeric-PID fallback.
                # Retain diagnostics and quarantine; never claim helper cleanup.
                with suppress(subprocess.TimeoutExpired):child.wait(timeout=limits.cleanup_seconds)
                complete=False
            if not complete:self._quarantined=True
            return complete
        def process_record():
            return {'policy':dict(process_policy.POLICY),'policy_sha256':process_policy.POLICY_SHA256,
                **process_evidence,'one_top_level_child_launched':child is not None,
                'helper_processes_allowed':True,
                **({'owned_tasks':watch.evidence()} if watch is not None else {}),
                'runner_quarantined':self._quarantined}
        try:
            signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,limits.wall_seconds)
            check();self._input_unchanged(handle,check)
            fixed=[]
            for path,expected in ((self.config.rscript,self.config.executable_pin),(self.config.script,self.config.script_pin)):
                fd=open_regular(path)
                try:require(pin_fd(fd,check)==expected,'server_file_pin_mismatch');fixed.append((path,expected,identity(os.fstat(fd))))
                finally:os.close(fd)
            target=self.config.publish_root/request.job_id
            require(not os.path.lexists(target),'publication_collision')
            job=self.config.staging_root/(request.job_id+'.partial-'+uuid4().hex);job.mkdir(mode=0o700)
            staged={'input.tsv':self._snapshot(handle._fd,job/'input.tsv',Pin(handle.version.bytes,handle.version.sha256),check)}
            scriptfd=open_regular(self.config.script)
            try:staged['script.R']=self._snapshot(scriptfd,job/'script.R',self.config.script_pin,check)
            finally:os.close(scriptfd)
            old_field_limit=__import__('csv').field_size_limit(26214400)
            try:input_header(job/'input.tsv',handle.version,request.quant_family,check)
            finally:__import__('csv').field_size_limit(old_field_limit)
            argv,child_env=self._invocation(job,request.quant_family)
            process_policy.admission(limits,check,process_evidence)
            mask=signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGALRM})
            def child_limits():
                signal.pthread_sigmask(signal.SIG_SETMASK,mask)
                os.umask(0o077)
                for key,value in ((resource.RLIMIT_CPU,limits.cpu_seconds),(resource.RLIMIT_AS,limits.address_space_bytes),(resource.RLIMIT_FSIZE,limits.per_file_bytes),(resource.RLIMIT_CORE,limits.core_bytes),(resource.RLIMIT_NOFILE,limits.open_files),(resource.RLIMIT_NPROC,limits.uid_nproc_limit)):
                    resource.setrlimit(key,(value,value))
            try:
                child=subprocess.Popen(argv,cwd=job,env=child_env,shell=False,stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,close_fds=True,preexec_fn=child_limits,start_new_session=True)
                pidfd=os.pidfd_open(child.pid)
                watch=process_policy.OwnedTasks(child,pidfd,limits);watch.bind_top()
            finally:signal.pthread_sigmask(signal.SIG_SETMASK,mask)
            with selectors.DefaultSelector() as selector:
                for stream,name in ((child.stdout,'stdout'),(child.stderr,'stderr')):
                    os.set_blocking(stream.fileno(),False);selector.register(stream,selectors.EVENT_READ,name)
                while selector.get_map() or child.poll() is None:
                    check();watch.observe(check);size=self._scan(job,staged);peak=max(peak,size);runtime_quota_checks+=1
                    for key,_ in selector.select(min(limits.monitor_seconds,max(0,deadline-time.monotonic()))):
                        data=os.read(key.fileobj.fileno(),65536)
                        if not data:selector.unregister(key.fileobj);continue
                        buffer,cap=(stdout,limits.stdout_bytes) if key.data=='stdout' else (stderr,limits.stderr_bytes)
                        remaining=max(0,cap-len(buffer));buffer.extend(data[:remaining])
                        require(len(data)<=remaining,key.data+'_limit')
            require(child.wait(timeout=0)==0,'executor_exit_failure')
            watch.finish(check)
            require(b'RAW_QC_COMPLETE\n' in stdout,'missing_completion_marker')
            self._scan(job,staged,final=True);check()
            old_field_limit=__import__('csv').field_size_limit(limits.per_file_bytes)
            try:summary=validate_outputs(job/'output',job/'input.tsv',handle.version,request.quant_family,self.config.expected_r_version,check)
            finally:__import__('csv').field_size_limit(old_field_limit)
            self._input_unchanged(handle,check);self._fixed_unchanged(fixed,check)
            for name,expected in (( 'input.tsv',Pin(handle.version.bytes,handle.version.sha256)),('script.R',self.config.script_pin)):
                fd=open_regular(job/name)
                try:require(pin_fd(fd,check)==expected,'snapshot_digest_mismatch')
                finally:os.close(fd)
            self._scan(job,staged,final=True)
            output_pins={}
            for name in sorted(OUTPUTS):
                fd=open_regular(job/'output'/name)
                try:pin=pin_fd(fd,check);output_pins[name]={'bytes':pin.bytes,'sha256':pin.sha256}
                finally:os.close(fd)
            receipt={**limits_metadata(limits),'job_id':request.job_id,'dataset_version_id':handle.version.dataset_version_id,'file_id':handle.version.file_id,
                'input_bytes':handle.version.bytes,'input_sha256':handle.version.sha256,'quant_family':request.quant_family,
                'script_sha256':self.config.script_pin.sha256,'executable_sha256':self.config.executable_pin.sha256,
                'executor_label':self.config.executor_label,'summary':summary,'outputs':output_pins,
                'entry_kind':self.config.entry_kind,
                'native_runtime_environment':({'TZ':child_env['TZ']} if self.config.entry_kind=='native_R' else {}),
                'native_directories':({p.env_key:{'path':str(p.path),'device':p.device,'inode':p.inode,'owner':p.owner,'mode':p.mode}
                                       for p in self.config.native_directory_pins} if self.config.entry_kind=='native_R' else {}),
                'runtime_quota_checks':runtime_quota_checks,'monitored_peak_output_bytes':peak,
                'aggregate_quota_is_hard_filesystem_quota':False,'transient_overshoot_between_checks_possible':True,
                'process_control':process_record(),'one_top_level_child_launched':True,
                'resource_limits_and_thread_env_applied':True,
                'stdout_bytes':len(stdout),'stderr_bytes':len(stderr),'authentication_or_queue_integration_claim':False}
            result=RunResult(target,receipt)
            # Single linearization point. No failure is reported after successful
            # publication. The local kernel rename itself is not interruptible
            # into a reversible transaction; do not claim crash/FS-stall atomicity.
            mask=signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGALRM})
            try:
                check();rename_noreplace(job/'output',target);published=True
                signal.setitimer(signal.ITIMER_REAL,0)
                if signal.SIGALRM in signal.sigpending():signal.sigtimedwait({signal.SIGALRM},0)
            finally:signal.pthread_sigmask(signal.SIG_SETMASK,mask)
            return result
        except BaseException as error:
            if published:return result
            code=error.code if isinstance(error,(RunnerFailure,process_policy.PolicyFailure)) else ('output_protocol_invalid' if isinstance(error,(ProtocolError,UnicodeError,ValueError)) else 'runner_failed')
            primary_code=code
            alive=child is not None and child.poll() is None
            signal.setitimer(signal.ITIMER_REAL,0)
            if not kill_owned():code='process_cleanup_unconfirmed'
            if job is not None:
                with suppress(Exception):
                    fd=os.open(job/'failure.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                    with os.fdopen(fd,'w') as f:json.dump({**limits_metadata(limits),'code':code,'primary_code':primary_code,'process_control':process_record(),'published':False,'stdout_bytes':len(stdout),'stderr_bytes':len(stderr),'runtime_quota_checks':runtime_quota_checks,'owned_child_stop_attempted':stopped,'child_alive_on_failure':alive,'aggregate_quota_detected_before_child_exit':primary_code=='output_total_limit' and alive},f)
                for name,data in (('stdout.bin',stdout),('stderr.bin',stderr)):
                    with suppress(Exception):
                        fd=os.open(job/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                        with os.fdopen(fd,'wb') as f:f.write(data)
            raise RunnerFailure(code,job) from None
        finally:
            signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,old_handler)
            if child is not None:
                for stream in (child.stdout,child.stderr):
                    with suppress(Exception):stream.close()
            if pidfd is not None:
                with suppress(OSError):os.close(pidfd)
            if watch is not None:
                with suppress(OSError):watch.close()
            self._active=False
