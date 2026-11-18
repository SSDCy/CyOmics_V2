"""One private POST/worker launch attempt; default off, no queue daemon/ACK.

Root activation must separately verify empty queue, old producer off, one API
worker and this unique enqueue path. Ordinary global claim is NOT a selector.
"""
import asyncio
from dataclasses import dataclass
import hashlib,json,os,signal,stat,sys,time
from pathlib import Path
from uuid import UUID

BACKEND=Path('/home/user01/Cy_Omics_v2.0/releases/raw-qc-live-review-001/candidate/backend')
STARTUP=BACKEND/'cyomics_projects/raw_qc_live_once_start.py'
MANIFEST=Path('/home/user01/Cy_Omics_v2.0/audit/RAW-QC-LIVE-CONTROL-001/approved-backend-manifest.json')
CONTROL=Path('/home/user01/Cy_Omics_v2.0/runtime/raw-qc-live-001/control')
PYTHON='/home/user01/Cy_Omics_v2.0/releases/backend-review-20261116-v1-projects-owner/venv/bin/python'
ARGV=(PYTHON,'-I','-B',str(STARTUP),'--once')
ENV={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8'}
SPAWN_SECONDS=3.0
CHILD_SECONDS=85.0
CLOSE_SECONDS=4.0
STDOUT_CAP=8192
STDERR_CAP=4096
NS={'pid':'pid:[4026532213]','net':'net:[4026531833]','mnt':'mnt:[4026532211]'}

class Refused(RuntimeError):
    def __init__(self):super().__init__('raw_qc_single_launcher_refused')
def need(v):
    if not v:raise Refused()
def sha(b):return hashlib.sha256(b).hexdigest()
def hashok(v):return type(v) is str and len(v)==64 and all(c in '0123456789abcdef' for c in v)
def uid(v):
    try:return type(v) is str and str(UUID(v))==v
    except (ValueError,TypeError):return False
def parse(raw,cap):
    need(type(raw) is bytes and 0<len(raw)<=cap and not raw.startswith(b'\xef\xbb\xbf'))
    def pairs(items):
        d={}
        for k,v in items:need(k not in d);d[k]=v
        return d
    return json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(Refused()))
def consume(task):
    try:
        if not task.cancelled():task.exception()
    except BaseException:pass

@dataclass(frozen=True,slots=True)
class Config:
    enabled:bool=False
    startup_sha256:str|None=None
    python_sha256:str|None=None
    backend_manifest_sha256:str|None=None
    activation_review_sha256:str|None=None

def configured(config):
    if config is None:return False
    need(type(config) is Config and type(config.enabled) is bool)
    if not config.enabled:return False
    need(all(hashok(v) for v in (config.startup_sha256,config.python_sha256,config.backend_manifest_sha256,config.activation_review_sha256)))
    return True

def _repo_types():
    from .raw_qc_repository import RawQCRepository,EnqueueObservation
    return RawQCRepository,EnqueueObservation
def _request(raw):
    from .raw_qc_contract import request_input
    return request_input(raw)
def _job(value,project,dataset,body):
    from .raw_qc_http import response_value
    return response_value(value,{'method':'POST','project':project,'dataset':dataset,'job':None,'request':None},body)
def _fault(code='raw_qc_unavailable',status=503):
    from .settings import Fault
    return Fault(status,code)

