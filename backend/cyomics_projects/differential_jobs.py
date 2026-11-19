"""Exact saved selection -> immutable queued admission; no execution or publication.

Original Auth caller supplies subject. Original SavedInputAssembly + Owner/RLS
repositories validate sources; this module creates no permission or R runtime.
"""
from dataclasses import dataclass,asdict,field
from datetime import datetime,timezone
import hashlib,json
from uuid import UUID,uuid4
from .settings import Fault
from .validation import canonical_uuid,strict_json
from .quant_diff_http import selection_input
from .quant_diff_saved_input import Selection,SavedInputAssembly
from .quant_diff_input import BoundInput,Result
from . import preprocess_contract as pc

CONTRACT='differential_queued_v0.1'
METHOD={'R_version': '4.6.1', 'contract': 'limma_core_v0.1', 'contrast_direction': 'numerator_minus_denominator', 'imputation': 'none', 'input_binder': {'bytes': 13163, 'sha256': '3a67e6c41b9ded6407562fe4b92a7ee6a163d751f80f3309ec44d8e8f0576fee'}, 'input_scale': 'log2', 'library_files_sha256': '7ab763eb038759748400b1ff055f2d4ea2733fd81a4ded99de2169670f76ff56', 'limma_version': '3.68.5', 'native': {'bytes': 14488, 'sha256': '9c572a708d444e0ec91c59935bf9b5c3fd426a75fe2b7101b2a65f4783155283'}, 'normalization': 'none', 'output_contract': {'bytes': 10301, 'sha256': '3534ef4ddf70bf1830abed33ccf05ad8273941343edf225f44006872d44a4047'}, 'publication_registered': False, 'replicate_model': 'independent_biological', 'runner': {'bytes': 19031, 'sha256': '14a330bd1c1c84879ed8f7ad90bb48debc72449e5475402b18db6b0411a6d7d5'}, 'runtime_registered': False, 'script': {'bytes': 12103, 'sha256': '9807467ba2ae54efd5ef77e5c15dbe0525d205f4d2988ba24995ea932024c908'}, 'statmod_version': '1.5.2', 'supervisor': {'bytes': 30207, 'sha256': '6f7839b4a867cea8fd9aef63b684ac8c9dbf63dbc7a95bb289080e8aa8074a3e'}}
MAX_META=4*1024*1024
MAX_RESPONSE=32768
HISTORY_CONTRACT='differential_job_history_v0.1'
HISTORY_LIMIT=5
MAX_HISTORY_RESPONSE=HISTORY_LIMIT*MAX_RESPONSE+512
KEYS=frozenset(('request_id','processed_dataset_version_id','sample_design_version_id','sample_design_request_id','contrast','replicate_model'))
TASK_KEYS=frozenset(('schemaVersion','contract','job_id','project_id','request_id','request_sha256','binding_sha256','selection','source','method','input_manifest','status','revision','observation','created_at','started_at','finished_at','attempt_id','runtime','result','error'))
COLUMNS='job_id,project_id,owner_id,request_id,request_json,request_sha256,binding_sha256,processed_dataset_version_id,parent_dataset_version_id,file_id,sample_design_version_id,sample_design_request_id,quant_family,method_json,method_sha256,input_snapshot_json,input_snapshot_sha256,input_manifest_json,input_manifest_sha256,status,revision,created_at'
VERIFY=',octet_length(matrix_bytes),encode(sha256(matrix_bytes),\'hex\'),octet_length(design_bytes),encode(sha256(design_bytes),\'hex\'),octet_length(preset_bytes),encode(sha256(preset_bytes),\'hex\')'

def fail(status=503,code='differential_persisted_invalid'):raise Fault(status,code)
def canonical(value):return pc.canonical(value).decode('utf8')
def sha(text):return hashlib.sha256(text.encode('utf8')).hexdigest()
def document(text,cap):
    if type(text) is not str or not 1<=len(text.encode('utf8'))<=cap:fail()
    try:
        value=strict_json(text.encode('utf8'))
        if canonical(value)!=text:fail()
        return value
    except (ValueError,TypeError,UnicodeError,RecursionError):fail()
def ids(subject,project,*others):
    if not canonical_uuid(subject):fail(401,'unauthenticated')
    if not canonical_uuid(project) or any(not canonical_uuid(v) for v in others):fail(400,'invalid_differential_request')
