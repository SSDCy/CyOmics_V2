"""Pure source-backed FASTA candidates. No Owner authority or persistence/IO.

Explicit approved config and trusted control are required for parsing. A returned
candidate is data, never permission to commit; a future publisher must check its
own live budget/Owner/transaction immediately before any write.
"""
from dataclasses import dataclass, fields
from datetime import datetime
import hashlib
import json
import math
import re
import time
from types import MappingProxyType
from uuid import UUID, uuid5
from .fasta_index import FastaIndex, FastaIndexError, Limits, Record, parse_fasta
from .sequence.vendor_domain_r1 import normalize_sequence

INDEX_CONTRACT = 'fasta_ascii_identity_index_v0.1'
IDENTITY_CONTRACT = 'sequence_identity_review_v0.1'
REPRESENTATION = 'fasta_source_backed_index'
NAMESPACE = UUID('ae925a0c-6877-58a0-a4f8-1ec6a8301191')
CONFIG_SHA256 = '380475bafbccc807ba0745ca595d3f6fe4953412a6d8403f5f14dab2fd2ce0ac'
CONFIG_JSON = '{"index_contract":"fasta_ascii_identity_index_v0.1","index_materialized":false,"limits":{"max_bytes":26214400,"max_chunks":500000,"max_id_characters":2000000,"max_id_length":256,"max_line_length":1000000,"max_lines":500000,"max_records":50000,"max_sequence_length":1000000},"parser_release":{"source_commit":"51ae7bd2e0a156e902839d4e60b98f73f964263d","source_pins":{"fasta_index.py":{"bytes":5814,"sha256":"e9b8c8b67f2ab1ca66f520ccf3e79e2e9081ac7f331ae8a06ab132cc4bbe5f3c"},"sequence/vendor_domain_r1.py":{"bytes":18361,"sha256":"09608f43623e2939b38ec2427f842a70a67cb39f810678b15cda2839044dbd87"}}},"publisher_contract":"fasta_source_index_publication_v1","record_serialization_contract":"fasta_full_ordered_index_jsonl_v1","representation":"fasta_source_backed_index","schemaVersion":1,"sequence_identity_contract":"sequence_identity_review_v0.1"}'  # Offline preparation substitutes the exact approved 848 bytes.
MAX_SOURCE = 26214400
SAFE_INTEGER = 9007199254740991
SUMMARY_KEYS = ('record_count','distinct_id_count','duplicate_record_excess',
    'valid_canonical_record_count','invalid_canonical_record_count',
    'total_raw_sequence_characters','line_count','blank_lines')
SOURCE_KEYS = frozenset(('source_id','project_id','request_id','filename','format',
    'bytes','sha256','status','createdAt'))
INDEX_KEYS = frozenset(('index_version_id','project_id','source_id','source_bytes',
    'source_sha256','index_contract','sequence_identity_contract','config_sha256',
    'index_manifest_sha256','representation','index_materialized','source_complete',
    'status','summary','createdAt'))
ISSUES = frozenset(('INVALID_LIMITS','INVALID_INPUT','CHUNK_LIMIT','INVALID_CHUNK',
    'BYTE_LIMIT','INVALID_UTF8','UTF8_BOM','NON_ASCII_FASTA','LINE_LIMIT','LONE_CR',
    'LINE_LENGTH_LIMIT','RECORD_LIMIT','EMPTY_ID','ID_LENGTH_LIMIT',
    'ID_CHARACTER_LIMIT','SEQUENCE_BEFORE_HEADER','SEQUENCE_LENGTH_LIMIT','NO_RECORDS'))
ERRORS = frozenset(('invalid_fasta_request','fasta_index_failed',
    'fasta_intake_unavailable','fasta_intake_timeout','fasta_source_integrity_failed',
    'fasta_index_persisted_invalid','fasta_parser_release_mismatch'))


@dataclass(frozen=True, slots=True, repr=False)
class ParseIssue:
    code: str
    line: int
    category: str


