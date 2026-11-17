"""Synthetic offline ASGI/contract tests; no real auth, DB, worker or socket."""
import asyncio,base64,copy,importlib.util,json,os,runpy,sys,time,types,unittest
from pathlib import Path
from uuid import uuid4
REPO=Path(os.environ.get('CYOMICS_HTTP_BASELINE',Path(__file__).resolve().parents[1]))
SUPPORT=Path(os.environ.get('CYOMICS_HTTP_SUPPORT',REPO))
os.environ['CYOMICS_RAW_QC_BASELINE']=str(REPO)
if importlib.util.find_spec('httpx') is None:
    h=types.ModuleType('httpx')
    class NoNetwork:
        def __init__(self,*a,**kw):raise AssertionError('real HTTP forbidden')
    h.AsyncClient=h.AsyncHTTPTransport=NoNetwork;sys.modules['httpx']=h
fixture=runpy.run_path(str(SUPPORT/'tests/test_raw_qc_contract.py'))
sys.modules['cyomics_projects'].__path__.insert(0,str(Path(__file__).resolve().parents[1]/'cyomics_projects'))
from cyomics_projects.raw_qc_http import RawQCJobs,route_input,response_value
from cyomics_projects.app import ProjectBoundary,create_app
from cyomics_projects.settings import Fault
from cyomics_projects.raw_qc_contract import decode_record,canonical,digest
OWNER,OTHER,PROJECT,DATASET=(fixture[k] for k in ('OWNER','OTHER','PROJECT','DATASET'))
REQ=str(uuid4());JOB=fixture['record'](REQ);PUBLIC=decode_record(JOB)
TOKEN='.'.join(base64.urlsafe_b64encode(json.dumps(x).encode()).decode().rstrip('=') for x in ({'alg':'HS256'},{'exp':time.time()+300})) + '.eA'
class SyntheticAuthority:
    def __init__(self,subject=OWNER):self.subject=subject;self.calls=0
    async def verify(self,request,budget):
        self.calls+=1
        if request.headers.get('authorization')!='Bearer '+TOKEN:raise Fault(401,'unauthenticated')
        return self.subject
class SyntheticRepository:
    def __init__(self):self.calls=[];self.value=PUBLIC;self.error=None
    async def call(self,kind,args):
        self.calls.append((kind,args[:-1]))
        if args[0]!=OWNER:raise Fault(404,'dataset_not_found')
        if self.error:raise self.error
        return copy.deepcopy(self.value)
    async def enqueue(self,*args):return await self.call('enqueue',args)
    async def read_request(self,*args):return await self.call('request',args)
    async def read_job(self,*args):return await self.call('job',args)
def scope(method='GET',detail=False,request=True):
    path='/api/v1/raw-qc-jobs/'+PUBLIC['job_id'] if detail else '/api/v1/datasets/'+DATASET+'/raw-qc-jobs'
    query='project_id='+PROJECT+('&request_id='+REQ if request and not detail and method=='GET' else '')
    headers=[(b'host',b'127.0.0.1:57430'),(b'authorization',('Bearer '+TOKEN).encode())]
    if method=='POST':headers += [(b'origin',b'http://127.0.0.1:4206'),(b'x-cyomics-request',b'1'),(b'content-type',b'application/json')]
    return {'type':'http','method':method,'path':path,'raw_path':path.encode(),'root_path':'','query_string':query.encode(),'headers':headers}
def body():return json.dumps({'schemaVersion':1,'request_id':REQ,'quant_family':'intensity','params':{}}).encode()
async def invoke(handler,s,raw=b''):
    q=asyncio.Queue();q.put_nowait({'type':'http.request','body':raw,'more_body':False});output=[]
    async def send(x):output.append(x)
    await handler(s,q.get,send)
    return output