def request_input(raw):
    if type(raw) is not bytes or not 1<=len(raw)<=8192:fail(400,'invalid_differential_request')
    try:body=strict_json(raw)
    except (ValueError,UnicodeError,RecursionError):fail(400,'invalid_differential_request')
    if type(body) is not dict or set(body)!=KEYS or not canonical_uuid(body['request_id']):fail(400,'invalid_differential_request')
    selection=selection_input(pc.canonical({k:v for k,v in body.items() if k!='request_id'}))
    return body,selection
def request_sha(subject,project,body):return sha(canonical({'owner_id':subject,'project_id':project,'request':body}))
def binding_sha(request_hash,snapshot_hash,manifest_hash,method_hash):
    return sha(canonical({'input_manifest_sha256':manifest_hash,'input_snapshot_sha256':snapshot_hash,
        'method_sha256':method_hash,'request_sha256':request_hash}))
def bytepin(body):return {'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()}

def snapshot(bound):
    if type(bound) is not BoundInput or bound.source_analysis_ready is not False:fail()
    # Existing BoundInput field names, original model dataclasses and original
    # source VersionV2 JSON are persisted; no replacement scientific report.
    result={name:asdict(getattr(bound,name)) if name in ('sample_source_binding','contrast') else getattr(bound,name)
        for name in ('source_analysis_ready','sample_source_binding','contrast','excluded_column_ordinals','numeric_design','design_rank','contrast_weights')}
    result.update(source_version_json=bound.source_version_json.decode('utf8'),
        family_columns=[asdict(x) for x in bound.family_columns],samples=[asdict(x) for x in bound.samples],
        rows=[asdict(x) for x in bound.rows],upstream_row_map_tsv=bytepin(bound.upstream_row_map_tsv),
        masks=[{'name':m.name,'source_sha256':m.source_sha256,'original_bytes':bytepin(m.original_bytes),
            'selected_bytes':bytepin(m.selected_bytes)} for m in bound.masks])
    text=canonical(result)
    if len(text.encode())>MAX_META:fail(422,'differential_input_too_large')
    return text

@dataclass(frozen=True,slots=True)
class StoredJob:
    public_json:str=field(repr=False)
    request_json:str=field(repr=False)
    request_sha256:str
    def public(self):return document(self.public_json,MAX_RESPONSE)
@dataclass(frozen=True,slots=True)
class Queued:
    stored:StoredJob
    created:bool
    commit_confirmed:bool=True
    teardown_confirmed:bool=True
@dataclass(frozen=True,slots=True)
class Admission:
    queued:Queued|None
    review:Result|None=None

def task_value(value,*,project=None,request_id=None,job_id=None):
    try:
        if type(value) is not dict or set(value)!=TASK_KEYS or value['schemaVersion']!=2 or type(value['schemaVersion']) is not int or value['contract']!=CONTRACT:fail()
        if not all(canonical_uuid(value[k]) for k in ('job_id','project_id','request_id')):fail()
        if project is not None and value['project_id']!=project or request_id is not None and value['request_id']!=request_id or job_id is not None and value['job_id']!=job_id:fail()
        if value['status']!='queued' or value['revision']!=1 or type(value['revision']) is not int or value['observation']!='confirmed':fail()
        if any(value[k] is not None for k in ('started_at','finished_at','attempt_id','runtime','result','error')):fail()
        pc.stamp(value['created_at'])
        if not all(pc.hashed(value[k]) for k in ('request_sha256','binding_sha256')):fail()
        body={**value['selection'],'request_id':value['request_id']};request_input(pc.canonical(body))
        pc.version_value(value['source'],project=value['project_id'],version=value['selection']['processed_dataset_version_id'])
        if type(value['method']) is not dict or value['method'].get('contract')!='limma_core_v0.1' or value['method'].get('runtime_registered') is not False or value['method'].get('publication_registered') is not False:fail()
        if set(value['input_manifest'])!={'matrix','design','preset'}:fail()
        for name,pin in value['input_manifest'].items():
            if set(pin)!={'bytes','sha256'} or type(pin['bytes']) is not int or not 1<=pin['bytes']<=26214400 or not pc.hashed(pin['sha256']):fail()
        if len(canonical(value).encode())>MAX_RESPONSE:fail()
        return value
    except (KeyError,TypeError,ValueError,UnicodeError,RecursionError):fail()

def history_value(value,*,project=None,processed=None):
    try:
        if (type(value) is not dict or set(value)!={'schemaVersion','contract','project_id','processed_dataset_version_id','jobs'}
                or type(value['schemaVersion']) is not int or value['schemaVersion']!=1 or value['contract']!=HISTORY_CONTRACT
                or not canonical_uuid(value['project_id']) or not canonical_uuid(value['processed_dataset_version_id'])):fail()
        if project is not None and value['project_id']!=project or processed is not None and value['processed_dataset_version_id']!=processed:fail()
        if type(value['jobs']) is not list or len(value['jobs'])>HISTORY_LIMIT:fail()
        previous=None;seen=set()
        for item in value['jobs']:
            task_value(item,project=value['project_id'])
            if item['selection']['processed_dataset_version_id']!=value['processed_dataset_version_id'] or item['job_id'] in seen:fail()
            key=(item['created_at'],item['job_id'])
            if previous is not None and not key<previous:fail()
            seen.add(item['job_id']);previous=key
        if len(canonical(value).encode('utf8'))>MAX_HISTORY_RESPONSE:fail()
        return value
    except (KeyError,TypeError,ValueError,UnicodeError,RecursionError):fail()

class DifferentialRepository:
    def __init__(self,pool=None):
        self.pool=pool;self._write_unknown=False;self._read_unknown=False
    async def _transaction(self,subject,budget,operation,*,writing=False):
        if self.pool is None:fail(503,'differential_unavailable')
        if self._read_unknown or writing and self._write_unknown:fail(504 if writing else 503,'differential_submission_unknown' if writing else 'differential_unavailable')
        from .raw_qc_repository import Transactions,_TransactionDisposition
        repository=self
        class OwnerTransaction(Transactions):
            def _disposition_unknown(self):
                if writing:repository._write_unknown=True
                else:repository._read_unknown=True
        disposition=_TransactionDisposition()
        try:
            result=await OwnerTransaction(self.pool).transaction(subject,budget,operation,writing=writing,_disposition=disposition)
            if disposition.uncertain or not disposition.commit_confirmed or not disposition.teardown_confirmed:
                if writing:self._write_unknown=True
                else:self._read_unknown=True
                fail(504 if writing else 503,'differential_submission_unknown' if writing else 'differential_unavailable')
            return result
        except BaseException as error:
            import asyncio
            if isinstance(error,asyncio.CancelledError):raise
            if disposition.uncertain or writing and self._write_unknown or not writing and self._read_unknown:
                fail(504 if writing else 503,'differential_submission_unknown' if writing else 'differential_unavailable')
            if isinstance(error,Fault):
                if error.code in ('raw_qc_unavailable','raw_qc_timeout','raw_qc_submission_unknown'):
                    fail(error.status,{'raw_qc_unavailable':'differential_unavailable','raw_qc_timeout':'differential_timeout','raw_qc_submission_unknown':'differential_submission_unknown'}[error.code])
                raise
            fail(504 if isinstance(error,TimeoutError) else 503,'differential_timeout' if isinstance(error,TimeoutError) else 'differential_unavailable')
    async def _owner(self,query,subject,project):
        row=await query('SELECT project_id FROM cyomics_app.projects WHERE owner_id=%s AND project_id=%s',(UUID(subject),UUID(project)))
        if row is None:fail(404,'differential_job_not_found')
        if len(row)!=1 or str(row[0])!=project:fail()
    def _decode(self,row,subject,project):
        if row is None or len(row)!=28:fail()
        r=dict(zip(COLUMNS.split(','),row[:22]))
        if str(r['owner_id'])!=subject or str(r['project_id'])!=project:fail(404,'differential_job_not_found')
        body=document(r['request_json'],8192);selection=Selection(**{k:v for k,v in body.items() if k!='request_id'})
        request_input(r['request_json'].encode());method=document(r['method_json'],8192)
        meta=document(r['input_snapshot_json'],MAX_META);manifest=document(r['input_manifest_json'],4096)
        if (request_sha(subject,project,body)!=r['request_sha256'] or sha(r['method_json'])!=r['method_sha256']
                or sha(r['input_snapshot_json'])!=r['input_snapshot_sha256'] or sha(r['input_manifest_json'])!=r['input_manifest_sha256']
                or binding_sha(r['request_sha256'],r['input_snapshot_sha256'],r['input_manifest_sha256'],r['method_sha256'])!=r['binding_sha256']):fail()
        source=document(meta['source_version_json'],32768)
        pc.version_value(source,project=project,version=str(r['processed_dataset_version_id']))
        if (selection.processed_dataset_version_id!=str(r['processed_dataset_version_id']) or source['parent_dataset_version_id']!=str(r['parent_dataset_version_id'])
                or source['file_id']!=str(r['file_id']) or source['quant_family']!=r['quant_family']
                or selection.sample_design_version_id!=str(r['sample_design_version_id']) or selection.sample_design_request_id!=str(r['sample_design_request_id'])
                or meta['sample_source_binding']['sample_design_version_id']!=selection.sample_design_version_id
                or meta['source_analysis_ready'] is not False or meta['design_rank']!=2 or meta['contrast_weights']!=[1,-1]):fail()
        for index,name in enumerate(('matrix','design','preset')):
            if row[22+index*2]!=manifest[name]['bytes'] or row[23+index*2]!=manifest[name]['sha256']:fail()
        if not isinstance(r['created_at'],datetime) or r['created_at'].utcoffset() is None:fail()
        value={'schemaVersion':2,'contract':CONTRACT,'job_id':str(r['job_id']),'project_id':project,'request_id':str(r['request_id']),
            'request_sha256':r['request_sha256'],'binding_sha256':r['binding_sha256'],'selection':{k:v for k,v in body.items() if k!='request_id'},
            'source':source,'method':method,'input_manifest':manifest,'status':r['status'],'revision':r['revision'],'observation':'confirmed',
            'created_at':r['created_at'].astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
            'started_at':None,'finished_at':None,'attempt_id':None,'runtime':None,'result':None,'error':None}
        task_value(value,project=project,request_id=body['request_id'])
        if str(r['request_id'])!=body['request_id']:fail()
        return StoredJob(canonical(value),r['request_json'],r['request_sha256'])
    async def find_request(self,subject,project,request_id,budget):
        ids(subject,project,request_id)
        async def operation(query):
            await self._owner(query,subject,project)
            row=await query('SELECT '+COLUMNS+VERIFY+' FROM cyomics_app.differential_jobs WHERE owner_id=%s AND project_id=%s AND request_id=%s',(UUID(subject),UUID(project),UUID(request_id)))
            return None if row is None else self._decode(row,subject,project)
        return await self._transaction(subject,budget,operation)
    async def read_request(self,subject,project,request_id,budget):
        stored=await self.find_request(subject,project,request_id,budget)
        if stored is None:fail(404,'differential_job_not_found')
        return stored.public()
    async def read_job(self,subject,project,job_id,budget):
        ids(subject,project,job_id)
        async def operation(query):
            await self._owner(query,subject,project)
            row=await query('SELECT '+COLUMNS+VERIFY+' FROM cyomics_app.differential_jobs WHERE owner_id=%s AND project_id=%s AND job_id=%s',(UUID(subject),UUID(project),UUID(job_id)))
            if row is None:fail(404,'differential_job_not_found')
            return self._decode(row,subject,project).public()
        return await self._transaction(subject,budget,operation)
    async def read_history(self,subject,project,processed_dataset_version_id,budget):
        ids(subject,project,processed_dataset_version_id)
        async def operation(query):
            await self._owner(query,subject,project)
            version=await query('SELECT processed_dataset_version_id FROM cyomics_app.processed_dataset_versions WHERE created_by=%s AND project_id=%s AND processed_dataset_version_id=%s',
                (UUID(subject),UUID(project),UUID(processed_dataset_version_id)))
            if version is None:fail(404,'differential_job_not_found')
            if len(version)!=1 or str(version[0])!=processed_dataset_version_id:fail()
            items=[];cursor=None
            # Original Transactions.query has fetchone semantics. At most five
            # bounded keyset reads preserve original driver rows and _decode;
            # no cursor/fetchall override, public pagination or raw output.
            for _ in range(HISTORY_LIMIT):
                clause='' if cursor is None else ' AND (created_at,job_id)<(%s,%s)'
                params=(UUID(subject),UUID(project),UUID(processed_dataset_version_id))+(cursor or ())
                row=await query('SELECT '+COLUMNS+VERIFY+' FROM cyomics_app.differential_jobs WHERE owner_id=%s AND project_id=%s AND processed_dataset_version_id=%s'+clause+' ORDER BY created_at DESC,job_id DESC LIMIT 1',params)
                if row is None:break
                value=task_value(self._decode(row,subject,project).public(),project=project)
                if value['selection']['processed_dataset_version_id']!=processed_dataset_version_id:fail()
                items.append(value);cursor=(row[21],row[0])
            return history_value({'schemaVersion':1,'contract':HISTORY_CONTRACT,'project_id':project,
                'processed_dataset_version_id':processed_dataset_version_id,'jobs':items},project=project,processed=processed_dataset_version_id)
        return await self._transaction(subject,budget,operation)

    async def enqueue(self,subject,project,body,bound,budget):
        ids(subject,project,body['request_id']);request_input(pc.canonical(body))
        if type(bound) is not BoundInput:fail()
        request_json=canonical(body);request_hash=request_sha(subject,project,body)
        source=document(bound.source_version_json.decode(),32768);pc.version_value(source,project=project,version=body['processed_dataset_version_id'])
        meta=snapshot(bound);meta_hash=sha(meta);manifest=canonical({name:bytepin(data) for name,data in (('matrix',bound.matrix_tsv),('design',bound.design_tsv),('preset',bound.preset_tsv))})
        method=canonical(METHOD);manifest_hash=sha(manifest);method_hash=sha(method)
        bind_hash=binding_sha(request_hash,meta_hash,manifest_hash,method_hash);created=False
        async def operation(query):
            nonlocal created
            await self._owner(query,subject,project)
            await query('SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s,47010))',(subject+':'+project,))
            prior=await query('SELECT '+COLUMNS+VERIFY+' FROM cyomics_app.differential_jobs WHERE owner_id=%s AND project_id=%s AND request_id=%s',(UUID(subject),UUID(project),UUID(body['request_id'])))
            if prior is not None:
                saved=self._decode(prior,subject,project)
                if saved.request_json!=request_json or saved.request_sha256!=request_hash:fail(409,'differential_request_conflict')
                return saved
            values=(uuid4(),UUID(project),UUID(subject),UUID(body['request_id']),request_json,request_hash,bind_hash,
                UUID(source['processed_dataset_version_id']),UUID(source['parent_dataset_version_id']),UUID(source['file_id']),
                UUID(body['sample_design_version_id']),UUID(body['sample_design_request_id']),source['quant_family'],method,method_hash,
                meta,meta_hash,manifest,manifest_hash,bound.matrix_tsv,bound.design_tsv,bound.preset_tsv)
            columns=COLUMNS.split(',')[:19]+['matrix_bytes','design_bytes','preset_bytes']
            row=await query('INSERT INTO cyomics_app.differential_jobs ('+','.join(columns)+') VALUES ('+','.join(['%s']*len(values))+') RETURNING '+COLUMNS+VERIFY,values,write=True)
            created=True;return self._decode(row,subject,project)
        saved=await self._transaction(subject,budget,operation,writing=True)
        return Queued(saved,created)

class DifferentialService:
    def __init__(self,assembly=None,repository=None):self.assembly=assembly;self.repository=repository
    async def submit(self,subject,project,raw,budget,*,before_enqueue=None):
        if self.assembly is None or self.repository is None:return None
        if type(self.assembly) is not SavedInputAssembly or type(self.repository) is not DifferentialRepository:fail(503,'differential_unavailable')
        body,selection=request_input(raw);ids(subject,project,body['request_id'])
        prior=await self.repository.find_request(subject,project,body['request_id'],budget)
        if prior is not None:
            if prior.request_json!=canonical(body) or prior.request_sha256!=request_sha(subject,project,body):fail(409,'differential_request_conflict')
            return Admission(Queued(prior,False))
        review=await self.assembly.assemble(subject,project,selection,budget)
        if type(review) is not Result:fail(503,'differential_unavailable')
        if not review.model_input_satisfied:
            if review.bound is not None or not review.reasons:fail()
            return Admission(None,review)
        if type(review.bound) is not BoundInput or review.reasons:fail()
        if before_enqueue is not None:
            if not callable(before_enqueue):fail(503,'differential_unavailable')
            await before_enqueue(body,review.bound) # After genuine binder, separately committed registration.
            budget.check()
        queued=await self.repository.enqueue(subject,project,body,review.bound,budget)
        return Admission(queued)
