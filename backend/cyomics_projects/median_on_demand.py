"""Default-off finite Median dispatch; original supervised transport, no queue.
A request ticket is DB truth, never authorization by a client DTO or boolean.
"""
from dataclasses import dataclass
from datetime import datetime,timedelta
from uuid import UUID
import json,os,stat
from . import classification_on_demand as shared,preprocess_repository as repository
from . import preprocess_contract as c
from .settings import Fault
from .validation import canonical_uuid,strict_json


def need(value,status=503,code='preprocessing_execution_unavailable'):
    if not value:raise Fault(status,code)


@dataclass(frozen=True,slots=True)
class MedianDispatchConfig:
    launcher:shared.Config|None=None
    service:object=None


def prepare(config=None):
    if config is None:return None
    from .preprocess_median_service_bootstrap import ServiceWorkerConfig,prepare as service_prepare
    need(type(config) is MedianDispatchConfig)
    if shared.prepare(config.launcher) is None:return None
    need(type(config.service) is ServiceWorkerConfig);service_prepare(config.service)
    need(config.launcher.backend==config.service.backend and config.launcher.control==config.service.launches
        and config.launcher.startup==config.service.startup)
    return config


TICKET_KEYS=('registration_id','epoch_id','scope_sha256','owner_id','project_id','dataset_version_id',
    'request_id','request_sha256','binding_sha256','planned_job_id','slot','approved_at','expires_at','observed_at')
IPC_KEYS=frozenset(TICKET_KEYS[:10]+('slot',))
RECEIPT_KEYS=frozenset(('schemaVersion','job_id','request_id','registration_id','attempt_id','result_id',
    'disposition','reason','acknowledged','teardown','loop_closed','pending_tasks_seen','async_resources_remaining','interrupted','exit_code'))


def ticket_value(row,expected,scope):
    need(type(row) in (tuple,list) and len(row)==15)
    disposition=row[0]
    if disposition=='refused':raise Fault(503,'preprocessing_execution_unavailable')
    if disposition=='conflict':raise Fault(409,'preprocessing_request_conflict')
    need(disposition=='issued')
    v=dict(zip(TICKET_KEYS,row[1:]))
    for k in ('registration_id','epoch_id','owner_id','project_id','dataset_version_id','request_id','planned_job_id'):
        v[k]=str(v[k]);need(canonical_uuid(v[k]))
    for k in ('scope_sha256','request_sha256','binding_sha256'):need(shared.hashed(v[k]))
    need(all(v[k]==value for k,value in expected.items()) and v['scope_sha256']==scope['scope_sha256'])
    need(type(v['slot']) is int and 0<=v['slot']<4)
    need(all(type(v[k]) is datetime and v[k].utcoffset() is not None for k in ('approved_at','expires_at','observed_at')))
    need(v['approved_at']<=v['observed_at']<v['expires_at'] and v['expires_at']-v['approved_at']<=timedelta(seconds=900)
        and v['expires_at']-v['observed_at']>timedelta(seconds=432))
    return v


