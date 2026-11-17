"""Offline app-only mount tests. HTTP/PG dependency seams forbid real access."""
import asyncio,copy,hashlib,inspect,json,pathlib,sys,threading,unittest
from unittest.mock import patch
H=pathlib.Path(__file__).resolve().parent;B=H.parent
sys.path.insert(0,str(B/'tests'))
import test_raw_qc_artifact_http as f
from cyomics_projects import app as a
from cyomics_projects import raw_qc_artifact_http as api

class Authority(f.f.SyntheticAuthority):
 instances=[]
 def __init__(self,transport,loader):
  super().__init__();self.transport=transport;self.loader=loader;self.closed=0;self.instances.append(self)
 async def close(self):self.closed+=1
class Pool:
 async def getconn(self):raise AssertionError('NO DATABASE')
 def close(self):pass
def make_app(repo=None,reader=None):
 with patch.object(a,'Authority',Authority):
  return a.create_app(auth_transport=object(),pool=Pool(),anon_key_loader=lambda:None,raw_qc_repository=repo,raw_qc_artifact_reader=reader)
def full_scope(s):
 return dict(s,asgi={'version':'3.0','spec_version':'2.3'},http_version='1.1',scheme='http',server=('127.0.0.1',57430),client=('127.0.0.1',1))
async def invoke(app,s,raw=b'',receive=None):
 output=[]
 if receive is None:
  q=asyncio.Queue();q.put_nowait({'type':'http.request','body':raw,'more_body':False});receive=q.get
 async def send(message):output.append(message)
 await app(full_scope(s),receive,send)
 return output
def status(out):return out[0]['status']
def code(out):return json.loads(out[-1]['body'])['error']['code']

