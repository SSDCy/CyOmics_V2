"""Bounded raw-backed DatasetVersion/ImportReport documents; no import-time I/O."""
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
from uuid import UUID, uuid5

from .import_report import CONTRACT_VERSION, SourceRef, ImportReportDraft
from .protein_groups_parser import ParserLimits, parse_protein_groups
from .settings import Fault
from .validation import canonical_uuid

REPORT_MAX_BYTES = 1048576
CONFIG_MAX_BYTES = 4096
PUBLISHER_CONTRACT = 'raw_backed_dataset_import_v1'
LEGACY_PROFILE = 'txt_10mib_1mcells_v1'
CURRENT_PROFILE = 'txt_25mib_2mcells_v2'
# Standalone, local, two-core-file snapshot; Git evidence/bundle accompanies review.
CORE_SOURCE_COMMIT = '7834bef44bc05f1634cdd351283dc8e851107df6'
CORE_PINS = {
    'import_report.py': {'bytes': 3771, 'sha256': '6127539ae5ab09c4709022e3bf97c6cb62fc1acd8206384e24439750fdffebca'},
    'protein_groups_parser.py': {'bytes': 13809, 'sha256': 'a681a57dd0c943623484e86865a5014b7befc1ee5d89ee62cc5b9988438b59f0'},
}
KEY_NAMESPACE = UUID('3bc24944-84f2-5e43-80cb-9d536a8b9231')
LEGACY_CORE_PINS = {
    'import_report.py': {'bytes': 3771, 'sha256': '6127539ae5ab09c4709022e3bf97c6cb62fc1acd8206384e24439750fdffebca'},
    'protein_groups_parser.py': {'bytes': 13809, 'sha256': '520d7c00f4a501483402885a2a71844274fe4430e6cc410e40789380bc0edaad'},
}
LEGACY_LIMITS = {'max_bytes': 10485760, 'max_records': 10000, 'max_columns': 512,
                 'max_cells': 1000000, 'max_field_units': 1000000,
                 'max_member_occurrences': 100000, 'max_id_units': 2000000}


class ImportFault(Fault):
    """Only fixed codes, parser issue coordinates and an authorized stable lookup key."""
    def __init__(self, status, code, *, version_key=None, issues=()):
        super().__init__(status, code)
        self.version_key = version_key
        self.issues = tuple(issues)


@dataclass(frozen=True, slots=True, repr=False)
class StoredRaw:
    file_id: str
    project_id: str
    format: str
    bytes: int
    sha256: str
    status: str
    content: bytes


@dataclass(frozen=True, slots=True, repr=False)
class ImportCandidate:
    version_id: str
    source: SourceRef
    config_json: str
    config_sha256: str
    report_json: str
    report_sha256: str


def canonical_json(value, cap, checkpoint=lambda: None):
    parts = []
    size = 0
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    for part in encoder.iterencode(value):
        checkpoint()
        size += len(part.encode('utf-8'))
        if size > cap:
            raise ImportFault(413, 'import_report_too_large')
        parts.append(part)
    return ''.join(parts)


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def configuration(profile=CURRENT_PROFILE):
    """Two exact server profiles; legacy is read-only, never caller supplied."""
    if profile not in (CURRENT_PROFILE, LEGACY_PROFILE):
        raise ImportFault(503, 'import_persisted_report_invalid')
    legacy = profile == LEGACY_PROFILE
    pins = {name: dict(value) for name, value in (LEGACY_CORE_PINS if legacy else CORE_PINS).items()}
    release = {'source_commit': 'ad3bad6e8c3c8c0b9f42551cd9a893876e659227' if legacy else CORE_SOURCE_COMMIT,
               'source_pins': pins, 'sha256': digest(canonical_json(pins, CONFIG_MAX_BYTES))}
    return {'schemaVersion': 1, 'publisher_contract': PUBLISHER_CONTRACT,
            'parser_contract': CONTRACT_VERSION, 'report_schema_version': 1,
            'parser_release': release,
            'adapter_version': 'raw_bytea_v1', 'representation': 'raw_backed',
            'parsed_table_materialized': False, 'limits': dict(LEGACY_LIMITS) if legacy else asdict(ParserLimits())}


def configuration_limits(config_json):
    """Accept the canonical bytes of exactly one approved profile, never arbitrary limits."""
    for profile in (CURRENT_PROFILE, LEGACY_PROFILE):
        expected = configuration(profile)
        if type(config_json) is str and config_json == canonical_json(expected, CONFIG_MAX_BYTES):
            return ParserLimits(**expected['limits'])
    raise ImportFault(503, 'import_persisted_report_invalid')


def version_key(file_id, config_json):
    return str(uuid5(KEY_NAMESPACE, file_id + ':' + CONTRACT_VERSION + ':' + digest(config_json)))


