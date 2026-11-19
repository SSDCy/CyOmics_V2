"""Owner-proved saved bytes to bounded deterministic ZIP. No execution/writes/credentials."""
from dataclasses import dataclass
from contextlib import ExitStack
from pathlib import Path
import hashlib,io,json,os,stat,zipfile
from .settings import Fault
from .validation import canonical_uuid

MAX_ARCHIVE=32*1024*1024
CONTEXT_KEYS=('project_id','actor_id','source_result_id','source_metadata_sha256','source_manifest_sha256','derived_version_id','derived_metadata_sha256','derived_manifest_sha256','source_binding_sha256','parameters_sha256')
PARAMETERS={'schema_version':'de_classification_v0.1','input_scale':'log2','adjusted_p_max':'0.05','absolute_log2fc_min':'1'}
SCRIPTS={'volcano':('r_de_volcano/volcano.R',13931,'5062b985000fb2cd44c93f8f0d760000bf671f3e0226ee70c773bf3b1f917fe2'),
 'heatmap':('r_de_heatmap/heatmap.R',11356,'c0a52145b1cbaba60e305fd93ab7be354869b8e753e767a59cddbd0cb8629847')}
INPUTS={'volcano':('classification.tsv','classification-parameters.tsv','classification-audit.tsv'),'heatmap':('matrix.tsv','provenance.json','samples.tsv','selected-DE.tsv')}
OUTPUTS={'volcano':('R-session.txt','volcano-audit.tsv','volcano-points.tsv','volcano.svg'),
 'heatmap':('R-session.txt','heatmap-audit.tsv','heatmap-matrix.tsv','heatmap-row-map.tsv','heatmap-samples.tsv','heatmap-zscore.tsv','heatmap.svg')}
ARGV={'volcano':('Rscript','--vanilla','scripts/volcano.R','inputs/classification.tsv','inputs/classification-parameters.tsv','inputs/classification-audit.tsv','reproduced'),
 'heatmap':('Rscript','--vanilla','scripts/heatmap.R','inputs/matrix.tsv','inputs/selected-DE.tsv','inputs/samples.tsv','inputs/provenance.json','reproduced')}

def need(ok):
    if not ok:raise Fault(503,'plot_reproducibility_unavailable')
def too_large():raise Fault(413,'plot_reproducibility_too_large')
def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')
def pin(raw):return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def identity(s):return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def script(kind,check):
    relative,size,digest=SCRIPTS[kind];q=Path(__file__).parent/relative;check()
    need(not any(x.is_symlink() for x in (q,*q.parents)))
    fd=os.open(q,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    try:
        before=os.fstat(fd);need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and not before.st_mode&0o022 and before.st_uid in (0,os.geteuid()) and before.st_size==size)
        raw=os.read(fd,size+1);check();need(pin(raw)=={'bytes':size,'sha256':digest} and identity(os.fstat(fd))==identity(before) and identity(q.lstat())==identity(before));return raw
    finally:os.close(fd)

def require_publication(kind,provider,version,check):
    # Caller has completed the original Owner/source proof; fixed members only.
    need(kind in OUTPUTS and canonical_uuid(version));check()
    required=set(OUTPUTS[kind])|{kind+'.png','metadata.json'}
    if kind=='heatmap':required.update(INPUTS[kind])
    try:
        with ExitStack() as stack:
            parent=provider._root(stack,check)
            fd=os.open(version,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent);stack.callback(os.close,fd)
            provider._directory(fd,private=True)
            if not required.issubset(set(os.listdir(fd))):raise Fault(404,'not_found')
            check()
    except FileNotFoundError:raise Fault(404,'not_found') from None

@dataclass(frozen=True,slots=True)
class Export:
    body:bytes
    sha256:str
    filename:str

class BoundedBuffer(io.BytesIO):
    def __init__(self,check):super().__init__();self.check=check
    def write(self,raw):
        self.check()
        if self.tell()+len(raw)>MAX_ARCHIVE:too_large()
        return super().write(raw)

