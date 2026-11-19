"""Default-off Owner-only saved heatmap reads. No write/launch/reconstruction."""
from contextlib import ExitStack
from dataclasses import dataclass
import os
from pathlib import Path
from .settings import Fault
from .validation import canonical_uuid,strict_json
from . import de_classification_repository as saved,volcano_owner_read as previous
from .differential_worker_once import ResultReader
from .sample_repository import SampleDesignRepository
from .sample_persistence import response_value
from .quant_diff_input import _tsv
from .r_de_heatmap import contract as heatmap

MAX_FILE=27262976
MAX_TOTAL=134217728
MAX_DESCRIPTOR=32768
MEDIA={n:('image/png' if n.endswith('.png') else 'image/svg+xml' if n.endswith('.svg') else 'text/plain;charset=utf-8' if n=='R-session.txt' else 'text/tab-separated-values;charset=utf-8') for n in heatmap.OUTPUTS}
NAMES=tuple(MEDIA)
INPUTS=('matrix.tsv','provenance.json','samples.tsv','selected-DE.tsv')
CONTEXT=previous.CONTEXT
DESCRIPTOR=previous.DESCRIPTOR
PRIVATE_KEYS=frozenset(DESCRIPTOR)|{'render_script','inputs'}
SCRIPT={'bytes':11356,'sha256':'c0a52145b1cbaba60e305fd93ab7be354869b8e753e767a59cddbd0cb8629847'}

def fail(code='r_heatmap_unavailable',status=503):raise Fault(status,code)
def need(ok):
    if not ok:fail()
def pin(raw):return {'bytes':len(raw),'sha256':saved.c.sha(raw)}
def valid_pin(value):
    return type(value) is dict and set(value)=={'bytes','sha256'} and type(value['bytes']) is int and 0<value['bytes']<=MAX_FILE and saved.admission.pc.hashed(value['sha256'])

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
    return config # No filesystem observation or default root.

def checked_registration(raw,context,inputs):
    need(type(raw) is bytes and 0<len(raw)<=65536 and not raw.startswith(b'\xef\xbb\xbf'))
    value=strict_json(raw)
    need(type(value) is dict and set(value)==PRIVATE_KEYS and type(value['schemaVersion']) is int and value['schemaVersion']==1
        and value['contract']=='r_heatmap_saved_v0.1' and saved.canonical(value).encode('utf8')==raw)
    for key in CONTEXT:need(value['created_by' if key=='actor_id' else key]==context[key])
    need(value['render_script']==SCRIPT and type(value['inputs']) is list and value['inputs']==inputs and len(inputs)==4)
    for name,p in zip(INPUTS,value['inputs']):
        need(type(p) is dict and set(p)=={'name','bytes','sha256'} and p['name']==name and valid_pin({k:p[k] for k in ('bytes','sha256')}))
    artifacts=value['artifacts'];need(type(artifacts) is list and len(artifacts)==8)
    for name,p in zip(NAMES,artifacts):
        need(type(p) is dict and set(p)=={'name','bytes','sha256','media_type'} and p['name']==name and p['media_type']==MEDIA[name]
            and valid_pin({k:p[k] for k in ('bytes','sha256')}))
    need(sum(p['bytes'] for p in artifacts+inputs)<=MAX_TOTAL)
    return value

