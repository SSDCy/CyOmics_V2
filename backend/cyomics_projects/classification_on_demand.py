"""Default-off, capacity-one supervised child for NEW confirmed admissions.
Root provides the pinned fixed shim and existing Control.verify; no gate,
credential, generic command, queue scanner or automatic restart is provided.
"""
import asyncio,hashlib,json,os,signal,stat,time
from dataclasses import dataclass,field
from pathlib import Path
from .settings import Fault
from .validation import canonical_uuid
from . import de_classification_jobs as jobs
from .raw_qc_single_launcher import _read_public,_read,consume

PYTHON='/home/user01/Cy_Omics_v2.0/releases/backend-review-20261116-v1-projects-owner/venv/bin/python'
ENV={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8'}
SPAWN_SECONDS=3.0
CHILD_SECONDS=420.0
CLOSE_SECONDS=4.0
STDOUT_CAP=8192
STDERR_CAP=4096
IPC_KEYS=frozenset(('owner_id','project_id','request_id','classification_job_id'))
RECEIPT_KEYS=frozenset(('schemaVersion','classification_job_id','request_id','attempt_id','disposition','reason','business_state',
    'scientific_status','teardown','loop_closed','pending_tasks_seen','async_resources_remaining','interrupted','exit_code'))

def need(value):
    if not value:raise Fault(503,'de_classification_jobs_unavailable')
def sha(body):return hashlib.sha256(body).hexdigest()
def hashed(value):return type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')
def notification(dto):
    jobs.dto(dto)
    return {'owner_id':dto['created_by'],'project_id':dto['project_id'],'request_id':dto['request_id'],'classification_job_id':dto['classification_job_id']}
def parse_notification(raw):
    from .validation import strict_json
    need(type(raw) is bytes and 0<len(raw)<=1024)
    value=strict_json(raw)
    need(type(value) is dict and set(value)==IPC_KEYS and all(canonical_uuid(v) for v in value.values()) and canonical(value)+b'\n'==raw)
    return value

@dataclass(frozen=True,slots=True)
class Config:
    enabled:bool=False
    startup:Path|None=None
    backend:Path|None=None
    control:Path|None=None
    startup_sha256:str|None=None
    python_sha256:str|None=None
    verify:object=field(default=None,repr=False)

def prepare(config=None):
    if config is None:return None
    need(type(config) is Config and type(config.enabled) is bool)
    if not config.enabled:return None
    need(all(type(p) is Path or isinstance(p,Path) for p in (config.startup,config.backend,config.control)))
    need(all(p.is_absolute() and '..' not in p.parts for p in (config.startup,config.backend,config.control)))
    need(hashed(config.startup_sha256) and hashed(config.python_sha256) and callable(config.verify))
    return config

@dataclass(frozen=True,slots=True)
class _Lease:
    key:tuple
    issuer:object=field(repr=False)

class Controller:
    def __init__(self,config=None):
        self.config=prepare(config);self._state='idle' if self.config else 'off';self._issuer=object();self._lease=None
        self._proc=None;self._pidfd=None;self._tasks=set();self._monitor=None;self._signalled=False;self._transport_close_attempted=False
        self._notification=None;self._namespaces=None;self._closing=False;self._last=None
    def snapshot(self):return {'state':self._state,'last':self._last,'automatic_retry':False,'queued_children':0}
    def _sources(self):
        cfg=self.config;need(cfg is not None and cfg.verify() is None and os.getuid()==os.geteuid()==1000)
        need(sha(_read_public(cfg.startup,65536))==cfg.startup_sha256)
        need(sha(_read_public(Path(PYTHON).resolve(strict=True),33554432))==cfg.python_sha256)
        self._namespaces={k:os.readlink('/proc/self/ns/'+k) for k in ('pid','net','mnt')}
    async def reserve(self,repository,subject,project,body,budget):
        need(type(repository) is jobs.ClassificationAdmissionRepository)
        # Do not hide an original unknown write behind off/capacity/replay.
        if repository.write_unknown:raise Fault(504,'de_classification_submission_unknown')
        need(self._state!='off' and not self._closing)
        key=(subject,project,body['request_id'])
        if self._state!='idle':
            try:value=jobs.dto(await repository.read_request(subject,project,body['request_id'],budget))
            except Fault as error:
                if error.status!=404:raise
                if self._lease is not None and self._lease.key==key:
                    raise Fault(504,'de_classification_submission_unknown') from None
                if self._state=='unknown':raise Fault(503,'de_classification_jobs_unavailable') from None
                raise Fault(409,'de_classification_capacity_busy') from None
            need(value['created_by']==subject and value['project_id']==project and value['request_id']==body['request_id'])
            if value['source_result_id']!=body['source_result_id'] or value['parameters']!=body['parameters']:
                raise Fault(409,'de_classification_request_conflict')
            return value # Confirmed original request read; never notify again.
        # No await before capacity is held. One reviewed API worker only.
        self._state='reserved';self._lease=_Lease(key,self._issuer)
        try:self._sources()
        except BaseException:self._state='unknown';raise
        return self._lease
    def reserved(self,lease):return type(lease) is _Lease and lease is self._lease and lease.issuer is self._issuer and self._state=='reserved'
    def release(self,lease,*,confirmed):
        if not self.reserved(lease):return
        if confirmed:self._lease=None;self._state='idle'
        else:self._state='unknown'
    def _intent(self,value):
        cfg=self.config;fds=[];uncertain=False
        try:
            fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC);fds.append(fd)
            for component in cfg.control.parts[1:]:
                fd=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd);fds.append(fd)
                st=os.fstat(fd);need(stat.S_ISDIR(st.st_mode) and st.st_uid in (0,1000) and not st.st_mode&0o022)
            st=os.fstat(fd);need(st.st_uid==1000 and stat.S_IMODE(st.st_mode)==0o700)
            parent=fd;fd=os.open(value['classification_job_id']+'.launch.once',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=parent);fds.append(fd)
            raw=canonical(value)+b'\n';offset=0
            while offset<len(raw):
                size=os.write(fd,raw[offset:]);need(size>0);offset+=size
            os.fsync(fd);os.fsync(parent);st=os.fstat(fd)
            need(stat.S_ISREG(st.st_mode) and st.st_uid==1000 and stat.S_IMODE(st.st_mode)==0o600 and st.st_nlink==1 and st.st_size==len(raw))
        finally:
            while fds:
                try:os.close(fds.pop())
                except BaseException:uncertain=True
            if uncertain:raise Fault(503,'de_classification_jobs_unavailable')
    def notify(self,lease,dto):
        # Admission is ALREADY confirmed. Launch refusal preserves its queued
        # response; it never invents a failed result or releases uncertainty.
        try:
            need(self.reserved(lease));value=notification(dto)
            need((value['owner_id'],value['project_id'],value['request_id'])==lease.key)
            self._sources();self._intent(value);self._notification=value;self._state='notified'
            co=self._run()
            try:self._monitor=asyncio.create_task(co)
            except BaseException:co.close();raise
            self._monitor.add_done_callback(consume);return True
        except asyncio.CancelledError:self._state='unknown';raise
        except Exception:self._state='unknown';return False
    def _task(self,operation):
        try:task=asyncio.create_task(operation)
        except BaseException:operation.close();raise
        self._tasks.add(task);task.add_done_callback(consume);return task
    def _argv(self):return (PYTHON,'-I','-B',str(self.config.startup),'--once')
    async def _spawn(self):
        return await asyncio.create_subprocess_exec(*self._argv(),cwd=str(self.config.backend),env=dict(ENV),
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True,limit=4096)
    def _pin(self,proc):
        pid=proc.pid;need(type(pid) is int and pid>1)
        def observe():
            root=Path('/proc')/str(pid);tick=int((root/'stat').read_bytes().rsplit(b')',1)[1].split()[19])
            need((root/'cmdline').read_bytes().removesuffix(b'\0').split(b'\0')==[v.encode() for v in self._argv()])
            need((root/'cwd').resolve()==self.config.backend)
            uids=next(l for l in (root/'status').read_text().splitlines() if l.startswith('Uid:')).split()[1:]
            need(uids==['1000']*4 and {k:os.readlink(str(root/'ns'/k)) for k in self._namespaces}==self._namespaces)
            return tick
        tick=observe();fd=os.pidfd_open(pid)
        try:need(observe()==tick);return fd
        except BaseException:os.close(fd);raise
    async def _send(self):
        need(self._proc.stdin is not None)
        self._proc.stdin.write(canonical(self._notification)+b'\n');await self._proc.stdin.drain()
        self._proc.stdin.close();await self._proc.stdin.wait_closed()
    def _receipt(self,raw,code):
        from .validation import strict_json
        need(raw.endswith(b'\n') and raw.count(b'\n')==1 and len(raw)<=STDOUT_CAP)
        value=strict_json(raw);need(type(value) is dict and set(value)==RECEIPT_KEYS and type(value['schemaVersion']) is int and value['schemaVersion']==1)
        need(value['classification_job_id']==self._notification['classification_job_id'] and value['request_id']==self._notification['request_id'])
        need(value['attempt_id'] is None or canonical_uuid(value['attempt_id']))
        need(type(value['exit_code']) is int and value['exit_code']==code==0 and value['disposition']=='confirmed' and value['teardown']=='confirmed')
        need(value['business_state'] in ('queued','claimed','finished','reconciliation_pending') and value['scientific_status'] in
            (None,'CLASSIFIED','BLOCKED_SOURCE_NO_TESTABLE_ROWS','BLOCKED_SOURCE_REVIEW_REQUIRED'))
        need(value['loop_closed'] is True and all(value[k] is False for k in ('pending_tasks_seen','async_resources_remaining','interrupted')))
        need(value['reason'] in ('finished','existing_result','attempt_consumed','runner_failed'))
        if value['reason'] in ('finished','runner_failed'):
            need(value['business_state']=='finished' and canonical_uuid(value['attempt_id']))
            need((value['scientific_status'] is None)==(value['reason']=='runner_failed'))
        elif value['reason']=='existing_result':
            need(value['business_state'] in ('finished','reconciliation_pending') and value['scientific_status'] is not None)
        else:need(canonical_uuid(value['attempt_id']))
        return value
    async def _stop(self):
        if self._proc is None:return
        if self._proc.returncode is None and self._pidfd is not None and not self._signalled:
            self._signalled=True
            try:signal.pidfd_send_signal(self._pidfd,signal.SIGTERM,None,0)
            except BaseException:self._state='unknown'
        waiter=self._task(self._proc.wait());await asyncio.wait({waiter},timeout=CLOSE_SECONDS)
        if not waiter.done():self._state='unknown'
    def _finish_resources(self):
        clean=True
        if self._pidfd is not None:
            fd=self._pidfd;self._pidfd=None
            try:os.close(fd)
            except BaseException:clean=False
        proc=self._proc
        if proc is not None:
            if proc.returncode is None:clean=False
            elif not self._transport_close_attempted:
                self._transport_close_attempted=True
                try:
                    t=proc._transport;need(t._finished is True);t.close();need(t.is_closing() is True)
                    for fd in (0,1,2):
                        pipe=t.get_pipe_transport(fd);need(pipe is None or pipe.get_extra_info('pipe').closed is True)
                except BaseException:clean=False
        return clean
    async def _late(self,spawn):
        try:
            self._proc=spawn.result()
            if self._proc.returncode is None:self._pidfd=self._pin(self._proc)
            await self._stop()
        except BaseException:pass
        finally:self._finish_resources();self._state='unknown'
    async def _run(self):
        spawn=None;confirmed=False
        try:
            self._sources();deadline=time.monotonic()+CHILD_SECONDS;spawn=self._task(self._spawn())
            done,_=await asyncio.wait({spawn},timeout=SPAWN_SECONDS)
            if spawn not in done:
                self._state='unknown'
                def late(task):self._task(self._late(task))
                spawn.add_done_callback(late);return
            self._proc=spawn.result()
            if self._proc.returncode is None:self._pidfd=self._pin(self._proc)
            sent=self._task(self._send());out=self._task(_read(self._proc.stdout,STDOUT_CAP));err=self._task(_read(self._proc.stderr,STDERR_CAP));waited=self._task(self._proc.wait())
            wanted={sent,out,err,waited};done,_=await asyncio.wait(wanted,timeout=max(0,deadline-time.monotonic()),return_when=asyncio.FIRST_EXCEPTION)
            need(done==wanted and all(not t.cancelled() and t.exception() is None for t in wanted))
            need(err.result()==b'' and type(waited.result()) is int and self._proc.returncode==waited.result())
            self._last=self._receipt(out.result(),waited.result());confirmed=self._state!='unknown' and not self._closing
        except BaseException:
            self._state='unknown'
            if spawn is not None and self._proc is None:
                def late(task):self._task(self._late(task))
                spawn.add_done_callback(late)
            try:await self._stop()
            except BaseException:pass
        finally:
            clean=self._finish_resources() and all(t.done() for t in self._tasks)
            if confirmed and clean:
                self._state='idle';self._lease=None;self._proc=None;self._tasks.clear();self._monitor=None;self._notification=None
                self._signalled=False;self._transport_close_attempted=False
            else:self._state='unknown'
    async def close(self):
        self._closing=True
        if self._state in ('off','idle'):return
        self._state='unknown'
        try:await self._stop()
        except BaseException:pass
        if self._monitor is not None:
            done,_=await asyncio.wait({self._monitor},timeout=CLOSE_SECONDS)
            if self._monitor not in done:self._state='unknown'
        self._finish_resources()
