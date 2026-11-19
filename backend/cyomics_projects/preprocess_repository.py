"""Owner TaskV2 admission/read and function-only worker repository; default off.

Durable versions and all11 bytea artifacts are authenticated DB authority.
A write disposition unknown never disables an independent fresh Owner read.
"""
import asyncio
from dataclasses import asdict,dataclass,field
from datetime import datetime,timezone
import hashlib
import json
import re
from uuid import UUID,uuid4
from . import preprocess_contract as contract
from .import_persistence import SourceRef,decode_version
from .settings import Fault

QUEUE_CONTRACT='preprocessing_queued_v0.1'
COLUMNS=('job_id,project_id,dataset_version_id,file_id,owner_id,request_id,request_sha256,'
         'binding_sha256,source_json,quant_family,params_json,execution_json,status,created_at')
DATASET_COLUMNS=('dataset_version_id,project_id,file_id,parser_contract,config_sha256,config_snapshot,'
                 'source_bytes,source_sha256,representation,parsed_table_materialized,report_json,report_sha256,created_at')
SOURCE_KEYS=frozenset(('project_id','dataset_version_id','file_id','source_bytes','source_sha256',
    'parser_contract','config_sha256','report_sha256','row_count','column_count','selected_columns_sha256'))
LIMITS={'wall_seconds':60,'cpu_seconds':45,'address_space_bytes':2147483648,'per_file_bytes':67108864,
    'output_bytes':268435456,'stdout_bytes':65536,'stderr_bytes':65536,'monitor_seconds':0.05,
    'cleanup_seconds':2,'open_files':256,'uid_nproc_limit':512,'core_bytes':0,'job_tasks':8,'uid_admission_tasks':480}
EXECUTION_KEYS=frozenset(('contract','entry_kind','r_version','script','validator','runner','executable',
                          'limits','process_policy_sha256','registered'))
FIXED_PINS={'script':{'bytes':11996,'sha256':'08aec68d6ee74bd09b7cc70100d10e147c527986a3d42323fe5541a3b608e787'},
 'validator':{'bytes':14877,'sha256':'ba5fa3ed521fe7f97810ecdab06e1936e9dec8c9ebac45f8d2da3e3d77989d9e'},
 'executable':{'bytes':14488,'sha256':'9c572a708d444e0ec91c59935bf9b5c3fd426a75fe2b7101b2a65f4783155283'}}
POLICY_SHA256='2ab8f62a3e1973711c2bd63940161a72d57d4daa641e22967b12c28ed5d5fede'

def fail(status=503,code='preprocessing_persisted_invalid'):
    raise Fault(status,code)

def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)

def digest(text):return hashlib.sha256(text.encode('utf8')).hexdigest()

def identifier(value):
    try:return type(value) is str and str(UUID(value))==value
    except ValueError:return False

def hashed(value):return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None

def document(raw,cap=4096):
    if type(raw) is not str or not 1<=len(raw.encode('utf8'))<=cap:fail()
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:fail()
            result[key]=value
        return result
    try:
        value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:fail())
        if canonical(value)!=raw:fail()
        return value
    except Fault:raise
    except BaseException:fail()

def execution_value(raw):
    """Server source-proposal snapshot; pins do not register or authorize execution."""
    value=document(raw)
    if type(value) is dict and value.get('contract')=='median_saved_parent_source_proposal_v1':
        from .median_processed_profile import execution_value as median_execution
        return median_execution(value)
    if type(value) is dict and value.get('contract')=='total_saved_parent_source_proposal_v1':
        from .total_processed_profile import execution_value as total_execution
        return total_execution(value)
    if type(value) is dict and value.get('contract')=='quantile_saved_parent_source_proposal_v1':
        from .quantile_processed_profile import execution_value as quantile_execution
        return quantile_execution(value)
    if (type(value) is not dict or set(value)!=EXECUTION_KEYS
        or value['contract']!='log2_local_source_proposal_v0.1' or value['entry_kind']!='native_R'
        or value['r_version']!='R version 4.6.1' or value['registered'] is not False
        or value['process_policy_sha256']!=POLICY_SHA256
        or type(value['limits']) is not dict or canonical(value['limits'])!=canonical(LIMITS)):
        fail()
    for key in ('script','validator','runner','executable'):
        p=value[key]
        if (type(p) is not dict or set(p)!={'bytes','sha256'} or type(p['bytes']) is not int
            or not 1<=p['bytes']<=262144 or not hashed(p['sha256'])):fail()
        if key in FIXED_PINS and p!=FIXED_PINS[key]:fail()
    return value

def source_value(value):
    if type(value) is not dict or set(value)!=SOURCE_KEYS:fail()
    if any(not identifier(value[key]) for key in ('project_id','dataset_version_id','file_id')):fail()
    if any(not hashed(value[key]) for key in ('source_sha256','config_sha256','report_sha256','selected_columns_sha256')):fail()
    if value['parser_contract']!='protein_groups_txt_v1':fail()
    for key,low,high in (('source_bytes',1,26214400),('row_count',1,10000),('column_count',2,512)):
        if type(value[key]) is not int or not low<=value[key]<=high:fail()
    if value['row_count']*value['column_count']>2000000:fail()
    return value

def scoped_request_hash(project,dataset,request):
    return digest(canonical({'project_id':project,'dataset_version_id':dataset,'request':request.request_value()}))

def binding_hash(request_hash,source,request,execution):
    return digest(canonical({'contract':QUEUE_CONTRACT,'request_sha256':request_hash,'source':source,
        'quant_family':request.quant_family,'parameters':request.parameters_value(),'execution':execution}))

def _transactions():
    # No driver/executor import is needed in the disabled path.
    from .raw_qc_repository import Transactions,_TransactionDisposition
    return Transactions,_TransactionDisposition

