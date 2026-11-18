"""Default-off Root adapter for two no-service bootstrap callbacks only.

Root injects separately reviewed scope pins, fixed roots, process specification
and the pinned existing pure Control helpers. No CLI/env/HTTP configuration,
preview activation, transport, credential loader or gate mutation exists here.
"""
from dataclasses import dataclass
import os
from pathlib import Path
import stat
import time
from types import ModuleType

LEGACY_PIN={'bytes':8726,'sha256':'75ad437aa43504ceefdc94525bdce34b5f6834ab15d61575fc0741ec4d83694f'}
CONTROL_NAME='preprocess_probe_control.py'
EXPECTED_KEYS=frozenset(('kind','epoch_id','scope_sha256','source_manifest_sha256','pid','starttick','uid'))
IDENTITY_KEYS=frozenset(('pid','starttick','uid','cwd','executable','argv','namespace_links'))
KINDS=frozenset(('preprocess_dev_sdk','preprocess_worker'))


class ProbeControlRejected(RuntimeError):
    def __init__(self):super().__init__('preprocessing_probe_control_rejected')


def require(value):
    if not value:raise ProbeControlRejected()


@dataclass(frozen=True,slots=True)
class Config:
    """Trusted Root source construction only. No actual approval is bundled.

    Root provisions both roots; this adapter never creates a directory. The
    process spec JSON has exactly uid/cwd/executable/argv. Namespace, PID and
    starttick are checked against the pinned scope and actual current process.
    """
    approval_path:Path
    approval_bytes:int
    approval_sha256:str
    source_root:Path
    source_manifest_sha256:str
    registration_root:Path
    epoch_id:str
    registration_id:str
    kind:str
    process_spec_json:bytes