class MedianController(shared.Controller):
    _job_key='job_id'
    def __init__(self,config=None,*,capacity=None):
        self.service_config=prepare(config);self.ticket=None;self.phase='none';self.scope=None
        super().__init__(None if self.service_config is None else self.service_config.launcher,capacity=capacity)

    def _sources(self):
        from .preprocess_median_service_bootstrap import load_service,read_private
        super()._sources()
        self.scope=load_service(self.service_config.service,child=False)
        root=self.config.control
        entries=list(root.glob('*.launch.once'));need(len(entries)<=4)
        for entry in entries:
            # Current owned child is still being supervised, not an old replay.
            if self._notification is not None and entry.name==self._notification['planned_job_id']+'.launch.once' and self._state in ('notified','reserved'):continue
            intent=read_private(entry,4096)
            closed=read_private(root/(entry.stem.removesuffix('.launch')+'.closed.once'),32768)
            need(closed.get('job_id')==intent.get('planned_job_id') and closed.get('notification_sha256')==shared.sha(shared.canonical(intent)+b'\n')
                and closed.get('contract')=='median_parent_transport_closed_v1' and closed.get('receipt',{}).get('acknowledged') is True)

    async def reserve(self,repo,subject,project,dataset,request,budget):
        need(type(repo) is repository.PreprocessRepository)
        if repo.quarantined:raise Fault(504,'preprocessing_submission_unknown')
        need(self._state!='off' and not self._closing)
        key=(subject,project,request.request_id)
        if self._state!='idle' or not self._capacity.available:
            if self._lease is not None and self._lease.key==key:raise Fault(504,'preprocessing_submission_unknown')
            need(self._state!='unknown' and not self._capacity.unknown)
            raise Fault(409,'preprocessing_not_ready')
        need(self._capacity.take(self,key));self._lease=shared._Lease(key,self._issuer);self._state='reserved';self.phase='preflight';self.ticket=None
        try:
            self._sources();s=self.scope
            need((subject,project,dataset,request.quant_family)==(s['owner_id'],s['project_id'],s['dataset_version_id'],'LFQ intensity'))
            need(request.parameters_bytes.decode()==s['parameters_json'],409,'preprocessing_not_ready')
            need(s['expires_at_ms']/1000-__import__('time').time()>432)
        except BaseException:
            # Source/private-file ambiguity must not release a possibly occupied slot.
            self.release(self._lease,confirmed=False);raise
        return self._lease

    def before_insert(self,lease,subject,project,dataset):
        async def issue(query,request,source,execution,binding,job_id):
            need(self.reserved(lease) and lease.key==(subject,project,request.request_id) and self.phase=='preflight')
            self._sources();need(request.parameters_value()['normalization']=='median')
            self.phase='issuing'
            row=await query('SELECT disposition,'+','.join(TICKET_KEYS)+' FROM cyomics_app.issue_median_request_registration(%s,%s,%s,%s,%s,%s)',
                (UUID(self.scope['scope_id']),UUID(project),UUID(dataset),repository.canonical(request.request_value()),
                 self.scope['profile']['source_manifest_sha256'],UUID(job_id)),write=True)
            expected=dict(owner_id=subject,project_id=project,dataset_version_id=dataset,request_id=request.request_id,
                request_sha256=repository.scoped_request_hash(project,dataset,request),binding_sha256=binding,planned_job_id=job_id)
            self.ticket=ticket_value(row,expected,self.scope);self.phase='issued'
        return issue

    def abort(self,lease,repo):
        # Only original confirmed rollback/teardown permits release. Unknown persists.
        confirmed=not repo.quarantined
        self.release(lease,confirmed=confirmed)
        if confirmed:self.phase='rolled_back';self.ticket=None

    def _notification_value(self,dto):
        c.task_value(dto);need(self.ticket is not None and self.phase=='issued')
        t=self.ticket
        need(dto['job_id']==t['planned_job_id'] and dto['request_id']==t['request_id'] and dto['binding_sha256']==t['binding_sha256']
            and dto['request_sha256']==t['request_sha256'] and dto['source']['project_id']==t['project_id'])
        return {k:t[k] for k in IPC_KEYS}

    def _intent(self,value,*,job_key='job_id'):
        need(value['planned_job_id']==self.ticket['planned_job_id'])
        # Reuse original exclusive fsync intent writer; job_id only selects filename.
        return super()._intent(dict(value,job_id=value['planned_job_id']),job_key='job_id')

    def _receipt(self,raw,code):
        need(type(raw) is bytes and len(raw)<=shared.STDOUT_CAP and raw.endswith(b'\n') and raw.count(b'\n')==1)
        v=strict_json(raw);need(type(v) is dict and set(v)==RECEIPT_KEYS)
        need(type(v['schemaVersion']) is int and v['schemaVersion']==1 and v['job_id']==self._notification['planned_job_id']
            and v['request_id']==self._notification['request_id'] and v['registration_id']==self._notification['registration_id'])
        need(canonical_uuid(v['attempt_id']) and v['reason'] in ('succeeded','failed'))
        need(canonical_uuid(v['result_id']) if v['reason']=='succeeded' else v['result_id'] is None)
        need(v['disposition']=='confirmed' and v['acknowledged'] is True and v['teardown']=='confirmed' and v['loop_closed'] is True
            and type(v['exit_code']) is int and v['exit_code']==code==0 and all(v[k] is False for k in ('pending_tasks_seen','async_resources_remaining','interrupted')))
        return v

    async def _run(self):
        from .preprocess_median_service_bootstrap import write_once
        notification=dict(self._notification)
        await super()._run()
        if self._state=='idle':
            try:
                # super has confirmed its exact transport/pipes/process/tasks close.
                # No await occurs between token release and durable close evidence.
                intent=dict(notification,job_id=notification['planned_job_id'])
                write_once(self.config.control/(notification['planned_job_id']+'.closed.once'),dict(contract='median_parent_transport_closed_v1',
                    job_id=notification['planned_job_id'],notification_sha256=shared.sha(shared.canonical(intent)+b'\n'),receipt=self._last))
            except BaseException:self._state='unknown';self._capacity.unknown=True
