"""Offline only: fixed source overlay, synthetic metadata/bytes and fake transactions.
Never creates a real database connection, native child, approval, service or socket.
"""
import asyncio,copy,importlib.util,json,sys,types,unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch,Mock
HERE=Path(__file__).resolve().parent
BASE=HERE.parent
# Both a normal checkout and a complete candidate tree have backend/ + frontend/.
AUDIT=BASE.parent/'frontend/tests/fixtures/preprocess-v2-contract'
R1_FIXTURES=AUDIT/'R1-addendum/fixtures/R1'
sys.path.insert(0,str(BASE));sys.path.insert(0,str(BASE/'tests'))
import test_raw_qc_contract # Historical fake DB support replaces the namespace once, before overlay imports.
import cyomics_projects
cyomics_projects.__path__.insert(0,str(HERE.parent/'cyomics_projects'))
from cyomics_projects import app as app_overlay,preprocess_http as http_overlay,preprocess_contract as c,preprocess_repository as p,preprocess_execution as e,preprocess_dev_probe as probe,log2_matrix_runner as r
from cyomics_projects.settings import Budget,Fault

def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module
oldrunner=load('reference_runner_tests',BASE/'tests/test_log2_matrix_runner.py')
oldrepo=load('reference_repo_tests',BASE/'tests/test_preprocess_repository.py')
oldhttp=load('reference_http_tests',HERE/'support/reference_preprocess_http.py')
# Ensure the old HTTP helper's historic __path__ prepend cannot replace this overlay.
cyomics_projects.__path__.insert(0,str(HERE.parent/'cyomics_projects'))

def fixture(name):
 if name=='R1/failed-null-runtime-invalid.json':path=AUDIT/'final-failed-state/failed-null-runtime-invalid.json'
 elif name.startswith('R1/'):path=R1_FIXTURES/name[3:]
 else:path=AUDIT/'fixtures'/name
 return json.loads(path.read_bytes())
Q=fixture('positive/queued.json');RUN=fixture('positive/running.json');OK=fixture('positive/succeeded.json');VER=fixture('positive/processed-version.json')
PROJECT=Q['source']['project_id'];DATASET=Q['source']['dataset_version_id'];JOB=Q['job_id'];ATTEMPT=RUN['attempt_id'];OWNER=VER['created_by'];REG=RUN['runtime']['registration_id']

def bind_fake_watch(watch):
 watch.bound={watch.process.pid:types.SimpleNamespace(proc=types.SimpleNamespace(pid=watch.process.pid,starttick=123,uid=1000,inode=456))}
oldrunner.Watch.bind_top=bind_fake_watch # Only synthetic observer witnesses, product untouched.

class ContractTests(unittest.TestCase):
 def test_positive20key_task_and_version_fixtures(self):
  for name in ['queued','running','reconciliation-pending','failed','succeeded']:
   value=fixture('positive/'+name+'.json');self.assertEqual(len(c.task_value(value)),20)
  self.assertEqual(c.version_value(VER,task=OK),VER)
 def test_negative_envelopes_and_failed_missing_runtime(self):
  for path in sorted((AUDIT/'fixtures/negative').glob('*.json')):
   value=json.loads(path.read_bytes())
   with self.subTest(path=path.name),self.assertRaises(Exception):
    (c.version_value if 'processed_dataset_version_id' in value else c.task_value)(value)
  with self.assertRaises(Exception):c.task_value(fixture('R1/failed-null-runtime-invalid.json'))
 def test_integer_zero_lexical_and_string_preservation(self):
  c.request_input((R1_FIXTURES/'request-int0-valid.json').read_bytes())
  for name in ['negative-zero','float-zero','exponent-zero']:
   with self.assertRaises(Exception):c.request_input((R1_FIXTURES/f'request-{name}-invalid.json').read_bytes())
  self.assertEqual(c.document(b'{"note":"-0 0.0 0e0"}')['note'],'-0 0.0 0e0')
 def test_actual_second_page_canonical_cursor_roundtrip_and_exclusive_queries(self):
  from cyomics_projects.preprocess_http import route_input
  first=fixture('R1/list-first-page.json');second=fixture('R1/list-second-page.json')
  c.list_value(first,project=PROJECT,dataset=DATASET);c.list_value(second,project=PROJECT,dataset=DATASET)
  cursor=first['next_cursor'];self.assertEqual(len(cursor),130);x=c.decode_cursor(cursor);self.assertEqual(c.encode_cursor(x['createdAt'],x['job_id']),cursor)
  self.assertLess((second['items'][0]['created_at'],second['items'][0]['job_id']),(x['createdAt'],x['job_id']))
  path='/api/v1/datasets/'+DATASET+'/preprocessing-jobs';query='project_id='+PROJECT+'&limit=1&cursor='+cursor
  route=route_input({'raw_path':path.encode(),'path':path,'query_string':query.encode(),'method':'GET'})
  self.assertEqual((route['kind'],route['cursor']),('list',cursor));self.assertLess(len(query),512)
  for suffix in ['&request_id='+Q['request_id'],'&cursor='+cursor,'&limit=01']:
   with self.assertRaises(Fault):route_input({'raw_path':path.encode(),'path':path,'query_string':(query+suffix).encode(),'method':'GET'})
 def test_cursor_target_and_json_caps(self):
  from cyomics_projects.preprocess_http import route_input
  for cur in ['x'*193,fixture('R1/list-first-page.json')['next_cursor']+'=', 'a']:
   with self.assertRaises(Exception):c.decode_cursor(cur)
  path='/api/v1/datasets/'+DATASET+'/preprocessing-jobs'
  for raw,query in [(path.encode(),b'x'*513),(b'x'*1025,b'')]:
   with self.assertRaises(Fault):route_input({'raw_path':raw,'path':path,'query_string':query,'method':'GET'})
  with self.assertRaises(Exception):c.document(b' '*32769)
 def test_manifest_exact11_sorted_bytes_hash_types(self):
  for mutate in [lambda v:v.pop(),lambda v:v.reverse(),lambda v:v[0].update(bytes=True),lambda v:v[0].update(bytes=26214401),lambda v:v[0].update(name='../secret')]:
   v=copy.deepcopy(VER['artifacts']);mutate(v)
   with self.assertRaises(Exception):c.artifact_manifest(v)
 def test_no_approval_defaultoff_and_no_gate_mutation(self):
  from cyomics_projects import settings
  before=(settings.AUTH_POLICY_VERIFIED,settings.RUNTIME_START_APPROVED,settings.production_gate,settings.load_runtime_password)
  self.assertIsNone(probe.bootstrap());self.assertIsNone(e.create_runtime());self.assertEqual(asyncio.run(probe.run_probe()),{'status':'off'})
  self.assertEqual(asyncio.run(probe.run_worker()),{'status':'off'})
  self.assertEqual(before,(settings.AUTH_POLICY_VERIFIED,settings.RUNTIME_START_APPROVED,settings.production_gate,settings.load_runtime_password))
 def test_api_config_pure_default_off_invalid_before_pool(self):
  from cyomics_projects.preprocess_api_assembly import prepare_preprocess_api_config,bind_preprocess_api,PreprocessAPIConfig
  self.assertIsNone(prepare_preprocess_api_config());self.assertIsNone(bind_preprocess_api())
  for bad in [True,{},PreprocessAPIConfig(b'{}')]:
   with self.assertRaises(Fault):prepare_preprocess_api_config(bad)

