"""One registered job/native attempt; original Owner transactions/binder/runner.

The existing server factory injects its Owner pool/assembly and existing Root
control verifier. No browser subject/credentials/path/permission flag or queue
daemon. Admission010 is immutable;011 run/result rows overlay its lifecycle.
"""
from dataclasses import dataclass,field
from datetime import datetime,timezone
import hashlib,json
from uuid import UUID,uuid4
from . import differential_jobs as admission,limma_bound_contract as contract
from .quant_diff_saved_input import SavedInputAssembly,Selection
from .quant_diff_input import BoundInput,Result
from .limma_bound_runner import LimmaBoundRunner,Config as RunnerConfig,RunResult
from .log2_matrix_runner import Log2RunFailure,FailureFacts
from .settings import Budget,Fault
from .validation import canonical_uuid

REG_COLUMNS='registration_id,owner_id,project_id,request_id,scope_sha256,epoch_id,method_json,approved_at,expires_at,max_jobs,profile_json'
RUN_COLUMNS='job_id,attempt_id,registration_id,owner_id,project_id,scope_sha256,status,observation,revision,started_at,finished_at,result_id,error_code'
RESULT_COLUMNS='result_id,job_id,attempt_id,owner_id,project_id,metadata_json,metadata_sha256,manifest_json,manifest_sha256,receipt_json,receipt_sha256,created_at'
ARTIFACT_COLUMNS=dict(zip(contract.OUTPUTS,('r_session','method_audit','results','sample_audit','test_set')))

def need(ok,code='differential_worker_integrity_failed'):
    if not ok:raise Fault(503,code)
def now():return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
def canonical(value):return admission.canonical(value)
def digest(value):return admission.sha(canonical(value))
def pin(body):return contract.pin(body)
def text_bytes(value):
    if type(value) is memoryview:value=value.tobytes()
    need(type(value) is bytes);return value

@dataclass(frozen=True,slots=True)
class OnceConfig:
    owner_id:str
    project_id:str
    registration_id:str
    scope_sha256:str
    epoch_id:str
    job_id:str
    native_directory_pins:tuple=field(repr=False)
    verify:object=field(repr=False) # Existing separately reviewed Root check.

@dataclass(frozen=True,slots=True)
class Claim:
    registration:dict
    run:dict
    job:admission.StoredJob
    input_snapshot_json:str=field(repr=False)
    inputs:tuple=field(repr=False)

@dataclass(frozen=True,slots=True)
class Outcome:
    status:str
    reason:str
    job_id:str|None=None
    attempt_id:str|None=None
    result_id:str|None=None

