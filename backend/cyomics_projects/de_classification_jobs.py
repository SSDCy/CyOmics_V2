"""Immutable Owner classification intent only; queued is historical admission.
No HTTP, Auth bypass, runtime approval, native execution, claim or worker.
"""
from dataclasses import dataclass,field
from uuid import UUID,uuid4
from . import differential_jobs as original,differential_worker_once as parent
from . import de_classification_contract as c,de_classification_repository as saved
from .settings import Fault
from .validation import canonical_uuid,strict_json

CONTRACT='de_classification_queued_v0.1'
TABLE='cyomics_app.de_classification_jobs'
COLUMNS=('classification_job_id,project_id,owner_id,request_id,request_json,request_payload_json,request_payload_sha256,'
    'source_result_id,source_manifest_sha256,source_binding_json,source_binding_sha256,parameters_bytes,parameters_sha256,core_json,status,created_at')
KEYS=frozenset(('schemaVersion','contract','classification_job_id','project_id','request_id','created_by','source_result_id',
    'source_manifest_sha256','source_binding_sha256','parameters','parameters_sha256','core','created_at','status','observation'))

def fail(status=503,code='de_classification_persisted_invalid'):raise Fault(status,code)
def need(ok):
    if not ok:fail()
def request_input(raw):
    try:
        if type(raw) is not bytes or not 0<len(raw)<=8192:raise ValueError()
        value=strict_json(raw)
        if type(value) is not dict or set(value)!={'request_id','source_result_id','parameters'}:raise ValueError()
        if not all(canonical_uuid(value[k]) for k in ('request_id','source_result_id')):raise ValueError()
        spec=parameter_input(value['parameters'])
        return value,spec
    except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):fail(400,'invalid_de_classification_request')
def parameter_input(value):
    if type(value) is not dict or set(value)!=set(c.PARAM_KEYS) or any(type(v) is not str or any(x in v for x in ('\t','\n','\r','\0')) for v in value.values()):raise ValueError()
    body=('key\tvalue\n'+''.join(k+'\t'+value[k]+'\n' for k in c.PARAM_KEYS)).encode('utf8')
    return c.parameters(body) # Original grammar/range; preserve every token.
def dto(value):
    try:
        need(type(value) is dict and set(value)==KEYS and type(value['schemaVersion']) is int and value['schemaVersion']==1 and value['contract']==CONTRACT)
        need(all(canonical_uuid(value[k]) for k in ('classification_job_id','project_id','request_id','created_by','source_result_id')))
        need(all(original.pc.hashed(value[k]) for k in ('source_manifest_sha256','source_binding_sha256','parameters_sha256')))
        need(c.sha(parameter_input(value['parameters']).body)==value['parameters_sha256'] and value['core']==saved.core())
        need(value['status']=='queued' and value['observation']=='confirmed')
        original.pc.stamp(value['created_at']);need(len(saved.canonical(value).encode())<=8192)
        return value
    except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):fail()

@dataclass(frozen=True,slots=True)
class StoredJob:
    public_json:str=field(repr=False)
    request_json:str=field(repr=False)
    request_payload_sha256:str
    def public(self):return dto(saved.doc(self.public_json,8192))
@dataclass(frozen=True,slots=True)
class Queued:
    stored:StoredJob
    created:bool
    commit_confirmed:bool=True
    teardown_confirmed:bool=True