class RunnerFactsTests(unittest.TestCase):
 def test_old_fake_runner_suite_preserves_prior18_cases(self):
  suite=unittest.defaultTestLoader.loadTestsFromModule(oldrunner);result=unittest.TestResult();suite.run(result)
  self.assertEqual(result.testsRun,18);self.assertFalse(result.errors+result.failures,result.errors+result.failures)
 def test_known_nochild_fact_snapshot_and_attempt_binding(self):
  conf=replace(oldrunner.config(),enabled=False,attempt_id=ATTEMPT)
  with patch.object(r,'_runtime') as native:
   with self.assertRaises(r.Log2RunFailure) as caught:r.Log2MatrixRunner(conf).run()
   native.assert_not_called()
  facts=caught.exception.facts.snapshot();self.assertTrue(facts['cleanup_known']);self.assertEqual(facts['launch_state'],'not_started');self.assertEqual(facts['attempt_id'],ATTEMPT)
  facts['cleanup_known']=False;self.assertTrue(caught.exception.facts.snapshot()['cleanup_known'])
 def test_known_actual_fake_rc_failure_and_receipt_consumer(self):
  for kwargs in [{'rc':2},{'stdout':b'WRONG\n'}]:
   with oldrunner.assembled(**kwargs) as (runner,b,process,selector,watch,*_):
    runner._config=replace(runner._config,job_id=JOB,attempt_id=ATTEMPT)
    with self.assertRaises(r.Log2RunFailure) as caught:runner.run()
    facts=caught.exception.facts.snapshot();self.assertTrue(facts['cleanup_known']);self.assertEqual(facts['process_identity'],{'pid':10,'starttick':123,'proc_inode':456,'uid':1000})
    capsule=p.ClaimedJob('{}',p.canonical(RUN));proof=e.failure_receipt(capsule,caught.exception,runner._config);self.assertIsNotNone(proof)
    self.assertIsNone(e.failure_receipt(capsule,caught.exception,replace(runner._config,attempt_id=OWNER)))
 def test_popen_throw_pidfd_bind_close_timer_and_observer_unknown_are_not_failed_proofs(self):
  for mode in ['popen','pidfd','bind','close','timer','observe']:
   with self.subTest(mode=mode),oldrunner.assembled() as (runner,b,process,selector,watch,*_):
    runner._config=replace(runner._config,job_id=JOB,attempt_id=ATTEMPT)
    if mode=='popen':b.subprocess.Popen.side_effect=OSError('PRIVATE')
    if mode=='pidfd':b.os.pidfd_open.side_effect=OSError('PRIVATE')
    if mode=='bind':watch.bind_top=Mock(side_effect=OSError('PRIVATE'))
    if mode=='close':process.stdout.failure=True
    if mode=='observe':watch.uncertainty=['unknown']
    if mode=='timer':
     def timer(k,v):
      if v==0:raise BaseException('PRIVATE')
     b.signal.setitimer.side_effect=timer
    with self.assertRaises(r.Log2RunFailure) as caught:runner.run()
    self.assertFalse(caught.exception.facts.snapshot()['cleanup_known']);self.assertTrue(runner.quarantined)
    self.assertIsNone(e.failure_receipt(p.ClaimedJob('{}',p.canonical(RUN)),caught.exception,runner._config));self.assertEqual(b.subprocess.Popen.call_count,1)
 def _parent_mask_restore_case(self,*,before_popen):
  with oldrunner.assembled() as (runner,b,process,selector,watch,*_):
   runner._config=replace(runner._config,job_id=JOB,attempt_id=ATTEMPT)
   blocked=False
   def restore(how,value):
    nonlocal blocked
    if how==b.signal.SIG_BLOCK:blocked=True;return set()
    if how==b.signal.SIG_SETMASK:raise BaseException('SYNTHETIC_PRIVATE_PARENT_MASK_RESTORE')
    raise AssertionError('unexpected mask operation')
   b.signal.pthread_sigmask.side_effect=restore
   with self.assertRaises(r.Log2RunFailure) as caught:runner.run(cancelled=lambda:before_popen and blocked)
   error=caught.exception
   capsule=p.ClaimedJob('{}',p.canonical(RUN))
   facts=error.facts.snapshot();accepted=e.failure_receipt(capsule,error,runner._config) is not None
   counts={'popen':b.subprocess.Popen.call_count,'stop':watch.stops,'watch_close':watch.closes,
    'stdout_close':process.stdout.closes,'stderr_close':process.stderr.closes,'pidfd_close':b.os.close.call_count,
    'selector_created':b.selectors.DefaultSelector.call_count,'selector_close':selector.closes,
    'parent_restore':sum(call.args[0]==b.signal.SIG_SETMASK for call in b.signal.pthread_sigmask.call_args_list)}
   with self.assertRaises(r.Log2RunFailure) as repeated:runner.run()
   return {'facts':facts,'receipt_accepted':accepted,'counts':counts,'error':str(error),
    'quarantined':runner.quarantined,'retry_error':repeated.exception.code,'popen_after_retry':b.subprocess.Popen.call_count}
 def test_parent_mask_restore_BaseException_after_owned_child_stays_unknown(self):
  case=self._parent_mask_restore_case(before_popen=False)
  self.assertIs(case['facts']['cleanup_known'],False)
  self.assertIs(case['receipt_accepted'],False)
  self.assertEqual(case['counts'],{'popen':1,'stop':1,'watch_close':1,'stdout_close':1,'stderr_close':1,
   'pidfd_close':1,'selector_created':0,'selector_close':0,'parent_restore':1})
  self.assertNotIn('SYNTHETIC_PRIVATE',case['error'])
  self.assertTrue(case['quarantined'])
  self.assertEqual((case['retry_error'],case['popen_after_retry']),('log2_runner_quarantined',1))
 def test_parent_mask_restore_BaseException_before_Popen_stays_unknown(self):
  case=self._parent_mask_restore_case(before_popen=True)
  self.assertIs(case['facts']['cleanup_known'],False)
  self.assertIs(case['receipt_accepted'],False)
  self.assertEqual(case['counts'],{'popen':0,'stop':0,'watch_close':0,'stdout_close':0,'stderr_close':0,
   'pidfd_close':0,'selector_created':0,'selector_close':0,'parent_restore':1})
  self.assertNotIn('SYNTHETIC_PRIVATE',case['error'])
  self.assertTrue(case['quarantined'])
  self.assertEqual((case['retry_error'],case['popen_after_retry']),('log2_runner_quarantined',0))