class IntakeError(ValueError):
    """Fixed content-free error; never accepts an arbitrary message."""
    def __init__(self, code='fasta_intake_unavailable', issue=None):
        self.code = code if type(code) is str and code in ERRORS else 'fasta_intake_unavailable'
        self.issue = issue if (type(issue) is ParseIssue and type(issue.code) is str and issue.code in ISSUES
            and type(issue.line) is int and 0 <= issue.line <= 500000
            and type(issue.category) is str and issue.category in ('unsupported','invalid_or_limit')) else None
        super().__init__(self.code)


def _fail(code='fasta_index_persisted_invalid'):
    raise IntakeError(code)


def _require(ok, code='fasta_index_persisted_invalid'):
    if not ok:
        _fail(code)


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _sha(value, code='fasta_index_persisted_invalid'):
    _require(type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None,code)
    return value


def _uuid(value, code='invalid_fasta_request'):
    _require(type(value) is str and re.fullmatch(
        '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',value) is not None,code)
    return value


def _integer(value, low, high, code='fasta_index_persisted_invalid'):
    _require(type(value) in (int,float),code)
    if type(value) is float:
        _require(math.isfinite(value) and value.is_integer(),code)
    _require(-SAFE_INTEGER <= value <= SAFE_INTEGER and low <= value <= high,code)
    return int(value)


def _exact(value, keys, code='fasta_index_persisted_invalid'):
    _require(type(value) is dict and all(type(key) is str for key in value)
        and set(value) == set(keys),code)


def _utf8(value, code='fasta_index_persisted_invalid'):
    result = None
    try:
        result = value.encode('utf8','strict')
    except BaseException:
        pass
    if result is None:
        _fail(code)
    return result


def _json(raw, cap, code='fasta_index_persisted_invalid'):
    _require(type(raw) is bytes and len(raw) <= cap,code)
    def pairs(items):
        result = {}
        for key,value in items:
            _require(key not in result,code)
            result[key] = value
        return result
    result = None
    bad = False
    try:
        result = json.loads(raw.decode('utf8'),object_pairs_hook=pairs,
            parse_constant=lambda _: _fail(code))
    except BaseException:
        bad = True
    if bad:
        _fail(code)  # outside except: no private JSON exception context retained
    return result


def _canonical(value, cap, code='fasta_index_persisted_invalid'):
    result = None
    bad = False
    try:
        result = json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,
            allow_nan=False)
        bad = len(result.encode('utf8')) > cap
    except BaseException:
        bad = True
    if bad:
        _fail(code)
    return result


def _timestamp(value, code='fasta_index_persisted_invalid'):
    _require(type(value) is str and re.fullmatch(
        '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z',value) is not None,code)
    bad = False
    try:
        datetime(int(value[:4]),int(value[5:7]),int(value[8:10]),int(value[11:13]),
            int(value[14:16]),int(value[17:19]),int(value[20:26]))
    except BaseException:
        bad = True
    if bad:
        _fail(code)
    return value


def configuration_json():
    """One fixed profile; this is metadata, not a loaded-source attestation."""
    return CONFIG_JSON


def configuration():
    return _json(CONFIG_JSON.encode('ascii'),8192)


def validate_config(value):
    _require(type(value) is str and value == CONFIG_JSON,'fasta_parser_release_mismatch')
    _require(_digest(value.encode('ascii')) == CONFIG_SHA256,'fasta_parser_release_mismatch')
    return value


def validate_source_dto(value):
    code = 'invalid_fasta_request'
    _exact(value,SOURCE_KEYS,code)
    result = {key:value[key] for key in SOURCE_KEYS}
    for key in ('project_id','source_id','request_id'):
        result[key] = _uuid(value[key],code)
    name = value['filename']
    _require(type(name) is str and not any(ord(ch)<32 or 127<=ord(ch)<=159
        or ch in '/\\' or 0xD800<=ord(ch)<=0xDFFF for ch in name),code)
    _require(1 <= len(name) <= 255 and len(name.encode('utf8')) <= 255
        and name.lower().endswith(('.fa','.faa','.fasta')),code)
    _require(type(value['format']) is str and value['format']=='protein_fasta',code)
    _require(type(value['status']) is str and value['status']=='stored',code)
    result['bytes'] = _integer(value['bytes'],1,MAX_SOURCE,code)
    result['sha256'] = _sha(value['sha256'],code)
    result['createdAt'] = _timestamp(value['createdAt'],code)
    return result


