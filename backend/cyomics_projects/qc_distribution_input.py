"""Default-off saved QC input binder, no runner/HTTP/publication or raw reads.
The actor is supplied by the existing verified boundary; snapshots are not authority.
All data/code-environment authority stays in original repositories/Root respectively.
"""
from dataclasses import dataclass,field
import csv,io,json,math,re
from . import preprocess_contract as model,preprocess_repository as saved
from .sample_repository import SampleDesignRepository
from .sample_persistence import response_value as design_value,canonical as design_json
from .settings import Fault
from .validation import canonical_uuid

MASKS=('original-blank-mask.tsv','original-zero-mask.tsv','working-NA-mask.tsv','imputed-mask.tsv')
NUMBER=re.compile(r'^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?$')
MAX_PROVENANCE=262144


def need(ok,code='qc_distribution_input_integrity_failed',status=503):
    if not ok:raise Fault(status,code)


@dataclass(frozen=True,slots=True)
class Config:
    enabled:bool=False


@dataclass(frozen=True,slots=True)
class Sample:
    sample_ordinal:int
    name_raw:str
    source_column_ordinal:int|None
    group_label_raw:str|None


@dataclass(frozen=True,slots=True)
class Row:
    record_number:int
    protein_ids_raw:str
    tokens:tuple[str,...]


@dataclass(frozen=True,slots=True,repr=False)
class FrozenInput:
    parent_json:bytes
    design_json:bytes|None
    artifacts:tuple[saved.StoredArtifact,...]
    samples:tuple[Sample,...]
    rows:tuple[Row,...]
    masks:tuple[tuple[str,tuple[tuple[bool,...],...]],...]
    provenance_json:bytes
    binding_sha256:str

    def provenance(self):return json.loads(self.provenance_json)


def table(raw,check):
    need(type(raw) is bytes and len(raw)<=model.MAX_ARTIFACT)
    try:
        text=raw.decode('utf8','strict');need('\x00' not in text)
        reader=csv.reader(io.StringIO(text,newline=''),delimiter='\t',quotechar='"',strict=True)
        header=next(reader);need(3<=len(header)<=66 and header[:2]==['record_number','protein_ids_raw']
            and len(set(header))==len(header) and all(type(s) is str and s for s in header))
        rows=[];previous=0
        for values in reader:
            check();need(len(rows)<10000 and len(values)==len(header) and re.fullmatch('[1-9][0-9]{0,4}',values[0]) is not None)
            number=int(values[0]);need(previous<number<=10000);previous=number
            rows.append(Row(number,values[1],tuple(values[2:])))
        need(1<=len(rows) and len(rows)*(len(header)-2)<=2000000)
        return tuple(header),tuple(rows)
    except (UnicodeError,csv.Error,StopIteration):raise Fault(503,'qc_distribution_input_integrity_failed') from None


def decoded_matrix(artifacts,parent,check):
    bodies={a.name:a.body for a in artifacts}
    header,rows=table(bodies['working-matrix.tsv'],check)
    need(len(rows)<=parent['source']['row_count'] and rows[-1].record_number<=parent['source']['row_count']
        and len(header)-2<parent['source']['column_count'])
    for row in rows:
        check()
        for token in row.tokens:
            need(token=='NA' or NUMBER.fullmatch(token) is not None and math.isfinite(float(token)))
    masks=[]
    for name in MASKS:
        have,values=table(bodies[name],check);need(have==header and len(values)==len(rows))
        result=[]
        for row,mask in zip(rows,values):
            check();need((row.record_number,row.protein_ids_raw)==(mask.record_number,mask.protein_ids_raw)
                and all(t in ('TRUE','FALSE') for t in mask.tokens))
            result.append(tuple(t=='TRUE' for t in mask.tokens))
        masks.append((name,tuple(result)))
    m=dict(masks)
    for i,row in enumerate(rows):
        check()
        for j,t in enumerate(row.tokens):
            blank=m[MASKS[0]][i][j];zero=m[MASKS[1]][i][j];missing=m[MASKS[2]][i][j];imputed=m[MASKS[3]][i][j]
            need(not imputed and not(blank and zero) and missing==(t=='NA') and missing==(blank or zero))
    need(bodies['parameters.json']==model.canonical(parent['parameters']))
    return header,rows,tuple(masks)


