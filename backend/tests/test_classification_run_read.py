"""One finite in-memory combination case; no actual HTTP/SQL/Auth/R or old suite."""
import asyncio,base64,copy,json,socket,subprocess,sys,time,unittest
from contextlib import ExitStack
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch

BACKEND=Path(__file__).resolve().parents[1]
BASELINE=Path('/home/user01/Cy_Omics_v2.0/source/CyOmics_V2-reviewed-5b4c7c1-bundle/backend')
sys.path.insert(0,str(BACKEND if (BACKEND/'cyomics_projects/__init__.py').is_file() else BASELINE))
import cyomics_projects
cyomics_projects.__path__.insert(0,str(BACKEND/'cyomics_projects'))
import httpx
from cyomics_projects.app import create_app
from cyomics_projects import de_classification_jobs as jobs,de_classification_once as once
from cyomics_projects import de_classification_repository as saved,de_classification_contract as contract
from cyomics_projects import de_classification_run_read as view
from cyomics_projects.settings import Fault,EXPECTED_ISSUER

A='a8e4f099-4797-4d82-a19b-90ba90160b06';B='22222222-2222-4222-8222-222222222222'
P='47278eaf-c156-4b66-b60a-287ca4b23805';J='e77fe0f7-b525-447e-b5c6-e0130d21957f'
Q='5e1b5e44-415e-434b-84d3-5c6c20f71f65';R='82db1136-2263-4781-9357-feadcdf11a27'
ATT='51c2bd7b-fe61-4ef3-baa3-0f0e72ad9e89';E='33333333-3333-4333-8333-333333333333'
V='44444444-4444-4444-8444-444444444444';M='55555555-5555-4555-8555-555555555555'
T0='2026-01-01T00:00:00.000000Z';T1='2026-01-01T00:00:01.000000Z';T2='2026-01-01T00:00:02.000000Z'
PARAM={'schema_version':'de_classification_v0.1','input_scale':'log2','adjusted_p_max':'0.05','absolute_log2fc_min':'1'}

def token(subject=A,role='authenticated'):
    enc=lambda x:base64.urlsafe_b64encode(json.dumps(x).encode()).decode().rstrip('=')
    now=int(time.time())
    return '.'.join((enc({'alg':'HS256'}),enc({'sub':subject,'role':role,'iss':EXPECTED_ISSUER,'aud':'authenticated',
        'iat':now-5,'exp':now+120,'is_anonymous':False}),enc('offline-only')))

def fixtures():
    binding={'owner_id':A,'project_id':P,'source_result_id':R,'source_manifest_sha256':'a'*64}
    parameter_sha=contract.sha(jobs.parameter_input(PARAM).body)
    job={'schemaVersion':1,'contract':jobs.CONTRACT,'classification_job_id':J,'project_id':P,'request_id':Q,'created_by':A,
        'source_result_id':R,'source_manifest_sha256':'a'*64,'source_binding_sha256':saved.digest(binding),
        'parameters':PARAM,'parameters_sha256':parameter_sha,'core':saved.core(),'created_at':T0,'status':'queued','observation':'confirmed'}
    stored=jobs.StoredJob(saved.canonical(job),'{}','f'*64)
    artifacts=[{'name':n,'bytes':1,'sha256':'b'*64} for n in contract.OUTPUTS]
    # Synthetic DTO only: these are not scientific bodies or native authority.
    meta={'schemaVersion':1,'contract':saved.CONTRACT,'derived_version_id':V,'request_id':Q,'project_id':P,'created_by':A,
        'source_result_id':R,'source_manifest_sha256':'a'*64,'source_binding_sha256':saved.digest(binding),'source_binding':binding,
        'parameters':PARAM,'parameters_sha256':parameter_sha,'core':saved.core(),'status':'CLASSIFIED','DE_members_evaluated':True,
        'counts':{'Up':1,'Down':1,'NS':3,'not_testable':2,'not_evaluated':0,'selected_DE_rows':2},'artifacts':artifacts,
        'manifest_sha256':saved.digest(artifacts),'member_set':{'member_set_id':M,'derived_version_id':V,'selected_artifact_sha256':'b'*64,'member_count':2},
        'created_at':T1,'source_analysis_ready':False}
    saved.validate_dto(meta)
    run={'classification_job_id':J,'attempt_id':ATT,'owner_id':A,'project_id':P,'request_id':Q,'epoch_id':E,
        'scope_sha256':'c'*64,'request_payload_sha256':'f'*64,'configuration_sha256':'d'*64,'status':'claimed','observation':'confirmed',
        'claimed_at':datetime.fromisoformat(T1.replace('Z','+00:00')),'finished_at':None,'derived_version_id':None,'error_code':None,
        'failure_facts_json':None,'failure_facts_sha256':None}
    return job,stored,meta,run

