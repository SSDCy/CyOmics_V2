"""Pure bounded preprocessing request contract. No IO, authorization or job run."""
from dataclasses import dataclass, field
import hashlib
import json
import re
from uuid import UUID

CONTRACT = 'preprocessing_log2_job_v0.1'
ENABLED = False
MAX_REQUEST_BYTES = 4096
FAMILIES = ('Intensity', 'LFQ intensity')
FLAG_KEYS = ('contaminant', 'reverse', 'site_only')
REQUEST_KEYS = frozenset(('request_id', 'quant_family', 'parameters'))
PARAMETER_KEYS = frozenset(('blank_policy', 'filter_flags', 'imputation', 'input_scale',
    'log2', 'normalization', 'output_scale', 'preset', 'pseudocount', 'seed', 'steps', 'zero_policy'))
STEPS = ('flags_filter', 'zero_and_blank_to_NA', 'log2_positive', 'normalization_none', 'imputation_none')
_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z')
ERROR_CODES = frozenset(('invalid_preprocessing_request', 'preprocessing_request_too_large'))


class RequestError(ValueError):
    """Fixed safe code only; never incorporates an untrusted value or JSON error."""
    def __init__(self, code='invalid_preprocessing_request'):
        self.code = code if code in ERROR_CODES else 'invalid_preprocessing_request'
        super().__init__(self.code)


def _need(value):
    if not value:
        raise RequestError()


def _canonical_uuid(value):
    # Equivalent to validation.canonical_uuid for plain strings; no settings
    # import or persistence/configuration dependency is required for this slice.
    return type(value) is str and _UUID.fullmatch(value) is not None and str(UUID(value)) == value


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf8')


def _integer_token(token):
    if token=='-0':raise RequestError()
    return int(token)


def _document(raw):
    _need(type(raw) is bytes and len(raw) > 0 and not raw.startswith(b'\xef\xbb\xbf'))
    if len(raw) > MAX_REQUEST_BYTES:
        raise RequestError('preprocessing_request_too_large')
    def pairs(items):
        result = {}
        for key, value in items:
            _need(key not in result)
            result[key] = value
        return result
    def reject(_value):
        raise RequestError()
    value = json.loads(raw.decode('utf8', errors='strict'), object_pairs_hook=pairs,
        parse_constant=reject, parse_int=_integer_token)
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        _need(depth <= 8)
        if type(item) is str:
            item.encode('utf8', errors='strict')  # no escaped lone surrogates
        elif type(item) is dict:
            pending.extend((v, depth+1) for pair in item.items() for v in pair)
        elif type(item) is list:
            pending.extend((v, depth+1) for v in item)
        else:
            _need(type(item) in (bool, int, type(None)))  # no float/NaN/Infinity aliases
    return value


def _parameter_value(flags):
    _need(type(flags) is dict and set(flags) == set(FLAG_KEYS))
    _need(all(type(flags[key]) is bool for key in FLAG_KEYS))
    return {'blank_policy':'to_NA', 'filter_flags':{key:flags[key] for key in FLAG_KEYS},
        'imputation':'none', 'input_scale':'raw_nonnegative', 'log2':'positive_only',
        'normalization':'none', 'output_scale':'log2', 'preset':'positive_log2_zero_na_no_impute_v1',
        'pseudocount':0, 'seed':None, 'steps':list(STEPS), 'zero_policy':'to_NA'}


def canonical_parameters(filter_flags):
    """Reconstruct canonical bytes by named boolean flags; never mutate the caller."""
    return _canonical(_parameter_value(filter_flags))


def _parameters(value):
    _need(type(value) is dict and set(value) == PARAMETER_KEYS)
    expected = _parameter_value(value['filter_flags'])
    for key, wanted in expected.items():
        actual = value[key]
        if key == 'filter_flags':
            continue  # exact dict keys and strict bool types already checked
        _need(type(actual) is type(wanted) and actual == wanted)
        if key == 'steps':
            _need(all(type(item) is str for item in actual))
    return expected


@dataclass(frozen=True, slots=True)
class PreprocessingRequest:
    """Immutable normalized request, not SourceRef, Owner grant or execution proof."""
    request_id: str
    quant_family: str
    filter_flags: tuple[tuple[str, bool], ...]
    parameters_bytes: bytes = field(repr=False)
    parameters_sha256: str
    canonical_request_bytes: bytes = field(repr=False)
    request_sha256: str

    def parameters_value(self):
        """Detached JSON value for a future adapter; cannot mutate this snapshot."""
        return json.loads(self.parameters_bytes)

    def request_value(self):
        return json.loads(self.canonical_request_bytes)


