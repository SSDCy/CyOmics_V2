"""Default-off, fixed-profile Owner read of operator-recorded QC distributions.

No generation, publisher write, R execution, queue, credential read or static route.
The parent repository remains the DB Owner/source/full11 authority.
"""
from contextlib import ExitStack
import hashlib, os
from .settings import Fault
from .validation import canonical_uuid, strict_json
from . import preprocess_repository as saved, preprocess_contract as pc
from .sample_repository import SampleDesignRepository
from .sample_persistence import response_value as design_value
from .volcano_owner_read import Config, FileProvider as SecureFiles, identity, prepare as secure_prepare

CONTRACT='qc_distribution_owner_read_v0.1'
REGISTERED='qc_distribution_registered_v0.1'
MAX_FILE=16777216; MAX_SVG=4194304; MAX_TOTAL=33554432; MAX_DESCRIPTOR=65536; MAX_REGISTRATION=131072
SECURITY={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
          'Content-Security-Policy':"default-src 'none'; sandbox"}
MEDIA={n:'image/svg+xml' for n in ('boxplot.svg','density.svg')}
MEDIA.update({n:'text/tab-separated-values; charset=utf-8' for n in ('boxplot-statistics.tsv','density-statistics.tsv',
    'plot-long-data.tsv','sample-counts.tsv','group-label-map.tsv')})
MEDIA.update({n:'text/plain; charset=utf-8' for n in ('parameters.R.txt','R-session-info.txt','distributions.R')})
NAMES=tuple(MEDIA)
PRIVATE_NAME='full-provenance.rds'
FILES=(*NAMES,PRIVATE_NAME)
KEYS=('schemaVersion','contract','status','project_id','created_by','processed_dataset_version_id',
      'parent','design','source','inputs','generation','artifacts')
PROJECT='47278eaf-c156-4b66-b60a-287ca4b23805'
VERSION='2fc0a8e6-ef3d-4afe-a15c-f27043b00227'
META_SHA='757806cef01cc885085c42caf7b1ed3defc27b0aeeea7a7ebb85a7e5eb39e2af'
DESIGN_VERSION='4cf9e303-46a1-4c04-969c-6400edc5490c'
DESIGN_REQUEST='f78be2cf-6c01-4f2d-948a-e5ff57c174f3'
DESIGN_SHA='46c8037ac1162406de11820bbfb79488adea334614fbe06e692ecc3c24f7e69f'
SCRIPT={'name':'distributions.R','bytes':12651,'sha256':'4c214b6140e0fb4540286e14d118cc910f18d5c9b9856c45a4a501fec8ae23e9'}
RECORD_SHA='3321cc57c9618801023b913d2d075785a1d08729df605addbeca22293b9f851d'
# Exact prior reviewed actual-A002 outputs, not arbitrary client or audit paths.
OUTPUT_PINS={
 'parameters.R.txt':(1379,'f353d9ebc19f2f8ce5b5b4fd567871119ab555a5f186fbe2b6025cbb3f53c5f9'),
 'full-provenance.rds':(4389,'4ff74fd96b93d769a01006bda6f7c9cfe4b88c7e8da7dbcec241a862f3219312'),
 'boxplot-statistics.tsv':(23792,'e9ad6257603dcabe563ec7288d3a7c8cd9469de5b9ecf92b65a59e7ab753bfaa'),
 'group-label-map.tsv':(230,'cd3adea0b10eeabdd55b29b3288c9825ee6015f7b7995a5104a52ae8630a907b'),
 'R-session-info.txt':(1241,'218c2f9cc8de7bebd23d334b5ed00dae9c0137a05e02524c699caa409b185c60'),
 'density.svg':(451144,'9baca09e0c378710604d35c17d9e5f283711d114b03e44e75228f0be3876b1dc'),
 'density-statistics.tsv':(2972006,'166078a007ab84abae3ca25feb3c5e0ce5050597080489723124c399dd3899a4'),
 'boxplot.svg':(480119,'a50e428507137a35dc2e3aefcb7dbb52f3baf4788cf044e8ba3b77104ae2f68b'),
 'plot-long-data.tsv':(6288202,'cb902496824aa9f0c7fc2ae4274b60c1beb0040b189c49d43f5273381bb07134'),
 'sample-counts.tsv':(3280,'0b760e61814d1533f0fa1f19d4d15ca410c3a1e9d7630f4eab7c9b8373cb36e1')}

def fail(code='qc_distribution_integrity_failed',status=503):raise Fault(status,code)
def need(ok):
    if not ok:fail()
def canonical(v):return saved.canonical(v).encode('utf8')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def prepare(config=None):
    try:return secure_prepare(config)
    except Fault:fail('qc_distribution_unavailable')
def output_manifest():
    return [{'name':n,'bytes':OUTPUT_PINS[n][0],'sha256':OUTPUT_PINS[n][1],
             'media_type':MEDIA.get(n,'application/octet-stream')} for n in sorted(OUTPUT_PINS)]
def artifact_pins():
    return [dict(SCRIPT if n=='distributions.R' else {'name':n,'bytes':OUTPUT_PINS[n][0],'sha256':OUTPUT_PINS[n][1]},media_type=MEDIA[n]) for n in NAMES]
def generation():
    return {'mode':'operator_recorded','controlled_job':False,'record_sha256':RECORD_SHA,'script':SCRIPT,
            'r_session':{'name':'R-session-info.txt','bytes':OUTPUT_PINS['R-session-info.txt'][0],'sha256':OUTPUT_PINS['R-session-info.txt'][1]},'output_manifest':output_manifest()}

