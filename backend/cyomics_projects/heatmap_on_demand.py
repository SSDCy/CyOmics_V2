"""Explicit saved-input heatmap adapter; original persistent plot controller reused.
No SQL writes, input reconstruction, queue scan or statistical recomputation.
"""
import os,sys,asyncio
from pathlib import Path
from uuid import UUID,uuid4
from . import volcano_on_demand as original,heatmap_owner_read as plots,heatmap_runner as runner
from . import de_classification_repository as saved
from .differential_worker_once import ResultReader
from .sample_repository import SampleDesignRepository
from .raw_qc_artifacts import open_root
from .raw_qc_once_entry import _eligible_context,_State,_Signals,_settle_and_close
from .settings import Budget,Fault
from .validation import canonical_uuid,strict_json
from .settings import production_gate as _GATE,load_runtime_password as _LOADER
shared=original.shared
Config=original.Config
Binding=original.Binding
Store=original.Store
canonical=original.canonical
sha=original.sha
need=original.need
parse_notification=original.parse_notification
request_input=original.request_input
claim_name=original.claim_name
checked_claim=original.checked_claim
alias=original.alias
alias_name=original.alias_name
POST_MARKER='cyomics.heatmap_post_inflight'
RECEIPT_KEYS=original.RECEIPT_KEYS
def prepare(config=None):
    if config is None:return None
    need(type(config) is Config)
    if shared.prepare(config.launcher) is None:return None
    need(all(isinstance(p,Path) and p.is_absolute() and '..' not in p.parts for p in (config.staging_root,config.publish_root)))
    paths=(config.launcher.control,config.staging_root,config.publish_root)
    need(all(a!=b and a not in b.parents and b not in a.parents for i,a in enumerate(paths) for b in paths[i+1:]))
    try:runner.profile(config.profile_json);scope=strict_json(config.scope_json)
    except Exception:raise Fault(503,'r_heatmap_unavailable') from None
    need(type(scope) is dict and set(scope)=={'project_id','source_result_id','parameters'}
        and canonical(scope)==config.scope_json and canonical_uuid(scope['project_id']) and canonical_uuid(scope['source_result_id'])
        and type(scope['parameters']) is dict and set(scope['parameters'])=={'schema_version','input_scale','adjusted_p_max','absolute_log2fc_min'})
    return config

async def read_matrix(reader,subject,project,parent,budget):
    """Original Owner transaction defaults READ ONLY; no claim/run/registry call."""
    repo=reader.parent.owner
    need(type(repo) is saved.admission.DifferentialRepository and canonical_uuid(parent['job_id']))
    async def operation(query):
        await repo._owner(query,subject,project)
        row=await query("SELECT matrix_bytes,octet_length(matrix_bytes),encode(sha256(matrix_bytes),'hex'),input_manifest_json FROM cyomics_app.differential_jobs WHERE job_id=%s AND owner_id=%s AND project_id=%s",
            (UUID(parent['job_id']),UUID(subject),UUID(project)))
        need(row is not None and len(row)==4)
        raw=saved.body(row[0]);pin=plots.pin(raw)
        need(plots.valid_pin(pin) and pin==parent['input_manifest']['matrix'] and row[1]==len(raw) and row[2]==pin['sha256']
            and saved.admission.document(row[3],4096)==parent['input_manifest'])
        return raw
    return await repo._transaction(subject,budget,operation)

async def bind(reader,subject,project,version,budget,config):
    need(type(reader) is plots.OwnerReader and type(reader.repository) is saved.ClassificationRepository
        and type(reader.parent) is ResultReader and type(reader.samples) is SampleDesignRepository)
    need(all(canonical_uuid(x) for x in (subject,project,version)),404,'not_found')
    meta=await reader.repository.read_version(subject,project,version,budget);saved.validate_dto(meta)
    need((meta['created_by'],meta['project_id'],meta['derived_version_id'])==(subject,project,version),404,'not_found')
    parent=await reader.parent.read(subject,project,budget,result_id=meta['source_result_id'])
    need((parent['created_by'],parent['project_id'],parent['result_id'])==(subject,project,meta['source_result_id']),404,'not_found')
    need(meta['status']=='CLASSIFIED' and meta['DE_members_evaluated'] is True
        and parent['manifest_sha256']==meta['source_manifest_sha256']
        and saved.digest(parent)==meta['source_binding']['source_metadata_sha256'])
    scope=strict_json(config.scope_json)
    need(scope['project_id']==project and scope['source_result_id']==meta['source_result_id'] and scope['parameters']==meta['parameters'],404,'not_found')
    context,pins,samples,provenance=await plots.bound_input_context(reader.samples,subject,project,meta,parent,budget)
    selected=await reader.repository.read_artifact(subject,project,version,'selected-DE.tsv',budget)
    parameters=await reader.repository.read_artifact(subject,project,version,'classification-parameters.tsv',budget)
    need(type(parameters) is bytes and sha(parameters)==meta['parameters_sha256']
        and dict(saved.c.table(parameters,('key','value'),4))==meta['parameters'])
    inputs={'matrix.tsv':await read_matrix(reader,subject,project,parent,budget),'samples.tsv':samples,
        'selected-DE.tsv':selected,'provenance.json':provenance}
    need(all(type(inputs[x['name']]) is bytes and plots.pin(inputs[x['name']])=={k:x[k] for k in ('bytes','sha256')} for x in pins))
    try:validated=runner.contract.validate_inputs(*(inputs[n] for n in runner.INPUT_NAMES))
    except Exception:raise Fault(503,'r_heatmap_unavailable') from None
    fresh=await reader.repository.read_version(subject,project,version,budget)
    need(saved.digest(fresh)==context['derived_metadata_sha256']);budget.check()
    return Binding({'context':context,'inputs':pins,'script':plots.SCRIPT,
        'validator':{'bytes':runner.VALIDATOR.bytes,'sha256':runner.VALIDATOR.sha256},'profile_sha256':sha(config.profile_json)},inputs,validated)

