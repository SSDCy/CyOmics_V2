"""One directed SQL-shaped mock combination. No live SQL/R/old suite.

Original HTTP/Authority/service hook/controller/supervision execute. Upstream
binder/source-version and admission storage are explicit doubles, not scientific
proof. The new015 SQL itself is not parsed or run by these mocks.
"""
from contextlib import ExitStack
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from uuid import UUID,uuid4
import asyncio,base64,copy,json,os,socket,subprocess,sys,time,unittest

BACKEND=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BACKEND))
import httpx
from cyomics_projects import differential_jobs as jobs,differential_on_demand as d,classification_on_demand as shared
from cyomics_projects.app import create_app
from cyomics_projects.preprocess_repository import PreprocessRepository
from cyomics_projects.quant_diff_input import BoundInput,Result
from cyomics_projects.quant_diff_saved_input import SavedInputAssembly
from cyomics_projects.settings import EXPECTED_ISSUER,Fault

A='11111111-1111-4111-8111-111111111111';B='22222222-2222-4222-8222-222222222222';P='33333333-3333-4333-8333-333333333333'
V='44444444-4444-4444-8444-444444444444';S='55555555-5555-4555-8555-555555555555';DR='66666666-6666-4666-8666-666666666666';DS='77777777-7777-4777-8777-777777777777';CID='88888888-8888-4888-8888-888888888888';SCOPE='99999999-9999-4999-8999-999999999999'
SOURCE_SHA='c'*64;SCOPE_SHA='d'*64
SELECTION={'processed_dataset_version_id':V,'sample_design_version_id':S,'sample_design_request_id':DR,
    'contrast':{'schemaVersion':1,'dataset_version_id':DS,'quant_family':'LFQ intensity','sample_design_version_id':S,'contrasts':[{'contrast_id':CID,'numerator_group':'Treatment','denominator_group':'WT'}]},'replicate_model':'independent_biological'}
def request():return {**copy.deepcopy(SELECTION),'request_id':str(uuid4())}
def token(actor=A,role='authenticated'):
    enc=lambda x:base64.urlsafe_b64encode(json.dumps(x).encode()).decode().rstrip('=')
    now=int(time.time());return '.'.join((enc({'alg':'HS256'}),enc({'sub':actor,'role':role,'iss':EXPECTED_ISSUER,'aud':'authenticated','iat':now-5,'exp':now+120,'is_anonymous':False}),enc('synthetic')))
# Deliberately synthetic typed projection; no original binder/science case replay.
BOUND=BoundInput(b'{}',False,None,None,(),(),(),(),(),2,(1,-1),b'matrix',b'design',b'preset',b'rows',())

