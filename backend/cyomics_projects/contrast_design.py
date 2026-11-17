"""Pure contrast_design_draft_v0.1 review + clarification-01.

Caller-owned project authorization and fully validated saved records are required.
This module performs no I/O, persistence, statistics, R, jobs or authentication.
The package sample_design module is the unchanged existing c19 validator.
"""
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from . import sample_design as sd

CONTRACT = 'contrast_design_draft_v0.1'
MAX_CONTRASTS = 32
SOURCE_KEYS = frozenset(('project_id', 'dataset_version_id', 'quant_family', 'sample_columns', 'sample_design_record'))
RECORD_KEYS = frozenset(('sample_design_version_id', 'parent_version_id', 'revision', 'request_id', 'design_sha256', 'created_at', 'design'))
PAYLOAD_KEYS = frozenset(('schemaVersion', 'dataset_version_id', 'sample_design_version_id', 'quant_family', 'contrasts'))
CONTRAST_KEYS = frozenset(('contrast_id', 'numerator_group', 'denominator_group'))
DIRECTION = {'FC': 'numerator/denominator', 'log2FC': 'log2(FC)',
             'positive_effect': 'numerator greater than denominator', 'computed_statistics': False}


class ContrastError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise ContrastError(code)


def canonical(value):
    """Same canonical normalization as the existing saved-sample record contract."""
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


def sha(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


@dataclass(frozen=True, slots=True)
class SourceBinding:
    project_id: str
    dataset_version_id: str
    quant_family: str
    sample_design_version_id: str
    design_sha256: str
    record_sha256: str
    source_sha256: str

    def to_payload(self):
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class BoundSource:
    binding: SourceBinding
    sample_review: sd.ValidatedDesign


def bind_source(value, *, expected_project_id=None):
    """Snapshot trusted server data, checking structure/digest; never authenticates it.

    expected_project_id, when supplied, is server-owned current context, not payload.
    A record's design is checked against independent import sample_columns, never
    descriptors reconstructed from that same client design.
    """
    try:
        require(type(value) is dict and set(value) == SOURCE_KEYS, 'invalid_source')
        project, dataset, family = (value[k] for k in ('project_id', 'dataset_version_id', 'quant_family'))
        require(sd._canonical_uuid(project) and sd._canonical_uuid(dataset), 'invalid_source')
        require(type(family) is str and family in sd.FAMILIES, 'invalid_source')
        if expected_project_id is not None:
            require(sd._canonical_uuid(expected_project_id) and project == expected_project_id, 'source_project_mismatch')
        descriptors = sd.bind_trusted_source(dataset, value['sample_columns'])
        record = value['sample_design_record']
        require(type(record) is dict and set(record) == RECORD_KEYS, 'invalid_source')
        version, parent, request = (record[k] for k in ('sample_design_version_id', 'parent_version_id', 'request_id'))
        revision = sd._json_integer(record['revision'])
        require(sd._canonical_uuid(version) and sd._canonical_uuid(request)
                and (parent is None or sd._canonical_uuid(parent)), 'invalid_source')
        require(revision is not None and 1 <= revision <= 100
                and (parent is None) == (revision == 1), 'invalid_source')
        created = record['created_at']
        require(type(created) is str, 'invalid_source')
        stamp = datetime.fromisoformat(created.replace('Z', '+00:00'))
        require(stamp.utcoffset() is not None and created == stamp.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), 'invalid_source')
        review = sd.validate_design(record['design'], descriptors)
        design = review.design.to_payload()
        require(design['quant_family'] == family, 'invalid_source')
        text = canonical(design)
        require(len(text.encode('utf-8')) <= 131072, 'invalid_source')
        digest = record['design_sha256']
        require(type(digest) is str and re.fullmatch('[0-9a-f]{64}', digest) is not None
                and hashlib.sha256(text.encode('utf-8')).hexdigest() == digest, 'source_digest_mismatch')
        normalized_record = dict(record, revision=revision, design=design)
        normalized_source = dict(value, sample_design_record=normalized_record,
            sample_columns=[{'ordinal': c.ordinal, 'name_raw': c.name_raw,
                             'quant_family': c.quant_family, 'sample_label_raw': c.sample_label_raw}
                            for c in descriptors.sample_columns])
        binding = SourceBinding(project, dataset, family, version, digest,
                                sha(normalized_record), sha(normalized_source))
        return BoundSource(binding, review)
    except ContrastError:
        raise
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
        raise ContrastError('invalid_source') from None


