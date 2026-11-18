"""Immutable classification persistence only. No HTTP/R/credential/approval path.

Only existing trusted Owner/Root caller supplies subject, genuine source/spec/
bundle and actual native receipt; none of these types or receipt flags is Auth.
"""
from dataclasses import dataclass
from datetime import datetime,timezone
from uuid import UUID,uuid4
from . import differential_jobs as admission,differential_worker_once as parent
from . import de_classification_contract as c,de_classification_runner as runner
from .settings import Budget,Fault
from .validation import canonical_uuid

CONTRACT='de_classification_version_v0.1'
TABLE='cyomics_app.derived_classification_versions'
ARTIFACT_COLUMNS=dict(zip(c.OUTPUTS,('classification_audit','classification_parameters','classification','selected_de','source_method_audit','source_test_set')))
COLUMNS=('derived_version_id,owner_id,project_id,request_id,request_payload_json,request_payload_sha256,'
    'publication_json,publication_sha256,source_result_id,source_manifest_sha256,source_binding_json,source_binding_sha256,'
    'parameters_bytes,parameters_sha256,core_json,metadata_json,metadata_sha256,manifest_json,manifest_sha256,'
    'native_receipt_json,native_receipt_sha256,configuration_sha256,status,members_evaluated,member_set_id,created_at')
VERIFY=','+','.join('octet_length('+col+'),encode(sha256('+col+"),'hex')" for col in ARTIFACT_COLUMNS.values())
META_KEYS=frozenset(('schemaVersion','contract','derived_version_id','request_id','project_id','source_result_id',
    'source_manifest_sha256','source_binding_sha256','source_binding','parameters','parameters_sha256','core','status',
    'DE_members_evaluated','counts','artifacts','manifest_sha256','member_set','created_at','created_by','source_analysis_ready'))
COUNT_KEYS=('Up','Down','NS','not_testable','not_evaluated','selected_DE_rows')

def fail(status=503,code='de_classification_persisted_invalid'):raise Fault(status,code)
def need(ok,code='de_classification_persisted_invalid'):
    if not ok:fail(503,code)
def canonical(v):return c.canonical(v).decode('utf8')
def digest(v):return c.sha(c.canonical(v))
def doc(text,cap):return admission.document(text,cap)
def body(value):
    if type(value) is memoryview:value=value.tobytes()
    need(type(value) is bytes);return value
def utc(value):
    need(isinstance(value,datetime) and value.utcoffset() is not None)
    return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
def core():
    return {'contract':c.CONTRACT,'script_pin':{'bytes':runner.SCRIPT_PIN.bytes,'sha256':runner.SCRIPT_PIN.sha256},
        'validator_pin':{'bytes':runner.CONTRACT_PIN.bytes,'sha256':runner.CONTRACT_PIN.sha256}}
def parameter_values(spec):return dict(c.table(spec.body,('key','value'),4))
def request_payload(subject,project,request,binding,spec):
    return {'owner_id':subject,'project_id':project,'request_id':request,'source_result_id':binding['source_result_id'],
        'source_manifest_sha256':binding['source_manifest_sha256'],'source_binding_sha256':digest(binding),
        'parameters_sha256':c.sha(spec.body),'core':core()}
def publication(intent_sha,manifest_sha,receipt_sha,configuration_sha):
    return {'request_payload_sha256':intent_sha,'manifest_sha256':manifest_sha,
        'native_receipt_sha256':receipt_sha,'configuration_sha256':configuration_sha}

