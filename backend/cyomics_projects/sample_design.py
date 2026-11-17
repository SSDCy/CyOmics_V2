"""Offline sample_design_review_v0.2 DTOs; Python standard library only.

The caller must authorize and fully validate the dataset/report before binding
sample_columns. Structural binding here is not authentication or persistence.
No I/O, inference, statistics, contrast or analysis-readiness is supplied.
"""
from collections import Counter
from dataclasses import dataclass
from math import isfinite
from uuid import UUID

CONTRACT = 'sample_design_review_v0.2'
FAMILIES = ('Intensity', 'LFQ intensity')
CONTROLS = ('unknown', 'yes', 'no')
REPLICATE_TYPES = ('unknown', 'biological', 'technical')
REQUEST_KEYS = frozenset(('schemaVersion', 'dataset_version_id', 'quant_family', 'rows'))
ROW_KEYS = frozenset(('column_ordinal', 'name_raw', 'sample_label_raw', 'group',
                      'condition', 'control', 'replicate', 'replicate_type', 'confirmed'))
MANUAL_KEYS = frozenset(('group', 'condition', 'control', 'replicate', 'replicate_type'))
SOURCE_KEYS = frozenset(('ordinal', 'name_raw', 'quant_family', 'sample_label_raw'))
TEXT_LIMITS = {'group': 80, 'condition': 160, 'replicate': 40}
MAX_SAFE_INTEGER = 9007199254740991
# Match browser String.trim and the existing parser's explicit ECMAScript set.
# Never apply this to stored manual/source text; it is only a blank/edge check.
TRIM = ('\u0009\u000b\u000c\u0020\u00a0\u1680'
        '\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a'
        '\u202f\u205f\u3000\ufeff\n\r\u2028\u2029')


class DesignError(ValueError):
    """Fixed diagnostics, never arbitrary source names or manual field values."""
    def __init__(self, code, field=None, column_ordinal=None):
        self.code = code
        self.field = field
        self.column_ordinal = column_ordinal
        super().__init__(code)


def _require(ok, code, field=None, ordinal=None):
    if not ok:
        raise DesignError(code, field, ordinal)


def _canonical_uuid(value):
    if type(value) is not str:
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _json_integer(value):
    """JSON number semantics shared with JS: finite safe integers, excluding bool."""
    if type(value) is int:
        return value if -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER else None
    if (type(value) is float and isfinite(value) and value.is_integer()
            and -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER):
        return int(value)
    return None


@dataclass(frozen=True, slots=True)
class SourceColumn:
    ordinal: int
    name_raw: str
    quant_family: str
    sample_label_raw: str


@dataclass(frozen=True, slots=True)
class TrustedSource:
    dataset_version_id: str
    sample_columns: tuple[SourceColumn, ...]


def _check_source(source):
    _require(type(source) is TrustedSource, 'invalid_trusted_source')
    _require(_canonical_uuid(source.dataset_version_id), 'invalid_trusted_source')
    _require(type(source.sample_columns) is tuple, 'invalid_trusted_source')
    seen = set()
    for column in source.sample_columns:
        _require(type(column) is SourceColumn, 'invalid_trusted_source')
        _require(type(column.ordinal) is int and _json_integer(column.ordinal) is not None and column.ordinal >= 0
                 and column.ordinal not in seen, 'invalid_trusted_source')
        _require(type(column.quant_family) is str and column.quant_family in FAMILIES
                 and type(column.name_raw) is str and type(column.sample_label_raw) is str
                 and column.name_raw == column.quant_family + ' ' + column.sample_label_raw,
                 'invalid_trusted_source')
        seen.add(column.ordinal)
    return source


def bind_trusted_source(dataset_version_id, validated_sample_columns):
    """Copy exact parser/report four-key descriptors, preserving their order.

    Only call with server-owned, previously authorized/validated report data.
    This function cannot prove that a supplied dict came from that server.
    """
    _require(type(validated_sample_columns) in (list, tuple), 'invalid_trusted_source')
    columns = []
    for item in validated_sample_columns:
        _require(type(item) is dict and set(item) == SOURCE_KEYS, 'invalid_trusted_source')
        ordinal = _json_integer(item['ordinal'])
        _require(ordinal is not None and ordinal >= 0, 'invalid_trusted_source')
        columns.append(SourceColumn(ordinal, item['name_raw'],
                                    item['quant_family'], item['sample_label_raw']))
    return _check_source(TrustedSource(dataset_version_id, tuple(columns)))


@dataclass(frozen=True, slots=True)
class DesignRow:
    column_ordinal: int
    name_raw: str
    sample_label_raw: str
    group: str
    condition: str
    control: str
    replicate: str
    replicate_type: str
    confirmed: bool

    def to_payload(self):
        return {key: getattr(self, key) for key in (
            'column_ordinal', 'name_raw', 'sample_label_raw', 'group', 'condition',
            'control', 'replicate', 'replicate_type', 'confirmed')}


