"""Bounded pure bytes-to-TSV core. No I/O, authorization, storage or analysis.

Consumes one finite iterator of immutable bytes. Cancellation is cooperative:
the caller must bound a blocking producer; no timer, thread or memory guarantee
is provided. Exactly one leading BOM is omitted from the decoded view. Original
bytes/SHA remain untouched. CRLF inside quoted cells is retained verbatim.
"""
import codecs
from dataclasses import dataclass, fields
import hashlib

from .import_report import (
    ImportFailure, ImportIssue, ImportReportDraft, ParseOutcome, SourceRef,
)

# ECMAScript WhiteSpace + LineTerminator, deliberately not Python str.strip().
ECMASCRIPT_TRIM = ('\u0009\u000b\u000c\u0020\u00a0\u1680'
                  '\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a'
                  '\u202f\u205f\u3000\ufeff\n\r\u2028\u2029')


def utf16_units(value):
    return sum(2 if ord(char) > 0xffff else 1 for char in value)


@dataclass(frozen=True, slots=True)
class ParserLimits:
    max_bytes: int = 25 * 1024 * 1024
    max_records: int = 10000
    max_columns: int = 512
    max_cells: int = 2000000
    max_field_units: int = 1000000
    max_member_occurrences: int = 100000
    max_id_units: int = 2000000

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not int or not 1 <= value <= field.default:
                raise ImportFailure(ImportIssue('INVALID_LIMITS', limit_key=field.name))


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """Half-open raw byte interval excluding the external record separator.

    Physical lines are one-based; embedded quoted CR/LF belongs to this span.
    The header span includes the original leading BOM, if any.
    """
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class Column:
    ordinal: int
    name_raw: str
    quant_family: str | None
    sample_label_raw: str | None


@dataclass(frozen=True, slots=True)
class Member:
    segment_ordinal: int
    token: str


@dataclass(frozen=True, slots=True, repr=False)
class ProteinGroupRow:
    record_number: int
    source_span: SourceSpan
    cells: tuple[str, ...]
    protein_ids_raw: str
    members: tuple[Member, ...]


@dataclass(frozen=True, slots=True, repr=False)
class ParsedProteinGroups:
    source: SourceRef
    raw_bytes: int
    raw_sha256: str
    columns: tuple[Column, ...]
    header_span: SourceSpan
    rows: tuple[ProteinGroupRow, ...]


