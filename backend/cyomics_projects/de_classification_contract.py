"""Detached parent binding and six-output invariants; NOT Auth/runtime permission.

Trusted Owner/Root caller supplies a genuine original binder-issued BoundInput
and immutable source records. Original five-output validation happens HERE;
classification never impersonates that model or its five-output bundle.
"""
from dataclasses import dataclass,field
import hashlib,json
from . import limma_bound_contract as original,differential_jobs as admission
from . import differential_worker_once as parent
from .quant_diff_input import BoundInput

CONTRACT='de_classification_v0.1'
OUTPUTS=tuple(sorted(('classification.tsv','selected-DE.tsv','classification-parameters.tsv',
    'classification-audit.tsv','source-method-audit.tsv','source-test-set.tsv')))
MAX_FILE=26*1024*1024
MAX_TOTAL=128*1024*1024
SOURCES=('results.tsv','method-audit.tsv','test-set.tsv')
PARAM_KEYS=('schema_version','input_scale','adjusted_p_max','absolute_log2fc_min')
HEADER=original.RESULT_HEADER+('classification','selected_DE')
AUDIT_KEYS=tuple(('schema_version classification_status DE_members_evaluated source_contract source_output_status '
    'source_warning_count source_contrast_id source_numerator source_denominator contrast_direction threshold_scale '
    'adjusted_p_max absolute_log2fc_min boundary_rule zero_log2FC input_rows source_testable_rows source_not_testable_rows '
    'Up Down NS not_testable not_evaluated selected_DE_rows source_bh_method source_bh_m source_bh_n source_bh_scope '
    'FC_column statistical_recomputation row_identity row_order specific_detection').split())
_SEAL=object()

class InvalidClassification(ValueError):
    def __init__(self,code='de_classification_invalid'):self.code=code;super().__init__(code)
def need(ok,code='de_classification_invalid'):
    if not ok:raise InvalidClassification(code)
def canonical(value):return original.canonical(value)
def sha(body):return hashlib.sha256(body).hexdigest()
def pin(body):return original.pin(body)
def document(body,cap):
    need(type(body) is bytes and 0<len(body)<=cap)
    try:return admission.document(body.decode('utf8'),cap)
    except Exception:raise InvalidClassification() from None
def table(body,header,maximum=10000):
    need(type(body) is bytes and 0<len(body)<=MAX_FILE and body.endswith(b'\n')
        and b'\0' not in body and b'\r' not in body and not body.startswith(b'\xef\xbb\xbf'))
    try:rows=[tuple(line.split('\t')) for line in body.decode('utf8').split('\n')[:-1]]
    except UnicodeError:raise InvalidClassification() from None
    need(1<=len(rows)<=maximum+1 and rows[0]==tuple(header) and all(len(r)==len(header) for r in rows))
    return rows[1:]

@dataclass(frozen=True,slots=True)
class Parameters:
    body:bytes=field(repr=False)
    adjusted_p_max:float
    absolute_log2fc_min:float

def parameters(body):
    need(type(body) is bytes and 0<len(body)<=4096,'de_parameters_invalid')
    rows=table(body,('key','value'),4)
    need(tuple(k for k,v in rows)==PARAM_KEYS,'de_parameters_invalid')
    p=dict(rows)
    need(p['schema_version']==CONTRACT and p['input_scale']=='log2','de_parameters_invalid')
    try:q=original.number(p['adjusted_p_max']);f=original.number(p['absolute_log2fc_min'])
    except original.InvalidOutput:raise InvalidClassification('de_parameters_invalid') from None
    need(0<q<=1 and f>=0,'de_parameters_invalid')
    return Parameters(body,q,f)

@dataclass(frozen=True,slots=True)
class ClassificationSource:
    _bound:BoundInput=field(repr=False)
    _job:admission.StoredJob=field(repr=False)
    _snapshot:str=field(repr=False)
    _metadata:bytes=field(repr=False)
    _receipt:bytes=field(repr=False)
    _artifacts:tuple=field(repr=False)
    _binding:bytes=field(repr=False)
    _seal:object=field(repr=False)
    @property
    def binding(self):return json.loads(self._binding)
    @property
    def binding_sha256(self):return sha(self._binding)
    def files(self):return {a.name:a.body for a in self._artifacts if a.name in SOURCES}

