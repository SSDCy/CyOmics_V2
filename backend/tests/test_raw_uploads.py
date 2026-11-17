import asyncio
import base64
import hashlib
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from uuid import UUID,uuid4
import httpx
from cyomics_projects.app import create_app,entry_budget
from cyomics_projects.settings import Budget,Fault
from cyomics_projects.upload_repository import UploadRepository,UploadOutcome
from cyomics_projects.upload_validation import classify,MAX_BYTES,input_headers
from fakes import A,B,PROJECT,SECOND,NOW
from test_projects import token,ANON
from upload_fakes import FilePool

RAW=b'\xef\xbb\xbfID\tValue\r\nx\t1\r\n\r\n'
RID='66666666-6666-4666-8666-666666666666'
P='/api/v1/projects/'+str(PROJECT)
def headers(raw=RAW,rid=RID,name='  raw.TXT'):
    return {'Content-Type':'application/octet-stream','Idempotency-Key':rid,
            'X-Cyomics-Filename':base64.urlsafe_b64encode(name.encode()).decode().rstrip('='),
            'X-Cyomics-File-Format':'proteinGroups_txt','X-Cyomics-File-Bytes':str(len(raw)),
            'X-Cyomics-File-Sha256':hashlib.sha256(raw).hexdigest()}
def upload_budget():
    budget=Budget();budget.create=UploadOutcome();return budget

class RawApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool=FilePool();self.auth_calls=[];self.auth_status=200
        async def auth(request):
            self.auth_calls.append(request)
            claims=json.loads(base64.urlsafe_b64decode(request.headers['authorization'][7:].split('.')[1]+'=='))
            return httpx.Response(self.auth_status,json={'id':claims['sub'],'aud':'authenticated','role':'authenticated','is_anonymous':False,'email':'fixture@example.invalid','email_confirmed_at':'2026-01-01T00:00:00Z'})
        self.app=create_app(auth_transport=httpx.MockTransport(auth),pool=self.pool,anon_key_loader=lambda:ANON,uploads_enabled=True)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://offline.invalid')
    async def asyncTearDown(self):
        await self.client.aclose();await self.app.state.authority.close();self.pool.close()
    async def request(self,method,path,raw=b'',subject=A,extra=None):
        h={'Authorization':'Bearer '+token(subject)}
        if method=='POST':h.update(headers(raw))
        h.update(extra or {})
        return await self.client.request(method,path,content=raw,headers=h)

    async def test_BOM_CRLF_exact_bytes_receipt_list_and_content_roundtrip(self):
        r=await self.request('POST',P+'/raw-uploads',RAW);self.assertEqual(r.status_code,201,r.text)
        file=r.json()['file'];self.assertEqual(file['filename'],'  raw.TXT');self.assertEqual(file['bytes'],len(RAW));self.assertEqual(file['sha256'],hashlib.sha256(RAW).hexdigest());self.assertEqual(file['status'],'stored')
        self.assertEqual(set(file),{'file_id','project_id','request_id','filename','format','bytes','sha256','status','createdAt'})
        lookup=await self.request('GET',P+'/raw-uploads/'+RID);self.assertEqual(lookup.json(),r.json())
        listing=await self.request('GET',P+'/files');self.assertEqual(listing.json(),{'items':[file],'nextCursor':None})
        content=await self.request('GET',P+'/files/'+file['file_id']+'/content');self.assertEqual(content.content,RAW)
        self.assertEqual(content.headers['content-type'],'application/octet-stream');self.assertTrue(content.headers['content-disposition'].startswith('attachment;'))
        for value in [r,lookup,listing,content]:
            self.assertEqual(value.headers['cache-control'],'no-store');self.assertEqual(value.headers['x-content-type-options'],'nosniff');self.assertEqual(value.headers['referrer-policy'],'no-referrer')
        self.assertEqual(len(self.auth_calls),4);self.assertTrue(all(v is None for v in self.pool.released))

    async def test_replay_same_id_and_no_quota_recharge_conflicting_body409(self):
        first=await self.request('POST',P+'/raw-uploads',RAW)
        replay=await self.request('POST',P+'/raw-uploads',RAW)
        self.assertEqual(replay.status_code,200);self.assertEqual(first.json(),replay.json());self.assertEqual(len(self.pool.files),1)
        bad=await self.request('POST',P+'/raw-uploads',RAW+b'x');self.assertEqual(bad.status_code,409);self.assertEqual(bad.json(),{'error':{'code':'upload_request_conflict'}})

    async def test_foreign_project_request_and_file_pairs_are_uniform404(self):
        first=await self.request('POST',P+'/raw-uploads',RAW);fid=first.json()['file']['file_id']
        for path in [P+'/files',P+'/raw-uploads/'+RID,P+'/files/'+fid+'/content']:
            result=await self.request('GET',path,subject=B);self.assertEqual(result.status_code,404);self.assertEqual(result.json(),{'error':{'code':'project_not_found'}})
        base='/api/v1/projects/'+str(SECOND)
        for path in [base+'/raw-uploads/'+RID,base+'/files/'+fid+'/content']:
            result=await self.request('GET',path,subject=B);self.assertEqual(result.json(),{'error':{'code':'file_not_found'}})

    async def test_gateoff_auth_failure_and_no_preverified_identity(self):
        off=create_app(auth_transport=httpx.MockTransport(lambda r:httpx.Response(500)),pool=self.pool,anon_key_loader=lambda:ANON)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=off),base_url='http://offline.invalid') as c:
            r=await c.post(P+'/raw-uploads',content=RAW,headers=headers());self.assertEqual(r.status_code,503)
        await off.state.authority.close();self.assertEqual(self.auth_calls,[]);self.assertEqual(self.pool.connections,[])
        r=await self.client.get(P+'/files',headers={'X-User-Id':A});self.assertEqual(r.status_code,401);self.assertEqual(self.pool.connections,[])
        self.auth_status=401;r=await self.request('GET',P+'/files');self.assertEqual(r.status_code,401);self.assertEqual(self.pool.connections,[])

    async def test_filename_header_compression_size_checksum_and_EOF_rejections(self):
        variations=[({'X-Cyomics-Filename':base64.urlsafe_b64encode(v.encode()).decode().rstrip('=')},400) for v in ['../x.txt','x\\y.txt','x\n.txt','a.tsv','x'*252+'.txt']]
        variations += [({'Content-Encoding':'gzip'},415),({'Content-Type':'text/plain'},415),({'X-Cyomics-File-Bytes':'01'},413),({'X-Cyomics-File-Bytes':str(MAX_BYTES+1)},413),({'X-Cyomics-File-Bytes':str(len(RAW)+1)},400),({'X-Cyomics-File-Sha256':'0'*64},400),({'Idempotency-Key':RID.upper()},400),({'X-Cyomics-Filename':'YQ=='},400)]
        # Use a UUID containing letters for the noncanonical-uppercase case.
        variations[-2]=({'Idempotency-Key':'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'.upper()},400)
        for extra,status in variations:
            r=await self.request('POST',P+'/raw-uploads',RAW,extra=extra);self.assertEqual(r.status_code,status,(extra,r.text))
        self.assertEqual(self.pool.files,[])

    async def test_bad_internal_budget_hints_use_only_raw_error_contract_before_auth(self):
        for method,path in [('POST',P+'/raw-uploads'),('GET',P+'/raw-uploads/'+RID),
                            ('GET',P+'/files'),('GET',P+'/files/'+RID+'/content')]:
            r=await self.request(method,path,extra={'X-Cyomics-Remaining-Ms':'not-a-number'})
            self.assertEqual((r.status_code,r.json()),(400,{'error':{'code':'invalid_upload_query'}}))
        self.assertEqual(self.auth_calls,[]);self.assertEqual(self.pool.connections,[])

    async def test_legacy_auth_pool_faults_and_unapproved_status_code_pairs_are_normalized(self):
        self.auth_status=503
        r=await self.request('GET',P+'/files')
        self.assertEqual((r.status_code,r.json()),(503,{'error':{'code':'uploads_unavailable'}}))
        self.auth_status=200
        async def unavailable_pool():raise Fault(503,'projects_unavailable')
        with patch.object(self.pool,'getconn',unavailable_pool):
            r=await self.request('GET',P+'/files')
        self.assertEqual((r.status_code,r.json()),(503,{'error':{'code':'uploads_unavailable'}}))
        async def invalid_pair(*args):raise Fault(400,'uploads_unavailable')
        with patch.object(self.app.state.authority,'verify',invalid_pair):
            r=await self.request('GET',P+'/files')
        self.assertEqual((r.status_code,r.json()),(503,{'error':{'code':'uploads_unavailable'}}))

    async def test_exact_path_method_query_and_metadata_body_rejected(self):
        for method,path in [('GET',P+'/raw-uploads'),('POST',P+'/files'),('GET',P+'/files/'),('GET',P+'/files?x=1'),('GET',P.replace('444','%34%34%34',1)+'/files')]:
            r=await self.request(method,path);self.assertIn(r.status_code,[400,404,405]);self.assertNotEqual(r.status_code,200)
        r=await self.request('GET',P+'/files',b'x');self.assertEqual(r.status_code,400)

    async def test_maximum_raw_bytes_accepted_and_oversize_rejected(self):
        raw=b'x'*MAX_BYTES;r=await self.request('POST',P+'/raw-uploads',raw);self.assertEqual(r.status_code,201,r.text)
        r=await self.request('POST',P+'/raw-uploads',raw+b'x',extra={'Idempotency-Key':str(uuid4())});self.assertEqual(r.status_code,413)

    async def test_committed_but_lost_response_unknown_lookup_recovers_without_retry(self):
        self.pool.commit_lost=True
        # Set only for the writing transaction, after the read-only owner precheck.
        original=self.pool.getconn
        async def acquire():
            c=await original();old=c.execute
            async def execute(sql,params=()):
                if sql=='COMMIT' and not c.pending:
                    flag=self.pool.commit_lost;self.pool.commit_lost=False
                    try:return await old(sql,params)
                    finally:self.pool.commit_lost=flag
                return await old(sql,params)
            c.execute=execute;return c
        self.pool.getconn=acquire
        r=await self.request('POST',P+'/raw-uploads',RAW);self.assertEqual(r.status_code,504,r.text);self.assertEqual(r.json(),{'error':{'code':'upload_result_unknown'}})
        self.assertEqual(sum(sql.startswith('INSERT INTO') for sql,_ in self.pool.calls),1)
        lookup=await self.request('GET',P+'/raw-uploads/'+RID);self.assertEqual(lookup.status_code,200);self.assertEqual(len(self.pool.files),1)

    async def test_known_rollback_is_unavailable_not_fake_stored(self):
        self.pool.commit_rejected=True;r=await self.request('POST',P+'/raw-uploads',RAW);self.assertEqual(r.status_code,503);self.assertEqual(self.pool.files,[])

    async def test_corrupt_stored_content_never_reaches_authorized_download(self):
        r=await self.request('POST',P+'/raw-uploads',RAW);fid=r.json()['file']['file_id'];row=self.pool.files[0];self.pool.files[0]=(*row[:9],b'corrupt')
        r=await self.request('GET',P+'/files/'+fid+'/content');self.assertEqual(r.status_code,503);self.assertNotIn(b'corrupt',r.content)

    async def test_rate_limit_and_bounded_project_file_list(self):
        for i in range(4):
            r=await self.request('POST',P+'/raw-uploads',RAW,extra={'Idempotency-Key':str(uuid4())});self.assertEqual(r.status_code,201)
        r=await self.request('POST',P+'/raw-uploads',RAW,extra={'Idempotency-Key':str(uuid4())});self.assertEqual(r.status_code,413);self.assertEqual(r.json()['error']['code'],'upload_quota_exceeded')
        for _ in range(6):r=await self.request('POST',P+'/raw-uploads',RAW)
        self.assertEqual(r.status_code,429);self.assertEqual(r.json()['error']['code'],'upload_rate_limited')
        r=await self.request('GET',P+'/files');self.assertEqual(len(r.json()['items']),4)

    async def test_duplicate_headers_and_token_lifetime_fence_before_any_write(self):
        values=[('Authorization','Bearer '+token()),*headers().items(),('X-Cyomics-Filename',headers()['X-Cyomics-Filename'])]
        r=await self.client.post(P+'/raw-uploads',content=RAW,headers=values);self.assertEqual(r.status_code,400);self.assertEqual(self.pool.files,[])
        import cyomics_projects.raw_uploads as raw_module
        from types import SimpleNamespace
        fake=SimpleNamespace(time=lambda:time.time()+1000,monotonic=time.monotonic)
        with patch.object(raw_module,'time',fake):
            r=await self.request('POST',P+'/raw-uploads',RAW)
        self.assertEqual(r.status_code,401);self.assertEqual(self.pool.files,[])

    async def test_same_subject_concurrency_is_busy_without_second_insert(self):
        self.pool.lock_pause=asyncio.Event()
        pending=asyncio.create_task(self.request('POST',P+'/raw-uploads',RAW));await self.pool.lock_entered.wait()
        busy=await self.request('POST',P+'/raw-uploads',RAW)
        self.assertEqual((busy.status_code,busy.json()['error']['code']),(429,'upload_busy'))
        self.pool.lock_pause.set();self.assertEqual((await pending).status_code,201);self.assertEqual(len(self.pool.files),1)

    async def test_disconnect_during_body_does_not_write_or_emit_late_success(self):
        scope={'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'POST','scheme':'http',
               'path':P+'/raw-uploads','raw_path':(P+'/raw-uploads').encode(),'query_string':b'',
               'root_path':'','server':('offline.invalid',80),'client':('127.0.0.1',123),
               'headers':[(k.lower().encode(),v.encode()) for k,v in {**headers(),'Authorization':'Bearer '+token()}.items()]}
        events=asyncio.Queue();events.put_nowait({'type':'http.request','body':RAW[:3],'more_body':True});events.put_nowait({'type':'http.disconnect'})
        sent=[]
        await self.app(scope,events.get,lambda event:self._capture(sent,event))
        self.assertEqual(self.pool.files,[]);self.assertFalse(any(e.get('status') in (200,201) for e in sent));await asyncio.sleep(0)

    @staticmethod
    async def _capture(sent,event):sent.append(event)

class RepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_nonblocking_lock_serializes_same_subject_before_quota(self):
        pool=FilePool();pool.lock_pause=asyncio.Event();repo=UploadRepository(pool)
        values={'request_id':RID,'filename':'raw.txt','format':'proteinGroups_txt','bytes':len(RAW),'sha256':hashlib.sha256(RAW).hexdigest()}
        first=asyncio.create_task(repo.save(A,str(PROJECT),values,RAW,upload_budget()));await pool.lock_entered.wait()
        with self.assertRaises(Fault) as caught:await repo.save(A,str(PROJECT),values,RAW,upload_budget())
        self.assertEqual((caught.exception.status,caught.exception.code),(429,'upload_busy'));pool.lock_pause.set();self.assertEqual((await first)[0],201)
        calls=[sql for sql,_ in pool.calls];lock=next(i for i,s in enumerate(calls) if 'pg_try_advisory' in s);count=next(i for i,s in enumerate(calls) if s.startswith('SELECT count(*)'));self.assertLess(lock,count);self.assertEqual(len(pool.files),1);pool.close()

    async def test_project_and_owner_byte_quotas_and_owner_file_quota(self):
        values={'request_id':RID,'filename':'raw.txt','format':'proteinGroups_txt','bytes':len(RAW),'sha256':hashlib.sha256(RAW).hexdigest()}
        for mode in ['project_bytes','owner_bytes','owner_count']:
            pool=FilePool();other=UUID('77777777-7777-4777-8777-777777777777');third=UUID('88888888-8888-4888-8888-888888888888');pool.projects[other]=A;pool.projects[third]=A
            if mode=='project_bytes':seeds=[(PROJECT,10*1024*1024)]*3;raw=b'x'*(3*1024*1024)
            elif mode=='owner_bytes':seeds=[(PROJECT,9*1024*1024)]*3+[(other,9*1024*1024)]*4;raw=b'x'*(2*1024*1024)
            else:seeds=[(other,1)]*4+[(third,1)]*4;raw=RAW
            pool.files=[(uuid4(),pid,uuid4(),'seed.txt','proteinGroups_txt',size,'0'*64,'stored',NOW,b'') for pid,size in seeds]
            value={**values,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
            with self.assertRaises(Fault) as caught:await UploadRepository(pool).save(A,str(PROJECT),value,raw,upload_budget())
            self.assertEqual(caught.exception.code,'upload_quota_exceeded');self.assertFalse(any(sql.startswith('INSERT') for sql,_ in pool.calls));pool.close()

class RouteBudgetTests(unittest.TestCase):
    def test_only_two_exact_long_routes_accept_long_remaining(self):
        deadline=str(int(time.time()*1000)+60000).encode()
        for method,path,long in [('POST',P+'/raw-uploads',True),('GET',P+'/files/'+RID+'/content',True),('GET',P+'/files',False),('GET',P+'/raw-uploads/'+RID,False),('GET','/api/v1/projects',False),('GET',P+'/raw-uploads',False),('POST',P+'/files/'+RID+'/content',False),('POST',P+'/raw-uploads/',False),('POST',P.replace('444','%34%34%34',1)+'/raw-uploads',False)]:
            scope={'method':method,'path':path,'raw_path':path.encode(),'query_string':b'','headers':[(b'x-cyomics-remaining-ms',b'55000'),(b'x-cyomics-deadline-unix-ms',deadline)]}
            if long:self.assertLessEqual(entry_budget(scope).remaining(),50)
            else:
                with self.assertRaises(Fault):entry_budget(scope)
            scope['headers']=[];self.assertLessEqual(entry_budget(scope).remaining(),50 if long else 9)
        scope={'method':'POST','path':P+'/raw-uploads','raw_path':(P+'/raw-uploads').encode(),'query_string':b'x=1','headers':[]};self.assertIsNone(classify(scope));self.assertLessEqual(entry_budget(scope).remaining(),9)

    def test_migration_source_only_rls_and_narrow_grants(self):
        sql=(Path(__file__).parents[1]/'migrations/003_private_raw_uploads.sql').read_text()
        self.assertIn('FORCE ROW LEVEL SECURITY',sql);self.assertIn('WITH CHECK',sql);self.assertIn('current_user_id()',sql);self.assertIn('UNIQUE (project_id,request_id)',sql)
        statements='\n'.join(line for line in sql.splitlines() if not line.lstrip().startswith('--'))
        self.assertNotRegex(statements,r'CREATE\s+(ROLE|EXTENSION)|SECURITY\s+DEFINER|GRANT\s+(UPDATE|DELETE|ALL)')
        self.assertIn('GRANT INSERT (project_id,request_id,filename,format,bytes,sha256,content)',sql)
