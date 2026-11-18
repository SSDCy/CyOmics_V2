"""Pure bytes consistency validator; no R execution, filesystem, Auth or publish.

The caller supplies immutable source bytes and a trusted server check callback.
Returned hashes describe those actual bytes, not execution/Owner attestation.
"""
import csv,hashlib,io,json,math,re
from dataclasses import dataclass,field

MAX_INPUT=25*1024*1024
MAX_SELECTED=128*1024
MAX_FILE=25*1024*1024
MAX_TOTAL=128*1024*1024
MAX_FIELD=65536
MAX_ROWS=10000
MAX_COLUMNS=512
MAX_CELLS=2000000
RELATIVE_TOLERANCE=1e-12
ABSOLUTE_TOLERANCE=0.0
OUTPUTS=tuple(sorted(('filter-log.tsv','row-map.tsv','summary.tsv','raw-retained.tsv','working-matrix.tsv',
 'original-blank-mask.tsv','original-zero-mask.tsv','working-NA-mask.tsv','imputed-mask.tsv','R-session-info.txt','parameters.json')))
_MATRICES=('raw-retained.tsv','working-matrix.tsv','original-blank-mask.tsv','original-zero-mask.tsv','working-NA-mask.tsv','imputed-mask.tsv')
_UNSIGNED=re.compile(r'[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z')
_SIGNED=re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z')

class ValidationFailure(ValueError):
    def __init__(self,code):self.code=code;super().__init__(code)

def _need(value,code):
    if not value:raise ValidationFailure(code)

def _pulse(check):
    try:value=check()
    except BaseException:raise ValidationFailure('validation_interrupted') from None
    _need(value is None,'invalid_check_result')

def _hash(raw,check):
    value=hashlib.sha256()
    for start in range(0,len(raw),65536):
        _pulse(check);value.update(raw[start:start+65536])
    return value.hexdigest()

def _canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf8')

def parameters(flags):
    """Exact internal three-bool tuple; not client configuration/authorization."""
    _need(type(flags)is tuple and len(flags)==3 and all(type(v)is bool for v in flags),'invalid_flags')
    return _canonical({'blank_policy':'to_NA','filter_flags':dict(zip(('reverse','contaminant','site_only'),flags)),
        'imputation':'none','input_scale':'raw_nonnegative','log2':'positive_only','normalization':'none',
        'output_scale':'log2','preset':'positive_log2_zero_na_no_impute_v1','pseudocount':0,'seed':None,
        'steps':['flags_filter','zero_and_blank_to_NA','log2_positive','normalization_none','imputation_none'],'zero_policy':'to_NA'})

def _source(raw,cap,check):
    _need(type(raw)is bytes and 1<=len(raw)<=cap and b'\0' not in raw,'invalid_source_bytes')
    normalized=raw.replace(b'\r\n',b'\n');_need(b'\r' not in normalized,'unsupported_source_line_ending')
    try:text=normalized.decode('utf-8-sig')
    except UnicodeError:raise ValidationFailure('invalid_source_utf8') from None
    lines=text.split('\n')
    if lines[-1]=='':lines.pop()
    _need(2<=len(lines)<=MAX_ROWS+1,'invalid_source_shape')
    result=[];width=None
    for i,line in enumerate(lines):
        if i%64==0:_pulse(check)
        row=line.split('\t');width=len(row) if width is None else width
        _need(2<=width<=MAX_COLUMNS and len(row)==width and all(len(v)<=MAX_FIELD for v in row),'invalid_source_shape')
        result.append(row)
    _need((len(result)-1)*width<=MAX_CELLS,'invalid_source_shape')
    _need(len(set(result[0]))==len(result[0]),'duplicate_source_columns')
    return result

def _number(token,raw=False):
    _need(type(token)is str and (_UNSIGNED if raw else _SIGNED).fullmatch(token)is not None,'invalid_numeric_cell')
    value=float(token);_need(math.isfinite(value),'invalid_numeric_cell')
    mantissa=re.split('[eE]',token,maxsplit=1)[0]
    _need(value!=0 or not any(c in '123456789' for c in mantissa),'numeric_underflow')
    return value

def _table(raw):
    _need(b'\0' not in raw and b'\r' not in raw and raw.endswith(b'\n') and not raw.startswith(b'\xef\xbb\xbf'),'invalid_artifact_text')
    try:text=raw.decode('utf8')
    except UnicodeError:raise ValidationFailure('invalid_artifact_text') from None
    return csv.reader(io.StringIO(text,newline=''),delimiter='\t',strict=True)

def _next(reader,width):
    try:row=next(reader)
    except (StopIteration,csv.Error,UnicodeError):raise ValidationFailure('artifact_shape_mismatch') from None
    _need(len(row)==width and all(len(v)<=MAX_FIELD for v in row),'artifact_shape_mismatch')
    return row

