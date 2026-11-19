"""Explicit one-call business wrapper. No daemon/HTTP/pool/credential creation.
Native execution remains the original synchronous, signal-aware runner. Caller
owns the original factory pool and all separately approved runtime resources.
"""
import asyncio
from dataclasses import dataclass
from uuid import UUID
from . import differential_jobs as parent_jobs,de_classification_jobs as jobs
from . import de_classification_once as once,de_classification_repository as saved
from . import de_classification_runner as runner,de_classification_contract as c
from .differential_worker_once import ResultReader
from .quant_diff_saved_input import SavedInputAssembly,Selection
from .quant_diff_input import Result,BoundInput
from .preprocess_repository import PreprocessRepository
from .import_repository import ImportRepository
from .sample_repository import SampleDesignRepository
from .repository import FixedPool
from .settings import Budget,Fault


def need(ok):
    if not ok:raise Fault(503,'de_classification_worker_unavailable')

def _raise_cancel(error):
    # Original runner/transaction wrappers suppress display of context while
    # retaining the original CancelledError. Preserve cancellation semantics.
    seen=set()
    while isinstance(error,BaseException) and id(error) not in seen:
        if isinstance(error,asyncio.CancelledError):raise error
        seen.add(id(error));error=error.__cause__ or error.__context__

@dataclass(frozen=True,slots=True)
class Outcome:
    disposition:str # This invocation: confirmed OR unknown; never inferred from GET.
    reason:str
    observation:once.Observation|None=None
    job_id:str|None=None
    attempt_id:str|None=None
    request_id:str|None=None

async def _load(pool,claim,check):
    """Generic exact saved-parent loader; all identity comes from confirmed claim.
    Original Root loader chain, without engineering constants or invented seals.
    """
    check();dto=claim.job.public();body,spec=jobs.request_input(claim.job.request_json.encode('utf8'))
    subject,project,result_id=dto['created_by'],dto['project_id'],dto['source_result_id']
    need(body['request_id']==dto['request_id'] and body['source_result_id']==result_id and body['parameters']==dto['parameters'])
    need(c.sha(spec.body)==dto['parameters_sha256'])
    reader=ResultReader(pool)
    metadata=await reader.read(subject,project,Budget(),result_id=result_id);check()
    need(metadata['manifest_sha256']==dto['source_manifest_sha256'])
    parent_id=metadata['job_id'];owner=parent_jobs.DifferentialRepository(pool)
    async def operation(query):
        check();await owner._owner(query,subject,project)
        row=await query('SELECT '+parent_jobs.COLUMNS+parent_jobs.VERIFY+' FROM cyomics_app.differential_jobs WHERE owner_id=%s AND project_id=%s AND job_id=%s',
            (UUID(subject),UUID(project),UUID(parent_id)))
        stored=owner._decode(row,subject,project)
        raw=await query('SELECT metadata_json,receipt_json FROM cyomics_app.differential_results WHERE owner_id=%s AND project_id=%s AND job_id=%s AND result_id=%s',
            (UUID(subject),UUID(project),UUID(parent_id),UUID(result_id)))
        check();need(raw is not None and len(raw)==2 and all(type(v) is str for v in raw))
        return stored,row[15],raw[0].encode('utf8'),raw[1].encode('utf8')
    parent,snapshot,metadata_bytes,receipt_bytes=await owner._transaction(subject,Budget(40),operation)
    check();need(parent_jobs.canonical(metadata).encode('utf8')==metadata_bytes)
    outputs={}
    for item in metadata['artifacts']:
        outputs[item['name']]=await reader.read(subject,project,Budget(),result_id=result_id,artifact=item['name']);check()
    assembly=SavedInputAssembly(PreprocessRepository(pool,enabled=True),ImportRepository(pool),SampleDesignRepository(pool))
    review=await assembly.assemble(subject,project,Selection(**parent.public()['selection']),Budget(50));check()
    need(type(review) is Result and review.model_input_satisfied and type(review.bound) is BoundInput)
    source=c.bind_source(bound=review.bound,job=parent,input_snapshot_json=snapshot,
        metadata_json=metadata_bytes,receipt_json=receipt_bytes,outputs=outputs,check=check)
    check();need(source.binding_sha256==dto['source_binding_sha256'] and source.binding['source_manifest_sha256']==dto['source_manifest_sha256'])
    need((subject,project,result_id,source.binding_sha256,c.sha(spec.body))==
        (claim.config.owner_id,claim.config.project_id,claim.config.source_result_id,claim.config.source_binding_sha256,claim.config.parameters_sha256))
    return source,spec