class OnceRepository:
    """Original Owner transaction boundary, no new pool/role/secret loader."""
    def __init__(self,pool=None):
        self.pool=pool;self.owner=admission.DifferentialRepository(pool)
    @property
    def quarantined(self):return self.owner._write_unknown or self.owner._read_unknown
    async def claim(self,config,attempt_id,budget):
        admission.ids(config.owner_id,config.project_id,config.job_id,config.registration_id,attempt_id)
        async def operation(query):
            await self.owner._owner(query,config.owner_id,config.project_id)
            await query('SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s,47011))',(config.registration_id,))
            row=await query('SELECT '+REG_COLUMNS+' FROM cyomics_app.differential_runtime_registrations WHERE registration_id=%s AND owner_id=%s AND project_id=%s AND scope_sha256=%s AND approved_at<clock_timestamp() AND expires_at>clock_timestamp()',
                (UUID(config.registration_id),UUID(config.owner_id),UUID(config.project_id),config.scope_sha256))
            need(row is not None and len(row)==11,'differential_registration_unavailable')
            reg=dict(zip(REG_COLUMNS.split(','),row))
            need(str(reg['epoch_id'])==config.epoch_id and reg['max_jobs']==1 and reg['method_json']==canonical(admission.METHOD))
            profile=admission.document(reg['profile_json'],32768)
            need(type(profile) is dict,'differential_claim_policy_invalid')
            # Absent policy preserves ONLY the old non-null exact request mode.
            # Null/unknown policy never turns a registration into a wildcard.
            policy=profile.get('claim_policy','exact_request' if reg['request_id'] is not None else None)
            need((policy=='exact_request' and reg['request_id'] is not None)
                or (policy=='one_new_job_in_scope' and reg['request_id'] is None),'differential_claim_policy_invalid')
            prior=await query('SELECT job_id FROM cyomics_app.differential_job_runs WHERE registration_id=%s OR job_id=%s',
                (UUID(config.registration_id),UUID(config.job_id)))
            need(prior is None,'differential_attempt_consumed')
            request_filter=' AND request_id=%s' if policy=='exact_request' else ''
            request_params=(UUID(str(reg['request_id'])),) if policy=='exact_request' else ()
            row=await query('SELECT '+admission.COLUMNS+admission.VERIFY+',matrix_bytes,design_bytes,preset_bytes FROM cyomics_app.differential_jobs WHERE owner_id=%s AND project_id=%s AND job_id=%s'+request_filter+' AND created_at>%s',
                (UUID(config.owner_id),UUID(config.project_id),UUID(config.job_id),*request_params,reg['approved_at']))
            need(row is not None and len(row)==31,'differential_new_admission_required')
            job=self.owner._decode(row[:28],config.owner_id,config.project_id)
            # The unique run's immutable job FK binds its actual request. Root
            # supplies one explicit job, never a queue scan or latest lookup.
            need(policy=='one_new_job_in_scope' or job.public()['request_id']==str(reg['request_id']))
            need(row[13]==reg['method_json'])
            frozen=tuple(text_bytes(value) for value in row[28:31])
            for name,body in zip(('matrix','design','preset'),frozen):need(pin(body)==job.public()['input_manifest'][name])
            row_run=await query('INSERT INTO cyomics_app.differential_job_runs (job_id,attempt_id,registration_id,owner_id,project_id,scope_sha256) VALUES (%s,%s,%s,%s,%s,%s) RETURNING '+RUN_COLUMNS,
                (UUID(config.job_id),UUID(attempt_id),UUID(config.registration_id),UUID(config.owner_id),UUID(config.project_id),config.scope_sha256),write=True)
            need(row_run is not None and len(row_run)==13)
            run=dict(zip(RUN_COLUMNS.split(','),row_run));need(run['status']=='running' and run['revision']==2 and str(run['attempt_id'])==attempt_id)
            return Claim(reg,run,job,row[15],frozen)
        return await self.owner._transaction(config.owner_id,budget,operation,writing=True)

    async def _running(self,query,claim):
        r=claim.run;g=claim.registration
        row=await query('SELECT '+RUN_COLUMNS+' FROM cyomics_app.differential_job_runs WHERE job_id=%s AND attempt_id=%s AND registration_id=%s AND owner_id=%s AND project_id=%s AND status=%s AND observation=%s AND revision=2',
            (r['job_id'],r['attempt_id'],r['registration_id'],r['owner_id'],r['project_id'],'running','confirmed'))
        need(row is not None and len(row)==13,'differential_attempt_conflict')
        live=await query('SELECT registration_id FROM cyomics_app.differential_runtime_registrations WHERE registration_id=%s AND owner_id=%s AND project_id=%s AND scope_sha256=%s AND expires_at>clock_timestamp()',
            (r['registration_id'],r['owner_id'],r['project_id'],r['scope_sha256']))
        need(live is not None and str(live[0])==str(g['registration_id']),'differential_registration_unavailable')
        return row

    async def publish(self,claim,metadata,bundle,receipt,budget):
        need(type(claim) is Claim and type(bundle) is contract.Bundle and type(receipt) is dict)
        validate_metadata(metadata,claim.job.public())
        validate_receipt(receipt,metadata,str(claim.registration['epoch_id']))
        manifest=[{'name':a.name,'bytes':a.bytes,'sha256':a.sha256} for a in bundle.artifacts]
        need(tuple(a.name for a in bundle.artifacts)==contract.OUTPUTS and digest(manifest)==bundle.manifest_sha256
            and metadata['manifest_sha256']==bundle.manifest_sha256 and metadata['receipt_sha256']==digest(receipt))
        for a in bundle.artifacts:need(pin(a.body)=={'bytes':a.bytes,'sha256':a.sha256})
        r=claim.run
        need(metadata['attempt_id']==str(r['attempt_id']) and metadata['registration_id']==str(r['registration_id'])
            and metadata['created_by']==str(r['owner_id']))
        async def operation(query):
            await self.owner._owner(query,str(r['owner_id']),str(r['project_id']));await self._running(query,claim)
            values=(UUID(metadata['result_id']),r['job_id'],r['attempt_id'],r['owner_id'],r['project_id'],
                canonical(metadata),digest(metadata),canonical(manifest),bundle.manifest_sha256,canonical(receipt),digest(receipt),
                datetime.fromisoformat(metadata['created_at'].replace('Z','+00:00')),*(a.body for a in bundle.artifacts))
            cols=RESULT_COLUMNS+','+','.join(ARTIFACT_COLUMNS.values())
            result=await query('INSERT INTO cyomics_app.differential_results ('+cols+') VALUES ('+','.join(['%s']*len(values))+') RETURNING result_id',values,write=True)
            need(result is not None and str(result[0])==metadata['result_id'])
            out=await query("UPDATE cyomics_app.differential_job_runs SET status='succeeded',observation='confirmed',revision=3,finished_at=statement_timestamp(),result_id=%s WHERE job_id=%s AND attempt_id=%s AND status='running' AND observation='confirmed' AND revision=2 RETURNING "+RUN_COLUMNS,
                (UUID(metadata['result_id']),r['job_id'],r['attempt_id']),write=True)
            need(out is not None and out[6]=='succeeded' and out[8]==3 and str(out[11])==metadata['result_id'])
            return metadata
        return await self.owner._transaction(str(r['owner_id']),budget,operation,writing=True)

    async def finish_failed(self,claim,error_code,budget):
        need(error_code in ('input_integrity_failed','runner_failed'))
        r=claim.run
        async def operation(query):
            await self._running(query,claim)
            row=await query("UPDATE cyomics_app.differential_job_runs SET status='failed',observation='confirmed',revision=3,finished_at=statement_timestamp(),error_code=%s WHERE job_id=%s AND attempt_id=%s AND status='running' AND revision=2 RETURNING job_id",
                (error_code,r['job_id'],r['attempt_id']),write=True)
            need(row is not None and str(row[0])==str(r['job_id']))
        return await self.owner._transaction(str(r['owner_id']),budget,operation,writing=True)

