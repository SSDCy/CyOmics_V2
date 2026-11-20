"""One new synthetic ASGI target. Original Authority.verify/header/Budget/helper
bodies execute; explicit HTTPX stream and five Repo read/app-pool doubles only.
No real Auth server, signature verification, PG/RLS, real FASTA or9s live proof.
"""
import asyncio,base64,copy,csv,hashlib,importlib.util,io,json,os,socket,subprocess,sys,time,types,unittest
from enum import IntEnum
from pathlib import Path
from unittest.mock import patch
BACKEND=Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:sys.path.insert(0,str(BACKEND))
class TS(IntEnum):IDLE=0;ACTIVE=1;INTRANS=2;INERROR=3;UNKNOWN=4
pq=types.ModuleType('psycopg.pq');pq.TransactionStatus=TS
driver=types.ModuleType('psycopg');driver.AsyncConnection=object;driver.pq=pq
sys.modules['psycopg']=driver;sys.modules['psycopg.pq']=pq
auth_calls=[]
class Stream:
 status_code=200
 def __init__(self,subject):self.subject=subject
 async def __aenter__(self):return self
 async def __aexit__(self,*args):pass
 async def aiter_bytes(self):
  yield json.dumps({'id':self.subject,'aud':'authenticated','role':'authenticated','is_anonymous':False,'email':'synthetic@example.invalid','email_confirmed_at':'2025-01-01T00:00:00Z'}).encode()
class Client:
 def __init__(self,*args,**kwargs):self.is_closed=False
 async def aclose(self):self.is_closed=True
 def stream(self,method,url,headers):
  from cyomics_projects.settings import AUTH_USER_URL
  assert (method,url)==('GET',AUTH_USER_URL)
  claim=json.loads(base64.urlsafe_b64decode(headers['Authorization'][7:].split('.')[1]+'=='));auth_calls.append(claim['sub']);return Stream(claim['sub'])
class Transport:
 def __init__(self,*args,**kwargs):pass
httpx=types.ModuleType('httpx');httpx.AsyncClient=Client;httpx.AsyncHTTPTransport=Transport;sys.modules['httpx']=httpx
from cyomics_projects import dep_fasta_handoff_http as api,dep_fasta_handoff as core,preprocess_repository
from cyomics_projects.identity import Authority
from cyomics_projects.settings import Fault,EXPECTED_ISSUER
# Existing source fixture definitions in this SAME checkout; no audit dependency,
# old main/cap targets, real experiment rows or scientific targets are executed.
ORIGINAL_BACKEND=Path(preprocess_repository.__file__).resolve().parents[1]
if str(ORIGINAL_BACKEND/'tests') not in sys.path:sys.path.insert(0,str(ORIGINAL_BACKEND/'tests'))
from test_dep_fasta_handoff import Fixture,ACTOR,OTHER,PROJECT,DERIVED,MAPPING

def token(subject=ACTOR,*,expired=False,anon=False):
 enc=lambda v:base64.urlsafe_b64encode(json.dumps(v).encode()).decode().rstrip('=')
 now=time.time();claims={'role':'anon'} if anon else {'sub':subject,'role':'authenticated','aud':'authenticated','iss':EXPECTED_ISSUER,'iat':now-5,'exp':now-1 if expired else now+60,'is_anonymous':False}
 return '.'.join((enc({'alg':'HS256'}),enc(claims),enc('synthetic-only-not-a-real-signature')))

