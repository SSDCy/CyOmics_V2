"""Default-off, synchronous Linux/POSIX artifact snapshot reader, outside ASGI.

Caller MUST authenticate and Owner-authorize/deep-decode the job first. These
checks are consistency checks, not Owner/attempt/DB authorization. No registration,
result regeneration, writes, fsync, R, DB, HTTP, thread, lease or retry exists here.
"""
from dataclasses import dataclass
import hashlib
import inspect
import os
from pathlib import Path
import stat
import sys
import time
from . import raw_qc_contract as c

PER_FILE=67108864
WALL_SECONDS=9
CHUNK=65536
MAX_ROOT_COMPONENTS=64
UNAVAILABLE='raw_qc_artifact_unavailable'
TIMEOUT='raw_qc_artifact_timeout'
CLOSE_UNCONFIRMED='raw_qc_artifact_cleanup_unconfirmed'
OFF='raw_qc_artifact_reader_unconfigured'


class ArtifactReadFailure(RuntimeError):
    def __init__(self,code=UNAVAILABLE):
        self.code=code if code in (OFF,UNAVAILABLE,TIMEOUT,CLOSE_UNCONFIRMED) else UNAVAILABLE
        super().__init__(self.code)


def require(value):
    if not value:raise ArtifactReadFailure()


def file_identity(s):
    return (s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_uid,s.st_gid,
        s.st_size,s.st_mtime_ns,s.st_ctime_ns)


def dir_identity(s):
    # Unrelated children of an ancestor or publish root can legitimately change.
    # Bind its instance/owner/permissions; selected file uses full identity.
    return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid)


def directory(fd,*,private=False):
    s=os.fstat(fd);require(stat.S_ISDIR(s.st_mode))
    mode=stat.S_IMODE(s.st_mode)
    if private:
        require(s.st_uid==os.geteuid() and mode in (0o700,0o500))
    else:
        require(s.st_uid in (0,os.geteuid()))
        # A root-owned sticky system temp parent protects our private child.
        require(not mode&0o022 or (s.st_uid==0 and bool(mode&stat.S_ISVTX)))
    return dir_identity(s)


def regular(fd):
    s=os.fstat(fd)
    require(stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_uid==os.geteuid()
        and stat.S_IMODE(s.st_mode) in (0o400,0o600) and 1<=s.st_size<=PER_FILE)
    return file_identity(s)


class _Owned:
    """Acquire all descriptors before releasing any; one close attempt per fd.

    Linux releases a close() descriptor even on late close errors. Never retry a
    numeric fd after an exception: another opener could already own that number.
    A close error prevents success, and does not skip closing the other fds.
    """
    def __init__(self):self.fds=[];self.links=[];self.cleanup_uncertain=False
    def _close_once(self,fd):
        try:os.close(fd)
        except BaseException:self.cleanup_uncertain=True
    def open(self,path,flags,*,dir_fd=None):
        fd=os.open(path,flags,dir_fd=dir_fd)
        try:self.fds.append(fd)
        except BaseException:
            # Tracking allocation can fail after open succeeded. This fd has
            # not entered the owned list; release it once here as well.
            self._close_once(fd)
            if self.cleanup_uncertain:raise ArtifactReadFailure(CLOSE_UNCONFIRMED) from None
            raise
        return fd
    def close_all(self):
        while self.fds:
            fd=self.fds.pop() # relinquish before syscall, including error paths
            self._close_once(fd)
        return not self.cleanup_uncertain
    def root(self,path,budget):
        require(isinstance(path,Path) and path.is_absolute() and '..' not in path.parts
            and str(path)==os.path.normpath(str(path)) and len(path.parts)<=MAX_ROOT_COMPONENTS)
        flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC
        budget();fd=self.open('/',flags);directory(fd)
        for name in path.parts[1:]:
            budget();parent=fd;fd=self.open(name,flags,dir_fd=parent)
            key=directory(fd)
            self.links.append((parent,name,fd,key))
        key=directory(fd,private=True)
        return fd,key
    def recheck_links(self,budget):
        for parent,name,fd,key in self.links:
            budget()
            require(directory(fd)==key and dir_identity(os.stat(name,dir_fd=parent,follow_symlinks=False))==key)


def exact_names(folder,budget,owned):
    names=set()
    entries=os.scandir(folder)
    try:
        for entry in entries:
            budget();require(entry.name in c.OUTPUTS and len(names)<7)
            names.add(entry.name)
    finally:
        try:entries.close()
        except BaseException:
            owned.cleanup_uncertain=True
            raise ArtifactReadFailure(CLOSE_UNCONFIRMED) from None
    require(names==c.OUTPUTS)


