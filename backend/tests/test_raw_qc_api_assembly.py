"""Pure constructor/gate tests; fake components never Auth/DB/R/FS."""
import ast,contextlib,copy,importlib.util,json,pathlib,sys,types,unittest
from unittest.mock import patch
H=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('raw_qc_assembly_candidate',H/'cyomics_projects/raw_qc_api_assembly.py')
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
events=[]
class Pool:
 def __init__(self):events.append('pool')
class Registration:
 def __init__(self,s):self.s=s
 def snapshot(self):return json.loads(self.s)
class Repo:
 def __init__(self,pool,registration):self.pool=pool;self.registration=registration;events.append('qc_repository')
class Reader:
 def __init__(self,root):self.root=root;events.append('reader')
def components():return Pool,Repo,Reader,Registration
def config():return m.RawQCAPIConfig(m.EXECUTION_JSON,m.PUBLISH_ROOT,1000)
class Auth:
 def __init__(self,transport,key):events.append('authority');self.transport=transport;self.key=key
class ProjectRepo:
 def __init__(self,pool):self.pool=pool
class Basic:
 def __init__(self,*args,**kwargs):self.args=args;self.kwargs=kwargs
class Jobs:
 def __init__(self,authority,repository):self.authority=authority;self.repository=repository
class Artifacts(Jobs):
 def __init__(self,authority,repository,reader):super().__init__(authority,repository);self.reader=reader
class App:
 def __init__(self,**kwargs):self.state=types.SimpleNamespace();self.middleware=[];self.routes=[];self.kwargs=kwargs
 def exception_handler(self,*args):return lambda f:f
 def add_middleware(self,cls,**kw):self.middleware.append((cls,kw))
 def add_api_route(self,*args,**kw):self.routes.append((args,kw))
def factory(gate=lambda:None):
 node=next(n for n in ast.parse((H/'cyomics_projects/app.py').read_text(encoding='utf-8')).body if isinstance(n,ast.FunctionDef) and n.name=='create_app')
 scope={'prepare_raw_qc_launcher_config':m.prepare_raw_qc_launcher_config,'bind_raw_qc_submit':m.bind_raw_qc_submit,'close_raw_qc_live_resources':m.close_raw_qc_live_resources,'prepare_raw_qc_api_config':m.prepare_raw_qc_api_config,'bind_raw_qc_api':m.bind_raw_qc_api,'APIAssemblyRefused':m.APIAssemblyRefused,'production_gate':gate,'FixedPool':Pool,'load_approved_anon_key':lambda:None,'Authority':Auth,'Repository':ProjectRepo,'CompleteImports':Basic,'asynccontextmanager':contextlib.asynccontextmanager,'FastAPI':App,'SampleDesigns':Basic,'RawQCJobs':Jobs,'RawQCArtifactHTTP':Artifacts,'ProjectBoundary':Basic,'RawUploads':Basic,'entry_budget':lambda s:None,'HTTPException':type('HTTPException',(Exception,),{}),'RequestValidationError':type('RequestValidationError',(Exception,),{}),'Request':object}
 exec(compile(ast.Module(body=[node],type_ignores=[]),'<exact-candidate-create_app-AST>','exec'),scope)
 return scope['create_app']
