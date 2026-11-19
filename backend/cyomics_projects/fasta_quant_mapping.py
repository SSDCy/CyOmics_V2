"""Complete exact-ID group/FASTA association, no I/O, auth or publication.

Inputs are internal original parser/model results plus saved-version provenance.
None of these types is authority. A caller validates persisted source bytes and
report metadata before constructing QuantVersionBinding; no client DTO shortcut.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import re
from uuid import UUID, uuid5
from . import fasta_intake_contract as fasta
from .import_report import SourceRef, CONTRACT_VERSION
from .protein_groups_parser import (ParsedProteinGroups, ProteinGroupRow, Member,
    Column, SourceSpan, ECMASCRIPT_TRIM)

ALGORITHM = 'protein_groups_exact_fasta_members_v1'
NAMESPACE = UUID('d9bfaa48-f76c-50f8-bab8-771567941727')
MAX_MATCH_REFERENCES = 2000000
MAX_OUTPUT_BYTES = 33554432


class MappingError(ValueError):
    pass


def _need(ok, code='mapping_input_invalid'):
    if not ok:
        raise MappingError(code)


def _uuid(value):
    _need(type(value) is str)
    try:valid = str(UUID(value)) == value
    except ValueError:valid = False
    _need(valid)


def _sha(value):
    _need(type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None)


def _json(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,
        allow_nan=False).encode('ascii')


@dataclass(frozen=True, slots=True)
class QuantVersionBinding:
    project_id: str
    dataset_version_id: str
    raw_file_id: str
    raw_version_key: str
    raw_bytes: int
    raw_sha256: str
    parser_contract: str
    config_sha256: str
    report_sha256: str
    row_count: int
    column_count: int
    member_occurrences: int


@dataclass(frozen=True, slots=True, repr=False)
class MappingResult:
    mapping_version_id: str
    binding_sha256: str
    sha256: str
    document: bytes

    def snapshot(self):
        return json.loads(self.document)


def map_quant_to_fasta(parsed, verified, binding, *, control=None):
    """Return a complete bounded association or fail; never a partial preview.

    Member hit ordinals join the complete fasta_records evidence dictionary.
    Mapping checkpoints use the unchanged real-clock <=8 CPU ParseControl.
    No canonical record or duplicate ID is filtered out of ID-match counts.
    """
    budget = fasta._Budget(control)
    budget.check()
    _need(type(parsed) is ParsedProteinGroups and type(binding) is QuantVersionBinding
        and type(verified) is fasta.VerifiedFastaIndex)
    for value in (binding.project_id,binding.dataset_version_id,binding.raw_file_id):
        _uuid(value)
    for value in (binding.raw_sha256,binding.config_sha256,binding.report_sha256):
        _sha(value)
    _need(type(binding.raw_version_key) is str and 1<=len(binding.raw_version_key)<=256
        and binding.parser_contract==CONTRACT_VERSION)
    _need(type(binding.raw_bytes) is int and 1<=binding.raw_bytes<=26214400
        and type(binding.row_count) is int and 0<=binding.row_count<=10000
        and type(binding.column_count) is int and 2<=binding.column_count<=512
        and binding.row_count*binding.column_count<=2000000
        and type(binding.member_occurrences) is int and 0<=binding.member_occurrences<=100000)
    source = parsed.source
    _need(type(source) is SourceRef and source.project_id==binding.project_id
        and source.raw_file_id==binding.raw_file_id and source.raw_version_key==binding.raw_version_key
        and source.expected_bytes==parsed.raw_bytes==binding.raw_bytes
        and source.expected_raw_sha256==parsed.raw_sha256==binding.raw_sha256)
    _need(type(parsed.rows) is tuple and len(parsed.rows)==binding.row_count
        and type(parsed.columns) is tuple and len(parsed.columns)==binding.column_count,
        'mapping_incomplete_quantification')
    _need(all(type(c) is Column and c.ordinal==n and type(c.name_raw) is str
        for n,c in enumerate(parsed.columns)))
    protein_columns = [c.ordinal for c in parsed.columns if c.name_raw=='Protein IDs']
    _need(len(protein_columns)==1)
    protein_column = protein_columns[0]
    candidate = fasta.decode_candidate(fasta.candidate_snapshot(verified.candidate))
    _need(candidate.project_id==binding.project_id,'mapping_project_mismatch')
    index = verified.index
    # Re-prove the complete typed index against its persisted all-record manifest.
    # No source reparse and no separate/extended CPU budget is introduced here.
    fasta._validate_index(index,budget)
    _need(index.source_bytes==candidate.source_bytes and index.source_sha256==candidate.source_sha256
        and fasta._fingerprint(index,budget)==candidate.index_manifest_sha256,
        'mapping_fasta_integrity_failed')
    fasta_binding = {k:getattr(candidate,k) for k in ('project_id','source_id','index_version_id',
        'source_bytes','source_sha256','index_contract','config_sha256','index_manifest_sha256')}
    inputs = {'algorithm':ALGORITHM,'quantification':asdict(binding),'fasta':fasta_binding}
    binding_sha = hashlib.sha256(_json(inputs)).hexdigest()
    identifier = str(uuid5(NAMESPACE,ALGORITHM+':'+binding_sha))
    summary = dict.fromkeys(('complete','partial','unmatched','empty','row_count',
        'member_occurrences','matched_member_occurrences','unmatched_member_occurrences',
        'duplicate_id_member_occurrences','invalid_canonical_match_occurrences',
        'rows_with_duplicate_fasta_id','rows_with_invalid_canonical_match'),0)
    rows = []; evidence = {}; tokens = set(); matched_tokens = set(); references = 0
    previous_end = parsed.header_span.end_byte if type(parsed.header_span) is SourceSpan else -1
    _need(0<=previous_end<=binding.raw_bytes)
    for number,row in enumerate(parsed.rows,1):
        budget.check()
        _need(type(row) is ProteinGroupRow and row.record_number==number
            and type(row.cells) is tuple and len(row.cells)==binding.column_count
            and type(row.protein_ids_raw) is str and row.cells[protein_column]==row.protein_ids_raw
            and type(row.members) is tuple and type(row.source_span) is SourceSpan
            and previous_end<=row.source_span.start_byte<=row.source_span.end_byte<=binding.raw_bytes)
        previous_end = row.source_span.end_byte
        # Reuse the original parser's exact semicolon/ECMAScript trimming rule.
        expected = []
        for ordinal,segment in enumerate(row.protein_ids_raw.split(';')):
            if ordinal%1024==0:budget.check()
            token = segment.strip(ECMASCRIPT_TRIM)
            if token:expected.append((ordinal,token))
        _need(all(type(m) is Member for m in row.members)
            and [(m.segment_ordinal,m.token) for m in row.members]==expected)
        members = []; matched = 0; duplicate = invalid = False
        for member in row.members:
            budget.check()
            tokens.add(member.token)
            ordinals = index.id_ordinals.get(member.token,())
            references += len(ordinals)
            _need(references<=MAX_MATCH_REFERENCES,'mapping_match_reference_limit')
            valid_count = 0
            for ordinal in ordinals:
                budget.check()
                record = index.records[ordinal-1]
                valid_count += record.canonical_sequence is not None
                evidence[str(ordinal)] = dict(record_ordinal=ordinal,fasta_id=record.id,
                    raw_sequence_sha256=record.raw_sequence_sha256,
                    canonical_sequence_sha256=record.sequence_sha256,
                    normalization_status=record.normalization_status,canonical_issue=record.canonical_issue)
            invalid_count = len(ordinals)-valid_count
            if ordinals:matched+=1;matched_tokens.add(member.token)
            duplicate |= len(ordinals)>1
            invalid |= invalid_count>0
            summary['duplicate_id_member_occurrences'] += len(ordinals)>1
            summary['invalid_canonical_match_occurrences'] += invalid_count
            members.append(dict(segment_ordinal=member.segment_ordinal,token=member.token,
                fasta_record_ordinals=ordinals,duplicate_fasta_id=len(ordinals)>1,
                valid_canonical_match_count=valid_count,invalid_canonical_match_count=invalid_count))
        state = 'empty' if not members else 'complete' if matched==len(members) else 'partial' if matched else 'unmatched'
        summary[state]+=1;summary['row_count']+=1;summary['member_occurrences']+=len(members)
        summary['matched_member_occurrences']+=matched
        summary['unmatched_member_occurrences']+=len(members)-matched
        summary['rows_with_duplicate_fasta_id']+=duplicate
        summary['rows_with_invalid_canonical_match']+=invalid
        rows.append(dict(record_number=number,protein_ids_raw=row.protein_ids_raw,
            segment_count=row.protein_ids_raw.count(';')+1,members=members,status=state,
            has_duplicate_fasta_id=duplicate,has_invalid_canonical_match=invalid,
            annotation_ready=False))
    _need(summary['member_occurrences']==binding.member_occurrences,'mapping_incomplete_quantification')
    summary.update(unique_member_tokens=len(tokens),matched_unique_member_tokens=len(matched_tokens),
        unmatched_unique_member_tokens=len(tokens-matched_tokens),match_record_references=references)
    budget.check()
    document = _json(dict(schemaVersion=1,algorithm=ALGORITHM,mapping_version_id=identifier,
        binding_sha256=binding_sha,inputs=inputs,summary=summary,rows=rows,fasta_records=evidence,
        annotation_ready=False,owner_authorized=False,published=False))
    _need(len(document)<=MAX_OUTPUT_BYTES,'mapping_output_byte_limit')
    budget.check()
    return MappingResult(identifier,binding_sha,hashlib.sha256(document).hexdigest(),document)
