"""Owner-bound fixed volcano generation; durable consumption, original child supervision.

GET never launches. No SQL writes/registry, queue scanner, installation or retry.
Only the pinned Root startup can provide Config; HTTP supplies one request UUID.
"""
import asyncio,json,os,stat,sys
from dataclasses import dataclass,field
from pathlib import Path
from uuid import uuid4
from . import classification_on_demand as shared,volcano_owner_read as plots
from . import de_classification_repository as saved,volcano_runner as runner
from .differential_worker_once import ResultReader
from .raw_qc_artifacts import open_root
from .settings import Budget,Fault
from .validation import canonical_uuid,strict_json
from .raw_qc_once_entry import _eligible_context,_State,_Signals,_settle_and_close

POST_MARKER='cyomics.volcano_post_inflight'
IPC_KEYS={'owner_id','project_id','request_id','job_id','derived_version_id','binding_sha256'}
RECEIPT_KEYS={'schemaVersion','render_id','request_id','binding_sha256','state','disposition','teardown',
    'loop_closed','pending_tasks_seen','async_resources_remaining','interrupted','exit_code'}
STATE_KEYS={'render_id','binding_sha256','state'}
from .settings import production_gate as _GATE,load_runtime_password as _LOADER

def need(ok,status=503,code='r_volcano_unavailable'):
    if not ok:raise Fault(status,code)
def canonical(v):return runner.canonical(v)
def sha(raw):return runner.sha(raw)
def request_input(raw):
    need(type(raw) is bytes and 0<len(raw)<=128,400,'invalid_r_volcano_request')
    try:v=strict_json(raw)
    except (ValueError,UnicodeError,RecursionError):raise Fault(400,'invalid_r_volcano_request') from None
    need(type(v) is dict and set(v)=={'request_id'} and canonical_uuid(v['request_id']),400,'invalid_r_volcano_request')
    return v
def parse_notification(raw):
    need(type(raw) is bytes and 0<len(raw)<=1024)
    v=strict_json(raw)
    need(type(v) is dict and set(v)==IPC_KEYS and shared.canonical(v)+b'\n'==raw
        and all(canonical_uuid(v[k]) for k in IPC_KEYS-{'binding_sha256'}) and shared.hashed(v['binding_sha256']))
    return v

@dataclass(frozen=True,slots=True)
class Config:
    launcher:shared.Config|None=None
    staging_root:Path|None=None
    publish_root:Path|None=None
    profile_json:bytes|None=field(default=None,repr=False)
    scope_json:bytes|None=field(default=None,repr=False)

def prepare(config=None):
    if config is None:return None
    need(type(config) is Config)
    if shared.prepare(config.launcher) is None:return None
    need(all(isinstance(p,Path) and p.is_absolute() and '..' not in p.parts for p in (config.staging_root,config.publish_root)))
    paths=(config.launcher.control,config.staging_root,config.publish_root)
    need(all(a!=b and a not in b.parents and b not in a.parents for i,a in enumerate(paths) for b in paths[i+1:]))
    try:runner.profile(config.profile_json);scope=strict_json(config.scope_json)
    except Exception:raise Fault(503,'r_volcano_unavailable') from None
    need(type(scope) is dict and set(scope)=={'project_id','source_result_id','parameters'}
        and canonical(scope)==config.scope_json and canonical_uuid(scope['project_id']) and canonical_uuid(scope['source_result_id'])
        and type(scope['parameters']) is dict and set(scope['parameters'])==set(runner.contract.PARAMETER_KEYS))
    return config

@dataclass(frozen=True,slots=True)
class Binding:
    identity:dict
    inputs:dict=field(repr=False)
    validated:object=field(repr=False)
    @property
    def digest(self):return sha(canonical(self.identity))
    @property
    def context(self):return self.identity['context']