@dataclass(frozen=True, slots=True, repr=False)
class StoredSource:
    source_id: str
    project_id: str
    request_id: str
    filename: str
    format: str
    bytes: int
    sha256: str
    status: str
    created_at: str
    content: bytes


def source_from_json(raw, content):
    value = validate_source_dto(_json(raw,2048,'invalid_fasta_request'))
    _require(type(content) is bytes,'fasta_source_integrity_failed')
    _require(len(content)==value['bytes'] and _digest(content)==value['sha256'],
        'fasta_source_integrity_failed')
    return StoredSource(**{('created_at' if k=='createdAt' else k):v for k,v in value.items()},content=content)


def _source(source, project, identifier):
    _uuid(project)
    _uuid(identifier)
    _require(type(source) is StoredSource,'fasta_source_integrity_failed')
    value = None
    try:
        value = validate_source_dto({
            ('createdAt' if field.name=='created_at' else field.name):getattr(source,field.name)
            for field in fields(StoredSource) if field.name!='content'})
    except IntakeError:
        pass
    if value is None:
        _fail('fasta_source_integrity_failed')
    _require(source.project_id==project and source.source_id==identifier,'fasta_source_integrity_failed')
    _require(type(source.content) is bytes and len(source.content)==value['bytes']
        and _digest(source.content)==source.sha256,'fasta_source_integrity_failed')
    return source


def version_key(project_id,source_id,source_sha256,config_json):
    _uuid(project_id)
    _uuid(source_id)
    _sha(source_sha256)
    validate_config(config_json)
    return str(uuid5(NAMESPACE,':'.join((project_id,source_id,source_sha256,
        INDEX_CONTRACT,CONFIG_SHA256))))


@dataclass(frozen=True, slots=True, repr=False)
class ParseControl:
    """Trusted server hooks; construction never invokes them. Not JSON config."""
    checkpoint: object = None
    cpu_clock: object = time.thread_time
    max_cpu_seconds: float = 8.0


class _Budget:
    def __init__(self, control):
        _require(type(control) is ParseControl and callable(control.checkpoint)
            and callable(control.cpu_clock),'fasta_intake_unavailable')
        cap = control.max_cpu_seconds
        _require(type(cap) in (int,float) and 0<cap<=8 and math.isfinite(cap),
            'fasta_intake_unavailable')
        self.control = control
        self.started = self.previous = self._clock()
    def _clock(self):
        value = None
        bad = False
        try:
            value = self.control.cpu_clock()
        except BaseException:
            bad = True
        if bad or type(value) not in (int,float) or not 0<=value<=SAFE_INTEGER or not math.isfinite(value):
            _fail('fasta_intake_unavailable')
        return value
    def check(self):
        bad = False
        result = None
        try:
            result = self.control.checkpoint()
        except BaseException:
            bad = True
        if bad:
            _fail('fasta_intake_timeout')
        _require(result is None,'fasta_intake_unavailable')
        now = self._clock()
        _require(now>=self.previous,'fasta_intake_unavailable')
        self.previous = now
        _require(now-self.started<=self.control.max_cpu_seconds,'fasta_intake_timeout')


def _chunks(content, budget):
    for offset in range(0,len(content),65536):
        budget.check()
        yield content[offset:offset+65536]
    budget.check()