@dataclass(frozen=True,slots=True)
class QueueObservation:
    _job_json:str=field(repr=False)
    created:bool
    commit_confirmed:bool
    teardown_confirmed:bool
    def job_public(self):return json.loads(self._job_json)

class PreprocessRepository:
    def __init__(self,pool=None,execution_json=None,*,enabled=False,median_execution_json=None,total_execution_json=None,quantile_execution_json=None):
        if type(enabled) is not bool:fail(503,'preprocessing_unavailable')
        self.pool=pool;self.enabled=enabled;self._locked=False;self._tx=None;self._read_locked=False;self._read_tx=None
        self._execution_json=None
        self._median_execution_json=None
        if median_execution_json is not None:
            from .median_processed_profile import PROPOSAL
            if type(median_execution_json) is not bytes or execution_value(median_execution_json.decode()).get("contract")!=PROPOSAL:fail(503,"preprocessing_unavailable")
            self._median_execution_json=median_execution_json.decode()
        self._total_execution_json=None
        if total_execution_json is not None:
            from .total_processed_profile import PROPOSAL
            if type(total_execution_json) is not bytes or execution_value(total_execution_json.decode()).get("contract")!=PROPOSAL:fail(503,"preprocessing_unavailable")
            self._total_execution_json=total_execution_json.decode()
        self._quantile_execution_json=None
        if quantile_execution_json is not None:
            from .quantile_processed_profile import PROPOSAL
            if type(quantile_execution_json) is not bytes or execution_value(quantile_execution_json.decode()).get("contract")!=PROPOSAL:fail(503,"preprocessing_unavailable")
            self._quantile_execution_json=quantile_execution_json.decode()
        if execution_json is not None:
            try:
                if type(execution_json) is not bytes:fail()
                text=execution_json.decode('utf8');execution_value(text);self._execution_json=text
            except BaseException:fail(503,'preprocessing_unavailable')

    @property
    def median_enabled(self):return self.enabled and self._median_execution_json is not None
    @property
    def total_enabled(self):return self.enabled and self._total_execution_json is not None
    @property
    def quantile_enabled(self):return self.enabled and self._quantile_execution_json is not None

    @property
    def quarantined(self):return self._locked or self._read_locked

    def _ready(self,writing=False):
        if not self.enabled or self.pool is None or self._read_locked or (writing and (self._locked or self._execution_json is None)):
            fail(503,'preprocessing_unavailable')

    @staticmethod
    def ids(subject,project,*others):
        if not identifier(subject):fail(401,'unauthenticated')
        if not identifier(project) or any(not identifier(v) for v in others):fail(400,'invalid_preprocessing_request')

    async def _run(self,subject,budget,operation,writing=False):
        # A writer's unknown disposition never clears. Independent read transactions
        # may still prove a committed version; no writer reconnect/retry is implied.
        self._ready(writing)
        lane='_locked' if writing else '_read_locked'
        tx_field='_tx' if writing else '_read_tx'
        def lock():setattr(self,lane,True)
        try:budget.check()
        except asyncio.CancelledError:lock();raise
        except TimeoutError:fail(504,'preprocessing_timeout')
        except BaseException:fail(503,'preprocessing_unavailable')
        try:
            Base,Disposition=_transactions();tx=getattr(self,tx_field)
            if tx is None:
                class Adapter(Base):
                    def _disposition_unknown(self):lock()
                tx=Adapter(self.pool);setattr(self,tx_field,tx)
        except BaseException:fail(503,'preprocessing_unavailable')
        disposition=Disposition()
        try:
            value=await tx.transaction(subject,budget,operation,writing=writing,_disposition=disposition)
            if getattr(self,lane) or disposition.uncertain or not disposition.commit_confirmed or not disposition.teardown_confirmed:
                lock();fail(504 if writing else 503,'preprocessing_submission_unknown' if writing else 'preprocessing_unavailable')
            return value
        except asyncio.CancelledError:lock();raise
        except BaseException as error:
            if disposition.uncertain or getattr(self,lane):
                lock();fail(504 if writing and disposition.commit_started else 503,
                    'preprocessing_submission_unknown' if writing and disposition.commit_started else 'preprocessing_unavailable')
            if isinstance(error,Fault):
                mapping={'raw_qc_unavailable':'preprocessing_unavailable','raw_qc_timeout':'preprocessing_timeout','raw_qc_submission_unknown':'preprocessing_submission_unknown'}
                if error.code in mapping:fail(error.status,mapping[error.code])
                if error.code in ('preprocessing_persisted_invalid','preprocessing_request_conflict','preprocessing_family_unavailable',
                    'preprocessing_flags_unavailable','preprocessing_job_not_found','dataset_not_found','processed_dataset_version_not_found',
                    'preprocessing_artifact_not_found','preprocessing_artifact_integrity_failed','preprocessing_attempt_conflict'):
                    raise
            fail(504 if isinstance(error,TimeoutError) else 503,'preprocessing_timeout' if isinstance(error,TimeoutError) else 'preprocessing_unavailable')

    async def _owner(self,query,subject,project):
        row=await query('SELECT project_id FROM cyomics_app.projects WHERE owner_id=%s AND project_id=%s',
                        (UUID(subject),UUID(project)))
        if row is None:fail(404,'dataset_not_found')
        if len(row)!=1 or str(row[0])!=project:fail()

    async def _source(self,query,subject,project,dataset,family,flags):
        row=await query('SELECT '+DATASET_COLUMNS+' FROM cyomics_app.dataset_versions WHERE project_id=%s AND dataset_version_id=%s',
                        (UUID(project),UUID(dataset)))
        if row is None:fail(404,'dataset_not_found')
        try:
            if len(row)!=13:fail()
            raw=await query('SELECT file_id,project_id,format,bytes,sha256,status FROM cyomics_app.raw_uploads WHERE project_id=%s AND file_id=%s',
                            (UUID(project),row[2]))
            if (raw is None or len(raw)!=6 or str(raw[0])!=str(row[2]) or str(raw[1])!=project
                or raw[2]!='proteinGroups_txt' or raw[5]!='stored'
                or type(raw[3]) is not int or raw[3]!=row[6] or raw[4]!=row[7]):fail()
            source=SourceRef(project,str(row[2]),'raw_bytea_v1:'+str(row[2])+':'+row[7],row[6],row[7])
            decoded=decode_version(row,source,row[5],dataset)
            report=decoded['importReport'];selected=[column for column in report['sample_columns'] if column['quant_family']==family]
            if family is not None and not selected:fail(422,'preprocessing_family_unavailable')
            available={column['name_raw'] for column in report['columns']}
            if any(flags[key] and name not in available for key,name in
                   (('reverse','Reverse'),('contaminant','Potential contaminant'),('site_only','Only identified by site'))):
                fail(422,'preprocessing_flags_unavailable')
            return source_value({'project_id':source.project_id,'dataset_version_id':dataset,'file_id':source.raw_file_id,
                'source_bytes':source.expected_bytes,'source_sha256':source.expected_raw_sha256,
                'parser_contract':row[3],'config_sha256':row[4],'report_sha256':row[11],
                'row_count':report['report']['row_count'],'column_count':report['report']['column_count'],
                'selected_columns_sha256':digest(canonical(selected))})
        except (asyncio.CancelledError,TimeoutError):raise
        except Fault as error:
            if error.code in ('preprocessing_family_unavailable','preprocessing_flags_unavailable'):raise
            fail()
        except BaseException:fail()

    def _decode(self,row,subject,project,expected_source=None):
        try:
            if row is None or len(row)!=14:fail()
            job,pid,dataset,fid,owner,rid,rsha,bsha,sjson,family,pjson,ejson,status,created=row
            if str(owner)!=subject or str(pid)!=project:fail(404,'preprocessing_job_not_found')
            if not all(identifier(str(v)) for v in (job,pid,dataset,fid,owner,rid)) or status!='queued':fail()
            source=source_value(document(sjson));execution=execution_value(ejson)
            if (source['project_id']!=project or source['dataset_version_id']!=str(dataset)
                or source['file_id']!=str(fid) or (expected_source is not None and source!=expected_source)):fail()
            params=document(pjson)
            request=contract.request_input(canonical({'request_id':str(rid),'quant_family':family,'parameters':params}).encode('utf8'),median_enabled=True,total_enabled=True,quantile_enabled=True)
            from .quantile_processed_profile import match_strategy as quantile_match
            quantile_match(params,execution)
            from .total_processed_profile import match_strategy as total_match
            total_match(params,execution)
            from .median_processed_profile import match_strategy
            match_strategy(params,execution)
            if (request.parameters_bytes.decode('utf8')!=pjson or rsha!=scoped_request_hash(project,str(dataset),request)
                or bsha!=binding_hash(rsha,source,request,execution)
                or not isinstance(created,datetime) or created.utcoffset() is None):fail()
            return contract.task_value({'schemaVersion':2,'job_id':str(job),'request_id':str(rid),'request_sha256':rsha,
                'binding_sha256':bsha,'source':source,'quant_family':family,'parameters':request.parameters_value(),
                'parameters_sha256':request.parameters_sha256,'proposal_sha256':digest(ejson),'status':'queued',
                'created_at':created.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                'result':None,'revision':1,'started_at':None,'finished_at':None,'observation':'confirmed',
                'error':None,'runtime':None,'attempt_id':None})
        except Fault:raise
        except BaseException:fail()

    async def enqueue(self,subject,project,dataset,raw_body,budget):
        # This contract is pure and precedes even transaction/driver creation.
        try:request=contract.request_input(raw_body,median_enabled=True,total_enabled=True,quantile_enabled=True)
        except contract.RequestError as error:fail(413 if error.code=='preprocessing_request_too_large' else 400,error.code)
        from .median_processed_profile import is_median
        median=is_median(request.parameters_value())
        from .total_processed_profile import is_total
        total=is_total(request.parameters_value())
        from .quantile_processed_profile import is_quantile
        quantile=is_quantile(request.parameters_value())
        if quantile and not self.quantile_enabled:fail(503,'preprocessing_unavailable')
        if total and not self.total_enabled:fail(503,'preprocessing_unavailable')
        if median and not self.median_enabled:fail(503,'preprocessing_unavailable')
        self.ids(subject,project,dataset);self._ready(True)
        created=False
        async def operation(query):
            nonlocal created
            await self._owner(query,subject,project)
            await query('SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s,47008))',(project,))
            prior=await query('SELECT '+COLUMNS+' FROM cyomics_app.preprocessing_jobs WHERE project_id=%s AND request_id=%s',
                              (UUID(project),UUID(request.request_id)))
            request_hash=scoped_request_hash(project,dataset,request)
            if prior is not None:
                value=self._decode(prior,subject,project)
                if value['request_sha256']!=request_hash:fail(409,'preprocessing_request_conflict')
            source=await self._source(query,subject,project,dataset,request.quant_family,request.parameters_value()['filter_flags'])
            if prior is not None:
                return await self._with_run(query,self._decode(prior,subject,project,source),proposal=prior[11])
            chosen=self._quantile_execution_json if quantile else self._total_execution_json if total else self._median_execution_json if median else self._execution_json
            execution=execution_value(chosen)
            if median or total or quantile:
                parent=await self._read_version(query,subject,project,request.parameters_value()['normalization_parent']['processed_dataset_version_id'],verify_output=True)
                from .median_processed_profile import parent_metadata
                if total:
                    from .total_processed_profile import parent_metadata
                elif quantile:
                    from .quantile_processed_profile import parent_metadata
                try:parent_metadata(parent,source,request.parameters_value(),request.quant_family,subject)
                except contract.RequestError:fail(400,'invalid_preprocessing_request')
            binding=binding_hash(request_hash,source,request,execution)
            row=await query('INSERT INTO cyomics_app.preprocessing_jobs '
                '(job_id,project_id,dataset_version_id,file_id,owner_id,request_id,request_sha256,binding_sha256,source_json,quant_family,params_json,execution_json) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING '+COLUMNS,
                (uuid4(),UUID(project),UUID(dataset),UUID(source['file_id']),UUID(subject),UUID(request.request_id),
                 request_hash,binding,canonical(source),request.quant_family,request.parameters_bytes.decode('utf8'),chosen),write=True)
            value=self._decode(row,subject,project,source)
            if value['request_id']!=request.request_id or value['binding_sha256']!=binding:fail()
            created=True
            return value
        value=await self._run(subject,budget,operation,writing=True)
        return QueueObservation(canonical(value),created,True,True)

    async def read_job(self,subject,project,job,budget):
        self.ids(subject,project,job)
        async def operation(query):
            await self._owner(query,subject,project)
            row=await query('SELECT '+COLUMNS+' FROM cyomics_app.preprocessing_jobs WHERE project_id=%s AND job_id=%s',
                            (UUID(project),UUID(job)))
            return await self._checked(query,subject,project,row)
        return await self._run(subject,budget,operation)

    async def read_request(self,subject,project,dataset,request_id,budget):
        self.ids(subject,project,dataset,request_id)
        async def operation(query):
            await self._owner(query,subject,project)
            row=await query('SELECT '+COLUMNS+' FROM cyomics_app.preprocessing_jobs WHERE project_id=%s AND dataset_version_id=%s AND request_id=%s',
                            (UUID(project),UUID(dataset),UUID(request_id)))
            return await self._checked(query,subject,project,row)
        return await self._run(subject,budget,operation)

    async def _checked(self,query,subject,project,row):
        if row is None:fail(404,'preprocessing_job_not_found')
        if len(row)!=14:fail()
        if str(row[4])!=subject:fail(404,'preprocessing_job_not_found')
        value=self._decode(row,subject,project)
        source=await self._source(query,subject,project,value['source']['dataset_version_id'],value['quant_family'],value['parameters']['filter_flags'])
        return await self._with_run(query,self._decode(row,subject,project,source),proposal=row[11])

    async def _with_run(self,query,value,*,proposal,verify_output=True):
        row=await query('SELECT '+RUN_PUBLIC_COLUMNS+' FROM cyomics_app.preprocessing_job_runs WHERE job_id=%s',(UUID(value['job_id']),))
        if row is None:return value
        if len(row)!=13:fail()
        reg=await query('SELECT registration_id,profile_sha256,attestation_sha256 FROM cyomics_app.preprocessing_runtime_registrations WHERE registration_id=%s',(row[2],))
        if reg is None or len(reg)!=3 or str(reg[0])!=str(row[2]):fail()
        try:
            value=join_run(value,dict(zip(RUN_PUBLIC_COLUMNS.split(','),row)),
                {'registration_id':str(reg[0]),'profile_sha256':reg[1],'attestation_sha256':reg[2]})
            if value['status']=='succeeded':await self._version_for_task(query,value,proposal,verify_output=verify_output)
            return value
        except (asyncio.CancelledError,TimeoutError):raise
        except Fault:raise
        except BaseException:fail()

    async def _version_for_task(self,query,task,proposal,*,verify_output):
        result=task['result'];vid=result['processed_dataset_version_id'];project=task['source']['project_id']
        row=await query('SELECT metadata_json,metadata_sha256,manifest_sha256,job_id,project_id,parent_dataset_version_id,file_id,created_by,created_at FROM cyomics_app.processed_dataset_versions WHERE project_id=%s AND processed_dataset_version_id=%s',(UUID(project),UUID(vid)))
        if row is None or len(row)!=9:fail()
        value=document(row[0],32768)
        if (digest(row[0])!=row[1] or row[2]!=result['manifest_sha256'] or str(row[3])!=task['job_id']
            or str(row[4])!=project or str(row[5])!=task['source']['dataset_version_id'] or str(row[6])!=task['source']['file_id']
            or str(row[7])!=value.get('created_by') or utc(row[8])!=value.get('created_at')):fail()
        try:contract.version_value(value,project=project,version=vid,task=task)
        except BaseException:fail()
        if value['tool']!=public_tool(execution_value(proposal)):fail()
        suffix=''
        if verify_output:
            suffix=','+','.join('octet_length('+col+'),encode(sha256('+col+"),'hex')" for col in ARTIFACT_COLUMNS.values())
        bundle=await query('SELECT manifest_json,manifest_sha256'+suffix+' FROM cyomics_app.processed_dataset_artifact_bundles WHERE processed_dataset_version_id=%s',(UUID(vid),))
        if bundle is None or len(bundle)!=(24 if verify_output else 2):fail()
        manifest=document(bundle[0],8192)
        if digest(bundle[0])!=bundle[1] or bundle[1]!=value['manifest_sha256'] or manifest!=value['artifacts']:fail()
        if verify_output:
            for i,item in enumerate(manifest):
                if type(bundle[2+i*2]) is not int or bundle[2+i*2]!=item['bytes'] or bundle[3+i*2]!=item['sha256']:
                    fail(503,'preprocessing_artifact_integrity_failed')
        return value

    async def list_jobs(self,subject,project,dataset,budget,*,limit=10,cursor=None):
        self.ids(subject,project,dataset)
        try:
            if type(limit) is not int or not 1<=limit<=10:raise ValueError()
            after=None if cursor is None else contract.decode_cursor(cursor)
        except BaseException:fail(400,'invalid_preprocessing_request')
        async def operation(query):
            await self._owner(query,subject,project)
            await self._source(query,subject,project,dataset,None,{'contaminant':False,'reverse':False,'site_only':False})
            where='';params=[UUID(project),UUID(dataset)]
            if after is not None:
                where=' AND (created_at,job_id)<(%s::timestamptz,%s)';params.extend([after['createdAt'],UUID(after['job_id'])])
            params.append(limit+1)
            row=await query('SELECT coalesce(jsonb_agg(to_jsonb(q)),\'[]\'::jsonb) FROM (SELECT '+COLUMNS+' FROM cyomics_app.preprocessing_jobs WHERE project_id=%s AND dataset_version_id=%s'+where+' ORDER BY created_at DESC,job_id DESC LIMIT %s) q',tuple(params))
            if row is None or len(row)!=1 or type(row[0]) is not list or len(row[0])>limit+1:fail()
            items=[]
            for item in row[0][:limit]:
                record=admission_row(item);base=self._decode(record,subject,project)
                source=await self._source(query,subject,project,dataset,base['quant_family'],base['parameters']['filter_flags'])
                value=await self._with_run(query,self._decode(record,subject,project,source),proposal=record[11],verify_output=False)
                tentative={'items':items+[value],'next_cursor':contract.encode_cursor(value['created_at'],value['job_id'])}
                if len(contract.canonical(tentative))>32768:break
                items.append(value)
            if row[0] and not items:fail()
            next_cursor=None if len(row[0])<=len(items) else contract.encode_cursor(items[-1]['created_at'],items[-1]['job_id'])
            result={'items':items,'next_cursor':next_cursor}
            try:return contract.list_value(result,project=project,dataset=dataset,limit=limit)
            except BaseException:fail()
        return await self._run(subject,budget,operation)

    async def _read_version(self,query,subject,project,version,*,verify_output):
        row=await query('SELECT job_id FROM cyomics_app.processed_dataset_versions WHERE project_id=%s AND processed_dataset_version_id=%s',(UUID(project),UUID(version)))
        if row is None:fail(404,'processed_dataset_version_not_found')
        if len(row)!=1:fail()
        admission=await query('SELECT '+COLUMNS+' FROM cyomics_app.preprocessing_jobs WHERE project_id=%s AND job_id=%s',(UUID(project),row[0]))
        if admission is None:fail()
        base=self._decode(admission,subject,project)
        source=await self._source(query,subject,project,base['source']['dataset_version_id'],base['quant_family'],base['parameters']['filter_flags'])
        # Decode/join without recursively fetching the bundle twice.
        task=await self._with_run(query,self._decode(admission,subject,project,source),proposal=admission[11],verify_output=False)
        if task['status']!='succeeded' or task['result']['processed_dataset_version_id']!=version:fail()
        return await self._version_for_task(query,task,admission[11],verify_output=verify_output)

    async def read_version(self,subject,project,version,budget):
        self.ids(subject,project,version)
        async def operation(query):
            await self._owner(query,subject,project)
            return await self._read_version(query,subject,project,version,verify_output=True)
        return await self._run(subject,budget,operation)

    async def read_artifact(self,subject,project,version,name,budget):
        self.ids(subject,project,version)
        if type(name) is not str or name not in ARTIFACT_COLUMNS:fail(404,'preprocessing_artifact_not_found')
        async def operation(query):
            await self._owner(query,subject,project)
            value=await self._read_version(query,subject,project,version,verify_output=False)
            pin=next(x for x in value['artifacts'] if x['name']==name)
            # Column comes exclusively from this fixed11-entry server whitelist.
            row=await query('SELECT '+ARTIFACT_COLUMNS[name]+' FROM cyomics_app.processed_dataset_artifact_bundles WHERE processed_dataset_version_id=%s',(UUID(version),))
            if row is None or len(row)!=1:fail(404,'preprocessing_artifact_not_found')
            data=row[0]
            if type(data) is memoryview:
                if data.ndim!=1 or data.itemsize!=1 or not data.contiguous or data.nbytes>contract.MAX_ARTIFACT:fail()
                data=data.tobytes()
            if type(data) is not bytes or len(data)!=pin['bytes'] or not 1<=len(data)<=contract.MAX_ARTIFACT or contract.bytes_sha(data)!=pin['sha256']:
                fail(503,'preprocessing_artifact_integrity_failed')
            return StoredArtifact(name,pin['media_type'],data,pin['sha256'])
        return await self._run(subject,budget,operation)


