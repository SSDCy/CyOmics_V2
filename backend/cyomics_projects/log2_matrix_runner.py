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
    def __init__(self, code, facts=None):
        self.code = code
        self.facts = facts if type(facts) is FailureFacts else None
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
    attempt_id: str | None = None


@dataclass(frozen=True,slots=True)
class FailureFacts:
    """Detached cleanup facts, never execution/Owner permission or kernel containment."""
    _json: bytes = field(repr=False)

    def snapshot(self):
        return json.loads(self._json)


def configuration_binding(config):
    """Attempt identity and intended byte pins; contains no filesystem paths."""
    if type(config) is not Config:return None
    def pin(value):
        return {'bytes':value.bytes,'sha256':value.sha256} if type(value) is Pin else None
    try:
        return hashlib.sha256(_canonical({'job_id':config.job_id,'attempt_id':config.attempt_id,
            'family':config.family,'flags':config.flags,'source':pin(config.source_pin),
            'selected':pin(config.selected_pin),'parameters':pin(config.parameters_pin)})).hexdigest()
    except BaseException:return None


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
        self._failure_trace = None

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
        if c.attempt_id is not None:
            try:valid_attempt=type(c.attempt_id) is str and str(UUID(c.attempt_id))==c.attempt_id
            except ValueError:valid_attempt=False
            need(valid_attempt,'invalid_log2_config')
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
        trace=self._failure_trace
        if trace is not None:trace['fds'].discard(fd) # consume once before close
        try:
            b.os.close(fd)
        except BaseException:
            if trace is not None:trace['close_unknown']=True
            self._quarantined = True
            raise Log2RunFailure('log2_cleanup_unconfirmed') from None

    def _owned_open(self,operation):
        trace=self._failure_trace
        if trace is not None:trace['open_unconfirmed']=True
        fd=operation()
        if trace is not None:
            trace['fds'].add(fd)
            trace['open_unconfirmed']=False
        return fd

    def _parents(self, b, path):
        b.safe_absolute(path)
        for parent in path.parents:
            s = parent.lstat()
            need(b.stat.S_ISDIR(s.st_mode) and s.st_uid in (0,1000) and not s.st_mode&0o022,
                 'unsafe_log2_ancestry')

    def _read(self, b, path, cap, check, expected=None, private=False):
        check(); self._parents(b,path)
        fd = self._owned_open(lambda:b.open_regular(path))
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
        fd = self._owned_open(lambda:b.os.open(path,b.os.O_WRONLY|b.os.O_CREAT|b.os.O_EXCL|b.os.O_CLOEXEC|b.os.O_NOFOLLOW,0o600))
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
        if self._failure_trace is not None:self._failure_trace['scan_unconfirmed']=True
        with b.os.scandir(job) as children:
            count=0
            for entry in children:
                check();count+=1
                need(count<=len(staged)+1 and entry.name in set(staged)|{'output'}, 'unexpected_log2_job_file')
        if self._failure_trace is not None:self._failure_trace['scan_unconfirmed']=False
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
        if self._failure_trace is not None:self._failure_trace['scan_unconfirmed']=True
        with b.os.scandir(output) as children:
            for entry in children:
                check()
                need(entry.name in validator.OUTPUTS and len(entries)<len(validator.OUTPUTS),
                     'missing_or_extra_log2_output')
                entries.append(output/entry.name)
        if self._failure_trace is not None:self._failure_trace['scan_unconfirmed']=False
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
        if self._failure_trace is not None:self._failure_trace['policy_unconfirmed']=True
        b.process_policy.admission(limits,check,admission)
        if self._failure_trace is not None:self._failure_trace['policy_unconfirmed']=False
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
                if self._failure_trace is not None:
                    self._failure_trace['popen_attempted']=True
                    self._failure_trace['launch_state']='launch_unconfirmed'
                process=b.subprocess.Popen(argv,cwd=job,env=dict(b.ENV,**b.NATIVE_ENV,TZ='UTC'),shell=False,
                    stdin=b.subprocess.DEVNULL,stdout=b.subprocess.PIPE,stderr=b.subprocess.PIPE,
                    close_fds=True,preexec_fn=restrict,start_new_session=True)
                if self._failure_trace is not None:self._failure_trace['launch_state']='started'
                streams=[process.stdout,process.stderr]
                pidfd=self._owned_open(lambda:b.os.pidfd_open(process.pid))
                watch=b.process_policy.OwnedTasks(process,pidfd,limits);watch.bind_top()
                if self._failure_trace is not None:
                    bound=watch.bound[process.pid].proc
                    self._failure_trace['process_identity']={'pid':bound.pid,'starttick':bound.starttick,'proc_inode':bound.inode,'uid':bound.uid}
            finally:
                try:b.signal.pthread_sigmask(b.signal.SIG_SETMASK,mask)
                except BaseException:
                    # A failed parent mask restore stays unknown through all cleanup.
                    cleanup_unknown=True
                    raise
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
            if self._failure_trace is not None:
                self._failure_trace['exit_code']=exited
                self._failure_trace['child_reaped']=True
            check();watch.finish(check)
            evidence=watch.evidence()
            need(evidence.get('cleanup',{}).get('complete_for_observed_identities') is True
                 and evidence.get('uncertainty')==[], 'log2_process_cleanup_unconfirmed')
            if self._failure_trace is not None:
                self._failure_trace['observed_tasks_stopped']=True
                self._failure_trace['process_evidence_sha256']=hashlib.sha256(_canonical(evidence)).hexdigest()
            result=(process.returncode,bytes(buffers['stdout']),bytes(buffers['stderr']),
                    {'admission':admission,'owned_tasks':evidence,'peak_allocated_output_bytes':peak})
            complete=True
        finally:
            # Never let the main execution timer interrupt its own bounded cleanup.
            try:b.signal.setitimer(b.signal.ITIMER_REAL,0)
            except BaseException:
                cleanup_unknown=True
                if self._failure_trace is not None:self._failure_trace['timer_disable_unknown']=True
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
                    elif self._failure_trace is not None:
                        self._failure_trace['observed_tasks_stopped']=True
                        try:
                            exited=process.wait(timeout=0)
                            if type(exited) is not int:raise ValueError()
                            self._failure_trace['child_reaped']=True
                            self._failure_trace['exit_code']=exited
                            evidence=watch.evidence()
                            if evidence.get('cleanup',{}).get('complete_for_observed_identities') is not True or evidence.get('uncertainty')!=[]:
                                self._failure_trace['policy_unconfirmed']=True
                            self._failure_trace['process_evidence_sha256']=hashlib.sha256(_canonical(evidence)).hexdigest()
                        except BaseException:cleanup_unknown=True
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
            if self._failure_trace is not None:
                self._failure_trace['spawn_resources_closed']=not cleanup_unknown
                if process is not None and self._failure_trace['process_identity'] is None:
                    cleanup_unknown=True
            if cleanup_unknown:
                if self._failure_trace is not None:self._failure_trace['close_unknown']=True
                self._quarantined=True
                raise Log2RunFailure('log2_cleanup_unconfirmed') from None
        # The caller's remaining deadline still bounds validation and rehash.
        check(); b.signal.setitimer(b.signal.ITIMER_REAL,max(0.000001,deadline-time.monotonic()))
        return result

    def run(self, *, cancelled=lambda:False):
        # A rejected reentry cannot overwrite the live/previous call's facts.
        need(not self._active,'log2_runner_busy')
        need(not self._quarantined,'log2_runner_quarantined')
        c=self._config
        trace={'fds':set(),'open_unconfirmed':False,'scan_unconfirmed':False,'policy_unconfirmed':False,'close_unknown':False,
            'timer_disable_unknown':False,'handler_restore_unknown':False,'timer_pending':False,'handler_pending':False,
            'popen_attempted':False,'launch_state':'not_started','process_identity':None,'exit_code':None,
            'child_reaped':False,'observed_tasks_stopped':False,'process_evidence_sha256':None,'spawn_resources_closed':True}
        self._failure_trace=trace
        try:
            return self._run_native(cancelled=cancelled)
        except BaseException as error:
            code=error.code if isinstance(error,Log2RunFailure) else 'log2_run_unconfirmed'
            nochild=not trace['popen_attempted']
            fds_closed=not(trace['fds'] or trace['open_unconfirmed'] or trace['scan_unconfirmed'] or trace['policy_unconfirmed'] or trace['close_unknown']) and trace['spawn_resources_closed']
            timer_disabled=not(trace['timer_pending'] or trace['timer_disable_unknown'])
            handler_restored=not(trace['handler_pending'] or trace['handler_restore_unknown'])
            reaped=nochild or trace['child_reaped'];stopped=nochild or trace['observed_tasks_stopped']
            known=(nochild or trace['launch_state']=='started' and trace['process_identity'] is not None) and reaped and stopped and fds_closed and timer_disabled and handler_restored
            facts=FailureFacts(_canonical({'schemaVersion':1,'job_id':c.job_id if type(c) is Config else None,
                'attempt_id':c.attempt_id if type(c) is Config else None,'configuration_sha256':configuration_binding(c),
                'popen_attempted':trace['popen_attempted'],'launch_state':trace['launch_state'],
                'process_identity':trace['process_identity'],'exit_code':trace['exit_code'],'child_reaped':reaped,
                'observed_tasks_stopped':stopped,'owned_fds_closed':fds_closed,'timer_disabled':timer_disabled,
                'signal_handler_restored':handler_restored,'process_evidence_sha256':trace['process_evidence_sha256'],'cleanup_known':bool(known)}))
            if not known:self._quarantined=True
            raise Log2RunFailure(code,facts) from None
        finally:self._failure_trace=None

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
                'attempt_id':c.attempt_id,'configuration_sha256':configuration_binding(c),
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