def _validated(bound,job,snapshot,metadata,receipt,outputs,check):
    need(callable(check));check()
    need(type(bound) is BoundInput and type(job) is admission.StoredJob and type(snapshot) is str)
    public=job.public();meta=document(metadata,32768);proof=document(receipt,131072)
    need(sha(receipt)==meta.get('receipt_sha256'),'de_parent_binding_invalid')
    need(public['method']==admission.METHOD,'de_parent_binding_invalid')
    need(admission.snapshot(bound)==snapshot,'de_parent_binding_invalid')
    need(admission.binding_sha(public['request_sha256'],admission.sha(snapshot),
        admission.sha(admission.canonical(public['input_manifest'])),admission.sha(admission.canonical(public['method'])))
        ==public['binding_sha256'],'de_parent_binding_invalid')
    need({n:pin(b) for n,b in zip(('matrix','design','preset'),(bound.matrix_tsv,bound.design_tsv,bound.preset_tsv))}
        ==public['input_manifest'],'de_parent_binding_invalid')
    parent.validate_metadata(meta,public);parent.validate_receipt(proof,meta)
    # Original validator, actual original model, COMPLETE original five bytes.
    bundle=original.validate(bound,meta['exit_code'],outputs,check=check)
    manifest=[{'name':a.name,'bytes':a.bytes,'sha256':a.sha256} for a in bundle.artifacts]
    need(manifest==meta['artifacts'] and bundle.manifest_sha256==meta['manifest_sha256']
        and bundle.status==meta['scientific_status'] and bundle.warning_count==meta['warning_count']
        and proof['manifest_sha256']==bundle.manifest_sha256,'de_parent_binding_invalid')
    binding={'schemaVersion':1,'contract':'de_parent_binding_v0.1','owner_id':meta['created_by'],
        'project_id':meta['project_id'],'source_result_id':meta['result_id'],'source_job_id':meta['job_id'],
        'source_attempt_id':meta['attempt_id'],'source_registration_id':meta['registration_id'],
        'source_request_id':meta['request_id'],'source_contrast_id':bound.contrast.contrast_id,
        'admission_binding_sha256':meta['admission_binding_sha256'],'input_snapshot_sha256':admission.sha(snapshot),
        'source_metadata_sha256':sha(metadata),'source_receipt_sha256':sha(receipt),
        'source_manifest_sha256':bundle.manifest_sha256,'source_artifacts':manifest,
        'source_status':bundle.status,'source_warning_count':bundle.warning_count,
        'source_method_sha256':sha(canonical(meta['method'])),'contrast_direction':'numerator_minus_denominator'}
    check();return bundle,canonical(binding)

def bind_source(*,bound,job,input_snapshot_json,metadata_json,receipt_json,outputs,check):
    """Pure trusted construction; never verifies Owner/Auth or grants execution."""
    bundle,binding=_validated(bound,job,input_snapshot_json,metadata_json,receipt_json,outputs,check)
    return ClassificationSource(bound,job,input_snapshot_json,metadata_json,receipt_json,bundle.artifacts,binding,_SEAL)

def verify_source(source,*,check):
    need(type(source) is ClassificationSource and source._seal is _SEAL,'de_source_required')
    need(type(source._binding) is bytes and type(source._artifacts) is tuple
        and all(type(a) is original.Artifact for a in source._artifacts)
        and tuple(a.name for a in source._artifacts)==original.OUTPUTS)
    outputs={a.name:a.body for a in source._artifacts}
    bundle,binding=_validated(source._bound,source._job,source._snapshot,source._metadata,source._receipt,outputs,check)
    need(bundle.artifacts==source._artifacts and binding==source._binding,'de_parent_binding_invalid')
    return source.binding

@dataclass(frozen=True,slots=True)
class DerivedClassificationBundle:
    exit_code:int
    status:str
    DE_members_evaluated:bool
    source_binding_sha256:str
    parameters_sha256:str
    artifacts:tuple=field(repr=False)
    manifest_sha256:str
    published:bool=False
    statistics_recomputed:bool=False