RUN_PUBLIC_COLUMNS='job_id,attempt_id,registration_id,runtime_binding_sha256,status,revision,observation,started_at,finished_at,error_code,receipt_sha256,result_version_id,manifest_sha256'
ARTIFACT_COLUMNS=dict(zip(contract.ARTIFACT_NAMES,('r_session_info','filter_log','imputed_mask','original_blank_mask','original_zero_mask','parameters','raw_retained','row_map','summary','working_na_mask','working_matrix')))


@dataclass(frozen=True,slots=True)
class StoredArtifact:
    name:str
    media_type:str
    body:bytes=field(repr=False)
    sha256:str


def utc(value):
    if type(value) is str:
        try:value=datetime.fromisoformat(value.replace('Z','+00:00'))
        except ValueError:fail()
    if not isinstance(value,datetime) or value.utcoffset() is None:fail()
    return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def admission_row(value):
    if type(value) is not dict or set(value)!=set(COLUMNS.split(',')):fail()
    result=[value[k] for k in COLUMNS.split(',')]
    try:
        for i in range(6):result[i]=UUID(result[i]) if type(result[i]) is str else result[i]
        result[13]=datetime.fromisoformat(value['created_at'].replace('Z','+00:00')) if type(value['created_at']) is str else value['created_at']
    except BaseException:fail()
    return result