async def bind(reader,subject,project,version,budget,config):
    need(type(reader) is plots.OwnerReader and type(reader.repository) is saved.ClassificationRepository and type(reader.parent) is ResultReader)
    need(all(canonical_uuid(x) for x in (subject,project,version)),404,'not_found')
    meta=await reader.repository.read_version(subject,project,version,budget);saved.validate_dto(meta)
    need((meta['created_by'],meta['project_id'],meta['derived_version_id'])==(subject,project,version),404,'not_found')
    parent=await reader.parent.read(subject,project,budget,result_id=meta['source_result_id'])
    need((parent['created_by'],parent['project_id'],parent['result_id'])==(subject,project,meta['source_result_id']),404,'not_found')
    need(meta['status']=='CLASSIFIED' and meta['DE_members_evaluated'] is True
        and parent['manifest_sha256']==meta['source_manifest_sha256']
        and saved.digest(parent)==meta['source_binding']['source_metadata_sha256'])
    scope=strict_json(config.scope_json)
    need(scope['project_id']==project and scope['source_result_id']==meta['source_result_id']
        and scope['parameters']==meta['parameters'],404,'not_found')
    context={'project_id':project,'actor_id':subject,'source_result_id':meta['source_result_id'],
        'source_metadata_sha256':saved.digest(parent),'source_manifest_sha256':parent['manifest_sha256'],'derived_version_id':version,
        'derived_metadata_sha256':saved.digest(meta),'derived_manifest_sha256':meta['manifest_sha256'],
        'source_binding_sha256':meta['source_binding_sha256'],'parameters_sha256':meta['parameters_sha256']}
    pins=plots.input_pins(meta);inputs={}
    for pin in pins:
        raw=await reader.repository.read_artifact(subject,project,version,pin['name'],budget)
        need(type(raw) is bytes and len(raw)==pin['bytes'] and sha(raw)==pin['sha256']);inputs[pin['name']]=raw
    # Canonical saved parameter bytes, not an HTTP threshold or regenerated classification.
    need(dict(saved.c.table(inputs['classification-parameters.tsv'],('key','value'),4))==meta['parameters']
        and sha(inputs['classification-parameters.tsv'])==meta['parameters_sha256'])
    try:validated=runner.contract.validate_inputs(*(inputs[n] for n in runner.INPUT_NAMES))
    except Exception:raise Fault(503,'r_volcano_unavailable') from None
    fresh=await reader.repository.read_version(subject,project,version,budget)
    need(saved.digest(fresh)==context['derived_metadata_sha256']);budget.check()
    identity={'context':context,'inputs':pins,'script':plots.SCRIPT,'validator':{'bytes':runner.VALIDATOR.bytes,'sha256':runner.VALIDATOR.sha256},
        'profile_sha256':sha(config.profile_json)}
    return Binding(identity,inputs,validated)

class Store:
    """Fixed private root; bounded canonical regular files, exclusive durable claims."""
    def __init__(self,root):self.root=root
    def read(self,name):
        fd=open_root(self.root)
        try:
            try:f=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=fd)
            except FileNotFoundError:return None
            try:
                before=os.fstat(f);need(stat.S_ISREG(before.st_mode) and before.st_uid==1000 and before.st_nlink==1
                    and stat.S_IMODE(before.st_mode)==0o600 and 0<before.st_size<=65536)
                raw=os.read(f,65537);after=os.fstat(f)
                need(len(raw)==before.st_size and plots.identity(before)==plots.identity(after)
                    and plots.identity(os.stat(name,dir_fd=fd,follow_symlinks=False))==plots.identity(before))
                v=strict_json(raw);need(canonical(v)==raw);return v
            finally:os.close(f)
        finally:os.close(fd)
    def write(self,name,value,*,replace=False):
        raw=canonical(value);need(0<len(raw)<=65536);fd=open_root(self.root);temporary=name+'.'+str(uuid4())+'.tmp' if replace else name
        try:
            try:f=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=fd)
            except FileExistsError:
                need(not replace);return False
            try:
                pos=0
                while pos<len(raw):n=os.write(f,raw[pos:]);need(n>0);pos+=n
                os.fsync(f)
            finally:os.close(f)
            if replace:os.replace(temporary,name,src_dir_fd=fd,dst_dir_fd=fd)
            os.fsync(fd);return True
        finally:os.close(fd)
    def state(self,rid,binding,state):
        need(canonical_uuid(rid) and shared.hashed(binding) and state in ('queued','running','ready','failed','unknown'))
        self.write('state-'+rid+'.json',{'render_id':rid,'binding_sha256':binding,'state':state},replace=True)
    def finish_active(self,rid,binding):
        need(self.read('active-render.json')=={'render_id':rid,'binding_sha256':binding})
        fd=open_root(self.root)
        try:os.unlink('active-render.json',dir_fd=fd);os.fsync(fd)
        finally:os.close(fd)

