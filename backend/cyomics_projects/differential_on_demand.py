"""Preissued011 ticket + original supervised transport/once worker only.
No registry writes, approvals, credentials, queue scans or new scientific code.
"""
import asyncio,sys
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID
from . import classification_on_demand as shared,differential_jobs as jobs
from . import differential_worker_once as worker,differential_once_entry as original_entry
from .settings import Budget,Fault
from .validation import canonical_uuid,strict_json
from .raw_qc_once_entry import _eligible_context,_State,_Signals,_settle_and_close,_Interrupted

IPC_KEYS=frozenset(('owner_id','project_id','request_id','job_id'))
RECEIPT_KEYS=frozenset(('schemaVersion','job_id','request_id','attempt_id','result_id','disposition','reason','business_state',
    'teardown','loop_closed','pending_tasks_seen','async_resources_remaining','interrupted','exit_code','registration_id'))
MIN_TICKET_SECONDS=shared.CHILD_SECONDS+12 # Original API entry budget + total child window.
def need(value,code='differential_unavailable'):
    if not value:raise Fault(503,code)
def ticket_need(value):need(value,'differential_registration_unavailable')

@dataclass(frozen=True,slots=True)
class Ticket:
    registration_id:str
    epoch_id:str
    scope_sha256:str
    owner_id:str
    project_id:str
@dataclass(frozen=True,slots=True)
class Config:
    launcher:shared.Config|None=None
    ticket:Ticket|None=None

def prepare(config=None):
    if config is None:return None
    need(type(config) is Config)
    if shared.prepare(config.launcher) is None:return None
    if config.ticket is not None:
        ticket_need(type(config.ticket) is Ticket)
        ticket_need(all(canonical_uuid(getattr(config.ticket,k)) for k in ('registration_id','epoch_id','owner_id','project_id'))
            and shared.hashed(config.ticket.scope_sha256))
    return config

def parse_notification(raw):
    need(type(raw) is bytes and 0<len(raw)<=1024)
    value=strict_json(raw)
    need(type(value) is dict and set(value)==IPC_KEYS and all(canonical_uuid(v) for v in value.values()) and shared.canonical(value)+b'\n'==raw)
    return value

class DifferentialController(shared.Controller):
    _job_key='job_id'
    def __init__(self,config=None,*,capacity=None):
        self.service_config=prepare(config)
        super().__init__(None if self.service_config is None else self.service_config.launcher,capacity=capacity)
    async def check_ticket(self,repository,subject,project,budget):
        ticket=None if self.service_config is None else self.service_config.ticket
        ticket_need(type(ticket) is Ticket and (ticket.owner_id,ticket.project_id)==(subject,project))
        async def operation(query):
            await repository._owner(query,subject,project)
            row=await query('SELECT '+worker.REG_COLUMNS+',clock_timestamp() FROM cyomics_app.differential_runtime_registrations WHERE registration_id=%s AND owner_id=%s AND project_id=%s',
                (UUID(ticket.registration_id),UUID(subject),UUID(project)))
            ticket_need(row is not None and len(row)==12)
            value=dict(zip(worker.REG_COLUMNS.split(','),row[:11]));now=row[11]
            ticket_need((str(value['registration_id']),str(value['epoch_id']),value['scope_sha256'])==
                (ticket.registration_id,ticket.epoch_id,ticket.scope_sha256))
            ticket_need(str(value['owner_id'])==subject and str(value['project_id'])==project and value['request_id'] is None
                and type(value['max_jobs']) is int and value['max_jobs']==1 and value['method_json']==jobs.canonical(jobs.METHOD))
            profile=jobs.document(value['profile_json'],32768)
            ticket_need(profile.get('contract')=='differential_runtime_profile_v0.1' and profile.get('claim_policy')=='one_new_job_in_scope'
                and profile.get('epoch_id')==ticket.epoch_id and profile.get('method_sha256')==jobs.sha(value['method_json']))
            ticket_need(value['approved_at']<now and value['expires_at']-value['approved_at']<=timedelta(minutes=15)
                and value['expires_at']-now>timedelta(seconds=MIN_TICKET_SECONDS))
            used=await query('SELECT job_id FROM cyomics_app.differential_job_runs WHERE registration_id=%s',(UUID(ticket.registration_id),))
            ticket_need(used is None)
            return value
        return await repository._transaction(subject,budget,operation)
    async def reserve(self,repository,subject,project,body,budget):
        need(type(repository) is jobs.DifferentialRepository)
        if repository._write_unknown:raise Fault(504,'differential_submission_unknown')
        # Original StoredJob compares the COMPLETE request JSON, not a subset.
        prior=await repository.find_request(subject,project,body['request_id'],budget)
        if prior is not None:
            if prior.request_json!=jobs.canonical(body):raise Fault(409,'differential_request_conflict')
            return jobs.task_value(prior.public(),project=project,request_id=body['request_id'])
        need(self._state!='off' and not self._closing)
        key=(subject,project,body['request_id'])
        if self._state!='idle' or not self._capacity.available:
            if self._lease is not None and self._lease.key==key:raise Fault(504,'differential_submission_unknown')
            if self._state=='unknown' or self._capacity.unknown:raise Fault(503,'differential_unavailable')
            raise Fault(409,'differential_capacity_busy')
        need(self._capacity.take(self,key));self._lease=shared._Lease(key,self._issuer);self._state='reserved'
        try:self._sources()
        except asyncio.CancelledError:self.release(self._lease,confirmed=False);raise
        except Exception:
            self.release(self._lease,confirmed=False);raise Fault(503,'differential_unavailable') from None
        try:await self.check_ticket(repository,subject,project,budget)
        except asyncio.CancelledError:self.release(self._lease,confirmed=False);raise
        except Exception:
            self.release(self._lease,confirmed=not repository._read_unknown and not repository._write_unknown);raise
        return self._lease
    def _notification_value(self,dto):
        value=jobs.task_value(dto);ticket=self.service_config.ticket
        need(value['source']['created_by']==ticket.owner_id and value['project_id']==ticket.project_id and self._lease is not None
            and self._lease.key==(ticket.owner_id,ticket.project_id,value['request_id']))
        return {'owner_id':ticket.owner_id,'project_id':ticket.project_id,'request_id':value['request_id'],'job_id':value['job_id']}
    def _receipt(self,raw,code):
        need(type(raw) is bytes and len(raw)<=shared.STDOUT_CAP and raw.endswith(b'\n') and raw.count(b'\n')==1)
        value=strict_json(raw);need(type(value) is dict and set(value)==RECEIPT_KEYS and type(value['schemaVersion']) is int and value['schemaVersion']==1)
        need(value['job_id']==self._notification['job_id'] and value['request_id']==self._notification['request_id']
            and value['registration_id']==self.service_config.ticket.registration_id and canonical_uuid(value['attempt_id']))
        need(type(value['exit_code']) is int and value['exit_code']==code==0 and value['disposition']=='confirmed'
            and value['business_state']=='confirmed' and value['teardown']=='confirmed' and value['loop_closed'] is True
            and all(value[k] is False for k in ('pending_tasks_seen','async_resources_remaining','interrupted')))
        need(value['reason'] in ('succeeded','failed','input_integrity_failed'))
        need(canonical_uuid(value['result_id']) if value['reason']=='succeeded' else value['result_id'] is None)
        return value

