"""Owner read-only safe projection of the original once-repository observation."""
from . import de_classification_jobs as jobs,de_classification_once as once
from .settings import Fault
from .validation import canonical_uuid

CONTRACT='de_classification_run_v0.1'
MAX_RESPONSE=32768
KEYS=frozenset(('schemaVersion','contract','classification_job_id','project_id','request_id','created_by',
    'source_result_id','source_manifest_sha256','source_binding_sha256','parameters_sha256','state','observation',
    'attempt_id','created_at','claimed_at','finished_at','error_code','derived_version_id','scientific_status'))
SCIENTIFIC=frozenset(('CLASSIFIED','BLOCKED_SOURCE_NO_TESTABLE_ROWS','BLOCKED_SOURCE_REVIEW_REQUIRED'))

def need(value):
    if not value:raise Fault(503,'de_classification_persisted_invalid')

def _project(observed,subject,project,request_id):
    need(type(observed) is once.Observation and type(observed.job) is jobs.StoredJob)
    job=jobs.dto(observed.job.public())
    need((job['created_by'],job['project_id'],job['request_id'])==(subject,project,request_id))
    jobs.original.pc.stamp(job['created_at']);run=observed.run();result=observed.result()
    need(type(observed.state) is str and type(observed.reason) is str)
    value={k:job[k] for k in ('classification_job_id','project_id','request_id','created_by','source_result_id',
        'source_manifest_sha256','source_binding_sha256','parameters_sha256','created_at')}
    value.update(schemaVersion=1,contract=CONTRACT,state=observed.state,observation='confirmed',attempt_id=None,
        claimed_at=None,finished_at=None,error_code=None,derived_version_id=None,scientific_status=None)
    if result is not None:
        need(all(result[k]==job[k] for k in ('project_id','request_id','created_by','source_result_id',
            'source_manifest_sha256','source_binding_sha256','parameters_sha256','parameters','core')))
        need(result['status'] in SCIENTIFIC and canonical_uuid(result['derived_version_id']))
        value.update(derived_version_id=result['derived_version_id'],scientific_status=result['status'])
    if run is None:
        need((observed.state,observed.reason)==(('finished','existing_result') if result is not None else ('queued','unclaimed')))
        if result is not None:value['finished_at']=result['created_at']
    else:
        need(type(run) is dict and set(run)==set(once.COLUMNS.split(',')))
        need((run['owner_id'],run['project_id'],run['request_id'],run['classification_job_id'])==
            (subject,project,request_id,job['classification_job_id']))
        need(canonical_uuid(run['attempt_id']) and canonical_uuid(run['epoch_id']) and run['observation']=='confirmed'
            and all(jobs.original.pc.hashed(run[k]) for k in ('scope_sha256','configuration_sha256','request_payload_sha256'))
            and run['request_payload_sha256']==observed.job.request_payload_sha256)
        claimed=jobs.original.pc.stamp(run['claimed_at'])
        value.update(attempt_id=run['attempt_id'],claimed_at=run['claimed_at'])
        if run['status']=='claimed':
            need(all(run[k] is None for k in ('finished_at','derived_version_id','error_code','failure_facts_json','failure_facts_sha256')))
            need((observed.state,observed.reason)==(('reconciliation_pending','result_saved_finish_unconfirmed')
                if result is not None else ('claimed','attempt_consumed')))
        else:
            need(run['status']=='finished' and (observed.state,observed.reason)==('finished','confirmed'))
            need(jobs.original.pc.stamp(run['finished_at'])>=claimed);value['finished_at']=run['finished_at']
            if result is not None:
                need(run['derived_version_id']==result['derived_version_id'] and all(run[k] is None for k in ('error_code','failure_facts_json','failure_facts_sha256')))
            else:
                need(run['derived_version_id'] is None and run['error_code'] in ('input_integrity_failed','runner_failed'))
                value['error_code']=run['error_code']
    need(set(value)==KEYS and len(jobs.saved.canonical(value).encode('utf8'))<=MAX_RESPONSE)
    return value

def project(observed,subject,project,request_id):
    try:return _project(observed,subject,project,request_id)
    except Exception:raise Fault(503,'de_classification_persisted_invalid') from None

async def read(pool,subject,project_id,request_id,budget):
    try:observed=await once.OnceRepository(pool,enabled=True).read_request(subject,project_id,request_id,budget)
    except Fault as error:
        if error.code in ('de_classification_once_persisted_invalid','de_classification_attempt_binding_mismatch',
            'de_classification_request_conflict','de_parent_binding_mismatch'):
            raise Fault(503,'de_classification_persisted_invalid') from None
        raise
    return project(observed,subject,project_id,request_id)
