"""Private durable intent, not an R receipt inside the seven-file publication.

Only dedicated server roots are accepted. No orphan cleanup, temp replacement,
credential discovery, background task, or database call occurs in this module.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
from .raw_qc_artifacts import open_root

fsync=os.fsync
MAX_CHECKPOINT=262144
MAX_PRIVATE=1048576
PHASES=frozenset(('idle','claim_intent','claimed','source_ready','executing',
    'result_ready','commit_intent','confirmed','reconciliation'))


class JournalFailure(RuntimeError):
    pass


class WorkerBusy(JournalFailure):
    pass


def require(value):
    if not value:raise JournalFailure('private_journal_unconfirmed')


def encode(value,cap):
    data=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')
    require(len(data)<=cap)
    return data


def decode(data,cap):
    require(len(data)<=cap)
    def pairs(items):
        value={}
        for k,v in items:
            require(k not in value);value[k]=v
        return value
    value=json.loads(data.decode('utf-8'),object_pairs_hook=pairs,
        parse_constant=lambda _:(_ for _ in ()).throw(JournalFailure('private_journal_invalid')))
    require(encode(value,cap)==data)
    return value


def ident(s):return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)


def own_file(fd,cap,*,empty=False):
    s=os.fstat(fd)
    require(stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid() and s.st_nlink==1
        and stat.S_IMODE(s.st_mode)==0o600 and (0 if empty else 1)<=s.st_size<=cap)
    return ident(s)


def write_all(fd,data):
    view=memoryview(data)
    while view:
        n=os.write(fd,view);require(n>0);view=view[n:]


@dataclass(frozen=True,slots=True)
class WorkerRoots:
    control:Path
    inputs:Path
    staging:Path
    published:Path
    receipts:Path

    def verify(self):
        paths=(self.control,self.inputs,self.staging,self.published,self.receipts)
        require(all(isinstance(p,Path) for p in paths))
        require(all(a!=b and not a.is_relative_to(b)
            for i,a in enumerate(paths) for j,b in enumerate(paths) if i!=j))
        for p in paths:
            fd=open_root(p)
            try:require(stat.S_IMODE(os.fstat(fd).st_mode)==0o700)
            finally:os.close(fd)


class Journal:
    def __init__(self,roots):
        self.roots=roots;self.root_fd=self.lock_fd=None;self.sequence=0;self.current=None

    @contextmanager
    def locked(self):
        self.roots.verify();self.root_fd=open_root(self.roots.control)
        try:
            self.lock_fd=os.open('worker.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,0o600,dir_fd=self.root_fd)
            own_file(self.lock_fd,1,empty=True)
            try:fcntl.flock(self.lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise WorkerBusy('worker_busy') from None
            self._locked_path();yield self
        finally:
            if self.lock_fd is not None:os.close(self.lock_fd);self.lock_fd=None
            if self.root_fd is not None:os.close(self.root_fd);self.root_fd=None

    def _locked_path(self):
        require(self.root_fd is not None and self.lock_fd is not None)
        require(ident(os.stat('worker.lock',dir_fd=self.root_fd,follow_symlinks=False))==ident(os.fstat(self.lock_fd)))
        check=open_root(self.roots.control)
        try:require(os.fstat(check).st_ino==os.fstat(self.root_fd).st_ino and os.fstat(check).st_dev==os.fstat(self.root_fd).st_dev)
        finally:os.close(check)

    def load(self):
        self._locked_path()
        try:os.stat('checkpoint.next',dir_fd=self.root_fd,follow_symlinks=False)
        except FileNotFoundError:pass
        else:raise JournalFailure('incomplete_checkpoint_requires_reconciliation')
        try:fd=os.open('checkpoint.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=self.root_fd)
        except FileNotFoundError:return {'phase':'idle'}
        try:
            before=own_file(fd,MAX_CHECKPOINT);data=os.pread(fd,MAX_CHECKPOINT+1,0)
            require(own_file(fd,MAX_CHECKPOINT)==before)
            envelope=decode(data,MAX_CHECKPOINT)
            require(set(envelope)=={'schemaVersion','sequence','state','sha256'}
                and type(envelope['schemaVersion']) is int and envelope['schemaVersion']==1
                and type(envelope['sequence']) is int and envelope['sequence']>=1
                and type(envelope['state']) is dict and envelope['state'].get('phase') in PHASES)
            payload={k:envelope[k] for k in ('schemaVersion','sequence','state')}
            require(hashlib.sha256(encode(payload,MAX_CHECKPOINT)).hexdigest()==envelope['sha256'])
            self.sequence=envelope['sequence'];self.current=before
            return envelope['state']
        finally:os.close(fd)

    def save(self,state):
        self._locked_path();require(type(state) is dict and state.get('phase') in PHASES)
        if self.current is not None:
            require(ident(os.stat('checkpoint.json',dir_fd=self.root_fd,follow_symlinks=False))==self.current)
        else:
            try:os.stat('checkpoint.json',dir_fd=self.root_fd,follow_symlinks=False)
            except FileNotFoundError:pass
            else:raise JournalFailure('unexpected_checkpoint')
        payload={'schemaVersion':1,'sequence':self.sequence+1,'state':state}
        data=encode({**payload,'sha256':hashlib.sha256(encode(payload,MAX_CHECKPOINT)).hexdigest()},MAX_CHECKPOINT)
        fd=os.open('checkpoint.next',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=self.root_fd)
        try:write_all(fd,data);fsync(fd);own_file(fd,MAX_CHECKPOINT)
        finally:os.close(fd)
        self._locked_path()
        os.replace('checkpoint.next','checkpoint.json',src_dir_fd=self.root_fd,dst_dir_fd=self.root_fd)
        fsync(self.root_fd)
        self.current=ident(os.stat('checkpoint.json',dir_fd=self.root_fd,follow_symlinks=False));self.sequence+=1

    def private_record(self,attempt,value):
        # Only a canonical UUID-derived basename, never a caller path.
        from .validation import canonical_uuid
        require(canonical_uuid(attempt));self._locked_path()
        name=attempt+'.receipt.json';data=encode(value,MAX_PRIVATE)
        root=open_root(self.roots.receipts)
        try:
            fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=root)
            try:write_all(fd,data);fsync(fd);own_file(fd,MAX_PRIVATE)
            finally:os.close(fd)
            fsync(root)
        finally:os.close(root)
        return {'name':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}

    def read_private(self,attempt,pin):
        require(type(pin) is dict and set(pin)=={'name','bytes','sha256'} and pin['name']==attempt+'.receipt.json')
        root=open_root(self.roots.receipts)
        try:
            fd=os.open(pin['name'],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=root)
            try:
                before=own_file(fd,MAX_PRIVATE);data=os.pread(fd,MAX_PRIVATE+1,0)
                require(own_file(fd,MAX_PRIVATE)==before and len(data)==pin['bytes']
                    and hashlib.sha256(data).hexdigest()==pin['sha256'])
                return decode(data,MAX_PRIVATE)
            finally:os.close(fd)
        finally:os.close(root)
