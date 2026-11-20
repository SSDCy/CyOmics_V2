"""One synthetic target: original HTTP/Authority/transactions/_source/_decode;
MockTransport, memory SELECT driver, and explicit parent-science proof doubles.
No actual Auth HTTP/DB/RLS, SQL server, scientific/native run, or old suites.
"""
from pathlib import Path
import base64,hashlib,json,socket,subprocess,sys,time,types,unittest
from datetime import datetime,timezone,timedelta
from uuid import UUID
from unittest.mock import patch
from contextlib import ExitStack
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from psycopg.pq import TransactionStatus as T
from cyomics_projects.app import create_app
from cyomics_projects import de_classification_jobs as jobs,de_classification_repository as saved
from cyomics_projects.settings import EXPECTED_ISSUER,Fault
A='11111111-1111-4111-8111-111111111111';B='22222222-2222-4222-8222-222222222222'
P='33333333-3333-4333-8333-333333333333';PB='44444444-4444-4444-8444-444444444444'
R='55555555-5555-4555-8555-555555555555';R2='66666666-6666-4666-8666-666666666666';RB='77777777-7777-4777-8777-777777777777'
PARAM={'schema_version':'de_classification_v0.1','input_scale':'log2','adjusted_p_max':'0.05','absolute_log2fc_min':'1'}
def ident(i):return '00000000-0000-4000-8000-'+str(i).zfill(12)
def token(actor=A,role='authenticated'):
 enc=lambda v:base64.urlsafe_b64encode(json.dumps(v).encode()).decode().rstrip('=');now=int(time.time())
 return '.'.join(map(enc,({'alg':'HS256'},{'sub':actor,'role':role,'iss':EXPECTED_ISSUER,'aud':'authenticated','iat':now-2,'exp':now+120,'is_anonymous':False},'synthetic')))
def headers(actor=A):return {'authorization':'Bearer '+token(actor),'origin':'http://127.0.0.1:4206','sec-fetch-site':'same-origin'}
class Cursor:
 def __init__(self,row):self.row=row
 async def fetchone(self):return self.row
class Memory:
 def __init__(self):self.rows=[];self.sql=[];self.parent_checks=[];self.corrupt=False;self.leak=False;self.releases=0
 def parent(self,actor,project,source):
  if (actor,project,source) not in ((A,P,R),(A,P,R2),(B,PB,RB)):return None
  meta={'registration_id':ident(100),'request_id':ident(101),'admission_binding_sha256':'a'*64,'scientific_status':'ANALYSIS_READY','warning_count':0,'method':{'contract':'synthetic'}}
  snapshot={'contrast':{'contrast_id':ident(102)}};jobrow=[None]*17;jobrow[15]=saved.canonical(snapshot);jobrow[16]='b'*64
  binding={'schemaVersion':1,'contract':'de_parent_binding_v0.1','owner_id':actor,'project_id':project,'source_result_id':source,
   'source_job_id':ident(103),'source_attempt_id':ident(104),'source_registration_id':ident(100),'source_request_id':ident(101),
   'source_contrast_id':ident(102),'admission_binding_sha256':'a'*64,'input_snapshot_sha256':'b'*64,'source_metadata_sha256':saved.digest(meta),
   'source_receipt_sha256':'c'*64,'source_manifest_sha256':saved.digest([]),'source_artifacts':[],'source_status':'ANALYSIS_READY',
   'source_warning_count':0,'source_method_sha256':saved.digest(meta['method']),'contrast_direction':'numerator_minus_denominator'}
  row=[UUID(source),UUID(ident(103)),UUID(ident(104)),UUID(actor),UUID(project),saved.canonical(meta),saved.digest(meta),saved.canonical([]),saved.digest([]),'{}','c'*64,datetime(2026,1,1,tzinfo=timezone.utc)]+[0,'d'*64]*5
  return tuple(row),tuple(jobrow),binding
 def add(self,i,actor=A,project=P,source=R,seconds=0):
  binding=self.parent(actor,project,source)[2];request={'request_id':ident(i+1000),'source_result_id':source,'parameters':PARAM};spec=jobs.parameter_input(PARAM);intent=saved.request_payload(actor,project,request['request_id'],binding,spec)
  row=(UUID(ident(i)),UUID(project),UUID(actor),UUID(request['request_id']),saved.canonical(request),saved.canonical(intent),saved.digest(intent),UUID(source),binding['source_manifest_sha256'],saved.canonical(binding),saved.digest(binding),spec.body,jobs.c.sha(spec.body),saved.canonical(saved.core()),'queued',datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(seconds=seconds))
  self.rows.append(row)
 async def getconn(self):return Connection(self)
 def release(self,c):assert c.closed;self.releases+=1