@dataclass(frozen=True, slots=True)
class SampleDesign:
    dataset_version_id: str
    quant_family: str
    rows: tuple[DesignRow, ...]

    def to_payload(self):
        return {'schemaVersion': 1, 'dataset_version_id': self.dataset_version_id,
                'quant_family': self.quant_family,
                'rows': [row.to_payload() for row in self.rows]}


@dataclass(frozen=True, slots=True)
class ConfirmationBlocker:
    code: str
    column_ordinals: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ValidatedDesign:
    design: SampleDesign
    all_rows_explicitly_confirmed: bool
    confirmation_blockers: tuple[ConfirmationBlocker, ...]

    @property
    def globally_confirmed(self):
        return self.all_rows_explicitly_confirmed and not self.confirmation_blockers

    @property
    def ready_for_differential_analysis(self):
        # Contrast, statistical design and replication policy remain later gates.
        return False


def _selected(source, family):
    _check_source(source)
    _require(type(family) is str and family in FAMILIES, 'invalid_quant_family', 'quant_family')
    return tuple(c for c in source.sample_columns if c.quant_family == family)


def _ambiguous(columns):
    counts = Counter(c.sample_label_raw for c in columns)
    # Duplicate comparison is exact raw text, never trim/casefold/normalization.
    return frozenset(c.ordinal for c in columns
                     if not c.sample_label_raw.strip(TRIM) or counts[c.sample_label_raw] > 1)


def _manual_text(value, name, ordinal):
    _require(type(value) is str, 'invalid_manual_text', name, ordinal)
    _require(not any(0xD800 <= ord(c) <= 0xDFFF for c in value),
             'manual_text_invalid_unicode_scalar', name, ordinal)
    _require(len(value) <= TEXT_LIMITS[name], 'manual_text_too_long', name, ordinal)
    _require(not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value),
             'manual_text_control_character', name, ordinal)


def validate_design(payload, source):
    """Validate one exact request, without modifying or normalizing its values.

    A True flag is only an explicit payload fact, not proof of a human action.
    Later UI/API state handling must use the edit/switch transitions below.
    """
    _check_source(source)  # Validate trusted descriptors before dependent success.
    _require(type(payload) is dict and set(payload) == REQUEST_KEYS, 'invalid_request_keys')
    _require(_json_integer(payload['schemaVersion']) == 1,
             'invalid_schema_version', 'schemaVersion')
    _require(_canonical_uuid(payload['dataset_version_id']), 'invalid_dataset_version', 'dataset_version_id')
    _require(payload['dataset_version_id'] == source.dataset_version_id,
             'dataset_version_mismatch', 'dataset_version_id')
    selected = _selected(source, payload['quant_family'])
    items = payload['rows']
    _require(type(items) is list, 'invalid_rows', 'rows')
    _require(len(items) == len(selected), 'row_coverage_mismatch', 'rows')
    by_ordinal = {c.ordinal: c for c in selected}
    ambiguous = _ambiguous(selected)
    rows = []
    seen = set()
    for index, item in enumerate(items):
        _require(type(item) is dict and set(item) == ROW_KEYS, 'invalid_row_keys')
        ordinal = _json_integer(item['column_ordinal'])
        _require(ordinal is not None and ordinal >= 0, 'invalid_column_ordinal', 'column_ordinal')
        _require(ordinal not in seen, 'duplicate_column_ordinal', 'column_ordinal', ordinal)
        _require(ordinal in by_ordinal, 'source_column_mismatch', 'column_ordinal', ordinal)
        # v0.2: do not sort or silently repair an incoming permutation.
        _require(ordinal == selected[index].ordinal, 'row_order_mismatch', 'column_ordinal', ordinal)
        column = by_ordinal[ordinal]
        _require(type(item['name_raw']) is str and type(item['sample_label_raw']) is str
                 and item['name_raw'] == column.name_raw
                 and item['sample_label_raw'] == column.sample_label_raw,
                 'source_value_mismatch', ordinal=ordinal)
        for field in TEXT_LIMITS:
            _manual_text(item[field], field, ordinal)
        _require(type(item['control']) is str and item['control'] in CONTROLS,
                 'invalid_control', 'control', ordinal)
        _require(type(item['replicate_type']) is str and item['replicate_type'] in REPLICATE_TYPES,
                 'invalid_replicate_type', 'replicate_type', ordinal)
        _require(type(item['confirmed']) is bool, 'invalid_confirmed', 'confirmed', ordinal)
        if item['confirmed']:
            _require(ordinal not in ambiguous, 'ambiguous_sample_label', 'confirmed', ordinal)
            _require(all(item[field].strip(TRIM) for field in TEXT_LIMITS),
                     'confirmation_incomplete', 'confirmed', ordinal)
            _require(item['group'] == item['group'].strip(TRIM),
                     'confirmed_group_requires_explicit_trim', 'group', ordinal)
            _require(item['control'] != 'unknown' and item['replicate_type'] != 'unknown',
                     'confirmation_incomplete', 'confirmed', ordinal)
        rows.append(DesignRow(**{**item, 'column_ordinal': ordinal}))
        seen.add(ordinal)
    design = SampleDesign(source.dataset_version_id, payload['quant_family'], tuple(rows))
    blockers = []
    if not selected:
        blockers.append(ConfirmationBlocker('no_selected_columns', ()))
    if ambiguous:
        blockers.append(ConfirmationBlocker('ambiguous_sample_labels',
                                            tuple(c.ordinal for c in selected if c.ordinal in ambiguous)))
    unconfirmed = tuple(row.column_ordinal for row in rows if not row.confirmed)
    if unconfirmed:
        blockers.append(ConfirmationBlocker('unconfirmed_rows', unconfirmed))
    groups = {}
    for row in rows:
        if row.group.strip(TRIM) and row.control != 'unknown':
            groups.setdefault(row.group, []).append(row)
    for group_rows in groups.values():
        if len({row.control for row in group_rows}) > 1:
            blockers.append(ConfirmationBlocker('group_control_inconsistent',
                                                tuple(row.column_ordinal for row in group_rows)))
    return ValidatedDesign(design, bool(rows) and all(row.confirmed for row in rows), tuple(blockers))