# The real reviewed Transactions close/disposition code consumes fake SQL connections.
class DB(oldrepo.DB):pass
class Connection(oldrepo.Connection):
 async def execute(self,sql,params=()):
  if 'FROM cyomics_app.preprocessing_job_runs' in sql:return oldrepo.Cursor(None)
  return await super().execute(sql,params)
class Pool(oldrepo.Pool):
 async def getconn(self):
  self.acquires+=1;conn=Connection(self.db);self.db.connections.append(conn);return conn
class RepositoryTests(unittest.IsolatedAsyncioTestCase):
 async def test_enqueue_replay_closed_commit_and_owner_metadata_read_without_client_refs(self):
  db=DB();pool=Pool(db);repo=p.PreprocessRepository(pool,oldrepo.EXECUTION_BYTES,enabled=True)
  raw=oldrepo.body();first=await repo.enqueue(oldrepo.OWNER,oldrepo.PROJECT,oldrepo.DATASET,raw,Budget())
  self.assertTrue(first.commit_confirmed and first.teardown_confirmed);self.assertEqual(len(first.job_public()),20)
  replay=await repo.enqueue(oldrepo.OWNER,oldrepo.PROJECT,oldrepo.DATASET,raw,Budget());self.assertFalse(replay.created)
  value=await repo.read_request(oldrepo.OWNER,oldrepo.PROJECT,oldrepo.DATASET,json.loads(raw)['request_id'],Budget())
  self.assertEqual(value,first.job_public());self.assertTrue(all(x.closed for x in db.connections));self.assertEqual(pool.releases,3)
 async def test_writer_commit_close_unknown_sticky_but_fresh_owner_read_independent(self):
  db=DB();pool=Pool(db);repo=p.PreprocessRepository(pool,oldrepo.EXECUTION_BYTES,enabled=True);raw=oldrepo.body()
  db.close_mode='finish_after_closed'
  with self.assertRaises(Fault):await repo.enqueue(oldrepo.OWNER,oldrepo.PROJECT,oldrepo.DATASET,raw,Budget())
  self.assertTrue(repo.quarantined);self.assertEqual(len(db.rows),1);db.close_mode=None
  recovered=await repo.read_request(oldrepo.OWNER,oldrepo.PROJECT,oldrepo.DATASET,json.loads(raw)['request_id'],Budget())
  self.assertEqual(recovered['status'],'queued');self.assertTrue(repo.quarantined)
  before=pool.acquires
  with self.assertRaises(Fault):await repo.enqueue(oldrepo.OWNER,oldrepo.PROJECT,oldrepo.DATASET,raw,Budget())
  self.assertEqual(before,pool.acquires)
 async def test_foreign_owner_has_no_parent_or_job_read_and_artifact_name_whitelist(self):
  db=DB();repo=p.PreprocessRepository(Pool(db),oldrepo.EXECUTION_BYTES,enabled=True)
  with self.assertRaises(Fault):await repo.read_job(oldrepo.OTHER,oldrepo.PROJECT,JOB,Budget())
  self.assertFalse(any('preprocessing_jobs' in sql or 'dataset_versions' in sql for sql,_ in db.sql))
  before=len(db.sql)
  with self.assertRaises(Fault):await repo.read_artifact(oldrepo.OWNER,oldrepo.PROJECT,VER['processed_dataset_version_id'],'../secret',Budget())
  self.assertEqual(before,len(db.sql))

