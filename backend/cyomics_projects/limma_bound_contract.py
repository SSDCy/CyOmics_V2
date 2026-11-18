"""Five-output shape/pin/lineage validation; no P, BH or fit implementation."""
from dataclasses import dataclass,field
import hashlib,json,math,re
from .quant_diff_input import BoundInput

OUTPUTS=tuple(sorted(('results.tsv','method-audit.tsv','sample-audit.tsv','test-set.tsv','R-session.txt')))
MAX_FILE=25*1024*1024
MAX_TOTAL=128*1024*1024
MAX_INPUT=25*1024*1024
MAX_DESIGN=128*1024
RESULT_HEADER=tuple('source_row_ordinal protein_ids_raw contrast_id test_status reason log2FC FC FC_reason AveExpr moderated_t df_residual df_total P.Value adj.P.Val n_obs_num n_obs_den n_fit_num n_fit_den'.split())
AUDIT_KEYS=tuple('schema_version output_status input_scale upstream_zero_blank pseudocount normalization imputation input_rows input_samples numerator denominator contrast_id eligible_before_fit bh_m bh_method bh_n bh_scope lmFit_method lmFit_ndups lmFit_spacing lmFit_weights lmFit_block lmFit_correlation contrasts_fit_coefficients eBayes_proportion eBayes_stdev_coef_lim eBayes_trend eBayes_span eBayes_robust eBayes_winsor_tail_p eBayes_legacy seed FC_definition warning_count warning_codes R_version limma_version statmod_version'.split())
FIXED={'schema_version':'limma_core_v0.1','input_scale':'log2','upstream_zero_blank':'NA','pseudocount':'0',
 'normalization':'none','imputation':'none','numerator':'g1','denominator':'g2','bh_method':'BH',
 'bh_scope':'all_final_testable_finite_P_for_one_contrast_before_DEP_thresholds',
 'lmFit_method':'ls','lmFit_ndups':'1','lmFit_spacing':'1','lmFit_weights':'NULL','lmFit_block':'NULL',
 'lmFit_correlation':'NULL','contrasts_fit_coefficients':'NULL','eBayes_proportion':'0.01','eBayes_stdev_coef_lim':'0.1,4',
 'eBayes_trend':'FALSE','eBayes_span':'NULL','eBayes_robust':'FALSE','eBayes_winsor_tail_p':'0.05,0.1','eBayes_legacy':'NULL',
 'seed':'not_used_no_stochastic_operation','FC_definition':'2^log2FC_geometric_scale_ratio_not_raw_arithmetic_mean_ratio',
 'R_version':'4.6.1','limma_version':'3.68.5','statmod_version':'1.5.2'}
NUMBER=re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z')
REASONS=frozenset(('invalid_numeric_token','insufficient_numerator_observations','insufficient_denominator_observations',
 'invalid_fit_or_residual_df','nonfinite_or_invalid_moderated_result'))

class InvalidOutput(ValueError):
    def __init__(self,code):self.code=code;super().__init__(code)