def _end(reader):
    try:next(reader)
    except StopIteration:return
    except (csv.Error,UnicodeError):raise ValidationFailure('artifact_shape_mismatch') from None
    raise ValidationFailure('artifact_shape_mismatch')

def _exact_rows(raw,expected,check):
    reader=_table(raw);width=len(expected[0])
    for i,want in enumerate(expected):
        if i%64==0:_pulse(check)
        _need(_next(reader,width)==want,'artifact_value_mismatch')
    _end(reader)

def _session(raw):
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
    if namespace is not None:
        packages=vector_tokens(namespace)
        _need(len(packages)<=1 and all(re.fullmatch(r'compiler_[0-9]+(?:\.[0-9]+)*',token)is not None for token in packages),'invalid_session_text')

@dataclass(frozen=True,slots=True)
class Artifact:
    name:str
    length:int
    sha256:str
    media_type:str
    _bytes:bytes=field(repr=False)
    @property
    def body(self):return self._bytes

@dataclass(frozen=True,slots=True)
class ValidatedLog2Bundle:
    family:str
    source_rows:int
    retained_rows:int
    selected_columns:int
    source_sha256:str
    selected_sha256:str
    parameters_sha256:str
    manifest_sha256:str
    artifacts:tuple[Artifact,...]=field(repr=False)
    _manifest:bytes=field(repr=False)
    bytes_consistency_verified:bool=True
    tool_execution_attested:bool=False
    owner_authorized:bool=False
    publication_performed:bool=False
    analysis_ready:bool=False
    @property
    def manifest_bytes(self):return self._manifest

def validate(original,selected,family,flags,outputs,*,check):
    """Validate one nonempty complete11-output result, return no partial bundle.

    check is an explicit trusted server budget/cancellation callback. It must not
    originate in client inputs. This function does no Auth or execution attesting.
    """
    try:return _validate(original,selected,family,flags,outputs,check)
    except ValidationFailure:raise
    except BaseException:raise ValidationFailure('validation_unconfirmed') from None

