"""Private import drafts and source descriptors, never authentication or storage.

No paths, credentials, database connections or publication are resolved here.
"""
from dataclasses import dataclass
import re

CONTRACT_VERSION = 'protein_groups_txt_v1'
ERROR_CODES = frozenset({
    'INVALID_SOURCE_REF', 'INVALID_LIMITS', 'INVALID_CONTROL', 'CONTROL_FAILED',
    'INPUT_READ_FAILED', 'INVALID_CHUNK', 'SOURCE_INTEGRITY_MISMATCH',
    'INVALID_UTF8', 'NUL_BYTE', 'EMPTY_FILE', 'TXT_HEADER_REQUIRED',
    'TXT_PROTEIN_IDS_HEADER_COUNT', 'TXT_ROW_WIDTH', 'TXT_QUOTE_SYNTAX',
    'RESOURCE_LIMIT', 'CANCELLED',
})
LIMIT_KEYS = frozenset({
    'max_bytes', 'max_records', 'max_columns', 'max_cells', 'max_field_units',
    'max_member_occurrences', 'max_id_units',
})


@dataclass(frozen=True, slots=True)
class ImportIssue:
    code: str
    record_number: int | None = None
    line: int | None = None
    column_ordinal: int | None = None
    limit_key: str | None = None

    def __post_init__(self):
        if (self.code not in ERROR_CODES
                or self.limit_key is not None and self.limit_key not in LIMIT_KEYS
                or any(value is not None and (type(value) is not int or value < 0)
                       for value in (self.record_number, self.line, self.column_ordinal))):
            raise ValueError('invalid_import_issue')


class ImportFailure(Exception):
    """Only a fixed code and safe positions; no input or original exception text."""
    def __init__(self, issue):
        if not isinstance(issue, ImportIssue):
            raise ValueError('invalid_import_issue')
        self.issue = issue
        super().__init__(issue.code)


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Opaque server-supplied provenance, NOT evidence of authorization/stored state."""
    project_id: str
    raw_file_id: str
    raw_version_key: str
    expected_bytes: int
    expected_raw_sha256: str
    role: str = 'quantification'
    format: str = 'proteinGroups_txt'
    adapter_version: str = 'raw_bytea_v1'

    def __post_init__(self):
        ids = (self.project_id, self.raw_file_id, self.raw_version_key, self.adapter_version)
        if (any(type(value) is not str or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}', value) is None for value in ids)
                or type(self.expected_bytes) is not int or self.expected_bytes < 1
                or type(self.expected_raw_sha256) is not str
                or re.fullmatch(r'[0-9a-f]{64}', self.expected_raw_sha256) is None
                or self.role != 'quantification' or self.format != 'proteinGroups_txt'):
            raise ImportFailure(ImportIssue('INVALID_SOURCE_REF'))


@dataclass(frozen=True, slots=True)
class ImportReportDraft:
    source: SourceRef | None
    status: str
    observed_bytes: int
    observed_raw_sha256: str | None
    source_complete: bool
    issues: tuple[ImportIssue, ...] = ()
    row_count: int | None = None
    column_count: int | None = None
    intensity_sample_count: int | None = None
    lfq_sample_count: int | None = None
    member_occurrences: int | None = None
    empty_group_count: int | None = None
    quant_empty_string_cells: int | None = None
    quant_exact_zero_string_cells: int | None = None
    leading_bom: bool | None = None
    crlf_count: int | None = None
    bare_cr_count: int | None = None
    bare_lf_count: int | None = None
    parser_contract_version: str = CONTRACT_VERSION
    association_status: str = 'not_requested'
    association_counts: None = None
    dataset_version_id: None = None
    persisted: bool = False
    scientific_analysis_status: str = 'not_started'


@dataclass(frozen=True, slots=True)
class ParseOutcome:
    ok: bool
    parsed: object | None
    report: ImportReportDraft