def need(ok,code='limma_output_invalid'):
    if not ok:raise InvalidOutput(code)
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def pin(body):return {'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()}
def table(body,header,maximum):
    need(type(body) is bytes and 0<len(body)<=MAX_FILE and body.endswith(b'\n')
        and b'\r' not in body and b'\0' not in body and not body.startswith(b'\xef\xbb\xbf'))
    try:rows=[tuple(line.split('\t')) for line in body.decode('utf8').split('\n')[:-1]]
    except UnicodeError:raise InvalidOutput('limma_output_invalid') from None
    need(1<=len(rows)<=maximum+1 and rows[0]==tuple(header) and all(len(r)==len(header) for r in rows))
    return rows[1:]
def integer(token):
    need(re.fullmatch('0|[1-9][0-9]*',token) is not None);return int(token)
def number(token):
    need(NUMBER.fullmatch(token) is not None)
    value=float(token);need(math.isfinite(value));return value

def inputs(bound):
    need(type(bound) is BoundInput,'limma_bound_input_required')
    need(1<=len(bound.rows)<=10000 and 4<=len(bound.samples)<=64 and bound.design_rank==2
        and bound.contrast_weights==(1,-1),'limma_bound_input_invalid')
    need(type(bound.matrix_tsv) is bytes and len(bound.matrix_tsv)<=MAX_INPUT
        and type(bound.design_tsv) is bytes and len(bound.design_tsv)<=MAX_DESIGN
        and type(bound.preset_tsv) is bytes and len(bound.preset_tsv)<=4096,'limma_bound_input_invalid')
    samples=bound.samples;keys=tuple('s'+str(i) for i in range(1,len(samples)+1))
    need(tuple(s.sample_key for s in samples)==keys,'limma_bound_input_invalid')
    design=table(bound.design_tsv,('sample_key','group_key','replicate_key','replicate_type','confirmed'),64)
    need(design==[(s.sample_key,s.group_key,s.replicate_key,'biological','true') for s in samples]
        and all(s.group_key in ('g1','g2') for s in samples),'limma_bound_input_invalid')
    need(all(sum(s.group_key==g for s in samples)>=2 for g in ('g1','g2')),'limma_bound_input_invalid')
    need(bound.numeric_design==tuple((1,0) if s.group_key=='g1' else (0,1) for s in samples),'limma_bound_input_invalid')
    expected=(('schema_version','limma_core_v0.1'),('input_scale','log2'),('upstream_transform','positive_log2'),
        ('upstream_zero_blank','NA'),('pseudocount','0'),('normalization','none'),('imputation','none'),
        ('replicate_type','biological'),('design_confirmed','true'),('contrast_id',bound.contrast.contrast_id),
        ('numerator','g1'),('denominator','g2'))
    need(table(bound.preset_tsv,('key','value'),12)==list(expected),'limma_bound_input_invalid')
    matrix=table(bound.matrix_tsv,('source_row_ordinal','protein_ids_raw',*keys),10000)
    need(len(matrix)==len(bound.rows),'limma_bound_input_invalid')
    previous=0
    for row,lineage in zip(matrix,bound.rows):
        ordinal=integer(row[0]);need(previous<ordinal and (ordinal,row[1])==(lineage.source_row_ordinal,lineage.protein_ids_raw),'limma_bound_input_invalid')
        previous=ordinal
        for token in row[2:]:
            if token!='NA':number(token)
    return matrix,design

@dataclass(frozen=True,slots=True)
class Artifact:
    name:str
    bytes:int
    sha256:str
    body:bytes=field(repr=False)
@dataclass(frozen=True,slots=True)
class Bundle:
    exit_code:int
    status:str
    warning_count:int
    artifacts:tuple[Artifact,...]=field(repr=False)
    manifest_sha256:str
    tested_source_ordinals:tuple
    published:bool=False
    statistics_recomputed:bool=False

def validate(bound,exit_code,outputs,*,check):
    need(callable(check));check()
    matrix,design=inputs(bound)
    need(type(exit_code) is int and exit_code in (0,3,4),'limma_executor_failed')
    need(type(outputs) is dict and set(outputs)==set(OUTPUTS),'limma_output_set_invalid')
    frozen={name:outputs[name] for name in OUTPUTS}
    need(all(type(b) is bytes and 0<len(b)<=MAX_FILE for b in frozen.values())
        and sum(map(len,frozen.values()))<=MAX_TOTAL,'limma_output_size_limit')
    audit_rows=table(frozen['method-audit.tsv'],('key','value'),len(AUDIT_KEYS))
    need(tuple(r[0] for r in audit_rows)==AUDIT_KEYS)
    audit=dict(audit_rows);need(all(audit.get(k)==v for k,v in FIXED.items()))
    need(audit['input_rows']==str(len(bound.rows)) and audit['input_samples']==str(len(bound.samples))
        and audit['contrast_id']==bound.contrast.contrast_id)
    warnings=integer(audit['warning_count'])
    need(audit['warning_codes']==('limma_warning_review_required' if warnings else ''))
    results=table(frozen['results.tsv'],RESULT_HEADER,len(bound.rows))
    tests=table(frozen['test-set.tsv'],('source_row_ordinal','included_in_BH','P.Value','test_status','reason'),len(bound.rows))
    need(len(results)==len(tests)==len(bound.rows))
    tested=[];eligible=0
    for index,(row,test,source) in enumerate(zip(results,tests,matrix)):
        check();r=dict(zip(RESULT_HEADER,row))
        need(row[:2]==source[:2] and r['contrast_id']==bound.contrast.contrast_id)
        counts=tuple(sum(token!='NA' and sample.group_key==group for token,sample in zip(source[2:],bound.samples)) for group in ('g1','g2'))
        need(tuple(integer(r[k]) for k in ('n_obs_num','n_obs_den'))==counts)
        eligible+=int(all(n>=2 for n in counts))
        fits=tuple(integer(r[k]) for k in ('n_fit_num','n_fit_den'));need(all(n<=obs for n,obs in zip(fits,counts)))
        yes=r['test_status']=='testable';need(r['test_status'] in ('testable','not_testable'))
        need(test==(source[0],'TRUE' if yes else 'FALSE',r['P.Value'],r['test_status'],r['reason']))
        if yes:
            need(all(n>=2 for n in counts) and fits==counts and r['reason']=='')
            for key in ('log2FC','AveExpr','moderated_t','df_residual','df_total','P.Value','adj.P.Val'):number(r[key])
            need(number(r['df_residual'])>0 and number(r['df_total'])>0 and 0<=number(r['P.Value'])<=1 and 0<=number(r['adj.P.Val'])<=1)
            need(r['FC_reason'] in ('','overflow','underflow','invalid'))
            if r['FC_reason']=='':need(number(r['FC'])>0)
            else:need(r['FC']=='NA')
            tested.append(int(source[0]))
        else:
            need(bool(r['reason']) and all(code in REASONS for code in r['reason'].split(';'))
                and r['FC_reason']=='not_testable')
            need(all(r[key]=='NA' for key in ('log2FC','FC','AveExpr','moderated_t','df_residual','df_total','P.Value','adj.P.Val')))
    need(audit['eligible_before_fit']==str(eligible) and audit['bh_m']==audit['bh_n']==str(len(tested)))
    expected_status='NO_TESTABLE_ROWS' if not tested else 'REVIEW_REQUIRED_LIMMA_WARNING' if warnings else 'COMPUTED_CANDIDATE'
    need(audit['output_status']==expected_status and exit_code==(3 if not tested else 4 if warnings else 0),'limma_exit_status_mismatch')
    sample_rows=table(frozen['sample-audit.tsv'],('matrix_column_ordinal','sample_key','group_key','replicate_key','replicate_type','confirmed','contrast_weight'),64)
    need(sample_rows==[(str(i),*row,'1' if row[1]=='g1' else '-1') for i,row in enumerate(design,1)])
    session=frozen['R-session.txt']
    need(b'\0' not in session and b'\r' not in session and session.endswith(b'\n'))
    try:text=session.decode('utf8')
    except UnicodeError:raise InvalidOutput('limma_output_invalid') from None
    need(text.startswith('Scientific core candidate; package/runtime/session provenance follows.\nUTC: ')
        and 'R version 4.6.1' in text and 'limma_3.68.5' in text and 'statmod_1.5.2' in text)
    artifacts=tuple(Artifact(name,len(frozen[name]),hashlib.sha256(frozen[name]).hexdigest(),frozen[name]) for name in OUTPUTS)
    manifest=[{'name':a.name,'bytes':a.bytes,'sha256':a.sha256} for a in artifacts]
    check();return Bundle(exit_code,expected_status,warnings,artifacts,hashlib.sha256(canonical(manifest)).hexdigest(),tuple(tested))