def _design_payload(design, source):
    _require(type(design) is SampleDesign, 'invalid_design')
    payload = design.to_payload()
    validate_design(payload, source)
    return payload


def new_draft(source, quant_family, suggested_facts_by_ordinal=None):
    """Create blank rows or explicit ordinal-bound suggestions, always unconfirmed."""
    columns = _selected(source, quant_family)
    suggestions = {} if suggested_facts_by_ordinal is None else suggested_facts_by_ordinal
    _require(type(suggestions) is dict, 'invalid_suggestions')
    valid_ordinals = {c.ordinal for c in columns}
    _require(all(type(k) is int and k in valid_ordinals for k in suggestions), 'invalid_suggestions')
    rows = []
    for column in columns:
        facts = suggestions.get(column.ordinal, {})
        _require(type(facts) is dict and set(facts) <= MANUAL_KEYS, 'invalid_suggestions')
        row = {'column_ordinal': column.ordinal, 'name_raw': column.name_raw,
               'sample_label_raw': column.sample_label_raw, 'group': '', 'condition': '',
               'control': 'unknown', 'replicate': '', 'replicate_type': 'unknown', 'confirmed': False}
        row.update(facts)
        rows.append(row)
    return validate_design({'schemaVersion': 1, 'dataset_version_id': source.dataset_version_id,
                            'quant_family': quant_family, 'rows': rows}, source)


def edit_row(design, source, column_ordinal, changes):
    """Only an actual fact change clears the flag; same-value/empty patches preserve it."""
    payload = _design_payload(design, source)
    column_ordinal = _json_integer(column_ordinal)
    _require(column_ordinal is not None and column_ordinal >= 0, 'invalid_column_ordinal')
    _require(type(changes) is dict and set(changes) <= MANUAL_KEYS, 'invalid_edit')
    row = next((r for r in payload['rows'] if r['column_ordinal'] == column_ordinal), None)
    _require(row is not None, 'source_column_mismatch')
    changed = any(row[key] != value for key, value in changes.items())
    row.update(changes)
    if changed:
        row['confirmed'] = False
    return validate_design(payload, source)


def set_row_confirmed(design, source, column_ordinal, confirmed):
    """Only this explicit transition requests a confirmation; normal validation applies."""
    payload = _design_payload(design, source)
    column_ordinal = _json_integer(column_ordinal)
    _require(column_ordinal is not None and column_ordinal >= 0, 'invalid_column_ordinal')
    _require(type(confirmed) is bool, 'invalid_confirmed')
    row = next((r for r in payload['rows'] if r['column_ordinal'] == column_ordinal), None)
    _require(row is not None, 'source_column_mismatch')
    row['confirmed'] = confirmed
    return validate_design(payload, source)


def switch_quantification(design, source, quant_family, suggested_facts_by_ordinal=None):
    """Never inherit flags or infer/copy facts by sample labels between families."""
    _design_payload(design, source)
    return new_draft(source, quant_family, suggested_facts_by_ordinal)