class Binder:
    def __init__(self,repository=None,designs=None,*,config=None):
        need(config is None or type(config) is Config and type(config.enabled) is bool,'qc_distribution_input_unavailable')
        self.repository=repository;self.designs=designs;self.enabled=config is not None and config.enabled

    def _ready(self):
        need(self.enabled and type(self.repository) is saved.PreprocessRepository and type(self.designs) is SampleDesignRepository
            and self.repository.enabled and self.repository.pool is not None and self.repository.pool is self.designs.pool,
            'qc_distribution_input_unavailable')

    async def bind(self,subject,project,version,budget,*,design_request_id=None,design_version_id=None):
        self._ready()
        need(all(canonical_uuid(v) for v in (subject,project,version)),'invalid_qc_distribution_input',400)
        need((design_request_id is None and design_version_id is None) or
            canonical_uuid(design_request_id) and canonical_uuid(design_version_id),'invalid_qc_distribution_input',400)
        budget.check()
        # Original Owner read precedes every artifact/design byte read.
        parent=await self.repository.read_version(subject,project,version,budget)
        try:
            model.version_value(parent,project=project,version=version)
            need(parent['created_by']==subject,'not_found',404)
            params=parent['parameters']
            need(parent['quant_family']=='LFQ intensity' and params['output_scale']=='log2' and params['imputation']=='none'
                and params['normalization'] in ('none','median','total','quantile') and params['log2']=='positive_only'
                and params['zero_policy']==params['blank_policy']=='to_NA' and type(params['pseudocount']) is int and params['pseudocount']==0,
                'qc_distribution_unsupported_parent',409)
            parent_raw=model.canonical(parent);parent=json.loads(parent_raw)
            artifacts=[];total=0
            for pin in parent['artifacts']:
                budget.check();value=await self.repository.read_artifact(subject,project,version,pin['name'],budget)
                need(type(value) is saved.StoredArtifact and type(value.body) is bytes and value.name==pin['name']
                    and value.media_type==pin['media_type'] and value.sha256==pin['sha256']
                    and len(value.body)==pin['bytes'] and model.bytes_sha(value.body)==pin['sha256'])
                total+=len(value.body);need(total<=model.MAX_ARTIFACT_TOTAL)
                artifacts.append(saved.StoredArtifact(value.name,value.media_type,value.body,value.sha256))
            header,rows,masks=decoded_matrix(artifacts,parent,budget.check)
            design_raw=None;samples=tuple(Sample(i,name,None,None) for i,name in enumerate(header[2:],1))
            if design_request_id is not None:
                envelope=await self.designs.read(subject,project,parent['parent_dataset_version_id'],parent['quant_family'],budget,request_id=design_request_id)
                design_value(envelope,project,parent['parent_dataset_version_id'],parent['quant_family'])
                record=envelope['record'];need(record is not None and record['request_id']==design_request_id
                    and record['sample_design_version_id']==design_version_id,'qc_distribution_design_mismatch',409)
                d=record['design']['rows'];need(len(d)==len(samples) and tuple(r['name_raw'] for r in d)==header[2:]
                    and all(r['confirmed'] is True and type(r['group']) is str and r['group'].strip()
                        and type(r['column_ordinal']) is int and 0<=r['column_ordinal']<parent['source']['column_count'] for r in d),
                    'qc_distribution_design_mismatch',409)
                need(all(a['column_ordinal']<z['column_ordinal'] for a,z in zip(d,d[1:])),'qc_distribution_design_mismatch',409)
                selected=[dict(ordinal=r['column_ordinal'],name_raw=r['name_raw'],sample_label_raw=r['sample_label_raw'],quant_family=parent['quant_family']) for r in d]
                need(saved.digest(saved.canonical(selected))==parent['source']['selected_columns_sha256'],'qc_distribution_design_mismatch',409)
                design_raw=design_json(envelope).encode('utf8')
                samples=tuple(Sample(i,r['name_raw'],r['column_ordinal'],r['group']) for i,r in enumerate(d,1))
            # Fresh exact immutable-version reads; no mutable design head lookup.
            fresh=await self.repository.read_version(subject,project,version,budget)
            model.version_value(fresh,project=project,version=version);need(model.canonical(fresh)==parent_raw)
            if design_raw is not None:
                fresh_design=await self.designs.read(subject,project,parent['parent_dataset_version_id'],parent['quant_family'],budget,request_id=design_request_id)
                design_value(fresh_design,project,parent['parent_dataset_version_id'],parent['quant_family'])
                need(design_json(fresh_design).encode('utf8')==design_raw,'qc_distribution_design_mismatch',409)
            provenance=dict(schemaVersion=1,contract='qc_saved_input_binding_v0.1',owner_id=subject,project_id=project,
                processed_dataset_version_id=version,parent_metadata_sha256=model.bytes_sha(parent_raw),parent=parent,
                input_manifest=parent['artifacts'],input_manifest_sha256=parent['manifest_sha256'],
                design=None if design_raw is None else json.loads(design_raw),
                design_envelope_sha256=None if design_raw is None else model.bytes_sha(design_raw),
                grouping='ungrouped_explicit' if design_raw is None else 'confirmed_saved_design',
                normalization=params['normalization'],rows=len(rows),samples=[dict(sample_ordinal=s.sample_ordinal,name_raw=s.name_raw,
                    source_column_ordinal=s.source_column_ordinal,group_label_raw=s.group_label_raw) for s in samples],cells=len(rows)*len(samples))
            encoded=model.canonical(provenance);need(len(encoded)<=MAX_PROVENANCE,'qc_distribution_input_limit',422);budget.check()
            return FrozenInput(parent_raw,design_raw,tuple(artifacts),samples,rows,masks,encoded,model.bytes_sha(encoded))
        except Fault:raise
        except (ValueError,TypeError,KeyError,OverflowError,RecursionError):raise Fault(503,'qc_distribution_input_integrity_failed') from None