def public_tool(proposal):
    return {'program':'R','r_version':proposal['r_version'],'script_sha256':proposal['script']['sha256'],
        'validator_sha256':proposal['validator']['sha256'],'runner_sha256':proposal['runner']['sha256'],
        'native_sha256':proposal['executable']['sha256'],'process_policy_sha256':proposal['process_policy_sha256']}


def join_run(value,run,reg):
    if str(run['job_id'])!=value['job_id'] or str(run['registration_id'])!=str(reg['registration_id']):fail()
    result={**value,'status':run['status'],'revision':run['revision'],'observation':run['observation'],
        'attempt_id':str(run['attempt_id']),'started_at':utc(run['started_at']),
        'finished_at':None if run['finished_at'] is None else utc(run['finished_at']),
        'error':None if run['error_code'] is None else {'code':run['error_code']},
        'runtime':{'registration_id':str(reg['registration_id']),'profile_sha256':reg['profile_sha256'],
            'attestation_sha256':reg['attestation_sha256'],'runtime_binding_sha256':run['runtime_binding_sha256'],
            'receipt_sha256':run['receipt_sha256']},'result':None}
    if run['status']=='succeeded':result['result']={'processed_dataset_version_id':str(run['result_version_id']),
        'manifest_sha256':run['manifest_sha256'],'artifact_count':11,'analysis_ready':False}
    try:return contract.task_value(result)
    except BaseException:fail()


