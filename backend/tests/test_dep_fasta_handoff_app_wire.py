"""Synthetic app-route seam only: actual app/Authority/handler/repository types.
Auth is MockTransport, helper assembly is a named synthetic double; no actual
Auth HTTP, DB/RLS, native/science, stored-artifact or live-preview proof.
"""
from pathlib import Path
import base64,hashlib,json,socket,subprocess,sys,time,unittest
from unittest.mock import patch
from contextlib import ExitStack
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from cyomics_projects import app as product
from cyomics_projects.repository import FixedPool
from cyomics_projects.preprocess_repository import PreprocessRepository
from cyomics_projects.dep_fasta_handoff import OwnerFastaHandoff,Bundle,Config
from cyomics_projects.dep_fasta_handoff_http import DEPFastaHandoffHTTP,MEDIA
from cyomics_projects.settings import EXPECTED_ISSUER,Fault
A='11111111-1111-4111-8111-111111111111'
B='22222222-2222-4222-8222-222222222222'
P='33333333-3333-4333-8333-333333333333'
D='44444444-4444-4444-8444-444444444444'
M='55555555-5555-4555-8555-555555555555'
PATH=f'/api/v1/projects/{P}/differential-classifications/{D}/fasta-handoff'
def token(actor=A,role='authenticated'):
 enc=lambda x:base64.urlsafe_b64encode(json.dumps(x).encode()).rstrip(b'=').decode()
 now=int(time.time())
 return '.'.join(map(enc,({'alg':'HS256'},{'sub':actor,'role':role,'iss':EXPECTED_ISSUER,'aud':'authenticated','iat':now-2,'exp':now+120,'is_anonymous':False},'synthetic')))
def headers(actor=A):
 return {'authorization':'Bearer '+token(actor),'origin':'http://127.0.0.1:4206','sec-fetch-site':'same-origin'}
