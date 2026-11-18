"""Default-off, trusted-server single native R log2 adapter; no publication/API.

Reuses RawQC's native pins, fixed limits/environment and OwnedTasks observer.
Only a dedicated Linux main-thread process may run it. No retry or numeric kill.
"""
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from uuid import UUID
from . import log2_matrix_validator as validator

SCRIPT_BYTES = 11996
SCRIPT_SHA256 = '08aec68d6ee74bd09b7cc70100d10e147c527986a3d42323fe5541a3b608e787'
VALIDATOR_BYTES = 14877
VALIDATOR_SHA256 = 'ba5fa3ed521fe7f97810ecdab06e1936e9dec8c9ebac45f8d2da3e3d77989d9e'
EMPTY_OUTPUTS = frozenset(('filter-log.tsv','row-map.tsv','summary.tsv','parameters.json','R-session-info.txt'))


class Log2RunFailure(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def need(value, code):
    if not value:
        raise Log2RunFailure(code)


@dataclass(frozen=True, slots=True)
class Pin:
    bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class Config:
    # Server-only construction, never decode this object from an HTTP DTO.
    enabled: bool = False
    job_id: str | None = None
    source: Path | None = field(default=None, repr=False)
    source_pin: Pin | None = None
    selected: Path | None = field(default=None, repr=False)
    selected_pin: Pin | None = None
    parameters_pin: Pin | None = None
    script: Path | None = field(default=None, repr=False)
    staging_root: Path | None = field(default=None, repr=False)
    family: str | None = None
    flags: tuple = ()
    native_directory_pins: tuple = field(default=(), repr=False)


@dataclass(frozen=True, slots=True)
class RunResult:
    bundle: validator.ValidatedLog2Bundle = field(repr=False)
    _receipt: bytes = field(repr=False)

    @property
    def receipt(self):
        return json.loads(self._receipt)


def _runtime():
    # Deferred: disabled construction/run does not import POSIX executors,
    # inspect native paths, load credentials, open files or create processes.
    from .r_raw_qc import raw_qc_runner
    return raw_qc_runner


def _pin(data):
    return Pin(len(data), hashlib.sha256(data).hexdigest())


def _valid_pin(value, cap):
    return (type(value) is Pin and type(value.bytes) is int and 1 <= value.bytes <= cap
            and type(value.sha256) is str and re.fullmatch('[0-9a-f]{64}',value.sha256) is not None)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf8')


class Log2MatrixRunner:
    def __init__(self, config=None):
        need(config is None or type(config) is Config, 'invalid_log2_config')
        self._config = config
        self._active = False
        self._quarantined = False

    @property
    def quarantined(self):
        return self._quarantined

    def _configuration(self):
        c = self._config
        need(c is not None and type(c.enabled) is bool and c.enabled, 'log2_runner_disabled')
        need(type(c.job_id) is str, 'invalid_log2_config')
        try:
            valid_id = str(UUID(c.job_id)) == c.job_id
        except ValueError:
            valid_id = False
        need(valid_id and type(c.family) is str and c.family in ('Intensity','LFQ intensity'), 'invalid_log2_config')
        need(all(isinstance(path,Path) and path.is_absolute() and '..' not in path.parts
                 for path in (c.source,c.selected,c.script,c.staging_root)), 'invalid_log2_config')
        need(len({c.source,c.selected,c.script})==3 and all(not path.is_relative_to(c.staging_root)
                 for path in (c.source,c.selected,c.script)), 'invalid_log2_config')
        need(_valid_pin(c.source_pin,validator.MAX_INPUT) and _valid_pin(c.selected_pin,validator.MAX_SELECTED)
             and _valid_pin(c.parameters_pin,4096), 'invalid_log2_config')
        try:params = validator.parameters(c.flags)
        except validator.ValidationFailure:
            raise Log2RunFailure('invalid_log2_config') from None
        need(_pin(params)==c.parameters_pin, 'parameters_pin_mismatch')
        return c

    def _close(self, b, fd):
        try:
            b.os.close(fd)
        except BaseException:
            self._quarantined = True
            raise Log2RunFailure('log2_cleanup_unconfirmed') from None

    def _parents(self, b, path):
        b.safe_absolute(path)
        for parent in path.parents:
            s = parent.lstat()
            need(b.stat.S_ISDIR(s.st_mode) and s.st_uid in (0,1000) and not s.st_mode&0o022,
                 'unsafe_log2_ancestry')

    def _read(self, b, path, cap, check, expected=None, private=False):
        check(); self._parents(b,path)
        fd = b.open_regular(path)
        try:
            start = b.os.fstat(fd)
            need(b.stat.S_ISREG(start.st_mode) and start.st_nlink==1
                 and start.st_uid in ((1000,) if private else (0,1000))
                 and not start.st_mode&(0o077 if private else 0o022)
                 and 1 <= start.st_size <= cap, 'invalid_log2_file')
            saved = b.identity(start)
            result = bytearray()
            while True:
                check()
                chunk = b.os.pread(fd,min(65536,cap+1-len(result)),len(result))
                if not chunk:
                    break
                result.extend(chunk)
                need(len(result)<=cap and len(result)<=start.st_size, 'log2_file_size_changed')
            need(len(result)==start.st_size and b.identity(b.os.fstat(fd))==saved
                 and b.identity(path.lstat())==saved, 'log2_short_or_changed_read')
            data = bytes(result)
            need(expected is None or _pin(data)==expected, 'log2_file_pin_mismatch')
            return data, saved
        finally:
            self._close(b,fd)

    def _write_snapshot(self,b,path,data,check):
        fd = b.os.open(path,b.os.O_WRONLY|b.os.O_CREAT|b.os.O_EXCL|b.os.O_CLOEXEC|b.os.O_NOFOLLOW,0o600)
        try:
            position = 0
            while position<len(data):
                check(); count = b.os.write(fd,data[position:position+65536])
                need(type(count) is int and 0<count<=min(65536,len(data)-position), 'log2_snapshot_write_failed')
                position += count
            b.os.fchmod(fd,0o400); b.os.fsync(fd)
        finally:
            self._close(b,fd)
        _,identity = self._read(b,path,len(data),check,_pin(data),private=True)
        return identity

    def _scan(self,b,job,staged,check,expected=None):
        check()
        need(b.stat.S_ISDIR(job.lstat().st_mode) and job.lstat().st_uid==1000
             and not job.lstat().st_mode&0o077, 'invalid_log2_job_directory')
        with b.os.scandir(job) as children:
            count=0
            for entry in children:
                check();count+=1
                need(count<=len(staged)+1 and entry.name in set(staged)|{'output'}, 'unexpected_log2_job_file')
        for name,identity in staged.items():
            need(b.identity((job/name).lstat())==identity, 'log2_snapshot_changed')
        output = job/'output'
        if not b.os.path.lexists(output):
            need(expected is None, 'missing_log2_outputs')
            return 0
        s = output.lstat()
        need(b.stat.S_ISDIR(s.st_mode) and s.st_uid==1000 and not s.st_mode&0o077,
             'invalid_log2_output_directory')
        entries=[]
        with b.os.scandir(output) as children:
            for entry in children:
                check()
                need(entry.name in validator.OUTPUTS and len(entries)<len(validator.OUTPUTS),
                     'missing_or_extra_log2_output')
                entries.append(output/entry.name)
        names = {p.name for p in entries}
        need(len(entries)<=len(validator.OUTPUTS) and names<=set(validator.OUTPUTS)
             and (expected is None or names==set(expected)), 'missing_or_extra_log2_output')
        total = 0
        for path in entries:
            check(); s=path.lstat()
            need(b.stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_uid==1000
                 and not s.st_mode&0o077 and 0<=s.st_size<=validator.MAX_FILE, 'invalid_log2_output_file')
            total += max(s.st_size,getattr(s,'st_blocks',0)*512)
        need(total<=validator.MAX_TOTAL, 'log2_output_total_limit')
        return total

    def _collect(self,b,job,staged,check,expected):
        self._scan(b,job,staged,check,expected)
        outputs = {}; identities = {}
        for name in sorted(expected):
            outputs[name],identities[name] = self._read(b,job/'output'/name,validator.MAX_FILE,check,private=True)
            need(sum(map(len,outputs.values()))<=validator.MAX_TOTAL, 'log2_output_total_limit')
        self._scan(b,job,staged,check,expected)
        for name,identity in identities.items():
            need(b.identity((job/'output'/name).lstat())==identity, 'log2_output_changed')
        return outputs, identities

    def _spawn_and_wait(self,b,job,staged,check,deadline,limits):
        # Lifecycle is the existing reviewed RawQC pattern and OwnedTasks, not
        # a new process controller. Close uncertainty remains sticky here.
        c=self._config; process=pidfd=watch=selector=None; streams=[]
        complete=False; cleanup_unknown=False; buffers={'stdout':bytearray(),'stderr':bytearray()}
        admission={}; peak=0; result=None
        argv=[str(b.NATIVE_EXECUTABLE),'--vanilla','--slave','--file='+str(job/'script.R'),'--args',
              str(job/'input.tsv'),str(job/'selected.tsv'),c.family,
              *['true' if flag else 'false' for flag in c.flags],
              'to_NA','positive_only','none','none',str(job/'output')]
        b.process_policy.admission(limits,check,admission)
        try:
            mask=b.signal.pthread_sigmask(b.signal.SIG_BLOCK,{b.signal.SIGALRM})
            def restrict():
                b.signal.pthread_sigmask(b.signal.SIG_SETMASK,mask)
                b.os.umask(0o077)
                for key,value in ((b.resource.RLIMIT_CPU,limits.cpu_seconds),
                    (b.resource.RLIMIT_AS,limits.address_space_bytes),(b.resource.RLIMIT_FSIZE,limits.per_file_bytes),
                    (b.resource.RLIMIT_CORE,limits.core_bytes),(b.resource.RLIMIT_NOFILE,limits.open_files),
                    (b.resource.RLIMIT_NPROC,limits.uid_nproc_limit)):
                    b.resource.setrlimit(key,(value,value))
            try:
                check()
                process=b.subprocess.Popen(argv,cwd=job,env=dict(b.ENV,**b.NATIVE_ENV,TZ='UTC'),shell=False,
                    stdin=b.subprocess.DEVNULL,stdout=b.subprocess.PIPE,stderr=b.subprocess.PIPE,
                    close_fds=True,preexec_fn=restrict,start_new_session=True)
                streams=[process.stdout,process.stderr]
                pidfd=b.os.pidfd_open(process.pid)
                watch=b.process_policy.OwnedTasks(process,pidfd,limits);watch.bind_top()
            finally:
                b.signal.pthread_sigmask(b.signal.SIG_SETMASK,mask)
            selector=b.selectors.DefaultSelector()
            for name,stream in zip(('stdout','stderr'),streams):
                b.os.set_blocking(stream.fileno(),False)
                selector.register(stream,b.selectors.EVENT_READ,name)
            while selector.get_map() or process.poll() is None:
                check();watch.observe(check);peak=max(peak,self._scan(b,job,staged,check))
                for key,_ in selector.select(min(limits.monitor_seconds,max(0,deadline-time.monotonic()))):
                    chunk=b.os.read(key.fileobj.fileno(),65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        cap=getattr(limits,key.data+'_bytes');remaining=cap-len(buffers[key.data])
                        buffers[key.data].extend(chunk[:max(0,remaining)])
                        need(len(chunk)<=remaining, 'log2_'+key.data+'_limit')
            exited=process.wait(timeout=0)
            need(type(exited) is int and exited==process.returncode, 'log2_child_exit_unknown')
            check();watch.finish(check)
            evidence=watch.evidence()
            need(evidence.get('cleanup',{}).get('complete_for_observed_identities') is True
                 and evidence.get('uncertainty')==[], 'log2_process_cleanup_unconfirmed')
            result=(process.returncode,bytes(buffers['stdout']),bytes(buffers['stderr']),
                    {'admission':admission,'owned_tasks':evidence,'peak_allocated_output_bytes':peak})
            complete=True
        finally:
            # Never let the main execution timer interrupt its own bounded cleanup.
            try:b.signal.setitimer(b.signal.ITIMER_REAL,0)
            except BaseException:
                cleanup_unknown=True
            if process is not None and not complete:
                try:
                    if watch is None:
                        # No numeric-PID fallback if creation/identity acquisition
                        # was incomplete, even if a later poll happens to show exit.
                        try:process.wait(timeout=limits.cleanup_seconds)
                        except BaseException:pass
                        cleanup_unknown=True
                    elif watch.stop() is not True:
                        cleanup_unknown=True
                except BaseException:
                    cleanup_unknown=True
            for resource in [watch,selector,*streams]:
                if resource is None:continue
                try:resource.close()
                except BaseException:cleanup_unknown=True
            if pidfd is not None:
                try:self._close(b,pidfd)
                except BaseException:cleanup_unknown=True
            if process is not None and process.returncode is None:
                cleanup_unknown=True
            if cleanup_unknown:
                self._quarantined=True
                raise Log2RunFailure('log2_cleanup_unconfirmed') from None
        # The caller's remaining deadline still bounds validation and rehash.
        check(); b.signal.setitimer(b.signal.ITIMER_REAL,max(0.000001,deadline-time.monotonic()))
        return result

    def run(self, *, cancelled=lambda:False):
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
            b.signal.signal(b.signal.SIGALRM,expired);b.signal.setitimer(b.signal.ITIMER_REAL,limits.wall_seconds)
            check();b.validate_native_directory_pins(c.native_directory_pins)
            self._parents(b,c.staging_root)
            root_info=c.staging_root.lstat()
            need(b.stat.S_ISDIR(root_info.st_mode) and root_info.st_uid==1000 and not root_info.st_mode&0o077,
                 'uncontrolled_log2_staging_root')
            fixed = ((c.source,c.source_pin,validator.MAX_INPUT),(c.selected,c.selected_pin,validator.MAX_SELECTED),
                     (c.script,Pin(SCRIPT_BYTES,SCRIPT_SHA256),SCRIPT_BYTES),
                     (Path(validator.__file__),Pin(VALIDATOR_BYTES,VALIDATOR_SHA256),VALIDATOR_BYTES),
                     (b.NATIVE_EXECUTABLE,Pin(14488,b.NATIVE_EXECUTABLE_SHA256),14488))
            native_info=b.NATIVE_EXECUTABLE.lstat()
            need(b.stat.S_ISREG(native_info.st_mode) and native_info.st_uid==0
                 and b.stat.S_IMODE(native_info.st_mode)==0o755, 'unsafe_log2_native_executable')
            content={}; identities={}
            for path,expected,cap in fixed:
                content[path],identities[path]=self._read(b,path,cap,check,expected,
                                                         private=path in (c.source,c.selected))
            need(b.os.access(b.NATIVE_EXECUTABLE,b.os.X_OK), 'native_log2_not_executable')
            job=c.staging_root/(c.job_id+'.partial')
            need(not b.os.path.lexists(job), 'log2_job_already_exists')
            job.mkdir(mode=0o700)
            staged={name:self._write_snapshot(b,job/name,content[path],check)
                    for name,path in (('input.tsv',c.source),('selected.tsv',c.selected),('script.R',c.script))}
            self._scan(b,job,staged,check)
            rc,stdout,stderr,process=self._spawn_and_wait(b,job,staged,check,deadline,limits)
            need(stderr==b'', 'log2_executor_stderr')
            if rc==3 and stdout==b'LOG2_MATRIX_EMPTY\n':
                outputs,_=self._collect(b,job,staged,check,EMPTY_OUTPUTS)
                try:validator.validate(content[c.source],content[c.selected],c.family,c.flags,outputs,check=check)
                except validator.ValidationFailure as error:
                    need(error.code=='empty_not_analyzable', 'log2_invalid_empty_diagnostic')
                    raise Log2RunFailure('empty_not_analyzable') from None
                raise Log2RunFailure('log2_invalid_empty_diagnostic')
            need(rc==0 and stdout==b'LOG2_MATRIX_COMPLETE\n', 'log2_executor_failed')
            outputs,output_ids=self._collect(b,job,staged,check,validator.OUTPUTS)
            bundle=validator.validate(content[c.source],content[c.selected],c.family,c.flags,outputs,check=check)
            # Full original/fixed bytes and output identities again after pure
            # verification. The bundle itself owns detached immutable bytes.
            for path,expected,cap in fixed:
                _,current=self._read(b,path,cap,check,expected,private=path in (c.source,c.selected))
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
                'source':{'bytes':c.source_pin.bytes,'sha256':bundle.source_sha256},
                'selected':{'bytes':c.selected_pin.bytes,'sha256':bundle.selected_sha256},
                'parameters':{'bytes':c.parameters_pin.bytes,'sha256':bundle.parameters_sha256},
                'script':{'bytes':SCRIPT_BYTES,'sha256':SCRIPT_SHA256},
                'validator':{'bytes':VALIDATOR_BYTES,'sha256':VALIDATOR_SHA256},
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
            try:b.signal.setitimer(b.signal.ITIMER_REAL,0)
            except BaseException:failure=Log2RunFailure('log2_cleanup_unconfirmed');self._quarantined=True
            try:b.signal.signal(b.signal.SIGALRM,old_handler)
            except BaseException:failure=Log2RunFailure('log2_cleanup_unconfirmed');self._quarantined=True
            self._active=False
        if failure is not None:
            raise failure from None
        need(result is not None, 'log2_run_unconfirmed')
        return result