class FileProvider(previous.FileProvider):
    """Reuse the reviewed directory/fd/identity helpers; new exact13 layout only."""
    def __init__(self,config):self.config=prepare(config);need(self.config is not None)
    def _read(self,version,check,expected=None):
        need(canonical_uuid(version))
        try:
            with ExitStack() as stack:
                parent=self._root(stack,check);parent_id=self._directory(parent,private=True)
                folder=os.open(version,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent);stack.callback(os.close,folder)
                folder_id=self._directory(folder,private=True)
                need(set(os.listdir(folder))==set(NAMES)|set(INPUTS)|{'metadata.json'})
                raw,record=self._file(stack,folder,'metadata.json',65536,check);opened=[record];bodies={}
                if expected is not None:
                    need(raw==expected)
                    registered=strict_json(raw);total=0
                    for p in registered['inputs']+registered['artifacts']:
                        body,record=self._file(stack,folder,p['name'],MAX_FILE,check)
                        need(pin(body)=={k:p[k] for k in ('bytes','sha256')});total+=len(body);need(total<=MAX_TOTAL)
                        opened.append(record);bodies[p['name']]=body
                for name,fd,before in opened:
                    check();need(previous.identity(os.fstat(fd))==before and previous.identity(os.stat(name,dir_fd=folder,follow_symlinks=False))==before)
                need(self._directory(parent,private=True)==parent_id and self._directory(folder,private=True)==folder_id
                    and previous.identity(os.stat(version,dir_fd=parent,follow_symlinks=False))==folder_id)
                need(self._directory(self._root(stack,check),private=True)==parent_id);check();return raw,bodies
        except FileNotFoundError:fail('not_found',404)
        except Fault as error:
            if error.code=='r_volcano_unavailable':fail()
            raise
        except Exception:fail()
    def read(self,context,inputs,check):
        raw,_=self._read(context['derived_version_id'],check)
        registered=checked_registration(raw,context,inputs)
        _,bodies=self._read(context['derived_version_id'],check,raw)
        return registered,bodies

async def bound_samples(reader,subject,project,parent,budget):
    selection=parent['selection'];need(type(selection) is dict and set(selection)=={'processed_dataset_version_id','sample_design_version_id','sample_design_request_id','contrast','replicate_model'})
    need(all(canonical_uuid(selection[k]) for k in ('processed_dataset_version_id','sample_design_version_id','sample_design_request_id'))
        and selection['replicate_model']=='independent_biological')
    contrast=selection['contrast']
    need(type(contrast) is dict and set(contrast)=={'schemaVersion','dataset_version_id','quant_family','sample_design_version_id','contrasts'}
        and type(contrast['schemaVersion']) is int and contrast['schemaVersion']==1 and canonical_uuid(contrast['dataset_version_id'])
        and contrast['quant_family']=='LFQ intensity' and contrast['sample_design_version_id']==selection['sample_design_version_id']
        and type(contrast['contrasts']) is list and len(contrast['contrasts'])==1)
    item=contrast['contrasts'][0]
    need(type(item) is dict and set(item)=={'contrast_id','numerator_group','denominator_group'} and canonical_uuid(item['contrast_id'])
        and all(type(item[k]) is str and item[k] for k in ('numerator_group','denominator_group')) and item['numerator_group']!=item['denominator_group'])
    envelope=await reader.read(subject,project,contrast['dataset_version_id'],contrast['quant_family'],budget,request_id=selection['sample_design_request_id'])
    response_value(envelope,project,contrast['dataset_version_id'],contrast['quant_family'])
    record=envelope['record']
    if record is None or (record['sample_design_version_id'],record['request_id'])!=(selection['sample_design_version_id'],selection['sample_design_request_id']):fail('not_found',404)
    rows=record['design']['rows'];need(all(r['confirmed'] is True for r in rows))
    chosen=[r for r in rows if r['group'] in (item['numerator_group'],item['denominator_group'])]
    need(len(chosen)==6 and all(r['replicate_type']=='biological' for r in chosen)
        and all(sum(r['group']==item[k] for r in chosen)>=2 for k in ('numerator_group','denominator_group'))
        and len({(r['group'],r['replicate']) for r in chosen})==6)
    # Same original binder order and key assignment, with original validated source ordinals/labels.
    samples=[('s'+str(i),str(r['column_ordinal']),r['sample_label_raw'],'g1' if r['group']==item['numerator_group'] else 'g2',r['group'],'r'+str(i),'biological') for i,r in enumerate(chosen,1)]
    design=[('sample_key','group_key','replicate_key','replicate_type','confirmed')]+[(s[0],s[3],s[5],'biological','true') for s in samples]
    preset=[('key','value'),('schema_version','limma_core_v0.1'),('input_scale','log2'),('upstream_transform','positive_log2'),('upstream_zero_blank','NA'),('pseudocount','0'),('normalization','none'),('imputation','none'),('replicate_type','biological'),('design_confirmed','true'),('contrast_id',item['contrast_id']),('numerator','g1'),('denominator','g2')]
    need(pin(_tsv(design))==parent['input_manifest']['design'] and pin(_tsv(preset))==parent['input_manifest']['preset'])
    budget.check();return _tsv([heatmap.SAMPLES_HEADER]+samples)

