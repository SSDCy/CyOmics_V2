import asyncio
import base64
import json
import socket
import threading
import time
import unittest
from unittest.mock import patch
import httpx
import psycopg
from cyomics_projects.app import create_app
from cyomics_projects import import_http as api,repository,settings
from cyomics_projects.import_persistence import ImportFault
from cyomics_projects.settings import Budget
from import_fakes import A,B,PROJECT,SECOND,FILE,OTHER_FILE,RAW,ImportPool,InlineTestRunner
from test_projects import token,ANON

PATH='/api/v1/uploads/'+str(FILE)+'/complete'
ORIGIN='http://127.0.0.1:4206'
GUARDS=[]
def forbidden(*args,**kwargs):GUARDS.append('real_action');raise AssertionError('Real runtime I/O prohibited')

class Pool(ImportPool):
    def close(self):
        for connection in self.connections:connection.finish()

class CpuFake(InlineTestRunner):
    def __init__(self):
        super().__init__();self.hold=False;self._wrapped=None;self.futures=[];self.entered=asyncio.Event()
    async def run(self,operation,budget,*,key=None):
        if not self.hold:return await super().run(operation,budget,key=key)
        self._wrapped=asyncio.get_running_loop().create_future();self.futures.append(self._wrapped);self.entered.set()
        lease=api.CURRENT_LEASE.get()
        if lease is not None:lease.future=self._wrapped
        return await asyncio.shield(self._wrapped)
    def finish(self):
        for future in self.futures:
            if not future.done():future.set_result(None)
    def close(self):self.finish()

class ImportHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches=[]
        for obj,name in [(socket.socket,'connect'),(threading.Thread,'start'),(psycopg.AsyncConnection,'connect'),(repository,'load_runtime_password'),(settings,'load_runtime_password'),(settings,'load_approved_anon_key')]:
            p=patch.object(obj,name,side_effect=forbidden);p.start();self.patches.append(p)
        self.pool=Pool();self.runner=CpuFake();self.auth_calls=[];self.auth_status=200
        async def auth(request):
            self.auth_calls.append(request.url.path)
            try:claims=json.loads(base64.urlsafe_b64decode(request.headers['authorization'][7:].split('.')[1]+'=='))
            except Exception:return httpx.Response(401,json={'code':'bad_fixture_token'})
            return httpx.Response(self.auth_status,json={'id':claims['sub'],'aud':'authenticated','role':'authenticated','is_anonymous':False,'email':'fixture@example.invalid','email_confirmed_at':'2026-01-01T00:00:00Z'})
        self.auth=httpx.MockTransport(auth)
        self.app=create_app(auth_transport=self.auth,pool=self.pool,anon_key_loader=lambda:ANON,imports_enabled=True,uploads_enabled=True,import_runner=self.runner)
        self.life=self.app.router.lifespan_context(self.app);await self.life.__aenter__()
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://127.0.0.1:57430')
    async def asyncTearDown(self):
        self.runner.finish();await self.client.aclose();await self.life.__aexit__(None,None,None)
        for p in reversed(self.patches):p.stop()
        self.assertEqual(GUARDS,[])
    async def request(self,method='POST',path=PATH,subject=A,body=None,headers=None):
        wire=json.dumps({'project_id':str(PROJECT)}).encode() if body is None and method=='POST' else b'' if body is None else body
        head={'Origin':ORIGIN,'X-Cyomics-Request':'1','Content-Type':'application/json'}
        if subject is not None:head['Authorization']='Bearer '+token(subject)
        head.update(headers or {})
        return await self.client.request(method,path,content=wire,headers=head)
    def raw_calls(self):return sum('FROM cyomics_app.raw_uploads' in sql for sql,_ in self.pool.calls)

    async def test_post_get_confirmed_exact_DTO_and_GET_never_inserts(self):
        first=await self.request();self.assertEqual(first.status_code,200,first.text)
        body=first.json();self.assertEqual(set(body),{'datasetVersion','importReport'})
        key=body['datasetVersion']['dataset_version_id'];count=sum(s.startswith('INSERT') for s,_ in self.pool.calls)
        second=await self.request('GET',PATH+'?version_id='+key+'&project_id='+str(PROJECT));self.assertEqual(second.status_code,200,second.text);self.assertEqual(second.json(),body)
        self.assertEqual(sum(s.startswith('INSERT') for s,_ in self.pool.calls),count)
        self.assertLessEqual(len(second.content),1114112);self.assertEqual(second.headers['cache-control'],'no-store');self.assertNotIn('set-cookie',second.headers)

    async def test_default_gate_and_unstarted_lifecycle_do_no_auth_or_raw_work(self):
        app=create_app(auth_transport=self.auth,pool=self.pool,anon_key_loader=lambda:ANON)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:57430') as client:
            response=await client.post(PATH,json={'project_id':str(PROJECT)})
        self.assertEqual(response.status_code,503);self.assertEqual(self.auth_calls,[]);self.assertEqual(self.raw_calls(),0);await app.state.authority.close()

    async def test_authority_unauthenticated_forged_expired_and_browser_identity_rejected(self):
        for headers,subject,status in [({},None,401),({'Authorization':'Bearer forged.signature.value'},None,401),({'Authorization':'Bearer '+token(exp=time.time()-1)},None,401),({'X-User-Id':A},None,403)]:
            value=await self.request(headers=headers,subject=subject);self.assertEqual(value.status_code,status,value.text)
        self.auth_status=401;self.assertEqual((await self.request()).status_code,401);self.assertEqual(self.raw_calls(),0)

    async def test_wrong_owner_or_wrong_project_file_404_no_report(self):
        wrong=await self.request(subject=B);self.assertEqual(wrong.json(),{'error':{'code':'project_not_found'}})
        wrong_file=await self.request(subject=B,body=json.dumps({'project_id':str(SECOND)}).encode());self.assertEqual(wrong_file.json(),{'error':{'code':'file_not_found'}})
        self.assertEqual(self.runner.calls,0);self.assertFalse(self.pool.versions)

    async def test_method_origin_path_query_body_type_and_stream_bounds(self):
        cases=[('PUT',PATH,None,None,405),('POST',PATH+'?project_id='+str(PROJECT),None,None,400),
               ('GET',PATH,None,None,400),('GET',PATH+'?project_id='+str(PROJECT)+'&project_id='+str(PROJECT),None,None,400),
               ('GET',PATH+'?project_id=%34'+str(PROJECT)[1:],None,None,400),('POST',PATH.replace('/uploads/','/%75ploads/'),None,None,400),
               ('POST',PATH,b'{"project_id":"'+str(PROJECT).encode()+b'","project_id":"'+str(PROJECT).encode()+b'"}',None,400),
               ('POST',PATH,b'\xff',None,400),('POST',PATH,b'x'*1025,None,413),('POST',PATH,None,{'Content-Type':'text/plain'},415),
               ('POST',PATH,None,{'Origin':'http://evil.invalid'},403),('POST',PATH,None,{'Content-Encoding':'gzip'},415),
               ('GET',PATH+'?project_id='+str(PROJECT),b'x',None,400),('POST',PATH,None,{'X-Owner-Id':A},403)]
        for method,path,body,headers,status in cases:
            value=await self.request(method,path,body=body,headers=headers);self.assertEqual(value.status_code,status,(method,path,value.text))
        self.assertEqual(self.raw_calls(),0)

    async def test_safe_422_issues_and_commit_unknown_manual_GET_confirm(self):
        self.pool.set_raw(FILE,PROJECT,b'Protein IDs\tIntensity x\na\t0\textra\n')
        value=await self.request();self.assertEqual(value.status_code,422,value.text)
        issues=value.json()['error']['issues'];self.assertLessEqual(len(issues),8);self.assertEqual(issues[0]['code'],'TXT_ROW_WIDTH');self.assertNotIn('extra',value.text)
        self.pool.set_raw(FILE,PROJECT,RAW);self.pool.commit_mode='lost';unknown=await self.request()
        self.assertEqual(unknown.status_code,504,unknown.text);self.assertEqual(unknown.json()['error']['code'],'import_result_unknown')
        key=unknown.json()['error']['version_key'];inserts=sum(s.startswith('INSERT') for s,_ in self.pool.calls)
        check=await self.request('GET',PATH+'?project_id='+str(PROJECT)+'&version_id='+key)
        self.assertEqual(check.status_code,200,check.text);self.assertEqual(sum(s.startswith('INSERT') for s,_ in self.pool.calls),inserts)

    async def test_rate_admission_and_capacity_precede_raw_loading(self):
        boundary=self.app.state.complete_imports;boundary.attempts[(A,'POST')]=(time.monotonic()+600,10)
        value=await self.request();self.assertEqual(value.status_code,429);self.assertEqual(value.json()['error']['code'],'import_rate_limited');self.assertEqual(self.raw_calls(),0)
        boundary.attempts.clear();self.runner.hold=True
        first=asyncio.create_task(self.request());await asyncio.wait_for(self.runner.entered.wait(),5);n=self.raw_calls()
        blocked=await self.request();self.assertEqual(blocked.status_code,429);self.assertEqual(self.raw_calls(),n)
        second=asyncio.create_task(self.request(path='/api/v1/uploads/'+str(OTHER_FILE)+'/complete',subject=B,body=json.dumps({'project_id':str(SECOND)}).encode()))
        async def two():
            while len(self.runner.futures)<2:await asyncio.sleep(0)
        await asyncio.wait_for(two(),5)
        count=self.raw_calls();third=await self.request();self.assertEqual(third.status_code,429);self.assertEqual(self.raw_calls(),count)
        first.cancel();second.cancel();await asyncio.gather(first,second,return_exceptions=True);self.runner.finish()

    async def test_timeout_preserves_lease_until_actual_fake_worker_finishes(self):
        self.runner.hold=True
        with patch.object(api,'import_budget',return_value=Budget(.03)):
            value=await self.request()
        self.assertEqual(value.status_code,504,value.text);self.assertEqual(value.json()['error']['code'],'import_timeout')
        self.assertEqual(len(self.app.state.complete_imports.leases),1);n=self.raw_calls()
        blocked=await self.request();self.assertEqual(blocked.status_code,429);self.assertEqual(self.raw_calls(),n)
        self.runner.finish()
        for _ in range(4):await asyncio.sleep(0)
        self.assertFalse(self.app.state.complete_imports.leases);self.assertFalse(self.pool.versions)

    async def test_disconnect_sends_no_late_success_and_retains_unfinished_worker(self):
        self.runner.hold=True;messages=asyncio.Queue();sent=[]
        await messages.put({'type':'http.request','body':json.dumps({'project_id':str(PROJECT)}).encode(),'more_body':False})
        scope={'type':'http','http_version':'1.1','method':'POST','scheme':'http','path':PATH,'raw_path':PATH.encode(),'query_string':b'','root_path':'','headers':[(b'host',b'127.0.0.1:57430'),(b'origin',ORIGIN.encode()),(b'x-cyomics-request',b'1'),(b'content-type',b'application/json'),(b'authorization',('Bearer '+token()).encode())],'server':('127.0.0.1',57430),'client':('127.0.0.1',12345)}
        async def send(value):sent.append(value)
        task=asyncio.create_task(self.app(scope,messages.get,send));await asyncio.wait_for(self.runner.entered.wait(),5);await messages.put({'type':'http.disconnect'});await task
        self.assertEqual(sent,[]);self.assertEqual(len(self.app.state.complete_imports.leases),1)
        self.runner.finish()
        for _ in range(4):await asyncio.sleep(0)
        self.assertEqual(sent,[]);self.assertFalse(self.app.state.complete_imports.leases)

    async def test_timing_hints_only_shorten_and_error_extensions_are_bounded(self):
        scope={'headers':[]}
        with patch.object(api.time,'monotonic',return_value=100),patch.object(api.time,'time',return_value=1700000000):
            self.assertEqual(api.import_budget(scope,'POST').deadline,150);self.assertEqual(api.import_budget(scope,'GET').deadline,109)
            scope['headers']=[(b'x-cyomics-remaining-ms',b'1000'),(b'x-cyomics-deadline-unix-ms',b'1700000000500')]
            self.assertEqual(api.import_budget(scope,'POST').deadline,100.5)
            scope['headers'].append(scope['headers'][0])
            with self.assertRaises(ImportFault):api.import_budget(scope,'POST')
        issue={'code':'TXT_ROW_WIDTH','record_number':1,'line':2,'column_ordinal':3,'limit_key':None}
        response=api.error_response(ImportFault(422,'txt_import_failed',issues=[issue]*20));value=json.loads(response.body)
        self.assertEqual(len(value['error']['issues']),8);self.assertLessEqual(len(response.body),4096)
        bad=dict(issue,raw_text='PRIVATE');value=json.loads(api.error_response(ImportFault(422,'txt_import_failed',issues=[bad])).body)
        self.assertNotIn('issues',value['error']);self.assertNotIn('PRIVATE',json.dumps(value))