def alias_name(actor,project,version,request):
    return 'request-'+sha(canonical({'actor_id':actor,'project_id':project,'derived_version_id':version,'request_id':request}))+'.json'
def alias(binding,request,rid):
    return {'request_id':request,'binding_sha256':binding.digest,'render_id':rid,**{k:binding.context[k] for k in ('actor_id','project_id','derived_version_id')}}
def claim_name(binding):return 'claim-'+binding.digest+'.json'
def checked_claim(value,binding):
    need(type(value) is dict and set(value)=={'render_id','binding_sha256','identity'} and canonical_uuid(value['render_id'])
        and value['binding_sha256']==binding.digest and value['identity']==binding.identity)
    return value['render_id']
def ready_descriptor(config,binding):
    provider=plots.FileProvider(plots.Config(True,config.publish_root,1000))
    registration=provider.registration(binding.context,binding.identity['inputs'],config.launcher.verify)
    value=registration.value()
    need(value['render_source_derived_id']==binding.context['derived_version_id']
        and value['render_source_metadata_sha256']==binding.context['derived_metadata_sha256'])
    bodies=provider.artifacts(registration,config.launcher.verify)
    runner.contract.validate_outputs(binding.validated,bodies)
    out={k:value[k] for k in plots.DESCRIPTOR};out['contract']='r_volcano_view_v0.1';return out

def publish(config,binding,outputs,rid):
    need(type(outputs) is dict and set(outputs)==set(plots.NAMES)
        and all(type(x) is bytes and 0<len(x)<=plots.MAX_FILE for x in outputs.values()) and sum(map(len,outputs.values()))<=plots.MAX_TOTAL)
    runner.contract.validate_outputs(binding.validated,outputs)
    context=binding.context
    value={'schemaVersion':1,'contract':'r_volcano_saved_v0.1',**{('created_by' if k=='actor_id' else k):v for k,v in context.items()},
        'artifacts':[dict(name=n,bytes=len(outputs[n]),sha256=sha(outputs[n]),media_type=plots.MEDIA[n]) for n in plots.NAMES],
        'render_source_derived_id':context['derived_version_id'],'render_source_metadata_sha256':context['derived_metadata_sha256'],
        'render_script':plots.SCRIPT,'inputs':binding.identity['inputs']}
    raw=canonical(value);plots.checked_registration(raw,context,binding.identity['inputs'])
    root=config.publish_root;fd=open_root(root);name='.render-'+rid+'.partial';folder=None
    try:
        os.mkdir(name,mode=0o700,dir_fd=fd);folder=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
        for n,body in {**outputs,'metadata.json':raw}.items():
            config.launcher.verify();f=os.open(n,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=folder)
            try:
                pos=0
                while pos<len(body):count=os.write(f,body[pos:]);need(count>0);pos+=count
                os.fchmod(f,0o400);os.fsync(f)
            finally:os.close(f)
        os.fsync(folder);config.launcher.verify()
        from .r_raw_qc.raw_qc_runner import rename_noreplace
        rename_noreplace(root/name,root/context['derived_version_id']);os.fsync(fd)
    finally:
        if folder is not None:os.close(folder)
        os.close(fd)
    ready_descriptor(config,binding)