def validate_metadata(meta,job):
    need(type(meta) is dict and meta.get('contract')=='differential_result_v0.1' and meta.get('schemaVersion')==1)
    for key in ('result_id','job_id','attempt_id','registration_id','project_id','created_by'):need(canonical_uuid(meta.get(key)))
    need(meta['job_id']==job['job_id'] and meta['project_id']==job['project_id']
        and meta['created_by']==job['source']['created_by']
        and meta['request_id']==job['request_id'] and meta['admission_binding_sha256']==job['binding_sha256']
        and meta['selection']==job['selection'] and meta['method']==job['method']
        and meta['input_manifest']==job['input_manifest'] and meta['source_analysis_ready'] is False
        and type(meta['exit_code']) is int and meta['exit_code'] in (0,3,4) and meta['stored'] is True
        and type(meta['warning_count']) is int and meta['warning_count']>=0 and type(meta['review_required']) is bool)
    expected={0:'COMPUTED_CANDIDATE',3:'NO_TESTABLE_ROWS',4:'REVIEW_REQUIRED_LIMMA_WARNING'}
    need(meta['scientific_status']==expected[meta['exit_code']] and meta['review_required']==(meta['warning_count']>0 or meta['exit_code']==4))
    need(type(meta['artifacts']) is list and len(meta['artifacts'])==5 and tuple(x['name'] for x in meta['artifacts'])==contract.OUTPUTS
        and digest(meta['artifacts'])==meta['manifest_sha256'])
    need(len(canonical(meta).encode())<=32768)

