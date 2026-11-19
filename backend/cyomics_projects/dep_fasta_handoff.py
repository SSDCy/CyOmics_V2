"""Default-off Owner DEP-to-FASTA read assembly. No routes, writes or engines.

Caller supplies the original application and an Authority-verified subject (or
explicitly reviewed operator subject). IDs, this config and parsed types are not
Auth. Borrowed repositories/pool remain owned by the original app. The helper
never reads credentials, enables gates, changes stored records or runs a tool.
"""
from dataclasses import dataclass
import csv,hashlib,io,json,re
from uuid import UUID
from .settings import Fault
from . import fasta_intake_contract as fasta

CONTRACT='dep_fasta_handoff_v0.1'
POLICY='preserve_all_member_record_occurrences_no_sequence_dedup_v1'
MAX_OUTPUT=32*1024*1024
MAX_QUERY=fasta.MAX_SOURCE
MAX_ROWS=10000
MAX_LINKS=100000
HEADER=tuple('source_row_ordinal protein_ids_raw contrast_id test_status reason log2FC FC FC_reason AveExpr moderated_t df_residual df_total P.Value adj.P.Val n_obs_num n_obs_den n_fit_num n_fit_den classification selected_DE'.split())
ROWMAP=('source_record_number','protein_ids_raw','kept','work_row_ordinal')
EXTRA=('raw_record_number','segment_ordinal','member_token','fasta_record_ordinal','fasta_id','fasta_header_raw','raw_sequence_sha256','canonical_sequence_sha256','sequence_length','sequence_status','query_id','project_id','derived_version_id','mapping_version_id','fasta_source_id','fasta_index_version_id')

def need(ok,code='dep_fasta_binding_invalid',status=503):
    if not ok:raise Fault(status,code)
def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')
def sha(b):return hashlib.sha256(b).hexdigest()
def pin(b):return {'bytes':len(b),'sha256':sha(b)}
def uid(v):
    return type(v) is str and re.fullmatch('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',v) is not None and str(UUID(v))==v

def checked(raw,expected,cap):
    need(type(raw) is bytes and 0<=len(raw)<=cap and pin(raw)=={k:expected[k] for k in ('bytes','sha256')})
    return raw

def table(raw,header,limit,cap):
    need(type(raw) is bytes and 0<len(raw)<=cap and raw.endswith(b'\n') and not raw.startswith(b'\xef\xbb\xbf') and b'\r' not in raw and b'\0' not in raw)
    try:rows=list(csv.reader(io.StringIO(raw.decode('utf8'),newline=''),delimiter='\t',strict=True))
    except (ValueError,csv.Error,UnicodeError):raise Fault(503,'dep_fasta_binding_invalid') from None
    need(rows and tuple(rows[0])==header and len(rows)-1<=limit and all(len(r)==len(header) for r in rows))
    return rows[1:]

def artifact(meta,name):
    found=[a for a in meta['artifacts'] if a['name']==name];need(len(found)==1)
    return found[0]

@dataclass(frozen=True,slots=True)
class Config:
    enabled:bool=False

@dataclass(frozen=True,slots=True)
class Bundle:
    descriptor_json:bytes
    queries_fasta:bytes
    membership_tsv:bytes

@dataclass(frozen=True,slots=True)
class _Readers:
    classifications:object
    results:object
    processed:object
    mappings:object
    fasta:object

