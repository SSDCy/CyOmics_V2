"""Default-off, fixed-profile Owner read of operator-recorded PCA and sample correlation.

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

CONTRACT='qc_pca_correlation_owner_read_v0.1'
REGISTERED='qc_pca_correlation_registered_v0.1'
MAX_FILE=16777216; MAX_SVG=4194304; MAX_TOTAL=33554432; MAX_DESCRIPTOR=65536; MAX_REGISTRATION=131072
SECURITY={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
          'Content-Security-Policy':"default-src 'none'; sandbox"}
MEDIA={'PCA.svg': 'image/svg+xml', 'correlation.svg': 'image/svg+xml', 'PCA-center.tsv': 'text/tab-separated-values; charset=utf-8', 'PCA-loadings.tsv': 'text/tab-separated-values; charset=utf-8', 'PCA-row-audit.tsv': 'text/tab-separated-values; charset=utf-8', 'PCA-scores.tsv': 'text/tab-separated-values; charset=utf-8', 'PCA-selected-source.tsv': 'text/tab-separated-values; charset=utf-8', 'PCA-variance.tsv': 'text/tab-separated-values; charset=utf-8', 'correlation-matrix.tsv': 'text/tab-separated-values; charset=utf-8', 'correlation-paired-n.tsv': 'text/tab-separated-values; charset=utf-8', 'correlation-pairs.tsv': 'text/tab-separated-values; charset=utf-8', 'correlation-reasons.tsv': 'text/tab-separated-values; charset=utf-8', 'full-saved-matrix.tsv': 'text/tab-separated-values; charset=utf-8', 'sample-design.tsv': 'text/tab-separated-values; charset=utf-8', 'sample-map.tsv': 'text/tab-separated-values; charset=utf-8', 'summary.tsv': 'text/tab-separated-values; charset=utf-8', 'parameters.R.txt': 'text/plain; charset=utf-8', 'R-session-info.txt': 'text/plain; charset=utf-8', 'pca_correlation.R': 'text/plain; charset=utf-8'}
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
SCRIPT={'name': 'pca_correlation.R', 'bytes': 15076, 'sha256': '6879d97eb501529f6af9fffdcf35d40285922f0b221843dc0c0892342368b4ef'}
RECORD_SHA='0b034b20ca5e14704374b82a65c4525a3bd1f66dfb392cc5ce79a6d504b10288'
# Exact prior reviewed PCA/correlation actual-A001 outputs, not arbitrary client or audit paths.
OUTPUT_PINS={'PCA-row-audit.tsv': (217396, '3e0143a205c4b4a6aad4464b75560c68666d89f32de99cad7504a20faae09818'), 'PCA.svg': (126799, '0974d1be016826379c2d385f13c223ebe39a6b9204de117aad65e2567f167261'), 'parameters.R.txt': (1509, '7a6b61ac3057e70a80b762a2cb8c26b9c2860ce7078b3c6a192ad502fb514584'), 'full-provenance.rds': (172276, 'd350f220cd8ff6116c4cd7713c252a8957008b1d086496f45731974c3b0e05c5'), 'correlation-paired-n.tsv': (3639, 'a8e4a5a08182d2c538a606b501cef1318c6c2a51fb7893ced839f28ec9a3ecf2'), 'PCA-loadings.tsv': (367272, '1325d3645c106417464dac4827e9f8e2fd64e320c34436c8c8d5c4b385678e87'), 'summary.tsv': (343, '510bb89adec55d787d004ba39420aefe448a7504958d0ceb3e11abd4cc40d57f'), 'correlation-matrix.tsv': (10062, 'cfc030cd68e0b6c7ee794400befdbcb602ead381022ac18ba0c08573ce2cc7f3'), 'correlation.svg': (234570, '9502d3989331802ea85eb18392e6835e6bf696d8852da829f7c18a50b433cb3a'), 'correlation-reasons.tsv': (6082, 'c82614cd82b004807e62b3a610e51df6be3a894b4a2afb9af671084cc0eecdb2'), 'sample-map.tsv': (1760, '3e045ef218edf5642d710c7acd272e085c6faf06957445c745f4a88d99e19b51'), 'R-session-info.txt': (1265, 'ca61cf284d00a0ec1d4005313eb865a327d20b96fdbc802a4084126a400cc46c'), 'sample-design.tsv': (2506, '58aa2fd580e1b1302c57f3b0c989aec94ad5c78d6c39c792d9860dd104469098'), 'PCA-variance.tsv': (2035, '2624b4aca1213253a44f59dcdf0ae7e84a707b3f97b5219bf052a745a0638d55'), 'full-saved-matrix.tsv': (691780, '7d6f11bb7891ac3549c463f3570d6a9fe5ea0eeea8a880ca5022687aed150c49'), 'PCA-selected-source.tsv': (318891, 'b17227cb2571e21a1b30cdeb9921066f915673c1f67dec84b7ec77a21b7b6a89'), 'PCA-center.tsv': (47283, '6e5faebdf9cec3048bdf4ffb962c08d59cc8ac1bd7c68e7b3b646b8f9627a724'), 'correlation-pairs.tsv': (66625, '76b5e4b8b6c8de173d96e6cac2978841522d694c4e7b9f9cb9f5ac3714d69e9c'), 'PCA-scores.tsv': (11766, '1659a54dcfb1f45135477cdfef04641b8dd4a208435dfe91000e06fdfaec6369')}
ANALYSIS={'source_rows': 2252, 'samples': 21, 'PCA_status': 'estimated', 'PCA_eligible_rows': 672, 'PCA_included_rows': 672, 'PCA_missing_excluded_rows': 1580, 'PCA_constant_excluded_rows': 0, 'PCA_center': True, 'PCA_scale': False, 'PCA_missing_policy': 'finite_complete_case_rows_no_imputation', 'PCA_sample_exclusion': False, 'PCA_plot_components': [1, 2], 'correlation_status': 'estimated', 'correlation_method': 'pearson', 'correlation_use': 'pairwise.complete.obs', 'correlation_source_rows': 2252, 'correlation_min_paired_observations': 3, 'correlation_paired_n_min': 867, 'correlation_paired_n_max': 1222, 'correlation_estimable_ordered_pairs': 441, 'correlation_nonestimable_ordered_pairs': 0, 'correlation_color_limits': [-1, 1], 'correlation_cluster_rows': False, 'correlation_cluster_columns': False, 'correlation_NA_policy': 'NA_grey_not_zero_or_one', 'input_scale': 'log2', 'normalization': 'none', 'imputation': 'none', 'analysis_ready': False}

def fail(code='qc_pca_correlation_integrity_failed',status=503):raise Fault(status,code)
def need(ok):
    if not ok:fail()
def canonical(v):return saved.canonical(v).encode('utf8')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def prepare(config=None):
    try:return secure_prepare(config)
    except Fault:fail('qc_pca_correlation_unavailable')
def output_manifest():
    return [{'name':n,'bytes':OUTPUT_PINS[n][0],'sha256':OUTPUT_PINS[n][1],
             'media_type':MEDIA.get(n,'application/octet-stream')} for n in sorted(OUTPUT_PINS)]
def artifact_pins():
    return [dict(SCRIPT if n=='pca_correlation.R' else {'name':n,'bytes':OUTPUT_PINS[n][0],'sha256':OUTPUT_PINS[n][1]},media_type=MEDIA[n]) for n in NAMES]
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
        except FileNotFoundError:fail('qc_pca_correlation_not_ready',404)
        except Fault as error:
            if error.code.startswith('qc_pca_correlation_'):raise
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
        if params['normalization']!='none' or params['imputation']!='none':fail('qc_pca_correlation_unsupported_parent',409)
        if (project,version)!=(PROJECT,VERSION):fail('qc_pca_correlation_not_ready',404)
        raw,registered,_=self.provider.read(parent,subject,project,version,budget.check,metadata_only=True)
        envelope=await self.designs.read(subject,project,parent['parent_dataset_version_id'],parent['quant_family'],budget,request_id=DESIGN_REQUEST)
        need(canonical(envelope)==canonical(registered['design']['envelope']))
        _,_,bodies=self.provider.read(parent,subject,project,version,budget.check,expected=raw)
        value=dict(registered,contract=CONTRACT)
        need(len(canonical(value))<=MAX_DESCRIPTOR);budget.check()
        if name is not None:need(name in MEDIA);return value,bodies[name]
        return value,None
