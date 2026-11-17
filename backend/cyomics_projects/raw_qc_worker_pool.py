"""Disabled by default; one lease, no idle sockets/tasks/retry or provisioning."""
import asyncio
from dataclasses import dataclass
import json
import os
import re
import stat
from .settings import Fault

SECRET_PARTS=('home','user01','Cy_Omics_v2.0','runtime','raw-qc-worker.json')


@dataclass(frozen=True, slots=True)
class Config:
    """Trusted startup object. Not a JSON/HTTP/environment configuration API."""
    expected_uid: int


def unavailable():
    return Fault(503,'raw_qc_unavailable')


class _SecretTeardownUnknown(Fault):
    """Fixed internal signal only; no descriptor number or credential data."""
    def __init__(self):
        super().__init__(503,'raw_qc_unavailable')


def _require(ok):
    if not ok:raise unavailable()


def _identity(s):
    return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)


def _decode_secret(raw):
    _require(type(raw) is bytes and 1<=len(raw)<=4096)
    def pairs(items):
        value={}
        for key,item in items:
            _require(key not in value);value[key]=item
        return value
    value={}
    try:
        value=json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,
                         parse_constant=lambda _: (_ for _ in ()).throw(unavailable()))
        _require(type(value) is dict and set(value)=={'schemaVersion','purpose','password'})
        _require(type(value['schemaVersion']) is int and value['schemaVersion']==1
                 and value['purpose']=='raw_qc_worker_runtime')
        password=value['password']
        _require(type(password) is str and re.fullmatch('[A-Za-z0-9_-]{64}',password) is not None)
        return password
    except BaseException:
        raise unavailable() from None
    finally:
        if type(value) is dict:value.clear()


def _load_password(uid):
    """Fixed descriptor walk, bounded lazy read. Never log or expose the document."""
    directory=file_fd=None;raw=None;owned=[];teardown_unknown=False
    def own(fd):
        owned.append(fd)
        return fd
    def close_once(fd):
        nonlocal teardown_unknown
        # Consume ownership before close. An error may mean the numeric fd is
        # already closed/reused: never retry it. Other owned fds remain tracked.
        owned.remove(fd)
        try:os.close(fd)
        except BaseException:teardown_unknown=True
    try:
        flags=os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|os.O_NOFOLLOW
        directory=own(os.open('/',flags))
        def safe_dir(fd):
            s=os.fstat(fd)
            _require(stat.S_ISDIR(s.st_mode) and s.st_uid in (0,uid) and not s.st_mode&0o022)
            return s
        safe_dir(directory)
        for part in SECRET_PARTS[:-1]:
            child=own(os.open(part,flags,dir_fd=directory))
            safe_dir(child)
            previous=directory;directory=child
            close_once(previous)
            if teardown_unknown:raise _SecretTeardownUnknown()
        parent=safe_dir(directory)
        _require(parent.st_uid==uid and stat.S_IMODE(parent.st_mode)==0o700)
        file_fd=own(os.open(SECRET_PARTS[-1],os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory))
        before=os.fstat(file_fd)
        _require(stat.S_ISREG(before.st_mode) and before.st_uid==uid and before.st_nlink==1
                 and stat.S_IMODE(before.st_mode)==0o600 and 1<=before.st_size<=4096)
        raw=bytearray()
        while len(raw)<=4096:
            chunk=os.read(file_fd,4097-len(raw))
            if not chunk:break
            raw.extend(chunk)
        _require(len(raw)==before.st_size and len(raw)<=4096
                 and _identity(os.fstat(file_fd))==_identity(before)
                 and _identity(os.stat(SECRET_PARTS[-1],dir_fd=directory,follow_symlinks=False))==_identity(before))
        return _decode_secret(bytes(raw))
    except BaseException:
        raise unavailable() from None
    finally:
        if type(raw) is bytearray:raw[:]=b'\0'*len(raw)
        for fd in reversed(tuple(owned)):close_once(fd)
        if teardown_unknown:raise _SecretTeardownUnknown() from None


async def _connect(password):
    # Existing driver only; lazy import prevents default construction from
    # importing a driver or attempting a socket. Endpoints cannot be configured.
    from psycopg import AsyncConnection
    return await AsyncConnection.connect(host='172.19.0.2',hostaddr='172.19.0.2',port=5432,
        dbname='cyomicsv2_business_dev',user='cyomicsv2_raw_qc_worker_runtime',password=password,
        connect_timeout=2,require_auth='scram-sha-256',sslmode='disable',gssencmode='disable',autocommit=True)


def _closed(connection):
    try:return connection.closed is True
    except BaseException:return False


def _open(connection):
    try:return connection.closed is False
    except BaseException:return False


def _finish(connection):
    try:
        if not _closed(connection):connection.pgconn.finish()
    except BaseException:pass
    return _closed(connection)


class WorkerPool:
    """Dedicated event-loop/single-thread pool. Unknown connections consume slot.

    Caller must bound waiting via existing Budget. No default runtime activation.
    """
    def __init__(self,config=None):
        if config is not None:
            _require(type(config) is Config and type(config.expected_uid) is int and config.expected_uid>0)
        self._config=config;self._slots=asyncio.Semaphore(1)
        self._held=False;self._lease=None;self._closing=False;self._uncertain=False

    def _free_confirmed(self):
        self._lease=None
        if self._held:self._held=False;self._slots.release()

    async def getconn(self):
        if self._config is None or self._closing or self._uncertain:raise unavailable()
        await self._slots.acquire()
        self._held=True
        password=None;connecting=False
        try:
            _require(not self._closing and not self._uncertain)
            _require(os.name=='posix' and os.getuid()==os.geteuid()==self._config.expected_uid)
            # Names only. Never read/log any PG* environment value.
            _require(not any(key.startswith('PG') for key in os.environ))
            password=_load_password(self._config.expected_uid)
            _require(not self._closing)
            connecting=True
            connection=await _connect(password)
            self._lease=connection  # Bind before checking cancellation or close.
            _require(connection is not None)
            connecting=False
            task=asyncio.current_task()
            if self._closing or (task is not None and task.cancelling()):
                if task is not None and task.cancelling():raise asyncio.CancelledError()
                raise unavailable()
            _require(_open(connection))
            return connection
        except BaseException as error:
            if self._lease is not None:
                if _finish(self._lease):self._free_confirmed()
                else:self._uncertain=True
            elif connecting or isinstance(error,_SecretTeardownUnknown):
                # Driver did not return a usable handle. Never infer teardown or
                # schedule background discovery/retry; withhold until recovery.
                # Unknown secret-descriptor teardown likewise must not be retried.
                self._uncertain=True
            else:
                self._free_confirmed()
            if isinstance(error,asyncio.CancelledError):raise
            raise unavailable() from None
        finally:password=None

    def release(self,connection):
        _require(connection is self._lease and self._held and connection is not None)
        if _finish(connection):self._free_confirmed()
        else:
            self._uncertain=True
            raise unavailable()

    def close(self):
        self._closing=True
        if self._lease is not None:
            if _finish(self._lease):self._free_confirmed()
            else:self._uncertain=True