def build(kind,context,metadata,inputs,outputs,parameters,source,check):
    need(kind in INPUTS and type(context) is dict and set(context)==set(CONTEXT_KEYS)
        and all(canonical_uuid(context[k]) for k in ('project_id','actor_id','source_result_id','derived_version_id')))
    need(type(parameters) is dict and set(parameters)==set(PARAMETERS) and parameters['schema_version']==PARAMETERS['schema_version'] and parameters['input_scale']=='log2')
    need(type(inputs) is dict and set(inputs)==set(INPUTS[kind]) and type(outputs) is dict and set(outputs)==set(OUTPUTS[kind]))
    need(type(metadata) is bytes and 0<len(metadata)<=65536 and type(source) is dict and set(source)=={'classification_core','source_method','source_selection','source_input_manifest'} and all(type(v) is dict for v in source.values()))
    method={'statistical_recomputation':False,'normalization':'none','imputation':'none','scope':'saved-input plot reproduction only',
        'expected_versions':{'R':'4.6.1','digest':'0.6.39',**({'ggplot2':'4.0.3'} if kind=='volcano' else {'pheatmap':'1.0.13'})},
        'argv':list(ARGV[kind]),'scripts':{k:{'bytes':v[1],'sha256':v[2]} for k,v in SCRIPTS.items()}}
    params=canonical({'schemaVersion':1,'contract':'plot_reproduction_parameters_v0.1','classification_parameters':parameters,'method':method,'sources':source})
    files={'metadata.json':metadata,'parameters.json':params,**{'inputs/'+n:v for n,v in inputs.items()},**{'outputs/'+n:v for n,v in outputs.items()}}
    need(all(type(v) is bytes and len(v)>0 for v in files.values()))
    if any(len(v)>MAX_ARCHIVE for v in files.values()) or sum(map(len,files.values()))>MAX_ARCHIVE:too_large()
    for k in SCRIPTS:files['scripts/'+k+'.R']=script(k,check)
    readme=('Cy_Omics_v2.0 saved '+kind+' plot reproduction\n\n'
        'Unzip into one local directory; run from its root. Create the output directory first:\n'
        'mkdir reproduced\n'+' '.join(ARGV[kind])+'\n\n'
        'Use R 4.6.1, digest 0.6.39 and '+('ggplot2 4.0.3' if kind=='volcano' else 'pheatmap 1.0.13')+'. Cairo support and the original Microsoft YaHei font are required. Font/package installation is not part of this archive or this download. Set your own R library/fontconfig environment before invoking Rscript. Fonts and binaries are not bundled.\n'
        'Both original plot scripts are included verbatim; only the current plot inputs are bundled. The other script requires its own saved inputs. The commands above use the exact original argument order, with no fitting, BH recomputation, reclassification or threshold changes.\n'
        'inputs/ contains exact original saved source bytes. For heatmaps, inputs/matrix.tsv is the full source; outputs/heatmap-matrix.tsv is a selected display output and must not replace it. parameters.json preserves original classification parameters, source method/selection/input pins and classification core. R-session.txt preserves the recorded execution versions; the manifest lists expected versions, not a new execution claim.\n'
        'Only SVG is bundled as an image. The unchanged scripts also generate PNG locally; the original PNG preview and all PDF are intentionally excluded from this archive. Original metadata.json is unchanged and may still list the original PNG artifact.\n'
        'manifest.json hashes every other archive member. Its own bytes are covered by the full ZIP SHA256 response header, avoiding a circular self hash. ZIP reproduction is byte deterministic for unchanged source bytes; local image rendering can depend on system fonts/Cairo/R builds. No credentials, private failure facts or runtime authority are included.\n').encode('utf8')
    files['README.txt']=readme
    manifest={'schemaVersion':1,'contract':'plot_reproducibility_manifest_v0.1','plot_kind':kind,'context':context,'parameters':parameters,'method':method,'files':{n:pin(v) for n,v in sorted(files.items())},'manifest_self_hash':'covered_by_archive_sha256_header'}
    files['manifest.json']=canonical(manifest)
    if sum(map(len,files.values()))>MAX_ARCHIVE:too_large()
    buffer=BoundedBuffer(check)
    try:
        with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_STORED,allowZip64=False) as archive:
            for name,raw in sorted(files.items()):
                check();info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.create_system=3;info.create_version=20;info.extract_version=20;info.external_attr=(stat.S_IFREG|0o444)<<16;info.compress_type=zipfile.ZIP_STORED;info.internal_attr=0
                archive.writestr(info,raw)
        check();body=buffer.getvalue();need(0<len(body)<=MAX_ARCHIVE)
    finally:buffer.close()
    return Export(body,hashlib.sha256(body).hexdigest(),'cyomics_'+kind+'_'+context['derived_version_id']+'_reproducibility.zip')