class Tests(unittest.TestCase):
 def setUp(self):events.clear()
 def test_none_default_zero_component_load_and_IO(self):
  with patch.object(m,'_components',side_effect=AssertionError('must not import dependencies')):
   self.assertIsNone(m.prepare_raw_qc_api_config());self.assertEqual(m.bind_raw_qc_api(),(None,None))
  self.assertEqual(events,[])
 def test_exact_all_eight_execution_values_required(self):
  good=json.loads(m.EXECUTION_JSON)
  self.assertEqual(len(good),8)
  for key in ('runner','executable','output_validator'):
   bad=copy.deepcopy(good);bad[key]['sha256']='a'*64
   with patch.object(m,'_components',side_effect=AssertionError()):
    with self.assertRaises(m.APIAssemblyRefused):m.bind_raw_qc_api(m.RawQCAPIConfig(json.dumps(bad,sort_keys=True,separators=(',',':')),m.PUBLISH_ROOT,1000),object())
  with self.assertRaises(m.APIAssemblyRefused):m.prepare_raw_qc_api_config(m.RawQCAPIConfig(m.EXECUTION_JSON+' ',m.PUBLISH_ROOT,1000))
 def test_root_alias_oldproduction_uid_types_and_subclass_rejected(self):
  for root,uid in ((m.PUBLISH_ROOT+'/',1000),(m.PUBLISH_ROOT+'/../published',1000),('/home/user01/Cy_Omics/data',1000),(m.PUBLISH_ROOT,True),(m.PUBLISH_ROOT,0)):
   with self.assertRaises(m.APIAssemblyRefused):m.prepare_raw_qc_api_config(m.RawQCAPIConfig(m.EXECUTION_JSON,root,uid))
  with self.assertRaises(m.APIAssemblyRefused):m.prepare_raw_qc_api_config(types.SimpleNamespace(execution_json=m.EXECUTION_JSON,publish_root=m.PUBLISH_ROOT,expected_uid=1000))
 def test_pool_is_factory_pool_and_single_reader_same_registration(self):
  with patch.object(m,'_components',side_effect=components):
   pool=Pool();repo,reader=m.bind_raw_qc_api(config(),pool)
   self.assertIs(repo.pool,pool);self.assertEqual(repo.registration.canonical_json if hasattr(repo.registration,'canonical_json') else repo.registration.s,m.EXECUTION_JSON);self.assertEqual(str(reader.root),str(pathlib.Path(m.PUBLISH_ROOT)))
   self.assertEqual(events,['pool','qc_repository','reader'])
   with self.assertRaises(m.APIAssemblyRefused):m.bind_raw_qc_api(config(),object())
 def test_existing_production_gate_precedes_pool_binding_and_auth(self):
  class Refused(Exception):pass
  def gate():events.append('gate');raise Refused()
  with patch.object(m,'_components',side_effect=AssertionError()):
   with self.assertRaises(Refused):factory(gate)(raw_qc_api_config=config())
  self.assertEqual(events,['gate'])
 def test_app_same_final_pool_same_authority_before_mount(self):
  def gate():events.append('gate')
  with patch.object(m,'_components',side_effect=components):app=factory(gate)(raw_qc_api_config=config())
  repo=app.state.raw_qc_jobs.repository;art=app.state.raw_qc_artifacts
  self.assertIs(repo.pool,app.state.repository.pool);self.assertIs(art.repository,repo);self.assertIs(art.authority,app.state.authority);self.assertIs(app.state.raw_qc_jobs.authority,app.state.authority)
  self.assertIs(app.middleware[0][1]['raw_qc'],app.state.raw_qc_jobs);self.assertIs(app.middleware[0][1]['raw_qc_artifacts'],art)
  self.assertEqual(events,['gate','pool','qc_repository','reader','authority']);self.assertEqual(events.count('reader'),1)
 def test_invalid_or_mixed_DI_refused_pre_gate_pool_auth(self):
  for kwargs in ({'raw_qc_repository':object()},{'raw_qc_artifact_reader':object()}):
   with self.assertRaises(m.APIAssemblyRefused):factory(lambda:events.append('gate'))(raw_qc_api_config=config(),**kwargs)
  with self.assertRaises(m.APIAssemblyRefused):factory(lambda:events.append('gate'))(raw_qc_api_config=object())
  self.assertEqual(events,[])
 def test_registration_failure_is_fixed_error_not_private_exception(self):
  class Bad(Registration):
   def snapshot(self):raise RuntimeError('SYNTHETIC_PRIVATE_VALUE')
  with patch.object(m,'_components',return_value=(Pool,Repo,Reader,Bad)):
   with self.assertRaises(m.APIAssemblyRefused) as error:m.bind_raw_qc_api(config(),Pool())
  self.assertNotIn('PRIVATE',str(error.exception));self.assertNotIn('reader',events)
if __name__=='__main__':unittest.main(verbosity=2)
