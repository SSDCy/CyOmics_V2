"""Full bytes validator for Total from a real saved, pinned None parent."""
import csv
import io
import math
import statistics
from . import log2_matrix_validator as old
from . import total_processed_profile as profile

OUTPUTS=old.OUTPUTS
MAX_FILE=old.MAX_FILE
MAX_TOTAL=old.MAX_TOTAL
ValidationFailure=old.ValidationFailure


def validate_bundle(original,selected,family,flags,outputs,*,check,parent,parent_outputs,parameters,owner):
    """No metadata is rewritten to None and no normalization is skipped."""
    try:
        from . import preprocess_contract as c
        profile.parent_metadata(parent,parent['source'],parameters,family,owner)
        profile.parent_artifacts(parent,parent_outputs)
        return _validate(original,selected,family,flags,outputs,check,parent,parameters,parent_outputs)
    except ValidationFailure:raise
    except BaseException:raise ValidationFailure('total_validation_unconfirmed') from None


def _validate(original,selected,family,flags,outputs,check,parent,params,parent_outputs):
    from . import preprocess_contract as c
    old._pulse(check)
    # Full original source, flags, row lineage, four masks, log2 and legacy summary.
    before=old.validate(original,selected,family,flags,parent_outputs,check=check)
    old._need(before.manifest_sha256==parent['manifest_sha256']
        and before.source_sha256==parent['source']['source_sha256']
        and before.selected_sha256==c.bytes_sha(selected),'total_parent_binding_mismatch')
    old._need(type(outputs) is dict and set(outputs)==set(OUTPUTS),'missing_or_extra_artifact')
    actual=dict(outputs)
    old._need(all(type(b) is bytes and 1<=len(b)<=MAX_FILE for b in actual.values())
        and sum(map(len,actual.values()))<=MAX_TOTAL,'artifact_size_limit')
    old._need(len(actual['summary.tsv'])<=1048576,'artifact_size_limit')
    for name in OUTPUTS:
        if name not in ('working-matrix.tsv','summary.tsv','parameters.json','R-session-info.txt'):
            old._need(actual[name]==parent_outputs[name],'total_parent_artifact_changed')
    old._need(actual['parameters.json']==c.canonical(params),'parameter_bytes_mismatch')
    old._session(actual['R-session-info.txt'])
    left=list(old._table(parent_outputs['working-matrix.tsv']))
    right=list(old._table(actual['working-matrix.tsv']))
    old._need(len(left)==len(right) and left[0]==right[0],'total_shape_mismatch')
    width=len(left[0]);n=len(left)-1
    old._need(1<=n<=10000 and 3<=width<=512 and n*(width-2)<=2000000,'total_shape_mismatch')
    columns=[[] for _ in range(width-2)]
    missing=[0]*(width-2)
    for ordinal,(a,b) in enumerate(zip(left[1:],right[1:])):
        if ordinal%64==0:old._pulse(check)
        old._need(len(a)==len(b)==width and a[:2]==b[:2],'row_lineage_mismatch')
        for j,token in enumerate(a[2:]):
            if token=='NA':missing[j]+=1;old._need(b[j+2]=='NA','mask_semantics_mismatch')
            else:columns[j].append(old._number(token))
    old._need(all(columns),'total_insufficient_observed_values')
    raw_table=list(old._table(parent_outputs['raw-retained.tsv']))
    totals=[math.fsum(old._number(r[j+2]) for r,a in zip(raw_table[1:],left[1:]) if a[j+2]!='NA') for j in range(width-2)]
    old._need(all(math.isfinite(v) and v>0 for v in totals),'total_invalid_totals')
    reference=statistics.median(totals);factors=[reference/v for v in totals];offsets=[math.log2(v) for v in factors]
    old._need(math.isfinite(reference) and all(math.isfinite(v) and v>0 for v in factors) and all(map(math.isfinite,offsets)),'total_nonfinite_factors')
    after=[[] for _ in columns]
    for ordinal,(a,b) in enumerate(zip(left[1:],right[1:])):
        if ordinal%64==0:old._pulse(check)
        for j,token in enumerate(a[2:]):
            if token!='NA':
                y=old._number(b[j+2]);want=old._number(token)+offsets[j]
                old._need(math.isclose(y,want,rel_tol=1e-12,abs_tol=1e-12),'total_value_mismatch')
                after[j].append(y)
    masks={name:list(old._table(parent_outputs[name])) for name in ('original-blank-mask.tsv','original-zero-mask.tsv','imputed-mask.tsv')}
    summary=list(old._table(actual['summary.tsv']))
    old._need(summary and summary[0]==list(profile.SUMMARY_COLUMNS) and len(summary)==len(columns)+1,'total_summary_shape')
    fixed=[profile.STRATEGY,profile.CORE_CONTRACT,'total','none','FALSE',parent['processed_dataset_version_id'],
        parent['manifest_sha256'],parent['parent_dataset_version_id'],parent['file_id'],parent['source']['source_sha256']]
    for j,row in enumerate(summary[1:]):
        old._pulse(check)
        counts=[len(columns[j]),missing[j],0,*[sum(r[j+2]=='TRUE' for r in masks[name][1:]) for name in ('original-blank-mask.tsv','original-zero-mask.tsv')]]
        old._need(len(row)==len(profile.SUMMARY_COLUMNS) and row[:10]==fixed
            and row[10:17]==[str(j+1),left[0][j+2],*[str(x) for x in counts]],'total_summary_counts')
        expected=(totals[j],reference,factors[j],offsets[j],totals[j]*factors[j])
        for token,want in zip(row[17:22],expected):
            old._need(math.isclose(old._number(token),want,rel_tol=1e-12,abs_tol=1e-12),'total_summary_factor')
        warnings=[]
        if len(set(map(len,columns)))>1:warnings.append('unequal_observed_counts_missingness_bias_not_corrected')
        if any(len(col)<2 for col in columns):warnings.append('sample_with_single_observation')
        old._need(row[22]==';'.join(warnings),'total_summary_warnings')
    artifacts=tuple(old.Artifact(name,len(actual[name]),old._hash(actual[name],check),
        'application/json' if name.endswith('.json') else 'text/plain; charset=utf-8' if name.endswith('.txt') else 'text/tab-separated-values; charset=utf-8',actual[name]) for name in OUTPUTS)
    manifest=old._canonical([{'name':a.name,'bytes':a.length,'sha256':a.sha256,'media_type':a.media_type} for a in artifacts])
    old._pulse(check)
    return old.ValidatedLog2Bundle(family,before.source_rows,n,width-2,before.source_sha256,
        before.selected_sha256,c.bytes_sha(c.canonical(params)),c.bytes_sha(manifest),artifacts,manifest)