def status(out):return out[0]['status']
class Tests(unittest.IsolatedAsyncioTestCase):
    async def test_default_disconnected_no_auth_repository_or_success(self):
        a=SyntheticAuthority();out=await invoke(RawQCJobs(a),scope('POST'),body())
        self.assertEqual(status(out),503);self.assertEqual(a.calls,0)
    async def test_three_routes_verify_every_time_and_bind_subject(self):
        a=SyntheticAuthority();r=SyntheticRepository();h=RawQCJobs(a,r)
        for s,b in [(scope('POST'),body()),(scope(),b''),(scope(detail=True),b'')]:
            out=await invoke(h,s,b);self.assertEqual(status(out),202 if s['method']=='POST' else 200);self.assertEqual(json.loads(out[1]['body']),PUBLIC)
            self.assertLessEqual(len(out[1]['body']),32768)
        self.assertEqual(a.calls,3);self.assertEqual([c[0] for c in r.calls],['enqueue','request','job'])
        self.assertTrue(all(c[1][0]==OWNER for c in r.calls))
    async def test_auth_failure_and_other_owner_never_success(self):
        r=SyntheticRepository();a=SyntheticAuthority();h=RawQCJobs(a,r);s=scope();s['headers']=s['headers'][:1]
        self.assertEqual(status(await invoke(h,s)),401);self.assertEqual(r.calls,[])
        self.assertEqual(status(await invoke(RawQCJobs(SyntheticAuthority(OTHER),r),scope())),404)
    async def test_route_strict_and_no_enumeration(self):
        for change,expected in [(lambda s:s.update(query_string=('project_id='+PROJECT).encode()),400),
          (lambda s:s.update(query_string=s['query_string']+b'&owner_id=x'),400),
          (lambda s:s.update(raw_path=s['raw_path']+b'/'),404),
          (lambda s:s.update(method='DELETE'),405),
          (lambda s:s.update(query_string=s['query_string']+b'&project_id=x'),400),
          (lambda s:s.update(query_string=s['query_string'].replace(b'project_id',b'%70roject_id')),400)]:
            s=scope();change(s)
            with self.assertRaises(Fault) as e:route_input(s)
            self.assertEqual(e.exception.status,expected)
    async def test_rejects_client_owner_pins_paths_matrix_and_body_cap(self):
        r=SyntheticRepository();h=RawQCJobs(SyntheticAuthority(),r)
        for k in ['owner_id','execution','path','matrix']:
            b=json.loads(body());b[k]='forbidden';self.assertEqual(status(await invoke(h,scope('POST'),json.dumps(b).encode())),400)
        self.assertEqual(status(await invoke(h,scope('POST'),b'x'*1025)),413);self.assertEqual(r.calls,[])
    async def test_origin_media_and_forged_headers(self):
        for key,value,code in [(b'origin',b'https://other.test',403),(b'content-type',b'text/plain',415),(b'x-owner-id',OWNER.encode(),403)]:
            s=scope('POST');s['headers']=[p for p in s['headers'] if p[0]!=key]+[(key,value)]
            r=SyntheticRepository();self.assertEqual(status(await invoke(RawQCJobs(SyntheticAuthority(),r),s,body())),code);self.assertEqual(r.calls,[])
    async def test_all_four_states_and_reconciliation_are_preserved(self):
        for state in ['queued','running','failed','succeeded','reconciliation_pending']:
            row=fixture['record'](REQ)
            if state in ('running','succeeded','reconciliation_pending'):row[12]='running';row[15]=fixture['STAMP'];row[17]=uuid4()
            if state=='failed':row[12]='failed';row[16]=fixture['STAMP'];row[19]='runner_failed'
            if state=='succeeded':
                row[12]='succeeded';row[16]=fixture['STAMP']
                # Build validated running public context before adding terminal result.
                running=list(row);running[12]='running';running[16]=None
                result=fixture['result_for'](decode_record(running));row[20]=canonical(result);row[21]=digest(row[20])
            if state=='reconciliation_pending':row[18]='reconciliation_pending'
            r=SyntheticRepository();r.value=decode_record(row)
            out=await invoke(RawQCJobs(SyntheticAuthority(),r),scope());self.assertEqual(status(out),200)
            value=json.loads(out[1]['body']);self.assertEqual(value['status'],'running' if state=='reconciliation_pending' else state)
            if state=='reconciliation_pending':self.assertIsNone(value['result'])
            post=await invoke(RawQCJobs(SyntheticAuthority(),r),scope('POST'),body())
            self.assertEqual(status(post),200 if state in ('succeeded','failed') else 202)
    async def test_corrupt_or_wrong_context_get503_post_unknown_and_no_retry(self):
        for mutate in [lambda v:v.update(status='unknown'),lambda v:v.update(request_id=str(uuid4())),lambda v:v['source'].update(project_id=OTHER),lambda v:v.update(result={'path':'/private'})]:
            r=SyntheticRepository();r.value=copy.deepcopy(PUBLIC);mutate(r.value)
            self.assertEqual(status(await invoke(RawQCJobs(SyntheticAuthority(),r),scope())),503)
            out=await invoke(RawQCJobs(SyntheticAuthority(),r),scope('POST'),body());self.assertEqual(status(out),504)
            self.assertEqual(json.loads(out[1]['body'])['error']['code'],'raw_qc_submission_unknown');self.assertEqual(len(r.calls),2)
    async def test_repository_unknown_conflict_capacity_preserved(self):
        for code,http in [('raw_qc_submission_unknown',504),('raw_qc_request_conflict',409),('raw_qc_capacity',429),('raw_qc_unavailable',503)]:
            r=SyntheticRepository();r.error=Fault(http,code);out=await invoke(RawQCJobs(SyntheticAuthority(),r),scope('POST'),body())
            self.assertEqual(status(out),http);self.assertEqual(json.loads(out[1]['body'])['error']['code'],code);self.assertEqual(len(r.calls),1)
    async def test_late_response_timeout_is_not_success(self):
        class Late(SyntheticRepository):
            async def enqueue(self,*args):raise TimeoutError()
        self.assertEqual(status(await invoke(RawQCJobs(SyntheticAuthority(),Late()),scope('POST'),body())),504)
    async def test_disconnect_cancels_pending_submission_without_private_response(self):
        entered=asyncio.Event();cancelled=asyncio.Event();output=[];q=asyncio.Queue()
        class Waiting(SyntheticRepository):
            async def enqueue(self,*args):
                entered.set()
                try:await asyncio.Future()
                finally:cancelled.set()
        q.put_nowait({'type':'http.request','body':body(),'more_body':False})
        async def send(value):output.append(value)
        task=asyncio.create_task(RawQCJobs(SyntheticAuthority(),Waiting())(scope('POST'),q.get,send))
        await asyncio.wait_for(entered.wait(),1);q.put_nowait({'type':'http.disconnect'})
        await asyncio.wait_for(task,1);await asyncio.wait_for(cancelled.wait(),1)
        self.assertEqual(output,[])
    async def test_dispatch_before_broad_samples_and_old_path_compatible(self):
        class Broad:
            calls=0
            def owns(self,s):return True
            async def __call__(self,*a):self.calls+=1
        broad=Broad();boundary=ProjectBoundary(None,samples=broad,raw_qc=RawQCJobs(SyntheticAuthority(),SyntheticRepository()))
        self.assertEqual(status(await invoke(boundary,scope())),200);self.assertEqual(broad.calls,0)
        old=scope();old['path']='/api/v1/datasets/'+DATASET+'/sample-designs';old['raw_path']=old['path'].encode()
        await invoke(boundary,old);self.assertEqual(broad.calls,1)
    async def test_default_factory_option_and_same_authority_wiring(self):
        import inspect
        self.assertIsNone(inspect.signature(create_app).parameters['raw_qc_repository'].default)
        self.assertIn('RawQCJobs(authority, raw_qc_repository)',inspect.getsource(create_app))
if __name__=='__main__':unittest.main(verbosity=2)
