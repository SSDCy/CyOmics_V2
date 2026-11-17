"""Owner-bound sample design documents. No I/O at import."""
from datetime import datetime, timezone
import hashlib
import json
import math
from .settings import Fault
from .validation import canonical_uuid, strict_json
from .sample_design import DesignError, bind_trusted_source, validate_design
from .import_persistence import SourceRef, decode_version

MODEL_CONTRACT = 'sample_design_review_v0.2'
VALIDATOR_SHA256 = 'c19d4f55de08154b4cafca216835b86a210b1919c16e85eaf99226750eb007ed'
MAX_REQUEST = 131072
MAX_RESPONSE = 196608
FAMILIES = {'intensity': 'Intensity', 'lfq': 'LFQ intensity'}


def canonical(value):
    # Only JSON safe integers are allowed by this contract, including 1.0.
    def normalize(v):
        if type(v) is float:
            if not math.isfinite(v) or not v.is_integer(): raise ValueError()
            v = int(v)
        if type(v) is int and abs(v) > 9007199254740991: raise ValueError()
        if type(v) is str:
            v.encode('utf-8')
            return v
        if type(v) is list: return [normalize(x) for x in v]
        if type(v) is dict:
            if any(type(k) is not str for k in v): raise ValueError()
            return {normalize(k): normalize(x) for k, x in v.items()}
        if v is None or type(v) in (bool, int): return v
        raise ValueError()
    return json.dumps(normalize(value), ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def failure(budget, error=None):
    if budget.create.commit_started or budget.create.committed:
        return Fault(504, 'sample_design_result_unknown')
    if isinstance(error, TimeoutError): return Fault(504, 'sample_design_timeout')
    return Fault(503, 'sample_design_unavailable')


def trusted_source(row, project_id, dataset_id):
    try:
        source = SourceRef(project_id, str(row[2]), 'raw_bytea_v1:' + str(row[2]) + ':' + row[7], row[6], row[7])
        decoded = decode_version(row, source, row[5], dataset_id)
        return bind_trusted_source(dataset_id, decoded['importReport']['sample_columns'])
    except Exception:
        raise Fault(503, 'sample_design_persisted_invalid') from None


def request_input(payload, source, family):
    try:
        if type(payload) is not dict or set(payload) != {'schemaVersion','request_id','expected_head_version_id','design'}: raise ValueError()
        if type(payload['schemaVersion']) not in (int,float) or isinstance(payload['schemaVersion'],bool) or payload['schemaVersion'] != 1: raise ValueError()
        if not canonical_uuid(payload['request_id']): raise ValueError()
        head = payload['expected_head_version_id']
        if head is not None and not canonical_uuid(head): raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise Fault(400, 'invalid_sample_design_request') from None
    try:
        design = validate_design(payload['design'], source).design.to_payload()
        if design['quant_family'] != family: raise ValueError()
        result = dict(payload, schemaVersion=1, design=design)
        if len(canonical(result).encode('utf-8')) > MAX_REQUEST: raise ValueError()
        return result
    except (ValueError, TypeError, KeyError, RecursionError, DesignError, UnicodeError):
        raise Fault(422, 'invalid_sample_design') from None


def decode_record(row, source, family):
    try:
        vid, parent, revision, request, request_hash, design_json, design_hash, created, model_contract, validator_sha256 = row
        if model_contract != MODEL_CONTRACT or validator_sha256 != VALIDATOR_SHA256: raise ValueError()
        if (not canonical_uuid(str(vid)) or parent is not None and not canonical_uuid(str(parent))
                or type(revision) is not int or not 1 <= revision <= 100
                or (parent is None) != (revision == 1) or not canonical_uuid(str(request))
                or type(request_hash) is not str or len(request_hash) != 64
                or any(c not in '0123456789abcdef' for c in request_hash)
                or type(design_json) is not str or len(design_json.encode('utf-8')) > MAX_REQUEST
                or not isinstance(created, datetime) or created.utcoffset() is None): raise ValueError()
        design = validate_design(strict_json(design_json.encode('utf-8')), source).design.to_payload()
        if design['quant_family'] != family or canonical(design) != design_json or sha(design_json) != design_hash: raise ValueError()
        return {'sample_design_version_id':str(vid), 'parent_version_id':None if parent is None else str(parent),
                'revision':revision, 'request_id':str(request), 'design_sha256':design_hash,
                'created_at':created.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), 'design':design}
    except Exception:
        raise Fault(503, 'sample_design_persisted_invalid') from None


def envelope(project_id, dataset_id, family, record):
    return {'schemaVersion':1, 'project_id':project_id, 'dataset_version_id':dataset_id,
            'quant_family':family, 'record':record}


def response_value(value, project, dataset, family):
    """Output shape guard supplements the repository's stronger trusted-source check."""
    try:
        if type(value) is not dict or set(value) != {'schemaVersion','project_id','dataset_version_id','quant_family','record'}: raise ValueError()
        if type(value['schemaVersion']) is not int or value['schemaVersion'] != 1 or value['project_id'] != project or value['dataset_version_id'] != dataset or value['quant_family'] != family: raise ValueError()
        record=value['record']
        if record is not None:
            if type(record) is not dict or set(record) != {'sample_design_version_id','parent_version_id','revision','request_id','design_sha256','created_at','design'}: raise ValueError()
            design=record['design']
            source=bind_trusted_source(dataset,[{'ordinal':r['column_ordinal'],'name_raw':r['name_raw'],'sample_label_raw':r['sample_label_raw'],'quant_family':family} for r in design['rows']])
            created=datetime.fromisoformat(record['created_at'].replace('Z','+00:00'))
            decoded=decode_record((record['sample_design_version_id'],record['parent_version_id'],record['revision'],record['request_id'],'0'*64,canonical(design),record['design_sha256'],created,MODEL_CONTRACT,VALIDATOR_SHA256),source,family)
            if decoded != record: raise ValueError()
        return value
    except Exception:
        raise Fault(503,'sample_design_persisted_invalid') from None