def check_ids(subject, project_id, file_id):
    if not canonical_uuid(subject):
        raise ImportFault(401, 'unauthenticated')
    if not canonical_uuid(project_id) or not canonical_uuid(file_id):
        raise ImportFault(400, 'invalid_import_request')


def verify_parser_release(checkpoint):
    """Only the two fixed public core files, never runtime configuration or caller paths."""
    for name, expected in CORE_PINS.items():
        checkpoint()
        path = Path(__file__).with_name(name)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size != expected['bytes']:
                raise ImportFault(503, 'parser_release_mismatch')
            raw = os.read(fd, expected['bytes'] + 1)
            if len(raw) != expected['bytes'] or hashlib.sha256(raw).hexdigest() != expected['sha256']:
                raise ImportFault(503, 'parser_release_mismatch')
        finally:
            os.close(fd)
    checkpoint()


def trusted_source(raw, project_id, file_id, checkpoint):
    checkpoint()
    if (type(raw) is not StoredRaw or raw.project_id != project_id or raw.file_id != file_id
            or raw.format != 'proteinGroups_txt' or raw.status != 'stored'
            or type(raw.content) is not bytes or type(raw.bytes) is not int
            or not 1 <= raw.bytes <= ParserLimits().max_bytes or len(raw.content) != raw.bytes
            or hashlib.sha256(raw.content).hexdigest() != raw.sha256):
        raise ImportFault(503, 'import_source_integrity_failed')
    checkpoint()
    return SourceRef(raw.project_id, raw.file_id, 'raw_bytea_v1:' + raw.file_id + ':' + raw.sha256,
                     raw.bytes, raw.sha256)


def build_candidate(raw, source, config_json, key, checkpoint, cancelled):
    # Old releases are readable only. Never label a new parse with legacy pins.
    if config_json != canonical_json(configuration(), CONFIG_MAX_BYTES):
        raise ImportFault(503, 'import_persisted_report_invalid')
    outcome = parse_protein_groups(source, (raw.content[i:i+65536] for i in range(0, raw.bytes, 65536)),
                                   limits=configuration_limits(config_json), cancelled=cancelled)
    checkpoint()
    if not outcome.ok:
        raise ImportFault(422, 'txt_import_failed', issues=tuple(asdict(issue) for issue in outcome.report.issues))
    parsed = outcome.parsed
    report = asdict(outcome.report)
    report.update(dataset_version_id=key, persisted=True)
    row_spans = []
    for index, row in enumerate(parsed.rows):
        if index % 256 == 0:
            checkpoint()
        row_spans.append({'record_number': row.record_number, **asdict(row.source_span)})
    columns = [asdict(column) for column in parsed.columns]
    warnings = []
    for field, code in [('empty_group_count', 'EMPTY_PROTEIN_GROUPS'),
                        ('quant_empty_string_cells', 'QUANT_EMPTY_STRING_CELLS')]:
        if report[field]:
            warnings.append({'code': code, 'count': report[field]})
    document = {'schemaVersion': 1, 'representation': 'raw_backed', 'parsed_table_materialized': False,
                'report': report, 'columns': columns,
                'sample_columns': [column for column in columns if column['quant_family'] is not None],
                'header_span': asdict(parsed.header_span), 'row_source_spans': row_spans, 'warnings': warnings}
    encoded = canonical_json(document, REPORT_MAX_BYTES, checkpoint)
    checkpoint()
    return ImportCandidate(key, source, config_json, digest(config_json), encoded, digest(encoded))