def checked_registration(raw,parent,subject,project,version):
    need(type(raw) is bytes and 0<len(raw)<=MAX_REGISTRATION)
    try:value=strict_json(raw)
    except Exception:fail()
    need(type(value) is dict and set(value)==set(KEYS) and canonical(value)==raw
        and type(value['schemaVersion']) is int and value['schemaVersion']==1 and value['contract']==REGISTERED
        and value['status']=='ready' and (value['created_by'],value['project_id'],value['processed_dataset_version_id'])==(subject,project,version))
    expected_parent={'metadata':parent,'metadata_sha256':sha(canonical(parent)),
                     'manifest':parent['artifacts'],'manifest_sha256':parent['manifest_sha256']}
    need(canonical(value['parent'])==canonical(expected_parent) and expected_parent['metadata_sha256']==META_SHA
        and sha(canonical(parent['artifacts']))==parent['manifest_sha256']
        and canonical(value['source'])==canonical(parent['source']) and canonical(value['inputs'])==canonical(parent['artifacts'])
        and canonical(value['generation'])==canonical(generation()) and canonical(value['artifacts'])==canonical(artifact_pins()))
    design=value['design'];need(type(design) is dict and set(design)=={'envelope','design_sha256'} and design['design_sha256']==DESIGN_SHA)
    envelope=design_value(design['envelope'],project,parent['parent_dataset_version_id'],parent['quant_family'])
    record=envelope['record']
    need(record is not None and record['sample_design_version_id']==DESIGN_VERSION and record['request_id']==DESIGN_REQUEST
        and record['design_sha256']==DESIGN_SHA and sha(canonical(record['design']))==DESIGN_SHA)
    need(sum(p['bytes'] for p in artifact_pins())+OUTPUT_PINS[PRIVATE_NAME][0]<=MAX_TOTAL)
    return value

class FileProvider(SecureFiles):
    """Reuse original openat/no-follow/UID/stat immutable-file guards unchanged."""
    def read(self,parent,subject,project,version,check,*,metadata_only=False,expected=None):
        need(canonical_uuid(version))
        try:
            with ExitStack() as stack:
                root=self._root(stack,check);root_id=self._directory(root,private=True)
                folder=os.open(version,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=root);stack.callback(os.close,folder)
                folder_id=self._directory(folder,private=True)
                raw,record=self._file(stack,folder,'metadata.json',MAX_REGISTRATION,check);opened=[record];bodies={}
                need(set(os.listdir(folder))==set(FILES)|{'metadata.json'})
                value=checked_registration(raw,parent,subject,project,version)
                if expected is not None:need(raw==expected)
                if not metadata_only:
                    pins={p['name']:p for p in value['artifacts']}
                    pins[PRIVATE_NAME]={'bytes':OUTPUT_PINS[PRIVATE_NAME][0],'sha256':OUTPUT_PINS[PRIVATE_NAME][1]}
                    total=0
                    for name in FILES:
                        body,record=self._file(stack,folder,name,MAX_SVG if name.endswith('.svg') else MAX_FILE,check)
                        need(len(body)==pins[name]['bytes'] and sha(body)==pins[name]['sha256'])
                        total+=len(body);need(total<=MAX_TOTAL);opened.append(record);bodies[name]=body
                for name,fd,before in opened:
                    check();need(identity(os.fstat(fd))==before and identity(os.stat(name,dir_fd=folder,follow_symlinks=False))==before)
                need(self._directory(root,private=True)==root_id and self._directory(folder,private=True)==folder_id
                    and identity(os.stat(version,dir_fd=root,follow_symlinks=False))==folder_id)
                need(self._directory(self._root(stack,check),private=True)==root_id);check()
                return raw,value,bodies
        except TimeoutError:raise
        except FileNotFoundError:fail('qc_distribution_not_ready',404)
        except Fault as error:
            if error.code.startswith('qc_distribution_'):raise
            fail()
        except Exception:fail()

class OwnerReader:
    def __init__(self,repository,design_reader,provider):
        need(type(repository) is saved.PreprocessRepository and type(design_reader) is SampleDesignRepository
            and type(provider) is FileProvider and repository.enabled and repository.pool is not None and repository.pool is design_reader.pool)
        self.repository=repository;self.designs=design_reader;self.provider=provider
    async def read(self,subject,project,version,budget,name=None):
        budget.check()
        parent=await self.repository.read_version(subject,project,version,budget)
        pc.version_value(parent,project=project,version=version)
        need(parent['created_by']==subject)
        params=parent['parameters']
        if params['normalization']!='none' or params['imputation']!='none':fail('qc_distribution_unsupported_parent',409)
        if (project,version)!=(PROJECT,VERSION):fail('qc_distribution_not_ready',404)
        raw,registered,_=self.provider.read(parent,subject,project,version,budget.check,metadata_only=True)
        envelope=await self.designs.read(subject,project,parent['parent_dataset_version_id'],parent['quant_family'],budget,request_id=DESIGN_REQUEST)
        need(canonical(envelope)==canonical(registered['design']['envelope']))
        _,_,bodies=self.provider.read(parent,subject,project,version,budget.check,expected=raw)
        value=dict(registered,contract=CONTRACT)
        need(len(canonical(value))<=MAX_DESCRIPTOR);budget.check()
        if name is not None:need(name in MEDIA);return value,bodies[name]
        return value,None