def _validate_index(index, budget):
    """Validate full internal shape, identities and the complete duplicate map."""
    _require(type(index) is FastaIndex)
    _require(type(index.index_contract) is str and index.index_contract==INDEX_CONTRACT
        and type(index.sequence_identity_contract) is str and index.sequence_identity_contract==IDENTITY_CONTRACT)
    _require(type(index.records) is tuple and 1<=len(index.records)<=50000)
    _require(type(index.id_ordinals) is MappingProxyType and 1<=len(index.id_ordinals)<=len(index.records))
    _require(type(index.source_bytes) is int and 1<=index.source_bytes<=MAX_SOURCE)
    _sha(index.source_sha256)
    _require(type(index.line_count) is int and 1<=index.line_count<=500000)
    _require(type(index.blank_lines) is int and 0<=index.blank_lines<=index.line_count)
    mapping = {}
    previous = 0
    total_ids = 0
    total_characters = total_lines = 0
    for ordinal,record in enumerate(index.records,1):
        budget.check()
        _require(type(record) is Record and type(record.ordinal) is int and record.ordinal==ordinal)
        _require(type(record.header_line) is int and previous<record.header_line<=index.line_count)
        previous = record.header_line
        _require(type(record.header_raw) is str and record.header_raw.isascii()
            and len(record.header_raw)<=1000000 and '\r' not in record.header_raw
            and '\n' not in record.header_raw)
        tokens = record.header_raw.split()
        _require(bool(tokens) and type(record.id) is str and record.id==tokens[0]
            and 1<=len(record.id)<=256)
        total_ids += len(record.id)
        _require(total_ids<=2000000)
        _require(type(record.sequence_lines) is tuple and len(record.sequence_lines)<=500000
            and all(type(line) is str
            and line.isascii() and len(line)<=1000000 and '\r' not in line and '\n' not in line
            for line in record.sequence_lines))
        raw_length = sum(len(line) for line in record.sequence_lines)
        _require(raw_length<=1000000)
        total_characters += 1+len(record.header_raw)+raw_length
        total_lines += 1+len(record.sequence_lines)
        _require(total_characters<=index.source_bytes and total_lines<=index.line_count)
        _require(type(record.sequence_raw) is str and record.sequence_raw==''.join(record.sequence_lines)
            and len(record.sequence_raw)<=1000000)
        raw = record.sequence_raw
        _require(_sha(record.raw_sequence_sha256)==_digest(raw.encode('ascii')))
        # Fixed vendor STANDARD20 + BJOUXZ is exactly all 26 ASCII letters.
        # The checks above already prove exact str, ASCII lines and raw equality.
        canonical = raw.upper() if raw and raw.isascii() and raw.isalpha() else None
        issue = (None if canonical is not None else 'empty_sequence' if not raw else
            'sequence_whitespace' if any(ch.isspace() for ch in raw) else
            'stop_or_gap' if '*' in raw or '-' in raw else 'illegal_residue')
        _require((record.canonical_sequence is None or type(record.canonical_sequence) is str)
            and (record.canonical_issue is None or type(record.canonical_issue) is str)
            and record.canonical_sequence==canonical and record.canonical_issue==issue)
        _require(type(record.normalization_status) is str and record.normalization_status==('valid_canonical_residues' if canonical is not None
            else 'invalid_no_canonical_sequence_identity'))
        if canonical is None:
            _require(record.sequence_sha256 is None)
        else:
            _require(_sha(record.sequence_sha256)==_digest(canonical.encode('ascii')))
        mapping.setdefault(record.id,[]).append(ordinal)
    _require(all(type(key) is str for key in index.id_ordinals) and set(index.id_ordinals)==set(mapping))
    for identifier,ordinals in index.id_ordinals.items():
        _require(type(identifier) is str and type(ordinals) is tuple
            and len(ordinals)==len(mapping[identifier]) and all(type(n) is int for n in ordinals)
            and ordinals==tuple(mapping[identifier]))
    budget.check()


def _summary(index):
    valid = sum(record.canonical_sequence is not None for record in index.records)
    return dict(zip(SUMMARY_KEYS,(len(index.records),len(index.id_ordinals),
        len(index.records)-len(index.id_ordinals),valid,len(index.records)-valid,
        sum(len(record.sequence_raw) for record in index.records),index.line_count,index.blank_lines)))


def validate_summary(value,source_bytes):
    _exact(value,SUMMARY_KEYS)
    source_bytes = _integer(source_bytes,1,MAX_SOURCE)
    bounds = ((1,50000),(1,50000),(0,49999),(0,50000),(0,50000),
        (0,source_bytes),(1,500000),(0,500000))
    result = {key:_integer(value[key],*bound) for key,bound in zip(SUMMARY_KEYS,bounds)}
    _require(result['distinct_id_count']+result['duplicate_record_excess']==result['record_count'])
    _require(result['valid_canonical_record_count']+result['invalid_canonical_record_count']==result['record_count'])
    _require(result['record_count']+result['blank_lines']<=result['line_count'])
    _require(result['valid_canonical_record_count']<=result['total_raw_sequence_characters'])
    return result