class Controller(shared.Controller):
    _job_key='job_id'
    _contract_name='r_volcano_render_v0.1'
    _error_prefix='r_volcano'
    @staticmethod
    def _prepare(config):return prepare(config)
    @staticmethod
    def _reader_ok(reader,config):return type(reader) is plots.OwnerReader and reader.provider.config.root==config.publish_root
    async def _bind(self,subject,project,version,budget):return await bind(self.reader,subject,project,version,budget,self.render_config)
    def _descriptor(self,binding):return ready_descriptor(self.render_config,binding)
    def __init__(self,config=None,reader=None,*,capacity=None):
        self.render_config=self._prepare(config);self.reader=reader;self.binding=None;self.render_id=None;self.request_id=None
        if self.render_config is not None:
            need(self._reader_ok(reader,self.render_config))
        super().__init__(None if self.render_config is None else self.render_config.launcher,capacity=capacity)
    @property
    def store(self):return Store(self.render_config.launcher.control)
    def quarantine(self):self._state='unknown';self._capacity.unknown=True
    def _live(self,rid):return self.render_id==rid and self._state in ('reserved','notified') and self._lease is not None
    def _dto(self,binding,request,rid):
        state=self.store.read('state-'+rid+'.json');phase='unknown';descriptor=None
        if state is not None:
            need(set(state)==STATE_KEYS and state['render_id']==rid and state['binding_sha256']==binding.digest
                and state['state'] in ('queued','running','ready','failed','unknown'))
            phase=state['state']
        if phase in ('queued','running') and not self._live(rid):phase='unknown'
        active=self.store.read('active-render.json')
        if active is not None and not self._live(active.get('render_id')):phase='unknown'
        if self._capacity.unknown:phase='unknown'
        if phase=='ready':
            try:descriptor=self._descriptor(binding)
            except Exception:phase='unknown'
        if phase=='unknown':self.quarantine()
        ctx=binding.context
        return {'schemaVersion':1,'contract':self._contract_name,'project_id':ctx['project_id'],'request_id':request,
            'derived_version_id':ctx['derived_version_id'],'source_result_id':ctx['source_result_id'],'render_id':rid,
            'binding_sha256':binding.digest,'state':phase,'observation':'unknown' if phase=='unknown' else 'confirmed',
            'error_code':self._error_prefix+'_submission_unknown' if phase=='unknown' else self._error_prefix+'_render_failed' if phase=='failed' else None,
            'descriptor':descriptor}
    async def status(self,subject,project,version,request,budget):
        need(self.render_config is not None and canonical_uuid(request),404,'not_found')
        binding=await self._bind(subject,project,version,budget)
        value=self.store.read(alias_name(subject,project,version,request));need(value is not None,404,'not_found')
        rid=checked_claim(self.store.read(claim_name(binding)),binding);need(value==alias(binding,request,rid))
        return self._dto(binding,request,rid)
    async def submit(self,subject,project,version,body,budget,*,mark_consumed=lambda:None):
        need(self.render_config is not None and self._state!='off' and not self._closing)
        request=body['request_id'];binding=await self._bind(subject,project,version,budget)
        self._sources();name=alias_name(subject,project,version,request)
        prior=self.store.read(claim_name(binding));existing_alias=self.store.read(name)
        if prior is not None:
            rid=checked_claim(prior,binding);wanted=alias(binding,request,rid)
            need(existing_alias is None or existing_alias==wanted,504,'r_volcano_submission_unknown')
            mark_consumed()
            try:
                if existing_alias is None:need(self.store.write(name,wanted) or self.store.read(name)==wanted)
                value=self._dto(binding,request,rid)
                need(value['state']!='unknown',504,'r_volcano_submission_unknown');return value,False
            except BaseException:self.quarantine();raise
        need(existing_alias is None,504,'r_volcano_submission_unknown')
        if self.store.read('active-render.json') is not None:
            self.quarantine();raise Fault(503,'r_volcano_unavailable')
        if self._capacity.unknown or self._state=='unknown':raise Fault(503,'r_volcano_unavailable')
        need(self._state=='idle' and self._capacity.available,409,'r_volcano_capacity_busy')
        key=(subject,project,request);need(self._capacity.take(self,key));self._lease=shared._Lease(key,self._issuer);self._state='reserved'
        self.binding=binding;self.render_id=str(uuid4());self.request_id=request;rid=self.render_id
        mark_consumed() # Failure of the exclusive write itself can already be uncertain.
        try:
            need(self.store.write('active-render.json',{'render_id':rid,'binding_sha256':binding.digest}))
            need(self.store.write(claim_name(binding),{'render_id':rid,'binding_sha256':binding.digest,'identity':binding.identity}))
            need(self.store.write(name,alias(binding,request,rid)));self.store.state(rid,binding.digest,'queued')
            value=self._dto(binding,request,rid);need(self.notify(self._lease,value),504,'r_volcano_submission_unknown')
            return value,True
        except BaseException:
            self.quarantine();raise Fault(504,'r_volcano_submission_unknown') from None
    def _notification_value(self,dto):
        need(self.binding is not None and dto['render_id']==self.render_id and dto['binding_sha256']==self.binding.digest)
        return {'owner_id':self.binding.context['actor_id'],'project_id':dto['project_id'],'request_id':self.request_id,
            'job_id':self.render_id,'derived_version_id':dto['derived_version_id'],'binding_sha256':self.binding.digest}
    def _receipt(self,raw,code):
        need(type(raw) is bytes and raw.endswith(b'\n') and raw.count(b'\n')==1 and len(raw)<=shared.STDOUT_CAP)
        v=strict_json(raw)
        need(type(v) is dict and set(v)==RECEIPT_KEYS and type(v['schemaVersion']) is int and v['schemaVersion']==1
            and (v['render_id'],v['request_id'],v['binding_sha256'])==(self.render_id,self.request_id,self.binding.digest)
            and type(v['exit_code']) is int and v['exit_code']==code==0 and v['disposition']=='confirmed'
            and v['teardown']=='confirmed' and v['loop_closed'] is True and v['state'] in ('ready','failed')
            and all(v[k] is False for k in ('pending_tasks_seen','async_resources_remaining','interrupted')))
        state=self.store.read('state-'+self.render_id+'.json')
        need(state=={'render_id':self.render_id,'binding_sha256':self.binding.digest,'state':'running'})
        if v['state']=='ready':self._descriptor(self.binding)
        return v
    async def _run(self):
        binding=self.binding;rid=self.render_id
        await super()._run()
        try:
            phase=self._last['state'] if self._state=='idle' and self._last is not None else 'unknown'
            self.store.state(rid,binding.digest,phase)
            if phase in ('ready','failed'):self.store.finish_active(rid,binding.digest)
        except BaseException:self.quarantine()