class ControllerTests(unittest.IsolatedAsyncioTestCase):
 def worker(self,statuses):
  class Worker:
   calls=0;acks=[]
   async def run_once(inner,**_):
    inner.calls+=1;status=statuses.pop(0);job='77777777-7777-4777-8777-'+format(inner.calls,'012x');attempt='88888888-8888-4888-8888-'+format(inner.calls,'012x');return e.Outcome(status,'synthetic',job,attempt)
   async def acknowledge_confirmed(inner,j,a):inner.acks.append((j,a));return e.Outcome('acknowledged','exact',j,a)
  return Worker()
 async def test_two_different_jobs_continue_ack_and_total_budget_across_calls(self):
  w=self.worker(['confirmed','confirmed']);ctl=e.PreprocessController(w,max_jobs=2,allow_confirmed_ack=True)
  out=await ctl.drain();self.assertEqual(len(out),2);self.assertEqual(w.calls,2);self.assertEqual(len(w.acks),2);self.assertEqual(len({x[0] for x in w.acks}),2);self.assertEqual(await ctl.drain(),[])
 async def test_unknown_stops_without_ack_claim_retry_or_automatic_reset(self):
  w=self.worker(['reconciliation_pending','confirmed']);ctl=e.PreprocessController(w,max_jobs=2,allow_confirmed_ack=True)
  self.assertEqual(len(await ctl.drain()),1);self.assertTrue(ctl.quarantined);self.assertEqual(w.calls,1);self.assertFalse(w.acks)
  with self.assertRaises(Fault):await ctl.drain()
 async def test_no_ack_authority_preserves_confirmed_checkpoint(self):
  w=self.worker(['confirmed','confirmed']);ctl=e.PreprocessController(w,max_jobs=2)
  self.assertEqual(len(await ctl.drain()),1);self.assertFalse(w.acks)
 async def test_off_worker_and_empty_scope_no_resources(self):
  self.assertEqual((await e.PreprocessWorker().run_once()).status,'off');self.assertEqual(await e.PreprocessController().drain(),[])

class HTTPTests(unittest.IsolatedAsyncioTestCase):
 def app(self,repo=None):
  auth=oldhttp.Auth();pool=oldhttp.NoPool();app=oldhttp.app_source.create_app(auth_transport=auth.transport(),pool=pool,anon_key_loader=lambda:oldhttp.token({'role':'anon'}),preprocessing_repository=repo)
  self.addAsyncCleanup(app.state.authority.close);return app,auth,pool
 def scope(self,auth,kind,subject=oldhttp.OWNER):
  s=oldhttp.scope(auth,subject=subject);path='/api/v1/datasets/'+DATASET+'/preprocessing-jobs' if kind=='list' else '/api/v1/processed-dataset-versions/'+VER['processed_dataset_version_id']+('/artifacts/working-matrix.tsv' if kind=='artifact' else '')
  s.update(path=path,raw_path=path.encode(),query_string=('project_id='+PROJECT).encode());return s
 def repo(self):
  class Repo:
   async def list_jobs(inner,subject,project,dataset,budget,**kwargs):
    if subject!=OWNER:raise Fault(404,'dataset_not_found')
    return fixture('positive/list-page.json')
   async def read_version(inner,subject,project,version,budget):
    if subject!=OWNER:raise Fault(404,'processed_dataset_version_not_found')
    return copy.deepcopy(VER)
   async def read_artifact(inner,subject,project,version,name,budget):
    if subject!=OWNER:raise Fault(404,'processed_dataset_version_not_found')
    return p.StoredArtifact(name,'text/tab-separated-values; charset=utf-8',b'x',c.bytes_sha(b'x'))
  return Repo()
 async def test_new_routes_defaultoff_same_real_authority_and_foreign_denial(self):
  app,auth,pool=self.app()
  for kind in ['list','version','artifact']:
   out=await oldhttp.invoke(app,self.scope(auth,kind,subject=None));self.assertEqual(oldhttp.status(out),401)
   out=await oldhttp.invoke(app,self.scope(auth,kind));self.assertEqual(oldhttp.status(out),503)
  self.assertEqual(len(auth.calls),3);self.assertEqual(pool.acquires,0)
  app,auth,_=self.app(self.repo())
  for kind in ['list','version','artifact']:self.assertEqual(oldhttp.status(await oldhttp.invoke(app,self.scope(auth,kind,subject=oldhttp.OTHER))),404)
 async def test_list_version_and_binary_fixed_headers_exact_bytes(self):
  app,auth,_=self.app(self.repo())
  for kind in ['list','version','artifact']:
   out=await oldhttp.invoke(app,self.scope(auth,kind));self.assertEqual(oldhttp.status(out),200)
   h=dict(out[0]['headers']);self.assertEqual(h[b'cache-control'],b'no-store')
   if kind=='artifact':
    self.assertEqual(out[-1]['body'],b'x');self.assertEqual(h[b'content-type'],b'application/octet-stream');self.assertEqual(h[b'content-disposition'],b'attachment; filename="working-matrix.tsv"');self.assertEqual(h[b'x-cyomics-file-sha256'],c.bytes_sha(b'x').encode());self.assertEqual(h[b'content-length'],b'1')
 async def test_artifact_tamper_sanitized_no_private_body(self):
  repo=self.repo()
  async def tampered(*args):return p.StoredArtifact('working-matrix.tsv','text/tab-separated-values; charset=utf-8',b'PRIVATE','a'*64)
  repo.read_artifact=tampered;app,auth,_=self.app(repo);out=await oldhttp.invoke(app,self.scope(auth,'artifact'))
  self.assertEqual(oldhttp.status(out),503);self.assertNotIn(b'PRIVATE',out[-1]['body'])
 async def test_disconnect_cancels_read_and_no_late_success(self):
  repo=self.repo();started=asyncio.Event();cancelled=asyncio.Event()
  async def blocked(*args):
   started.set()
   try:await asyncio.Future()
   finally:cancelled.set()
  repo.read_artifact=blocked;app,auth,_=self.app(repo);n=0
  async def receive():
   nonlocal n;n+=1
   if n==1:return {'type':'http.request','body':b'','more_body':False}
   await started.wait();return {'type':'http.disconnect'}
  out=await oldhttp.invoke(app,self.scope(auth,'artifact'),receive=receive);self.assertEqual(out,[]);await asyncio.wait_for(cancelled.wait(),1)