@dataclass(frozen=True,slots=True)
class ClaimedJob:
    _record_json:str=field(repr=False)
    _task_json:str=field(repr=False)
    def record(self):return json.loads(self._record_json)
    def task(self):return json.loads(self._task_json)


@dataclass(frozen=True,slots=True)
class BoundInput:
    capsule:ClaimedJob
    _dataset_json:str=field(repr=False)
    _raw_meta_json:str=field(repr=False)
    content:bytes=field(repr=False)
    def dataset(self):return json.loads(self._dataset_json)
    def raw_meta(self):return json.loads(self._raw_meta_json)


def decode_capsule(value):
    try:
        if type(value) is not dict or set(value)!={'admission','run','registration'}:fail()
        row=admission_row(value['admission']);g=value['registration'];run=value['run']
        if type(g) is not dict or type(run) is not dict:fail()
        base=PreprocessRepository()._decode(row,str(row[4]),str(row[1]))
        if (str(g['project_id'])!=base['source']['project_id'] or g['proposal_sha256']!=base['proposal_sha256']
            or digest(g['profile_json'])!=g['profile_sha256'] or digest(g['attestation_json'])!=g['attestation_sha256']):fail()
        task=join_run(base,run,g)
        return ClaimedJob(canonical(value),canonical(task))
    except Fault:raise
    except BaseException:fail()