class AppWireTarget(unittest.IsolatedAsyncioTestCase):
 async def test_one_synthetic_app_route_target(self):
  apps=[];auth_calls=[];helper_calls=[]
  async def auth(request):
   actor=json.loads(base64.urlsafe_b64decode(request.headers['authorization'].split('.')[1]+'=='))['sub'];auth_calls.append(actor)
   return httpx.Response(200,json={'id':actor,'role':'authenticated','aud':'authenticated','is_anonymous':False,'email':'synthetic@example.invalid','email_confirmed_at':'2026-01-01T00:00:00Z'})
  pool=FixedPool()
  def make(**overrides):
   kw={'auth_transport':httpx.MockTransport(auth),'pool':pool,'anon_key_loader':lambda:token(role='anon')}
   kw.update(overrides);a=product.create_app(**kw);apps.append(a);return a
  def enabled():
   return {'fasta_handoff_enabled':True,'preprocessing_repository':PreprocessRepository(pool,enabled=True),
     'differential_results_enabled':True,'classification_results_enabled':True,'fasta_private_enabled':True,'fasta_mapping_enabled':True}
  async def synthetic_assemble(helper,subject,project,derived,mapping,budget):
   self.assertIs(helper.app,active);helper._readers();helper_calls.append((subject,project,derived,mapping))
   if subject!=A:raise Fault(404,'not_found') # explicit foreign-owner synthetic refusal, not RLS proof
   bodies={'membership.tsv':b'row\tmember\n1\tp1\n','queries.fasta':b'>p1\nACD\n'}
   descriptor={'contract':'dep_fasta_handoff_v0.1','created_by':subject,'project_id':project,
    'classification':{'derived_version_id':derived},'mapping':{'mapping_version_id':mapping},
    'outputs':[{'name':n,'media_type':MEDIA[n],'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()} for n,b in bodies.items()]}
   return Bundle(json.dumps(descriptor).encode(),bodies['queries.fasta'],bodies['membership.tsv'])
  with ExitStack() as stack:
   network=stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')))
   native=stack.enter_context(patch.object(subprocess,'Popen',side_effect=AssertionError('native forbidden')))
   db=stack.enter_context(patch.object(FixedPool,'getconn',side_effect=AssertionError('DB forbidden')))
   stack.enter_context(patch('asyncio.create_subprocess_exec',side_effect=AssertionError('native forbidden')))
   stack.enter_context(patch.object(OwnerFastaHandoff,'assemble',synthetic_assemble))
   try:
    off=make();self.assertFalse(off.state.fasta_handoff.enabled);self.assertIsNone(off.state.fasta_handoff.helper)
    self.assertIsNone(off.state.fasta_handoff.authority);self.assertIsNone(off.state.preprocessing_jobs.repository)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=off),base_url='http://127.0.0.1:57430') as c:
     r=await c.get(PATH,params={'mapping_version_id':M},headers=headers());self.assertEqual(r.status_code,503,r.text)
    self.assertEqual((auth_calls,helper_calls,db.call_count),([],[],0))
    for bad in (None,0,1,'true'):
     with self.assertRaises(Exception):make(fasta_handoff_enabled=bad)
    for missing in ('preprocessing_repository','differential_results_enabled','classification_results_enabled','fasta_private_enabled','fasta_mapping_enabled'):
     kw=enabled();kw[missing]=None if missing=='preprocessing_repository' else False
     with self.assertRaises(product.APIAssemblyRefused):make(**kw)
    for repo in (PreprocessRepository(pool),PreprocessRepository(FixedPool(),enabled=True)):
     kw=enabled();kw['preprocessing_repository']=repo
     with self.assertRaises(product.APIAssemblyRefused):make(**kw)
    kw=enabled();kw['pool']=object()
    with self.assertRaises(product.APIAssemblyRefused):make(**kw)
    self.assertEqual((auth_calls,helper_calls,db.call_count),([],[],0))
    active=make(**enabled());h=active.state.fasta_handoff
    self.assertIs(type(h),DEPFastaHandoffHTTP);self.assertIs(h.app,active);self.assertIs(h.authority,active.state.authority)
    self.assertIs(type(h.helper),OwnerFastaHandoff);self.assertEqual(h.helper.config,Config(True));refs=h.helper._readers()
    self.assertIs(refs.processed.pool,pool);self.assertIs(refs.classifications.owner.pool,pool);self.assertIs(refs.results.owner.pool,pool)
    self.assertIs(refs.mappings.pool,pool);self.assertIs(refs.fasta.pool,pool)
    self.assertIsNone(active.state.differential_input);self.assertIsNone(active.state.differential_jobs)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=active),base_url='http://127.0.0.1:57430') as c:
     with patch.object(product,'owns_classification',side_effect=AssertionError('legacy broad router swallowed handoff')):
      r=await c.get(PATH,params={'mapping_version_id':M},headers=headers());self.assertEqual(r.status_code,200,r.text);descriptor=r.json()
      for item in descriptor['outputs']:
       r=await c.get(PATH+'/artifacts/'+item['name'],params={'mapping_version_id':M,'expected_sha256':item['sha256']},headers=headers())
       self.assertEqual(r.status_code,200,r.text);self.assertEqual(hashlib.sha256(r.content).hexdigest(),item['sha256']);self.assertIn('attachment',r.headers['content-disposition'])
      n=len(helper_calls)
      for suffix,method,query,status in (('/','GET',{'mapping_version_id':M},404),('/artifacts/other','GET',{'mapping_version_id':M},404),('','POST',{'mapping_version_id':M},405),('','GET',{},400)):
       r=await c.request(method,PATH+suffix,params=query,headers=headers());self.assertEqual(r.status_code,status,r.text)
      self.assertEqual(len(helper_calls),n)
      r=await c.get(PATH,params={'mapping_version_id':M},headers=headers(B));self.assertEqual(r.status_code,404,r.text);self.assertNotIn('outputs',r.text)
      r=await c.get(PATH,params={'mapping_version_id':M});self.assertEqual(r.status_code,401,r.text)
     for route in ('/unrelated',f'/api/v1/projects/{P}/differential-classifications/{D}/unknown'):
      before=len(helper_calls)
      with patch.object(product,'owns_classification',wraps=product.owns_classification) as legacy:
       r=await c.get(route,headers=headers());self.assertEqual(r.status_code,404,r.text);self.assertGreater(legacy.call_count,0)
      self.assertEqual(len(helper_calls),before)
    self.assertIn(A,auth_calls);self.assertIn(B,auth_calls)
    self.assertEqual((network.call_count,native.call_count,db.call_count),(0,0,0))
   finally:
    for a in apps:await a.state.authority.close()
    pool.close()
if __name__=='__main__':unittest.main()