def checked_public(job,name):
    # Never forge a running job or an Owner/attempt/database row to reuse the
    # running-only artifact builder. Validate the persisted public shape directly.
    require(type(name) is str and name in c.OUTPUTS)
    c.exact(job,c.PUBLIC_KEYS)
    require(type(job['schemaVersion']) is int and job['schemaVersion']==1)
    for key in ('job_id','request_id'):c.identifier(job[key])
    c.source_value(job['source']);c.execution_value(job['execution'])
    require(job['quant_family'] in c.FAMILIES.values())
    c.exact(job['params'],())
    require(job['status']=='succeeded' and job['observation']=='confirmed' and job['error'] is None)
    c.integer(job['revision'],1)
    created=c.parse_stamp(job['created_at']);started=c.parse_stamp(job['started_at']);finished=c.parse_stamp(job['finished_at'])
    require(created<=started<=finished)
    s=job['source'];request=c.request_sha(s['project_id'],s['dataset_version_id'],job['quant_family'])
    require(job['request_sha256']==request and job['binding_sha256']==c.binding_sha(request,s,job['execution'],job['quant_family']))
    result=job['result'];c.result_value(result,job)
    require(type(result['schemaVersion']) is int and result['schemaVersion']==1
        and c.parse_stamp(result['verified_at'])<=finished)
    # Isolate the metadata from caller mutations during check()/I/O callbacks.
    snapshot=c.strict_document(c.canonical(job).encode('utf-8'),c.MAX_RESPONSE)
    artifact=next(item for item in snapshot['result']['artifacts'] if item['name']==name)
    require(1<=artifact['bytes']<=PER_FILE)
    return snapshot,artifact


@dataclass(frozen=True,slots=True)
class VerifiedArtifact:
    body:bytes
    name:str
    media_type:str
    sha256:str
    length:int


class RawQCArtifactReader:
    """One long-lived server instance, serialized calls outside ASGI.

    Cleanup uncertainty permanently quarantines this instance. No automatic
    reset/replacement is safe; an operator must resolve the resource state.
    """
    def __init__(self,publish_root=None):
        self._publish_root=publish_root;self._quarantined=False

    def read_verified(self,job_public,name,*,check=lambda:None):
        if self._publish_root is None:raise ArtifactReadFailure(OFF)
        if self._quarantined:raise ArtifactReadFailure(CLOSE_UNCONFIRMED)
        owned=_Owned();value=None;failure=None
        try:
            # Restrict the close-error semantics to the project's Linux target;
            # this is not a claim of portable EINTR retry behavior on all POSIX.
            require(os.name=='posix' and sys.platform.startswith('linux') and os.geteuid()>0
                and hasattr(os,'O_NOFOLLOW') and hasattr(os,'pread') and callable(check))
            deadline=time.monotonic()+WALL_SECONDS
            def budget():
                returned=check()
                if inspect.isawaitable(returned):
                    if inspect.iscoroutine(returned):returned.close()
                    raise ArtifactReadFailure()
                require(returned is None)
                if time.monotonic()>=deadline:raise TimeoutError()
            budget();job,pin=checked_public(job_public,name);budget()
            root,root_key=owned.root(self._publish_root,budget)
            flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC
            folder=owned.open(job['job_id'],flags,dir_fd=root)
            folder_key=directory(folder,private=True)
            exact_names(folder,budget,owned)
            fd=owned.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=folder)
            before=regular(fd);length=int(pin['bytes']);require(before[6]==length)
            digest=hashlib.sha256();buffer=bytearray();offset=0
            while offset<length:
                budget();chunk=os.pread(fd,min(CHUNK,length-offset),offset)
                require(bool(chunk) and len(chunk)<=length-offset)
                digest.update(chunk);buffer.extend(chunk);offset+=len(chunk)
            budget();require(os.pread(fd,1,length)==b'')
            require(offset==length and digest.hexdigest()==pin['sha256'] and regular(fd)==before)
            require(file_identity(os.stat(name,dir_fd=folder,follow_symlinks=False))==before
                and directory(folder,private=True)==folder_key and directory(root,private=True)==root_key
                and dir_identity(os.stat(job['job_id'],dir_fd=root,follow_symlinks=False))==folder_key)
            exact_names(folder,budget,owned)
            owned.recheck_links(budget)
            # Reopen the trusted path to bind it to the same publish instance.
            _,reopened=owned.root(self._publish_root,budget);require(reopened==root_key)
            budget();value=VerifiedArtifact(bytes(buffer),pin['name'],pin['media_type'],pin['sha256'],length)
        except TimeoutError:failure=TIMEOUT
        except Exception:failure=UNAVAILABLE
        finally:
            if not owned.close_all():
                self._quarantined=True
                raise ArtifactReadFailure(CLOSE_UNCONFIRMED) from None
        if failure is not None:raise ArtifactReadFailure(failure) from None
        try:budget() # includes snapshot-copy/close time before returning bytes
        except TimeoutError:raise ArtifactReadFailure(TIMEOUT) from None
        except Exception:raise ArtifactReadFailure() from None
        return value