class OwnerHandoffHTTPFinite(unittest.IsolatedAsyncioTestCase):
 async def test_owner_reads_strict_routes_expected_hash_and_late_fences(self):
  # Windows loop's internal wakeup pipe is created by unittest BEFORE this guard.
  effects=dict(network=0,subprocess=0,write=0,credential=0)
  def guard(event,args):
   category=None
   if event.startswith('socket.'):category='network'
   elif event in ('subprocess.Popen','os.system','os.exec','os.spawn','os.posix_spawn'):category='subprocess'
   elif event=='open':
    path,mode,flags=args
    if isinstance(flags,int) and flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):category='write'
    if any(s in str(path).lower().replace('\\','/') for s in ('/.ssh/','runtime/business-runtime.json','credential')):category='credential'
   if category:effects[category]+=1;raise AssertionError('forbidden:'+category)
  sys.addaudithook(guard)
  fixture=Fixture();authority=Authority(None,lambda:token(anon=True));app=types.SimpleNamespace(state=types.SimpleNamespace(authority=authority));handler=api.DEPFastaHandoffHTTP(app,enabled=True)
  original_class=fixture.readers.classifications.read_version;original_mapping=fixture.readers.mappings.read
  async def classify(subject,project,derived,budget):
   if derived!=DERIVED:raise Fault(404,'de_classification_version_not_found')
   return await original_class(subject,project,derived,budget)
  async def mapping(subject,project,identifier,budget):
   if identifier!=MAPPING:raise Fault(404,'fasta_mapping_not_found')
   return await original_mapping(subject,project,identifier,budget)
  fixture.readers.classifications.read_version=classify;fixture.readers.mappings.read=mapping
  base='/api/v1/projects/'+PROJECT+'/differential-classifications/'+DERIVED+'/fasta-handoff';query='mapping_version_id='+MAPPING
  async def request(suffix='',*,query_text=query,method='GET',subject=ACTOR,authenticated=True,raw_override=None,path_override=None,root_path='',body=b'',extra=(),disconnect_on=None,expired=False,use=handler):
   path=base+suffix;raw=path.encode() if raw_override is None else raw_override
   headers=[(b'host',b'127.0.0.1:57430'),(b'origin',b'http://127.0.0.1:4206'),(b'sec-fetch-site',b'same-origin')]
   if authenticated:headers.append((b'authorization',('Bearer '+token(subject,expired=expired)).encode()))
   headers.extend(extra)
   scope={'type':'http','method':method,'path':path if path_override is None else path_override,'raw_path':raw,'root_path':root_path,'query_string':query_text.encode(),'headers':headers,'scheme':'http','server':('127.0.0.1',57430)}
   first=True;never=asyncio.Event();messages=[]
   async def receive():
    nonlocal first
    if first:first=False;return {'type':'http.request','body':body,'more_body':False}
    if disconnect_on is not None:await disconnect_on.wait();return {'type':'http.disconnect'}
    await never.wait()
   async def send(message):messages.append(message)
   await use(scope,receive,send)
   starts=[x for x in messages if x['type']=='http.response.start'];payload=b''.join(x.get('body',b'') for x in messages if x['type']=='http.response.body')
   return (starts[0]['status'] if starts else None,dict(starts[0]['headers']) if starts else {},payload,messages)
  def expect(response,status,code=None):
   self.assertEqual(response[0],status)
   if code:self.assertEqual(json.loads(response[2]),{'error':{'code':code}})
  with patch.object(core.OwnerFastaHandoff,'_readers',return_value=fixture.readers):
   try:
    with self.subTest('defaultoff_and_anonymous'):
     before=len(auth_calls);expect(await request(use=api.DEPFastaHandoffHTTP()),503,'dep_fasta_unavailable');self.assertEqual(len(auth_calls),before)
     expect(await request(authenticated=False),401,'unauthenticated');self.assertEqual(fixture.log,[])
    with self.subTest('descriptor_and_two_original_exports'):
     meta=await request();expect(meta,200);desc=json.loads(meta[2]);self.assertEqual(desc['counts']['query_records'],5);self.assertFalse(desc['BLAST_executed']);self.assertFalse(desc['annotation_ready'])
     self.assertEqual(meta[1][b'content-type'],b'application/json');self.assertEqual(meta[1][b'cache-control'],b'no-store')
     self.assertLessEqual(len(meta[2]),65536);self.assertEqual(meta[1][b'x-cyomics-file-sha256'].decode(),api.sha(meta[2]))
     for item in desc['outputs']:
      response=await request('/artifacts/'+item['name'],query_text=query+'&expected_sha256='+item['sha256']);expect(response,200)
      self.assertEqual(response[1][b'content-type'].decode(),item['media_type']);self.assertEqual(response[1][b'content-disposition'].decode(),'attachment; filename="'+item['name']+'"')
      self.assertEqual(response[1][b'content-length'].decode(),str(len(response[2])));self.assertEqual(response[1][b'x-cyomics-file-sha256'].decode(),api.sha(response[2]));self.assertEqual(api.sha(response[2]),item['sha256'])
      self.assertEqual(response[1][b'x-content-type-options'],b'nosniff');self.assertEqual(response[1][b'referrer-policy'],b'no-referrer')
      if item['name']=='queries.fasta':self.assertEqual(response[2].count(b'\nACD\n'),4)
      else:self.assertEqual(len(list(csv.DictReader(io.StringIO(response[2].decode(),newline=''),delimiter='\t'))),8)
     self.assertEqual(fixture.log.count('classification_owner'),3);self.assertEqual(fixture.log.count('fasta_owner_verified'),3)
    with self.subTest('fresh_foreign_and_expected_SHA_conflict'):
     fixture.log=[];expect(await request(subject=OTHER),404,'not_found');self.assertEqual(fixture.log,['classification_owner']);self.assertEqual(auth_calls[-1],OTHER)
     expect(await request('/artifacts/queries.fasta',query_text=query+'&expected_sha256='+'0'*64),409,'dep_fasta_output_changed')
    with self.subTest('strict_key_UUID_rawpath_method_and_identity_input'):
     for bad in ['',query+'&'+query,query+'&verified=true',query.replace(MAPPING,MAPPING.upper().replace('0','A',1)),query+'&expected_sha256='+'0'*64]:
      expect(await request(query_text=bad),400,'invalid_dep_fasta_request')
     expect(await request('/artifacts/queries.fasta'),400,'invalid_dep_fasta_request')
     expect(await request('/artifacts/queries.fasta',query_text=query+'&expected_sha256='+'A'*64),400,'invalid_dep_fasta_request')
     for method in ('POST','HEAD'):expect(await request(method=method),405,'method_not_allowed')
     expect(await request(raw_override=base.replace('fasta-handoff','%66asta-handoff').encode()),404,'not_found')
     expect(await request(path_override=base+'/'),404,'not_found');expect(await request(root_path='/foreign'),404,'not_found')
     expect(await request(raw_override=base.replace('differential-classifications','derived-classifications').encode(),path_override=base.replace('differential-classifications','derived-classifications')),404,'not_found')
     expect(await request(extra=[(b'x-owner-id',ACTOR.encode())]),403,'auth_origin_rejected');expect(await request(body=b'notempty'),400,'invalid_dep_fasta_request')
    with self.subTest('original_expiry_and_short_budget_no_late_success'):
     expect(await request(expired=True),401,'unauthenticated')
     prior=fixture.readers.results.read
     async def slow(*args,**kwargs):await asyncio.sleep(.03);return await prior(*args,**kwargs)
     fixture.readers.results.read=slow
     ms=str(int(time.time()*1000)+1000).encode();expect(await request(extra=[(b'x-cyomics-remaining-ms',b'1'),(b'x-cyomics-deadline-unix-ms',ms)]),504,'dep_fasta_read_timeout')
     fixture.readers.results.read=prior
    with self.subTest('disconnect_cancels_only_current_worker'):
     started=asyncio.Event();cancelled=asyncio.Event();prior=fixture.readers.results.read
     async def held(*args,**kwargs):
      started.set()
      try:await asyncio.sleep(10)
      finally:cancelled.set()
     fixture.readers.results.read=held;response=await request(disconnect_on=started);self.assertIsNone(response[0]);await asyncio.sleep(0);self.assertTrue(cancelled.is_set());fixture.readers.results.read=prior
    with self.subTest('original_internal_timeout_and_private_error_sanitization'):
     prior=fixture.readers.mappings.read
     async def timeout(*args,**kwargs):raise TimeoutError('private path must not leak')
     fixture.readers.mappings.read=timeout;expect(await request(),504,'dep_fasta_read_timeout')
     async def private(*args,**kwargs):raise RuntimeError('private path must not leak')
     fixture.readers.mappings.read=private;res=await request();expect(res,503,'dep_fasta_unavailable');self.assertNotIn(b'private path',res[2]);fixture.readers.mappings.read=prior
    with self.subTest('zero_queries_and_large_TSV_direct_handler_framing'):
     # Pure output-boundary control,not original core/native computation.
     original=handler.helper.assemble
     small=core.Bundle(meta[2],b'',b'header\n')
     empty=copy.deepcopy(desc);empty['status']='no_query_sequences';empty['counts']={k:0 for k in empty['counts']};empty['outputs']=[dict(name='membership.tsv',bytes=7,sha256=api.sha(b'header\n'),media_type=api.MEDIA['membership.tsv']),dict(name='queries.fasta',bytes=0,sha256=api.sha(b''),media_type=api.MEDIA['queries.fasta'])]
     async def empty_bundle(*args,**kwargs):return core.Bundle(core.canonical(empty)+b'\n',b'',b'header\n')
     handler.helper.assemble=empty_bundle
     z=await request('/artifacts/queries.fasta',query_text=query+'&expected_sha256='+api.sha(b''));expect(z,200);self.assertEqual(z[2],b'');self.assertTrue(any(x['type']=='http.response.body' and x.get('more_body') is False for x in z[3]))
     data=b'synthetic-only\n'*85000;big=copy.deepcopy(empty);big['outputs'][0].update(bytes=len(data),sha256=api.sha(data))
     async def big_bundle(*args,**kwargs):return core.Bundle(core.canonical(big)+b'\n',b'',data)
     handler.helper.assemble=big_bundle
     large=await request('/artifacts/membership.tsv',query_text=query+'&expected_sha256='+api.sha(data));expect(large,200);self.assertGreater(len(large[2]),1048576);self.assertEqual(large[2],data)
     with patch.dict(api.CAP,{'membership.tsv':1}):expect(await request(),413,'dep_fasta_too_large')
     handler.helper.assemble=original
   finally:await authority.close()
  self.assertTrue(authority._client.is_closed);self.assertEqual(effects,dict(network=0,subprocess=0,write=0,credential=0))
  print(json.dumps({'target':'ONE_NEW_SYNTHETIC_ASGI_COMPOSITION','guards':effects,'original_Authority_verify':True,'simulated_authoritative_user_calls':len(auth_calls),'actual_AuthHTTP':False,'actual_PG_RLS_app_pool':False,'native_R':0,'BLAST':0,'old_target_runs':0,'original_core_helper_with_explicit_five_Repo_doubles':True,'real_Fasta':False,'HTTP_9s_actual_performance':False}))

if __name__=='__main__':unittest.main(verbosity=2)