def _validate(original,selected,family,flags,outputs,check):
    _need(callable(check),'trusted_check_required');_pulse(check)
    _need(type(family)is str and family in ('Intensity','LFQ intensity'),'invalid_family')
    params=parameters(flags)
    _need(type(outputs)is dict,'missing_or_extra_artifact')
    # Detach byte references before any later callback; validate complete output
    # shape only after source empty/invalid disposition is established.
    snapshot=dict(outputs)
    table=_source(original,MAX_INPUT,check);descriptors=_source(selected,MAX_SELECTED,check)
    header=table[0];rows=table[1:]
    _need(header.count('Protein IDs')==1,'required_protein_ids');protein=header.index('Protein IDs')
    indices=[i for i,name in enumerate(header) if name.startswith(family+' ')]
    _need(indices and all(len(header[i])>len(family)+1 for i in indices),'missing_sample_columns')
    _need(descriptors[0]==['column_ordinal','name_raw','quant_family','sample_label_raw'],'selected_binding_mismatch')
    _need(descriptors[1:]==[[str(i),header[i],family,header[i][len(family)+1:]] for i in indices],'selected_binding_mismatch')
    flag_names=('Reverse','Potential contaminant','Only identified by site');reason_names=('reverse','potential_contaminant','site_only')
    positions=[]
    for name,enabled in zip(flag_names,flags):
        _need(name in header or not enabled,'missing_enabled_flag');positions.append(header.index(name) if name in header else None)
    mapping=[['source_record_number','protein_ids_raw','kept','work_row_ordinal']]
    log=[['record_number','protein_ids_raw','excluded_reverse','excluded_contaminant','excluded_site_only','excluded','reason_codes']]
    kept=[];causes=[];counts={'source_blank':0,'source_zero':0,'source_positive':0,'blank':0,'zero':0,'positive':0}
    for ordinal,row in enumerate(rows,1):
        if ordinal%64==1:_pulse(check)
        cells=[row[i] for i in indices];numbers=[]
        for token in cells:
            if token=='':counts['source_blank']+=1;numbers.append(None)
            else:
                value=_number(token,raw=True);numbers.append(value)
                counts['source_zero' if value==0 else 'source_positive']+=1
        why=[]
        for position,enabled in zip(positions,flags):
            token='' if position is None else row[position];_need(token in ('','+'),'invalid_flag_token')
            why.append(enabled and token=='+')
        excluded=any(why);causes.append(why);work_ordinal='NA' if excluded else str(len(kept)+1)
        mapping.append([str(ordinal),row[protein],str(not excluded).upper(),work_ordinal])
        log.append([str(ordinal),row[protein],*[str(v).upper() for v in why],str(excluded).upper(),'|'.join(name for name,yes in zip(reason_names,why) if yes)])
        if not excluded:kept.append((ordinal,row[protein],cells,numbers))
    _need(kept,'empty_not_analyzable')
    _need(set(snapshot)==set(OUTPUTS),'missing_or_extra_artifact')
    frozen=tuple((name,snapshot[name]) for name in OUTPUTS)
    _need(all(type(b)is bytes and 1<=len(b)<=MAX_FILE for name,b in frozen),'artifact_size_limit')
    _need(sum(len(b) for name,b in frozen)<=MAX_TOTAL,'artifact_size_limit')
    actual=dict(frozen)
    prefix=['record_number','protein_ids_raw'];matrix_header=prefix+[header[i] for i in indices]
    readers={name:_table(actual[name]) for name in _MATRICES}
    for reader in readers.values():_need(_next(reader,len(matrix_header))==matrix_header,'artifact_header_mismatch')
    for position,(ordinal,protein_id,cells,numbers) in enumerate(kept):
        if position%64==0:_pulse(check)
        records={name:_next(reader,len(matrix_header)) for name,reader in readers.items()}
        _need(all(record[:2]==[str(ordinal),protein_id] for record in records.values()),'row_lineage_mismatch')
        _need(records['raw-retained.tsv'][2:]==cells,'raw_value_mismatch')
        for j,value in enumerate(numbers,2):
            blank=value is None;zero=value==0 if not blank else False;missing=blank or zero
            want=[blank,zero,missing,False]
            observed=[records[name][j] for name in ('original-blank-mask.tsv','original-zero-mask.tsv','working-NA-mask.tsv','imputed-mask.tsv')]
            _need(observed==[str(v).upper() for v in want],'mask_semantics_mismatch')
            token=records['working-matrix.tsv'][j]
            if missing:_need(token=='NA','working_value_mismatch')
            else:
                number=_number(token);mantissa,exponent=math.frexp(value)
                expected=float(exponent-1) if mantissa==0.5 else math.log2(value)
                _need(number==expected if mantissa==0.5 else math.isclose(number,expected,rel_tol=RELATIVE_TOLERANCE,abs_tol=ABSOLUTE_TOLERANCE),'log2_value_mismatch')
            counts['blank' if blank else 'zero' if zero else 'positive']+=1
    for reader in readers.values():_end(reader)
    _exact_rows(actual['row-map.tsv'],mapping,check);_exact_rows(actual['filter-log.tsv'],log,check)
    fields=['status','input_rows','input_columns','quant_family','selected_columns','input_selected_cells','kept_rows','excluded_rows',
        'excluded_reverse','excluded_contaminant','excluded_site_only','overlap_excluded_rows','source_blank_cells','source_zero_cells','source_positive_cells',
        'retained_selected_cells','retained_blank_cells','retained_zero_cells','retained_positive_cells','working_NA_cells','imputed_cells',
        'reverse_enabled','contaminant_enabled','site_only_enabled','zero_policy','blank_policy','log2','pseudocount','normalization','imputation','analysis_ready']
    values=['complete_positive_log2',len(rows),len(header),family,len(indices),len(rows)*len(indices),len(kept),len(rows)-len(kept),
        *[sum(row[i] for row in causes) for i in range(3)],sum(sum(row)>1 for row in causes),
        counts['source_blank'],counts['source_zero'],counts['source_positive'],len(kept)*len(indices),counts['blank'],counts['zero'],counts['positive'],counts['blank']+counts['zero'],0,
        *[str(v).upper() for v in flags],'to_NA','to_NA','positive_only',0,'none','none','FALSE']
    _exact_rows(actual['summary.tsv'],[fields,[str(v) for v in values]],check)
    _need(actual['parameters.json']==params,'parameter_bytes_mismatch');_session(actual['R-session-info.txt'])
    artifacts=tuple(Artifact(name,len(b),_hash(b,check),'application/json' if name.endswith('.json') else 'text/plain; charset=utf-8' if name.endswith('.txt') else 'text/tab-separated-values; charset=utf-8',b) for name,b in frozen)
    manifest=_canonical([{'name':a.name,'bytes':a.length,'sha256':a.sha256,'media_type':a.media_type} for a in artifacts])
    source_sha=_hash(original,check);selected_sha=_hash(selected,check);params_sha=_hash(params,check);manifest_sha=_hash(manifest,check)
    _pulse(check)
    return ValidatedLog2Bundle(family,len(rows),len(kept),len(indices),source_sha,selected_sha,params_sha,manifest_sha,artifacts,manifest)
