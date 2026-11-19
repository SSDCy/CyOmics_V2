"""One new timeout-only target, no old target methods/Golden/live DB.
Original transaction/HTTP/Auth with memory SQL and explicitly injected CPU error.
"""
from pathlib import Path
import base64,copy,hashlib,json,socket,subprocess,sys,unittest
from contextlib import ExitStack
from datetime import datetime,timezone
from unittest.mock import patch
from uuid import UUID,uuid4
BACKEND=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BACKEND))
import httpx
import test_fasta_private_http as prior # reusable memory-driver/token helpers only, no old test method execution
from cyomics_projects.app import create_app
from cyomics_projects import fasta_intake_contract as model,fasta_private_repository as repo
from cyomics_projects.settings import Budget
class CpuTimeoutTarget(unittest.IsolatedAsyncioTestCase):
 async def test_only_rebuild_CPU_timeout_routes(self):
  db=prior.DB();raw=prior.RAW;request_id=str(uuid4());source_id=str(uuid4());now=datetime.now(timezone.utc);name='timeout.fa';budget=Budget(50)
  document={'source_id':source_id,'project_id':prior.P,'request_id':request_id,'filename':name,'format':'protein_fasta','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'status':'stored','createdAt':repo._stamp(now)}
  source=model.source_from_json(json.dumps(document).encode(),raw)
  candidate=model.build_candidate(source,expected_project_id=prior.P,expected_source_id=source_id,control=model.ParseControl(checkpoint=budget.check),config_json=model.CONFIG_JSON)
  # Seed one fully valid synthetic immutable pair directly in the explicit memory driver.
  db.sources[(prior.P,request_id)]=(UUID(source_id),UUID(prior.P),UUID(request_id),name,'protein_fasta',len(raw),source.sha256,'stored',now,raw)
  db.indexes[source_id]=(UUID(candidate.index_version_id),UUID(prior.P),UUID(source_id),len(raw),source.sha256,candidate.config_sha256,candidate.index_manifest_sha256,repo._json(model.candidate_snapshot(candidate)),now)
  snapshot=(copy.deepcopy(db.sources),copy.deepcopy(db.indexes));observed=[]
  async def auth(request):
   actor=json.loads(base64.urlsafe_b64decode(request.headers['authorization'].split('.')[1]+'=='))['sub']
   return httpx.Response(200,json={'id':actor,'role':'authenticated','aud':'authenticated','is_anonymous':False,'email':'synthetic@example.invalid','email_confirmed_at':'2026-01-01T00:00:00Z'})
  def cpu_timeout(*args,**kwargs):
   control=kwargs['control'];self.assertIs(type(control),model.ParseControl);self.assertEqual(control.max_cpu_seconds,8.0)
   request_budget=control.checkpoint.__self__;self.assertIs(type(request_budget),Budget);self.assertGreater(request_budget.remaining(),40)
   observed.append({'CPU_limit':control.max_cpu_seconds,'wall_remaining_more_than40':True})
   raise model.IntakeError('fasta_intake_timeout')
  with ExitStack() as stack:
   no_network=stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')));no_native=stack.enter_context(patch.object(subprocess,'Popen',side_effect=AssertionError('native forbidden')));no_async=stack.enter_context(patch('asyncio.create_subprocess_exec',side_effect=AssertionError('native forbidden')))
   injected=stack.enter_context(patch.object(model,'rebuild_and_verify',side_effect=cpu_timeout))
   app=create_app(auth_transport=httpx.MockTransport(auth),pool=db,anon_key_loader=lambda:prior.token(role='anon'),fasta_private_enabled=True)
   c=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:57430')
   base='/api/v1/projects/'+prior.P+'/fasta-sources';headers={'authorization':'Bearer '+prior.token(),'origin':'http://127.0.0.1:4206','sec-fetch-site':'same-origin'};out=[]
   try:
    for kind,method,path in [('request_receipt','GET',base+'/requests/'+request_id),('source','GET',base+'/'+source_id),('content','GET',base+'/'+source_id+'/content'),('idempotent_existing_request','POST',base)]:
     before=injected.call_count
     response=await c.request(method,path,headers=prior.head(raw,request_id,name) if method=='POST' else headers,**({'content':raw} if method=='POST' else {}))
     self.assertEqual(response.status_code,504,response.text);self.assertEqual(response.json(),{'error':{'code':'fasta_intake_timeout'}});self.assertEqual(response.headers['cache-control'],'no-store');self.assertEqual(response.headers['content-type'],'application/json');self.assertNotIn('content-disposition',response.headers);self.assertEqual(injected.call_count-before,1)
     self.assertEqual((db.sources,db.indexes),snapshot);out.append({'route':kind,'status':response.status_code,'error_code':response.json()['error']['code'],'rebuild_calls':1,'source_index_pair_unchanged':True})
    self.assertEqual(injected.call_count,4);self.assertEqual(len(observed),4);self.assertTrue(all(sql.endswith('READ ONLY') for sql in db.events if sql.startswith('BEGIN')));self.assertEqual(sum(sql=='ROLLBACK' for sql in db.events),4);self.assertFalse(any(sql.startswith(('INSERT','UPDATE','DELETE','CREATE','ALTER','DROP')) for sql in db.events));self.assertEqual((no_network.call_count,no_native.call_count,no_async.call_count),(0,0,0))
    print(json.dumps({'target':'PASS_ONE_NEW_REBUILD_CPU_TIMEOUT_TARGET','routes':out,'original_UploadRepository_transaction':True,'wall_budget_not_expired':True,'CPU_limit_unchanged':8,'explicit_injection':'model.IntakeError fasta_intake_timeout at rebuild_and_verify; not an actual8s timed run','SQL':'memory driver/pool only, not real PG/RLS','old_target_methods_run':0,'Golden_runs':0,'actual_DB_R_network_services_calls':0},sort_keys=True))
   finally:
    await c.aclose();app.state.complete_imports.close();await app.state.authority.close()
if __name__=='__main__':unittest.main()
