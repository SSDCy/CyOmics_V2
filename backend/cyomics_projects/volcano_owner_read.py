"""Default-off Owner reader and fixed-root immutable file provider. No writes/R."""
from contextlib import ExitStack
from dataclasses import dataclass
import os,stat
from pathlib import Path
from .settings import Fault
from .validation import canonical_uuid,strict_json
from . import de_classification_repository as saved
from .differential_worker_once import ResultReader

MAX_FILE=27262976
MAX_TOTAL=134217728
MAX_DESCRIPTOR=32768
MEDIA={'R-session.txt':'text/plain;charset=utf-8','volcano-audit.tsv':'text/tab-separated-values;charset=utf-8',
    'volcano-points.tsv':'text/tab-separated-values;charset=utf-8','volcano.png':'image/png','volcano.svg':'image/svg+xml'}
NAMES=tuple(MEDIA)
INPUTS=('classification-audit.tsv','classification-parameters.tsv','classification.tsv')
CONTEXT=('project_id','actor_id','source_result_id','source_metadata_sha256','source_manifest_sha256','derived_version_id',
    'derived_metadata_sha256','derived_manifest_sha256','source_binding_sha256','parameters_sha256')
DESCRIPTOR=('schemaVersion','contract','project_id','created_by','source_result_id','derived_version_id','source_metadata_sha256',
    'source_manifest_sha256','derived_metadata_sha256','derived_manifest_sha256','source_binding_sha256','parameters_sha256','artifacts')
PRIVATE_KEYS=frozenset(DESCRIPTOR)|{'render_source_derived_id','render_source_metadata_sha256','render_script','inputs'}
SCRIPT={'bytes':13931,'sha256':'5062b985000fb2cd44c93f8f0d760000bf671f3e0226ee70c773bf3b1f917fe2'}

def fail(code='r_volcano_unavailable',status=503):raise Fault(status,code)
def need(value,code='r_volcano_unavailable',status=503):
    if not value:fail(code,status)
def hashed(value):return saved.admission.pc.hashed(value)
def identity(s):return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)

@dataclass(frozen=True,slots=True)
class Config:
    enabled:bool=False
    root:Path|None=None
    expected_uid:int=1000

def prepare(config=None):
    if config is None:return None
    need(type(config) is Config and type(config.enabled) is bool)
    if not config.enabled:return None
    need(isinstance(config.root,Path) and config.root.is_absolute() and '..' not in config.root.parts
        and str(config.root)==os.path.normpath(str(config.root)) and len(config.root.parts)<=64
        and type(config.expected_uid) is int and config.expected_uid==1000)
    return config # No stat/open/default root, connection or file IO here.

def input_pins(meta):
    pins={x['name']:x for x in meta['artifacts']}
    need(all(name in pins for name in INPUTS))
    return [{k:pins[name][k] for k in ('name','bytes','sha256')} for name in INPUTS]

def checked_registration(raw,context,inputs):
    need(type(raw) is bytes and 0<len(raw)<=65536 and not raw.startswith(b'\xef\xbb\xbf'))
    value=strict_json(raw)
    need(type(value) is dict and set(value)==PRIVATE_KEYS and type(value['schemaVersion']) is int and value['schemaVersion']==1
        and value['contract']=='r_volcano_saved_v0.1' and saved.canonical(value).encode('utf8')==raw)
    for key in CONTEXT:
        target='created_by' if key=='actor_id' else key
        need(value[target]==context[key])
    need(canonical_uuid(value['render_source_derived_id']) and hashed(value['render_source_metadata_sha256']) and value['render_script']==SCRIPT)
    need(type(value['inputs']) is list and len(value['inputs'])==3 and value['inputs']==inputs)
    for name,pin in zip(INPUTS,value['inputs']):
        need(type(pin) is dict and set(pin)=={'name','bytes','sha256'} and pin['name']==name
            and type(pin['bytes']) is int and 0<pin['bytes']<=MAX_FILE and hashed(pin['sha256']))
    pins=value['artifacts'];need(type(pins) is list and len(pins)==5)
    for name,pin in zip(NAMES,pins):
        need(type(pin) is dict and set(pin)=={'name','bytes','sha256','media_type'} and pin['name']==name and pin['media_type']==MEDIA[name]
            and type(pin['bytes']) is int and 0<pin['bytes']<=MAX_FILE and hashed(pin['sha256']))
    need(sum(p['bytes'] for p in pins)<=MAX_TOTAL)
    return value

@dataclass(frozen=True,slots=True)
class Registration:
    raw:bytes
    context:tuple
    inputs:tuple
    def value(self):return strict_json(self.raw)