class OwnerFastaHandoff:
    def __init__(self,app=None,config=None):self.app=app;self.config=config
    def _readers(self):
        # Only existing original repositories and the exact shared FixedPool.
        # Imports have no transport/config/credential activation; no construction.
        from .de_classification_repository import ClassificationRepository
        from .differential_worker_once import ResultReader
        from .preprocess_repository import PreprocessRepository
        from .fasta_quant_mapping_repository import FastaQuantMappingRepository
        from .fasta_private_repository import FastaPrivateRepository
        from .repository import FixedPool
        state=self.app.state
        refs=_Readers(state.classification_results,state.differential_results,
            state.preprocessing_jobs.repository,state.fasta_mapping.repository,state.fasta_private.repository)
        types=(ClassificationRepository,ResultReader,PreprocessRepository,FastaQuantMappingRepository,FastaPrivateRepository)
        need(all(type(v) is t for v,t in zip((refs.classifications,refs.results,refs.processed,refs.mappings,refs.fasta),types)),status=503,code='dep_fasta_unavailable')
        pools=(refs.classifications.owner.pool,refs.results.owner.pool,refs.processed.pool,refs.mappings.pool,refs.fasta.pool)
        need(type(pools[0]) is FixedPool and all(p is pools[0] for p in pools),code='dep_fasta_unavailable')
        return refs

    async def assemble(self,subject,project,derived_version_id,mapping_version_id,budget):
        try:return await self._assemble(subject,project,derived_version_id,mapping_version_id,budget)
        except (KeyError,TypeError,ValueError,AttributeError,IndexError,UnicodeError,csv.Error):
            raise Fault(503,'dep_fasta_binding_invalid') from None

    async def _assemble(self,subject,project,derived_version_id,mapping_version_id,budget):
        need(type(self.config) is Config and self.config.enabled is True and self.app is not None,'dep_fasta_unavailable')
        need(uid(subject),'unauthenticated',401)
        need(all(uid(v) for v in (project,derived_version_id,mapping_version_id)),'invalid_dep_fasta_request',400)
        need(callable(getattr(budget,'check',None)),'dep_fasta_unavailable');budget.check()
        refs=self._readers()
        # All private data is reached through original fresh Owner read methods.
        meta=await refs.classifications.read_version(subject,project,derived_version_id,budget);budget.check()
        need(meta['derived_version_id']==derived_version_id and meta['created_by']==subject and meta['project_id']==project)
        need(meta['status']=='CLASSIFIED' and meta['DE_members_evaluated'] is True and meta['member_set'] is not None,'dep_fasta_not_classified',409)
        member=meta['member_set'];need(member['derived_version_id']==derived_version_id and uid(member['member_set_id']))
        frozen=await refs.classifications.read_member_set(subject,project,member['member_set_id'],budget);budget.check()
        need(frozen['member_set']==member)
        selected=checked(frozen['bytes'],artifact(meta,'selected-DE.tsv'),MAX_QUERY)
        need(sha(selected)==member['selected_artifact_sha256'])
        selected_rows=table(selected,HEADER,MAX_ROWS,MAX_QUERY)
        need(len(selected_rows)==member['member_count']==meta['counts']['selected_DE_rows'])
        need(meta['counts']['Up']+meta['counts']['Down']==len(selected_rows))
        binding=meta['source_binding'];need(binding['owner_id']==subject and binding['project_id']==project and binding['source_result_id']==meta['source_result_id'])
        parent=await refs.results.read(subject,project,budget,result_id=meta['source_result_id']);budget.check()
        need(parent['created_by']==subject and parent['project_id']==project and parent['result_id']==meta['source_result_id'])
        need(sha(canonical(parent))==binding['source_metadata_sha256'] and parent['manifest_sha256']==binding['source_manifest_sha256']==meta['source_manifest_sha256'])
        need(parent['artifacts']==binding['source_artifacts'] and parent['receipt_sha256']==binding['source_receipt_sha256'])
        need(parent['job_id']==binding['source_job_id'] and parent['attempt_id']==binding['source_attempt_id'] and parent['request_id']==binding['source_request_id'])
        need(sha(canonical(parent['method']))==binding['source_method_sha256'] and parent['admission_binding_sha256']==binding['admission_binding_sha256'])
        choice=parent['selection'];processed_id=choice['processed_dataset_version_id']
        need(uid(processed_id) and len(choice['contrast']['contrasts'])==1)
        contrast=choice['contrast']['contrasts'][0];need(contrast['contrast_id']==binding['source_contrast_id'] and binding['contrast_direction']=='numerator_minus_denominator')
        processed=await refs.processed.read_version(subject,project,processed_id,budget);budget.check()
        need(processed['processed_dataset_version_id']==processed_id and processed['created_by']==subject and processed['project_id']==project)
        need(processed['parameters']['normalization']=='none' and processed['parameters']['imputation']=='none','dep_fasta_source_unsupported',409)
        need(choice['contrast']['dataset_version_id']==processed['parent_dataset_version_id'] and choice['contrast']['quant_family']==processed['quant_family'])
        row_artifact=await refs.processed.read_artifact(subject,project,processed_id,'row-map.tsv',budget);budget.check()
        from .preprocess_repository import StoredArtifact
        expected_row_map=artifact(processed,'row-map.tsv')
        need(type(row_artifact) is StoredArtifact and row_artifact.name=='row-map.tsv'
            and row_artifact.media_type==expected_row_map['media_type']=='text/tab-separated-values; charset=utf-8'
            and row_artifact.sha256==expected_row_map['sha256'])
        row_map=checked(row_artifact.body,expected_row_map,MAX_QUERY)
        raw_rows=table(row_map,ROWMAP,MAX_ROWS,MAX_QUERY);need(len(raw_rows)==processed['source']['row_count'])
        rawmap={}
        work_ordinal=0
        for i,r in enumerate(raw_rows,1):
            budget.check();need(r[0]==str(i) and r[2] in ('TRUE','FALSE'))
            if r[2]=='TRUE':work_ordinal+=1;need(r[3]==str(work_ordinal))
            else:need(r[3]=='NA')
            rawmap[i]=r
        saved=await refs.mappings.read(subject,project,mapping_version_id,budget);budget.check()
        mapping_meta=saved.metadata;need(mapping_meta['mapping_version_id']==mapping_version_id and mapping_meta['created_by']==subject and mapping_meta['project_id']==project)
        checked(saved.content,mapping_meta['result'],MAX_OUTPUT)
        # Original MappingRepo.read has already fully validated all saved rows,
        # catalogue, natural identity and binding. No mapping computation here.
        doc=json.loads(saved.content);need(doc['mapping_version_id']==mapping_version_id and doc['inputs']==mapping_meta['inputs'] and doc['binding_sha256']==mapping_meta['binding_sha256'])
        inputs=doc['inputs'];quant=inputs['quantification'];fb=inputs['fasta'];src=processed['source']
        pairs={'dataset_version_id':processed['parent_dataset_version_id'],'project_id':project,'raw_file_id':processed['file_id'],'raw_bytes':src['source_bytes'],'raw_sha256':src['source_sha256'],'config_sha256':src['config_sha256'],'report_sha256':src['report_sha256'],'row_count':src['row_count'],'column_count':src['column_count'],'parser_contract':src['parser_contract']}
        need(all(quant[k]==v for k,v in pairs.items()) and fb['project_id']==project)
        mapping_rows={r['record_number']:r for r in doc['rows']}
        # Freeze every selected raw ordinal/whole ID BEFORE loading FASTA bytes.
        selection=[];previous=0
        for r in selected_rows:
            budget.check();need(re.fullmatch('[1-9][0-9]*',r[0]) is not None)
            ordinal=int(r[0]);need(previous<ordinal<=len(raw_rows));previous=ordinal
            rr=rawmap[ordinal];mapped=mapping_rows.get(ordinal)
            need(rr[2]=='TRUE' and mapped is not None and r[1]==rr[1]==mapped['protein_ids_raw'])
            need(r[2]==contrast['contrast_id'] and r[3]=='testable' and r[-2] in ('Up','Down') and r[-1]=='TRUE')
            selection.append((r,mapped))
        need(sum(r[-2]=='Up' for r,_ in selection)==meta['counts']['Up'] and sum(r[-2]=='Down' for r,_ in selection)==meta['counts']['Down'])
        stored=await refs.fasta.read_verified_source(subject,project,fb['source_id'],budget,config_json=fasta.CONFIG_JSON);budget.check()
        need(type(stored.verified) is fasta.VerifiedFastaIndex)
        index=stored.verified.index;candidate=stored.verified.candidate;source=stored.source
        need(candidate==stored.candidate and candidate.project_id==project and candidate.source_id==fb['source_id'] and candidate.index_version_id==fb['index_version_id'])
        need(candidate.source_bytes==source.bytes==fb['source_bytes'] and candidate.source_sha256==source.sha256==fb['source_sha256'])
        need(candidate.index_manifest_sha256==fb['index_manifest_sha256'] and candidate.config_sha256==fb['config_sha256'] and candidate.index_contract==fb['index_contract'])
        need(len(source.content)==source.bytes and sha(source.content)==source.sha256 and index.source_sha256==source.sha256)
        queries=bytearray();membership=bytearray();counts=dict(selected_groups=len(selection),member_occurrences=0,relation_rows=0,query_records=0,unmatched_members=0,empty_groups=0,invalid_record_occurrences=0,duplicate_id_members=0)
        seqhashes=set();record_ids=set()
        def emit(cells):
            buf=io.StringIO(newline='');csv.writer(buf,delimiter='\t',lineterminator='\n').writerow(cells)
            data=buf.getvalue().encode('utf8');need(counts['relation_rows']<MAX_LINKS and len(membership)+len(data)+len(queries)<=MAX_OUTPUT,'dep_fasta_too_large',413);membership.extend(data)
        emit(HEADER+EXTRA)
        for r,mapped in selection:
            members=mapped['members']
            if not members:
                counts['empty_groups']+=1
                emit(tuple(r)+(r[0],'','','','','','','','','empty_member_group','',project,derived_version_id,mapping_version_id,fb['source_id'],fb['index_version_id']))
                counts['relation_rows']+=1
            for member_record in members:
                budget.check();counts['member_occurrences']+=1
                hits=member_record['fasta_record_ordinals'];counts['duplicate_id_members']+=len(hits)>1
                if not hits:
                    counts['unmatched_members']+=1
                    emit(tuple(r)+(r[0],member_record['segment_ordinal'],member_record['token'],'','','','','','','unmatched','',project,derived_version_id,mapping_version_id,fb['source_id'],fb['index_version_id']))
                    counts['relation_rows']+=1
                for ordinal in hits:
                    budget.check();need(type(ordinal) is int and 1<=ordinal<=len(index.records))
                    record=index.records[ordinal-1];catalog=doc['fasta_records'][str(ordinal)]
                    need(record.ordinal==ordinal and record.id==member_record['token']==catalog['fasta_id'])
                    need(record.raw_sequence_sha256==catalog['raw_sequence_sha256'] and record.sequence_sha256==catalog['canonical_sequence_sha256'] and record.normalization_status==catalog['normalization_status'] and record.canonical_issue==catalog['canonical_issue'])
                    need(sha(record.sequence_raw.encode('ascii'))==record.raw_sequence_sha256)
                    query_id='';length='';state=record.canonical_issue or 'valid_canonical_residues'
                    if record.canonical_sequence is not None:
                        # No new canonicalizer; original parser rules only. No
                        # trimming '*', whitespace, gaps or changing residues.
                        from .sequence.vendor_domain_r1 import normalize_sequence
                        need(normalize_sequence(record.sequence_raw)==record.canonical_sequence and sha(record.canonical_sequence.encode('ascii'))==record.sequence_sha256)
                        query_id=f'q_r{int(r[0]):05d}_m{member_record["segment_ordinal"]:05d}_f{ordinal:05d}'
                        need(re.fullmatch(r'q_r[0-9]+_m[0-9]+_f[0-9]+',query_id) is not None)
                        seq=record.canonical_sequence;length=len(seq)
                        data=('>'+query_id+'\n'+'\n'.join(seq[i:i+60] for i in range(0,len(seq),60))+'\n').encode('ascii')
                        need(len(queries)+len(data)<=MAX_QUERY and len(membership)+len(queries)+len(data)<=MAX_OUTPUT,'dep_fasta_too_large',413)
                        queries.extend(data);counts['query_records']+=1;seqhashes.add(record.sequence_sha256);record_ids.add(ordinal)
                    else:counts['invalid_record_occurrences']+=1
                    emit(tuple(r)+(r[0],member_record['segment_ordinal'],member_record['token'],ordinal,record.id,record.header_raw,record.raw_sequence_sha256,record.sequence_sha256 or '',length,state,query_id,project,derived_version_id,mapping_version_id,fb['source_id'],fb['index_version_id']))
                    counts['relation_rows']+=1
                    need(counts['relation_rows']<=MAX_LINKS,'dep_fasta_too_large',413)
        counts.update(distinct_valid_fasta_records=len(record_ids),distinct_valid_sequence_sha256=len(seqhashes),Up=meta['counts']['Up'],Down=meta['counts']['Down'])
        budget.check();query_bytes=bytes(queries);member_bytes=bytes(membership)
        descriptor={'schemaVersion':1,'contract':CONTRACT,'status':('ready_with_unresolved' if any(counts[k] for k in ('unmatched_members','empty_groups','invalid_record_occurrences')) else 'ready') if counts['query_records'] else 'no_query_sequences','created_by':subject,'project_id':project,'dedup_policy':POLICY,'sequence_identity_contract':index.sequence_identity_contract,'species':None,'taxonomy_id':None,'annotation_ready':False,'BLAST_executed':False,
            'classification':{'derived_version_id':derived_version_id,'metadata_sha256':sha(canonical(meta)),'manifest_sha256':meta['manifest_sha256'],'parameters':meta['parameters'],'parameters_sha256':meta['parameters_sha256'],'member_set':meta['member_set'],'selected_DE':pin(selected)},
            'parent_result':{'result_id':parent['result_id'],'metadata_sha256':sha(canonical(parent)),'manifest_sha256':parent['manifest_sha256'],'selection':choice,'source_binding':binding},
            'processed':{'processed_dataset_version_id':processed_id,'metadata_sha256':sha(canonical(processed)),'manifest_sha256':processed['manifest_sha256'],'source':src,'row_map':pin(row_map)},
            'mapping':{'mapping_version_id':mapping_version_id,'metadata_sha256':sha(canonical(mapping_meta)),'content':pin(saved.content),'binding_sha256':mapping_meta['binding_sha256'],'inputs':inputs},
            'fasta':{'source_id':source.source_id,'source_bytes':source.bytes,'source_sha256':source.sha256,'index_version_id':candidate.index_version_id,'index_manifest_sha256':candidate.index_manifest_sha256,'config_sha256':candidate.config_sha256,'source_dto':stored.dto()},
            'counts':counts,'outputs':[{'name':'membership.tsv',**pin(member_bytes),'media_type':'text/tab-separated-values; charset=utf-8'},{'name':'queries.fasta',**pin(query_bytes),'media_type':'text/plain; charset=us-ascii'}]}
        desc=canonical(descriptor)+b'\n';need(len(desc)<=65536 and len(desc)+len(query_bytes)+len(member_bytes)<=MAX_OUTPUT,'dep_fasta_too_large',413)
        budget.check();return Bundle(desc,query_bytes,member_bytes)