def fingerprint_index(index, *, control=None):
    """Private content stays in memory; callers receive only an all-fields SHA."""
    budget = _Budget(control)
    budget.check()
    _validate_index(index,budget)
    return _fingerprint(index,budget)


def _fingerprint(index,budget):
    budget.check()
    digest = hashlib.sha256()
    encoder = json.JSONEncoder(sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)
    def line(value):
        # One already-validated, bounded record/id unit; never a whole manifest.
        budget.check()
        encoded = encoder.encode(value)
        budget.check()
        for offset in range(0,len(encoded),65536):
            budget.check()
            digest.update(encoded[offset:offset+65536].encode('ascii'))
        budget.check()
        digest.update(b'\n')
    line({'kind':'index','value':{'schemaVersion':1,'index_contract':INDEX_CONTRACT,
        'sequence_identity_contract':IDENTITY_CONTRACT,'source_bytes':index.source_bytes,
        'source_sha256':index.source_sha256,'record_count':len(index.records),
        'blank_lines':index.blank_lines,'line_count':index.line_count}})
    for record in index.records:
        line({'kind':'record','value':{field.name:getattr(record,field.name) for field in fields(Record)}})
    for identifier in sorted(index.id_ordinals):
        line({'kind':'id_ordinals','id':identifier,'ordinals':index.id_ordinals[identifier]})
    budget.check()
    return digest.hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class IndexCandidate:
    index_version_id: str
    project_id: str
    source_id: str
    source_bytes: int
    source_sha256: str
    index_contract: str
    config_json: str
    config_sha256: str
    index_manifest_sha256: str
    summary_json: str
    summary_sha256: str
    representation: str = REPRESENTATION
    index_materialized: bool = False
    source_complete: bool = True
    status: str = 'indexed'


def candidate_snapshot(candidate):
    _require(type(candidate) is IndexCandidate)
    return {field.name:getattr(candidate,field.name) for field in fields(IndexCandidate)}


def decode_candidate(value):
    _exact(value,{field.name for field in fields(IndexCandidate)})
    result = dict(value)
    _uuid(value['project_id'])
    _uuid(value['source_id'])
    _uuid(value['index_version_id'])
    validate_config(value['config_json'])
    _require(_sha(value['config_sha256'])==CONFIG_SHA256)
    _sha(value['source_sha256'])
    _sha(value['index_manifest_sha256'])
    result['source_bytes'] = _integer(value['source_bytes'],1,MAX_SOURCE)
    _require(type(value['index_contract']) is str and value['index_contract']==INDEX_CONTRACT
        and type(value['representation']) is str and value['representation']==REPRESENTATION
        and value['index_materialized'] is False and value['source_complete'] is True
        and type(value['status']) is str and value['status']=='indexed')
    _require(value['index_version_id']==version_key(value['project_id'],value['source_id'],
        value['source_sha256'],value['config_json']))
    _require(type(value['summary_json']) is str)
    summary_raw = _utf8(value['summary_json'])
    summary = validate_summary(_json(summary_raw,4096),result['source_bytes'])
    canonical = _canonical(summary,4096)
    _require(value['summary_json']==canonical and _sha(value['summary_sha256'])==_digest(summary_raw))
    return IndexCandidate(**result)


def candidate_from_json(raw):
    return decode_candidate(_json(raw,16384))