def ready_descriptor(config,binding):
    provider=plots.FileProvider(plots.Config(True,config.publish_root,1000))
    registered,bodies=provider.read(binding.context,binding.identity['inputs'],config.launcher.verify)
    need(all(bodies[n]==raw for n,raw in binding.inputs.items()))
    runner.contract.validate_outputs(binding.validated,{n:bodies[n] for n in plots.NAMES})
    value={k:registered[k] for k in plots.DESCRIPTOR};value['contract']='r_heatmap_view_v0.1';return value

def publish(config,binding,outputs,rid):
    need(type(outputs) is dict and set(outputs)==set(plots.NAMES) and all(type(x) is bytes and 0<len(x)<=plots.MAX_FILE for x in outputs.values()))
    runner.contract.validate_outputs(binding.validated,outputs)
    value={'schemaVersion':1,'contract':'r_heatmap_saved_v0.1',
        **{('created_by' if k=='actor_id' else k):v for k,v in binding.context.items()},
        'artifacts':[dict(name=n,bytes=len(outputs[n]),sha256=sha(outputs[n]),media_type=plots.MEDIA[n]) for n in plots.NAMES],
        'render_script':plots.SCRIPT,'inputs':binding.identity['inputs']}
    raw=canonical(value);plots.checked_registration(raw,binding.context,binding.identity['inputs'])
    root=config.publish_root;fd=open_root(root);name='.render-'+rid+'.partial';folder=None
    try:
        os.mkdir(name,mode=0o700,dir_fd=fd);folder=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
        for n,body in {**binding.inputs,**outputs,'metadata.json':raw}.items():
            config.launcher.verify();f=os.open(n,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=folder)
            try:
                pos=0
                while pos<len(body):count=os.write(f,body[pos:]);need(count>0);pos+=count
                os.fchmod(f,0o400);os.fsync(f)
            finally:os.close(f)
        os.fsync(folder);config.launcher.verify()
        from .r_raw_qc.raw_qc_runner import rename_noreplace
        rename_noreplace(root/name,root/binding.context['derived_version_id']);os.fsync(fd)
    finally:
        if folder is not None:os.close(folder)
        os.close(fd)
    ready_descriptor(config,binding)

class Controller(original.Controller):
    _contract_name='r_heatmap_render_v0.1'
    _error_prefix='r_heatmap'
    @staticmethod
    def _prepare(config):return prepare(config)
    @staticmethod
    def _reader_ok(reader,config):return type(reader) is plots.OwnerReader and reader.provider.config.root==config.publish_root
    async def _bind(self,subject,project,version,budget):return await bind(self.reader,subject,project,version,budget,self.render_config)
    def _descriptor(self,binding):return ready_descriptor(self.render_config,binding)

async def execute(config,notification,reader,*,native_pins,verify,cancelled=lambda:False):
    need(prepare(config) is config and verify() is None)
    n=notification;budget=Budget(12);binding=await bind(reader,n['owner_id'],n['project_id'],n['derived_version_id'],budget,config)
    need(binding.digest==n['binding_sha256']);store=Store(config.launcher.control)
    need(checked_claim(original.render_claim(store,binding),binding)==n['job_id'])
    need(store.read('active-render.json')=={'render_id':n['job_id'],'binding_sha256':binding.digest})
    need(store.read(alias_name(n['owner_id'],n['project_id'],n['derived_version_id'],n['request_id']))==alias(binding,n['request_id'],n['job_id']))
    need(store.write('execute-'+n['job_id']+'.once',{'render_id':n['job_id'],'binding_sha256':binding.digest}))
    store.state(n['job_id'],binding.digest,'running');attempt=str(uuid4());verify()
    cfg=runner.Config(True,n['job_id'],attempt,binding.digest,config.staging_root,config.profile_json,native_pins,verify)
    try:output=runner.HeatmapRunner(cfg).run(binding.inputs,cancelled=cancelled)
    except runner.shared.Log2RunFailure as error:
        facts=error.facts.snapshot() if type(error.facts) is runner.shared.FailureFacts else {}
        original.private_failure(store,n,binding,attempt,config.profile_json,error,facts)
        if (facts.get('cleanup_known') is True and facts.get('job_id')==n['job_id'] and facts.get('attempt_id')==attempt
                and facts.get('configuration_sha256')==sha(canonical({'binding_sha256':binding.digest,'profile_sha256':sha(config.profile_json)}))):
            return 'failed'
        raise
    need(type(output) is runner.Output and output.receipt.get('all_eight_outputs_verified') is True
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
        reader=plots.OwnerReader(saved.ClassificationRepository(pool),ResultReader(pool),SampleDesignRepository(pool),plots.FileProvider(plots.Config(True,config.publish_root,1000)))
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