async def execute(config,notification,reader,*,native_pins,verify,cancelled=lambda:False):
    need(prepare(config) is config and verify() is None)
    n=notification;budget=Budget(12);binding=await bind(reader,n['owner_id'],n['project_id'],n['derived_version_id'],budget,config)
    need(binding.digest==n['binding_sha256']);store=Store(config.launcher.control)
    need(checked_claim(store.read(claim_name(binding)),binding)==n['job_id'])
    need(store.read('active-render.json')=={'render_id':n['job_id'],'binding_sha256':binding.digest})
    need(store.read(alias_name(n['owner_id'],n['project_id'],n['derived_version_id'],n['request_id']))==alias(binding,n['request_id'],n['job_id']))
    need(store.write('execute-'+n['job_id']+'.once',{'render_id':n['job_id'],'binding_sha256':binding.digest}))
    store.state(n['job_id'],binding.digest,'running');attempt=str(uuid4());verify()
    cfg=runner.Config(True,n['job_id'],attempt,binding.digest,config.staging_root,config.profile_json,native_pins,verify)
    try:output=runner.VolcanoRunner(cfg).run(binding.inputs,cancelled=cancelled)
    except runner.shared.Log2RunFailure as error:
        facts=error.facts.snapshot() if type(error.facts) is runner.shared.FailureFacts else {}
        if (facts.get('cleanup_known') is True and facts.get('job_id')==n['job_id'] and facts.get('attempt_id')==attempt
                and facts.get('configuration_sha256')==sha(canonical({'binding_sha256':binding.digest,'profile_sha256':sha(config.profile_json)}))):
            return 'failed'
        raise
    need(type(output) is runner.Output and output.receipt.get('all_five_outputs_verified') is True
        and output.receipt.get('binding_sha256')==binding.digest and output.receipt.get('profile_sha256')==sha(config.profile_json))
    fresh=await bind(reader,n['owner_id'],n['project_id'],n['derived_version_id'],Budget(12),config)
    need(fresh.identity==binding.identity and verify() is None);publish(config,binding,output.files,n['job_id'])
    store.write('native-'+n['job_id']+'.json',output.receipt);verify();return 'ready'