def _read_public(path,cap):
    # Exact fixed public sources; no runtime/credential discovery.
    for parent in path.parents:
        s=parent.lstat();need(stat.S_ISDIR(s.st_mode) and s.st_uid in (0,1000) and not s.st_mode&0o022)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    try:
        before=os.fstat(fd);need(stat.S_ISREG(before.st_mode) and before.st_uid in (0,1000) and before.st_nlink==1 and not before.st_mode&0o022 and 0<=before.st_size<=cap)
        out=b''
        while len(out)<=cap:
            b=os.read(fd,min(65536,cap+1-len(out)))
            if not b:break
            out+=b
        key=lambda s:(s.st_dev,s.st_ino,s.st_uid,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        need(len(out)==before.st_size and key(os.fstat(fd))==key(before)==key(path.lstat()))
        return out
    finally:os.close(fd)

def _sources(config):
    need(os.name=='posix' and sys.platform.startswith('linux') and os.getuid()==os.geteuid()==1000)
    need({k:os.readlink('/proc/self/ns/'+k) for k in NS}==NS)
    need(sha(_read_public(STARTUP,65536))==config.startup_sha256)
    # Existing fixed venv executable may be a reviewed symlink. Bind its resolved
    # regular target bytes, while argv remains the one fixed venv path.
    native=Path(PYTHON).resolve(strict=True);need(sha(_read_public(native,16777216))==config.python_sha256)
    raw=_read_public(MANIFEST,131072);need(sha(raw)==config.backend_manifest_sha256);m=parse(raw,131072)
    need(type(m) is dict and set(m)=={'files'} and type(m['files']) is dict and 1<=len(m['files'])<=256)
    total=0
    for name,p in m['files'].items():
        need(type(name) is str and name.startswith('cyomics_projects/') and '\\' not in name and not name.startswith('/') and all(x not in ('','.','..') for x in name.split('/')))
        need(type(p) is dict and set(p)=={'bytes','sha256'} and type(p['bytes']) is int and 0<=p['bytes']<=262144 and hashok(p['sha256']))
        total+=p['bytes'];need(total<=4194304)
        b=_read_public(BACKEND/name,262144);need(len(b)==p['bytes'] and sha(b)==p['sha256'])
    required={'raw_qc_single_launcher.py','raw_qc_live_once_start.py','raw_qc_once_entry.py','raw_qc_dispatch.py','raw_qc_runtime.py','raw_qc_repository.py','raw_qc_worker_pool.py','raw_qc_worker_source.py','raw_qc_input_export.py','raw_qc_artifacts.py','raw_qc_contract.py','raw_qc_worker.py','raw_qc_checkpoint.py','settings.py','r_raw_qc/__init__.py','r_raw_qc/raw_qc_runner.py','r_raw_qc/process_policy.py','r_raw_qc/output_contract.py','r_raw_qc/approved_raw_qc.R'}
    need({'cyomics_projects/'+n for n in required}<=set(m['files']))

def _reserve(key):
    # Persistent one-attempt latch precedes SQL. Existing/unknown never reset.
    need(hashok(key))
    fds=[];uncertain=False
    try:
        fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC);fds.append(fd)
        for part in CONTROL.parts[1:]:
            fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd);fds.append(fd)
            s=os.fstat(fd);need(stat.S_ISDIR(s.st_mode) and s.st_uid in (0,1000) and not s.st_mode&0o022)
        s=os.fstat(fd);need(s.st_uid==1000 and stat.S_IMODE(s.st_mode)==0o700)
        parent=fd;fd=os.open('web-launch.once',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=parent);fds.append(fd)
        raw=(key+'\n').encode('ascii');offset=0
        while offset<len(raw):
            n=os.write(fd,raw[offset:]);need(n>0);offset+=n
        os.fsync(fd);os.fsync(parent)
        s=os.fstat(fd);need(stat.S_ISREG(s.st_mode) and s.st_uid==1000 and stat.S_IMODE(s.st_mode)==0o600 and s.st_nlink==1 and s.st_size==65)
    finally:
        while fds:
            owned=fds.pop()
            try:os.close(owned)
            except BaseException:uncertain=True
        if uncertain:raise Refused()

async def _spawn():
    return await asyncio.create_subprocess_exec(*ARGV,cwd=str(BACKEND),env=dict(ENV),stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True,limit=4096)

def _pin_child(proc):
    # Numeric PID is only a hint; signal only a descriptor bound after matching
    # the exact fixed argv/UID/starttick before and after pidfd acquisition.
    pid=proc.pid;need(type(pid) is int and pid>1)
    def observe():
        base=Path('/proc')/str(pid);raw=(base/'stat').read_bytes();tick=int(raw.rsplit(b')',1)[1].split()[19])
        args=(base/'cmdline').read_bytes().removesuffix(b'\0').split(b'\0')
        need(args==[s.encode() for s in ARGV] and (base/'cwd').resolve()==BACKEND)
        lines=(base/'status').read_text().splitlines();uids=next(x for x in lines if x.startswith('Uid:')).split()[1:];need(uids==['1000']*4)
        need({k:os.readlink(str(base/'ns'/k)) for k in NS}==NS)
        return tick
    tick=observe();fd=os.pidfd_open(pid)
    try:need(observe()==tick);return fd
    except BaseException:
        os.close(fd);raise Refused() from None

async def _read(stream,cap):
    data=bytearray()
    while True:
        b=await stream.read(min(4096,cap+1-len(data)));need(type(b) is bytes)
        if not b:return bytes(data)
        data.extend(b);need(len(data)<=cap)

