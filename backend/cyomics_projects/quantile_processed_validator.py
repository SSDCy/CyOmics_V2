"""Full bytes validator for Quantile from a real saved, pinned None parent."""
import csv
import io
import math
import statistics
from . import log2_matrix_validator as old
from . import quantile_processed_profile as profile

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
    except BaseException:raise ValidationFailure('quantile_validation_unconfirmed') from None


def _validate(original,selected,family,flags,outputs,check,parent,params,parent_outputs):
    from . import preprocess_contract as c
    old._pulse(check)
    # Full original source, flags, row lineage, four masks, log2 and legacy summary.
    before=old.validate(original,selected,family,flags,parent_outputs,check=check)
    old._need(before.manifest_sha256==parent['manifest_sha256']
        and before.source_sha256==parent['source']['source_sha256']
        and before.selected_sha256==c.bytes_sha(selected),'quantile_parent_binding_mismatch')
    old._need(type(outputs) is dict and set(outputs)==set(OUTPUTS),'missing_or_extra_artifact')
    actual=dict(outputs)
    old._need(all(type(b) is bytes and 1<=len(b)<=MAX_FILE for b in actual.values())
        and sum(map(len,actual.values()))<=MAX_TOTAL,'artifact_size_limit')
    old._need(len(actual['summary.tsv'])<=1048576,'artifact_size_limit')
    for name in OUTPUTS:
        if name not in ('working-matrix.tsv','summary.tsv','parameters.json','R-session-info.txt'):
            old._need(actual[name]==parent_outputs[name],'quantile_parent_artifact_changed')
    old._need(actual['parameters.json']==c.canonical(params),'parameter_bytes_mismatch')
    _quantile_session(actual['R-session-info.txt'])
    left=list(old._table(parent_outputs['working-matrix.tsv']))
    right=list(old._table(actual['working-matrix.tsv']))
    old._need(len(left)==len(right) and left[0]==right[0],'quantile_shape_mismatch')
    width=len(left[0]);n=len(left)-1
    old._need(1<=n<=10000 and 3<=width<=512 and n*(width-2)<=2000000,'quantile_shape_mismatch')
    columns=[[] for _ in range(width-2)]
    missing=[0]*(width-2)
    for ordinal,(a,b) in enumerate(zip(left[1:],right[1:])):
        if ordinal%64==0:old._pulse(check)
        old._need(len(a)==len(b)==width and a[:2]==b[:2],'row_lineage_mismatch')
        for j,token in enumerate(a[2:]):
            if token=='NA':missing[j]+=1;old._need(b[j+2]=='NA','mask_semantics_mismatch')
            else:columns[j].append(old._number(token))
    old._need(all(columns),'quantile_insufficient_observed_values')
    old._need(all(len(col)>=2 for col in columns),'quantile_insufficient_observed_values')
    ordered=[sorted(col) for col in columns]
    def interpolate(values,position):
        k=min(len(values)-2,math.floor(position));t=position-k
        return values[k]+(values[k+1]-values[k])*t
    if len(columns)==1:targets=[{v:v for v in columns[0]}]
    else:
        grid=[math.fsum(interpolate(col,i*(len(col)-1)/(n-1)) for col in ordered)/len(columns) for i in range(n)]
        targets=[]
        for col in ordered:
            target={};start=0
            while start<len(col):
                end=start+1
                while end<len(col) and col[end]==col[start]:end+=1
                rank=(start+1+end)/2
                target[col[start]]=interpolate(grid,(rank-1)/(len(col)-1)*(n-1));start=end
            targets.append(target)
    after=[[] for _ in columns];delta=[[] for _ in columns]
    for ordinal,(a,b) in enumerate(zip(left[1:],right[1:])):
        if ordinal%64==0:old._pulse(check)
        for j,token in enumerate(a[2:]):
            if token!='NA':
                original=old._number(token);y=old._number(b[j+2]);want=targets[j][original]
                old._need(math.isclose(y,want,rel_tol=1e-12,abs_tol=1e-12),'quantile_value_mismatch')
                after[j].append(y);delta[j].append(y-original)
    masks={name:list(old._table(parent_outputs[name])) for name in ('original-blank-mask.tsv','original-zero-mask.tsv','imputed-mask.tsv')}
    summary=list(old._table(actual['summary.tsv']))
    old._need(summary and summary[0]==list(profile.SUMMARY_COLUMNS) and len(summary)==len(columns)+1,'quantile_summary_shape')
    fixed=[profile.STRATEGY,profile.CORE_CONTRACT,'quantile','none','FALSE',parent['processed_dataset_version_id'],
        parent['manifest_sha256'],parent['parent_dataset_version_id'],parent['file_id'],parent['source']['source_sha256']]
    for j,row in enumerate(summary[1:]):
        old._pulse(check)
        counts=[len(columns[j]),missing[j],0,*[sum(r[j+2]=='TRUE' for r in masks[name][1:]) for name in ('original-blank-mask.tsv','original-zero-mask.tsv')]]
        old._need(len(row)==len(profile.SUMMARY_COLUMNS) and row[:10]==fixed
            and row[10:17]==[str(j+1),left[0][j+2],*[str(x) for x in counts]],'quantile_summary_counts')
        expected=(statistics.median(columns[j]),statistics.median(after[j]),min(delta[j]),max(delta[j]))
        for token,want in zip(row[17:21],expected):
            old._need(math.isclose(old._number(token),want,rel_tol=1e-12,abs_tol=1e-12),'quantile_summary_factor')
        warnings=['quantile_changes_observed_distribution_global_shift_may_be_removed']
        if len(set(map(len,columns)))>1:warnings.append('unequal_missingness_interpolated_quantile_curves_no_cell_imputation')
        if any(missing) or any(len(set(col))<len(col) for col in columns):warnings.append('missing_or_tied_values_no_exact_empirical_distribution_equality_claim')
        if len(columns)==1:warnings.append('single_sample_native_identity_no_crosssample_normalization')
        old._need(row[21:]==['NA','not_scalar_rank_transform',';'.join(warnings)],'quantile_summary_nonscalar_or_warnings')
    artifacts=tuple(old.Artifact(name,len(actual[name]),old._hash(actual[name],check),
        'application/json' if name.endswith('.json') else 'text/plain; charset=utf-8' if name.endswith('.txt') else 'text/tab-separated-values; charset=utf-8',actual[name]) for name in OUTPUTS)
    manifest=old._canonical([{'name':a.name,'bytes':a.length,'sha256':a.sha256,'media_type':a.media_type} for a in artifacts])
    old._pulse(check)
    return old.ValidatedLog2Bundle(family,before.source_rows,n,width-2,before.source_sha256,
        before.selected_sha256,c.bytes_sha(c.canonical(params)),c.bytes_sha(manifest),artifacts,manifest)