async def _from_factory(config,n,*,activate_factory,verify,native_pins,cancelled):
    from . import app as server,settings,repository
    factory=server.create_app;pool_type=repository.FixedPool;application=None;outcome='unknown';closed=True
    def original():
        need(settings.production_gate is _GATE and server.production_gate is _GATE and settings.load_runtime_password is _LOADER
            and repository.load_runtime_password is _LOADER and server.FixedPool is pool_type and repository.FixedPool is pool_type
            and server.create_app is factory)
    try:
        need(verify() is None and settings.AUTH_POLICY_VERIFIED is False and settings.RUNTIME_START_APPROVED is False);original()
        called=False
        def create():
            nonlocal application,called
            need(not called and verify() is None);called=True;original();_GATE();application=factory();return application
        returned=activate_factory(create);need(called and returned is application and application is not None);original();_GATE()
        pool=application.state.repository.pool;need(type(pool) is pool_type)
        reader=plots.OwnerReader(saved.ClassificationRepository(pool),ResultReader(pool),plots.FileProvider(plots.Config(True,config.publish_root,1000)))
        outcome=await execute(config,n,reader,native_pins=native_pins,verify=verify,cancelled=cancelled)
    finally:
        if application is not None:
            try:
                pool=application.state.repository.pool;handles=tuple(pool._idle)+tuple(pool._active)
                pool.close();need(not pool._active and all(h.closed is True for h in handles))
            except BaseException:closed=False
            try:application.state.complete_imports.close()
            except BaseException:closed=False
            try:await application.state.authority.close();need(application.state.authority._client.is_closed is True)
            except BaseException:closed=False
    need(closed and verify() is None);return outcome

def main_from_server(raw=None,*,config=None,activate_factory=None,verify=None,native_pins=()):
    state=_State();fence=_Signals(state);loop=None;pending=resources=False;closed=None;n=None;outcome='unknown'
    try:
        need(raw is not None and prepare(config) is config and callable(activate_factory) and callable(verify)
            and _eligible_context() and verify() is None)
        n=parse_notification(raw);fence.install();loop=asyncio.new_event_loop()
        def quiet(_loop,_ctx):state.loop_error=True
        loop.set_exception_handler(quiet)
        outcome=loop.run_until_complete(_from_factory(config,n,activate_factory=activate_factory,verify=verify,
            native_pins=native_pins,cancelled=lambda:state.interrupted))
    except BaseException:state.loop_error=True
    finally:
        if loop is not None:pending,resources,closed=_settle_and_close(loop,state)
        fence.close()
    confirmed=outcome in ('ready','failed') and closed is True and not pending and not resources and not state.interrupted and not state.loop_error and not state.cleanup_error
    value={'schemaVersion':1,'render_id':None if n is None else n['job_id'],'request_id':None if n is None else n['request_id'],
        'binding_sha256':None if n is None else n['binding_sha256'],'state':outcome if confirmed else 'unknown',
        'disposition':'confirmed' if confirmed else 'unknown','teardown':'confirmed' if confirmed else 'unknown','loop_closed':closed,
        'pending_tasks_seen':pending,'async_resources_remaining':resources,'interrupted':state.interrupted,'exit_code':0 if confirmed else 1}
    sys.stdout.write(shared.canonical(value).decode()+'\n');sys.stdout.flush();return value['exit_code']