def validate(source,spec,exit_code,outputs,*,check):
    verify_source(source,check=check)
    need(type(spec) is Parameters and parameters(spec.body)==spec,'de_parameters_invalid')
    need(type(exit_code) is int and exit_code in (0,3,4) and type(outputs) is dict and set(outputs)==set(OUTPUTS))
    need(all(type(b) is bytes and 0<len(b)<=MAX_FILE for b in outputs.values()) and sum(map(len,outputs.values()))<=MAX_TOTAL)
    files=source.files()
    need(outputs['classification-parameters.tsv']==spec.body and outputs['source-method-audit.tsv']==files['method-audit.tsv']
        and outputs['source-test-set.tsv']==files['test-set.tsv'],'de_copy_mismatch')
    rows=table(files['results.tsv'],original.RESULT_HEADER)
    a=dict(table(files['method-audit.tsv'],('key','value'),len(original.AUDIT_KEYS)))
    yes=a['output_status']=='COMPUTED_CANDIDATE' and a['warning_count']=='0'
    expected_exit=0 if yes else 3 if a['output_status']=='NO_TESTABLE_ROWS' else 4
    status='CLASSIFIED' if yes else 'BLOCKED_SOURCE_NO_TESTABLE_ROWS' if expected_exit==3 else 'BLOCKED_SOURCE_REVIEW_REQUIRED'
    need(exit_code==expected_exit,'de_exit_status_mismatch')
    actual=table(outputs['classification.tsv'],HEADER)
    selected=table(outputs['selected-DE.tsv'],HEADER)
    need(len(actual)==len(rows));labels=[];expected_selected=[]
    for source_row,row in zip(rows,actual):
        check();need(row[:18]==source_row,'de_source_token_changed')
        tested=source_row[3]=='testable'
        label='not_testable' if not tested else 'not_evaluated'
        if tested and yes:
            effect=original.number(source_row[5]);q=original.number(source_row[13])
            passed=q<=spec.adjusted_p_max and abs(effect)>=spec.absolute_log2fc_min
            label='Up' if passed and effect>0 else 'Down' if passed and effect<0 else 'NS'
        flag=('TRUE' if label in ('Up','Down') else 'FALSE') if yes else ('NOT_EVALUATED' if tested else 'FALSE')
        need(row[18:]==(label,flag),'de_label_mismatch');labels.append(label)
        if flag=='TRUE':expected_selected.append(row)
    need(selected==expected_selected,'de_selection_mismatch')
    p=dict(table(spec.body,('key','value'),4));counts={label:labels.count(label) for label in ('Up','Down','NS','not_testable','not_evaluated')}
    expected={'schema_version':CONTRACT,'classification_status':status,'DE_members_evaluated':'TRUE' if yes else 'FALSE',
        'source_contract':a['schema_version'],'source_output_status':a['output_status'],'source_warning_count':a['warning_count'],
        'source_contrast_id':a['contrast_id'],'source_numerator':a['numerator'],'source_denominator':a['denominator'],
        'contrast_direction':'numerator_minus_denominator','threshold_scale':'log2FC',
        'adjusted_p_max':p['adjusted_p_max'],'absolute_log2fc_min':p['absolute_log2fc_min'],
        'boundary_rule':'adj.P.Val_le_max_and_abs_log2FC_ge_min_and_strict_direction_sign','zero_log2FC':'NS_if_evaluated',
        'input_rows':str(len(rows)),'source_testable_rows':a['bh_m'],'source_not_testable_rows':str(counts['not_testable']),
        **{label:str(counts[label]) if yes else 'NA' for label in ('Up','Down','NS')},
        'not_testable':str(counts['not_testable']),'not_evaluated':str(counts['not_evaluated']),
        'selected_DE_rows':str(len(selected)) if yes else 'NA','source_bh_method':a['bh_method'],'source_bh_m':a['bh_m'],
        'source_bh_n':a['bh_n'],'source_bh_scope':a['bh_scope'],'FC_column':'copied_original_not_used_for_threshold',
        'statistical_recomputation':'none','row_identity':'source_row_ordinal_no_ProteinID_deduplication',
        'row_order':'original','specific_detection':'not_implemented_D04_not_frozen'}
    audit_rows=table(outputs['classification-audit.tsv'],('key','value'),len(AUDIT_KEYS))
    need(tuple(k for k,v in audit_rows)==AUDIT_KEYS and dict(audit_rows)==expected,'de_audit_mismatch')
    artifacts=tuple(original.Artifact(n,len(outputs[n]),sha(outputs[n]),outputs[n]) for n in OUTPUTS)
    manifest=[{'name':a.name,'bytes':a.bytes,'sha256':a.sha256} for a in artifacts]
    check();return DerivedClassificationBundle(exit_code,status,yes,source.binding_sha256,sha(spec.body),artifacts,sha(canonical(manifest)))