def decode_version(row, source, config_json, key):
    """Validate persisted schema/bounds/provenance. Never returns cells or raw contents."""
    try:
        limits = configuration_limits(config_json)
        if key != version_key(source.raw_file_id, config_json) or not 1 <= source.expected_bytes <= limits.max_bytes:
            raise ValueError()
        (vid, project, fid, contract, config_hash, stored_config, source_bytes, source_hash,
         representation, materialized, report_json, report_hash, created_at) = row
        if (str(vid) != key or str(project) != source.project_id or str(fid) != source.raw_file_id
                or contract != CONTRACT_VERSION or stored_config != config_json or config_hash != digest(config_json)
                or source_bytes != source.expected_bytes or source_hash != source.expected_raw_sha256
                or representation != 'raw_backed' or materialized is not False
                or type(report_json) is not str or len(report_json.encode('utf-8')) > REPORT_MAX_BYTES
                or digest(report_json) != report_hash):
            raise ValueError()
        def unique(pairs):
            result = {}
            for name, value in pairs:
                if name in result:
                    raise ValueError()
                result[name] = value
            return result
        document = json.loads(report_json, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if (type(document) is not dict or set(document) != {'schemaVersion','representation','parsed_table_materialized','report','columns','sample_columns','header_span','row_source_spans','warnings'}
                or type(document['schemaVersion']) is not int or document['schemaVersion'] != 1
                or document['representation'] != 'raw_backed' or document['parsed_table_materialized'] is not False):
            raise ValueError()
        report = document['report']
        if (type(report) is not dict or set(report) != {field.name for field in fields(ImportReportDraft)}
                or report['source'] != asdict(source) or report['status'] != 'parsed' or report['persisted'] is not True
                or report['dataset_version_id'] != key or report['source_complete'] is not True
                or report['observed_bytes'] != source.expected_bytes or report['observed_raw_sha256'] != source.expected_raw_sha256
                or report['parser_contract_version'] != CONTRACT_VERSION or report['issues'] != []
                or report['association_status'] != 'not_requested' or report['association_counts'] is not None
                or report['scientific_analysis_status'] != 'not_started'):
            raise ValueError()
        counts = ('row_count','column_count','intensity_sample_count','lfq_sample_count','member_occurrences',
                  'empty_group_count','quant_empty_string_cells','quant_exact_zero_string_cells','crlf_count','bare_cr_count','bare_lf_count')
        if any(type(report[name]) is not int or report[name] < 0 for name in counts):
            raise ValueError()
        columns, spans = document['columns'], document['row_source_spans']
        if (type(columns) is not list or not 2 <= len(columns) == report['column_count'] <= limits.max_columns
                or type(spans) is not list or not 1 <= len(spans) == report['row_count'] <= limits.max_records):
            raise ValueError()
        for ordinal, column in enumerate(columns):
            if (type(column) is not dict or set(column) != {'ordinal','name_raw','quant_family','sample_label_raw'}
                    or type(column['ordinal']) is not int or column['ordinal'] != ordinal or type(column['name_raw']) is not str
                    or column['quant_family'] not in (None,'Intensity','LFQ intensity')
                    or (column['quant_family'] is None and column['sample_label_raw'] is not None)
                    or (column['quant_family'] is not None and (type(column['sample_label_raw']) is not str
                        or column['name_raw'] != column['quant_family'] + ' ' + column['sample_label_raw']))):
                raise ValueError()
        if (sum(column['name_raw']=='Protein IDs' for column in columns)!=1
                or report['row_count']*report['column_count']>limits.max_cells
                or report['member_occurrences']>limits.max_member_occurrences or report['empty_group_count']>report['row_count']
                or report['quant_empty_string_cells']+report['quant_exact_zero_string_cells']>report['row_count']*(report['intensity_sample_count']+report['lfq_sample_count'])):
            raise ValueError()
        if document['sample_columns'] != [column for column in columns if column['quant_family'] is not None]:
            raise ValueError()
        for family, field in [('Intensity','intensity_sample_count'),('LFQ intensity','lfq_sample_count')]:
            if report[field] != sum(column['quant_family'] == family for column in columns):
                raise ValueError()
        header=document['header_span']
        if (type(header) is not dict or set(header)!={'start_byte','end_byte','start_line','end_line'}
                or any(type(value) is not int for value in header.values()) or header['start_byte']!=0
                or not 0<=header['end_byte']<=source.expected_bytes or header['start_line']!=1 or header['end_line']<1):
            raise ValueError()
        previous = header['end_byte']
        for number, span in enumerate(spans, 1):
            if (set(span) != {'record_number','start_byte','end_byte','start_line','end_line'}
                    or any(type(value) is not int for value in span.values()) or span['record_number'] != number
                    or not previous <= span['start_byte'] <= span['end_byte'] <= source.expected_bytes
                    or not 1 <= span['start_line'] <= span['end_line']):
                raise ValueError()
            previous = span['end_byte']
        if type(report['leading_bom']) is not bool or type(document['warnings']) is not list:
            raise ValueError()
        expected_warnings=[{'code':code,'count':report[field]} for field,code in
                           [('empty_group_count','EMPTY_PROTEIN_GROUPS'),('quant_empty_string_cells','QUANT_EMPTY_STRING_CELLS')] if report[field]]
        if document['warnings']!=expected_warnings or not isinstance(created_at,datetime) or created_at.utcoffset() is None:
            raise ValueError()
        stamp = created_at.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        return {'datasetVersion': {'dataset_version_id': key, 'project_id': source.project_id, 'file_id': source.raw_file_id,
                                  'parser_contract': CONTRACT_VERSION, 'configuration': json.loads(config_json),
                                  'config_sha256': config_hash, 'source': asdict(source), 'representation': 'raw_backed',
                                  'parsed_table_materialized': False, 'createdAt': stamp}, 'importReport': document}
    except Exception:
        raise ImportFault(503, 'import_persisted_report_invalid') from None
