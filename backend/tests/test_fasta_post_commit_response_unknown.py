"""FQMH01 only: fresh response failures after original write TX commits.
No old suites, source parser rerun, DB, network, native work or listener.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import asyncio,json,types,unittest
from unittest.mock import patch
import httpx
from test_fasta_private_http import A,P,DB,token,head,RAW
from cyomics_projects.app import create_app,entry_budget
from cyomics_projects import fasta_private_http as fa,fasta_quant_mapping_http as mapping
from cyomics_projects.upload_repository import UploadOutcome,UploadRepository
from cyomics_projects.settings import Fault
from starlette.responses import JSONResponse
from uuid import uuid4

class PostCommitResponseTarget(unittest.IsolatedAsyncioTestCase):
 async def test_one_finite_post_commit_response_target(self):
  db=DB();apps=[];cases=[]
  async def auth(request):return httpx.Response(200,json=dict(id=A,role='authenticated',aud='authenticated',is_anonymous=False,email='synthetic@example.invalid',email_confirmed_at='2026-01-01T00:00:00Z'))
  try:
   for module,family,unknown,unavailable in [(mapping,'fasta-quant-mappings','fasta_mapping_result_unknown','fasta_mapping_unavailable'),(fa,'fasta-sources','fasta_result_unknown','fasta_intake_unavailable')]:
    for stage in ('response_start','serialization'):
     for error_type in (OSError,RuntimeError):
      app=create_app(auth_transport=httpx.MockTransport(auth),pool=db,anon_key_loader=lambda:token(role='anon'),fasta_private_enabled=True,fasta_mapping_enabled=True);apps.append(app)
      handler=app.state.fasta_mapping if module is mapping else app.state.fasta_private
      repo=handler.repository;observed=[]
      async def save(actor,project,*args,**kwargs):
       budget=args[-1]
       async def operation(q):
        await repo.project(q,actor,project)
        return 201,types.SimpleNamespace(metadata={'stored':True},dto=lambda:{'stored':True})
       result=await UploadRepository.transaction(repo,actor,budget,operation,writing=True)
       self.assertTrue(budget.create.commit_started and budget.create.committed);self.assertFalse(budget.create.rollback_confirmed);self.assertGreater(budget.remaining(),1)
       observed.append(budget);return result
      repo.save=save
      if module is mapping:
       async def lookup(*args):return None
       repo.lookup=lookup
       raw=json.dumps(dict(dataset_version_id=str(uuid4()),fasta_source_id=str(uuid4()),fasta_index_version_id=str(uuid4()))).encode()
       headers={'authorization':'Bearer '+token(),'origin':'http://127.0.0.1:4206','sec-fetch-site':'same-origin','x-cyomics-request':'1','content-type':'application/json'}
      else:raw=RAW;headers=head()
      path='/api/v1/projects/'+P+'/'+family
      headers['host']='127.0.0.1:57430';headers['content-length']=str(len(raw))
      scope=dict(type='http',asgi={'version':'3.0'},http_version='1.1',method='POST',path=path,raw_path=path.encode(),query_string=b'',root_path='',headers=[(k.encode(),v.encode()) for k,v in headers.items()])
      events=[];injected=[];received=False
      async def receive():
       nonlocal received
       if not received:received=True;return dict(type='http.request',body=raw,more_body=False)
       await asyncio.Future()
      async def send(event):
       if stage=='response_start' and event['type']=='http.response.start' and not injected:
        injected.append(True);raise error_type('synthetic send failure')
       events.append(event)
      def response(*args,**kwargs):
       if stage=='serialization' and not injected:
        injected.append(True);raise error_type('synthetic serialization failure')
       return JSONResponse(*args,**kwargs)
      with patch.object(module,'JSONResponse',side_effect=response):await handler(scope,receive,send)
      self.assertEqual(len(observed),1);self.assertEqual(len(injected),1)
      self.assertEqual(events[0]['status'],504,events)
      self.assertEqual(json.loads(b''.join(e.get('body',b'') for e in events)),{'error':{'code':unknown}})
      self.assertTrue(observed[0].create.committed);cases.append(family+':'+stage+':'+error_type.__name__)
    # The translation's new priority is explicitly POST only and never overrides
    # a confirmed rollback. Ordinary errors still get the original503 semantics.
    for method,started,committed,rolled,expected in [('POST',False,False,False,503),('POST',True,False,True,503),('POST',True,True,True,503),('GET',True,True,False,503),('POST',True,False,False,504)]:
     path='/api/v1/projects/'+P+'/'+family;budget=entry_budget(dict(path=path,raw_path=path.encode(),method='POST',query_string=b'',headers=[]));budget.create=UploadOutcome()
     budget.create.commit_started=started;budget.create.committed=committed;budget.create.rollback_confirmed=rolled
     for error in (OSError('synthetic'),RuntimeError('synthetic')):
      result=module.translated(error,budget,method=method);self.assertEqual(result.status_code,expected)
      self.assertEqual(json.loads(result.body)['error']['code'],unknown if expected==504 else unavailable)
    # Existing timeout/known Fault codes and GET timeout remain unchanged.
    budget=entry_budget(dict(path=path,raw_path=path.encode(),method='POST',query_string=b'',headers=[]));budget.create=UploadOutcome()
    self.assertEqual(module.translated(Fault(403,'auth_origin_rejected'),budget,method='POST').status_code,403)
    self.assertEqual(module.translated(TimeoutError(),budget,method='GET').status_code,504)
   self.assertEqual(len(cases),8)
   self.assertTrue(any(s=='COMMIT' for s in db.events));self.assertFalse(any(s.startswith(('INSERT','UPDATE','DELETE','CREATE','ALTER','DROP')) for s in db.events))
   print(json.dumps(dict(target='PASS_FQMH01_POST_COMMIT_RESPONSE_UNKNOWN',HTTP_cases=cases,translation_matrix_cases=20,original_write_transaction=True,Authority='original via MockTransport',SQL='explicit memory driver only',real_DB_native_listener=0,old_suite_runs=0),sort_keys=True))
  finally:
   for app in apps:app.state.complete_imports.close();await app.state.authority.close()
if __name__=='__main__':unittest.main()