class WorkerFlowTests(unittest.IsolatedAsyncioTestCase):
 def setup_state(self,root):
  from dataclasses import asdict
  from cyomics_projects.import_report import SourceRef
  from cyomics_projects.protein_groups_parser import parse_protein_groups
  from cyomics_projects.raw_qc_checkpoint import WorkerRoots
  raw=oldrunner.RAW;parsed=parse_protein_groups(SourceRef(PROJECT,Q['source']['file_id'],'synthetic',len(raw),c.bytes_sha(raw)),[raw]);self.assertTrue(parsed.ok)
  selected=[asdict(col) for col in parsed.parsed.columns if col.quant_family=='LFQ intensity']
  proposal=e.proposal_bytes({'bytes':len(Path(r.__file__).read_bytes()),'sha256':c.bytes_sha(Path(r.__file__).read_bytes())}).decode()
  source={**Q['source'],'source_bytes':len(raw),'source_sha256':c.bytes_sha(raw),'row_count':len(parsed.parsed.rows),'column_count':len(parsed.parsed.columns),'selected_columns_sha256':c.digest(selected)}
  request=c.request_input(c.canonical({'request_id':Q['request_id'],'quant_family':'LFQ intensity','parameters':json.loads(r.validator.parameters((True,True,True)))}))
  rh=p.scoped_request_hash(PROJECT,DATASET,request)
  task={**Q,'source':source,'parameters':request.parameters_value(),'quant_family':'LFQ intensity','parameters_sha256':request.parameters_sha256,'request_sha256':rh,'binding_sha256':p.binding_hash(rh,source,request,json.loads(proposal)),'proposal_sha256':p.digest(proposal)}
  paths=[]
  for name in ['control','inputs','stage','scratch','receipts']:
   path=root/name;path.mkdir(mode=0o700);paths.append(path)
  roots=WorkerRoots(*paths)
  class Repo:
   claims=0;publishes=0;finishes=[];unknown_after_publish=False;failures=False;current=None;version=None;bundle=None;receipt=None
   def capsule(inner):return p.ClaimedJob(p.canonical({'admission':{'execution_json':proposal,'owner_id':OWNER},'registration':{'startup_scope_sha256':'a'*64}}),p.canonical(inner.current))
   async def claim(inner,reg,scope,attempt,budget,*,job_id=None):
    inner.claims+=1
    if inner.current is not None:return None
    inner.current={**copy.deepcopy(task),'status':'running','revision':2,'attempt_id':attempt,'started_at':'2026-11-18T02:00:00.000000Z','runtime':{**RUN['runtime'],'receipt_sha256':None}}
    rt=inner.current['runtime'];rt['runtime_binding_sha256']=c.runtime_binding(JOB,task['binding_sha256'],task['proposal_sha256'],REG,rt['profile_sha256'],attempt)
    return inner.capsule()
   async def load_bound_source(inner,*_):return p.BoundInput(inner.capsule(),'{}','{}',raw)
   async def read_attempt(inner,*_):return inner.capsule()
   async def publish(inner,capsule,metadata,bundle,receipt,budget):
    inner.publishes+=1;e.validate_receipt(capsule,bundle,receipt);e.validate_version_for_claim(capsule,metadata,bundle,receipt)
    inner.version=metadata;inner.bundle=bundle;inner.receipt=receipt
    inner.current={**inner.current,'status':'succeeded','revision':3,'finished_at':metadata['created_at'],'runtime':metadata['runtime'],'result':{'processed_dataset_version_id':metadata['processed_dataset_version_id'],'manifest_sha256':bundle.manifest_sha256,'artifact_count':11,'analysis_ready':False}}
    if inner.unknown_after_publish:raise Fault(504,'preprocessing_submission_unknown')
    return inner.capsule()
   async def finish(inner,capsule,target,code,budget,*,failure_receipt=None):
    inner.finishes.append(target)
    if target=='reconciliation_pending':
     inner.current={**inner.current,'revision':3,'observation':'reconciliation_pending'}
    else:
     inner.current={**inner.current,'status':'failed','revision':3,'finished_at':'2026-11-18T03:00:00.000000Z','error':{'code':code},'runtime':{**inner.current['runtime'],'receipt_sha256':c.digest(failure_receipt)}}
    return inner.capsule()
  repo=Repo()
  class Executor:
   calls=0;mode='success'
   def __init__(inner,config):inner.config=config;inner.quarantined=False
   def run(inner):
    Executor.calls+=1;conf=inner.config
    if Executor.mode!='success':
     if Executor.mode=='unknown':raise r.Log2RunFailure('log2_cleanup_unconfirmed')
     facts={'schemaVersion':1,'job_id':conf.job_id,'attempt_id':conf.attempt_id,'configuration_sha256':r.configuration_binding(conf),'popen_attempted':False,'launch_state':'not_started','process_identity':None,'exit_code':None,'child_reaped':True,'observed_tasks_stopped':True,'owned_fds_closed':True,'timer_disabled':True,'signal_handler_restored':True,'process_evidence_sha256':None,'cleanup_known':True}
     raise r.Log2RunFailure('log2_file_pin_mismatch',r.FailureFacts(c.canonical(facts)))
    # Pure byte validator over historical synthetic expected-output fixtures.
    # This receipt is a fake executor witness for state-machine tests ONLY.
    bundle=r.validator.validate(raw,conf.selected.read_bytes(),conf.family,conf.flags,oldrunner.OUTPUTS['lfq'],check=lambda:None)
    proposal_value=json.loads(proposal)
    receipt={'job_id':conf.job_id,'attempt_id':conf.attempt_id,'family':conf.family,'flags':list(conf.flags),'configuration_sha256':r.configuration_binding(conf),'source':{'bytes':conf.source_pin.bytes,'sha256':conf.source_pin.sha256},'selected':{'bytes':conf.selected_pin.bytes,'sha256':conf.selected_pin.sha256},'parameters':{'bytes':conf.parameters_pin.bytes,'sha256':conf.parameters_pin.sha256},'script':proposal_value['script'],'validator':proposal_value['validator'],'native':proposal_value['executable'],'process_policy_sha256':p.POLICY_SHA256,'limits':p.LIMITS,'exit_code':0,'stderr_bytes':0,'one_native_child':True,'all_11_outputs_verified':True,'close_confirmed':True,'manifest_sha256':bundle.manifest_sha256,'published':False,'Owner_or_API_or_DB_authorized':False,'process_control':{'owned_tasks':{'cleanup':{'complete_for_observed_identities':True},'uncertainty':[]}}}
    return r.RunResult(bundle,c.canonical(receipt))
  verify=Mock()
  worker=e.PreprocessWorker(repo,roots,verify=verify,executor_factory=Executor,registration_id=REG,scope_sha256='a'*64,clock=lambda:'2026-11-18T03:00:00.000000Z')
  return worker,repo,Executor,roots
 def private_root(self):
  import tempfile
  tmp=tempfile.TemporaryDirectory(prefix='preprocess-offline-');self.addCleanup(tmp.cleanup);return Path(tmp.name)
 async def test_real_journal_prepare_validator_atomic_publish_then_exact_ack(self):
  from cyomics_projects.raw_qc_checkpoint import Journal
  worker,repo,executor,roots=self.setup_state(self.private_root());out=await worker.run_once(expected_job_id=JOB)
  self.assertEqual((out.status,out.reason),('confirmed','succeeded'));self.assertEqual((executor.calls,repo.publishes,repo.claims),(1,1,1));self.assertEqual(len(repo.bundle.artifacts),11)
  self.assertEqual(repo.current['status'],'succeeded');self.assertFalse(repo.version['analysis_ready']);self.assertEqual(repo.finishes,[])
  with Journal(roots).locked(require_existing=True) as j:self.assertEqual(j.load()['phase'],'confirmed')
  ack=await worker.acknowledge_confirmed(out.job_id,out.attempt_id);self.assertEqual(ack.status,'acknowledged')
  with Journal(roots).locked(require_existing=True) as j:self.assertEqual(j.load(),{'phase':'idle'})
  self.assertEqual(executor.calls,1)
 async def test_committed_success_private_unknown_never_downgraded_or_retried(self):
  from cyomics_projects.raw_qc_checkpoint import Journal
  worker,repo,executor,roots=self.setup_state(self.private_root());repo.unknown_after_publish=True
  out=await worker.run_once(expected_job_id=JOB);self.assertEqual(out.status,'reconciliation_pending');self.assertTrue(worker.quarantined)
  self.assertEqual(repo.current['status'],'succeeded');self.assertEqual(repo.finishes,[]);self.assertEqual((executor.calls,repo.publishes),(1,1))
  with Journal(roots).locked(require_existing=True) as j:self.assertEqual(j.load()['phase'],'reconciliation')
  self.assertEqual((await worker.run_once()).status,'reconciliation_pending');self.assertEqual((await worker.acknowledge_confirmed(JOB,out.attempt_id)).status,'reconciliation_pending')
  observed=await worker.recover_read_only(out.attempt_id,JOB);self.assertEqual(observed.task()['status'],'succeeded');self.assertTrue(worker.quarantined);self.assertEqual(repo.claims,1)
 async def test_known_nochild_failure_committed_only_with_bound_facts(self):
  worker,repo,executor,roots=self.setup_state(self.private_root());executor.mode='known'
  out=await worker.run_once(expected_job_id=JOB);self.assertEqual((out.status,out.reason),('confirmed','failed'));self.assertEqual(repo.finishes,['failed']);self.assertEqual(repo.publishes,0);self.assertEqual(executor.calls,1)
  self.assertEqual((await worker.acknowledge_confirmed(JOB,out.attempt_id)).status,'acknowledged')
 async def test_unknown_runner_failure_stops_without_failure_publication(self):
  worker,repo,executor,roots=self.setup_state(self.private_root());executor.mode='unknown'
  out=await worker.run_once(expected_job_id=JOB);self.assertEqual(out.status,'reconciliation_pending');self.assertTrue(worker.quarantined);self.assertEqual(repo.finishes,['reconciliation_pending']);self.assertEqual(repo.publishes,0)
  self.assertEqual((await worker.run_once()).status,'reconciliation_pending');self.assertEqual(executor.calls,1)
 async def test_old_unknown_checkpoint_is_preserved_without_claim_or_R(self):
  from cyomics_projects.raw_qc_checkpoint import Journal
  worker,repo,executor,roots=self.setup_state(self.private_root())
  with Journal(roots).locked() as j:j.load();j.save({'phase':'reconciliation','job_id':JOB,'attempt_id':ATTEMPT})
  out=await worker.run_once();self.assertEqual(out.status,'reconciliation_pending');self.assertEqual((repo.claims,executor.calls),(0,0))
 async def test_wrong_ack_does_not_reset_or_start_next_job(self):
  worker,repo,executor,roots=self.setup_state(self.private_root());out=await worker.run_once(expected_job_id=JOB)
  self.assertEqual(out.status,'confirmed');self.assertEqual((await worker.acknowledge_confirmed(OWNER,out.attempt_id)).status,'reconciliation_pending');self.assertTrue(worker.quarantined);self.assertEqual(executor.calls,1)


class FixedRPCTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.db=DB();self.pool=Pool(self.db);owner=p.PreprocessRepository(self.pool,oldrepo.EXECUTION_BYTES,enabled=True)
  queued=(await owner.enqueue(oldrepo.OWNER,oldrepo.PROJECT,oldrepo.DATASET,oldrepo.body(),Budget())).job_public()
  row=next(iter(self.db.rows.values()));self.admission=dict(zip(p.COLUMNS.split(','),row));self.admission={k:(str(v) if isinstance(v,__import__('uuid').UUID) else p.utc(v) if isinstance(v,__import__('datetime').datetime) else v) for k,v in self.admission.items()}
  rt={**RUN['runtime'],'receipt_sha256':None};rt['runtime_binding_sha256']=c.runtime_binding(queued['job_id'],queued['binding_sha256'],queued['proposal_sha256'],REG,rt['profile_sha256'],ATTEMPT)
  self.record={'admission':self.admission,'run':{'job_id':queued['job_id'],'attempt_id':ATTEMPT,'registration_id':REG,'runtime_binding_sha256':rt['runtime_binding_sha256'],'status':'running','revision':2,'observation':'confirmed','started_at':'2026-11-18T02:00:00.000000Z','finished_at':None,'error_code':None,'receipt_sha256':None,'result_version_id':None,'manifest_sha256':None},'registration':{'registration_id':REG,'project_id':oldrepo.PROJECT,'proposal_sha256':queued['proposal_sha256'],'profile_json':'{}','profile_sha256':p.digest('{}'),'attestation_json':'{}','attestation_sha256':p.digest('{}'),'startup_scope_sha256':'a'*64}}
  self.record['run']['runtime_binding_sha256']=c.runtime_binding(queued['job_id'],queued['binding_sha256'],queued['proposal_sha256'],REG,p.digest('{}'),ATTEMPT)
  self.record=p.document(p.canonical(self.record),32768)
  self.reply=copy.deepcopy(self.record)
  outer=self
  class RPCConnection(Connection):
   async def execute(inner,sql,params=()):
    if sql.startswith('SELECT cyomics_app.preprocessing_'):
     inner.db.sql.append((sql,params));return oldrepo.Cursor(copy.deepcopy((outer.reply,)))
    if sql.startswith('SELECT record,dataset_json'):
     inner.db.sql.append((sql,params));f=oldrepo.f;dataset=dict(zip(p.DATASET_COLUMNS.split(','),f.DATASET_ROW));dataset={k:(str(v) if isinstance(v,__import__('uuid').UUID) else p.utc(v) if isinstance(v,__import__('datetime').datetime) else v) for k,v in dataset.items()}
     raw=dict(zip(('file_id','project_id','format','bytes','sha256','status'),f.RAW_META));raw={k:str(v) if isinstance(v,__import__('uuid').UUID) else v for k,v in raw.items()}
     return oldrepo.Cursor((copy.deepcopy(outer.record),dataset,raw,f.RAW))
    return await super().execute(sql,params)
  class RPCPool(Pool):
   async def getconn(inner):
    inner.acquires+=1;conn=RPCConnection(inner.db);inner.db.connections.append(conn);return conn
  self.rpcpool=RPCPool(self.db);self.repo=p.PreprocessWorkerRepository(self.rpcpool,enabled=True)
 async def test_claim_and_bound_source_use_only_fixed_RPC_with_full_detached_capsule(self):
  job=self.admission['job_id'];capsule=await self.repo.claim(REG,'a'*64,ATTEMPT,Budget(),job_id=job)
  self.assertEqual(capsule.task()['status'],'running');capsule.task()['runtime']['profile_sha256']='BAD';self.assertEqual(capsule.task()['runtime']['profile_sha256'],p.digest('{}'))
  bound=await self.repo.load_bound_source(job,ATTEMPT,Budget());self.assertEqual(bound.content,oldrepo.f.RAW)
  self.assertEqual(bound.capsule.task(),capsule.task());self.assertTrue(all(conn.closed for conn in self.db.connections))
  calls=[(s,a) for s,a in self.db.sql if 'preprocessing_claim(' in s];self.assertEqual(len(calls),1);self.assertEqual(tuple(map(str,calls[0][1])),(job,ATTEMPT,REG,'a'*64))
 async def test_capsule_tampering_refused_and_transaction_closed(self):
  self.reply['admission']['binding_sha256']='0'*64
  with self.assertRaises(Fault):await self.repo.read_attempt(self.admission['job_id'],ATTEMPT,Budget())
  self.assertTrue(all(conn.closed for conn in self.db.connections))
 async def test_claim_commit_or_teardown_unknown_sticky_no_second_RPC(self):
  self.db.close_mode='finish_after_closed'
  with self.assertRaises(Fault):await self.repo.claim(REG,'a'*64,ATTEMPT,Budget(),job_id=self.admission['job_id'])
  self.assertTrue(self.repo.quarantined);before=len(self.db.sql)
  with self.assertRaises(Fault):await self.repo.claim(REG,'a'*64,ATTEMPT,Budget(),job_id=self.admission['job_id'])
  self.assertEqual(before,len(self.db.sql))
 async def test_finish_requires_proof_and_reconciliation_never_terminal(self):
  cap=p.decode_capsule(self.record);before=len(self.db.sql)
  with self.assertRaises(Fault):await self.repo.finish(cap,'failed','runner_failed',Budget())
  self.assertEqual(before,len(self.db.sql));self.reply['run'].update(revision=3,observation='reconciliation_pending')
  out=await self.repo.finish(cap,'reconciliation_pending',None,Budget());self.assertEqual(out.task()['status'],'running');self.assertEqual(out.task()['observation'],'reconciliation_pending')