def validate_native(proof,binding,spec,manifest_sha,status,evaluated):
    """Consistency of actual trusted caller's receipt, never execution authority."""
    need(type(proof) is dict and proof.get('contract')=='de_classification_receipt_v0.1'
        and type(proof.get('schemaVersion')) is int and proof.get('schemaVersion')==1)
    need(all(canonical_uuid(proof.get(k)) for k in ('job_id','attempt_id','epoch_id')))
    need(proof['job_id']!=binding['source_job_id'] and proof['attempt_id']!=binding['source_attempt_id'])
    need(proof.get('source_binding')==binding and proof.get('source_binding_sha256')==digest(binding)
        and proof.get('parameters')==c.pin(spec.body) and proof.get('manifest_sha256')==manifest_sha
        and proof.get('script')==core()['script_pin'] and proof.get('validator')==core()['validator_pin']
        and proof.get('supervisor')=={'bytes':runner.SHARED_PIN.bytes,'sha256':runner.SHARED_PIN.sha256}
        and proof.get('native')=={'bytes':runner.NATIVE_PIN.bytes,'sha256':runner.NATIVE_PIN.sha256})
    need(proof.get('classification_status')==status and proof.get('DE_members_evaluated') is evaluated
        and proof.get('exit_code')==({'CLASSIFIED':0,'BLOCKED_SOURCE_NO_TESTABLE_ROWS':3,'BLOCKED_SOURCE_REVIEW_REQUIRED':4}[status])
        and type(proof.get('exit_code')) is int and proof.get('all_six_outputs_verified') is True
        and proof.get('close_confirmed') is True and proof.get('published') is False and proof.get('statistics_recomputed') is False)
    need(all(admission.pc.hashed(proof.get(k)) for k in ('configuration_sha256','process_policy_sha256','environment_sha256')))
    adapter=proof.get('adapter');need(type(adapter) is dict and set(adapter)=={'bytes','sha256'}
        and type(adapter['bytes']) is int and adapter['bytes']>0 and admission.pc.hashed(adapter['sha256']))
    owned=proof.get('process_control',{}).get('owned_tasks',{})
    need(owned.get('cleanup',{}).get('complete_for_observed_identities') is True and owned.get('uncertainty')==[])
    need(proof.get('resource_supervisor_reused') is True and proof.get('contract_per_file_bytes')==c.MAX_FILE
        and proof.get('contract_total_bytes')==c.MAX_TOTAL)
    return proof

def validate_dto(meta):
    need(type(meta) is dict and set(meta)==META_KEYS and type(meta['schemaVersion']) is int
        and meta['schemaVersion']==1 and meta['contract']==CONTRACT and meta['source_analysis_ready'] is False)
    need(all(canonical_uuid(meta[k]) for k in ('derived_version_id','request_id','project_id','source_result_id','created_by')))
    need(all(admission.pc.hashed(meta[k]) for k in ('source_manifest_sha256','source_binding_sha256','parameters_sha256','manifest_sha256')))
    binding=meta['source_binding'];need(type(binding) is dict and digest(binding)==meta['source_binding_sha256']
        and binding.get('owner_id')==meta['created_by'] and binding.get('project_id')==meta['project_id']
        and binding.get('source_result_id')==meta['source_result_id'] and binding.get('source_manifest_sha256')==meta['source_manifest_sha256'])
    need(meta['core']==core() and type(meta['parameters']) is dict)
    need(set(meta['parameters'])==set(c.PARAM_KEYS) and all(type(v) is str for v in meta['parameters'].values()))
    need(type(meta['DE_members_evaluated']) is bool and meta['status'] in
        ('CLASSIFIED','BLOCKED_SOURCE_NO_TESTABLE_ROWS','BLOCKED_SOURCE_REVIEW_REQUIRED'))
    yes=meta['status']=='CLASSIFIED';need(meta['DE_members_evaluated'] is yes)
    counts=meta['counts'];need(type(counts) is dict and set(counts)==set(COUNT_KEYS))
    for k in COUNT_KEYS:
        if not yes and k in ('Up','Down','NS','selected_DE_rows'):need(counts[k] is None)
        else:need(type(counts[k]) is int and 0<=counts[k]<=10000)
    if yes:need(counts['selected_DE_rows']==counts['Up']+counts['Down'] and counts['not_evaluated']==0)
    artifacts=meta['artifacts'];need(type(artifacts) is list and len(artifacts)==6
        and tuple(a['name'] for a in artifacts)==c.OUTPUTS and digest(artifacts)==meta['manifest_sha256'])
    for a in artifacts:need(set(a)=={'name','bytes','sha256'} and type(a['bytes']) is int and 0<a['bytes']<=c.MAX_FILE and admission.pc.hashed(a['sha256']))
    need(sum(a['bytes'] for a in artifacts)<=c.MAX_TOTAL)
    member=meta['member_set']
    if yes:
        selected=next(a for a in artifacts if a['name']=='selected-DE.tsv')
        need(type(member) is dict and set(member)=={'member_set_id','derived_version_id','selected_artifact_sha256','member_count'}
            and canonical_uuid(member['member_set_id']) and member['derived_version_id']==meta['derived_version_id']
            and member['selected_artifact_sha256']==selected['sha256'] and type(member['member_count']) is int
            and member['member_count']==counts['selected_DE_rows'])
    else:need(member is None)
    admission.pc.stamp(meta['created_at']);need(len(canonical(meta).encode())<=32768)
    return meta

@dataclass(frozen=True,slots=True)
class Config:
    enabled:bool=False
    verify:object=None # Existing reviewed Root/service context; never a wire flag.