@dataclass(frozen=True, slots=True)
class Contrast:
    contrast_id: str
    numerator_group: str
    denominator_group: str

    def to_payload(self):
        return {'contrast_id': self.contrast_id, 'numerator_group': self.numerator_group,
                'denominator_group': self.denominator_group}


@dataclass(frozen=True, slots=True)
class ContrastDraft:
    dataset_version_id: str
    sample_design_version_id: str
    quant_family: str
    contrasts: tuple[Contrast, ...]

    def to_payload(self):
        return {'schemaVersion': 1, 'dataset_version_id': self.dataset_version_id,
                'sample_design_version_id': self.sample_design_version_id,
                'quant_family': self.quant_family,
                'contrasts': [c.to_payload() for c in self.contrasts]}


@dataclass(frozen=True, slots=True)
class GroupSummary:
    group: str
    total_columns: int
    confirmed_columns: int
    column_ordinals: tuple[int, ...]

    def to_payload(self):
        return {'group': self.group, 'total_columns': self.total_columns,
                'confirmed_columns': self.confirmed_columns, 'column_ordinals': list(self.column_ordinals)}


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    column_ordinals: tuple[int, ...] = ()
    contrast_ids: tuple[str, ...] = ()

    def to_payload(self):
        return {'code': self.code, 'column_ordinals': list(self.column_ordinals),
                'contrast_ids': list(self.contrast_ids)}


@dataclass(frozen=True, slots=True)
class Review:
    state: str
    binding: SourceBinding | None
    draft: ContrastDraft | None
    groups: tuple[GroupSummary, ...]
    issues: tuple[Issue, ...]

    @property
    def analysis_ready(self):
        return False

    def to_payload(self):
        return {'state': self.state, 'source_binding': None if self.binding is None else self.binding.to_payload(),
                'draft': None if self.draft is None else self.draft.to_payload(),
                'groups': [g.to_payload() for g in self.groups],
                'issues': [i.to_payload() for i in self.issues], 'direction': dict(DIRECTION), 'analysis_ready': False}


def _groups(source):
    groups = {}
    for row in source.sample_review.design.rows:
        if row.group.strip(sd.TRIM):
            groups.setdefault(row.group, []).append(row)
    return groups


def _source_issues(source):
    return [Issue(b.code, b.column_ordinals) for b in source.sample_review.confirmation_blockers]


def _choice(value, available):
    require(type(value) is str, 'invalid_group_choice')
    try: value.encode('utf-8')
    except UnicodeError: raise ContrastError('invalid_group_choice') from None
    require(len(value) <= 80 and not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value), 'invalid_group_choice')
    if value != '':
        require(bool(value.strip(sd.TRIM)) and value in available, 'unknown_group_choice')
    return value