class ProbeControl:
    def __init__(self,config=None,*,legacy_ops=None):
        self.config=config;self.legacy=legacy_ops
        self._state='off' if config is None else 'new'
        self._expected=None;self._identity=None;self._mono=None;self._deadline=None

    def _ready(self):
        require(type(self.config) is Config and type(self.legacy) is ModuleType)
        from . import preprocess_contract as c
        cfg=self.config
        require(cfg.kind in KINDS and c._canonical_uuid(cfg.epoch_id) and c._canonical_uuid(cfg.registration_id))
        require(type(cfg.approval_bytes) is int and 1<=cfg.approval_bytes<=32768 and c.hashed(cfg.approval_sha256) and c.hashed(cfg.source_manifest_sha256))
        for path in (cfg.approval_path,cfg.source_root,cfg.registration_root):
            require(isinstance(path,Path) and path.is_absolute() and '..' not in path.parts)
        require(cfg.approval_path.name=='Root-approval.json' and cfg.source_root.name=='cyomics_projects')
        registration=cfg.registration_root.lstat()
        require(stat.S_ISDIR(registration.st_mode) and registration.st_uid==1000 and stat.S_IMODE(registration.st_mode)==0o700
            and not any(path.is_symlink() for path in (cfg.registration_root,*cfg.registration_root.parents)))
        require(not cfg.approval_path.is_relative_to(cfg.registration_root) and not cfg.source_root.is_relative_to(cfg.registration_root))
        require(type(cfg.process_spec_json) is bytes)
        spec=c.document(cfg.process_spec_json,8192);c.exact(spec,('uid','cwd','executable','argv'))
        require(type(spec['uid']) is int and spec['uid']==1000 and os.getuid()==os.geteuid()==1000)
        require(all(type(spec[k]) is str and Path(spec[k]).is_absolute() for k in ('cwd','executable')))
        require(type(spec['argv']) is list and 1<=len(spec['argv'])<=32 and all(type(x) is str and 0<len(x)<=4096 and '\0' not in x for x in spec['argv']))
        source=Path(self.legacy.__file__)
        for name in ('public_json','verify_file','identity','pin'):
            function=getattr(self.legacy,name,None)
            require(callable(function) and getattr(function,'__code__',None) is not None and Path(function.__code__.co_filename)==source)
        # These three helpers are Root-injected trusted code; no legacy start,
        # scope, specification, process registration or service check is called.
        self.legacy.verify_file(source,LEGACY_PIN)
        return c,spec

    def _validate(self,expected,scope_argument=None):
        c,spec=self._ready();cfg=self.config
        c.exact(expected,EXPECTED_KEYS)
        require(expected['kind']==cfg.kind and expected['epoch_id']==cfg.epoch_id
            and expected['scope_sha256']==cfg.approval_sha256 and expected['source_manifest_sha256']==cfg.source_manifest_sha256)
        require(type(expected['pid']) is int and expected['pid']>1 and type(expected['starttick']) is int and expected['starttick']>0
            and type(expected['uid']) is int and expected['uid']==1000 and expected['pid']==os.getpid())
        parent=cfg.approval_path.parent.lstat();before=cfg.approval_path.lstat()
        require(stat.S_ISDIR(parent.st_mode) and parent.st_uid==1000 and stat.S_IMODE(parent.st_mode)==0o700)
        require(stat.S_ISREG(before.st_mode) and before.st_uid==1000 and before.st_nlink==1 and stat.S_IMODE(before.st_mode)==0o600 and before.st_size==cfg.approval_bytes)
        value,raw=self.legacy.public_json(cfg.approval_path)
        def signature(info):return (info.st_dev,info.st_ino,info.st_uid,info.st_mode,info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
        require(signature(before)==signature(cfg.approval_path.lstat()) and self.legacy.pin(raw)=={'bytes':cfg.approval_bytes,'sha256':cfg.approval_sha256})
        from .preprocess_dev_probe import validate_scope
        scope=validate_scope(raw)
        require(scope==value and (scope_argument is None or c.canonical(scope_argument)==c.canonical(scope)))
        require(scope['kind']==cfg.kind and scope['epoch_id']==cfg.epoch_id and scope['registration_id']==cfg.registration_id
            and c.digest(scope['source_manifest'])==cfg.source_manifest_sha256 and CONTROL_NAME in scope['source_manifest'])
        for name,pin in scope['source_manifest'].items():
            file=cfg.source_root/name;info=file.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==1000 and not info.st_mode&0o022)
            self.legacy.verify_file(file,pin)
        self.legacy.verify_file(Path(__file__),scope['source_manifest'][CONTROL_NAME])
        identity=self.legacy.identity(os.getpid());c.exact(identity,IDENTITY_KEYS)
        require(all(identity[k]==expected[k] for k in ('pid','starttick','uid')) and all(identity[k]==v for k,v in spec.items())
            and identity['namespace_links']==scope['expected_namespaces'])
        require(self.legacy.identity(os.getpid())==identity)
        self._time(scope)
        return c,scope,identity

    def _time(self,scope):
        now=int(time.time()*1000);mono=time.monotonic()
        require(scope['issued_at_ms']<=now<scope['expires_at_ms'])
        if self._mono is not None:require(self._mono<=mono<self._deadline)
        return now,mono

    def _register_once(self,data):
        # Same O_EXCL/no-follow ownership pattern as legacy save_process, with
        # file+directory fsync, readback and every owned close checked before ACK.
        # Any existing complete OR partial record consumes this epoch forever.
        # File bytes are not a standing approval and cannot authorize a restart.
        from .raw_qc_artifacts import open_root
        root_fd=file_fd=None;unknown=False
        try:
            root_fd=open_root(self.config.registration_root)
            require(stat.S_IMODE(os.fstat(root_fd).st_mode)==0o700)
            name=self.config.epoch_id+'.probe-process.json'
            file_fd=os.open(name,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=root_fd)
            offset=0
            while offset<len(data):
                count=os.write(file_fd,data[offset:]);require(type(count) is int and 0<count<=len(data)-offset);offset+=count
            os.fsync(file_fd)
            info=os.fstat(file_fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid==1000 and info.st_nlink==1 and stat.S_IMODE(info.st_mode)==0o600 and info.st_size==len(data))
            require(os.pread(file_fd,len(data)+1,0)==data and os.stat(name,dir_fd=root_fd,follow_symlinks=False).st_ino==info.st_ino)
            os.fsync(root_fd)
        finally:
            for fd in (file_fd,root_fd):
                if fd is not None:
                    try:os.close(fd)
                    except BaseException:unknown=True
            if unknown:raise ProbeControlRejected() from None

    def verify_probe_start(self,expected,scope):
        try:
            require(self._state=='new');self._state='checking'
            c,value,identity=self._validate(expected,scope)
            # Occupied epochs (even partial failed writes) never reauthorize.
            marker=self.config.registration_root/(self.config.epoch_id+'.probe-process.json')
            try:marker.lstat()
            except FileNotFoundError:pass
            else:raise ProbeControlRejected()
            now,mono=self._time(value);self._mono=mono;self._deadline=mono+(value['expires_at_ms']-now)/1000
            self._expected=c.canonical(expected);self._identity=c.canonical(identity);self._state='verified'
            return {**c.document(self._expected),'verified':True}
        except BaseException:
            self._state='unknown';self._expected=None
            raise ProbeControlRejected() from None

    def save_probe_process(self,expected):
        try:
            require(self._state=='verified');self._state='consuming'
            c,scope,identity=self._validate(expected)
            require(c.canonical(expected)==self._expected and c.canonical(identity)==self._identity)
            now,_=self._time(scope)
            data=c.canonical({'schemaVersion':1,'kind':'preprocessing_probe_identity_once','epoch_id':self.config.epoch_id,
                'registration_id':self.config.registration_id,'expected':expected,'identity':identity,'recorded_at_ms':now,
                'authorization':'only_successful_callback_ack; never_replay_this_file'})
            require(len(data)<=32768);self._register_once(data)
            c,final_scope,final_identity=self._validate(expected)
            require(c.canonical(final_identity)==self._identity and c.canonical(final_scope)==c.canonical(scope))
            self._time(scope);self._state='consumed'
            return {**c.document(self._expected),'recorded':True}
        except BaseException:
            self._state='unknown';self._expected=None
            raise ProbeControlRejected() from None