class FileProvider:
    def __init__(self,config):self.config=prepare(config);need(self.config is not None)
    def _directory(self,fd,*,private=False,owners=None):
        s=os.fstat(fd)
        need(stat.S_ISDIR(s.st_mode) and not s.st_mode&0o022)
        if private:need(s.st_uid==self.config.expected_uid and stat.S_IMODE(s.st_mode) in (0o500,0o700))
        else:need(s.st_uid in owners)
        return identity(s)
    def _root(self,stack,check):
        check();flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC
        fd=os.open('/',flags);stack.callback(os.close,fd)
        owners={os.fstat(fd).st_uid,self.config.expected_uid} # Existing root-anchor mapped-owner convention.
        self._directory(fd,owners=owners)
        for part in self.config.root.parts[1:]:
            check();fd=os.open(part,flags,dir_fd=fd);stack.callback(os.close,fd);self._directory(fd,owners=owners)
        self._directory(fd,private=True);return fd
    def _file(self,stack,folder,name,cap,check):
        check();fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,dir_fd=folder);stack.callback(os.close,fd)
        before=os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid==self.config.expected_uid and before.st_nlink==1
            and stat.S_IMODE(before.st_mode) in (0o400,0o444) and 0<before.st_size<=cap)
        chunks=[];size=0
        while True:
            check();chunk=os.pread(fd,65536,size)
            if not chunk:break
            size+=len(chunk);need(size<=cap and size<=before.st_size);chunks.append(chunk)
        raw=b''.join(chunks);need(size==before.st_size and identity(os.fstat(fd))==identity(before)
            and identity(os.stat(name,dir_fd=folder,follow_symlinks=False))==identity(before))
        return raw,(name,fd,identity(before))
    def _read(self,version,check,expected=None):
        need(canonical_uuid(version))
        try:
            with ExitStack() as stack:
                parent=self._root(stack,check);parent_id=self._directory(parent,private=True)
                folder=os.open(version,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent);stack.callback(os.close,folder)
                folder_id=self._directory(folder,private=True)
                need(set(os.listdir(folder))==set(NAMES)|{'metadata.json'})
                raw,record=self._file(stack,folder,'metadata.json',65536,check);opened=[record];bodies={}
                if expected is not None:
                    need(raw==expected)
                    pins=strict_json(raw)['artifacts'];total=0
                    for pin in pins:
                        body,record=self._file(stack,folder,pin['name'],MAX_FILE,check)
                        need(len(body)==pin['bytes'] and saved.c.sha(body)==pin['sha256'])
                        total+=len(body);need(total<=MAX_TOTAL);opened.append(record);bodies[pin['name']]=body
                for name,fd,before in opened:
                    check();need(identity(os.fstat(fd))==before and identity(os.stat(name,dir_fd=folder,follow_symlinks=False))==before)
                need(self._directory(parent,private=True)==parent_id and self._directory(folder,private=True)==folder_id
                    and identity(os.stat(version,dir_fd=parent,follow_symlinks=False))==folder_id)
                need(self._directory(self._root(stack,check),private=True)==parent_id);check()
                return raw,bodies
        except FileNotFoundError:fail('not_found',404)
        except Fault:raise
        except Exception:fail()
    def registration(self,context,inputs,check):
        raw,_=self._read(context['derived_version_id'],check)
        checked_registration(raw,context,inputs)
        return Registration(raw,tuple(context.items()),tuple(tuple(p.items()) for p in inputs))
    def artifacts(self,registration,check):
        need(type(registration) is Registration)
        context=dict(registration.context);inputs=[dict(p) for p in registration.inputs]
        checked_registration(registration.raw,context,inputs)
        _,bodies=self._read(context['derived_version_id'],check,registration.raw)
        return bodies

class OwnerReader:
    def __init__(self,repository,parent_reader,provider):
        need(type(repository) is saved.ClassificationRepository and type(parent_reader) is ResultReader and type(provider) is FileProvider
            and repository.owner.pool is not None and repository.owner.pool is parent_reader.owner.pool)
        self.repository=repository;self.parent=parent_reader;self.provider=provider
    async def read(self,subject,project,version,budget,name=None):
        budget.check()
        # Mandatory original Owner/version/parent proof BEFORE even registration lookup.
        meta=await self.repository.read_version(subject,project,version,budget);saved.validate_dto(meta)
        need((meta['created_by'],meta['project_id'],meta['derived_version_id'])==(subject,project,version))
        parent=await self.parent.read(subject,project,budget,result_id=meta['source_result_id'])
        need((parent['created_by'],parent['project_id'],parent['result_id'])==(subject,project,meta['source_result_id'])
            and parent['manifest_sha256']==meta['source_manifest_sha256']
            and meta['source_binding']['source_metadata_sha256']==saved.digest(parent))
        if meta['status']!='CLASSIFIED':fail('not_found',404)
        context={'project_id':project,'actor_id':subject,'source_result_id':meta['source_result_id'],
            'source_metadata_sha256':saved.digest(parent),'source_manifest_sha256':parent['manifest_sha256'],'derived_version_id':version,
            'derived_metadata_sha256':saved.digest(meta),'derived_manifest_sha256':meta['manifest_sha256'],
            'source_binding_sha256':meta['source_binding_sha256'],'parameters_sha256':meta['parameters_sha256']}
        inputs=input_pins(meta);registration=self.provider.registration(context,inputs,budget.check);registered=registration.value()
        origin=meta if registered['render_source_derived_id']==version else await self.repository.read_version(subject,project,registered['render_source_derived_id'],budget)
        saved.validate_dto(origin)
        need((origin['created_by'],origin['project_id'])==(subject,project)
            and all(origin[k]==meta[k] for k in ('source_result_id','source_manifest_sha256','source_binding_sha256','parameters_sha256','parameters','core','status'))
            and input_pins(origin)==inputs and saved.digest(origin)==registered['render_source_metadata_sha256'])
        bodies=self.provider.artifacts(registration,budget.check);budget.check()
        descriptor={k:registered[k] for k in DESCRIPTOR};descriptor['contract']='r_volcano_view_v0.1'
        need(set(descriptor)==set(DESCRIPTOR) and len(saved.canonical(descriptor).encode('utf8'))<=MAX_DESCRIPTOR)
        if name is not None:need(name in MEDIA);return descriptor,bodies[name]
        return descriptor,None