def review_draft(payload, source):
    """Return review for a valid draft, raise fixed diagnostics for invalid input."""
    require(type(source) is BoundSource, 'invalid_source')
    require(type(payload) is dict and set(payload) == PAYLOAD_KEYS, 'invalid_payload_keys')
    require(sd._json_integer(payload['schemaVersion']) == 1, 'invalid_schema_version')
    binding = source.binding
    require(sd._canonical_uuid(payload['dataset_version_id'])
            and payload['dataset_version_id'] == binding.dataset_version_id, 'dataset_version_mismatch')
    require(sd._canonical_uuid(payload['sample_design_version_id'])
            and payload['sample_design_version_id'] == binding.sample_design_version_id, 'sample_version_mismatch')
    require(type(payload['quant_family']) is str and payload['quant_family'] == binding.quant_family, 'quant_family_mismatch')
    values = payload['contrasts']
    require(type(values) is list and len(values) <= MAX_CONTRASTS, 'invalid_contrast_list')
    groups = _groups(source)
    ids, pairs, contrasts = set(), set(), []
    for item in values:
        require(type(item) is dict and set(item) == CONTRAST_KEYS, 'invalid_contrast_keys')
        cid = item['contrast_id']
        require(sd._canonical_uuid(cid), 'invalid_contrast_id')
        require(cid not in ids, 'duplicate_contrast_id')
        num = _choice(item['numerator_group'], groups)
        den = _choice(item['denominator_group'], groups)
        if num != '' and den != '':
            require(num != den, 'same_group')
            require((num, den) not in pairs, 'duplicate_ordered_pair')
            pairs.add((num, den))
        ids.add(cid)
        contrasts.append(Contrast(cid, num, den))
    draft = ContrastDraft(binding.dataset_version_id, binding.sample_design_version_id,
                          binding.quant_family, tuple(contrasts))
    summaries = tuple(GroupSummary(name, len(rows), sum(r.confirmed for r in rows),
                                   tuple(r.column_ordinal for r in rows)) for name, rows in groups.items())
    issues = _source_issues(source)
    incomplete = tuple(c.contrast_id for c in contrasts if c.numerator_group == '' or c.denominator_group == '')
    if not contrasts: issues.append(Issue('contrasts_empty'))
    if incomplete: issues.append(Issue('contrasts_incomplete', contrast_ids=incomplete))
    included = {g for c in contrasts for g in (c.numerator_group, c.denominator_group) if g != ''}
    for name, rows in groups.items():
        if name not in included: continue
        ordinals = tuple(r.column_ordinal for r in rows)
        controls = {r.control for r in rows}
        if len(controls) > 1: issues.append(Issue('included_group_control_inconsistent', ordinals))
        elif 'unknown' in controls: issues.append(Issue('included_group_control_unknown', ordinals))
        types = {r.replicate_type for r in rows}
        if len(types) > 1: issues.append(Issue('mixed_replicate_types', ordinals))
        elif types == {'technical'}: issues.append(Issue('technical_replicates_require_policy', ordinals))
        elif types == {'unknown'}: issues.append(Issue('unknown_replicate_type', ordinals))
        labels = [r.replicate for r in rows]
        if any(not label.strip(sd.TRIM) for label in labels): issues.append(Issue('replicate_label_missing', ordinals))
        if any(count > 1 for count in Counter(labels).values()): issues.append(Issue('duplicate_replicate_labels', ordinals))
    state = 'draft_incomplete' if not contrasts or incomplete else ('blocked_by_sample_design' if issues else 'ready_for_method_selection')
    return Review(state, binding, draft, summaries, tuple(issues))


class ReviewSession:
    """Pure in-memory lifecycle: every source bind clears old draft/result first.

    This demonstrates invalidation only; it is not persistence or an API session.
    A wire payload has no project field, so server-owned project context remains
    the caller's responsibility. Binding fingerprints also cover project/metadata.
    """
    def __init__(self):
        self._source = self._result = self._error = None
        self._disposed = False

    def bind_source(self, value, *, expected_project_id=None):
        require(not self._disposed, 'disposed')
        self._source = self._result = self._error = None
        if value is not None:
            try: self._source = bind_source(value, expected_project_id=expected_project_id)
            except ContrastError as error:
                self._error = error.code
                raise
        return self.view()

    def review(self, payload):
        require(not self._disposed, 'disposed')
        self._result = None
        if self._source is None:
            return self.view()
        self._error = None
        try: self._result = review_draft(payload, self._source)
        except ContrastError as error:
            self._error = error.code
            raise
        return self._result

    def view(self):
        if self._disposed: return Review('disposed', None, None, (), ())
        if self._error:
            groups, issues = (), [Issue(self._error)]
            if self._source is not None:
                groups = tuple(GroupSummary(name, len(rows), sum(r.confirmed for r in rows),
                                            tuple(r.column_ordinal for r in rows))
                               for name, rows in _groups(self._source).items())
                issues.extend(_source_issues(self._source))
            return Review('invalid', None if self._source is None else self._source.binding,
                          None, groups, tuple(issues))
        if self._source is None: return Review('waiting_source', None, None, (), ())
        if self._result is not None: return self._result
        b = self._source.binding
        return review_draft({'schemaVersion': 1, 'dataset_version_id': b.dataset_version_id,
                             'sample_design_version_id': b.sample_design_version_id,
                             'quant_family': b.quant_family, 'contrasts': []}, self._source)

    def export_draft(self):
        if self._disposed or self._result is None: return None
        return self._result.draft.to_payload()

    def dispose(self):
        self._source = self._result = self._error = None
        self._disposed = True