def request_input(raw):
    """Parse exactly the3-key body; reject every client-supplied server binding.

    A future authenticated handler must independently resolve the route and
    authoritative SourceRef. The returned request hash is intentionally unbound
    to Owner/project/source/execution and must never be used as authorization.
    """
    try:
        value = _document(raw)
        _need(type(value) is dict and set(value) == REQUEST_KEYS)
        _need(_canonical_uuid(value['request_id']))
        family = value['quant_family']
        _need(type(family) is str and family in FAMILIES)
        parameters = _parameters(value['parameters'])
        parameter_bytes = _canonical(parameters)
        # Build all accepted keys from validated scalars and server constants,
        # never serialize the caller's original mutable dictionary by reference.
        body = {'request_id':value['request_id'], 'quant_family':family, 'parameters':parameters}
        request_bytes = _canonical(body)
        return PreprocessingRequest(value['request_id'], family,
            tuple((key,parameters['filter_flags'][key]) for key in FLAG_KEYS),
            parameter_bytes, hashlib.sha256(parameter_bytes).hexdigest(),
            request_bytes, hashlib.sha256(request_bytes).hexdigest())
    except RequestError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise RequestError() from None

# Execution v2 public contract. The admission request above remains byte-exact.
from datetime import datetime,timezone

TASK_KEYS=frozenset('schemaVersion job_id request_id request_sha256 binding_sha256 source quant_family parameters parameters_sha256 proposal_sha256 status created_at result revision started_at finished_at observation error runtime attempt_id'.split())
RUNTIME_KEYS=frozenset(('registration_id','profile_sha256','runtime_binding_sha256','attestation_sha256','receipt_sha256'))
VERSION_KEYS=frozenset('schemaVersion processed_dataset_version_id project_id parent_dataset_version_id file_id created_by created_at job_id attempt_id source quant_family steps parameters parameters_sha256 proposal_sha256 runtime tool manifest_sha256 artifacts analysis_ready'.split())
SOURCE_KEYS=frozenset('project_id dataset_version_id file_id source_bytes source_sha256 parser_contract config_sha256 report_sha256 row_count column_count selected_columns_sha256'.split())
ARTIFACT_NAMES=tuple(sorted(('filter-log.tsv','row-map.tsv','summary.tsv','raw-retained.tsv','working-matrix.tsv','original-blank-mask.tsv','original-zero-mask.tsv','working-NA-mask.tsv','imputed-mask.tsv','R-session-info.txt','parameters.json')))
TOOL_KEYS=frozenset(('program','r_version','script_sha256','validator_sha256','runner_sha256','native_sha256','process_policy_sha256'))
FAILURE_CODES=frozenset(('input_integrity_failed','execution_unavailable','resource_limit','runner_failed','output_invalid','publish_integrity_failed','empty_not_analyzable','worker_interrupted'))
MAX_RESPONSE=32768
MAX_ARTIFACT=26214400
MAX_ARTIFACT_TOTAL=134217728


def exact(value,keys):
    _need(type(value) is dict and set(value)==set(keys));return value


def hashed(value):return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None

def canonical(value):return _canonical(value)

def digest(value):return hashlib.sha256(_canonical(value)).hexdigest()

def bytes_sha(value):return hashlib.sha256(value).hexdigest()


def document(raw,cap=MAX_RESPONSE):
    _need(type(raw) is bytes and 0<len(raw)<=cap and not raw.startswith(b'\xef\xbb\xbf'))
    def pairs(items):
        value={}
        for k,v in items:
            _need(k not in value);value[k]=v
        return value
    def reject(_):raise RequestError()
    try:
        value=json.loads(raw.decode('utf8'),object_pairs_hook=pairs,parse_constant=reject, parse_int=_integer_token)
        pending=[(value,0)]
        while pending:
            item,depth=pending.pop();_need(depth<=12)
            if type(item) is str:item.encode('utf8','strict')
            elif type(item) is dict:pending.extend((v,depth+1) for pair in item.items() for v in pair)
            elif type(item) is list:pending.extend((v,depth+1) for v in item)
        return value
    except (ValueError,TypeError,UnicodeError,RecursionError,OverflowError):raise RequestError() from None


