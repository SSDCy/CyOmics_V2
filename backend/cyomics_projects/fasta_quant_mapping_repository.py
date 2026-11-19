"""Immutable private association, original Owner/transactions and source parsers.
No activation, filesystem inputs, alternate credentials, migration or native work.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from uuid import UUID, uuid5
from . import fasta_quant_mapping as core, fasta_intake_contract as fasta
from . import import_persistence as ip
from .import_repository import ImportRepository
from .fasta_private_repository import FastaPrivateRepository
from .protein_groups_parser import parse_protein_groups, ECMASCRIPT_TRIM
from .settings import Fault
from .upload_repository import UploadRepository
from .validation import canonical_uuid

INPUT_KEYS=('dataset_version_id','fasta_source_id','fasta_index_version_id')
SUMMARY_KEYS=('complete','partial','unmatched','empty','row_count','member_occurrences',
    'matched_member_occurrences','unmatched_member_occurrences','duplicate_id_member_occurrences',
    'invalid_canonical_match_occurrences','rows_with_duplicate_fasta_id','rows_with_invalid_canonical_match',
    'unique_member_tokens','matched_unique_member_tokens','unmatched_unique_member_tokens','match_record_references')
COLUMNS='mapping_version_id,project_id,created_by,dataset_version_id,fasta_source_id,fasta_index_version_id,algorithm,binding_sha256,metadata_json,metadata_sha256,result_json,result_bytes,result_sha256,created_at'
TABLE='cyomics_app.fasta_quant_mapping_versions'
READ='SELECT '+COLUMNS+' FROM '+TABLE+' WHERE project_id=%s AND mapping_version_id=%s'
LOOKUP='SELECT '+COLUMNS+' FROM '+TABLE+' WHERE project_id=%s AND dataset_version_id=%s AND fasta_source_id=%s AND fasta_index_version_id=%s AND algorithm=%s'


def need(ok,code='fasta_mapping_persisted_invalid'):
    if not ok:raise Fault(503,code)


def inputs(value):
    if type(value) is not dict or set(value)!=set(INPUT_KEYS) or any(not canonical_uuid(v) for v in value.values()):
        raise Fault(400,'invalid_fasta_mapping_request')
    return value


def stamp(value):
    need(type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None)
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')


class Phase:
    """Real-clock original CPU guard, not a simulated timeout or enlarged limit."""
    def __init__(self,budget):self.guard=fasta._Budget(fasta.ParseControl(checkpoint=budget.check))
    def check(self):
        try:self.guard.check()
        except fasta.IntakeError as e:
            if e.code=='fasta_intake_timeout':raise Fault(504,'fasta_mapping_timeout') from None
            raise
    def cancelled(self):self.check();return False


def validated(raw,budget):
    """Full saved JSON coherence, all rows/members/evidence; no source reparse."""
    phase=Phase(budget);phase.check()
    try:
        need(type(raw) is bytes and 1<=len(raw)<=core.MAX_OUTPUT_BYTES)
        doc=json.loads(raw)
        need(type(doc) is dict and set(doc)=={'schemaVersion','algorithm','mapping_version_id','binding_sha256','inputs','summary','rows','fasta_records','annotation_ready','owner_authorized','published'})
        need(type(doc['schemaVersion']) is int and doc['schemaVersion']==1 and doc['algorithm']==core.ALGORITHM
             and all(doc[k] is False for k in ('annotation_ready','owner_authorized','published')))
        bound=doc['inputs'];need(type(bound) is dict and set(bound)=={'algorithm','quantification','fasta'} and bound['algorithm']==core.ALGORITHM)
        q=bound['quantification'];f=bound['fasta']
        need(type(q) is dict and set(q)==set(core.QuantVersionBinding.__dataclass_fields__))
        need(type(f) is dict and set(f)=={'project_id','source_id','index_version_id','source_bytes','source_sha256','index_contract','config_sha256','index_manifest_sha256'})
        for v in (q['project_id'],q['dataset_version_id'],q['raw_file_id'],f['project_id'],f['source_id'],f['index_version_id']):need(canonical_uuid(v))
        for v in (q['raw_sha256'],q['config_sha256'],q['report_sha256'],f['source_sha256'],f['config_sha256'],f['index_manifest_sha256']):core._sha(v)
        need(q['project_id']==f['project_id'] and q['parser_contract']==ip.CONTRACT_VERSION and f['index_contract']==fasta.INDEX_CONTRACT)
        need(q['raw_version_key']=='raw_bytea_v1:'+q['raw_file_id']+':'+q['raw_sha256'])
        for value,lo,hi in ((q['raw_bytes'],1,26214400),(f['source_bytes'],1,26214400),(q['row_count'],0,10000),(q['column_count'],2,512),(q['member_occurrences'],0,100000)):
            need(type(value) is int and lo<=value<=hi)
        need(q['row_count']*q['column_count']<=2000000)
        binding=hashlib.sha256(core._json(bound)).hexdigest()
        need(doc['binding_sha256']==binding and doc['mapping_version_id']==str(uuid5(core.NAMESPACE,core.ALGORITHM+':'+binding)))
        summary=doc['summary'];rows=doc['rows'];catalog=doc['fasta_records']
        need(type(summary) is dict and set(summary)==set(SUMMARY_KEYS) and all(type(v) is int and v>=0 for v in summary.values()))
        need(type(rows) is list and len(rows)==q['row_count'] and type(catalog) is dict and len(catalog)<=50000)
        counts=dict.fromkeys(SUMMARY_KEYS,0);tokens=set();matched_tokens=set();used=set()
        for key,record in catalog.items():
            phase.check()
            need(type(key) is str and key.isascii() and key.isdecimal() and str(int(key))==key and 1<=int(key)<=50000)
            need(type(record) is dict and set(record)=={'record_ordinal','fasta_id','raw_sequence_sha256','canonical_sequence_sha256','normalization_status','canonical_issue'})
            need(type(record['record_ordinal']) is int and record['record_ordinal']==int(key) and type(record['fasta_id']) is str and 1<=len(record['fasta_id'])<=2000000)
            core._sha(record['raw_sequence_sha256'])
            if record['canonical_sequence_sha256'] is not None:core._sha(record['canonical_sequence_sha256'])
            need(record['normalization_status']==('valid_canonical_residues' if record['canonical_sequence_sha256'] is not None else 'invalid_no_canonical_sequence_identity'))
            need(record['canonical_issue'] is None if record['canonical_sequence_sha256'] is not None else record['canonical_issue'] in ('empty_sequence','sequence_whitespace','stop_or_gap','illegal_residue'))
        for n,row in enumerate(rows,1):
            phase.check()
            need(type(row) is dict and set(row)=={'record_number','protein_ids_raw','segment_count','members','status','has_duplicate_fasta_id','has_invalid_canonical_match','annotation_ready'})
            need(type(row['record_number']) is int and row['record_number']==n and type(row['protein_ids_raw']) is str and row['annotation_ready'] is False)
            expected=[]
            for ordinal,segment in enumerate(row['protein_ids_raw'].split(';')):
                phase.check();token=segment.strip(ECMASCRIPT_TRIM)
                if token:expected.append((ordinal,token))
            need(type(row['segment_count']) is int and row['segment_count']==row['protein_ids_raw'].count(';')+1 and type(row['members']) is list and len(row['members'])==len(expected))
            matched=0;duplicate=invalid=False
            for member,(ordinal,token) in zip(row['members'],expected):
                phase.check()
                need(type(member) is dict and set(member)=={'segment_ordinal','token','fasta_record_ordinals','duplicate_fasta_id','valid_canonical_match_count','invalid_canonical_match_count'})
                need(type(member['segment_ordinal']) is int and member['segment_ordinal']==ordinal and member['token']==token)
                hits=member['fasta_record_ordinals'];need(type(hits) is list and all(type(h) is int and 1<=h<=50000 for h in hits) and hits==sorted(set(hits)))
                good=0
                for hit in hits:
                    phase.check();key=str(hit);need(key in catalog and catalog[key]['fasta_id']==token);used.add(key)
                    good+=catalog[key]['canonical_sequence_sha256'] is not None
                bad=len(hits)-good;dup=len(hits)>1
                need(type(member['duplicate_fasta_id']) is bool and member['duplicate_fasta_id']==dup)
                need(type(member['valid_canonical_match_count']) is int and member['valid_canonical_match_count']==good and type(member['invalid_canonical_match_count']) is int and member['invalid_canonical_match_count']==bad)
                tokens.add(token)
                if hits:matched+=1;matched_tokens.add(token)
                duplicate|=dup;invalid|=bad>0
                counts['duplicate_id_member_occurrences']+=dup;counts['invalid_canonical_match_occurrences']+=bad;counts['match_record_references']+=len(hits)
                need(counts['match_record_references']<=core.MAX_MATCH_REFERENCES)
            state='empty' if not expected else 'complete' if matched==len(expected) else 'partial' if matched else 'unmatched'
            need(row['status']==state and type(row['has_duplicate_fasta_id']) is bool and row['has_duplicate_fasta_id']==duplicate and type(row['has_invalid_canonical_match']) is bool and row['has_invalid_canonical_match']==invalid)
            counts[state]+=1;counts['row_count']+=1;counts['member_occurrences']+=len(expected);counts['matched_member_occurrences']+=matched;counts['unmatched_member_occurrences']+=len(expected)-matched
            counts['rows_with_duplicate_fasta_id']+=duplicate;counts['rows_with_invalid_canonical_match']+=invalid
        counts.update(unique_member_tokens=len(tokens),matched_unique_member_tokens=len(matched_tokens),unmatched_unique_member_tokens=len(tokens-matched_tokens))
        need(counts==summary and counts['member_occurrences']==q['member_occurrences'] and used==set(catalog))
        need(core._json(doc)==raw);phase.check();return doc
    except Fault:raise
    except (ValueError,TypeError,KeyError,OverflowError,RecursionError):raise Fault(503,'fasta_mapping_persisted_invalid') from None


@dataclass(frozen=True,slots=True,repr=False)
class SavedMapping:
    metadata: dict
    content: bytes


class FastaQuantMappingRepository(UploadRepository):
    def __init__(self,pool=None):
        super().__init__(pool);self.imports=ImportRepository(pool);self.fasta=FastaPrivateRepository(pool)

    def _check(self,actor,project,budget):
        if self.pool is None:raise Fault(503,'fasta_mapping_unavailable')
        if not canonical_uuid(actor):raise Fault(401,'unauthenticated')
        if not canonical_uuid(project):raise Fault(400,'invalid_fasta_mapping_request')
        budget.check()

    def _decode(self,row,project,budget):
        try:
            need(type(row) in (tuple,list) and len(row)==14)
            for value in row[:6]:need(type(value) is UUID)
            mid,pid,actor,did,sid,iid,algorithm,binding,metadata_text,metadata_sha,raw,size,sha,created=row
            need(str(pid)==project and algorithm==core.ALGORITHM and isinstance(raw,(bytes,memoryview,bytearray)))
            raw=bytes(raw);need(type(size) is int and len(raw)==size and hashlib.sha256(raw).hexdigest()==sha)
            doc=validated(raw,budget)
            need(doc['mapping_version_id']==str(mid) and doc['binding_sha256']==binding)
            bound=doc['inputs'];need(bound['quantification']['dataset_version_id']==str(did) and bound['fasta']['source_id']==str(sid) and bound['fasta']['index_version_id']==str(iid) and bound['quantification']['project_id']==project)
            expected=self._metadata(doc,str(actor),stamp(created),raw,sha)
            need(type(metadata_text) is str and len(metadata_text.encode('ascii'))<=65536 and core._json(expected).decode()==metadata_text and hashlib.sha256(metadata_text.encode()).hexdigest()==metadata_sha)
            return SavedMapping(expected,raw)
        except Fault:raise
        except (ValueError,TypeError,KeyError,UnicodeError):raise Fault(503,'fasta_mapping_persisted_invalid') from None

    def _metadata(self,doc,actor,created,raw,sha):
        return dict(schemaVersion=1,contract='fasta_quant_mapping_saved_v0.1',mapping_version_id=doc['mapping_version_id'],project_id=doc['inputs']['quantification']['project_id'],created_by=actor,algorithm=core.ALGORITHM,binding_sha256=doc['binding_sha256'],inputs=doc['inputs'],summary=doc['summary'],result=dict(name='mapping.json',bytes=len(raw),sha256=sha,media_type='application/json'),status='stored',createdAt=created)

    async def _lookup(self,q,project,values,budget):
        row=await q(LOOKUP,(UUID(project),*(UUID(values[k]) for k in INPUT_KEYS),core.ALGORITHM))
        return self._decode(row,project,budget) if row is not None else None

    async def lookup(self,actor,project,values,budget):
        self._check(actor,project,budget);inputs(values)
        async def operation(q):
            await self.project(q,actor,project);return await self._lookup(q,project,values,budget)
        return await self.transaction(actor,budget,operation)

    async def read(self,actor,project,identifier,budget):
        self._check(actor,project,budget)
        if not canonical_uuid(identifier):raise Fault(400,'invalid_fasta_mapping_request')
        async def operation(q):
            await self.project(q,actor,project)
            row=await q(READ,(UUID(project),UUID(identifier)))
            if row is None:raise Fault(404,'fasta_mapping_not_found')
            result=self._decode(row,project,budget);need(result.metadata['mapping_version_id']==identifier);return result
        return await self.transaction(actor,budget,operation)

    async def _quant(self,actor,project,dataset,budget):
        async def locate(q):
            await self.project(q,actor,project)
            row=await q('SELECT file_id FROM cyomics_app.dataset_versions WHERE project_id=%s AND dataset_version_id=%s',(UUID(project),UUID(dataset)))
            if row is None:raise Fault(404,'fasta_mapping_input_not_found')
            need(len(row)==1 and type(row[0]) is UUID,'fasta_mapping_source_integrity_failed');return str(row[0])
        file_id=await self.transaction(actor,budget,locate)
        saved=await self.imports.read_version(actor,project,file_id,dataset,budget)
        raw=await self.imports.load_raw(actor,project,file_id,budget)
        phase=Phase(budget);phase.check();source=ip.trusted_source(raw,project,file_id,phase.check)
        ip.verify_parser_release(phase.check)
        version=saved.decoded['datasetVersion'];config=ip.canonical_json(version['configuration'],ip.CONFIG_MAX_BYTES,phase.check)
        limits=ip.configuration_limits(config)
        outcome=parse_protein_groups(source,(raw.content[i:i+65536] for i in range(0,raw.bytes,65536)),limits=limits,cancelled=phase.cancelled)
        phase.check();need(outcome.ok,'fasta_mapping_source_integrity_failed');parsed=outcome.parsed
        report=asdict(outcome.report);report.update(dataset_version_id=dataset,persisted=True)
        columns=[asdict(c) for c in parsed.columns];spans=[]
        for row in parsed.rows:
            phase.check();spans.append(dict(record_number=row.record_number,**asdict(row.source_span)))
        warnings=[dict(code=code,count=report[field]) for field,code in (('empty_group_count','EMPTY_PROTEIN_GROUPS'),('quant_empty_string_cells','QUANT_EMPTY_STRING_CELLS')) if report[field]]
        document=dict(schemaVersion=1,representation='raw_backed',parsed_table_materialized=False,report=report,columns=columns,sample_columns=[c for c in columns if c['quant_family'] is not None],header_span=asdict(parsed.header_span),row_source_spans=spans,warnings=warnings)
        # Full canonical comparison normalizes immutable JSON tuple/list representation only.
        need(ip.canonical_json(document,ip.REPORT_MAX_BYTES,phase.check)==ip.canonical_json(saved.decoded['importReport'],ip.REPORT_MAX_BYTES,phase.check),'fasta_mapping_source_integrity_failed')
        binding=core.QuantVersionBinding(project,dataset,file_id,source.raw_version_key,raw.bytes,raw.sha256,ip.CONTRACT_VERSION,version['config_sha256'],saved.report_sha256,report['row_count'],report['column_count'],report['member_occurrences'])
        phase.check();return parsed,binding

    async def save(self,actor,project,values,budget):
        self._check(actor,project,budget);inputs(values)
        existing=await self.lookup(actor,project,values,budget)
        if existing is not None:return 200,existing
        parsed,binding=await self._quant(actor,project,values['dataset_version_id'],budget)
        verified=(await self.fasta.read_verified_source(actor,project,values['fasta_source_id'],budget,config_json=fasta.CONFIG_JSON)).verified
        if verified.candidate.index_version_id!=values['fasta_index_version_id']:raise Fault(404,'fasta_mapping_input_not_found')
        result=core.map_quant_to_fasta(parsed,verified,binding,control=fasta.ParseControl(checkpoint=budget.check))
        # Release large raw/index inputs before persistence validation.
        del parsed,verified
        doc=validated(result.document,budget);now=datetime.now(timezone.utc)
        metadata=self._metadata(doc,actor,stamp(now),result.document,result.sha256);text=core._json(metadata).decode('ascii');need(len(text)<=65536)
        row=(UUID(result.mapping_version_id),UUID(project),UUID(actor),*(UUID(values[k]) for k in INPUT_KEYS),core.ALGORITHM,result.binding_sha256,text,hashlib.sha256(text.encode()).hexdigest(),result.document,len(result.document),result.sha256,now)
        async def operation(q):
            await self.project(q,actor,project)
            lock=int.from_bytes(hashlib.sha256(('cyomics:fasta-quant-mapping:v1:'+actor).encode()).digest()[:8],'big',signed=True)
            if not (await q('SELECT pg_catalog.pg_try_advisory_xact_lock(%s)',(lock,)))[0]:raise Fault(429,'fasta_mapping_busy')
            prior=await self._lookup(q,project,values,budget)
            if prior is not None:return 200,prior
            counts=await q('SELECT count(*),COALESCE(sum(m.result_bytes),0),count(*) FILTER (WHERE m.project_id=%s),COALESCE(sum(m.result_bytes) FILTER (WHERE m.project_id=%s),0) FROM '+TABLE+' m JOIN cyomics_app.projects p ON p.project_id=m.project_id WHERE p.owner_id=%s',(UUID(project),UUID(project),UUID(actor)))
            need(type(counts) in (tuple,list) and len(counts)==4 and all(type(v) is int and v>=0 for v in counts))
            if counts[0]>=16 or counts[1]+len(result.document)>268435456 or counts[2]>=8 or counts[3]+len(result.document)>134217728:raise Fault(413,'fasta_mapping_quota_exceeded')
            returned=await q('INSERT INTO '+TABLE+' ('+COLUMNS+') VALUES ('+','.join(['%s']*14)+') RETURNING '+COLUMNS,row)
            saved=self._decode(returned,project,budget)
            need(saved.content==result.document and saved.metadata==metadata);return 201,saved
        return await self.transaction(actor,budget,operation,writing=True)
