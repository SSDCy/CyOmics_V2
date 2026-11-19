"""Durable one-attempt reservation and verified termination; never runs R.
Only a first INSERT with confirmed commit/teardown mints Claim. Read recovery
never mints one, including after an unknown disposition or a new Root scope.
"""
from dataclasses import dataclass,field
from uuid import UUID
from . import differential_jobs as original,de_classification_jobs as admission
from . import de_classification_repository as saved,de_classification_runner as runner
from .settings import Fault
from .validation import canonical_uuid

TABLE='cyomics_app.de_classification_job_runs'
COLUMNS=('classification_job_id,attempt_id,owner_id,project_id,request_id,epoch_id,scope_sha256,request_payload_sha256,'
    'configuration_sha256,status,observation,claimed_at,finished_at,derived_version_id,error_code,failure_facts_json,failure_facts_sha256')


def fail(status=503,code='de_classification_once_persisted_invalid'):raise Fault(status,code)
def need(ok,code='de_classification_once_persisted_invalid'):
    if not ok:fail(503,code)

@dataclass(frozen=True,slots=True)
class Observation:
    state:str
    reason:str
    job:admission.StoredJob
    run_json:str|None=field(default=None,repr=False)
    result_json:str|None=field(default=None,repr=False)
    def run(self):return None if self.run_json is None else saved.doc(self.run_json,32768)
    def result(self):return None if self.result_json is None else saved.validate_dto(saved.doc(self.result_json,32768))
    @property
    def scientific_status(self):
        value=self.result();return None if value is None else value['status']

@dataclass(frozen=True,slots=True)
class Claim:
    job:admission.StoredJob
    run_json:str=field(repr=False)
    config:runner.Config=field(repr=False)
    _issuer:object=field(repr=False)
    def run(self):return saved.doc(self.run_json,32768)