def _entry(raw,exit_code):
    need(raw.endswith(b'\n') and raw.count(b'\n')==1);v=parse(raw,STDOUT_CAP)
    keys={'schemaVersion','action','status','reason','exit_code','dispatch_called','dispatch_status','dispatch_reason','attempt_id','job_id','teardown','loop_created','loop_closed','pending_tasks_seen','async_resources_remaining','interrupted','automatic_retry','ACK_calls','next_jobs','Owner_or_API_E2E'}
    need(type(v) is dict and set(v)==keys and type(v['schemaVersion']) is int and v['schemaVersion']==1 and v['action']=='run_once' and type(v['exit_code']) is int and v['exit_code']==exit_code)
    for k in ('dispatch_called','loop_created','pending_tasks_seen','async_resources_remaining','interrupted','automatic_retry','Owner_or_API_E2E'):need(type(v[k]) is bool)
    need((type(v['loop_closed']) is bool or v['loop_closed'] is None) and v['teardown'] in ('confirmed','unknown') and v['automatic_retry'] is False and v['ACK_calls']==0 and type(v['ACK_calls']) is int and v['next_jobs']==0 and type(v['next_jobs']) is int and v['Owner_or_API_E2E'] is False)
    need(all(x is None or uid(x) for x in (v['attempt_id'],v['job_id'])))
    allowed={'confirmed_succeeded':('succeeded',0),'confirmed_failed':('failed',4),'confirmed_terminal':('terminal_readback_confirmed',4),'idle':('confirmed_no_job',3)}
    need(v['status'] in allowed and (v['reason'],exit_code)==allowed[v['status']])
    need(v['dispatch_called'] is True and v['loop_created'] is True and v['loop_closed'] is True and v['pending_tasks_seen'] is False and v['async_resources_remaining'] is False and v['interrupted'] is False and v['teardown']=='confirmed')
    need(v['dispatch_reason']==v['reason'] and v['dispatch_status']==('idle' if v['status']=='idle' else 'confirmed'))
    if v['status']=='idle':need(v['attempt_id'] is v['job_id'] is None)
    else:need(uid(v['attempt_id']) and uid(v['job_id']))
    return {'status':v['status'],'attempt_id':v['attempt_id'],'actual_job_id':v['job_id']}