class Connection:
 def __init__(self,m):self.m=m;self.closed=False;self.pgconn=types.SimpleNamespace(transaction_status=T.IDLE,finish=self.finish)
 def finish(self):self.closed=True
 async def execute(self,sql,params=()):
  m=self.m;m.sql.append((sql,params));row=None
  if sql.startswith('BEGIN'):assert sql.endswith(' READ ONLY');self.pgconn.transaction_status=T.INTRANS
  elif sql in ('COMMIT','ROLLBACK'):self.pgconn.transaction_status=T.IDLE
  elif 'set_config' in sql:pass
  elif sql.startswith('SELECT project_id FROM'):
   actor,project=map(str,params);row=(UUID(project),) if (actor,project) in ((A,P),(B,PB)) else None
  elif ' FROM cyomics_app.differential_results ' in sql:
   parent=m.parent(*map(str,params));row=None if parent is None else parent[0]
  elif ' FROM cyomics_app.differential_jobs ' in sql:
   actor,project,job=map(str,params);parent=m.parent(actor,project,R if actor==A else RB);row=parent[1]
  elif ' FROM '+jobs.TABLE+' ' in sql:
   assert 'owner_id=%s AND project_id=%s AND source_result_id=%s' in sql
   assert sql.endswith(' ORDER BY created_at DESC,classification_job_id DESC LIMIT 1')
   actor,project,source=map(str,params[:3]);rows=[r for r in m.rows if tuple(map(str,(r[2],r[1],r[7])))==(actor,project,source)]
   if len(params)==5:rows=[r for r in rows if (r[15],r[0])<(params[3],params[4])]
   rows.sort(key=lambda r:(r[15],r[0]),reverse=True);row=rows[0] if rows else None
   if m.leak:row=next(r for r in m.rows if str(r[7])==R2)
   if row is not None and m.corrupt:row=tuple('f'*64 if i==6 else v for i,v in enumerate(row))
  else:raise AssertionError('Unexpected or writing SQL in synthetic driver: '+sql)
  return Cursor(row)