class DatabaseDouble:
    def __init__(self):self.jobs={};self.registrations={};self.issued=0;self.enqueued=0;self.mode=None;self.events=[]
    async def transaction(self,repo,subject,budget,operation,writing=False):
        async def query(sql,args=(),write=False):
            if sql.startswith('SELECT project_id FROM cyomics_app.projects WHERE owner_id='):
                self.events.append('Owner');return (UUID(P),) if tuple(map(str,args))==(A,P) else None
            if 'issue_differential_request_registration(%s,%s,%s,%s)' in sql:
                assert writing and write;scope,project,canonical,source_sha=args;body=json.loads(canonical);assert jobs.canonical(body)==canonical
                assert str(scope)==SCOPE and str(project)==P;self.events.append('issue_sql')
                now=datetime.now(timezone.utc);key=(subject,P,body['request_id']);digest=jobs.request_sha(subject,P,body)
                if self.mode=='refuse' or source_sha!=SOURCE_SHA or {k:v for k,v in body.items() if k!='request_id'}!=SELECTION:
                    return ('unavailable',)+(None,)*9+(now,)
                if key in self.registrations:
                    old=self.registrations[key];return (('consumed' if old[7]==digest else 'conflict'),)+old[1:]
                row=('issued',uuid4(),uuid4(),SCOPE_SHA,UUID(subject),UUID(P),UUID(body['request_id']),digest,now+timedelta(minutes=14),now-timedelta(milliseconds=1),now)
                self.registrations[key]=row;self.issued+=1
                if self.mode=='registration_unknown':repo._write_unknown=True;raise Fault(504,'differential_submission_unknown')
                return row
            if sql.startswith('SELECT synthetic_saved_request'):
                return self.jobs.get((subject,P,str(args[0])))
            if sql.startswith('INSERT INTO cyomics_app.differential_jobs '):
                assert writing and write;body=args[0];self.events.append('enqueue_sql');rid=body['request_id'];key=(subject,P,rid)
                digest=jobs.request_sha(subject,P,body);manifest={n:{'bytes':len(b),'sha256':jobs.sha(b.decode())} for n,b in (('matrix',BOUND.matrix_tsv),('design',BOUND.design_tsv),('preset',BOUND.preset_tsv))}
                dto={'schemaVersion':2,'contract':jobs.CONTRACT,'job_id':str(uuid4()),'project_id':P,'request_id':rid,'request_sha256':digest,'binding_sha256':'e'*64,
                    'selection':{k:v for k,v in body.items() if k!='request_id'},'source':{'created_by':subject,'project_id':P,'processed_dataset_version_id':V},'method':copy.deepcopy(jobs.METHOD),
                    'input_manifest':manifest,'status':'queued','revision':1,'observation':'confirmed','created_at':'2026-01-01T00:00:00.000000Z',
                    'started_at':None,'finished_at':None,'attempt_id':None,'runtime':None,'result':None,'error':None}
                jobs.task_value(dto,project=P,request_id=rid)
                stored=jobs.StoredJob(jobs.canonical(dto),jobs.canonical(body),digest);self.jobs[key]=stored;self.enqueued+=1
                if self.mode=='enqueue_unknown':repo._write_unknown=True;raise Fault(504,'differential_submission_unknown')
                if self.mode=='enqueue_refused':self.jobs.pop(key);self.enqueued-=1;raise Fault(503,'differential_unavailable')
                return stored
            raise AssertionError('unexpected SQL-shaped mock operation')
        return await operation(query)

class Writer:
    def __init__(self):self.data=b''
    def write(self,value):self.data+=value
    async def drain(self):pass
    def close(self):pass
    async def wait_closed(self):pass
class Transport:
    def __init__(self,bad=False):self._finished=not bad;self.closed=False
    def close(self):self.closed=True
    def is_closing(self):return self.closed
    def get_pipe_transport(self,fd):return None
class Process:
    def __init__(self,controller,bad=False):
        self.returncode=None;self.stdin=Writer();self.stdout=asyncio.StreamReader();self.stderr=asyncio.StreamReader();self._transport=Transport(bad)
        n=controller._notification;receipt={'schemaVersion':1,'job_id':n['job_id'],'request_id':n['request_id'],'attempt_id':str(uuid4()),'result_id':str(uuid4()),
            'disposition':'confirmed','reason':'succeeded','business_state':'confirmed','teardown':'confirmed','loop_closed':True,'pending_tasks_seen':False,'async_resources_remaining':False,'interrupted':False,'exit_code':0,'registration_id':controller._ticket.registration_id}
        self.stdout.feed_data(shared.canonical(receipt)+b'\n');self.stdout.feed_eof();self.stderr.feed_eof()
    async def wait(self):await asyncio.sleep(0);self.returncode=0;return 0