def _payload(notification=None,config=None,outcome=None):
    confirmed=type(outcome) is worker.Outcome and outcome.status=='confirmed' and outcome.reason in ('succeeded','failed','input_integrity_failed')
    return {'schemaVersion':1,'job_id':None if notification is None else notification['job_id'],
        'request_id':None if notification is None else notification['request_id'],'attempt_id':None if outcome is None else outcome.attempt_id,
        'result_id':None if outcome is None else outcome.result_id,'disposition':'confirmed' if confirmed else 'unknown',
        'reason':'entry_unknown' if outcome is None else outcome.reason,'business_state':None if outcome is None else outcome.status,
        'teardown':'confirmed' if confirmed else 'unknown','loop_closed':None,'pending_tasks_seen':False,'async_resources_remaining':False,'interrupted':False,
        'exit_code':0 if confirmed else 1,'registration_id':None if config is None else config.registration_id}

def main_from_server(raw=None,*,configure=None,activate_factory=None,verify=None,cancelled=lambda:False):
    """Root shim supplies callbacks/roots only; original entry owns its factory."""
    notification=config=None;state=_State();fence=_Signals(state);loop=None;closed=None;pending=resources=False;value=None
    if raw is None or configure is None or activate_factory is None or verify is None:
        value=_payload();value.update(disposition='off',reason='worker_unconfigured',teardown='confirmed',exit_code=2)
    elif not _eligible_context():value=_payload();value.update(reason='dedicated_context_required',teardown='unknown')
    else:
        try:
            notification=parse_notification(raw)
            need(callable(configure) and callable(activate_factory) and callable(verify) and verify() is None)
            config,roots=configure(notification)
            need(type(config) is worker.OnceConfig and callable(config.verify) and config.verify() is None)
            need((config.owner_id,config.project_id,config.job_id)==tuple(notification[k] for k in ('owner_id','project_id','job_id')))
            need(all(canonical_uuid(getattr(config,k)) for k in ('registration_id','epoch_id')) and shared.hashed(config.scope_sha256))
            fence.install();loop=asyncio.new_event_loop()
            def quiet(_loop,_context):state.loop_error=True
            loop.set_exception_handler(quiet)
            outcome=loop.run_until_complete(original_entry.run_from_server(config,roots=roots,activate_factory=activate_factory,cancelled=cancelled))
            need(type(outcome) is worker.Outcome and outcome.job_id==notification['job_id'])
            need(verify() is None);value=_payload(notification,config,outcome)
        except BaseException as error:
            if isinstance(error,(asyncio.CancelledError,KeyboardInterrupt,_Interrupted)):state.interrupted=True
            state.loop_error=True;value=_payload(notification,config)
        finally:
            if loop is not None:pending,resources,closed=_settle_and_close(loop,state)
            fence.close()
    value.update(loop_closed=closed,pending_tasks_seen=pending,async_resources_remaining=resources,interrupted=state.interrupted)
    if loop is not None and (closed is not True or pending or resources or state.interrupted or state.loop_error or state.cleanup_error):
        value.update(disposition='unknown',reason='entry_teardown_unknown',teardown='unknown',exit_code=1)
    encoded=shared.canonical(value)+b'\n';need(len(encoded)<=shared.STDOUT_CAP);sys.stdout.write(encoded.decode())
    return value['exit_code']