def validate_receipt(receipt,meta,epoch=None):
    """Bind the original runner receipt to this immutable attempt/result."""
    need(type(receipt) is dict and receipt.get('close_confirmed') is True
        and receipt.get('job_id')==meta['job_id'] and receipt.get('attempt_id')==meta['attempt_id']
        and canonical_uuid(receipt.get('epoch_id')) and (epoch is None or receipt['epoch_id']==epoch)
        and receipt.get('script')==meta['method']['script'] and receipt.get('native')==meta['method']['native']
        and receipt.get('library_manifest_sha256')==meta['method']['library_files_sha256']
        and receipt.get('inputs')==meta['input_manifest'] and receipt.get('all_five_outputs_verified') is True
        and receipt.get('manifest_sha256')==meta['manifest_sha256'] and receipt.get('published') is False
        and receipt.get('source_analysis_ready') is False
        and receipt.get('exit_code')==meta['exit_code'] and receipt.get('status')==meta['scientific_status']
        and receipt.get('warning_count')==meta['warning_count'] and receipt.get('review_required')==meta['review_required'])
    owned=receipt.get('process_control',{}).get('owned_tasks',{})
    need(owned.get('cleanup',{}).get('complete_for_observed_identities') is True and owned.get('uncertainty')==[]
        and receipt.get('configuration_sha256')==digest({'epoch_id':receipt['epoch_id'],'job_id':meta['job_id'],
            'attempt_id':meta['attempt_id'],'inputs':meta['input_manifest']}))