def _quantile_session(raw):
    import re
    _need=old._need
    _need(b'\0' not in raw and b'\r' not in raw and not raw.startswith(b'\xef\xbb\xbf') and raw.endswith(b'\n'),'invalid_session_text')
    try:text=raw.decode('utf8')
    except UnicodeError:raise ValidationFailure('invalid_session_text') from None
    _need(re.match(r'R version 4\.6\.1(?: |\n)',text)is not None and text.count('attached base packages:')==1,'invalid_session_text')
    # Parse the whole package sections, not merely recognized substrings.
    base_marker='attached base packages:'
    marker='loaded via a namespace (and not attached):'
    headings=re.findall(r'(?im)^[^\n]*attached[^\n]*packages[^\n]*:[ \t]*$',text)
    _need(headings==[base_marker],'invalid_session_text')
    namespace_headings=re.findall(r'(?im)^[^\n]*namespace[^\n]*:[ \t]*$',text)
    _need(namespace_headings in ([],[marker]) and text.count(marker)<=1,'invalid_session_text')
    before,tail=text.split(base_marker+'\n',1)
    _need(marker not in before,'invalid_session_text')
    if marker in tail:
        attached,namespace=tail.split(marker+'\n',1)
    else:attached=tail;namespace=None
    def vector_tokens(body):
        tokens=[]
        for line in body.splitlines():
            if not line.strip():continue
            match=re.fullmatch(r'\s*\[([1-9][0-9]*)\]\s+(.+?)\s*',line)
            _need(match is not None and int(match[1])==len(tokens)+1,'invalid_session_text')
            tokens.extend(match[2].split())
        return tokens
    names=vector_tokens(attached)
    _need(len(names)==7 and set(names)=={'stats','graphics','grDevices','utils','datasets','methods','base'},'invalid_session_text')
    _need(namespace is not None,'invalid_quantile_session_packages')
    packages=vector_tokens(namespace)
    _need(len(packages)==4 and set(packages)=={'compiler_4.6.1','limma_'+profile.DEPENDENCIES['limma_version'],'digest_'+profile.DEPENDENCIES['digest_version'],'statmod_'+profile.DEPENDENCIES['statmod_version']},'invalid_quantile_session_packages')