class _Scanner:
    def __init__(self, limits, control):
        self.limits, self.control = limits, control
        self.columns = None
        self.header_span = None
        self.rows = []
        self.row = []
        self.field = []
        self.field_units = 0
        self.quoted = self.closed_quote = self.touched = False
        self.skip_record_lf = self.previous_cr = False
        self.first = True
        self.bom = False
        self.byte_cursor = self.record_start = 0
        self.line = self.record_start_line = 1
        self.cr = self.lf = self.crlf = 0
        self.cells = self.members = self.id_units = self.empty_groups = 0
        self.empty_quant = self.zero_quant = 0
        self.quant_indices = ()
        self.protein_index = 0

    def check_control(self):
        if self.control is None:
            return
        try:
            cancelled = self.control()
        except Exception:
            raise ImportFailure(ImportIssue('CONTROL_FAILED')) from None
        if type(cancelled) is not bool:
            raise ImportFailure(ImportIssue('CONTROL_FAILED'))
        if cancelled:
            raise ImportFailure(ImportIssue('CANCELLED'))

    def fail(self, code, limit_key=None):
        raise ImportFailure(ImportIssue(code, len(self.rows) + 1 if self.columns is not None else 0,
                                       self.line, len(self.row), limit_key))

    def append(self, char):
        self.field_units += 2 if ord(char) > 0xffff else 1
        if self.field_units > self.limits.max_field_units:
            self.fail('RESOURCE_LIMIT', 'max_field_units')
        self.field.append(char)

    def end_field(self):
        if len(self.row) >= self.limits.max_columns:
            self.fail('RESOURCE_LIMIT', 'max_columns')
        if self.columns is not None and self.cells + len(self.row) + 1 > self.limits.max_cells:
            self.fail('RESOURCE_LIMIT', 'max_cells')
        self.row.append(''.join(self.field))
        self.field.clear()
        self.field_units = 0
        self.closed_quote = False

    def end_record(self, end_byte):
        self.check_control()
        self.end_field()
        values = tuple(self.row)
        span = SourceSpan(self.record_start, end_byte, self.record_start_line, self.line)
        if self.columns is None:
            if len(values) < 2:
                self.fail('TXT_HEADER_REQUIRED')
            if values.count('Protein IDs') != 1:
                self.fail('TXT_PROTEIN_IDS_HEADER_COUNT')
            columns = []
            for ordinal, name in enumerate(values):
                family = next((prefix for prefix in ('Intensity', 'LFQ intensity')
                               if name.startswith(prefix + ' ')), None)
                columns.append(Column(ordinal, name, family, name[len(family) + 1:] if family else None))
            self.columns = tuple(columns)
            self.header_span = span
            self.protein_index = values.index('Protein IDs')
            self.quant_indices = tuple(c.ordinal for c in self.columns if c.quant_family)
        else:
            if len(values) != len(self.columns):
                self.fail('TXT_ROW_WIDTH')
            if len(self.rows) >= self.limits.max_records:
                self.fail('RESOURCE_LIMIT', 'max_records')
            raw_ids = values[self.protein_index]
            members = []
            next_units = self.id_units + utf16_units(raw_ids)
            if next_units > self.limits.max_id_units:
                self.fail('RESOURCE_LIMIT', 'max_id_units')
            # Empty segments remain traceable through the original field and
            # segment ordinals; no object per empty segment is allocated.
            for ordinal, raw_token in enumerate(raw_ids.split(';')):
                if ordinal % 1024 == 0:
                    self.check_control()
                token = raw_token.strip(ECMASCRIPT_TRIM)
                if not token:
                    continue
                if self.members + len(members) + 1 > self.limits.max_member_occurrences:
                    self.fail('RESOURCE_LIMIT', 'max_member_occurrences')
                next_units += utf16_units(token)
                if next_units > self.limits.max_id_units:
                    self.fail('RESOURCE_LIMIT', 'max_id_units')
                members.append(Member(ordinal, token))
            self.id_units = next_units
            self.members += len(members)
            self.empty_groups += not members
            self.cells += len(values)
            self.empty_quant += sum(values[i] == '' for i in self.quant_indices)
            self.zero_quant += sum(values[i] == '0' for i in self.quant_indices)
            self.rows.append(ProteinGroupRow(len(self.rows) + 1, span, values, raw_ids, tuple(members)))
        self.row.clear()
        self.touched = False

    def feed(self, text):
        for offset, char in enumerate(text):
            if offset % 1024 == 0:
                self.check_control()
            start = self.byte_cursor
            code = ord(char)
            self.byte_cursor += 1 if code < 0x80 else 2 if code < 0x800 else 3 if code < 0x10000 else 4
            if self.first:
                self.first = False
                if char == '\ufeff':
                    self.bom = True
                    continue
            if char == '\0':
                self.fail('NUL_BYTE')
            skip = self.skip_record_lf and char == '\n'
            self.skip_record_lf = False
            separator = False
            if skip:
                pass
            elif self.quoted:
                if char == '"':
                    self.quoted = False
                    self.closed_quote = True
                else:
                    self.append(char)
            elif self.closed_quote and char == '"':
                self.append('"')
                self.quoted = True
                self.closed_quote = False
            elif char == '\t':
                self.end_field()
                self.touched = True
            elif char in ('\r', '\n'):
                self.end_record(start)
                separator = True
                self.skip_record_lf = char == '\r'
            elif self.closed_quote:
                self.fail('TXT_QUOTE_SYNTAX')
            elif char == '"' and not self.field:
                self.quoted = True
                self.touched = True
            else:
                self.append(char)
                self.touched = True
            if char == '\r':
                self.cr += 1
                self.line += 1
            elif char == '\n':
                self.lf += 1
                if self.previous_cr:
                    self.crlf += 1
                else:
                    self.line += 1
            self.previous_cr = char == '\r'
            if separator or skip:
                self.record_start = self.byte_cursor
                self.record_start_line = self.line

    def finish(self):
        if self.quoted:
            self.fail('TXT_QUOTE_SYNTAX')
        if self.touched or self.row or self.field or self.closed_quote:
            self.end_record(self.byte_cursor)
        if self.columns is None:
            self.fail('EMPTY_FILE')
        if not self.rows:
            self.fail('TXT_HEADER_REQUIRED')