class OnceWorker:
    def __init__(self,config=None,repository=None,assembly=None,roots=None):
        self.config=config;self.repository=repository;self.assembly=assembly;self.roots=roots
        self.quarantined=False;self.used=False
    def _check(self):
        c=self.config
        need(type(c) is OnceConfig and all(canonical_uuid(getattr(c,k)) for k in ('owner_id','project_id','registration_id','epoch_id','job_id'))
            and admission.pc.hashed(c.scope_sha256) and callable(c.verify))
        need(c.verify() is None);self.roots.verify()
    async def run_once(self,*,cancelled=lambda:False):
        if self.config is None or self.repository is None or self.assembly is None or self.roots is None:return Outcome('off','worker_unconfigured')
        if self.used or self.quarantined:return Outcome('reconciliation_pending','attempt_consumed')
        self.used=True
        from .raw_qc_checkpoint import Journal,WorkerBusy
        state={};journal=Journal(self.roots);claim=None
        def outcome(status,reason,result=None):return Outcome(status,reason,state.get('job_id'),state.get('attempt_id'),result)
        def unknown(reason):
            self.quarantined=True
            try:journal.save({**state,'phase':'reconciliation','reason':reason})
            except BaseException:pass
            return outcome('reconciliation_pending',reason)
        try:
            need(type(self.repository) is OnceRepository and type(self.assembly) is SavedInputAssembly)
            self._check()
            with journal.locked():
                old=journal.load()
                if old['phase']!='idle':
                    self.quarantined=True
                    return Outcome('reconciliation_pending','existing_journal_no_retry',old.get('job_id'),old.get('attempt_id'),old.get('result_id'))
                state={'phase':'claim_intent','job_id':self.config.job_id,'attempt_id':str(uuid4())};journal.save(state)
                try:claim=await self.repository.claim(self.config,state['attempt_id'],Budget())
                except BaseException:return unknown('claim_disposition_unknown')
                state={**state,'phase':'claimed','binding_sha256':claim.job.public()['binding_sha256']};journal.save(state)
                job=claim.job.public();selection=Selection(**job['selection'])
                # Exact saved IDs/request only. The REAL binder issues the typed
                # BoundInput; no dataclass hydration/forged seal/parameter rebuild.
                try:
                    review=await self.assembly.assemble(self.config.owner_id,self.config.project_id,selection,Budget(50))
                    need(type(review) is Result and review.model_input_satisfied and type(review.bound) is BoundInput)
                    bound=review.bound
                    need(admission.snapshot(bound)==claim.input_snapshot_json
                        and (bound.matrix_tsv,bound.design_tsv,bound.preset_tsv)==claim.inputs,'differential_frozen_input_mismatch')
                except Fault as error:
                    if error.status!=404 and error.code not in ('differential_frozen_input_mismatch','differential_worker_integrity_failed'):
                        return unknown('source_read_or_teardown_unknown')
                    journal.save({**state,'phase':'commit_intent','target':'failed'})
                    try:
                        self._check();await self.repository.finish_failed(claim,'input_integrity_failed',Budget())
                    except BaseException:return unknown('source_failure_commit_unknown')
                    journal.save({**state,'phase':'confirmed','target':'failed'});return outcome('confirmed','input_integrity_failed')
                need(job['method']==admission.METHOD and canonical(job['method'])==claim.registration['method_json'])
                state={**state,'phase':'source_ready'};journal.save(state);self._check()
                cfg=RunnerConfig(True,self.config.epoch_id,job['job_id'],state['attempt_id'],self.config.native_directory_pins,self.config.verify)
                executor=LimmaBoundRunner(cfg)
                state={**state,'phase':'executing'};journal.save(state)
                try:result=executor.run(bound,cancelled=cancelled)
                except Log2RunFailure as error:
                    facts=error.facts.snapshot() if type(error.facts) is FailureFacts else {}
                    known=(facts.get('cleanup_known') is True and facts.get('job_id')==job['job_id']
                        and facts.get('attempt_id')==state['attempt_id'] and facts.get('configuration_sha256')==executor._failure_binding()['configuration_sha256'])
                    if not known:return unknown('native_cleanup_or_execution_unknown')
                    journal.save({**state,'phase':'commit_intent','target':'failed'})
                    try:
                        self._check();await self.repository.finish_failed(claim,'runner_failed',Budget())
                    except BaseException:return unknown('failure_commit_unknown')
                    journal.save({**state,'phase':'confirmed','target':'failed'});return outcome('confirmed','failed')
                need(type(result) is RunResult and executor.quarantined is False)
                receipt=result.receipt
                need(receipt.get('close_confirmed') is True and receipt.get('job_id')==job['job_id']
                    and receipt.get('attempt_id')==state['attempt_id'] and receipt.get('epoch_id')==self.config.epoch_id)
                need(receipt.get('script')==job['method']['script'] and receipt.get('native')==job['method']['native']
                    and receipt.get('library_manifest_sha256')==job['method']['library_files_sha256'])
                need(receipt.get('inputs')==job['input_manifest'] and receipt.get('all_five_outputs_verified') is True
                    and receipt.get('manifest_sha256')==result.bundle.manifest_sha256 and receipt.get('published') is False)
                owned=receipt.get('process_control',{}).get('owned_tasks',{})
                need(owned.get('cleanup',{}).get('complete_for_observed_identities') is True and owned.get('uncertainty')==[]
                    and receipt.get('configuration_sha256')==executor._failure_binding()['configuration_sha256'])
                bodies={a.name:a.body for a in result.bundle.artifacts}
                bundle=contract.validate(bound,result.bundle.exit_code,bodies,check=self._check)
                need(bundle==result.bundle and receipt.get('exit_code')==bundle.exit_code)
                meta={'schemaVersion':1,'contract':'differential_result_v0.1','result_id':str(uuid4()),'job_id':job['job_id'],
                    'attempt_id':state['attempt_id'],'registration_id':self.config.registration_id,'project_id':self.config.project_id,
                    'created_by':self.config.owner_id,'created_at':now(),'request_id':job['request_id'],
                    'admission_binding_sha256':job['binding_sha256'],'selection':job['selection'],'method':job['method'],
                    'input_manifest':job['input_manifest'],'source_analysis_ready':False,'exit_code':bundle.exit_code,
                    'scientific_status':bundle.status,'warning_count':bundle.warning_count,'review_required':bundle.warning_count>0 or bundle.exit_code==4,
                    'artifacts':[{'name':a.name,'bytes':a.bytes,'sha256':a.sha256} for a in bundle.artifacts],
                    'manifest_sha256':bundle.manifest_sha256,'receipt_sha256':digest(receipt),'stored':True}
                validate_metadata(meta,job)
                validate_receipt(receipt,meta,self.config.epoch_id)
                private=journal.private_record(state['attempt_id'],{'result_id':meta['result_id'],'metadata_sha256':digest(meta),
                    'manifest_sha256':bundle.manifest_sha256,'receipt_sha256':digest(receipt)})
                state={**state,'phase':'result_ready','private_record':private,'result_id':meta['result_id']};journal.save(state)
                state={**state,'phase':'commit_intent'};journal.save(state);self._check()
                try:await self.repository.publish(claim,meta,bundle,receipt,Budget())
                except BaseException:return unknown('result_commit_or_teardown_unknown')
                journal.save({**state,'phase':'confirmed'});return outcome('confirmed','succeeded',meta['result_id'])
        except WorkerBusy:return outcome('busy','worker_lock_held')
        except BaseException:
            self.quarantined=True
            # No second claim/native/publish even if checkpoint cleanup fails.
            return outcome('reconciliation_pending','source_or_checkpoint_unknown')