class ClassificationAdmissionRepository:
    def __init__(self,pool=None,*,enabled=False):
        if type(enabled) is not bool:fail(503,'de_classification_jobs_unavailable')
        self.owner=original.DifferentialRepository(pool);self.proof=saved.ClassificationRepository(pool);self.enabled=enabled
    @property
    def write_unknown(self):return self.owner._write_unknown
    async def _transaction(self,subject,project,budget,operation,*,writing=False):
        try:return await self.owner._transaction(subject,budget,operation,writing=writing)
        except Fault as error:
            if error.code=='differential_job_not_found':fail(404,'de_classification_source_not_found' if writing else 'de_classification_job_not_found')
            if error.code=='differential_submission_unknown':fail(504,'de_classification_submission_unknown')
            if error.code=='differential_timeout':fail(504,'de_classification_submission_unknown' if writing else 'de_classification_read_timeout')
            if error.code=='differential_unavailable':fail(503,'de_classification_jobs_unavailable')
            if error.code=='differential_persisted_invalid':fail()
            raise
    def _ids(self,subject,project,*values):
        if not canonical_uuid(subject):fail(401,'unauthenticated')
        if not canonical_uuid(project) or any(not canonical_uuid(v) for v in values):fail(400,'invalid_de_classification_request')
    async def _source(self,query,subject,project,source_id):
        # Reconstruct the original 20-key binding from immutable rows, never
        # from client proof. Original _parent then verifies full hashes, five
        # physical pins, receipt, job snapshot and succeeded run in SAME tx.
        suffix=','+','.join('octet_length('+v+'),encode(sha256('+v+"),'hex')" for v in parent.ARTIFACT_COLUMNS.values())
        row=await query('SELECT '+parent.RESULT_COLUMNS+suffix+' FROM cyomics_app.differential_results WHERE owner_id=%s AND project_id=%s AND result_id=%s',
            (UUID(subject),UUID(project),UUID(source_id)))
        if row is None:fail(404,'de_classification_source_not_found')
        need(len(row)==22);meta=saved.doc(row[5],32768);manifest=saved.doc(row[7],8192)
        jobrow=await query('SELECT '+original.COLUMNS+original.VERIFY+' FROM cyomics_app.differential_jobs WHERE owner_id=%s AND project_id=%s AND job_id=%s',
            (UUID(subject),UUID(project),row[1]))
        need(jobrow is not None);self.owner._decode(jobrow,subject,project)
        snapshot=saved.doc(jobrow[15],original.MAX_META)
        binding={'schemaVersion':1,'contract':'de_parent_binding_v0.1','owner_id':subject,'project_id':project,
            'source_result_id':source_id,'source_job_id':str(row[1]),'source_attempt_id':str(row[2]),
            'source_registration_id':meta['registration_id'],'source_request_id':meta['request_id'],
            'source_contrast_id':snapshot['contrast']['contrast_id'],'admission_binding_sha256':meta['admission_binding_sha256'],
            'input_snapshot_sha256':jobrow[16],'source_metadata_sha256':row[6],'source_receipt_sha256':row[10],
            'source_manifest_sha256':row[8],'source_artifacts':manifest,'source_status':meta['scientific_status'],
            'source_warning_count':meta['warning_count'],'source_method_sha256':saved.digest(meta['method']),
            'contrast_direction':'numerator_minus_denominator'}
        await self.proof._parent(query,subject,project,source_id,binding)
        return binding
    async def _decode(self,query,row,subject,project):
        need(len(row)==16);v=dict(zip(COLUMNS.split(','),row))
        need(str(v['owner_id'])==subject and str(v['project_id'])==project)
        try:body,spec=request_input(v['request_json'].encode())
        except Fault:fail()
        need(saved.canonical(body)==v['request_json'] and body['request_id']==str(v['request_id']) and body['source_result_id']==str(v['source_result_id']))
        binding=saved.doc(v['source_binding_json'],16384);intent=saved.doc(v['request_payload_json'],16384)
        need(saved.digest(binding)==v['source_binding_sha256'] and binding.get('source_result_id')==body['source_result_id']
            and binding.get('owner_id')==subject and binding.get('project_id')==project and binding.get('source_manifest_sha256')==v['source_manifest_sha256'])
        need(type(v['parameters_bytes']) in (bytes,memoryview));parameters=bytes(v['parameters_bytes'])
        need(parameters==spec.body and c.sha(parameters)==v['parameters_sha256'] and saved.doc(v['core_json'],8192)==saved.core())
        need(intent==saved.request_payload(subject,project,body['request_id'],binding,spec) and saved.digest(intent)==v['request_payload_sha256'])
        await self.proof._parent(query,subject,project,body['source_result_id'],binding)
        value=dto({'schemaVersion':1,'contract':CONTRACT,'classification_job_id':str(v['classification_job_id']),
            'project_id':project,'request_id':body['request_id'],'created_by':subject,'source_result_id':body['source_result_id'],
            'source_manifest_sha256':v['source_manifest_sha256'],'source_binding_sha256':v['source_binding_sha256'],
            'parameters':body['parameters'],'parameters_sha256':v['parameters_sha256'],'core':saved.core(),
            'created_at':saved.utc(v['created_at']),'status':v['status'],'observation':'confirmed'})
        return StoredJob(saved.canonical(value),v['request_json'],v['request_payload_sha256'])
    async def enqueue(self,subject,project,raw,budget):
        # Sticky write disposition MUST precede disabled/missing configuration.
        if self.write_unknown:fail(504,'de_classification_submission_unknown')
        if self.enabled is False or self.owner.pool is None:return None
        if self.enabled is not True:fail(503,'de_classification_jobs_unavailable')
        self._ids(subject,project);body,spec=request_input(raw);created=False
        async def operation(query):
            nonlocal created
            await self.owner._owner(query,subject,project)
            await query('SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s,47013))',
                (subject+':'+project+':'+body['request_id'],))
            prior=await query('SELECT '+COLUMNS+' FROM '+TABLE+' WHERE owner_id=%s AND project_id=%s AND request_id=%s',
                (UUID(subject),UUID(project),UUID(body['request_id'])))
            if prior is not None:
                stored=await self._decode(query,prior,subject,project)
                if stored.request_json!=saved.canonical(body):fail(409,'de_classification_request_conflict')
                return stored
            binding=await self._source(query,subject,project,body['source_result_id'])
            intent=saved.request_payload(subject,project,body['request_id'],binding,spec)
            values=(uuid4(),UUID(project),UUID(subject),UUID(body['request_id']),saved.canonical(body),saved.canonical(intent),saved.digest(intent),
                UUID(body['source_result_id']),binding['source_manifest_sha256'],saved.canonical(binding),saved.digest(binding),
                spec.body,c.sha(spec.body),saved.canonical(saved.core()))
            columns=','.join(COLUMNS.split(',')[:14])
            row=await query('INSERT INTO '+TABLE+' ('+columns+') VALUES ('+','.join(['%s']*len(values))+') RETURNING '+COLUMNS,values,write=True)
            need(row is not None and str(row[0])==str(values[0]));stored=await self._decode(query,row,subject,project)
            created=True;return stored
        stored=await self._transaction(subject,project,budget,operation,writing=True)
        return Queued(stored,created)
    async def _read(self,subject,project,value,budget,key):
        if self.enabled is not True or self.owner.pool is None:fail(503,'de_classification_jobs_unavailable')
        self._ids(subject,project,value)
        async def operation(query):
            await self.owner._owner(query,subject,project)
            row=await query('SELECT '+COLUMNS+' FROM '+TABLE+' WHERE owner_id=%s AND project_id=%s AND '+key+'=%s',
                (UUID(subject),UUID(project),UUID(value)))
            if row is None:fail(404,'de_classification_job_not_found')
            return (await self._decode(query,row,subject,project)).public()
        return await self._transaction(subject,project,budget,operation)
    async def read_request(self,subject,project,request_id,budget):return await self._read(subject,project,request_id,budget,'request_id')
    async def read_job(self,subject,project,classification_job_id,budget):return await self._read(subject,project,classification_job_id,budget,'classification_job_id')