class Mount(unittest.IsolatedAsyncioTestCase):
 async def test_factory_same_authority_repo_reader_single_instance(self):
  repo=f.Repository();reader=f.Reader()
  with patch.object(a,'RawQCArtifactHTTP',wraps=api.RawQCArtifactHTTP) as construct:
   app=make_app(repo,reader)
   for name in ('raw-matrix.tsv','R-session-info.txt'):
    self.assertEqual(status(await invoke(app,f.scope(repo,name))),200)
   self.assertEqual(construct.call_count,1)
  handler=app.state.raw_qc_artifacts
  self.assertIs(type(handler),api.RawQCArtifactHTTP);self.assertIs(handler.authority,app.state.authority)
  self.assertIs(handler.authority,app.state.raw_qc_jobs.authority);self.assertIs(handler.repository,repo)
  self.assertIs(handler.repository,app.state.raw_qc_jobs.repository);self.assertIs(handler.reader,reader)
  self.assertEqual(app.state.authority.calls,2);self.assertEqual(len(repo.calls),2);self.assertIsNone(handler._lease)

 async def test_constructor_and_None_request_have_no_reader_thread_receive_or_repository_IO(self):
  repo=f.Repository()
  def forbidden(*args,**kw):raise AssertionError('NO RUNTIME IO')
  with patch('builtins.open',side_effect=forbidden),patch('os.open',side_effect=forbidden),patch.object(threading.Thread,'start',side_effect=forbidden):
   app=make_app(repo)
   async def receive():raise AssertionError('NO RECEIVE WHILE OFF')
   out=await invoke(app,f.scope(repo),receive=receive)
  self.assertEqual((status(out),code(out)),(503,'raw_qc_unavailable'))
  self.assertIsNone(app.state.raw_qc_artifacts.reader);self.assertEqual(app.state.authority.calls,0);self.assertEqual(repo.calls,[])
  self.assertIsNone(inspect.signature(a.create_app).parameters['raw_qc_artifact_reader'].default)

 async def test_default_production_gate_still_blocks_before_constructor(self):
  with patch.object(a,'RawQCArtifactHTTP',side_effect=AssertionError('constructor not reached')):
   with self.assertRaises(a.production_gate.__globals__['RuntimeNotApproved']):a.create_app()

 async def test_reader_without_raw_repository_does_not_fall_back_to_app_repository(self):
  reader=f.Reader();route_repo=f.Repository();app=make_app(reader=reader)
  out=await invoke(app,f.scope(route_repo))
  self.assertEqual(status(out),503);self.assertIsNone(app.state.raw_qc_artifacts.repository)
  self.assertIsNotNone(app.state.repository);self.assertEqual(reader.calls,[]);self.assertEqual(app.state.authority.calls,0)

 async def test_binary_larger_than_generic_1MiB_survives_full_app(self):
  body=b'Z'*(1048576+73)
  with patch.object(f,'BODY',body):
   repo=f.Repository();reader=f.Reader();app=make_app(repo,reader)
   with patch.object(a,'entry_budget',side_effect=AssertionError('generic buffer must be bypassed')):
    out=await invoke(app,f.scope(repo))
  self.assertEqual(status(out),200);self.assertEqual(out[-1]['body'],body)
  headers=dict(out[0]['headers']);self.assertEqual(headers[b'content-length'],str(len(body)).encode())
  self.assertEqual(headers[b'x-cyomics-file-sha256'],hashlib.sha256(body).hexdigest().encode());self.assertEqual(headers[b'cache-control'],b'no-store')

 async def test_old_jobs_three_routes_do_not_touch_artifact_reader(self):
  repo=f.f.SyntheticRepository();reader=f.Reader();app=make_app(repo,reader)
  for s,raw,expected in [(f.f.scope('POST'),f.f.body(),202),(f.f.scope(),b'',200),(f.f.scope(detail=True),b'',200)]:
   out=await invoke(app,s,raw);self.assertEqual(status(out),expected)
  self.assertEqual([x[0] for x in repo.calls],['enqueue','request','job']);self.assertEqual(reader.calls,[])
  self.assertEqual(app.state.authority.calls,3)

 async def test_artifact_near_routes_reach_strict_handler_before_auth(self):
  repo=f.Repository();reader=f.Reader();app=make_app(repo,reader)
  original=f.scope(repo)
  paths=[original['path']+'/',original['path'].rsplit('/',1)[0],original['path'].replace('/artifacts/','/artifacts//'),
    original['path'].replace('/artifacts/','/%61rtifacts/'),original['path'].replace('/raw-qc-jobs/','//raw-qc-jobs/'),
    original['path'].replace(repo.value['job_id'],'malformed-id'),original['path']+'/extra',original['path'].replace('raw-matrix.tsv','..')]
  for path in paths:
   with self.subTest(path=path):
    s=copy.deepcopy(original);s.update(path=path,raw_path=path.encode())
    out=await invoke(app,s);self.assertEqual(status(out),404)
  s=copy.deepcopy(original);s['method']='POST';self.assertEqual(status(await invoke(app,s)),405)
  s=copy.deepcopy(original);s['query_string']+=b'&owner_id=bad';self.assertEqual(status(await invoke(app,s)),400)
  self.assertEqual(app.state.authority.calls,0);self.assertEqual(repo.calls,[]);self.assertEqual(reader.calls,[])

 async def test_no_reader_artifact_errors_stay_off_without_falling_generic(self):
  repo=f.Repository();app=make_app(repo)
  with patch.object(a,'entry_budget',side_effect=AssertionError('must not fall generic')):
   for suffix in ('/artifacts/raw-matrix.tsv','/artifacts','/artifacts/invalid/extra'):
    path='/api/v1/raw-qc-jobs/bad-id'+suffix;s=f.scope(repo);s.update(path=path,raw_path=path.encode())
    self.assertEqual(status(await invoke(app,s)),503)
  self.assertEqual(app.state.authority.calls,0);self.assertEqual(repo.calls,[])

 async def test_concurrent_request_uses_one_admission_and_same_instance(self):
  repo=f.Repository();reader=f.Reader();reader.gate=threading.Event();app=make_app(repo,reader)
  handler=app.state.raw_qc_artifacts;first=asyncio.create_task(invoke(app,f.scope(repo)))
  try:
   await f.until(reader.entered.is_set);lease=handler._lease
   second=await invoke(app,f.scope(repo));self.assertEqual(status(second),503)
   self.assertIs(app.state.raw_qc_artifacts,handler);self.assertIs(handler._lease,lease)
   self.assertEqual(len(reader.calls),1);self.assertEqual(len(repo.calls),1)
  finally:reader.gate.set()
  self.assertEqual(status(await asyncio.wait_for(first,2)),200);self.assertIsNone(handler._lease)

 async def test_uncertain_cleanup_latches_across_requests_not_rebuilt(self):
  repo=f.Repository();reader=f.Reader();reader.error=api.ArtifactReadFailure(api.CLOSE_UNKNOWN);app=make_app(repo,reader)
  handler=app.state.raw_qc_artifacts
  self.assertEqual(status(await invoke(app,f.scope(repo))),503);lease=handler._lease;self.assertIsNotNone(lease)
  reader.error=None
  self.assertEqual(status(await invoke(app,f.scope(repo))),503)
  self.assertIs(app.state.raw_qc_artifacts,handler);self.assertIs(handler._lease,lease)
  self.assertEqual(len(reader.calls),1);self.assertEqual(len(repo.calls),1)

 async def test_app_instances_have_separate_admission_and_authority(self):
  bad=f.Reader();bad.error=api.ArtifactReadFailure(api.CLOSE_UNKNOWN)
  repo1=f.Repository();app1=make_app(repo1,bad);self.assertEqual(status(await invoke(app1,f.scope(repo1))),503)
  repo2=f.Repository();good=f.Reader();app2=make_app(repo2,good);self.assertEqual(status(await invoke(app2,f.scope(repo2))),200)
  self.assertIsNot(app1.state.raw_qc_artifacts,app2.state.raw_qc_artifacts);self.assertIsNot(app1.state.authority,app2.state.authority)
  self.assertIsNotNone(app1.state.raw_qc_artifacts._lease);self.assertIsNone(app2.state.raw_qc_artifacts._lease)

 async def test_other_owner_and_missing_auth_do_not_reach_reader(self):
  repo=f.Repository();reader=f.Reader();app=make_app(repo,reader)
  s=f.scope(repo);s['headers']=s['headers'][:1];self.assertEqual(status(await invoke(app,s)),401)
  app.state.authority.subject=f.f.OTHER
  self.assertEqual(status(await invoke(app,f.scope(repo))),404);self.assertEqual(reader.calls,[])