class RunReadCombination(unittest.IsolatedAsyncioTestCase):
    async def test_one_finite_combination(self):
        job,stored,meta,claimed=fixtures();state={'kind':'queued','run':None,'result':None};calls=[];auth_calls=[]
        async def auth(request):
            subject=json.loads(base64.urlsafe_b64decode(request.headers['authorization'].split('.')[1]+'=='))['sub']
            auth_calls.append(subject)
            return httpx.Response(200,json={'id':subject,'aud':'authenticated','role':'authenticated','is_anonymous':False,
                'email':'offline@example.invalid','email_confirmed_at':'2025-01-01T00:00:00Z'})
        async def transaction(repo,subject,budget,operation,*,writing=False):
            calls.append((subject,writing));self.assertFalse(writing)
            if state['kind']=='unknown':raise Fault(504,'de_classification_once_unknown')
            async def query(sql,args,**kw):
                self.assertFalse(kw.get('write',False))
                self.assertTrue(sql.startswith('SELECT project_id FROM cyomics_app.projects WHERE owner_id='))
                return (P,) if subject==A and tuple(map(str,args))==(A,P) else None
            return await operation(query)
        async def exact_job(repo,query,subject,project,**kwargs):
            self.assertEqual(kwargs,{'request_id':Q});return stored,{}
        async def exact_run(repo,query,subject,project,job_id):return copy.deepcopy(state['run'])
        async def exact_result(repo,query,subject,project,job,row):
            if state['result'] is None:return None
            return state['result'],{'native_receipt_json':saved.canonical({'job_id':J,'attempt_id':ATT,'epoch_id':E,'configuration_sha256':'d'*64})}
        pool=object();app=create_app(auth_transport=httpx.MockTransport(auth),pool=pool,anon_key_loader=lambda:token(role='anon'),classification_jobs_enabled=True)
        headers={'authorization':'Bearer '+token(),'origin':'http://127.0.0.1:4206','sec-fetch-site':'same-origin','x-cyomics-request':'1'}
        path='/api/v1/projects/'+P+'/classification-jobs?request_id='+Q
        passed=[]
        try:
            with ExitStack() as stack:
                network=stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')))
                native=stack.enter_context(patch.object(subprocess,'Popen',side_effect=AssertionError('native forbidden')))
                stack.enter_context(patch.object(once.OnceRepository,'_transaction',transaction))
                stack.enter_context(patch.object(once.OnceRepository,'_job',exact_job))
                stack.enter_context(patch.object(once.OnceRepository,'_run',exact_run))
                stack.enter_context(patch.object(once.OnceRepository,'_result',exact_result))
                enqueue=stack.enter_context(patch.object(jobs.ClassificationAdmissionRepository,'enqueue',side_effect=AssertionError('POST write forbidden')))
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:57430') as client:
                    async def check(name,expected_state,run,result,error=None):
                        state.update(kind=name,run=run,result=result)
                        response=await client.get(path+'&view=run',headers=headers)
                        self.assertEqual(response.status_code,200,response.text);value=response.json()
                        self.assertEqual(set(value),view.KEYS);self.assertEqual(len(value),19)
                        self.assertEqual((value['state'],value['observation'],value['error_code']),(expected_state,'confirmed',error))
                        self.assertEqual(value['attempt_id'],None if run is None else ATT)
                        self.assertEqual(value['derived_version_id'],V if result is not None else None)
                        self.assertEqual(value['scientific_status'],result['status'] if result is not None else None)
                        self.assertEqual(value['finished_at'],T1 if run is None and result is not None else T2 if run is not None and run['status']=='finished' else None)
                        self.assertLessEqual(len(response.content),32768);self.assertEqual(response.headers['cache-control'],'no-store')
                        for private in ('epoch_id','scope_sha256','configuration_sha256','failure_facts','native_receipt','reason'):
                            self.assertNotIn(private,value)
                        passed.append(name)
                    await check('queued','queued',None,None)
                    await check('claimed','claimed',claimed,None)
                    await check('reconciliation_pending','reconciliation_pending',claimed,meta)
                    succeeded=dict(claimed,status='finished',finished_at=datetime.fromisoformat(T2.replace('Z','+00:00')),derived_version_id=V)
                    await check('finished_success','finished',succeeded,meta)
                    await check('finished_existing_result_no_run','finished',None,meta)
                    for status in ('BLOCKED_SOURCE_NO_TESTABLE_ROWS','BLOCKED_SOURCE_REVIEW_REQUIRED'):
                        blocked=copy.deepcopy(meta);blocked.update(status=status,DE_members_evaluated=False,member_set=None,
                            counts={'Up':None,'Down':None,'NS':None,'not_testable':2,'not_evaluated':7,'selected_DE_rows':None})
                        await check('finished_'+status,'finished',succeeded,blocked)
                    for error in ('runner_failed','input_integrity_failed'):
                        failed=dict(succeeded,derived_version_id=None,error_code=error,failure_facts_json='{"private_cleanup_proof":true}',failure_facts_sha256='e'*64)
                        await check('actual_failure_shape_'+error,'finished',failed,None,error)
                    state.update(kind='claimed',run=claimed,result=None)
                    foreign=await client.get(path+'&view=run',headers=dict(headers,authorization='Bearer '+token(B)))
                    self.assertEqual(foreign.status_code,404);self.assertEqual(foreign.json(),{'error':{'code':'de_classification_job_not_found'}});passed.append('original_Owner_denial')
                    invalid=await client.get(path+'&view=run',headers={k:v for k,v in headers.items() if k!='authorization'})
                    self.assertEqual(invalid.status_code,401);passed.append('original_Auth_denial')
                    bad_runs=[dict(claimed,request_id=B),dict(claimed,observation='unknown'),dict(claimed,finished_at=datetime.fromisoformat(T2.replace('Z','+00:00')))]
                    for bad in bad_runs:
                        state.update(kind='bad_binding',run=bad,result=None)
                        response=await client.get(path+'&view=run',headers=headers)
                        self.assertEqual(response.status_code,503);self.assertEqual(response.json(),{'error':{'code':'de_classification_persisted_invalid'}})
                    passed.append('bad_run_binding_observation_time')
                    badmeta=copy.deepcopy(meta);badmeta['source_binding']['source_manifest_sha256']='9'*64;badmeta['source_manifest_sha256']='9'*64;badmeta['source_binding_sha256']=saved.digest(badmeta['source_binding'])
                    state.update(kind='bad_result',run=succeeded,result=badmeta)
                    response=await client.get(path+'&view=run',headers=headers)
                    self.assertEqual(response.status_code,503);passed.append('bad_source_binding')
                    state.update(kind='unknown',run=None,result=None)
                    response=await client.get(path+'&view=run',headers=headers)
                    self.assertEqual(response.status_code,504);self.assertEqual(response.json(),{'error':{'code':'de_classification_read_timeout'}});self.assertNotIn('state',response.json());passed.append('unknown_no_terminal_DTO')
                    with self.assertRaises(Fault):view.project(once.Observation('unknown','unknown',stored),A,P,Q)
                    state.update(kind='queued',run=None,result=None)
                    app.state.classification_jobs.owner._write_unknown=True
                    response=await client.get(path+'&view=run',headers=headers)
                    self.assertEqual(response.status_code,200);self.assertTrue(app.state.classification_jobs.write_unknown)
                    passed.append('read_never_clears_original_write_unknown')
                    for suffix in ('&view=unknown','&view=run&view=run','&extra=1','&view=run&extra=1'):
                        response=await client.get(path+suffix,headers=headers);self.assertEqual(response.status_code,400)
                    response=await client.get('/api/v1/projects/'+P+'/classification-jobs?view=run&request_id='+Q,headers=headers)
                    self.assertEqual(response.status_code,400)
                    response=await client.post(path+'&view=run',headers=headers,json={});self.assertEqual(response.status_code,400)
                    passed.append('exact_query_and_POST_unchanged')
                    before=len(calls)
                    with patch.object(jobs.ClassificationAdmissionRepository,'read_request',return_value=job):
                        response=await client.get(path,headers=headers)
                    self.assertEqual(response.status_code,200);self.assertEqual(response.json(),job);self.assertEqual(len(response.json()),15)
                    self.assertEqual(len(calls),before);passed.append('old_GET_exact_15_keys')
                self.assertEqual(network.call_count,0);self.assertEqual(native.call_count,0);self.assertEqual(enqueue.call_count,0)
            self.assertIn(B,[s for s,_ in calls]);self.assertTrue(all(not w for _,w in calls));self.assertTrue(auth_calls)
            print(json.dumps({'single_combination_case':'PASS','scenarios':passed,'native_network_DB_writes':0,'old_suites_run':0}))
        finally:
            app.state.complete_imports.close();await app.state.authority.close()

if __name__=='__main__':unittest.main()