def parse_protein_groups(source, byte_chunks, limits=None, cancelled=None):
    """Return an immutable private draft; failures never expose provisional rows.

    SourceRef is only descriptive. The future caller must authorize the exact
    project/file version before supplying chunks. A successful result means
    parsed bytes only: association not_requested, no DatasetVersion/publication.
    Input exceptions become fixed codes. Producer blocking cannot be interrupted.
    """
    digest = hashlib.sha256()
    observed = 0
    complete = False
    scanner = None
    try:
        if type(source) is not SourceRef:
            raise ImportFailure(ImportIssue('INVALID_SOURCE_REF'))
        if limits is None:
            limits = ParserLimits()
        if type(limits) is not ParserLimits:
            raise ImportFailure(ImportIssue('INVALID_LIMITS'))
        if cancelled is not None and not callable(cancelled):
            raise ImportFailure(ImportIssue('INVALID_CONTROL'))
        scanner = _Scanner(limits, cancelled)
        scanner.check_control()
        if source.expected_bytes > limits.max_bytes:
            raise ImportFailure(ImportIssue('RESOURCE_LIMIT', limit_key='max_bytes'))
        decoder = codecs.getincrementaldecoder('utf-8')('strict')
        try:
            iterator = iter(byte_chunks)
        except Exception:
            raise ImportFailure(ImportIssue('INPUT_READ_FAILED')) from None
        while True:
            scanner.check_control()
            try:
                chunk = next(iterator)
            except StopIteration:
                break
            except Exception:
                raise ImportFailure(ImportIssue('INPUT_READ_FAILED')) from None
            if type(chunk) is not bytes:
                raise ImportFailure(ImportIssue('INVALID_CHUNK'))
            observed += len(chunk)
            if observed > limits.max_bytes:
                raise ImportFailure(ImportIssue('RESOURCE_LIMIT', limit_key='max_bytes'))
            digest.update(chunk)
            try:
                decoded = decoder.decode(chunk, final=False)
            except UnicodeDecodeError:
                raise ImportFailure(ImportIssue('INVALID_UTF8')) from None
            scanner.feed(decoded)
        complete = True
        try:
            scanner.feed(decoder.decode(b'', final=True))
        except UnicodeDecodeError:
            raise ImportFailure(ImportIssue('INVALID_UTF8')) from None
        scanner.check_control()
        if observed != source.expected_bytes or digest.hexdigest() != source.expected_raw_sha256:
            raise ImportFailure(ImportIssue('SOURCE_INTEGRITY_MISMATCH'))
        scanner.finish()
        scanner.check_control()
        parsed = ParsedProteinGroups(source, observed, digest.hexdigest(), scanner.columns,
                                     scanner.header_span, tuple(scanner.rows))
        report = ImportReportDraft(
            source, 'parsed', observed, digest.hexdigest(), True,
            row_count=len(scanner.rows), column_count=len(scanner.columns),
            intensity_sample_count=sum(c.quant_family == 'Intensity' for c in scanner.columns),
            lfq_sample_count=sum(c.quant_family == 'LFQ intensity' for c in scanner.columns),
            member_occurrences=scanner.members, empty_group_count=scanner.empty_groups,
            quant_empty_string_cells=scanner.empty_quant,
            quant_exact_zero_string_cells=scanner.zero_quant,
            leading_bom=scanner.bom, crlf_count=scanner.crlf,
            bare_cr_count=scanner.cr - scanner.crlf, bare_lf_count=scanner.lf - scanner.crlf)
        return ParseOutcome(True, parsed, report)
    except ImportFailure as failure:
        report = ImportReportDraft(source if type(source) is SourceRef else None,
                                   'failed', observed, digest.hexdigest() if complete else None, complete,
                                   issues=(failure.issue,))
        return ParseOutcome(False, None, report)