class ResultReader:
    def __init__(self,pool=None):self.owner=admission.DifferentialRepository(pool)
    async def read_run(self,subject,project,job_id,budget):
        admission.ids(subject,project,job_id)
        async def operation(query):
            await self.owner._owner(query,subject,project)
            row=await query('SELECT '+RUN_COLUMNS+' FROM cyomics_app.differential_job_runs WHERE owner_id=%s AND project_id=%s AND job_id=%s',
                (UUID(subject),UUID(project),UUID(job_id)))
            if row is None:raise Fault(404,'differential_run_not_found')
            need(len(row)==13);r=dict(zip(RUN_COLUMNS.split(','),row))
            need(str(r['owner_id'])==subject and str(r['project_id'])==project and str(r['job_id'])==job_id
                and r['status'] in ('running','succeeded','failed') and r['observation'] in ('confirmed','reconciliation_pending'))
            for k in ('job_id','attempt_id','registration_id','project_id','result_id'):
                r[k]=None if r[k] is None else str(r[k])
            for k in ('started_at','finished_at'):
                r[k]=None if r[k] is None else r[k].astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            r.pop('owner_id');r.pop('scope_sha256')
            return {'schemaVersion':1,'contract':'differential_run_v0.1',**r}
        return await self.owner._transaction(subject,budget,operation)

    async def read(self,subject,project,budget,*,job_id=None,result_id=None,artifact=None):
        admission.ids(subject,project,*(x for x in (job_id,result_id) if x is not None))
        need((job_id is None)!=(result_id is None),'differential_result_selection_required')
        if artifact is not None and artifact not in ARTIFACT_COLUMNS:raise Fault(404,'differential_artifact_not_found')
        async def operation(query):
            await self.owner._owner(query,subject,project)
            key='job_id' if job_id is not None else 'result_id';value=job_id or result_id
            suffix=','+','.join('octet_length('+col+'),encode(sha256('+col+"),'hex')" for col in ARTIFACT_COLUMNS.values())
            row=await query('SELECT '+RESULT_COLUMNS+suffix+' FROM cyomics_app.differential_results WHERE owner_id=%s AND project_id=%s AND '+key+'=%s',
                (UUID(subject),UUID(project),UUID(value)))
            if row is None:raise Fault(404,'differential_result_not_found')
            need(len(row)==22);meta=admission.document(row[5],32768);manifest=admission.document(row[7],8192)
            need(admission.sha(row[5])==row[6] and admission.sha(row[7])==row[8] and admission.sha(row[9])==row[10]
                and meta['manifest_sha256']==row[8] and meta['receipt_sha256']==row[10] and meta['artifacts']==manifest
                and meta['created_by']==subject and meta['project_id']==project and meta['result_id']==str(row[0])
                and meta['job_id']==str(row[1]) and meta['attempt_id']==str(row[2])
                and str(row[3])==subject and str(row[4])==project
                and datetime.fromisoformat(meta['created_at'].replace('Z','+00:00'))==row[11])
            job_row=await query('SELECT '+admission.COLUMNS+admission.VERIFY+' FROM cyomics_app.differential_jobs WHERE owner_id=%s AND project_id=%s AND job_id=%s',
                (UUID(subject),UUID(project),row[1]))
            need(job_row is not None);job=self.owner._decode(job_row,subject,project).public();validate_metadata(meta,job)
            validate_receipt(admission.document(row[9],131072),meta)
            run=await query('SELECT job_id,registration_id FROM cyomics_app.differential_job_runs WHERE job_id=%s AND attempt_id=%s AND status=%s AND result_id=%s',
                (row[1],row[2],'succeeded',row[0]));need(run is not None and len(run)==2 and str(run[1])==meta['registration_id'])
            for i,item in enumerate(manifest):need((row[12+2*i],row[13+2*i])==(item['bytes'],item['sha256']))
            if artifact is None:return meta
            value=await query('SELECT '+ARTIFACT_COLUMNS[artifact]+' FROM cyomics_app.differential_results WHERE result_id=%s',(row[0],))
            need(value is not None and len(value)==1);body=text_bytes(value[0]);expected=next(x for x in manifest if x['name']==artifact)
            need(pin(body)=={'bytes':expected['bytes'],'sha256':expected['sha256']})
            return body
        return await self.owner._transaction(subject,budget,operation)