class ClassificationOnceWorker:
    def __init__(self,config=None,scope_sha256=None,repository=None,persistence=None):
        self.config=config;self.scope_sha256=scope_sha256;self.repository=repository;self.persistence=persistence
        self.used=False;self.quarantined=False;self._identity=None;self._attempt=None
    def _components(self):
        need(type(self.config) is runner.Config and type(self.repository) is once.OnceRepository
            and type(self.persistence) is saved.ClassificationRepository)
        pool=self.repository.owner.pool
        need(type(pool) is FixedPool and self.repository.admission.owner.pool is pool
            and self.repository.results.owner.pool is pool and self.persistence.owner.pool is pool)
        need(self.repository.enabled is True and type(self.persistence.config) is saved.Config
            and self.persistence.config.enabled is True and callable(self.persistence.config.verify))
        need(callable(self.config.verify) and self.config.verify() is None)
        return pool
    def _remember(self,job):
        dto=job.public();self._identity=(dto['created_by'],dto['project_id'],dto['request_id'],dto['classification_job_id'])
        return dto
    def _outcome(self,disposition,reason,observation=None):
        identity=self._identity
        run=None if observation is None else observation.run()
        attempt=(None if run is None else run['attempt_id']) if observation is not None else self._attempt or getattr(self.config,'attempt_id',None)
        return Outcome(disposition,reason,observation,None if identity is None else identity[3],
            attempt,None if identity is None else identity[2])
    async def _observe(self,reason,*,unknown):
        # A bounded independent read chain only. Never re-claim/save/finish.
        try:
            if self._identity is None:
                cfg=self.config
                dto=await self.repository.admission.read_job(cfg.owner_id,cfg.project_id,cfg.job_id,Budget())
                self._identity=(dto['created_by'],dto['project_id'],dto['request_id'],dto['classification_job_id'])
            subject,project,request_id,job_id=self._identity
            observation=await self.repository.read_request(subject,project,request_id,Budget(40))
            need(type(observation) is once.Observation and observation.job.public()['classification_job_id']==job_id)
            return self._outcome('unknown' if unknown else 'confirmed',reason,observation)
        except asyncio.CancelledError:
            self.quarantined=True;raise
        except Exception as error:
            self.quarantined=True
            _raise_cancel(error)
            return self._outcome('unknown',reason+'_observation_unavailable')
    async def run_once(self,*,cancelled=lambda:False):
        if ((type(self.repository) is once.OnceRepository and self.repository.write_unknown)
                or (type(self.persistence) is saved.ClassificationRepository and self.persistence.write_unknown)):
            self.quarantined=True
        if self.used or self.quarantined:
            return await self._observe('attempt_consumed',unknown=self.quarantined)
        if (self.config is None or self.scope_sha256 is None or self.repository is None or self.persistence is None
                or type(self.config) is runner.Config and self.config.enabled is False):
            return self._outcome('confirmed','off')
        if (type(self.repository) is once.OnceRepository and self.repository.enabled is False
                or type(self.persistence) is saved.ClassificationRepository and (self.persistence.config is None
                    or type(self.persistence.config) is saved.Config and (self.persistence.config.enabled is False or self.persistence.config.verify is None))):
            return self._outcome('confirmed','off')
        self.used=True;stage='entry'
        try:
            pool=self._components();need(callable(cancelled))
            stage='claim'
            try:claimed=await self.repository.claim(self.config,self.scope_sha256,Budget(40))
            except Fault as error:
                if error.code=='de_classification_attempt_consumed':return await self._observe('attempt_consumed',unknown=False)
                raise
            if type(claimed) is once.Observation:
                self._remember(claimed.job)
                return self._outcome('confirmed','existing_result',claimed)
            need(type(claimed) is once.Claim)
            self._attempt=claimed.run()['attempt_id']
            dto=self._remember(claimed.job);body,spec=jobs.request_input(claimed.job.request_json.encode('utf8'))
            need(body['request_id']==dto['request_id'] and body['parameters']==dto['parameters'])
            def check():
                self.repository._claim(claimed)
                need(self.config.verify() is None)
            stage='parent_load';source,loaded_spec=await _load(pool,claimed,check)
            need(loaded_spec.body==spec.body);check()
            stage='native';native=runner.ClassificationRunner(claimed.config)
            try:
                # No to_thread/executor: original main-thread signals/limits.
                result=native.run(source,spec,cancelled=cancelled)
            except runner.shared.Log2RunFailure as error:
                _raise_cancel(error)
                facts=getattr(error,'facts',None)
                if type(facts) is not runner.shared.FailureFacts:
                    self.quarantined=True;return await self._observe('native_cleanup_unknown',unknown=True)
                try:self.repository._failure(facts.snapshot(),claimed.run())
                except Fault:
                    self.quarantined=True;return await self._observe('native_cleanup_unknown',unknown=True)
                stage='finish_failed'
                observation=await self.repository.finish_failed(claimed,'runner_failed',facts,Budget(40))
                need(type(observation) is once.Observation and observation.state=='finished' and observation.result() is None)
                return self._outcome('confirmed','runner_failed',observation)
            need(type(result) is runner.RunResult)
            stage='save';check()
            metadata=await self.persistence.save_verified(dto['created_by'],dto['project_id'],body['request_id'],
                source,spec,result.bundle,result._receipt,Budget(40))
            need(type(metadata) is dict)
            saved.validate_dto(metadata)
            need(metadata['request_id']==body['request_id'] and metadata['source_binding_sha256']==source.binding_sha256
                and metadata['manifest_sha256']==result.bundle.manifest_sha256)
            stage='finish'
            observation=await self.repository.finish(claimed,Budget(40))
            need(type(observation) is once.Observation and observation.state=='finished' and observation.result()==metadata)
            return self._outcome('confirmed','finished',observation)
        except asyncio.CancelledError:
            self.quarantined=True;raise
        except Exception as error:
            self.quarantined=True
            _raise_cancel(error)
            return await self._observe(stage+'_unknown',unknown=True)
