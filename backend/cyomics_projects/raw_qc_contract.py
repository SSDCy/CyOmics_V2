"""Raw QC v0.1 source contract. No I/O, worker launch, or default runtime pins."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from .settings import Fault
from .validation import canonical_uuid
from .import_persistence import SourceRef, decode_version
from .sample_persistence import canonical

CONTRACT = 'raw_qc_job_v0.1'
ENABLED = False
MAX_REQUEST = 1024
MAX_RESPONSE = 32768
FAMILIES = {'intensity': 'Intensity', 'lfq': 'LFQ intensity'}
OUTPUTS = frozenset(('raw-matrix.tsv', 'numeric-matrix.tsv', 'blank-mask.tsv',
                     'zero-mask.tsv', 'sample-qc.tsv', 'summary.tsv', 'R-session-info.txt'))
SCRIPT_SHA = '2072a20e9dca53ab9ff03ef5a0a5d6aa9556dcee8004b5f28ce2fa4345624d64'
LIMITS = {'wall_seconds':60, 'cpu_seconds':45, 'address_space_bytes':2147483648,
          'per_file_bytes':67108864, 'output_bytes':268435456,
          'stdout_bytes':65536, 'stderr_bytes':65536,
          'monitor_seconds':0.05, 'cleanup_seconds':2,
          'open_files':256, 'processes':1, 'core_bytes':0}
ERROR_CODES = frozenset(('input_integrity_failed','execution_unavailable','runner_failed',
                        'resource_limit','output_invalid','worker_interrupted','publish_integrity_failed'))
SOURCE_KEYS = frozenset(('project_id','dataset_version_id','file_id','source_bytes','source_sha256',
                        'parser_contract','config_sha256','report_sha256','row_count',
                        'column_count','selected_columns_sha256'))
EXEC_KEYS = frozenset(('script','runner','output_validator','executable','r_version',
                      'entry_kind','limits_profile','limits_sha256'))
PUBLIC_KEYS = frozenset(('schemaVersion','job_id','request_id','request_sha256','binding_sha256',
                        'source','quant_family','params','execution','status','revision','created_at',
                        'started_at','finished_at','observation','error','result'))
JOB_COLUMNS = ('job_id','project_id','dataset_version_id','file_id','owner_id','request_id',
               'request_sha256','binding_sha256','source_json','quant_family','params_json',
               'execution_json','status','revision','created_at','started_at','finished_at',
               'attempt_id','observation','error_code','result_json','result_sha256')
COLUMNS = ','.join(JOB_COLUMNS)


def fail(status=503, code='raw_qc_persisted_invalid'):
    raise Fault(status, code)


def exact(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError()


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def limits_sha256():
    """Native005 identity envelope; fractional monitor_seconds is intentional.
    Separate from document canonical(), which permits only integral numbers.
    """
    value = {'limits_profile':'raw_qc_limits_v2', 'limits':LIMITS}
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return digest(encoded)


def integer(value, low=0, high=9007199254740991):
    if (type(value) not in (int,float) or not low <= value <= high
            or not math.isfinite(value) or value != int(value)):
        raise ValueError()


def hash_value(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError()


def identifier(value):
    if not canonical_uuid(value):
        raise ValueError()


def strict_document(raw, cap):
    if type(raw) is not bytes or len(raw) > cap or raw.startswith(b'\xef\xbb\xbf'):
        raise ValueError()
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    def walk(v, depth=0):
        if depth > 12:
            raise ValueError()
        if type(v) is str:
            v.encode('utf-8')  # no lone surrogates
        elif type(v) is dict:
            for k, item in v.items():
                walk(k, depth+1); walk(item, depth+1)
        elif type(v) is list:
            for item in v:
                walk(item, depth+1)
    walk(value)
    return value


def request_input(raw):
    try:
        body = strict_document(raw, MAX_REQUEST)
        exact(body, ('schemaVersion','request_id','quant_family','params'))
        if type(body['schemaVersion']) is not int or body['schemaVersion'] != 1:
            raise ValueError()
        identifier(body['request_id'])
        if type(body['quant_family']) is not str or body['quant_family'] not in FAMILIES:
            raise ValueError()
        exact(body['params'], ())
        return dict(body, quant_family=FAMILIES[body['quant_family']])
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        fail(400, 'invalid_raw_qc_request')


def request_sha(project, dataset, family):
    return digest(canonical({'schemaVersion':1, 'project_id':project,
                             'dataset_version_id':dataset, 'quant_family':family, 'params':{}}))


def source_value(value):
    exact(value, SOURCE_KEYS)
    for key in ('project_id','dataset_version_id','file_id'):
        identifier(value[key])
    for key in ('source_sha256','config_sha256','report_sha256','selected_columns_sha256'):
        hash_value(value[key])
    integer(value['source_bytes'], 1, 26214400)
    integer(value['row_count'], 1, 10000)
    integer(value['column_count'], 2, 512)
    if value['parser_contract'] != 'protein_groups_txt_v1':
        raise ValueError()
    return value


def bound_source(row, raw_metadata, project, dataset, family):
    """Pure integrity decoder only. Repository Owner+RLS checks must precede this."""
    try:
        if family not in FAMILIES.values():
            raise ValueError()
        vid, pid, fid, contract, config_sha, config, size, sha, *_ = row
        file_id, raw_project, fmt, raw_bytes, raw_sha, raw_status = raw_metadata
        if (str(pid) != project or str(vid) != dataset or str(fid) != str(file_id)
                or str(raw_project) != project or fmt != 'proteinGroups_txt'
                or raw_status != 'stored' or raw_bytes != size or raw_sha != sha):
            raise ValueError()
        source = SourceRef(project, str(fid), 'raw_bytea_v1:'+str(fid)+':'+sha, size, sha)
        decoded = decode_version(row, source, config, dataset)
        report = decoded['importReport']
        selected = [c for c in report['sample_columns'] if c['quant_family'] == family]
        if not selected:
            fail(422, 'raw_qc_family_unavailable')
        value = {'project_id':project, 'dataset_version_id':dataset, 'file_id':str(fid),
                 'source_bytes':size, 'source_sha256':sha, 'parser_contract':contract,
                 'config_sha256':config_sha, 'report_sha256':row[11],
                 'row_count':report['report']['row_count'], 'column_count':report['report']['column_count'],
                 'selected_columns_sha256':digest(canonical(selected))}
        return source_value(value)
    except Fault as error:
        if error.code == 'raw_qc_family_unavailable':
            raise
        fail()
    except (ValueError, TypeError, KeyError, IndexError):
        fail()


def execution_value(value):
    exact(value, EXEC_KEYS)
    for key in ('script','runner','output_validator','executable'):
        exact(value[key], ('bytes','sha256'))
        integer(value[key]['bytes'], 1, 268435456)
        hash_value(value[key]['sha256'])
    if (value['script'] != {'bytes':4949,'sha256':SCRIPT_SHA}
            or value['r_version'] != 'R version 4.6.1' or value['entry_kind'] != 'native_R'
            or value['limits_profile'] != 'raw_qc_limits_v2'
            or value['limits_sha256'] != limits_sha256()):
        raise ValueError()
    return value


@dataclass(frozen=True)
class ExecutionRegistration:
    """Trusted server configuration snapshot, not Owner proof or runtime approval.
    No production loader exists in this slice. Native runner pins remain Root/QA pending.
    """
    canonical_json: str

    def snapshot(self):
        value = strict_document(self.canonical_json.encode('utf-8'), 4096)
        execution_value(value)
        if canonical(value) != self.canonical_json:
            raise ValueError()
        return value


def binding_sha(request_hash, source, execution, family):
    return digest(canonical({'contract':CONTRACT, 'request_sha256':request_hash,
                             'source':source, 'execution':execution, 'quant_family':family, 'params':{}}))


def stamp(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError()
    return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def parse_stamp(value):
    if type(value) is not str or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z', value):
        raise ValueError()
    dt = datetime.fromisoformat(value.replace('Z','+00:00'))
    if stamp(dt) != value:
        raise ValueError()
    return dt


def result_value(value, job):
    exact(value, ('schemaVersion','job_id','binding_sha256','execution','artifacts','manifest_sha256','verified_at'))
    integer(value['schemaVersion'],1,1)
    if (value['schemaVersion'] != 1
            or any(value[k] != job[k] for k in ('job_id','binding_sha256','execution'))):
        raise ValueError()
    artifacts = value['artifacts']
    if type(artifacts) is not list or len(artifacts) != 7:
        raise ValueError()
    for a in artifacts:
        exact(a, ('name','bytes','sha256','media_type'))
        if type(a['name']) is not str or a['name'] not in OUTPUTS:
            raise ValueError()
        integer(a['bytes'], 1, LIMITS['per_file_bytes']); hash_value(a['sha256'])
        media = 'text/plain; charset=utf-8' if a['name'] == 'R-session-info.txt' else 'text/tab-separated-values; charset=utf-8'
        if a['media_type'] != media:
            raise ValueError()
    if (set(a['name'] for a in artifacts) != OUTPUTS
            or artifacts != sorted(artifacts, key=lambda a:a['name'])
            or sum(a['bytes'] for a in artifacts) > LIMITS['output_bytes']
            or value['manifest_sha256'] != digest(canonical(artifacts))):
        raise ValueError()
    verified = parse_stamp(value['verified_at'])
    if verified < parse_stamp(job['started_at']):
        raise ValueError()
    return value


def decode_record(row, expected_source=None):
    try:
        if len(row) != len(JOB_COLUMNS):
            raise ValueError()
        r = dict(zip(JOB_COLUMNS,row))
        for k in ('job_id','project_id','dataset_version_id','file_id','owner_id','request_id'):
            r[k] = str(r[k]); identifier(r[k])
        source = strict_document(r['source_json'].encode('utf-8'), 4096)
        execution = strict_document(r['execution_json'].encode('utf-8'), 4096)
        source_value(source); execution_value(execution)
        if (canonical(source) != r['source_json'] or canonical(execution) != r['execution_json']
                or r['params_json'] != '{}' or r['quant_family'] not in FAMILIES.values()
                or any(source[k] != r[k] for k in ('project_id','dataset_version_id','file_id'))
                or expected_source is not None and source != expected_source):
            raise ValueError()
        expected_request = request_sha(r['project_id'],r['dataset_version_id'],r['quant_family'])
        if (r['request_sha256'] != expected_request
                or r['binding_sha256'] != binding_sha(expected_request,source,execution,r['quant_family'])):
            raise ValueError()
        integer(r['revision'],1)
        status = r['status']
        if status not in ('queued','running','succeeded','failed'):
            raise ValueError()
        started = None if r['started_at'] is None else stamp(r['started_at'])
        finished = None if r['finished_at'] is None else stamp(r['finished_at'])
        created = stamp(r['created_at'])
        if started and parse_stamp(started)<parse_stamp(created) or finished and parse_stamp(finished)<parse_stamp(started or created):
            raise ValueError()
        if r['attempt_id'] is not None:
            identifier(str(r['attempt_id']))
        if r['observation'] not in ('confirmed','reconciliation_pending'):
            raise ValueError()
        if status == 'queued' and (started is not None or finished is not None or r['attempt_id'] is not None):
            raise ValueError()
        if status == 'running' and (started is None or finished is not None or r['attempt_id'] is None):
            raise ValueError()
        if status in ('succeeded','failed') and finished is None:
            raise ValueError()
        if status == 'succeeded' and (started is None or r['attempt_id'] is None):
            raise ValueError()
        if status == 'failed' and ((started is None) != (r['attempt_id'] is None)):
            raise ValueError()
        if r['observation'] != 'confirmed' and status != 'running':
            raise ValueError()
        if (status == 'failed') != (r['error_code'] in ERROR_CODES):
            raise ValueError()
        if status != 'failed' and r['error_code'] is not None:
            raise ValueError()
        if (status == 'succeeded') != (r['result_json'] is not None):
            raise ValueError()
        job = {'schemaVersion':1,'job_id':r['job_id'],'request_id':r['request_id'],
               'request_sha256':r['request_sha256'],'binding_sha256':r['binding_sha256'],
               'source':source,'quant_family':r['quant_family'],'params':{},'execution':execution,
               'status':status,'revision':r['revision'],'created_at':created,'started_at':started,
               'finished_at':finished,'observation':r['observation'],
               'error':None if r['error_code'] is None else {'code':r['error_code']},'result':None}
        if r['result_json'] is not None:
            result = strict_document(r['result_json'].encode('utf-8'), MAX_RESPONSE)
            result_value(result,job)
            if (canonical(result) != r['result_json'] or r['result_sha256'] != digest(r['result_json'])
                    or parse_stamp(result['verified_at']) > parse_stamp(finished)):
                raise ValueError()
            job['result'] = result
        elif r['result_sha256'] is not None:
            raise ValueError()
        if len(canonical(job).encode('utf-8')) > MAX_RESPONSE:
            raise ValueError()
        return job
    except (ValueError, TypeError, KeyError, IndexError, UnicodeError, RecursionError):
        fail()