async def run_once_owned(config=None,*,pool=None,roots=None,cancelled=lambda:False):
    """Minimal Root entry; caller transfers a dedicated ORIGINAL Owner pool.

    Root must already have its existing control/gate/loader authorization. This
    helper never enables gates or creates credentials/pools/registration/roots.
    """
    if config is None or pool is None or roots is None:return Outcome('off','worker_unconfigured')
    from .preprocess_repository import PreprocessRepository
    from .import_repository import ImportRepository
    from .sample_repository import SampleDesignRepository
    worker=None;outcome=None;closed=False
    try:
        assembly=SavedInputAssembly(PreprocessRepository(pool,enabled=True),ImportRepository(pool),SampleDesignRepository(pool))
        worker=OnceWorker(config,OnceRepository(pool),assembly,roots)
        outcome=await worker.run_once(cancelled=cancelled)
    except BaseException:
        outcome=Outcome('reconciliation_pending','entry_or_assembly_unknown',getattr(config,'job_id',None))
    finally:
        try:
            # FixedPool.close removes idle entries. Retain the exact handles
            # first so its deque mutation cannot conceal a failed abort.
            connections=tuple(getattr(pool,'_idle',()))+tuple(getattr(pool,'_active',()))
            pool.close()
            closed=not getattr(pool,'_active',()) and all(c.closed is True for c in connections)
        except BaseException:closed=False
    if not closed:
        if worker is not None:worker.quarantined=True
        return Outcome('reconciliation_pending','owner_pool_teardown_unknown',
            None if outcome is None else outcome.job_id,None if outcome is None else outcome.attempt_id,
            None if outcome is None else outcome.result_id)
    return outcome
