"""ONE new finite offline target. Actual HTTP/Authority/enqueue/TX/controller.
Memory SQL and source/parent repository adapters + fake child transport explicitly
replace external boundaries. No actual approval/capability, PG/RLS/native/listener.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import asyncio,base64,copy,json,hashlib,socket,subprocess,time,types,unittest
from datetime import datetime,timezone,timedelta
from uuid import UUID,uuid4
from unittest.mock import patch
from contextlib import ExitStack
import httpx
from psycopg.pq import TransactionStatus as T
from cyomics_projects import preprocess_contract as c,preprocess_repository as p,median_processed_profile as median
from cyomics_projects import median_on_demand as dispatch,preprocess_median_service_bootstrap as service,classification_on_demand as shared
from cyomics_projects.app import create_app
from cyomics_projects.settings import Budget,Fault,EXPECTED_ISSUER
from cyomics_projects import settings,preprocess_execution as execution,preprocess_dev_probe as oldbootstrap

A='11111111-1111-4111-8111-111111111111';B='22222222-2222-4222-8222-222222222222'
P='47278eaf-c156-4b66-b60a-287ca4b23805';D='28bc9473-173f-5c48-bc61-7edc7c40f584';PARENT='2fc0a8e6-ef3d-4afe-a15c-f27043b00227'
BACKEND=Path(__file__).resolve().parents[1]
BASE=json.loads((BACKEND.parent/'frontend/tests/fixtures/preprocess-v2-contract/fixtures/positive/processed-version.json').read_bytes())
PARENT_VALUE=copy.deepcopy(BASE);PARENT_VALUE.update(project_id=P,parent_dataset_version_id=D,processed_dataset_version_id=PARENT)
PARENT_VALUE['source'].update(project_id=P,dataset_version_id=D)
SOURCE=PARENT_VALUE['source'];PARAMS=median.parameters(PARENT_VALUE['parameters']['filter_flags'],dict(processed_dataset_version_id=PARENT,manifest_sha256=PARENT_VALUE['manifest_sha256']))
MEDIAN=median.proposal_bytes();NONE=c.canonical(dict(contract='log2_local_source_proposal_v0.1',entry_kind='native_R',r_version='R version 4.6.1',**p.FIXED_PINS,runner={'bytes':30207,'sha256':'6f7839b4a867cea8fd9aef63b684ac8c9dbf63dbc7a95bb289080e8aa8074a3e'},limits=dict(p.LIMITS),process_policy_sha256=p.POLICY_SHA256,registered=False))
CONFIG=dispatch.MedianDispatchConfig(shared.Config(enabled=True,startup=BACKEND.parent/'controls/median_service_startup.py',backend=BACKEND,
    control=service.ROOT/'launches',startup_sha256='1'*64,python_sha256='2'*64,verify=lambda:None),
    service.ServiceWorkerConfig(100,'3'*64,BACKEND,BACKEND.parent/'controls/median_service_startup.py'))

def body(rid=None,normalization='median'):
 return c.canonical(dict(request_id=rid or str(uuid4()),quant_family='LFQ intensity',parameters=PARAMS if normalization=='median' else PARENT_VALUE['parameters']))
def token(actor=A,role='authenticated'):
 def enc(v):return base64.urlsafe_b64encode(json.dumps(v).encode()).rstrip(b'=').decode()
 now=int(time.time());return '.'.join([enc({'alg':'HS256'}),enc(dict(sub=actor,role=role,iss=EXPECTED_ISSUER,aud='authenticated',iat=now-2,exp=now+120,is_anonymous=False)),enc('synthetic')])
def headers(actor=A):return {'authorization':'Bearer '+token(actor),'origin':'http://127.0.0.1:4206','x-cyomics-request':'1','sec-fetch-site':'same-origin','content-type':'application/json'}
class Cursor:
 def __init__(self,row=None):self.row=row
 async def fetchone(self):return copy.deepcopy(self.row)
class DB:
 def __init__(self):
  self.jobs={};self.bindings={};self.registrations={};self.events=[];self.insert_fail=False;self.commit_unknown=False;self.close_unknown=False;self.issue_calls=0;self.cap=4;self.sources=0;self.parents=0;self.lock=asyncio.Lock()
 async def getconn(self):return Connection(self)
 def release(self,connection):assert connection.closed
class Connection:
 def __init__(self,db):self.db=db;self.pgconn=self;self.transaction_status=T.IDLE;self.closed=False;self.held=False;self.subject=None;self.pending={};self.binding={};self.registration={};self.committed=False
 def finish(self):
  self.closed=True;self.transaction_status=T.UNKNOWN
  if self.held:self.db.lock.release();self.held=False
  if self.committed and self.db.close_unknown:raise OSError('synthetic close acknowledgement unknown')
 async def execute(self,sql,params=()):
  d=self.db;d.events.append(sql)
  if sql.startswith('BEGIN'):self.transaction_status=T.INTRANS;return Cursor()
  if "set_config('app.user_id'" in sql:self.subject=params[0];return Cursor()
  if sql.startswith('SELECT pg_catalog.set_config'):return Cursor()
  if sql=='COMMIT':
   assert all(str(r[10]) in self.pending for r in self.binding.values()),'deferred exact job FK'
   d.jobs.update(self.pending);d.bindings.update(self.binding);d.registrations.update(self.registration);self.pending={};self.committed=True;self.transaction_status=T.IDLE
   if self.held:d.lock.release();self.held=False
   if d.commit_unknown and self.binding:
    d.commit_unknown=False;raise OSError('synthetic lost COMMIT reply')
   return Cursor()
  if sql=='ROLLBACK':
   self.pending={};self.binding={};self.registration={};self.transaction_status=T.IDLE
   if self.held:d.lock.release();self.held=False
   return Cursor()
  if 'pg_advisory_xact_lock' in sql:await d.lock.acquire();self.held=True;return Cursor()
  if 'FROM cyomics_app.projects' in sql:return Cursor((UUID(P),) if self.subject==A and str(params[0])==A and str(params[1])==P else None)
  if 'FROM cyomics_app.preprocessing_jobs' in sql:
   rows=[v for v in {**d.jobs,**self.pending}.values() if str(v[1])==str(params[0])]
   if 'AND dataset_version_id=' in sql:rows=[v for v in rows if str(v[2])==str(params[1]) and str(v[5])==str(params[2])]
   elif 'AND request_id=' in sql:rows=[v for v in rows if str(v[5])==str(params[1])]
   else:rows=[v for v in rows if str(v[0])==str(params[1])]
   return Cursor(rows[0] if rows else None)
  if 'FROM cyomics_app.preprocessing_job_runs' in sql:return Cursor()
  if 'issue_median_request_registration' in sql:
   d.issue_calls+=1;assert self.subject==A and self.held
   scope,pid,did,raw,manifest,job=map(str,params);req=c.request_input(raw.encode(),median_enabled=True,total_enabled=True)
   if len(d.bindings)>=d.cap:raise Fault(503,'preprocessing_execution_unavailable')
   assert pid==P and did==D and req.parameters_value()==PARAMS
   rh=p.scoped_request_hash(P,D,req);bh=p.binding_hash(rh,SOURCE,req,p.execution_value(MEDIAN.decode()));reg=uuid4();epoch=uuid4();now=datetime.now(timezone.utc)
   row=('issued',reg,epoch,'a'*64,UUID(A),UUID(P),UUID(D),UUID(req.request_id),rh,bh,UUID(job),len(d.bindings),now,now+timedelta(seconds=900),now)
   self.binding[job]=row;self.registration[str(reg)]=row;return Cursor(row)
  if sql.startswith('INSERT INTO cyomics_app.preprocessing_jobs'):
   if d.insert_fail:raise OSError('synthetic insert failure')
   row=list(params)+['queued',datetime.now(timezone.utc)];key=str(row[0]);self.pending[key]=row;return Cursor(row)
  raise AssertionError('unexpected fixed SQL '+sql[:90])

class Reader:
 def __init__(self,raw):self.raw=raw
 async def read(self,n):v=self.raw[:n];self.raw=self.raw[n:];return v
class Stdin:
 def write(self,raw):self.raw=raw
 async def drain(self):pass
 def close(self):pass
 async def wait_closed(self):pass
class Transport:
 _finished=True
 def close(self):self.closing=True
 def is_closing(self):return self.closing
 def get_pipe_transport(self,fd):return None
class FakeChild:
 def __init__(self,raw):self.returncode=0;self.stdout=Reader(raw);self.stderr=Reader(b'');self.stdin=Stdin();self._transport=Transport()
 async def wait(self):return 0

class MedianAutomaticTarget(unittest.IsolatedAsyncioTestCase):
 async def test_one_new_finite_dispatch_target(self):
  apps=[];clients=[];groups=[];spawned=[];intents=[];closed=[];source_gates=(settings.AUTH_POLICY_VERIFIED,settings.RUNTIME_START_APPROVED)
  c.version_value(PARENT_VALUE)
  async def source(repo,q,subject,project,dataset,family,flags):repo.pool.sources+=1;return copy.deepcopy(SOURCE)
  async def parent(repo,q,subject,project,version,verify_output=True):repo.pool.parents+=1;self.assertEqual(version,PARENT);return copy.deepcopy(PARENT_VALUE)
  def sources(controller):controller.scope=dict(scope_id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',scope_sha256='a'*64,owner_id=A,project_id=P,dataset_version_id=D,parameters_json=p.canonical(PARAMS),expires_at_ms=int((time.time()+4000)*1000),profile={'source_manifest_sha256':'b'*64})
  async def auth(request):
   actor=json.loads(base64.urlsafe_b64decode(request.headers['authorization'].split('.')[1]+'=='))['sub']
   return httpx.Response(200,json=dict(id=actor,role='authenticated',aud='authenticated',is_anonymous=False,email='synthetic@example.invalid',email_confirmed_at='2026-01-01T00:00:00Z'))
  def client(db=None,on=True):
   db=db or DB();repo=p.PreprocessRepository(db,NONE,enabled=True,median_execution_json=MEDIAN)
   app=create_app(auth_transport=httpx.MockTransport(auth),pool=db,anon_key_loader=lambda:token(role='anon'),preprocessing_repository=repo,median_dispatch_config=CONFIG if on else None);apps.append(app)
   cl=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:57430');clients.append(cl);return cl,app,db,repo
  base='/api/v1/datasets/'+D+'/preprocessing-jobs?project_id='+P
  async def post(cl,raw=None,actor=A):return await cl.post(base,content=raw or body(),headers=headers(actor))
  async def wait_controller(controller):
   monitor=controller._monitor
   if monitor is not None:await monitor
  async def spawn(controller):
   spawned.append(dict(controller._notification));n=controller._notification
   receipt=dict(schemaVersion=1,job_id=n['planned_job_id'],request_id=n['request_id'],registration_id=n['registration_id'],attempt_id=str(uuid4()),result_id=str(uuid4()),disposition='confirmed',reason='succeeded',acknowledged=True,teardown='confirmed',loop_closed=True,pending_tasks_seen=False,async_resources_remaining=False,interrupted=False,exit_code=0)
   return FakeChild(c.canonical(receipt)+b'\n')
  with ExitStack() as stack:
   network=stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('actual network forbidden')))
   native=stack.enter_context(patch.object(subprocess,'Popen',side_effect=AssertionError('actual native forbidden')))
   asyncnative=stack.enter_context(patch('asyncio.create_subprocess_exec',side_effect=AssertionError('actual child forbidden')))
   stack.enter_context(patch.object(p.PreprocessRepository,'_source',new=source));stack.enter_context(patch.object(p.PreprocessRepository,'_read_version',new=parent))
   stack.enter_context(patch.object(dispatch.MedianController,'_sources',new=sources))
   stack.enter_context(patch.object(dispatch.MedianController,'_intent',new=lambda self,value,**kw:intents.append(dict(value))))
   stack.enter_context(patch.object(dispatch.MedianController,'_spawn',new=spawn))
   stack.enter_context(patch.object(service,'write_once',side_effect=lambda path,value:closed.append((str(path),value))))
   try:
    self.assertIsNone(dispatch.prepare());self.assertIsNone(execution.create_runtime());self.assertIsNone(oldbootstrap.bootstrap())
    off,offapp,offdb,_=client(on=False);self.assertIsNone(offapp.state.median_dispatch)
    response=await post(off);self.assertEqual(response.status_code,201,response.text);self.assertEqual(offdb.issue_calls,0);self.assertFalse(spawned)
    cl,app,db,repo=client();raw=body();rid=json.loads(raw)['request_id'];response=await post(cl,raw);self.assertEqual(response.status_code,201,response.text);dto=response.json();self.assertEqual(len(dto),20);self.assertEqual(dto['parameters']['normalization'],'median');await wait_controller(app.state.median_dispatch)
    self.assertEqual((len(db.jobs),len(db.bindings),len(db.registrations),len(spawned),len(intents),len(closed)),(1,1,1,1,1,1))
    self.assertEqual(app.state.median_dispatch._state,'idle');self.assertTrue(closed[0][1]['receipt']['acknowledged'])
    issue=next(i for i,s in enumerate(db.events) if 'issue_median_request_registration' in s);insert=next(i for i,s in enumerate(db.events) if s.startswith('INSERT INTO'));self.assertLess(issue,insert);self.assertEqual(db.issue_calls,1)
    before=(db.issue_calls,len(spawned),db.parents);response=await post(cl,raw);self.assertEqual(response.status_code,200,response.text)
    response=await cl.get(base+'&request_id='+rid,headers=headers());self.assertEqual(response.status_code,200,response.text);self.assertEqual(before,(db.issue_calls,len(spawned),db.parents))
    other=json.loads(raw);other['parameters']['filter_flags']['reverse']=True;self.assertEqual((await post(cl,c.canonical(other))).status_code,409)
    self.assertEqual((await post(cl,actor=B)).status_code,404);self.assertEqual((db.issue_calls,len(spawned)),(1,1))
    response=await post(cl,body(normalization='none'));self.assertEqual(response.status_code,201,response.text);self.assertEqual((db.issue_calls,len(spawned)),(1,1))
    groups.append('defaultoff preserves original manual admission; real Authority/Owner/enqueue/raw transaction + source/parent adapters; registration before original job INSERT atomically confirmed; original supervised transport receives one fake child with exact ticket; repeated POST/GET zero new issue/launch; None unmodified')
    # Definite precommit failure rolls registration/binding/admission back together.
    failed,failedapp,faildb,_=client();faildb.insert_fail=True;response=await post(failed);self.assertEqual(response.status_code,503,response.text);self.assertEqual((len(faildb.jobs),len(faildb.bindings),len(faildb.registrations)),(0,0,0));self.assertEqual(failedapp.state.median_dispatch._state,'idle')
    # Committed DB truth + lost ACK is sticky, but independent GET/repeat only reads.
    unknown,uapp,udb,urepo=client();udb.commit_unknown=True;raw2=body();rid2=json.loads(raw2)['request_id'];n=len(spawned);response=await post(unknown,raw2);self.assertEqual(response.status_code,504,response.text);self.assertTrue(urepo.quarantined);self.assertEqual(uapp.state.median_dispatch._state,'unknown');self.assertEqual((len(udb.jobs),len(udb.bindings),len(udb.registrations)),(1,1,1));self.assertEqual(len(spawned),n)
    response=await unknown.get(base+'&request_id='+rid2,headers=headers());self.assertEqual(response.status_code,200,response.text);self.assertEqual((await post(unknown,raw2)).status_code,200);self.assertEqual(udb.issue_calls,1);self.assertEqual(len(spawned),n)
    # Teardown ambiguity after successful COMMIT likewise cannot notify.
    closebad,capp,cdb,_=client();cdb.close_unknown=True;n=len(spawned);self.assertEqual((await post(closebad)).status_code,504);self.assertEqual(capp.state.median_dispatch._state,'unknown');self.assertEqual(len(spawned),n)
    # Launch intent failure is postcommit unknown, not a rollback/refund/retry.
    launchbad,lapp,ldb,_=client();raw3=body();n=len(spawned)
    with patch.object(lapp.state.median_dispatch,'_intent',side_effect=OSError('synthetic intent close unknown')):response=await post(launchbad,raw3)
    self.assertEqual(response.status_code,504,response.text);self.assertEqual(lapp.state.median_dispatch._state,'unknown');self.assertEqual(len(ldb.jobs),1);self.assertEqual((await post(launchbad,raw3)).status_code,200);self.assertEqual(len(spawned),n)
    groups.append('original transaction definite rollback zero3rows; COMMIT/close unknown no child; independently read committed truth never clears latch; failed launch-intent preserves committed admission and repeated POST zero launch')
    capcl,capapp,capdb,_=client();capapp.state.median_dispatch._capacity.unknown=True;self.assertEqual((await post(capcl)).status_code,503);self.assertEqual(capdb.issue_calls,0)
    quota,_,qdb,_=client();qdb.cap=0;response=await post(quota);self.assertEqual(response.status_code,503);self.assertFalse(qdb.jobs or qdb.bindings)
    expired,eapp,edb,_=client()
    def expired_source(controller):sources(controller);controller.scope['expires_at_ms']=int(time.time()*1000)
    with patch.object(dispatch.MedianController,'_sources',new=expired_source):self.assertEqual((await post(expired)).status_code,503)
    self.assertEqual(edb.issue_calls,0)
    fake=service.ServiceApprovedRuntime(CONFIG.service,{}, {},(1,1,1000),None,(),object())
    with self.assertRaises(Fault):fake.check()
    with self.assertRaises(Fault):execution.create_runtime(fake)
    with self.assertRaises(Fault):service.scope_value(b'{}')
    self.assertEqual(oldbootstrap.APPROVAL_PATH,Path('/home/user01/Cy_Omics_v2.0/runtime/preprocess-execution-001/Root-approval.json'))
    n=spawned[0];self.assertEqual(service.parse_notification(c.canonical(n)+b'\n'),n)
    with self.assertRaises(Fault):service.parse_notification(c.canonical(dict(n,verified=True))+b'\n')
    self.assertEqual(source_gates,(settings.AUTH_POLICY_VERIFIED,settings.RUNTIME_START_APPROVED));self.assertEqual((network.call_count,native.call_count,asyncnative.call_count),(0,0,0))
    groups.append('pre-mutation capacity/quota/expiry refusal; forged sealed capability rejected before IO; original global approval and gates unchanged; exact bounded IPC rejects verified flag; no actual gate/bootstrap/PG/native/listener')
    print(json.dumps(dict(target='PASS_ONE_MEDIAN_AUTO_DISPATCH_OFFLINE_TARGET',groups=groups,original_Authority='MockTransport',original_enqueue=True,original_transaction=True,source_and_None_parent='explicit synthetic repository adapters, not ParentAPI authorization',process='fake pipes/transport only; original controller supervision exercised',actual_approval_or_capability=False,actual_DB_R_services=0,old_target_runs=0),sort_keys=True))
   finally:
    for cl in clients:await cl.aclose()
    # All fake child monitors are settled before close; never call lifespan/service.
    for app in apps:
     ctl=app.state.median_dispatch
     if ctl is not None:await ctl.close()
     app.state.complete_imports.close();await app.state.authority.close()
class MetadataReaderTarget(unittest.TestCase):
 def test_metadata_reader_atime_is_not_identity(self):
  # New static-review regression only. Actual bytes come from owned temp fixture;
  # metadata UID/atime are explicit stat doubles, NOT a Root approval capability.
  import tempfile,stat
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'fixture';raw=c.canonical({'contract':'reader_fixture'})+b'\n';path.write_bytes(raw);path.chmod(0o600)
   info=path.stat();before=types.SimpleNamespace(st_dev=info.st_dev,st_ino=info.st_ino,st_mode=info.st_mode,st_uid=1000,st_nlink=1,st_size=len(raw),st_mtime_ns=info.st_mtime_ns,st_ctime_ns=info.st_ctime_ns,st_atime_ns=1)
   after=types.SimpleNamespace(**dict(vars(before),st_atime_ns=2))
   with patch.object(service.os,'fstat',side_effect=[before,after]),patch.object(Path,'lstat',return_value=after):
    self.assertEqual(service.read_private(path,4096),{'contract':'reader_fixture'})
  print('PASS_NEW_METADATA_READER_REGRESSION_ONLY; atime drift accepted, inode/mode/UID/nlink/size/mtime/ctime still pinned; no Root approval or gate activation')

if __name__=='__main__':unittest.main()