class OwnerBundleTests(unittest.IsolatedAsyncioTestCase):
 async def test_actual_owner_read_checks_all11_DB_hashes_and_download_bytes_before_return(self):
  proposal=oldrepo.EXECUTION_BYTES.decode();task=copy.deepcopy(OK);metadata=copy.deepcopy(VER)
  task['proposal_sha256']=metadata['proposal_sha256']=p.digest(proposal)
  task['runtime']['runtime_binding_sha256']=c.runtime_binding(task['job_id'],task['binding_sha256'],task['proposal_sha256'],REG,task['runtime']['profile_sha256'],task['attempt_id']);metadata['runtime']=copy.deepcopy(task['runtime']);metadata['tool']=p.public_tool(json.loads(proposal))
  repo=p.PreprocessRepository();calls=[];bad=False
  async def query(sql,args=()):
   calls.append(sql)
   if 'FROM cyomics_app.processed_dataset_versions' in sql:return (p.canonical(metadata),p.digest(p.canonical(metadata)),metadata['manifest_sha256'],__import__('uuid').UUID(JOB),__import__('uuid').UUID(PROJECT),__import__('uuid').UUID(DATASET),__import__('uuid').UUID(metadata['file_id']),__import__('uuid').UUID(OWNER),metadata['created_at'])
   pins=[]
   for item in metadata['artifacts']:pins.extend((item['bytes'],'0'*64 if bad else item['sha256']))
   return (p.canonical(metadata['artifacts']),metadata['manifest_sha256'],*pins)
  value=await repo._version_for_task(query,task,proposal,verify_output=True);self.assertEqual(value,metadata);self.assertEqual(len(calls),2);self.assertEqual(calls[-1].count('octet_length('),11);self.assertEqual(calls[-1].count('sha256('),11)
  bad=True
  with self.assertRaises(Fault) as caught:await repo._version_for_task(query,task,proposal,verify_output=True)
  self.assertEqual(caught.exception.code,'preprocessing_artifact_integrity_failed')