class PreprocessWorkerRepository:
    """Fixed function-only worker capsule; no caller Owner, role or generic UPDATE.

    Any unknown disposition permanently locks this instance. A fresh Owner read
    uses its own repository transaction; it never clears this worker's unknown.
    """
    def __init__(self,pool=None,*,enabled=False):
        if type(enabled) is not bool:fail(503,'preprocessing_unavailable')
        self.pool=pool;self.enabled=enabled;self._locked=False;self._tx=None
    @property
    def quarantined(self):return self._locked

    async def _run(self,budget,operation,*,writing=False):
        if not self.enabled or self.pool is None or self._locked:fail(503,'preprocessing_unavailable')
        Base,Disposition=_transactions()
        if self._tx is None:
            parent=self
            class Adapter(Base):
                def _disposition_unknown(self):parent._locked=True
            self._tx=Adapter(self.pool)
        disposition=Disposition()
        try:
            budget.check()
            value=await self._tx.transaction(None,budget,operation,writing=writing,_disposition=disposition)
            if self._locked or disposition.uncertain or not disposition.commit_confirmed or not disposition.teardown_confirmed:
                self._locked=True;fail(504 if writing else 503,'preprocessing_submission_unknown' if writing else 'preprocessing_unavailable')
            return value
        except asyncio.CancelledError:self._locked=True;raise
        except BaseException as error:
            if self._locked or disposition.uncertain:
                self._locked=True;fail(504 if writing and disposition.commit_started else 503,
                    'preprocessing_submission_unknown' if writing and disposition.commit_started else 'preprocessing_unavailable')
            if isinstance(error,Fault) and error.code in ('preprocessing_attempt_conflict','preprocessing_persisted_invalid',
                'preprocessing_unavailable','preprocessing_execution_unavailable'):raise
            fail(504 if isinstance(error,TimeoutError) else 503,'preprocessing_timeout' if isinstance(error,TimeoutError) else 'preprocessing_unavailable')

    @staticmethod
    def _ids(*values):
        if any(not identifier(v) for v in values):fail(400,'invalid_preprocessing_request')

    async def claim(self,registration_id,scope_sha256,attempt_id,budget,*,job_id=None):
        self._ids(registration_id,attempt_id)
        if job_id is not None:self._ids(job_id)
        if not hashed(scope_sha256):fail(400,'invalid_preprocessing_request')
        async def operation(query):
            row=await query('SELECT cyomics_app.preprocessing_claim(%s,%s,%s,%s)',
                (None if job_id is None else UUID(job_id),UUID(attempt_id),UUID(registration_id),scope_sha256),write=True)
            if row is None or len(row)!=1:fail()
            if row[0] is None:return None
            value=decode_capsule(row[0])
            if value.task()['attempt_id']!=attempt_id or value.task()['status']!='running' or value.task()['runtime']['registration_id']!=registration_id:fail()
            if job_id is not None and value.task()['job_id']!=job_id:fail()
            return value
        return await self._run(budget,operation,writing=True)

    async def read_attempt(self,job_id,attempt_id,budget):
        self._ids(attempt_id)
        if job_id is not None:self._ids(job_id)
        async def operation(query):
            row=await query('SELECT cyomics_app.preprocessing_run_record(%s,%s)',(None if job_id is None else UUID(job_id),UUID(attempt_id)))
            if row is None or len(row)!=1:fail()
            if row[0] is None:return None
            value=decode_capsule(row[0]);task=value.task()
            if task['attempt_id']!=attempt_id or job_id is not None and task['job_id']!=job_id:fail()
            return value
        return await self._run(budget,operation)

    async def load_bound_source(self,job_id,attempt_id,budget):
        self._ids(job_id,attempt_id)
        async def operation(query):
            row=await query('SELECT record,dataset_json,raw_meta,content FROM cyomics_app.preprocessing_bound_source(%s,%s)',(UUID(job_id),UUID(attempt_id)))
            if row is None:fail(409,'preprocessing_attempt_conflict')
            if len(row)!=4:fail()
            capsule=decode_capsule(row[0]);task=capsule.task();source=task['source'];data=row[3]
            if task['job_id']!=job_id or task['attempt_id']!=attempt_id or task['status']!='running' or task['observation']!='confirmed':fail()
            if type(data) is memoryview:
                if data.ndim!=1 or data.itemsize!=1 or not data.contiguous or not 1<=data.nbytes<=26214400:fail()
                data=data.tobytes()
            if type(data) is not bytes or len(data)!=source['source_bytes'] or contract.bytes_sha(data)!=source['source_sha256']:fail()
            d=row[1];raw=row[2]
            if type(d) is not dict or type(raw) is not dict or set(raw)!={'file_id','project_id','format','bytes','sha256','status'}:fail()
            if (str(raw['file_id'])!=source['file_id'] or str(raw['project_id'])!=source['project_id'] or raw['format']!='proteinGroups_txt'
                or raw['status']!='stored' or type(raw['bytes']) is not int or raw['bytes']!=len(data) or raw['sha256']!=source['source_sha256']):fail()
            columns=DATASET_COLUMNS.split(',')
            if set(d)!=set(columns):fail()
            values=[d[k] for k in columns]
            for i in range(3):values[i]=UUID(values[i]) if type(values[i]) is str else values[i]
            values[12]=datetime.fromisoformat(d['created_at'].replace('Z','+00:00')) if type(d['created_at']) is str else d['created_at']
            ref=SourceRef(source['project_id'],source['file_id'],'raw_bytea_v1:'+source['file_id']+':'+source['source_sha256'],len(data),source['source_sha256'])
            decoded=decode_version(values,ref,d['config_snapshot'],source['dataset_version_id'])
            report=decoded['importReport'];selected=[x for x in report['sample_columns'] if x['quant_family']==task['quant_family']]
            if (not selected or digest(canonical(selected))!=source['selected_columns_sha256'] or d['config_sha256']!=source['config_sha256']
                or d['report_sha256']!=source['report_sha256'] or report['report']['row_count']!=source['row_count']
                or report['report']['column_count']!=source['column_count']):fail()
            return BoundInput(capsule,canonical(d),canonical(raw),data)
        return await self._run(budget,operation)

    async def load_median_parent(self,capsule,budget):
        # The only new worker read is bound to an already claimed job/attempt.
        if type(capsule) is not ClaimedJob:fail()
        task=capsule.task()
        if task['parameters']['normalization']!='median':fail()
        async def operation(query):
            row=await query('SELECT metadata_json,manifest_json,'+','.join(ARTIFACT_COLUMNS.values())+
                ' FROM cyomics_app.preprocessing_median_parent_bundle(%s,%s)',
                (UUID(task['job_id']),UUID(task['attempt_id'])))
            if row is None:fail(409,'preprocessing_attempt_conflict')
            if len(row)!=13:fail()
            parent=document(row[0],32768);manifest=document(row[1],8192)
            from .median_processed_profile import parent_metadata,parent_artifacts
            try:
                parent_metadata(parent,task['source'],task['parameters'],task['quant_family'],capsule.record()['admission']['owner_id'])
                if manifest!=parent['artifacts'] or digest(row[1])!=parent['manifest_sha256']:fail()
                bodies={}
                for name,data in zip(contract.ARTIFACT_NAMES,row[2:]):
                    if type(data) is memoryview:
                        if data.ndim!=1 or data.itemsize!=1 or not data.contiguous or data.nbytes>contract.MAX_ARTIFACT:fail()
                        data=data.tobytes()
                    bodies[name]=data
                parent_artifacts(parent,bodies)
            except BaseException:fail()
            return parent,bodies
        return await self._run(budget,operation)

    async def load_total_parent(self,capsule,budget):
        # The only new worker read is bound to an already claimed job/attempt.
        if type(capsule) is not ClaimedJob:fail()
        task=capsule.task()
        if task['parameters']['normalization']!='total':fail()
        async def operation(query):
            row=await query('SELECT metadata_json,manifest_json,'+','.join(ARTIFACT_COLUMNS.values())+
                ' FROM cyomics_app.preprocessing_total_parent_bundle(%s,%s)',
                (UUID(task['job_id']),UUID(task['attempt_id'])))
            if row is None:fail(409,'preprocessing_attempt_conflict')
            if len(row)!=13:fail()
            parent=document(row[0],32768);manifest=document(row[1],8192)
            from .total_processed_profile import parent_metadata,parent_artifacts
            try:
                parent_metadata(parent,task['source'],task['parameters'],task['quant_family'],capsule.record()['admission']['owner_id'])
                if manifest!=parent['artifacts'] or digest(row[1])!=parent['manifest_sha256']:fail()
                bodies={}
                for name,data in zip(contract.ARTIFACT_NAMES,row[2:]):
                    if type(data) is memoryview:
                        if data.ndim!=1 or data.itemsize!=1 or not data.contiguous or data.nbytes>contract.MAX_ARTIFACT:fail()
                        data=data.tobytes()
                    bodies[name]=data
                parent_artifacts(parent,bodies)
            except BaseException:fail()
            return parent,bodies
        return await self._run(budget,operation)

    async def load_quantile_parent(self,capsule,budget):
        # The only new worker read is bound to an already claimed job/attempt.
        if type(capsule) is not ClaimedJob:fail()
        task=capsule.task()
        if task['parameters']['normalization']!='quantile':fail()
        async def operation(query):
            row=await query('SELECT metadata_json,manifest_json,'+','.join(ARTIFACT_COLUMNS.values())+
                ' FROM cyomics_app.preprocessing_quantile_parent_bundle(%s,%s)',
                (UUID(task['job_id']),UUID(task['attempt_id'])))
            if row is None:fail(409,'preprocessing_attempt_conflict')
            if len(row)!=13:fail()
            parent=document(row[0],32768);manifest=document(row[1],8192)
            from .quantile_processed_profile import parent_metadata,parent_artifacts
            try:
                parent_metadata(parent,task['source'],task['parameters'],task['quant_family'],capsule.record()['admission']['owner_id'])
                if manifest!=parent['artifacts'] or digest(row[1])!=parent['manifest_sha256']:fail()
                bodies={}
                for name,data in zip(contract.ARTIFACT_NAMES,row[2:]):
                    if type(data) is memoryview:
                        if data.ndim!=1 or data.itemsize!=1 or not data.contiguous or data.nbytes>contract.MAX_ARTIFACT:fail()
                        data=data.tobytes()
                    bodies[name]=data
                parent_artifacts(parent,bodies)
            except BaseException:fail()
            return parent,bodies
        return await self._run(budget,operation)

    async def finish(self,capsule,target,error_code,budget,*,failure_receipt=None):
        if type(capsule) is not ClaimedJob:fail(400,'invalid_preprocessing_request')
        task=capsule.task();contract.task_value(task)
        if target not in ('failed','reconciliation_pending') or (target=='failed' and error_code not in contract.FAILURE_CODES) or (target=='reconciliation_pending' and error_code is not None):fail(400,'invalid_preprocessing_request')
        if target=='failed':
            if type(failure_receipt) is not dict or failure_receipt.get('job_id')!=task['job_id'] or failure_receipt.get('attempt_id')!=task['attempt_id'] or failure_receipt.get('runtime_binding_sha256')!=task['runtime']['runtime_binding_sha256'] or failure_receipt.get('code')!=error_code or failure_receipt.get('facts',{}).get('cleanup_known') is not True:fail(400,'invalid_preprocessing_request')
        elif failure_receipt is not None:fail(400,'invalid_preprocessing_request')
        async def operation(query):
            row=await query('SELECT cyomics_app.preprocessing_finish(%s,%s,%s,%s,%s,%s)',(UUID(task['job_id']),UUID(task['attempt_id']),task['revision'],target,error_code,None if failure_receipt is None else canonical(failure_receipt)),write=True)
            if row is None or len(row)!=1 or row[0] is None:fail(409,'preprocessing_attempt_conflict')
            value=decode_capsule(row[0]);out=value.task()
            if out['job_id']!=task['job_id'] or out['attempt_id']!=task['attempt_id'] or out['revision']!=task['revision']+1:fail()
            if out['status']!=('running' if target=='reconciliation_pending' else 'failed'):fail()
            return value
        return await self._run(budget,operation,writing=True)

    async def publish(self,capsule,metadata,bundle,receipt,budget):
        from .log2_matrix_validator import ValidatedLog2Bundle
        if type(capsule) is not ClaimedJob or type(bundle) is not ValidatedLog2Bundle or type(receipt) is not dict:fail(400,'invalid_preprocessing_request')
        task=capsule.task();contract.task_value(task);contract.version_value(metadata)
        manifest=[{'name':a.name,'bytes':a.length,'sha256':a.sha256,'media_type':a.media_type} for a in bundle.artifacts]
        if (tuple(a.name for a in bundle.artifacts)!=contract.ARTIFACT_NAMES or metadata['artifacts']!=manifest
            or metadata['manifest_sha256']!=bundle.manifest_sha256 or contract.bytes_sha(contract.canonical(manifest))!=bundle.manifest_sha256):fail()
        for artifact in bundle.artifacts:
            if type(artifact.body) is not bytes or len(artifact.body)!=artifact.length or contract.bytes_sha(artifact.body)!=artifact.sha256:fail()
        from .preprocess_execution import validate_receipt,validate_version_for_claim
        validate_receipt(capsule,bundle,receipt);validate_version_for_claim(capsule,metadata,bundle,receipt)
        bodies={a.name:a.body for a in bundle.artifacts}
        order=('filter-log.tsv','row-map.tsv','summary.tsv','raw-retained.tsv','working-matrix.tsv','original-blank-mask.tsv','original-zero-mask.tsv','working-NA-mask.tsv','imputed-mask.tsv','R-session-info.txt','parameters.json')
        async def operation(query):
            args=(UUID(task['job_id']),UUID(task['attempt_id']),task['revision'],UUID(metadata['processed_dataset_version_id']),canonical(metadata),canonical(manifest),canonical(receipt),*(bodies[name] for name in order))
            row=await query('SELECT cyomics_app.preprocessing_publish('+','.join(['%s']*18)+')',args,write=True)
            if row is None or len(row)!=1 or row[0] is None:fail(409,'preprocessing_attempt_conflict')
            value=decode_capsule(row[0]);out=value.task()
            if (out['job_id']!=task['job_id'] or out['attempt_id']!=task['attempt_id'] or out['status']!='succeeded'
                or out['revision']!=task['revision']+1 or out['result']['processed_dataset_version_id']!=metadata['processed_dataset_version_id']
                or out['result']['manifest_sha256']!=bundle.manifest_sha256 or out['runtime']['receipt_sha256']!=digest(canonical(receipt))):fail()
            contract.version_value(metadata,task=out);return value
        return await self._run(budget,operation,writing=True)
