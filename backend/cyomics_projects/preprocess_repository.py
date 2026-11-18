"""Default-off Owner-bound queued-only preprocessing repository.

Source/subject/execution are trusted server dependencies, never body authority.
No worker, process, source bytea, HTTP route, registration or publication here.
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
    def __init__(self,pool=None,execution_json=None,*,enabled=False):
        if type(enabled) is not bool:fail(503,'preprocessing_unavailable')
        self.pool=pool;self.enabled=enabled;self._locked=False;self._tx=None
        self._execution_json=None
        if execution_json is not None:
            try:
                if type(execution_json) is not bytes:fail()
                text=execution_json.decode('utf8');execution_value(text);self._execution_json=text
            except BaseException:fail(503,'preprocessing_unavailable')

    @property
    def quarantined(self):return self._locked

    def _ready(self,writing=False):
        if not self.enabled or self.pool is None or self._locked or (writing and self._execution_json is None):
            fail(503,'preprocessing_unavailable')

    @staticmethod
    def ids(subject,project,*others):
        if not identifier(subject):fail(401,'unauthenticated')
        if not identifier(project) or any(not identifier(v) for v in others):fail(400,'invalid_preprocessing_request')

    async def _run(self,subject,budget,operation,writing=False):
        self._ready(writing)
        try:budget.check()
        except asyncio.CancelledError:self._locked=True;raise
        except TimeoutError:fail(504,'preprocessing_timeout')
        except BaseException:fail(503,'preprocessing_unavailable')
        try:
            Base,Disposition=_transactions()
            if self._tx is None:
                parent=self
                class Adapter(Base):
                    def _disposition_unknown(self):parent._locked=True
                self._tx=Adapter(self.pool)
        except BaseException:fail(503,'preprocessing_unavailable')
        disposition=Disposition()
        try:
            value=await self._tx.transaction(subject,budget,operation,writing=writing,_disposition=disposition)
            if self._locked or disposition.uncertain or not disposition.commit_confirmed or not disposition.teardown_confirmed:
                self._locked=True
                fail(504 if writing else 503,'preprocessing_submission_unknown' if writing else 'preprocessing_unavailable')
            return value
        except asyncio.CancelledError:
            self._locked=True;raise
        except BaseException as error:
            if disposition.uncertain or self._locked:
                self._locked=True
                fail(504 if writing and disposition.commit_started else 503,
                     'preprocessing_submission_unknown' if writing and disposition.commit_started else 'preprocessing_unavailable')
            if isinstance(error,Fault):
                mapping={'raw_qc_unavailable':'preprocessing_unavailable','raw_qc_timeout':'preprocessing_timeout',
                         'raw_qc_submission_unknown':'preprocessing_submission_unknown'}
                if error.code in mapping:fail(error.status,mapping[error.code])
                if error.code in ('preprocessing_persisted_invalid','preprocessing_request_conflict',
                    'preprocessing_family_unavailable','preprocessing_flags_unavailable','preprocessing_job_not_found','dataset_not_found'):
                    raise
            fail(504 if isinstance(error,TimeoutError) else 503,
                 'preprocessing_timeout' if isinstance(error,TimeoutError) else 'preprocessing_unavailable')

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
            if not selected:fail(422,'preprocessing_family_unavailable')
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
            request=contract.request_input(canonical({'request_id':str(rid),'quant_family':family,'parameters':params}).encode('utf8'))
            if (request.parameters_bytes.decode('utf8')!=pjson or rsha!=scoped_request_hash(project,str(dataset),request)
                or bsha!=binding_hash(rsha,source,request,execution)
                or not isinstance(created,datetime) or created.utcoffset() is None):fail()
            return {'schemaVersion':1,'job_id':str(job),'request_id':str(rid),'request_sha256':rsha,
                'binding_sha256':bsha,'source':source,'quant_family':family,'parameters':request.parameters_value(),
                'parameters_sha256':request.parameters_sha256,'execution_sha256':digest(ejson),'status':'queued',
                'created_at':created.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                'result':None,'computation_performed':False,'worker_started':False,'execution_registered':False}
        except Fault:raise
        except BaseException:fail()

    async def enqueue(self,subject,project,dataset,raw_body,budget):
        # This contract is pure and precedes even transaction/driver creation.
        try:request=contract.request_input(raw_body)
        except contract.RequestError as error:fail(413 if error.code=='preprocessing_request_too_large' else 400,error.code)
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
                return self._decode(prior,subject,project,source)
            execution=execution_value(self._execution_json)
            binding=binding_hash(request_hash,source,request,execution)
            row=await query('INSERT INTO cyomics_app.preprocessing_jobs '
                '(job_id,project_id,dataset_version_id,file_id,owner_id,request_id,request_sha256,binding_sha256,source_json,quant_family,params_json,execution_json) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING '+COLUMNS,
                (uuid4(),UUID(project),UUID(dataset),UUID(source['file_id']),UUID(subject),UUID(request.request_id),
                 request_hash,binding,canonical(source),request.quant_family,request.parameters_bytes.decode('utf8'),self._execution_json),write=True)
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
        return self._decode(row,subject,project,source)