class OwnerListTests(unittest.IsolatedAsyncioTestCase):
 async def test_real_repository_second_page_roundtrip_metadata_only_no_local_reference(self):
  db=DB()
  class ListConnection(Connection):
   async def execute(inner,sql,params=()):
    if sql.startswith('SELECT coalesce(jsonb_agg'):
     inner.db.sql.append((sql,params));rows=sorted(db.rows.values(),key=lambda row:(row[13],row[0]),reverse=True)
     if 'AND (created_at,job_id)<' in sql:
      stamp=c.stamp(params[2]);rows=[row for row in rows if (row[13],row[0])<(stamp,params[3])]
     docs=[]
     for row in rows[:params[-1]]:
      docs.append({k:str(v) if isinstance(v,__import__('uuid').UUID) else p.utc(v) if isinstance(v,__import__('datetime').datetime) else v for k,v in zip(p.COLUMNS.split(','),row)})
     return oldrepo.Cursor((docs,))
    return await super().execute(sql,params)
  class ListPool(Pool):
   async def getconn(inner):
    inner.acquires+=1;conn=ListConnection(db);db.connections.append(conn);return conn
  repo=p.PreprocessRepository(ListPool(db),oldrepo.EXECUTION_BYTES,enabled=True)
  created=[]
  for _ in range(3):created.append((await repo.enqueue(oldrepo.OWNER,oldrepo.PROJECT,oldrepo.DATASET,oldrepo.body(),Budget())).job_public())
  first=await repo.list_jobs(oldrepo.OWNER,oldrepo.PROJECT,oldrepo.DATASET,Budget(),limit=2)
  second=await repo.list_jobs(oldrepo.OWNER,oldrepo.PROJECT,oldrepo.DATASET,Budget(),limit=2,cursor=first['next_cursor'])
  self.assertEqual([len(first['items']),len(second['items'])],[2,1]);self.assertIsNone(second['next_cursor']);self.assertEqual(len({x['job_id'] for x in first['items']+second['items']}),3)
  self.assertEqual([x['job_id'] for x in first['items']+second['items']],sorted((x['job_id'] for x in created),reverse=True))
  self.assertFalse(any('content FROM' in sql or 'artifact_bundles' in sql for sql,_ in db.sql));self.assertTrue(all(conn.closed for conn in db.connections))


class LegacyBoundaryTests(unittest.TestCase):
 def test_two_existing_rawqc_behavior_boundaries(self):
  import runpy
  with patch.object(runpy,'run_path',return_value=vars(oldrepo.f)):
   legacy=load('reference_rawqc_http_tests',BASE/'tests/test_raw_qc_http.py')
  case=next(value for value in vars(legacy).values() if isinstance(value,type) and issubclass(value,unittest.TestCase) and hasattr(value,'test_three_routes_verify_every_time_and_bind_subject'))
  suite=unittest.TestSuite([case('test_three_routes_verify_every_time_and_bind_subject'),case('test_dispatch_before_broad_samples_and_old_path_compatible')])
  result=unittest.TestResult();suite.run(result);self.assertEqual(result.testsRun,2);self.assertFalse(result.errors+result.failures,result.errors+result.failures)

if __name__=='__main__':unittest.main(verbosity=2)