class HistoryTarget(unittest.IsolatedAsyncioTestCase):
 async def test_one_owner_history_target(self):
  db=Memory();auth_calls=[]
  async def auth(request):
   actor=json.loads(base64.urlsafe_b64decode(request.headers['authorization'].split('.')[1]+'=='))['sub'];auth_calls.append(actor)
   return httpx.Response(200,json={'id':actor,'role':'authenticated','aud':'authenticated','is_anonymous':False,'email':'synthetic@example.invalid','email_confirmed_at':'2026-01-01T00:00:00Z'})
  # Only original scientific parent proof dependencies are doubled; original
  # _source reconstructs its20-key binding, and classification _decode stays real.
  def parent_job_decode(repo,row,actor,project):self.assertEqual(len(row),17)
  async def parent_proof(repo,query,actor,project,source,binding):
   parent=db.parent(actor,project,source);self.assertIsNotNone(parent);self.assertEqual(binding,parent[2]);db.parent_checks.append((actor,project,source));return []
  app=create_app(auth_transport=httpx.MockTransport(auth),pool=db,anon_key_loader=lambda:token(role='anon'),classification_jobs_enabled=True)
  path=f'/api/v1/projects/{P}/classification-jobs';query='?source_result_id='+R+'&view=history'
  with ExitStack() as stack:
   net=stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')))
   native=stack.enter_context(patch.object(subprocess,'Popen',side_effect=AssertionError('native forbidden')))
   enqueue=stack.enter_context(patch.object(jobs.ClassificationAdmissionRepository,'enqueue',side_effect=AssertionError('admission forbidden')))
   stack.enter_context(patch.object(jobs.original.DifferentialRepository,'_decode',parent_job_decode))
   stack.enter_context(patch.object(saved.ClassificationRepository,'_parent',parent_proof))
   try:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:57430') as c:
     r=await c.get(path+query,headers=headers());self.assertEqual(r.status_code,200,r.text);v=r.json();self.assertEqual(v,{'schemaVersion':1,'contract':jobs.HISTORY_CONTRACT,'project_id':P,'source_result_id':R,'items':[]});self.assertEqual(len(db.parent_checks),1)
     for i in range(1,8):db.add(i,seconds=0 if i<=4 else 1)
     db.add(90,source=R2,seconds=10);db.add(91,actor=B,project=PB,source=RB,seconds=20)
     before=len(db.sql);proofs=len(db.parent_checks);r=await c.get(path+query,headers=headers());self.assertEqual(r.status_code,200,r.text);items=r.json()['items']
     self.assertEqual([v['classification_job_id'] for v in items],[ident(i) for i in (7,6,5,4,3)])
     self.assertTrue(all(v['contract']==jobs.CONTRACT and v['status']=='queued' and v['created_by']==A and v['source_result_id']==R for v in items))
     self.assertTrue(all('derived_version_id' not in v for v in items));self.assertEqual(len(db.parent_checks)-proofs,6)
     self.assertEqual(sum(' FROM '+jobs.TABLE+' ' in sql for sql,_ in db.sql[before:]),5)
     self.assertIn('no-store',r.headers['cache-control'])
     # Other existing request GET still returns original DTO; no implicit scientific state lookup.
     with patch.object(jobs.ClassificationAdmissionRepository,'read_request',return_value=items[0]) as original_get:
      r=await c.get(path+'?request_id='+ident(1007),headers=headers());self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json(),items[0]);self.assertEqual(original_get.call_count,1)
     for url,actor in ((path+query,B),(f'/api/v1/projects/{PB}/classification-jobs'+query,B)):
      before=len(db.sql);r=await c.get(url,headers=headers(actor));self.assertEqual(r.status_code,404,r.text)
      self.assertEqual(sum(' FROM '+jobs.TABLE+' ' in sql for sql,_ in db.sql[before:]),0)
     r=await c.get(path+query);self.assertEqual(r.status_code,401,r.text)
     for suffix in ('?view=history&source_result_id='+R,query+'&limit=10',query+'&view=history','?source_result_id=AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA&view=history','?source_result_id='+R):
      if suffix==query:continue
      r=await c.get(path+suffix,headers=headers());self.assertEqual(r.status_code,400,r.text)
     db.corrupt=True;r=await c.get(path+query,headers=headers());self.assertEqual(r.status_code,503,r.text);db.corrupt=False
     db.leak=True;r=await c.get(path+query,headers=headers());self.assertEqual(r.status_code,503,r.text);db.leak=False
     with self.assertRaises(Fault):jobs.history_value({**v,'items':items[::-1]},subject=A,project=P,source=R)
     with self.assertRaises(Fault):jobs.history_value({**v,'items':items+[items[-1]]},subject=A,project=P,source=R)
    self.assertEqual((enqueue.call_count,net.call_count,native.call_count),(0,0,0))
    self.assertTrue(all(sql.startswith(('SELECT','BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY')) or sql in ('COMMIT','ROLLBACK') for sql,_ in db.sql));self.assertGreater(db.releases,0)
   finally:await app.state.authority.close()
if __name__=='__main__':unittest.main()