class ClassificationRepository:
    def __init__(self,pool=None,config=None):
        self.owner=admission.DifferentialRepository(pool);self.config=config
    @property
    def write_unknown(self):return self.owner._write_unknown
    def _write_check(self):
        cfg=self.config
        need(type(cfg) is Config and type(cfg.enabled) is bool and cfg.enabled and callable(cfg.verify),'de_write_unavailable')
        need(cfg.verify() is None,'de_write_unavailable')
    async def _parent(self,query,subject,project,source_id,binding):
        suffix=','+','.join('octet_length('+v+'),encode(sha256('+v+"),'hex')" for v in parent.ARTIFACT_COLUMNS.values())
        row=await query('SELECT '+parent.RESULT_COLUMNS+suffix+' FROM cyomics_app.differential_results WHERE owner_id=%s AND project_id=%s AND result_id=%s',
            (UUID(subject),UUID(project),UUID(source_id)))
        if row is None:fail(404,'de_classification_source_not_found')
        need(len(row)==22);meta=doc(row[5],32768);manifest=doc(row[7],8192);proof=doc(row[9],131072)
        need(str(row[0])==source_id and str(row[3])==subject and str(row[4])==project
            and meta['result_id']==source_id and meta['job_id']==str(row[1]) and meta['attempt_id']==str(row[2])
            and meta['project_id']==project and meta['created_by']==subject and meta['created_at']==utc(row[11])
            and admission.sha(row[5])==row[6] and admission.sha(row[7])==row[8] and admission.sha(row[9])==row[10]
            and meta['receipt_sha256']==row[10] and meta['manifest_sha256']==row[8] and meta['artifacts']==manifest)
        jobrow=await query('SELECT '+admission.COLUMNS+admission.VERIFY+' FROM cyomics_app.differential_jobs WHERE owner_id=%s AND project_id=%s AND job_id=%s',
            (UUID(subject),UUID(project),row[1]))
        need(jobrow is not None);job=self.owner._decode(jobrow,subject,project).public()
        parent.validate_metadata(meta,job);parent.validate_receipt(proof,meta)
        run=await query('SELECT job_id,registration_id FROM cyomics_app.differential_job_runs WHERE job_id=%s AND attempt_id=%s AND status=%s AND result_id=%s',
            (row[1],row[2],'succeeded',row[0]))
        need(run is not None and len(run)==2 and str(run[1])==meta['registration_id'])
        snapshot=doc(jobrow[15],admission.MAX_META)
        expected={'schemaVersion':1,'contract':'de_parent_binding_v0.1','owner_id':subject,'project_id':project,
            'source_result_id':source_id,'source_job_id':str(row[1]),'source_attempt_id':str(row[2]),
            'source_registration_id':meta['registration_id'],'source_request_id':meta['request_id'],
            'source_contrast_id':snapshot['contrast']['contrast_id'],'admission_binding_sha256':meta['admission_binding_sha256'],
            'input_snapshot_sha256':jobrow[16],'source_metadata_sha256':row[6],'source_receipt_sha256':row[10],
            'source_manifest_sha256':row[8],'source_artifacts':manifest,'source_status':meta['scientific_status'],
            'source_warning_count':meta['warning_count'],'source_method_sha256':digest(meta['method']),
            'contrast_direction':'numerator_minus_denominator'}
        need(binding==expected,'de_parent_binding_mismatch')
        for i,a in enumerate(manifest):need((row[12+2*i],row[13+2*i])==(a['bytes'],a['sha256']))
        return manifest
    async def _get(self,query,subject,project,*,request_id=None,version_id=None,member_id=None):
        key,value=('request_id',request_id) if request_id is not None else ('derived_version_id',version_id) if version_id is not None else ('member_set_id',member_id)
        row=await query('SELECT '+COLUMNS+VERIFY+' FROM '+TABLE+' WHERE owner_id=%s AND project_id=%s AND '+key+'=%s',
            (UUID(subject),UUID(project),UUID(value)))
        if row is None:return None
        need(len(row)==38);r=dict(zip(COLUMNS.split(','),row[:26]))
        need(str(r['owner_id'])==subject and str(r['project_id'])==project)
        meta=validate_dto(doc(r['metadata_json'],32768));binding=doc(r['source_binding_json'],16384)
        params=c.parameters(body(r['parameters_bytes']));manifest=doc(r['manifest_json'],8192)
        proof=doc(r['native_receipt_json'],262144);intent=doc(r['request_payload_json'],16384);published=doc(r['publication_json'],8192)
        need(meta['derived_version_id']==str(r['derived_version_id']) and meta['request_id']==str(r['request_id'])
            and meta['project_id']==project and meta['created_by']==subject and meta['source_result_id']==str(r['source_result_id'])
            and meta['created_at']==utc(r['created_at']) and meta['parameters']==parameter_values(params)
            and meta['core']==doc(r['core_json'],8192) and meta['status']==r['status']
            and meta['DE_members_evaluated'] is r['members_evaluated'])
        need(meta['member_set'] is None and r['member_set_id'] is None or meta['member_set'] is not None and meta['member_set']['member_set_id']==str(r['member_set_id']))
        need(admission.sha(r['metadata_json'])==r['metadata_sha256'] and admission.sha(r['manifest_json'])==r['manifest_sha256']
            and meta['artifacts']==manifest and meta['manifest_sha256']==r['manifest_sha256']
            and admission.sha(r['source_binding_json'])==r['source_binding_sha256']==meta['source_binding_sha256'] and meta['source_binding']==binding
            and meta['source_manifest_sha256']==r['source_manifest_sha256']==binding['source_manifest_sha256']
            and c.sha(params.body)==r['parameters_sha256']==meta['parameters_sha256']
            and admission.sha(r['native_receipt_json'])==r['native_receipt_sha256']
            and proof.get('configuration_sha256')==r['configuration_sha256'])
        validate_native(proof,binding,params,meta['manifest_sha256'],meta['status'],meta['DE_members_evaluated'])
        need(intent==request_payload(subject,project,meta['request_id'],binding,params)
            and digest(intent)==r['request_payload_sha256'] and published==publication(r['request_payload_sha256'],r['manifest_sha256'],r['native_receipt_sha256'],r['configuration_sha256'])
            and digest(published)==r['publication_sha256'])
        await self._parent(query,subject,project,meta['source_result_id'],binding)
        for i,a in enumerate(manifest):need((row[26+2*i],row[27+2*i])==(a['bytes'],a['sha256']))
        copies={'classification-parameters.tsv':c.pin(params.body),'source-method-audit.tsv':next(a for a in binding['source_artifacts'] if a['name']=='method-audit.tsv'),
            'source-test-set.tsv':next(a for a in binding['source_artifacts'] if a['name']=='test-set.tsv')}
        for a in manifest:
            if a['name'] in copies:need((a['bytes'],a['sha256'])==(copies[a['name']]['bytes'],copies[a['name']]['sha256']))
        return meta,r
    async def save_verified(self,subject,project,request_id,source,spec,bundle,native_receipt,budget):
        if self.owner._write_unknown:fail(504,'de_classification_submission_unknown')
        cfg=self.config
        if cfg is None or type(cfg) is Config and (cfg.enabled is False or cfg.verify is None):return None
        self._write_check();admission.ids(subject,project,request_id)
        need(type(source) is c.ClassificationSource and type(spec) is c.Parameters and type(bundle) is c.DerivedClassificationBundle)
        binding=c.verify_source(source,check=self._write_check)
        if (binding['owner_id'],binding['project_id'])!=(subject,project):fail(404,'de_classification_source_not_found')
        outputs={a.name:a.body for a in bundle.artifacts}
        need(c.validate(source,spec,bundle.exit_code,outputs,check=self._write_check)==bundle)
        need(type(native_receipt) is bytes and 0<len(native_receipt)<=262144)
        proof=c.document(native_receipt,262144);validate_native(proof,binding,spec,bundle.manifest_sha256,bundle.status,bundle.DE_members_evaluated)
        intent=request_payload(subject,project,request_id,binding,spec);intent_sha=digest(intent)
        pub=publication(intent_sha,bundle.manifest_sha256,c.sha(native_receipt),proof['configuration_sha256']);pub_sha=digest(pub)
        async def operation(query):
            self._write_check();await self.owner._owner(query,subject,project)
            await query('SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s,47012))',(subject+':'+project+':'+request_id,))
            await self._parent(query,subject,project,binding['source_result_id'],binding)
            prior=await self._get(query,subject,project,request_id=request_id)
            if prior is not None:
                meta,r=prior
                if r['request_payload_sha256']!=intent_sha:fail(409,'de_classification_request_conflict')
                if r['publication_sha256']!=pub_sha:fail(409,'de_classification_publication_conflict')
                return meta
            clock=await query('SELECT statement_timestamp()');need(clock is not None and len(clock)==1)
            version=str(uuid4());member_id=str(uuid4()) if bundle.DE_members_evaluated else None
            audit=dict(c.table(outputs['classification-audit.tsv'],('key','value'),len(c.AUDIT_KEYS)))
            counts={k:None if audit[k]=='NA' else int(audit[k]) for k in COUNT_KEYS}
            manifest=[{'name':a.name,'bytes':a.bytes,'sha256':a.sha256} for a in bundle.artifacts]
            selected=next(a for a in manifest if a['name']=='selected-DE.tsv')
            meta={'schemaVersion':1,'contract':CONTRACT,'derived_version_id':version,'request_id':request_id,'project_id':project,
                'source_result_id':binding['source_result_id'],'source_manifest_sha256':binding['source_manifest_sha256'],
                'source_binding_sha256':source.binding_sha256,'source_binding':binding,'parameters':parameter_values(spec),'parameters_sha256':c.sha(spec.body),
                'core':core(),'status':bundle.status,'DE_members_evaluated':bundle.DE_members_evaluated,'counts':counts,
                'artifacts':manifest,'manifest_sha256':bundle.manifest_sha256,'member_set':None if member_id is None else
                {'member_set_id':member_id,'derived_version_id':version,'selected_artifact_sha256':selected['sha256'],'member_count':counts['selected_DE_rows']},
                'created_at':utc(clock[0]),'created_by':subject,'source_analysis_ready':False}
            validate_dto(meta);self._write_check()
            values=(UUID(version),UUID(subject),UUID(project),UUID(request_id),canonical(intent),intent_sha,canonical(pub),pub_sha,
                UUID(binding['source_result_id']),binding['source_manifest_sha256'],canonical(binding),source.binding_sha256,
                spec.body,c.sha(spec.body),canonical(core()),canonical(meta),digest(meta),canonical(manifest),bundle.manifest_sha256,
                native_receipt.decode('utf8'),c.sha(native_receipt),proof['configuration_sha256'],bundle.status,bundle.DE_members_evaluated,
                None if member_id is None else UUID(member_id),clock[0],*(a.body for a in bundle.artifacts))
            await query('INSERT INTO '+TABLE+' ('+COLUMNS+','+','.join(ARTIFACT_COLUMNS.values())+') VALUES ('+','.join(['%s']*len(values))+')',values,write=True)
            result=await self._get(query,subject,project,version_id=version);need(result is not None)
            return result[0]
        return await self.owner._transaction(subject,budget,operation,writing=True)
    async def _read(self,subject,project,budget,*,request_id=None,version_id=None,member_id=None,artifact=None):
        if sum(v is not None for v in (request_id,version_id,member_id))!=1:fail(400,'invalid_de_classification_selection')
        admission.ids(subject,project,*(v for v in (request_id,version_id,member_id) if v is not None))
        if artifact is not None and artifact not in ARTIFACT_COLUMNS:fail(404,'de_classification_artifact_not_found')
        async def operation(query):
            await self.owner._owner(query,subject,project)
            found=await self._get(query,subject,project,request_id=request_id,version_id=version_id,member_id=member_id)
            if found is None:fail(404,'de_classification_version_not_found')
            meta,r=found
            if artifact is None and member_id is None:return meta
            name='selected-DE.tsv' if member_id is not None else artifact
            row=await query('SELECT '+ARTIFACT_COLUMNS[name]+' FROM '+TABLE+' WHERE owner_id=%s AND project_id=%s AND derived_version_id=%s',
                (UUID(subject),UUID(project),r['derived_version_id']))
            need(row is not None and len(row)==1);data=body(row[0]);expected=next(a for a in meta['artifacts'] if a['name']==name)
            need(c.pin(data)=={'bytes':expected['bytes'],'sha256':expected['sha256']})
            if member_id is not None:
                need(meta['member_set'] is not None and meta['member_set']['member_set_id']==member_id)
                return {'member_set':meta['member_set'],'bytes':data}
            return data
        return await self.owner._transaction(subject,budget,operation)
    async def read_request(self,subject,project,request_id,budget):return await self._read(subject,project,budget,request_id=request_id)
    async def read_version(self,subject,project,derived_version_id,budget):return await self._read(subject,project,budget,version_id=derived_version_id)
    async def read_artifact(self,subject,project,derived_version_id,name,budget):return await self._read(subject,project,budget,version_id=derived_version_id,artifact=name)
    async def read_member_set(self,subject,project,member_set_id,budget):return await self._read(subject,project,budget,member_id=member_set_id)
