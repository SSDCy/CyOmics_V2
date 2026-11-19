"""One finite HTTP/core target. Real Authority/HTTP/parser/UploadRepository TX;
explicit memory SQL/pool and Auth MockTransport, never live DB/RLS/R/listeners.
"""
from pathlib import Path
import asyncio,base64,copy,hashlib,json,socket,subprocess,sys,time,types,unittest
from unittest.mock import patch
from uuid import UUID,uuid4
from contextlib import ExitStack
BACKEND=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BACKEND))
import httpx
from psycopg.pq import TransactionStatus as T
from cyomics_projects.app import create_app,entry_budget
from cyomics_projects import fasta_intake_contract as model,fasta_private_repository as core
from cyomics_projects.settings import EXPECTED_ISSUER,Fault
from cyomics_projects.upload_repository import UploadOutcome
from cyomics_projects.fasta_private_http import MAX_JSON
A='11111111-1111-4111-8111-111111111111';B='22222222-2222-4222-8222-222222222222'
P='33333333-3333-4333-8333-333333333333';P2='44444444-4444-4444-8444-444444444444';P3='55555555-5555-4555-8555-555555555555';PB='66666666-6666-4666-8666-666666666666'
RAW=b'>dup original header\r\nACd\r\n>dup second header\r\nA*\r\n>empty\r\n'
def token(actor=A,role='authenticated'):
 enc=lambda x:base64.urlsafe_b64encode(json.dumps(x).encode()).rstrip(b'=').decode();now=int(time.time())
 return '.'.join([enc({'alg':'HS256'}),enc({'sub':actor,'role':role,'iss':EXPECTED_ISSUER,'aud':'authenticated','iat':now-2,'exp':now+120,'is_anonymous':False}),enc('synthetic')])
def head(raw=RAW,request=None,name='私有原文件.fasta',actor=A):
 return {'authorization':'Bearer '+token(actor),'origin':'http://127.0.0.1:4206','sec-fetch-site':'same-origin','x-cyomics-request':'1','content-type':'application/octet-stream','idempotency-key':request or str(uuid4()),'x-cyomics-filename':base64.urlsafe_b64encode(name.encode()).rstrip(b'=').decode(),'x-cyomics-file-format':'protein_fasta','x-cyomics-file-bytes':str(len(raw)),'x-cyomics-file-sha256':hashlib.sha256(raw).hexdigest()}
class Cursor:
 def __init__(self,rows):self.rows=rows
 async def fetchone(self):return self.rows[0] if self.rows else None
 async def fetchall(self):return self.rows
class DB:
 def __init__(self):
  self.projects={P:A,P2:A,P3:A,PB:B};self.sources={};self.indexes={};self.events=[];self.index_fail=False;self.unknown_commit=False;self.count_override=None;self.release_count=0
 async def getconn(self):return Connection(self)
 def release(self,connection):
  assert connection.closed or connection.pgconn.transaction_status==T.IDLE;self.release_count+=1
class Connection:
 def __init__(self,db):
  self.db=db;self.actor=None;self.closed=False;self.pgconn=types.SimpleNamespace(transaction_status=T.IDLE,finish=self.finish);self.writing=False
 def finish(self):self.closed=True
 async def execute(self,sql,params=()):
  db=self.db;db.events.append(sql)
  if sql.startswith('BEGIN'):
   self.writing='READ ONLY' not in sql;self.snapshot=(copy.copy(db.sources),copy.copy(db.indexes));self.pgconn.transaction_status=T.INTRANS
  elif sql=='COMMIT':
   self.pgconn.transaction_status=T.IDLE
   if self.writing and db.unknown_commit:db.unknown_commit=False;raise RuntimeError('synthetic commit acknowledgement loss')
  elif sql=='ROLLBACK':db.sources,db.indexes=self.snapshot;self.pgconn.transaction_status=T.IDLE
  elif "set_config('app.user_id'" in sql:self.actor=str(params[0])
  elif 'set_config' in sql:pass
  elif sql.startswith('SELECT project_id FROM'):
   actor,project=map(str,params);assert self.actor==actor;return Cursor([(UUID(project),)] if db.projects.get(project)==actor else [])
  elif 'pg_try_advisory_xact_lock' in sql:return Cursor([(True,)])
  elif sql.startswith('SELECT count(*)'):
   project,project2,actor=map(str,params);assert actor==self.actor and project==project2
   if db.count_override is not None:return Cursor([db.count_override])
   rows=[row for row in db.sources.values() if db.projects[str(row[1])]==actor];here=[row for row in rows if str(row[1])==project]
   return Cursor([(len(rows),sum(row[5] for row in rows),len(here),sum(row[5] for row in here))])
  elif sql.startswith('SELECT s.source_id'):
   project=str(params[0]);assert db.projects[project]==self.actor
   if 'ORDER BY s.created_at DESC' in sql:
    rows=sorted((row for row in db.sources.values() if str(row[1])==project),key=lambda row:(row[8],str(row[0])),reverse=True)[:5]
    return Cursor([row[:9]+(db.indexes[str(row[0])][-2],db.indexes[str(row[0])][-1]) for row in rows])
   key=str(params[1]);row=next((row for row in db.sources.values() if str(row[1])==project and str(row[0])==key),None) if 's.source_id=%s' in sql else db.sources.get((project,key))
   idx=db.indexes.get(str(row[0])) if row else None;return Cursor([row+(idx[-2],idx[-1])] if row and idx else [row+(None,None)] if row else [])
  elif sql.startswith('INSERT INTO cyomics_app.fasta_sources'):
   assert self.writing and db.projects[str(params[1])]==self.actor;key=(str(params[1]),str(params[2]));assert key not in db.sources;db.sources[key]=tuple(params);return Cursor([(params[0],)])
  elif sql.startswith('INSERT INTO cyomics_app.fasta_index_versions'):
   assert self.writing
   if db.index_fail:db.index_fail=False;raise RuntimeError('synthetic second insert failure')
   key=str(params[2]);assert key not in db.indexes;db.indexes[key]=tuple(params);return Cursor([(params[0],)])
  else:raise AssertionError('unexpected SQL')
  return Cursor([])