class OwnerReader:
    def __init__(self,repository,parent_reader,design_reader,provider):
        need(type(repository) is saved.ClassificationRepository and type(parent_reader) is ResultReader
            and type(design_reader) is SampleDesignRepository and type(provider) is FileProvider
            and repository.owner.pool is not None and repository.owner.pool is parent_reader.owner.pool is design_reader.pool)
        self.repository=repository;self.parent=parent_reader;self.samples=design_reader;self.provider=provider
    async def read(self,subject,project,version,budget,name=None):
        budget.check();meta=await self.repository.read_version(subject,project,version,budget);saved.validate_dto(meta)
        need((meta['created_by'],meta['project_id'],meta['derived_version_id'])==(subject,project,version))
        parent=await self.parent.read(subject,project,budget,result_id=meta['source_result_id'])
        need((parent['created_by'],parent['project_id'],parent['result_id'])==(subject,project,meta['source_result_id'])
            and parent['manifest_sha256']==meta['source_manifest_sha256'] and meta['source_binding']['source_metadata_sha256']==saved.digest(parent))
        if meta['status']!='CLASSIFIED':fail('not_found',404)
        sample_bytes=await bound_samples(self.samples,subject,project,parent,budget)
        context={'project_id':project,'actor_id':subject,'source_result_id':meta['source_result_id'],'source_metadata_sha256':saved.digest(parent),
            'source_manifest_sha256':parent['manifest_sha256'],'derived_version_id':version,'derived_metadata_sha256':saved.digest(meta),
            'derived_manifest_sha256':meta['manifest_sha256'],'source_binding_sha256':meta['source_binding_sha256'],'parameters_sha256':meta['parameters_sha256']}
        matrix_pin=parent['input_manifest']['matrix'];need(valid_pin(matrix_pin))
        selected=next(p for p in meta['artifacts'] if p['name']=='selected-DE.tsv');selected_pin={k:selected[k] for k in ('bytes','sha256')};need(valid_pin(selected_pin))
        three=[{'name':n,**p} for n,p in (('matrix.tsv',matrix_pin),('samples.tsv',pin(sample_bytes)),('selected-DE.tsv',selected_pin))]
        provenance={'schemaVersion':1,'contract':'r_heatmap_provenance_v0.1',**{('created_by' if k=='actor_id' else k):v for k,v in context.items()},'inputs':three}
        provenance_bytes=saved.canonical(provenance).encode('utf8')
        inputs=[{'name':n,**p} for n,p in (('matrix.tsv',matrix_pin),('provenance.json',pin(provenance_bytes)),('samples.tsv',pin(sample_bytes)),('selected-DE.tsv',selected_pin))]
        # All three original Owner readers have completed before even cache metadata IO.
        registered,bodies=self.provider.read(context,inputs,budget.check)
        need(bodies['samples.tsv']==sample_bytes and bodies['provenance.json']==provenance_bytes)
        verified=heatmap.validate_inputs(bodies['matrix.tsv'],bodies['selected-DE.tsv'],bodies['samples.tsv'],bodies['provenance.json'])
        heatmap.validate_outputs(verified,{n:bodies[n] for n in NAMES});budget.check()
        descriptor={k:registered[k] for k in DESCRIPTOR};descriptor['contract']='r_heatmap_view_v0.1'
        need(len(saved.canonical(descriptor).encode('utf8'))<=MAX_DESCRIPTOR)
        if name is not None:need(name in MEDIA);return descriptor,bodies[name]
        return descriptor,None