def source_value(value):
    exact(value,SOURCE_KEYS)
    _need(all(_canonical_uuid(value[k]) for k in ('project_id','dataset_version_id','file_id')))
    _need(all(hashed(value[k]) for k in ('source_sha256','config_sha256','report_sha256','selected_columns_sha256')))
    _need(value['parser_contract']=='protein_groups_txt_v1')
    for k,low,high in (('source_bytes',1,26214400),('row_count',1,10000),('column_count',2,512)):
        _need(type(value[k]) is int and low<=value[k]<=high)
    _need(value['row_count']*value['column_count']<=2000000);return value


def stamp(value):
    _need(type(value) is str and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z',value) is not None)
    try:
        dt=datetime.fromisoformat(value.replace('Z','+00:00'))
        _need(dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')==value);return dt
    except ValueError:raise RequestError() from None


def runtime_binding(job_id,binding,proposal,registration,profile,attempt):
    return digest({'contract':'preprocessing_runtime_binding_v1','job_id':job_id,'binding_sha256':binding,
        'proposal_sha256':proposal,'registration_id':registration,'profile_sha256':profile,'attempt_id':attempt})


def runtime_value(value,*,job=None):
    exact(value,RUNTIME_KEYS);_need(_canonical_uuid(value['registration_id']))
    _need(all(hashed(value[k]) for k in ('profile_sha256','runtime_binding_sha256','attestation_sha256')))
    _need(value['receipt_sha256'] is None or hashed(value['receipt_sha256']))
    if job is not None:
        _need(value['runtime_binding_sha256']==runtime_binding(job['job_id'],job['binding_sha256'],job['proposal_sha256'],
            value['registration_id'],value['profile_sha256'],job['attempt_id']))
    return value


def artifact_manifest(value):
    _need(type(value) is list and len(value)==11);total=0
    for name,item in zip(ARTIFACT_NAMES,value):
        exact(item,('name','bytes','sha256','media_type'))
        _need(item['name']==name and type(item['bytes']) is int and 1<=item['bytes']<=MAX_ARTIFACT and hashed(item['sha256']))
        media='application/json' if name.endswith('.json') else 'text/plain; charset=utf-8' if name.endswith('.txt') else 'text/tab-separated-values; charset=utf-8'
        _need(item['media_type']==media);total+=item['bytes']
    _need(total<=MAX_ARTIFACT_TOTAL);return value


def task_value(value,*,project=None,dataset=None,job=None,request_id=None,request=None):
    exact(value,TASK_KEYS);_need(type(value['schemaVersion']) is int and value['schemaVersion']==2)
    _need(all(_canonical_uuid(value[k]) for k in ('job_id','request_id')))
    _need(all(hashed(value[k]) for k in ('request_sha256','binding_sha256','parameters_sha256','proposal_sha256')))
    source=source_value(value['source'])
    _need(project is None or project==source['project_id']);_need(dataset is None or dataset==source['dataset_version_id'])
    _need(job is None or job==value['job_id']);_need(request_id is None or request_id==value['request_id'])
    parsed=request_input(canonical({'request_id':value['request_id'],'quant_family':value['quant_family'],'parameters':value['parameters']}))
    _need(request is None or parsed==request)
    _need(value['parameters_sha256']==parsed.parameters_sha256 and value['request_sha256']==digest({
        'project_id':source['project_id'],'dataset_version_id':source['dataset_version_id'],'request':parsed.request_value()}))
    created=stamp(value['created_at']);state=value['status'];observation=value['observation']
    _need(state in ('queued','running','succeeded','failed') and observation in ('confirmed','reconciliation_pending'))
    _need(type(value['revision']) is int and value['revision']>=1)
    if state=='queued':
        _need(value['revision']==1 and observation=='confirmed' and all(value[k] is None for k in
            ('started_at','finished_at','runtime','attempt_id','error','result')))
    else:
        _need(value['revision']>=2 and _canonical_uuid(value['attempt_id']) and stamp(value['started_at'])>=created)
        runtime_value(value['runtime'],job=value)
        if state=='running':_need(value['finished_at'] is None and value['error'] is None and value['result'] is None)
        else:
            _need(value['revision']>=3 and observation=='confirmed' and stamp(value['finished_at'])>=stamp(value['started_at']))
            if state=='failed':
                exact(value['error'],('code',));_need(value['error']['code'] in FAILURE_CODES and value['result'] is None)
            else:
                _need(value['error'] is None and hashed(value['runtime']['receipt_sha256']))
                exact(value['result'],('processed_dataset_version_id','manifest_sha256','artifact_count','analysis_ready'))
                result=value['result'];_need(_canonical_uuid(result['processed_dataset_version_id']) and hashed(result['manifest_sha256'])
                    and type(result['artifact_count']) is int and result['artifact_count']==11 and result['analysis_ready'] is False)
    _need(len(canonical(value))<=MAX_RESPONSE);return value


def version_value(value,*,project=None,version=None,task=None):
    exact(value,VERSION_KEYS);_need(type(value['schemaVersion']) is int and value['schemaVersion']==2)
    for k in ('processed_dataset_version_id','project_id','parent_dataset_version_id','file_id','created_by','job_id','attempt_id'):
        _need(_canonical_uuid(value[k]))
    _need(project is None or value['project_id']==project);_need(version is None or value['processed_dataset_version_id']==version)
    stamp(value['created_at']);source_value(value['source'])
    _need((value['source']['project_id'],value['source']['dataset_version_id'],value['source']['file_id'])==
        (value['project_id'],value['parent_dataset_version_id'],value['file_id']))
    params=_parameters(value['parameters']);_need(value['quant_family'] in FAMILIES and value['steps']==list(STEPS))
    _need(value['parameters_sha256']==digest(params) and hashed(value['proposal_sha256']) and value['analysis_ready'] is False)
    runtime_value(value['runtime']);_need(hashed(value['runtime']['receipt_sha256']))
    exact(value['tool'],TOOL_KEYS);_need(value['tool']['program']=='R' and value['tool']['r_version']=='R version 4.6.1')
    _need(all(hashed(value['tool'][k]) for k in TOOL_KEYS-{'program','r_version'}))
    artifact_manifest(value['artifacts']);_need(value['manifest_sha256']==digest(value['artifacts']))
    if task is not None:
        task_value(task);_need(task['status']=='succeeded' and task['result']['processed_dataset_version_id']==value['processed_dataset_version_id']
            and task['result']['manifest_sha256']==value['manifest_sha256'] and task['job_id']==value['job_id']
            and task['attempt_id']==value['attempt_id'] and task['source']==value['source'] and task['runtime']==value['runtime']
            and all(task[k]==value[k] for k in ('quant_family','parameters','parameters_sha256','proposal_sha256')))
    _need(len(canonical(value))<=MAX_RESPONSE);return value


def encode_cursor(created_at,job_id):
    import base64
    stamp(created_at);_need(_canonical_uuid(job_id))
    return base64.urlsafe_b64encode(canonical({'v':1,'createdAt':created_at,'job_id':job_id})).decode().rstrip('=')


def decode_cursor(value):
    import base64
    _need(type(value) is str and re.fullmatch('[A-Za-z0-9_-]{1,192}',value) is not None)
    try:
        raw=base64.b64decode(value+'='*(-len(value)%4),altchars=b'-_',validate=True)
        _need(len(raw)<=384 and base64.urlsafe_b64encode(raw).decode().rstrip('=')==value)
        item=document(raw,384);exact(item,('v','createdAt','job_id'))
        _need(type(item['v']) is int and item['v']==1);stamp(item['createdAt']);_need(_canonical_uuid(item['job_id']))
        _need(encode_cursor(item['createdAt'],item['job_id'])==value);return item
    except (ValueError,TypeError,UnicodeError):raise RequestError() from None


def list_value(value,*,project,dataset,limit=10):
    exact(value,('items','next_cursor'));_need(type(limit) is int and 1<=limit<=10)
    _need(type(value['items']) is list and len(value['items'])<=limit)
    seen=set();previous=None
    for item in value['items']:
        task_value(item,project=project,dataset=dataset);_need(item['job_id'] not in seen);seen.add(item['job_id'])
        key=(stamp(item['created_at']),item['job_id']);_need(previous is None or key<previous);previous=key
    if value['next_cursor'] is not None:
        _need(bool(value['items']));cursor=decode_cursor(value['next_cursor']);last=value['items'][-1]
        _need(cursor=={'v':1,'createdAt':last['created_at'],'job_id':last['job_id']})
    _need(len(canonical(value))<=MAX_RESPONSE);return value

MAX_QUERY_BYTES=512
MAX_RAW_TARGET_BYTES=1024
MAX_CURSOR_CHARS=192