class PrivateFastaTarget(unittest.IsolatedAsyncioTestCase):
 async def test_one_finite_HTTP_core_target(self):
  db=DB();apps=[];clients=[];groups=[];auth_calls=[]
  async def auth(request):
   auth_calls.append(1);actor=json.loads(base64.urlsafe_b64decode(request.headers['authorization'].split('.')[1]+'=='))['sub']
   return httpx.Response(200,json={'id':actor,'role':'authenticated','aud':'authenticated','is_anonymous':False,'email':'synthetic@example.invalid','email_confirmed_at':'2026-01-01T00:00:00Z'})
  def client(enabled=True):
   app=create_app(auth_transport=httpx.MockTransport(auth),pool=db,anon_key_loader=lambda:token(role='anon'),fasta_private_enabled=enabled);apps.append(app)
   c=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:57430');clients.append(c);return c,app
  prefix=lambda project:'/api/v1/projects/'+project+'/fasta-sources'
  async def upload(c,project=P,raw=RAW,rq=None,name='私有原文件.fasta',**kwargs):return await c.post(prefix(project),content=raw,headers=head(raw,rq,name),**kwargs)
  common={'authorization':'Bearer '+token(),'origin':'http://127.0.0.1:4206','sec-fetch-site':'same-origin'}
  with ExitStack() as stack:
   no_network=stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')));no_native=stack.enter_context(patch.object(subprocess,'Popen',side_effect=AssertionError('native forbidden')));no_async=stack.enter_context(patch('asyncio.create_subprocess_exec',side_effect=AssertionError('native forbidden')))
   parser=stack.enter_context(patch.object(model,'parse_fasta',wraps=model.parse_fasta))
   try:
    off,offapp=client(False);self.assertIsNone(offapp.state.fasta_private.repository);self.assertEqual((await off.get(prefix(P),headers=common)).status_code,503);self.assertEqual((len(db.events),parser.call_count),(0,0))
    with self.assertRaises(Exception):client(1)
    c,app=client();rq=str(uuid4());n=parser.call_count;response=await upload(c,rq=rq);self.assertEqual(response.status_code,201,response.text);dto=response.json();self.assertEqual(parser.call_count-n,1);self.assertEqual(set(dto),{'source','index'});self.assertEqual(dto['index']['summary']['record_count'],3);self.assertEqual(dto['index']['summary']['invalid_canonical_record_count'],2);self.assertFalse(dto['index']['index_materialized']);self.assertTrue(dto['index']['source_complete']);sid=dto['source']['source_id'];self.assertEqual(len(db.sources),len(db.indexes));self.assertEqual(len(db.sources),1)
    stored=next(iter(db.sources.values()));self.assertEqual(stored[9],RAW);self.assertEqual(dto['source']['sha256'],hashlib.sha256(RAW).hexdigest())
    n=parser.call_count;response=await upload(c,rq=rq);self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json(),dto);self.assertEqual(parser.call_count-n,1)
    events=len(db.events);n=parser.call_count;response=await c.get(prefix(P),headers=common);self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json(),{'items':[dto],'nextCursor':None});self.assertEqual(parser.call_count,n);self.assertFalse(any('s.content' in sql for sql in db.events[events:]));self.assertFalse(any(sql.startswith('INSERT') for sql in db.events[events:]))
    reopened,app2=client()
    for path in [prefix(P)+'/requests/'+rq,prefix(P)+'/'+sid]:
     n=parser.call_count;response=await reopened.get(path,headers=common);self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json(),dto);self.assertEqual(parser.call_count-n,1)
    n=parser.call_count;response=await reopened.get(prefix(P)+'/'+sid+'/content',headers=common);self.assertEqual(response.status_code,200);self.assertEqual(response.content,RAW);self.assertEqual(response.headers['x-cyomics-file-sha256'],dto['source']['sha256']);self.assertEqual(response.headers['content-length'],str(len(RAW)));self.assertEqual(response.headers['content-type'],'application/octet-stream');self.assertIn("filename*=UTF-8''",response.headers['content-disposition']);self.assertEqual(parser.call_count-n,1)
    groups.append('original Authority/transaction/parser save201; idempotent200; metadata list0parse; refresh source/receipt/content each1parse and exact CRLF/header/invalid-canonical bytes')
    before=len(db.sources);n=parser.call_count;foreign=dict(common,authorization='Bearer '+token(B))
    for path in [prefix(P),prefix(P)+'/'+sid,prefix(P)+'/requests/'+rq,prefix(P)+'/'+sid+'/content']:self.assertEqual((await c.get(path,headers=foreign)).status_code,404)
    self.assertEqual((await c.post(prefix(P),content=RAW,headers=head(actor=B))).status_code,404);self.assertEqual(parser.call_count,n);self.assertEqual(len(db.sources),before)
    self.assertEqual((await upload(c,rq=rq,raw=RAW+b'\n')).status_code,409)
    for bad in ['wrong.txt','../escape.fa']:self.assertEqual((await upload(c,name=bad)).status_code,400)
    bad=head();bad['x-cyomics-file-sha256']='0'*64;self.assertEqual((await c.post(prefix(P),content=RAW,headers=bad)).json(),{'error':{'code':'fasta_checksum_mismatch'}})
    bad=head();bad['x-cyomics-file-format']='proteinGroups_txt';self.assertEqual((await c.post(prefix(P),content=RAW,headers=bad)).status_code,400)
    bad=head();bad['x-cyomics-file-bytes']=str(model.MAX_SOURCE+1);self.assertEqual((await c.post(prefix(P),content=RAW,headers=bad)).status_code,413)
    async def too_large():yield b'x'*(model.MAX_SOURCE+1)
    bad=head();bad['x-cyomics-file-bytes']=str(model.MAX_SOURCE);self.assertEqual((await c.post(prefix(P),content=too_large(),headers=bad)).status_code,413)
    # Fresh app resets only ephemeral rate counters; persistent SQL store remains.
    c,app=client();invalid=b'PRIVATE_SEQUENCE_NO_HEADER';response=await upload(c,raw=invalid);self.assertEqual(response.status_code,422,response.text);self.assertEqual(set(response.json()['error']['issue']),{'code','line','category'});self.assertNotIn(invalid.decode(),response.text)
    self.assertEqual(len(db.sources),before)
    for method,path,body in [('GET',prefix(P)+'?x=1',b''),('GET',prefix(P),b'bad')]:self.assertEqual((await c.request(method,path,content=body,headers=common)).status_code,400)
    self.assertEqual((await c.put(prefix(P),headers=common)).status_code,405);self.assertEqual((await c.get(prefix(P)+'/',headers=common)).status_code,404);self.assertEqual((await c.get(prefix(P).replace('/fasta-sources','/%66asta-sources'),headers=common)).status_code,404)
    forged=dict(common,**{'x-owner-id':A});self.assertEqual((await c.get(prefix(P),headers=forged)).status_code,403)
    groups.append('cross Owner404 before parse; conflict/format/path/query/body/forged-owner/checksum/syntax/25MiB stream/header guards')
    # Race: local parse completes once, then a second writer wins just before this write TX.
    race_db=DB();race_client,race_app=client();race_repo=race_app.state.fasta_private.repository;race_repo.pool=race_db
    race_rq=str(uuid4());original_tx=race_repo.transaction;injected=False
    async def transaction(actor,budget,operation,writing=False):
     nonlocal injected
     if writing and not injected:
      injected=True
      # Same fully parsed candidate is published by the explicit memory SQL race double.
      closure={k:v.cell_contents for k,v in zip(operation.__code__.co_freevars,operation.__closure__)};source=closure['source'];candidate=closure['candidate'];sid2=uuid4();from dataclasses import replace
      winner=replace(candidate,source_id=str(sid2),index_version_id=model.version_key(P,str(sid2),source.sha256,candidate.config_json));created=closure['now'];race_db.sources[(P,race_rq)]=(sid2,UUID(P),UUID(race_rq),source.filename,'protein_fasta',len(RAW),source.sha256,'stored',created,RAW);race_db.indexes[str(sid2)]=(UUID(winner.index_version_id),UUID(P),sid2,len(RAW),source.sha256,winner.config_sha256,winner.index_manifest_sha256,core._json(model.candidate_snapshot(winner)),created)
     return await original_tx(actor,budget,operation,writing)
    race_repo.transaction=transaction;n=parser.call_count;response=await upload(race_client,rq=race_rq);self.assertEqual(response.status_code,200,response.text);self.assertEqual(parser.call_count-n,1);self.assertEqual((len(race_db.sources),len(race_db.indexes)),(1,1))
    groups.append('raced identical request rebinds same-request full fingerprint to persisted IDs; exactly1parse and no new INSERT')
    # Atomicity, COMMIT uncertainty and read-only receipt recovery over original transaction.
    c,app=client();db.index_fail=True;count=len(db.sources);response=await upload(c,project=P3);self.assertEqual(response.status_code,503,response.text);self.assertEqual((len(db.sources),len(db.indexes)),(count,count))
    unknown_rq=str(uuid4());db.unknown_commit=True;response=await upload(c,project=P3,rq=unknown_rq);self.assertEqual(response.status_code,504,response.text);self.assertEqual(response.json()['error']['code'],'fasta_result_unknown');self.assertEqual(len(db.sources),count+1);response=await c.get(prefix(P3)+'/requests/'+unknown_rq,headers=common);self.assertEqual(response.status_code,200,response.text)
    groups.append('source/index INSERT failure rollback together; committed-but-unacknowledged504 no false success; receiptGET recovers truth')
    c,app=client()
    while len([row for row in db.sources.values() if str(row[1])==P])<4:self.assertEqual((await upload(c)).status_code,201)
    response=await upload(c);self.assertEqual((response.status_code,response.json()['error']['code']),(413,'fasta_quota_exceeded'));self.assertEqual((await upload(c,rq=rq)).status_code,200)
    # Owner already has5: fourP plus oneP3. ThreeP2 reaches8 total.
    for _ in range(3):self.assertEqual((await upload(c,project=P2)).status_code,201)
    response=await upload(c,project=P2);self.assertEqual((response.status_code,response.json()['error']['code']),(413,'fasta_quota_exceeded'));self.assertEqual(len(db.sources),8)
    c,app=client();db.count_override=(7,209715200,1,1);self.assertEqual((await upload(c,project=P3)).status_code,413)
    db.count_override=(7,104857600,3,104857600);self.assertEqual((await upload(c,project=P3)).status_code,413);db.count_override=None;self.assertEqual(len(db.sources),8)
    response=await c.get(prefix(P),headers=common);self.assertEqual(response.status_code,200);self.assertEqual(len(response.json()['items']),4);self.assertIsNone(response.json()['nextCursor']);self.assertLess(len(response.content),MAX_JSON)
    groups.append('actor-lock project4/100MiB and Owner8/200MiB limits; existing request bypasses new quota; list<=4')
    route={'type':'http','path':prefix(P),'raw_path':prefix(P).encode(),'method':'POST','query_string':b'','headers':[]};self.assertGreater(entry_budget(route).remaining(),49);route['method']='GET';self.assertGreater(entry_budget(route).remaining(),8)
    self.assertEqual(model.ParseControl().max_cpu_seconds,8.0);self.assertFalse(any(sql.startswith(('UPDATE','DELETE','CREATE','ALTER','DROP')) for sql in db.events));self.assertEqual((no_network.call_count,no_native.call_count,no_async.call_count),(0,0,0))
    print(json.dumps({'target':'PASS_ONE_FINITE_FASTA_HTTP_TARGET','groups':groups,'new_test_methods':1,'real_parser':True,'original_UploadRepository_transaction':True,'Authority':'original via MockTransport','SQL':'explicit memory driver/pool, not actual PG/RLS','actual_DB_R_network_listener_calls':0,'old_suites':0,'limits':{'long_wall':50,'list_wall':9,'parser_CPU':8,'single_bytes':26214400,'project_files':4,'Owner_files':8},'golden_38387_not_rerun':True},sort_keys=True))
   finally:
    for c in clients:await c.aclose()
    for app in apps:app.state.complete_imports.close();await app.state.authority.close()
if __name__=='__main__':unittest.main()
