"""Actual RawQCJobs + candidate app/assembly/launcher, synthetic dependencies."""
import asyncio,copy,importlib,importlib.util,json,os,pathlib,runpy,sys,types,unittest
from unittest.mock import patch
H=pathlib.Path(__file__).resolve().parents[1]
REPO=H
LAUNCH=REPO/'cyomics_projects'
f=runpy.run_path(str(REPO/'tests/test_raw_qc_http.py'))
package=sys.modules['cyomics_projects'];package.__path__.insert(0,str(LAUNCH));package.__path__.insert(0,str(REPO/'cyomics_projects'))
for name in ('cyomics_projects.app','cyomics_projects.raw_qc_api_assembly','cyomics_projects.raw_qc_single_launcher'):sys.modules.pop(name,None)
a=importlib.import_module('cyomics_projects.app');m=importlib.import_module('cyomics_projects.raw_qc_api_assembly');l=importlib.import_module('cyomics_projects.raw_qc_single_launcher')
from cyomics_projects.raw_qc_repository import EnqueueObservation
from cyomics_projects.raw_qc_contract import ExecutionRegistration,canonical,binding_sha
from cyomics_projects.settings import Fault
PUBLIC=copy.deepcopy(f['PUBLIC']);PUBLIC['execution']=json.loads(m.EXECUTION_JSON);PUBLIC['binding_sha256']=binding_sha(PUBLIC['request_sha256'],PUBLIC['source'],PUBLIC['execution'],PUBLIC['quant_family'])
events=[]
class Pool:
 instances=[]
 def __init__(self):self.closed=0;self.fail=False;self.instances.append(self);events.append('pool_new')
 async def getconn(self):raise AssertionError('NO REAL DATABASE')
 def close(self):
  self.closed+=1;events.append('pool_close')
  if self.fail:raise OSError('SYNTHETIC_POOL_CLOSE')
class Repo:
 instances=[]
 def __init__(self,pool,registration=None,*,enqueue_observation_enabled=False):
  self.pool=pool;self.registration=registration;self._enqueue_observation_enabled=enqueue_observation_enabled;self.enqueue_observation_locked=False;self.calls=[];self.created=True;self.commit=True;self.teardown=True;self.instances.append(self)
 def check(self,args,kind):
  self.calls.append(kind)
  if args[0]!=f['OWNER']:raise Fault(404,'dataset_not_found')
 async def enqueue(self,*args):self.check(args,'legacy');return copy.deepcopy(PUBLIC)
 async def enqueue_observed(self,*args):
  self.check(args,'observed');return EnqueueObservation(canonical(PUBLIC),self.created,self.commit,self.teardown)
 async def read_request(self,*args):self.check(args,'request');return copy.deepcopy(PUBLIC)
 async def read_job(self,*args):self.check(args,'job');return copy.deepcopy(PUBLIC)
class Reader:
 def __init__(self,root):self.root=root
class Auth(f['SyntheticAuthority']):
 instances=[]
 def __init__(self,transport,loader):super().__init__();self.closed=0;self.fail=False;self.instances.append(self);events.append('auth_new')
 async def close(self):
  self.closed+=1;events.append('auth_close')
  if self.fail:raise OSError('SYNTHETIC_AUTH_CLOSE')
class Imports:
 def __init__(self,*args,**kwargs):self.closed=self.started=0;self.fail=False
 def start(self):self.started+=1
 def close(self):
  self.closed+=1;events.append('imports_close')
  if self.fail:raise OSError('SYNTHETIC_IMPORT_CLOSE')
class Stream:
 def __init__(self,raw):self.raw=raw
 async def read(self,n):v=self.raw[:n];self.raw=self.raw[n:];return v
class Transport:
 _finished=True
 def __init__(self):self.closed=False
 def close(self):self.closed=True
 def is_closing(self):return self.closed
 def get_pipe_transport(self,fd):return None
class Proc:
 pid=999002;returncode=0
 def __init__(self):
  value={'schemaVersion':1,'action':'run_once','status':'confirmed_succeeded','reason':'succeeded','exit_code':0,'dispatch_called':True,'dispatch_status':'confirmed','dispatch_reason':'succeeded','attempt_id':'00000000-0000-0000-0000-000000000009','job_id':'00000000-0000-0000-0000-000000000008','teardown':'confirmed','loop_created':True,'loop_closed':True,'pending_tasks_seen':False,'async_resources_remaining':False,'interrupted':False,'automatic_retry':False,'ACK_calls':0,'next_jobs':0,'Owner_or_API_E2E':False}
  self.stdout=Stream((json.dumps(value)+'\n').encode());self.stderr=Stream(b'');self._transport=Transport()
 async def wait(self):return 0
