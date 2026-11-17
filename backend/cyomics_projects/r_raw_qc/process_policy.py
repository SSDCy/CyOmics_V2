"""Development-only observation of one owned session, never kernel containment.

Only public real-UID metadata is used to filter task details to our UID. Signals use verified
pidfds exclusively; no numeric PID/PGID, command-name or whole-UID termination.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import resource
import select
import signal
import time
from types import MappingProxyType

POLICY = MappingProxyType({
    'id':'dev_owned_tasks_v1','hard_per_job_pids_cap':False,
    'uid_census_scope':'visible_same_real_uid_procfs_metadata',
    'short_lived_or_unobserved_escape_detection_guaranteed':False,
    'all_descendant_containment_or_cleanup_guaranteed':False,
    'CPU_AS_are_per_process_not_job_aggregate':True,
    'signals':'identity_bound_pidfd_only','background_monitor':False,
    'max_observed_process_identities':128})
POLICY_SHA256=hashlib.sha256(json.dumps(dict(POLICY),sort_keys=True,
    separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()


class PolicyFailure(RuntimeError):
    def __init__(self,code):self.code=code;super().__init__(code)


@dataclass(frozen=True, slots=True)
class Proc:
    pid:int
    starttick:int
    inode:int
    uid:int
    ppid:int
    pgid:int
    sid:int
    threads:int
    state:str

    @property
    def key(self):return (self.pid,self.starttick,self.inode,self.uid)


def read_proc(pid):
    """Metadata only; pin proc directory across both reads, reject PID recycling."""
    path=Path('/proc')/str(pid)
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|os.O_NOFOLLOW)
    try:
        inode=os.fstat(fd).st_ino
        def read(name):
            child=os.open(name,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW,dir_fd=fd)
            try:
                data=os.read(child,16384)
                if len(data)==16384:raise PolicyFailure('proc_metadata_oversize')
                return data.decode('utf-8',errors='surrogateescape')
            finally:os.close(child)
        status=read('status')
        uid=int(next(s for s in status.splitlines() if s.startswith('Uid:')).split()[1])
        first=read('stat');second=read('stat')
        def fields(text):
            tail=text[text.rindex(')')+2:].split()
            return (int(tail[19]),int(tail[1]),int(tail[2]),int(tail[3]),int(tail[17]),tail[0])
        one,two=fields(first),fields(second)
        if one[0]!=two[0] or path.stat().st_ino!=inode:
            raise PolicyFailure('proc_identity_changed')
        if two[4]<1:raise PolicyFailure('proc_metadata_invalid')
        return Proc(pid,two[0],inode,uid,two[1],two[2],two[3],two[4],two[5])
    finally:os.close(fd)


def read_real_uid(pid):
    """Read only public UID metadata for filtering, no command/env/user files.

    Proc-directory ownership alone misses same-real-UID nondumpable/setuid tasks.
    The inode binds this UID observation to the subsequent own-task metadata.
    """
    fd=os.open('/proc/'+str(pid),os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|os.O_NOFOLLOW)
    try:
        status=os.open('status',os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW,dir_fd=fd)
        try:data=os.read(status,16384)
        finally:os.close(status)
        if len(data)==16384:raise PolicyFailure('uid_census_metadata_oversize')
        line=next(s for s in data.splitlines() if s.startswith(b'Uid:'))
        return int(line.split()[1]),os.fstat(fd).st_ino
    finally:os.close(fd)


def uid_snapshot(check=lambda:None):
    """A sampled census, not atomic; unreadable same-UID metadata fails closed.

    Only the public real-UID field is read before filtering. Full task metadata
    is read for our real UID only. A mismatch is incomplete, never silently
    excluded. Actual-A preflight is required; hidden namespaces are not inferred.
    """
    result={};uid=os.getuid()
    try:
        with os.scandir('/proc') as entries:
            for entry in entries:
                if not entry.name.isdecimal():continue
                check()
                try:
                    real_uid,inode=read_real_uid(int(entry.name))
                    if real_uid!=uid:continue
                    proc=read_proc(int(entry.name))
                except (FileNotFoundError,ProcessLookupError):continue
                if proc.uid!=uid or proc.inode!=inode:raise PolicyFailure('uid_census_uid_mismatch')
                result[proc.pid]=proc
    except (OSError,ValueError,StopIteration,UnicodeError) as error:
        raise PolicyFailure('uid_census_incomplete') from error
    if os.getpid() not in result:raise PolicyFailure('uid_census_incomplete')
    return result


def admission(limits,check,evidence):
    _,hard=resource.getrlimit(resource.RLIMIT_NPROC)
    evidence['inherited_NPROC_hard']=hard
    if hard!=resource.RLIM_INFINITY and hard<limits.uid_nproc_limit:
        raise PolicyFailure('uid_nproc_inherited_hard_low')
    snapshot=uid_snapshot(check)
    count=sum(p.threads for p in snapshot.values())
    evidence.update(uid_tasks_before=count,uid_census_complete_before=True)
    if count>limits.uid_admission_tasks:raise PolicyFailure('uid_admission_tasks_exceeded')


def fd_exited(fd):
    poll=select.poll();poll.register(fd,select.POLLIN|select.POLLHUP|select.POLLERR)
    events=poll.poll(0)
    if any(flags&select.POLLNVAL for _,flags in events):
        raise PolicyFailure('pidfd_invalid')
    return bool(events)


@dataclass(slots=True)
class Bound:
    proc:Proc
    fd:int
    signalled:bool=False


class OwnedTasks:
    def __init__(self,child,root_fd,limits):
        self.child=child;self.root_fd=root_fd;self.limits=limits
        self.bound={};self.events=[];self.unknown=[];self.scans=0
        self.peak=0;self.last_live=[];self.cleanup=None;self.after=None;self.last_uid_tasks=None

    def _event(self,kind,proc):
        if len(self.events)>=512:raise PolicyFailure('process_evidence_limit')
        self.events.append({'event':kind,'pid':proc.pid,'starttick':proc.starttick,
            'proc_inode':proc.inode,'ppid':proc.ppid,'pgid':proc.pgid,
            'sid':proc.sid,'uid':proc.uid,'observed_threads':proc.threads,'state':proc.state})

    def _uncertain(self,code):
        if code not in self.unknown:self.unknown.append(code)
        raise PolicyFailure(code)

    def bind_top(self):
        p=read_proc(self.child.pid)
        if p.uid!=os.getuid() or p.ppid!=os.getpid() or p.sid!=p.pid or p.pgid!=p.pid:
            self._uncertain('top_level_identity_unconfirmed')
        self._bind(p,self.root_fd)

    def _bind(self,proc,fd=None):
        if len(self.bound)>=POLICY['max_observed_process_identities']:
            self._uncertain('process_observation_limit')
        owned_fd=fd is None
        if owned_fd:fd=os.pidfd_open(proc.pid)
        try:
            again=read_proc(proc.pid)
            fdinfo=Path('/proc/self/fdinfo')/str(fd)
            bound_pid=int(next(s for s in fdinfo.read_text().splitlines() if s.startswith('Pid:')).split()[1])
            if again.key!=proc.key or bound_pid!=proc.pid:
                self._uncertain('process_identity_changed')
            self._event('bound',again);self.bound[proc.pid]=Bound(proc,fd)
        except BaseException:
            if owned_fd:os.close(fd)
            raise

    def observe(self,check=lambda:None,*,enforce=True):
        try:snapshot=uid_snapshot(check)
        except PolicyFailure as error:self._uncertain(error.code)
        self.scans+=1
        if self.child.pid not in self.bound:self._uncertain('top_level_identity_unconfirmed')
        # The identity key remains binding after reparenting or session change.
        for pid,bound in list(self.bound.items()):
            current=snapshot.get(pid)
            if current is not None and current.key!=bound.proc.key:
                self._uncertain('process_identity_reused')
            if current is not None and not fd_exited(bound.fd):
                if current.sid!=self.child.pid or current.pgid!=self.child.pid:
                    self._uncertain('owned_process_session_changed')
            elif current is None and not fd_exited(bound.fd):
                self._uncertain('owned_process_metadata_missing')
        remaining={pid:p for pid,p in snapshot.items() if pid not in self.bound}
        changed=True
        while changed:
            changed=False
            for pid,p in list(remaining.items()):
                parent=self.bound.get(p.ppid)
                parent_now=snapshot.get(p.ppid)
                if parent is None or parent_now is None or parent_now.key!=parent.proc.key:continue
                try:self._bind(p)
                except (FileNotFoundError,ProcessLookupError):
                    # This sampled child has verifiably departed, never signalled.
                    try:read_proc(pid)
                    except (FileNotFoundError,ProcessLookupError):
                        self._event('vanished_before_binding',p);del remaining[pid];continue
                    self._uncertain('process_binding_unconfirmed')
                except (OSError,ValueError,StopIteration) as error:
                    self._uncertain('process_binding_unconfirmed')
                del remaining[pid];changed=True
                if p.sid!=self.child.pid or p.pgid!=self.child.pid:
                    self._uncertain('owned_process_session_changed')
        if any(p.sid==self.child.pid or p.pgid==self.child.pid for p in remaining.values()):
            self._uncertain('unproven_job_session_member')
        live=[snapshot[pid] for pid,b in self.bound.items()
              if pid in snapshot and not fd_exited(b.fd)]
        count=sum(p.threads for p in live)
        self.peak=max(self.peak,count);self.last_live=[p.pid for p in live]
        self.last_uid_tasks=sum(p.threads for p in snapshot.values())
        if self.unknown:raise PolicyFailure(self.unknown[0])
        if enforce and count>self.limits.job_tasks:raise PolicyFailure('job_tasks_exceeded')
        return live

    def finish(self,check):
        live=self.observe(check)
        if live:raise PolicyFailure('job_residual_tasks')
        self.after=self.last_uid_tasks
        self.cleanup={'status':'no_observed_live_tasks','complete_for_observed_identities':True}

    def stop(self):
        """Bounded best effort only for verified identities; uncertainty is sticky."""
        deadline=time.monotonic()+self.limits.cleanup_seconds
        errors=[]
        def budget():
            if time.monotonic()>=deadline:raise PolicyFailure('process_cleanup_timeout')
        while time.monotonic()<deadline:
            try:self.observe(budget,enforce=False)
            except Exception as error:
                code=getattr(error,'code','cleanup_observation_failed')
                if code not in errors:errors.append(code)
            # Helpers first; all signals remain pidfd-bound, including the root.
            ordered=sorted(self.bound.values(),key=lambda b:b.proc.pid==self.child.pid)
            for b in ordered:
                if time.monotonic()>=deadline:break
                try:
                    if fd_exited(b.fd) or b.signalled:continue
                    current=read_proc(b.proc.pid)
                    if current.key!=b.proc.key:
                        if 'process_identity_reused' not in self.unknown:self.unknown.append('process_identity_reused')
                        continue
                    signal.pidfd_send_signal(b.fd,signal.SIGKILL);b.signalled=True
                    self._event('SIGKILL_pidfd',current)
                except ProcessLookupError:continue
                except Exception as error:
                    code=getattr(error,'code','cleanup_identity_unconfirmed')
                    if code not in errors:errors.append(code)
            self.child.poll() # Reap only the Popen-owned top-level process.
            try:
                alive=any(not fd_exited(b.fd) for b in self.bound.values())
            except PolicyFailure:
                alive=True
            if self.bound and not alive:
                try:
                    self.observe(budget,enforce=False);self.after=self.last_uid_tasks
                except Exception as error:
                    code=getattr(error,'code','cleanup_observation_failed')
                    if code not in errors:errors.append(code)
                break
            time.sleep(min(0.01,max(0,deadline-time.monotonic())))
        try:all_dead=bool(self.bound) and all(fd_exited(b.fd) for b in self.bound.values())
        except Exception:all_dead=False
        self.cleanup={'status':'observed_tasks_stopped' if all_dead and not errors and not self.unknown else 'unconfirmed',
            'complete_for_observed_identities':all_dead and not errors and not self.unknown,
            'errors':errors,'uncertainty':list(self.unknown)}
        return self.cleanup['complete_for_observed_identities']

    def evidence(self):
        value={'policy':dict(POLICY),'policy_sha256':POLICY_SHA256,
            'observations':self.events,'observation_scans':self.scans,
            'observed_process_identities':len(self.bound),
            'verified_descendant_identities':max(0,len(self.bound)-1),
            'signals_sent':sum(b.signalled for b in self.bound.values()),
            'cleanup':self.cleanup,'uncertainty':list(self.unknown)}
        if self.last_uid_tasks is not None:
            value.update(observed_job_tasks_peak=self.peak,uid_tasks_last_observed=self.last_uid_tasks)
        if self.after is not None:value['uid_tasks_after']=self.after
        return value

    def close(self):
        for b in self.bound.values():
            if b.fd!=self.root_fd:os.close(b.fd)