class OnceRepository:
    def __init__(self,pool=None,*,enabled=False):
        if type(enabled) is not bool:fail(503,'de_classification_once_unavailable')
        self.owner=original.DifferentialRepository(pool)
        self.admission=admission.ClassificationAdmissionRepository(pool,enabled=True)
        self.results=saved.ClassificationRepository(pool)
        self.enabled=enabled;self._issuer=object()
    @property
    def write_unknown(self):return self.owner._write_unknown
    def _write_available(self):
        if self.write_unknown:fail(504,'de_classification_once_unknown')
        return self.enabled is True and self.owner.pool is not None
    def _config(self,config,scope):
        need(type(config) is runner.Config and config.enabled is True and callable(config.verify),'de_classification_once_unavailable')
        need(all(canonical_uuid(getattr(config,k)) for k in ('owner_id','project_id','job_id','attempt_id','epoch_id','source_result_id')))
        need(all(original.pc.hashed(v) for v in (scope,config.source_binding_sha256,config.parameters_sha256)))
        need(config.verify() is None,'de_classification_once_unavailable')
        digest=runner.ClassificationRunner(config)._failure_binding()['configuration_sha256']
        need(original.pc.hashed(digest),'de_classification_once_unavailable')
        return digest
    async def _transaction(self,subject,budget,operation,*,writing=False):
        try:return await self.owner._transaction(subject,budget,operation,writing=writing)
        except Fault as error:
            if error.code=='differential_job_not_found':fail(404,'de_classification_job_not_found')
            if error.code=='differential_submission_unknown':fail(504,'de_classification_once_unknown')
            if error.code=='differential_timeout':fail(504,'de_classification_once_unknown' if writing else 'de_classification_read_timeout')
            if error.code=='differential_unavailable':fail(503,'de_classification_once_unavailable')
            raise
    async def _job(self,query,subject,project,*,job_id=None,request_id=None):
        key,value=('classification_job_id',job_id) if job_id is not None else ('request_id',request_id)
        row=await query('SELECT '+admission.COLUMNS+' FROM '+admission.TABLE+' WHERE owner_id=%s AND project_id=%s AND '+key+'=%s',
            (UUID(subject),UUID(project),UUID(value)))
        if row is None:fail(404,'de_classification_job_not_found')
        job=await self.admission._decode(query,row,subject,project)
        return job,dict(zip(admission.COLUMNS.split(','),row))
    async def _run(self,query,subject,project,job_id):
        row=await query('SELECT '+COLUMNS+' FROM '+TABLE+' WHERE owner_id=%s AND project_id=%s AND classification_job_id=%s',
            (UUID(subject),UUID(project),UUID(job_id)))
        if row is None:return None
        need(len(row)==17);run=dict(zip(COLUMNS.split(','),row))
        need(all(canonical_uuid(str(run[k])) for k in ('classification_job_id','attempt_id','owner_id','project_id','request_id','epoch_id')))
        need(str(run['owner_id'])==subject and str(run['project_id'])==project and str(run['classification_job_id'])==job_id)
        need(all(original.pc.hashed(run[k]) for k in ('scope_sha256','request_payload_sha256','configuration_sha256')))
        need(run['observation']=='confirmed' and run['status'] in ('claimed','finished'))
        saved.utc(run['claimed_at'])
        if run['status']=='claimed':need(all(run[k] is None for k in ('finished_at','derived_version_id','error_code','failure_facts_json','failure_facts_sha256')))
        else:
            need(run['finished_at'] is not None and run['finished_at']>=run['claimed_at']);saved.utc(run['finished_at'])
            success=run['derived_version_id'] is not None
            need((success and canonical_uuid(str(run['derived_version_id'])) and all(run[k] is None for k in ('error_code','failure_facts_json','failure_facts_sha256')))
                or (not success and run['error_code'] in ('input_integrity_failed','runner_failed') and type(run['failure_facts_json']) is str
                    and saved.digest(saved.doc(run['failure_facts_json'],32768))==run['failure_facts_sha256']))
            if not success:self._failure(saved.doc(run['failure_facts_json'],32768),run)
        return run
    async def _result(self,query,subject,project,job,row):
        result=await self.results._get(query,subject,project,request_id=job.public()['request_id'])
        if result is None:return None
        meta,stored=result
        if stored['request_payload_sha256']!=row['request_payload_sha256'] or stored['request_payload_json']!=row['request_payload_json']:
            fail(409,'de_classification_request_conflict')
        need(stored['source_binding_json']==row['source_binding_json'] and bytes(stored['parameters_bytes'])==bytes(row['parameters_bytes'])
            and stored['core_json']==row['core_json'])
        return result
    def _proof(self,result,run):
        meta,stored=result;proof=saved.doc(stored['native_receipt_json'],262144)
        # Original _get already validates all_six, close_confirmed, typed member
        # semantics, original native/config/core/source/parameter proof + pins.
        need((proof['job_id'],proof['attempt_id'],proof['epoch_id'],proof['configuration_sha256'])==
            (str(run['classification_job_id']),str(run['attempt_id']),str(run['epoch_id']),run['configuration_sha256']),
            'de_classification_attempt_binding_mismatch')
        return meta
    def _failure(self,facts,run):
        need(type(facts) is dict and type(facts.get('schemaVersion')) is int and facts.get('schemaVersion')==1 and facts.get('cleanup_known') is True)
        need((facts.get('job_id'),facts.get('attempt_id'),facts.get('configuration_sha256'))==
            (str(run['classification_job_id']),str(run['attempt_id']),run['configuration_sha256']))
        need(all(facts.get(k) is True for k in ('child_reaped','observed_tasks_stopped','owned_fds_closed','timer_disabled','signal_handler_restored')))
    def _wire(self,run):
        if run is None:return None
        out=dict(run)
        for k in ('classification_job_id','attempt_id','owner_id','project_id','request_id','epoch_id','derived_version_id'):
            if out[k] is not None:out[k]=str(out[k])
        for k in ('claimed_at','finished_at'):
            if out[k] is not None:out[k]=saved.utc(out[k])
        return saved.canonical(out)
    def _observe(self,job,run,result):
        if run is not None:
            need(run['request_payload_sha256']==job.request_payload_sha256 and str(run['request_id'])==job.public()['request_id'])
        if run is None:
            state,reason=('finished','existing_result') if result else ('queued','unclaimed')
        elif run['status']=='claimed':
            if result:self._proof(result,run)
            state,reason=('reconciliation_pending','result_saved_finish_unconfirmed') if result else ('claimed','attempt_consumed')
        else:
            state,reason='finished','confirmed'
            if run['derived_version_id'] is None:need(result is None)
            else:need(result is not None and result[0]['derived_version_id']==str(run['derived_version_id']));self._proof(result,run)
        return Observation(state,reason,job,self._wire(run),None if result is None else saved.canonical(result[0]))
    async def claim(self,config,scope_sha256,budget):
        if not self._write_available():return None
        if config is None or type(config) is runner.Config and config.enabled is False:return None
        configuration=self._config(config,scope_sha256)
        async def operation(query):
            await self.owner._owner(query,config.owner_id,config.project_id)
            await query('SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s,47014))',(config.job_id,))
            job,row=await self._job(query,config.owner_id,config.project_id,job_id=config.job_id)
            need((str(row['source_result_id']),row['source_binding_sha256'],row['parameters_sha256'])==
                (config.source_result_id,config.source_binding_sha256,config.parameters_sha256))
            result=await self._result(query,config.owner_id,config.project_id,job,row)
            run=await self._run(query,config.owner_id,config.project_id,config.job_id)
            if result is not None:return self._observe(job,run,result)
            if run is not None:fail(409,'de_classification_attempt_consumed')
            need(self._config(config,scope_sha256)==configuration)
            values=(UUID(config.job_id),UUID(config.attempt_id),UUID(config.owner_id),UUID(config.project_id),UUID(job.public()['request_id']),
                UUID(config.epoch_id),scope_sha256,job.request_payload_sha256,configuration)
            inserted=await query('INSERT INTO '+TABLE+' ('+','.join(COLUMNS.split(',')[:9])+') VALUES ('+','.join(['%s']*len(values))+') RETURNING '+COLUMNS,values,write=True)
            need(inserted is not None and len(inserted)==17 and str(inserted[0])==config.job_id and str(inserted[1])==config.attempt_id)
            run=await self._run(query,config.owner_id,config.project_id,config.job_id);need(run is not None and run['status']=='claimed')
            return job,self._wire(run)
        result=await self._transaction(config.owner_id,budget,operation,writing=True)
        if type(result) is Observation:return result
        # Mint ONLY outside the confirmed original transaction/teardown return.
        job,run_json=result
        return Claim(job,run_json,config,self._issuer)
    async def _read(self,subject,project,value,budget,key):
        if self.enabled is not True or self.owner.pool is None:fail(503,'de_classification_once_unavailable')
        self.admission._ids(subject,project,value)
        async def operation(query):
            await self.owner._owner(query,subject,project)
            if key=='attempt_id':
                selected=await query('SELECT classification_job_id FROM '+TABLE+' WHERE owner_id=%s AND project_id=%s AND attempt_id=%s',
                    (UUID(subject),UUID(project),UUID(value)))
                if selected is None:fail(404,'de_classification_attempt_not_found')
                need(len(selected)==1);job,row=await self._job(query,subject,project,job_id=str(selected[0]))
            else:job,row=await self._job(query,subject,project,request_id=value)
            run=await self._run(query,subject,project,job.public()['classification_job_id'])
            if key=='attempt_id':need(run is not None and str(run['attempt_id'])==value)
            return self._observe(job,run,await self._result(query,subject,project,job,row))
        return await self._transaction(subject,budget,operation)
    async def read_request(self,subject,project,request_id,budget):return await self._read(subject,project,request_id,budget,'request_id')
    async def read_attempt(self,subject,project,attempt_id,budget):return await self._read(subject,project,attempt_id,budget,'attempt_id')
    def _claim(self,claim):
        need(type(claim) is Claim and claim._issuer is self._issuer,'de_classification_claim_required')
        run=claim.run();cfg=claim.config
        need(self._config(cfg,run['scope_sha256'])==run['configuration_sha256'])
        need((cfg.job_id,cfg.attempt_id,cfg.epoch_id,cfg.owner_id,cfg.project_id)==tuple(run[k] for k in
            ('classification_job_id','attempt_id','epoch_id','owner_id','project_id')))
        return run,cfg
    async def _finish(self,claim,budget,*,error_code=None,facts=None):
        if not self._write_available():return None
        before,config=self._claim(claim)
        if error_code is not None:
            need(error_code in ('input_integrity_failed','runner_failed') and type(facts) is runner.shared.FailureFacts)
            failure=facts.snapshot();self._failure(failure,before)
        async def operation(query):
            await self.owner._owner(query,config.owner_id,config.project_id)
            await query('SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s,47014))',(config.job_id,))
            job,admitted=await self._job(query,config.owner_id,config.project_id,job_id=config.job_id)
            run=await self._run(query,config.owner_id,config.project_id,config.job_id)
            if run is None or self._wire(run)!=claim.run_json:fail(409,'de_classification_attempt_consumed')
            result=await self._result(query,config.owner_id,config.project_id,job,admitted)
            if error_code is None:
                need(result is not None,'de_classification_result_required');meta=self._proof(result,run)
                version=UUID(meta['derived_version_id']);failure_json=failure_sha=None
            else:
                need(result is None,'de_classification_result_already_saved');version=None
                failure_json=saved.canonical(failure);failure_sha=saved.digest(failure)
            self._claim(claim)
            out=await query('UPDATE '+TABLE+" SET status='finished',finished_at=statement_timestamp(),derived_version_id=%s,error_code=%s,failure_facts_json=%s,failure_facts_sha256=%s WHERE classification_job_id=%s AND attempt_id=%s AND epoch_id=%s AND scope_sha256=%s AND owner_id=%s AND project_id=%s AND status='claimed' RETURNING "+COLUMNS,
                (version,error_code,failure_json,failure_sha,UUID(config.job_id),UUID(config.attempt_id),UUID(config.epoch_id),before['scope_sha256'],UUID(config.owner_id),UUID(config.project_id)),write=True)
            need(out is not None and len(out)==17 and str(out[0])==config.job_id and str(out[1])==config.attempt_id)
            after=await self._run(query,config.owner_id,config.project_id,config.job_id);need(after is not None and after['status']=='finished')
            return self._observe(job,after,result)
        return await self._transaction(config.owner_id,budget,operation,writing=True)
    async def finish(self,claim,budget):return await self._finish(claim,budget)
    async def finish_failed(self,claim,error_code,facts,budget):return await self._finish(claim,budget,error_code=error_code,facts=facts)