def cfg():return l.Config(True,'a'*64,'b'*64,'c'*64,'d'*64)
def api():return m.RawQCAPIConfig(m.EXECUTION_JSON,m.PUBLISH_ROOT,1000)
class Tests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  events.clear();Pool.instances.clear();Repo.instances.clear();Auth.instances.clear();self.spawn=[];self.reserved=[]
  async def spawn():self.spawn.append(1);return Proc()
  self.stack=__import__('contextlib').ExitStack()
  for obj,name,value in ((a,'Authority',Auth),(a,'FixedPool',Pool),(a,'CompleteImports',Imports),(m,'_components',lambda:(Pool,Repo,Reader,ExecutionRegistration)),(l,'_repo_types',lambda:(Repo,EnqueueObservation)),(l,'_sources',lambda config:None),(l,'_reserve',lambda key:self.reserved.append(key)),(l,'_spawn',spawn)):
   self.stack.enter_context(patch.object(obj,name,value))
 def tearDown(self):self.stack.close()
 def make(self,enabled=True):
  return a.create_app(auth_transport=object(),pool=Pool(),anon_key_loader=lambda:None,raw_qc_api_config=api(),raw_qc_launcher_config=cfg() if enabled else None)
 async def test_actual_handler_POST_full_unchanged_DTO_and_one_controller(self):
  app=self.make();controller=app.state.raw_qc_single_launcher;repo=app.state.raw_qc_artifacts.repository
  self.assertIs(type(app.state.raw_qc_jobs),f['RawQCJobs']);self.assertIs(app.state.raw_qc_jobs.authority,app.state.authority);self.assertIs(app.state.raw_qc_artifacts.authority,app.state.authority)
  self.assertIs(app.state.raw_qc_jobs.repository._repository,repo);self.assertIs(app.state.raw_qc_jobs.repository._launcher,controller);self.assertIs(repo.pool,app.state.repository.pool);self.assertTrue(repo._enqueue_observation_enabled)
  out=await f['invoke'](app.state.raw_qc_jobs,f['scope']('POST'),f['body']());self.assertEqual(f['status'](out),202);self.assertEqual(json.loads(out[-1]['body']),PUBLIC)
  await controller._monitor;self.assertEqual(self.spawn,[1]);self.assertEqual(repo.calls,['observed']);self.assertEqual(controller.snapshot()['notification_job_id'],PUBLIC['job_id']);self.assertNotEqual(controller.snapshot()['worker_outcome']['actual_job_id'],PUBLIC['job_id'])
 async def test_same_POST_replay_reads_only_and_GET_never_launches(self):
  app=self.make();h=app.state.raw_qc_jobs;repo=app.state.raw_qc_artifacts.repository
  await f['invoke'](h,f['scope']('POST'),f['body']());await app.state.raw_qc_single_launcher._monitor
  self.assertEqual(f['status'](await f['invoke'](h,f['scope']('POST'),f['body']())),202)
  for scope in (f['scope'](),f['scope'](detail=True)):self.assertEqual(f['status'](await f['invoke'](h,scope)),200)
  self.assertEqual(self.spawn,[1]);self.assertEqual(repo.calls,['observed','request','request','job']);self.assertEqual(app.state.authority.calls,4)
 async def test_None_retains_original_legacy_enqueue_and_reader(self):
  app=self.make(False);self.assertIsNone(app.state.raw_qc_single_launcher);self.assertIs(app.state.raw_qc_jobs.repository,app.state.raw_qc_artifacts.repository)
  self.assertFalse(app.state.raw_qc_jobs.repository._enqueue_observation_enabled)
  out=await f['invoke'](app.state.raw_qc_jobs,f['scope']('POST'),f['body']());self.assertEqual(f['status'](out),202);self.assertEqual(app.state.raw_qc_jobs.repository.calls,['legacy']);self.assertEqual(self.spawn,[])
 async def test_Auth_body_and_Owner_fault_priority_precede_notify(self):
  app=self.make();h=app.state.raw_qc_jobs;repo=app.state.raw_qc_artifacts.repository
  bad=f['scope']('POST');bad['headers']=bad['headers'][:1]+bad['headers'][2:]
  self.assertEqual(f['status'](await f['invoke'](h,bad,f['body']())),401);self.assertEqual(repo.calls,[]);self.assertEqual(self.reserved,[])
  badbody=json.loads(f['body']());badbody['owner_id']='forbidden';out=await f['invoke'](h,f['scope']('POST'),json.dumps(badbody).encode());self.assertEqual(f['status'](out),400);self.assertEqual(repo.calls,[]);self.assertEqual(self.reserved,[])
  app.state.authority.subject=f['OTHER'];out=await f['invoke'](h,f['scope']('POST'),f['body']());self.assertEqual(f['status'](out),404);self.assertEqual(repo.calls,['observed']);self.assertEqual(self.spawn,[])
 async def test_first_existing_replay_no_spawn_and_unconfirmed_observation_unknown(self):
  app=self.make();repo=app.state.raw_qc_artifacts.repository;repo.created=False
  self.assertEqual(f['status'](await f['invoke'](app.state.raw_qc_jobs,f['scope']('POST'),f['body']())),202);self.assertEqual(self.spawn,[])
  app=self.make();repo=app.state.raw_qc_artifacts.repository;repo.teardown=False
  out=await f['invoke'](app.state.raw_qc_jobs,f['scope']('POST'),f['body']());self.assertEqual(f['status'](out),504);self.assertEqual(json.loads(out[-1]['body'])['error']['code'],'raw_qc_submission_unknown');self.assertEqual(self.spawn,[])
 async def test_disabled_and_invalid_launcher_config_never_enables_observer(self):
  app=a.create_app(auth_transport=object(),pool=Pool(),anon_key_loader=lambda:None,raw_qc_api_config=api(),raw_qc_launcher_config=l.Config())
  self.assertIsNone(app.state.raw_qc_single_launcher);self.assertFalse(app.state.raw_qc_artifacts.repository._enqueue_observation_enabled)
  for launcher in (object(),l.Config(True)):
   with self.assertRaises(m.APIAssemblyRefused):a.create_app(raw_qc_api_config=api(),raw_qc_launcher_config=launcher)
  with self.assertRaises(m.APIAssemblyRefused):a.create_app(raw_qc_launcher_config=cfg())
 async def test_real_lifespan_one_close_each_in_new_branch(self):
  app=self.make();controller=app.state.raw_qc_single_launcher;calls=[];original=controller.close
  async def close():calls.append(1);events.append('controller_close');await original()
  with patch.object(controller,'close',side_effect=close):
   async with app.router.lifespan_context(app):pass
  self.assertEqual(calls,[1]);self.assertEqual(app.state.complete_imports.closed,1);self.assertEqual(app.state.authority.closed,1);self.assertEqual(app.state.repository.pool.closed,1)
  self.assertEqual(events[-4:],['imports_close','controller_close','auth_close','pool_close'])
 async def test_Auth_close_failure_still_closes_pool_once(self):
  app=self.make();app.state.authority.fail=True
  with self.assertRaises(OSError):
   async with app.router.lifespan_context(app):pass
  self.assertEqual(app.state.authority.closed,1);self.assertEqual(app.state.repository.pool.closed,1)
 async def test_import_or_controller_close_failure_attempts_every_remaining_resource(self):
  app=self.make();app.state.complete_imports.fail=True
  with self.assertRaises(OSError):
   async with app.router.lifespan_context(app):pass
  self.assertEqual(app.state.authority.closed,1);self.assertEqual(app.state.repository.pool.closed,1)
  app=self.make();controller=app.state.raw_qc_single_launcher
  with patch.object(controller,'close',side_effect=OSError('SYNTHETIC_CONTROLLER_CLOSE')):
   with self.assertRaises(OSError):
    async with app.router.lifespan_context(app):pass
  self.assertEqual(app.state.authority.closed,1);self.assertEqual(app.state.repository.pool.closed,1)
 async def test_None_close_is_not_proof_unknown_or_pending_fail_after_other_cleanup(self):
  app=self.make();controller=app.state.raw_qc_single_launcher;controller._state='unknown'
  with self.assertRaises(m.APIAssemblyRefused):
   async with app.router.lifespan_context(app):pass
  self.assertEqual(app.state.authority.closed,1);self.assertEqual(app.state.repository.pool.closed,1)
  app=self.make();controller=app.state.raw_qc_single_launcher;pending=asyncio.create_task(asyncio.Event().wait());controller._tasks.add(pending)
  try:
   with self.assertRaises(m.APIAssemblyRefused):
    async with app.router.lifespan_context(app):pass
   self.assertEqual(app.state.repository.pool.closed,1)
  finally:
   pending.cancel()
   try:await pending
   except asyncio.CancelledError:pass
 async def test_old_production_gate_still_refuses_before_new_pool_Auth(self):
  before=len(Pool.instances);auth=len(Auth.instances)
  with self.assertRaises(a.production_gate.__globals__['RuntimeNotApproved']):a.create_app(raw_qc_api_config=api(),raw_qc_launcher_config=cfg())
  self.assertEqual(len(Pool.instances),before);self.assertEqual(len(Auth.instances),auth)
 async def test_pool_close_failure_is_not_silently_reported_closed(self):
  app=self.make();app.state.repository.pool.fail=True
  with self.assertRaises(OSError):
   async with app.router.lifespan_context(app):pass
  self.assertEqual(app.state.complete_imports.closed,1);self.assertEqual(app.state.authority.closed,1);self.assertEqual(app.state.repository.pool.closed,1)
if __name__=='__main__':unittest.main(verbosity=2)