def _build(source,project,identifier,control,config):
    if config is None:
        _fail('fasta_intake_unavailable')
    validate_config(config)
    budget = _Budget(control)
    budget.check()
    source = _source(source,project,identifier)
    budget.check()
    index = None
    issue = None
    bad = None
    try:
        index = parse_fasta(_chunks(source.content,budget),limits=Limits())
    except FastaIndexError as error:
        issue = ParseIssue(error.code,error.line,error.category)
        bad = 'fasta_index_failed'
    except IntakeError as error:
        bad = error.code
    except BaseException:
        bad = 'fasta_intake_unavailable'
    if bad:
        raise IntakeError(bad,issue)
    budget.check()
    _require(type(index) is FastaIndex and type(index.source_bytes) is int
        and index.source_bytes==source.bytes and index.source_sha256==source.sha256,
        'fasta_source_integrity_failed')
    _validate_index(index,budget)
    summary = validate_summary(_summary(index),source.bytes)
    summary_json = _canonical(summary,4096)
    fingerprint = _fingerprint(index,budget)
    candidate = IndexCandidate(version_key(project,identifier,source.sha256,config),
        project,identifier,source.bytes,source.sha256,INDEX_CONTRACT,config,CONFIG_SHA256,
        fingerprint,summary_json,_digest(summary_json.encode('ascii')))
    candidate = decode_candidate(candidate_snapshot(candidate))
    budget.check()  # late complete/cancel/over-CPU cannot return a publishable candidate
    return candidate,index


def build_candidate(source=None, *, expected_project_id=None, expected_source_id=None,
                    control=None, config_json=None):
    return _build(source,expected_project_id,expected_source_id,control,config_json)[0]


def index_dto(candidate,created_at):
    candidate = decode_candidate(candidate_snapshot(candidate))
    return validate_index_dto({'index_version_id':candidate.index_version_id,
        'project_id':candidate.project_id,'source_id':candidate.source_id,
        'source_bytes':candidate.source_bytes,'source_sha256':candidate.source_sha256,
        'index_contract':INDEX_CONTRACT,'sequence_identity_contract':IDENTITY_CONTRACT,
        'config_sha256':candidate.config_sha256,'index_manifest_sha256':candidate.index_manifest_sha256,
        'representation':REPRESENTATION,'index_materialized':False,'source_complete':True,
        'status':'indexed','summary':_json(candidate.summary_json.encode('ascii'),4096),
        'createdAt':created_at})


def validate_index_dto(value):
    _exact(value,INDEX_KEYS)
    result = dict(value)
    _uuid(value['project_id'])
    _uuid(value['source_id'])
    _uuid(value['index_version_id'])
    _sha(value['source_sha256'])
    _sha(value['index_manifest_sha256'])
    _require(_sha(value['config_sha256'])==CONFIG_SHA256
        and type(value['index_contract']) is str and value['index_contract']==INDEX_CONTRACT
        and type(value['sequence_identity_contract']) is str and value['sequence_identity_contract']==IDENTITY_CONTRACT
        and type(value['representation']) is str and value['representation']==REPRESENTATION
        and value['index_materialized'] is False and value['source_complete'] is True
        and type(value['status']) is str and value['status']=='indexed')
    _require(value['index_version_id']==version_key(value['project_id'],value['source_id'],
        value['source_sha256'],CONFIG_JSON))
    result['source_bytes'] = _integer(value['source_bytes'],1,MAX_SOURCE)
    result['summary'] = validate_summary(value['summary'],result['source_bytes'])
    result['createdAt'] = _timestamp(value['createdAt'])
    return result


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedFastaIndex:
    """Integrity evidence only. This class carries no Owner/DB authorization."""
    candidate: IndexCandidate
    index: FastaIndex


def rebuild_and_verify(source=None,candidate=None, *, expected_project_id=None,
                       expected_source_id=None,expected_index_version_id=None,control=None):
    _require(type(candidate) is IndexCandidate)
    stored = decode_candidate(candidate_snapshot(candidate))
    _require(stored.index_version_id==_uuid(expected_index_version_id))
    _require(stored.project_id==expected_project_id and stored.source_id==expected_source_id,
        'fasta_source_integrity_failed')
    rebuilt,index = _build(source,expected_project_id,expected_source_id,control,stored.config_json)
    _require(candidate_snapshot(rebuilt)==candidate_snapshot(stored))
    # Metadata-only DTO acceptance is never used as an input shortcut here.
    return VerifiedFastaIndex(stored,index)