class Routing(unittest.IsolatedAsyncioTestCase):
 async def test_predicate_does_not_claim_ordinary_jobs_or_other_families(self):
  for path in ('/api/v1/raw-qc-jobs/id','/api/v1/raw-qc-jobs','/api/v1/datasets/id/raw-qc-jobs','/api/v1/raw-qc-jobs/id/artifactsOther/x','/api/v1/projects/id/artifacts/x','/api/v1/raw-qc-jobs/id/%2561rtifacts/x'):
   self.assertFalse(a._owns_raw_qc_artifact({'path':path}),path)
  for path in ('/api/v1/raw-qc-jobs/id/artifacts/x','/api/v1/raw-qc-jobs/id/artifacts','/api//v1/raw-qc-jobs/id/%61rtifacts/x'):
   self.assertTrue(a._owns_raw_qc_artifact({'path':path}),path)
  for path in (None,b'/api/v1/raw-qc-jobs/id/artifacts/x',17):self.assertFalse(a._owns_raw_qc_artifact({'path':path}))

 async def test_boundary_precedence_and_nonHTTP_delegation(self):
  seen=[]
  class Sink:
   def __init__(self,name):self.name=name
   def owns(self,scope):return True
   async def __call__(self,*args):seen.append(self.name)
  inner=Sink('inner');artifact=Sink('artifact');jobs=Sink('jobs');samples=Sink('samples')
  boundary=a.ProjectBoundary(inner,samples=samples,raw_qc=jobs,raw_qc_artifacts=artifact)
  async def forbidden():raise AssertionError('not receive')
  await boundary({'type':'http','path':'/api/v1/raw-qc-jobs/id/artifacts/x'},forbidden,forbidden)
  await boundary({'type':'http','path':'/api/v1/raw-qc-jobs/id'},forbidden,forbidden)
  await boundary({'type':'websocket','path':'/api/v1/raw-qc-jobs/id/artifacts/x'},forbidden,forbidden)
  self.assertEqual(seen,['artifact','jobs','inner'])

if __name__=='__main__':unittest.main(verbosity=2)
