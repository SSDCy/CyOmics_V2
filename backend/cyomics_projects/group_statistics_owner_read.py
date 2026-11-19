"""Default-off, fixed-profile Owner read of operator-recorded group statistics and raw CV.

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

CONTRACT='group_statistics_owner_read_v0.1'
REGISTERED='group_statistics_registered_v0.1'
MAX_FILE=16777216; MAX_SVG=4194304; MAX_TOTAL=33554432; MAX_DESCRIPTOR=65536; MAX_REGISTRATION=131072
SECURITY={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
          'Content-Security-Policy':"default-src 'none'; sandbox"}
MEDIA={'group-statistics.tsv': 'text/tab-separated-values; charset=utf-8', 'groups.tsv': 'text/tab-separated-values; charset=utf-8', 'sample-design.tsv': 'text/tab-separated-values; charset=utf-8', 'parameters.R.txt': 'text/plain; charset=utf-8', 'R-session-info.txt': 'text/plain; charset=utf-8', 'group_stats.R': 'text/plain; charset=utf-8'}
NAMES=tuple(MEDIA)
PRIVATE_NAME='full-provenance.rds'
FILES=(*NAMES,PRIVATE_NAME)
KEYS=('schemaVersion','contract','status','project_id','created_by','processed_dataset_version_id',
      'parent','design','source','inputs','generation','artifacts','analysis')
PROJECT='47278eaf-c156-4b66-b60a-287ca4b23805'
VERSION='2fc0a8e6-ef3d-4afe-a15c-f27043b00227'
META_SHA='757806cef01cc885085c42caf7b1ed3defc27b0aeeea7a7ebb85a7e5eb39e2af'
DESIGN_VERSION='4cf9e303-46a1-4c04-969c-6400edc5490c'
DESIGN_REQUEST='f78be2cf-6c01-4f2d-948a-e5ff57c174f3'
DESIGN_SHA='46c8037ac1162406de11820bbfb79488adea334614fbe06e692ecc3c24f7e69f'
SCRIPT={'name': 'group_stats.R', 'bytes': 10705, 'sha256': '46f343b3bf6cb123af9e68e1600507dfde746b5a9d0f8cbd7cb1782dbad202f7'}
RECORD_SHA='023e5c5b40af682e48fd7f9e1f0962f86751ebea6e0ca97c3cbb6798263626ce'
# Exact prior reviewed group-statistics actual-A001 outputs, not arbitrary client or audit paths.
OUTPUT_PINS={'group-statistics.tsv': (5481244, '6792410a7ab3f92506151399dd86a63c0f1ee6a46ad4259c0f2cf21cafb96e6e'), 'parameters.R.txt': (1386, '61eae957cec0bd312de2f00127f05ad5030c3ce3f7ba5b3ed12152e7d5071d76'), 'full-provenance.rds': (4401, '46c9d6b92dbe2bff050d3bf17cce08b0327073af833b58d0e82a15263a627c8f'), 'groups.tsv': (494, 'b655bdf02f38876dc87173929fcc5c051d7e34dd2fb3cc0280ea33083c6c172f'), 'R-session-info.txt': (789, 'a182cc85ba39efb3e966d20cc5a9166a1cc3ae39d9fe54a6ae6a08c067b3ddba'), 'sample-design.tsv': (2506, '58aa2fd580e1b1302c57f3b0c989aec94ad5c78d6c39c792d9860dd104469098')}
ANALYSIS={'source_rows': 2252, 'samples': 21, 'groups': 7, 'result_records': 15764, 'CV_status_counts': {'estimable': 7916, 'all_missing': 6456, 'insufficient_valid_n': 1392}, 'raw_scale': 'observed_positive_intensity', 'raw_mean_function': 'base::mean', 'raw_median_function': 'stats::median', 'raw_SD_function': 'stats::sd', 'raw_SD_denominator': 'n_valid-1', 'cv_raw_formula': 'raw_sample_SD/raw_mean', 'rsd_percent_raw_formula': '100*cv_raw', 'log2_statistics_scale': 'saved_log2', 'zero_blank_policy': 'original_zero_and_blank_to_NA', 'imputation': 'none', 'normalization': 'none', 'insufficient_valid_n_policy': 'n_valid<2_SD_CV_RSD_NA', 'all_missing_policy': 'all_statistics_NA_never_zero', 'sample_exclusion': False, 'biological_repeat_consistency_passed': False, 'differential_significance_claimed': False, 'analysis_ready': False}

def fail(code='group_statistics_integrity_failed',status=503):raise Fault(status,code)
def need(ok):
    if not ok:fail()
def canonical(v):return saved.canonical(v).encode('utf8')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def prepare(config=None):
    try:return secure_prepare(config)
    except Fault:fail('group_statistics_unavailable')
def output_manifest():
    return [{'name':n,'bytes':OUTPUT_PINS[n][0],'sha256':OUTPUT_PINS[n][1],
             'media_type':MEDIA.get(n,'application/octet-stream')} for n in sorted(OUTPUT_PINS)]
def artifact_pins():
    return [dict(SCRIPT if n=='group_stats.R' else {'name':n,'bytes':OUTPUT_PINS[n][0],'sha256':OUTPUT_PINS[n][1]},media_type=MEDIA[n]) for n in NAMES]
def generation():
    return {'mode':'operator_recorded','controlled_job':False,'record_sha256':RECORD_SHA,'script':SCRIPT,
            'r_session':{'name':'R-session-info.txt','bytes':OUTPUT_PINS['R-session-info.txt'][0],'sha256':OUTPUT_PINS['R-session-info.txt'][1]},'output_manifest':output_manifest()}

def checked_registration(raw,parent,subject,project,version):
    need(type(raw) is bytes and 0<len(raw)<=MAX_REGISTRATION)
    try:value=strict_json(raw)
    except Exception:fail()
    need(type(value) is dict and set(value)==set(KEYS) and canonical(value)==raw
        and type(value['schemaVersion']) is int and value['schemaVersion']==1 and value['contract']==REGISTERED
        and canonical(value['analysis'])==canonical(ANALYSIS) and value['status']=='ready' and (value['created_by'],value['project_id'],value['processed_dataset_version_id'])==(subject,project,version))
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
        except FileNotFoundError:fail('group_statistics_not_ready',404)
        except Fault as error:
            if error.code.startswith('group_statistics_'):raise
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
        need(type(parent) is dict and parent.get('created_by')==subject and type(parent.get('parameters')) is dict)
        params=parent['parameters']
        if params.get('normalization')!='none' or params.get('imputation')!='none':fail('group_statistics_unsupported_parent',409)
        pc.version_value(parent,project=project,version=version)
        if (project,version)!=(PROJECT,VERSION):fail('group_statistics_not_ready',404)
        raw,registered,_=self.provider.read(parent,subject,project,version,budget.check,metadata_only=True)
        envelope=await self.designs.read(subject,project,parent['parent_dataset_version_id'],parent['quant_family'],budget,request_id=DESIGN_REQUEST)
        need(canonical(envelope)==canonical(registered['design']['envelope']))
        _,_,bodies=self.provider.read(parent,subject,project,version,budget.check,expected=raw)
        value=dict(registered,contract=CONTRACT)
        need(len(canonical(value))<=MAX_DESCRIPTOR);budget.check()
        if name is not None:need(name in MEDIA);return value,bodies[name]
        return value,None