class SingleRawQCLauncher:
    """One app-lifetime instance. No cancellation/reload automatically frees it."""
    def __init__(self,config=None):
        self._config=config;self._state='idle' if configured(config) else 'off';self._key=None
        self._monitor=None;self._tasks=set();self._proc=None;self._pidfd=None;self._signalled=False
        self._notification_job=None;self._actual=None;self._captures=None;self._transport_close_attempted=False;self._late_hooked=False
    def snapshot(self):
        return {'state':self._state,'notification_job_id':self._notification_job,'worker_outcome':None if self._actual is None else dict(self._actual),'captures':None if self._captures is None else dict(self._captures),'automatic_retry':False,'ACK_calls':0,'next_jobs':0,'post_job_success_inferred':False}
    async def submit(self,repository,subject,project,dataset,raw,budget):
        if self._state=='off':raise _fault()
        Repo,Observation=_repo_types();need(type(repository) is Repo and repository._enqueue_observation_enabled is True)
        need(all(uid(s) for s in (subject,project,dataset)));body=_request(raw)
        key=sha(json.dumps([subject,project,dataset,body['request_id'],body['quant_family']],separators=(',',':')).encode())
        if self._state!='idle':
            if self._state in ('notified','observed','replayed') and self._key==key:
                return _job(await repository.read_request(subject,project,dataset,body['request_id'],budget),project,dataset,body)
            raise _fault('raw_qc_unavailable')
        # No await before the slot is held: one API loop serializes the entire
        # enqueue/COMMIT/disposition/notify interval, not only the later spawn.
        self._state='reserving';self._key=key
        try:
            need(repository.enqueue_observation_locked is False)
            _sources(self._config);_reserve(key)
            self._state='enqueuing'
            observation=await repository.enqueue_observed(subject,project,dataset,raw,budget)
            need(type(observation) is Observation and all(type(getattr(observation,k)) is bool for k in ('created','commit_confirmed','teardown_confirmed')))
            need(observation.commit_confirmed is True and observation.teardown_confirmed is True and repository.enqueue_observation_locked is False)
            value=_job(observation.job_public(),project,dataset,body);self._notification_job=value['job_id']
            if observation.created is False:
                need(observation.may_notify_new is False);self._state='replayed';return value
            need(observation.may_notify_new is True and value['status']=='queued' and value['observation']=='confirmed')
            self._state='notified'
            co=self._run()
            try:self._monitor=asyncio.create_task(co)
            except BaseException:co.close();raise
            self._monitor.add_done_callback(consume)
            return value
        except BaseException:
            self._state='unknown';raise
    def _retain(self,task):
        self._tasks.add(task);task.add_done_callback(consume);return task
    def _task(self,co):
        try:task=asyncio.create_task(co)
        except BaseException:co.close();raise
        return self._retain(task)
    async def _stop(self):
        if self._proc is None:return
        if self._proc.returncode is None:
            if self._pidfd is None or self._signalled:return
            self._signalled=True
            try:signal.pidfd_send_signal(self._pidfd,signal.SIGTERM,None,0)
            except BaseException:self._state='unknown'
        waiting=self._task(self._proc.wait())
        await asyncio.wait({waiting},timeout=CLOSE_SECONDS)
        if not waiting.done():self._state='unknown'
    async def _late_spawn(self,task):
        try:
            self._proc=task.result()
            if self._proc.returncode is None:self._pidfd=_pin_child(self._proc)
            await self._stop()
        except BaseException:pass
        finally:self._finish_resources()
        # No later callback is allowed to undo the original uncertainty.
        self._state='unknown'
    def _arm_late(self,task):
        if self._late_hooked:return
        self._late_hooked=True
        def late(done):
            co=self._late_spawn(done)
            try:self._retain(asyncio.create_task(co))
            except BaseException:co.close();consume(done)
        task.add_done_callback(late)
    async def _run(self):
        spawn=stdout=stderr=waited=None
        try:
            need(self._state=='notified')
            _sources(self._config)
            need(self._state=='notified')
            spawn=self._task(_spawn())
            done,_=await asyncio.wait({spawn},timeout=SPAWN_SECONDS)
            if spawn not in done:
                self._state='unknown'
                self._arm_late(spawn);return
            self._proc=spawn.result()
            if self._proc.returncode is None:self._pidfd=_pin_child(self._proc)
            stdout=self._task(_read(self._proc.stdout,STDOUT_CAP))
            stderr=self._task(_read(self._proc.stderr,STDERR_CAP))
            waited=self._task(self._proc.wait())
            wanted={stdout,stderr,waited};done,_=await asyncio.wait(wanted,timeout=CHILD_SECONDS,return_when=asyncio.FIRST_EXCEPTION)
            need(done==wanted and all(not t.cancelled() and t.exception() is None for t in wanted))
            out,err,exit_code=stdout.result(),stderr.result(),waited.result()
            self._captures={'stdout_bytes':len(out),'stdout_sha256':sha(out),'stderr_bytes':len(err),'stderr_sha256':sha(err),'child_exit':exit_code}
            need(err==b'' and type(exit_code) is int and self._proc.returncode==exit_code)
            result=_entry(out,exit_code)
            if self._state=='unknown':raise Refused()
            self._actual=result;self._state='observed'
        except BaseException:
            self._state='unknown'
            if spawn is not None and self._proc is None:self._arm_late(spawn)
            try:await self._stop()
            except BaseException:pass
        finally:
            captured={'stdout_bytes':None,'stdout_sha256':None,'stderr_bytes':None,'stderr_sha256':None,'child_exit':None,'capture_complete':False}
            if self._proc is not None and type(self._proc.returncode) is int:captured['child_exit']=self._proc.returncode
            try:
                if stdout is not None and stderr is not None and all(t.done() and not t.cancelled() and t.exception() is None for t in (stdout,stderr)):
                    a,b=stdout.result(),stderr.result();captured.update(stdout_bytes=len(a),stdout_sha256=sha(a),stderr_bytes=len(b),stderr_sha256=sha(b),capture_complete=True)
            except BaseException:pass
            self._captures=captured
            self._finish_resources()
            if any(not t.done() for t in self._tasks):self._state='unknown'
    def _finish_resources(self):
        if self._pidfd is not None:
            fd=self._pidfd;self._pidfd=None
            try:os.close(fd)
            except BaseException:self._state='unknown'
        if self._proc is not None and self._proc.returncode is not None and not self._transport_close_attempted:
            self._transport_close_attempted=True
            try:
                # Reviewed CPython asyncio subprocess transport only. Never call
                # close while alive: that could use a numeric-PID kill path.
                transport=self._proc._transport
                need(transport._finished is True)
                transport.close();need(transport.is_closing() is True)
                for fd in (1,2):
                    pipe=transport.get_pipe_transport(fd)
                    need(pipe is None or pipe.get_extra_info('pipe').closed is True)
            except BaseException:self._state='unknown'
    async def close(self):
        if self._state in ('off','idle','replayed'):return
        if self._monitor is not None and self._monitor.done():
            if any(not t.done() for t in self._tasks):self._state='unknown'
            return
        self._state='unknown'
        try:await self._stop()
        except BaseException:pass
        # Never cancel-and-gather an unbounded handshake or declare it closed.

class SingleLaunchRepository:
    """Explicit server DI adapter; RawQCJobs' Authority remains unchanged.

    Caller must enable repository enqueue observations and retain/close the
    launcher in app state/lifespan. No generic getattr or worker SQL surface.
    """
    def __init__(self,repository=None,launcher=None):self._repository=repository;self._launcher=launcher
    async def enqueue(self,*args):
        if self._repository is None or self._launcher is None:raise _fault()
        return await self._launcher.submit(self._repository,*args)
    async def read_request(self,*args):
        if self._repository is None:raise _fault()
        return await self._repository.read_request(*args)
    async def read_job(self,*args):
        if self._repository is None:raise _fault()
        return await self._repository.read_job(*args)