class ServiceFiniteCombination(unittest.IsolatedAsyncioTestCase):
    async def test_one_directed_combination(self):
        apps=[];clients=[];db=DatabaseDouble();spawned=[];intent_records=[];scenarios=[];cleanup_bad=set()
        async def auth(request):
            subject=json.loads(base64.urlsafe_b64decode(request.headers['authorization'].split('.')[1]+'=='))['sub']
            return httpx.Response(200,json={'id':subject,'role':'authenticated','aud':'authenticated','is_anonymous':False,'email':'synthetic@example.invalid','email_confirmed_at':'2026-01-01T00:00:00Z'})
        async def tx(repo,subject,budget,operation,writing=False):
            try:return await db.transaction(repo,subject,budget,operation,writing)
            except BaseException as error:
                if not isinstance(error,Fault):
                    import traceback
                    traceback.print_exc()
                raise
        async def find(repo,subject,project,rid,budget):
            async def op(query):await repo._owner(query,subject,project);return await query('SELECT synthetic_saved_request FROM cyomics_app.differential_jobs WHERE request_id=%s',(rid,))
            return await repo._transaction(subject,budget,op)
        async def enqueue(repo,subject,project,body,bound,budget):
            self.assertIs(bound,BOUND)
            async def op(query):await repo._owner(query,subject,project);return await query('INSERT INTO cyomics_app.differential_jobs (synthetic_only) VALUES (%s)',(body,),write=True)
            return jobs.Queued(await repo._transaction(subject,budget,op,writing=True),True)
        async def binder(assembly,subject,project,selection,budget):
            db.events.append('original_binder_double');self.assertEqual((subject,project),(A,P));return Result(False,('synthetic_rejection',),False) if db.mode=='binder_rejected' else Result(True,(),False,BOUND)
        def sources(controller):db.events.append('source_control_double')
        def intent(controller,value,job_key='job_id'):
            # Capture the real subclass extension before the unchanged base filesystem helper.
            self.assertEqual(set(value),set(d.IPC_KEYS)|{'registration_id','epoch_id','scope_sha256','service_scope_id'})
            self.assertEqual(value['registration_id'],controller._ticket.registration_id);intent_records.append(copy.deepcopy(value))
        async def spawn(controller):
            spawned.append((controller._notification.copy(),controller._ticket));return Process(controller,id(controller) in cleanup_bad)
        def client():
            launcher=shared.Config(enabled=True,startup=Path('/synthetic/no-exec.py'),backend=BACKEND,control=Path('/synthetic/no-runtime'),startup_sha256='a'*64,python_sha256='b'*64,verify=lambda:None)
            pool=object();app=create_app(auth_transport=httpx.MockTransport(auth),pool=pool,anon_key_loader=lambda:token(role='anon'),preprocessing_repository=PreprocessRepository(pool),imports_enabled=True,sample_designs_enabled=True,differential_inputs_enabled=True,differential_jobs_enabled=True,
                differential_dispatch_config=d.Config(launcher=launcher,service_scope=d.ServiceScope(SCOPE,SCOPE_SHA,SOURCE_SHA)))
            apps.append(app);c=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:57430');clients.append(c);return c,app.state.differential_dispatch
        headers={'authorization':'Bearer '+token(),'origin':'http://127.0.0.1:4206','sec-fetch-site':'same-origin','x-cyomics-request':'1'};path='/api/v1/projects/'+P+'/differential-jobs'
        async def settle(controller):
            task=controller._monitor
            if task is not None:await task
        try:
            with ExitStack() as stack:
                network=stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')))
                native=stack.enter_context(patch.object(subprocess,'Popen',side_effect=AssertionError('native forbidden')))
                stack.enter_context(patch('asyncio.create_subprocess_exec',side_effect=AssertionError('native forbidden')))
                stack.enter_context(patch.object(jobs.DifferentialRepository,'_transaction',tx));stack.enter_context(patch.object(jobs.DifferentialRepository,'find_request',find));stack.enter_context(patch.object(jobs.DifferentialRepository,'enqueue',enqueue));stack.enter_context(patch.object(SavedInputAssembly,'assemble',binder))
                stack.enter_context(patch.object(jobs.pc,'version_value',side_effect=lambda value,**kwargs:value)) # Original saved-version proof is outside this directed seam case.
                stack.enter_context(patch.object(shared.Controller,'_sources',sources));stack.enter_context(patch.object(shared.Controller,'_intent',intent));stack.enter_context(patch.object(shared.Controller,'_spawn',spawn));stack.enter_context(patch.object(shared.Controller,'_pin',side_effect=lambda proc:os.open('/dev/null',os.O_RDONLY)))
                c,controller=client();body=request();response=await c.post(path,json=body,headers=headers)
                self.assertEqual(response.status_code,201,(response.text,db.events,controller.snapshot()));await settle(controller)
                self.assertEqual((db.issued,db.enqueued,len(spawned)),(1,1,1));self.assertEqual(controller.snapshot()['state'],'idle')
                self.assertLess(db.events.index('original_binder_double'),db.events.index('issue_sql'));self.assertLess(db.events.index('issue_sql'),db.events.index('enqueue_sql'))
                first=spawned[0][1];body2=request();response=await c.post(path,json=body2,headers=headers);self.assertEqual(response.status_code,201,response.text);await settle(controller)
                self.assertEqual((db.issued,db.enqueued,len(spawned)),(2,2,2));self.assertNotEqual(first.registration_id,spawned[1][1].registration_id);self.assertNotEqual(first.epoch_id,spawned[1][1].epoch_id)
                self.assertEqual(len(intent_records),2);self.assertEqual(set(spawned[0][0]),d.IPC_KEYS)
                scenarios.append('legal_new_requests_binder_then_committed_exact_ticket_then_enqueue_201_one_notify_each_perlease_distinct_intents')
                before=(db.issued,db.enqueued,len(spawned));response=await c.post(path,json=body,headers=headers);self.assertEqual(response.status_code,200,response.text)
                conflict=copy.deepcopy(body);conflict['contrast']['contrasts'][0]['numerator_group']='different';response=await c.post(path,json=conflict,headers=headers);self.assertEqual(response.status_code,409,response.text)
                denied=dict(headers,authorization='Bearer '+token(B));response=await c.post(path,json=request(),headers=denied);self.assertEqual(response.status_code,404,response.text)
                self.assertEqual((db.issued,db.enqueued,len(spawned)),before)
                db.mode='binder_rejected';response=await c.post(path,json=request(),headers=headers);self.assertEqual(response.status_code,422,response.text);self.assertEqual((db.issued,db.enqueued,len(spawned)),before);db.mode=None
                scenarios.append('same_request_200_no_issuance_or_spawn_payload409_foreignOwner404_binder422_before_issuance')
                for mode in ('registration_unknown','enqueue_unknown','enqueue_refused'):
                    c2,k=client();db.mode=mode;r=request();n=len(spawned);response=await c2.post(path,json=r,headers=headers)
                    self.assertEqual(response.status_code,504,(mode,response.text));self.assertEqual(response.json()['error']['code'],'differential_submission_unknown');self.assertEqual(k.snapshot()['state'],'unknown');self.assertTrue(k._capacity.unknown)
                    self.assertEqual(len(spawned),n);issued=db.issued;response=await c2.post(path,json=r,headers=headers);self.assertEqual(response.status_code,504);self.assertEqual(db.issued,issued);self.assertEqual(len(spawned),n)
                db.mode=None;c3,k3=client();cleanup_bad.add(id(k3));r=request();n=len(spawned);response=await c3.post(path,json=r,headers=headers);self.assertEqual(response.status_code,201,response.text);await settle(k3)
                self.assertEqual(k3.snapshot()['state'],'unknown');self.assertTrue(k3._capacity.unknown);self.assertEqual(len(spawned),n+1)
                response=await c3.post(path,json=r,headers=headers);self.assertEqual(response.status_code,200);self.assertEqual(len(spawned),n+1)
                issued=db.issued;response=await c3.post(path,json=request(),headers=headers);self.assertEqual(response.status_code,503);self.assertEqual(db.issued,issued)
                scenarios.append('registration_enqueue_and_teardown_unknown_keep_consumption_quarantine_capacity_no_repeat_200_replay_never_spawn')
                self.assertEqual(network.call_count,0);self.assertEqual(native.call_count,0)
            print(json.dumps({'single_directed_combination':'PASS','scenario_groups':scenarios,'live_SQL_R_Auth_HTTP_listener_calls':0,'old_suites':0,'mock_boundaries':['SQL-shaped transaction/issuer/admission','upstream binder/saved-version proof','process creation/identity/base intent filesystem'],'original_exercised':['Authority via MockTransport','HTTP status/actor','service binder-before-hook','controller perlease/receipt','shared supervision/capacity/cleanup']}))
        finally:
            for c in clients:await c.aclose()
            for app in apps:app.state.complete_imports.close();await app.state.authority.close()

if __name__=='__main__':unittest.main()
