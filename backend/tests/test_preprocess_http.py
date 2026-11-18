"""Offline full-app ASGI tests: real Authority with synthetic MockTransport /user.

No real authentication service, DB, file data, worker, R, pool or sockets.
"""
import asyncio
import base64
import copy
from datetime import datetime,timezone
import inspect
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch
from uuid import UUID

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import cyomics_projects
cyomics_projects.__path__.insert(0,str(Path(__file__).resolve().parents[1]/'cyomics_projects'))
import httpx
from cyomics_projects import app as app_source,preprocess_http as api,preprocess_repository as p
from cyomics_projects.settings import Budget,Fault,EXPECTED_ISSUER

OWNER='11111111-1111-4111-8111-111111111111'
OTHER='22222222-2222-4222-8222-222222222222'
PROJECT='33333333-3333-4333-8333-333333333333'
DATASET='44444444-4444-4444-8444-444444444444'
FILE='55555555-5555-4555-8555-555555555555'
RID='66666666-6666-4666-8666-666666666666'
JOB='77777777-7777-4777-8777-777777777777'
EXECUTION={'contract':'log2_local_source_proposal_v0.1','entry_kind':'native_R','r_version':'R version 4.6.1',
    **copy.deepcopy(p.FIXED_PINS),'runner':{'bytes':21566,'sha256':'b9731437092d34d9143242c4ea3ec68593ca13869b99e910adc30efeb60f5db8'},
    'limits':dict(p.LIMITS),'process_policy_sha256':p.POLICY_SHA256,'registered':False}
SOURCE={'project_id':PROJECT,'dataset_version_id':DATASET,'file_id':FILE,'source_bytes':61,'source_sha256':'a'*64,
    'parser_contract':'protein_groups_txt_v1','config_sha256':'b'*64,'report_sha256':'c'*64,
    'row_count':2,'column_count':3,'selected_columns_sha256':'d'*64}

def body(family='Intensity'):
    return p.canonical({'request_id':RID,'quant_family':family,'parameters':json.loads(
        p.contract.canonical_parameters({'contaminant':False,'reverse':False,'site_only':False}))}).encode()

def dto(raw=None):
    request=p.contract.request_input(raw or body());rh=p.scoped_request_hash(PROJECT,DATASET,request)
    row=[UUID(JOB),UUID(PROJECT),UUID(DATASET),UUID(FILE),UUID(OWNER),UUID(request.request_id),rh,
        p.binding_hash(rh,SOURCE,request,EXECUTION),p.canonical(SOURCE),request.quant_family,
        request.parameters_bytes.decode(),p.canonical(EXECUTION),'queued',datetime(2026,11,18,tzinfo=timezone.utc)]
    return p.PreprocessRepository()._decode(row,OWNER,PROJECT)

def token(payload):
    def enc(value):return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    return enc({'alg':'HS256'})+'.'+enc(payload)+'.eA'

class Auth:
    def __init__(self):
        self.calls=[]
        self.tokens={subject:token({'sub':subject,'iss':EXPECTED_ISSUER,'aud':'authenticated','role':'authenticated',
            'is_anonymous':False,'iat':time.time()-5,'exp':time.time()+300}) for subject in (OWNER,OTHER)}
    def transport(self):
        async def user(request):
            self.calls.append((request.method,str(request.url)))
            assert request.method=='GET' and str(request.url)=='http://127.0.0.1:57421/auth/v1/user'
            subject=next((s for s,t in self.tokens.items() if request.headers.get('authorization')=='Bearer '+t),None)
            if subject is None:return httpx.Response(401,json={})
            return httpx.Response(200,json={'id':subject,'aud':'authenticated','role':'authenticated',
                'is_anonymous':False,'email':'synthetic@example.invalid','email_confirmed_at':'2026-01-01T00:00:00Z'})
        return httpx.MockTransport(user)

class NoPool:
    def __init__(self):self.acquires=0
    async def getconn(self):
        self.acquires+=1
        raise AssertionError('REAL DB FORBIDDEN')
    def close(self):pass

class Repo:
    def __init__(self):self.calls=[];self.saved=None;self.error=None;self.mutate=None;self.flags=(True,True)
    async def call(self,kind,args):
        subject,project,*rest=args[:-1]
        self.calls.append((kind,subject,project,*rest))
        if subject!=OWNER or project!=PROJECT:raise Fault(404,'dataset_not_found')
        if self.error:raise self.error
        if kind=='enqueue':
            dataset,raw=rest
            if dataset!=DATASET:raise Fault(404,'dataset_not_found')
            value=dto(raw);created=self.saved is None
            if self.saved is not None and self.saved['request_sha256']!=value['request_sha256']:
                raise Fault(409,'preprocessing_request_conflict')
            self.saved=copy.deepcopy(value)
        else:
            if self.saved is None:raise Fault(404,'preprocessing_job_not_found')
            if (kind=='request' and rest!=[DATASET,RID]) or (kind=='job' and rest!=[JOB]):
                raise Fault(404,'preprocessing_job_not_found')
            value=copy.deepcopy(self.saved)
        if self.mutate:self.mutate(value)
        return p.QueueObservation(p.canonical(value),created,*self.flags) if kind=='enqueue' else value
    async def enqueue(self,*args):return await self.call('enqueue',args)
    async def read_request(self,*args):return await self.call('request',args)
    async def read_job(self,*args):return await self.call('job',args)

def scope(auth,method='GET',*,detail=False,subject=OWNER):
    path='/api/v1/preprocessing-jobs/'+JOB if detail else '/api/v1/datasets/'+DATASET+'/preprocessing-jobs'
    query='project_id='+PROJECT+('&request_id='+RID if method=='GET' and not detail else '')
    h=[(b'host',b'127.0.0.1:57430')]
    if subject is not None:h.append((b'authorization',('Bearer '+auth.tokens[subject]).encode()))
    if method=='POST':h.extend([(b'origin',b'http://127.0.0.1:4206'),(b'x-cyomics-request',b'1'),(b'content-type',b'application/json')])
    return {'type':'http','method':method,'path':path,'raw_path':path.encode(),'root_path':'',
        'query_string':query.encode(),'headers':h,'asgi':{'version':'3.0','spec_version':'2.3'},
        'http_version':'1.1','scheme':'http','server':('127.0.0.1',57430),'client':('127.0.0.1',1)}

async def invoke(app,s,raw=b'',receive=None):
    q=asyncio.Queue();q.put_nowait({'type':'http.request','body':raw,'more_body':False})
    out=[]
    async def send(message):out.append(message)
    await app(s,receive or q.get,send)
    return out

def status(out):return out[0]['status']
def value(out):return json.loads(out[-1]['body'])
def code(out):return value(out)['error']['code']

class HTTPTests(unittest.IsolatedAsyncioTestCase):
    def app(self,repo=None):
        auth=Auth();pool=NoPool()
        app=app_source.create_app(auth_transport=auth.transport(),pool=pool,
            anon_key_loader=lambda:token({'role':'anon'}),preprocessing_repository=repo)
        self.addAsyncCleanup(app.state.authority.close)
        return app,auth,pool

    async def test_default_off_authenticates_all_three_private_routes_without_demo_or_pool(self):
        app,auth,pool=self.app()
        for method,detail in [('POST',False),('GET',False),('GET',True)]:
            out=await invoke(app,scope(auth,method,detail=detail,subject=None),body() if method=='POST' else b'')
            self.assertEqual((status(out),code(out)),(401,'unauthenticated'))
            out=await invoke(app,scope(auth,method,detail=detail),body() if method=='POST' else b'')
            self.assertEqual((status(out),code(out)),(503,'preprocessing_unavailable'))
        self.assertEqual(len(auth.calls),3);self.assertEqual(pool.acquires,0)
        self.assertIsNone(inspect.signature(app_source.create_app).parameters['preprocessing_repository'].default)
        self.assertIs(app.state.preprocessing_jobs.authority,app.state.authority)

    async def test_create_replay_and_two_reads_direct_exact_queued_DTO(self):
        repo=Repo();app,auth,pool=self.app(repo)
        for method,detail,expected in [('POST',False,201),('POST',False,200),('GET',False,200),('GET',True,200)]:
            out=await invoke(app,scope(auth,method,detail=detail),body() if method=='POST' else b'')
            self.assertEqual(status(out),expected);self.assertEqual(value(out),repo.saved)
            self.assertEqual(set(value(out)),api.PUBLIC_KEYS);self.assertEqual(len(value(out)),16)
            self.assertFalse(any(value(out)[k] for k in ('computation_performed','worker_started','execution_registered')))
            self.assertEqual(dict(out[0]['headers'])[b'cache-control'],b'no-store')
        self.assertEqual([x[0] for x in repo.calls],['enqueue','enqueue','request','job'])
        self.assertTrue(all(x[1]==OWNER for x in repo.calls));self.assertEqual(len(auth.calls),4)
        self.assertEqual(pool.acquires,0);self.assertIs(app.state.preprocessing_jobs.repository,repo)

    async def test_cross_owner_missing_and_scope_conflict_have_fixed_denials(self):
        repo=Repo();repo.saved=dto();app,auth,_=self.app(repo)
        for method,detail in [('POST',False),('GET',False),('GET',True)]:
            out=await invoke(app,scope(auth,method,detail=detail,subject=OTHER),body() if method=='POST' else b'')
            self.assertEqual(status(out),404)
        changed=scope(auth);changed['query_string']=changed['query_string'].replace(PROJECT.encode(),OTHER.encode())
        self.assertEqual(status(await invoke(app,changed)),404)
        out=await invoke(app,scope(auth,'POST'),body('LFQ intensity'))
        self.assertEqual((status(out),code(out)),(409,'preprocessing_request_conflict'))
        self.assertEqual(repo.saved,dto())

    async def test_exact_request_contract_and_4KiB_streaming_boundary(self):
        repo=Repo();app,auth,_=self.app(repo)
        invalid=[b'{}',b'{"request_id":"x","request_id":"y"}',b'\xef\xbb\xbf'+body(),b'\xff']
        for key in ('owner_id','source','path','execution'):
            v=json.loads(body());v[key]='FORBIDDEN';invalid.append(json.dumps(v).encode())
        v=json.loads(body());v['parameters']['filter_flags']['reverse']=1;invalid.append(json.dumps(v).encode())
        for raw in invalid:self.assertEqual(status(await invoke(app,scope(auth,'POST'),raw)),400)
        self.assertEqual(repo.calls,[])
        padded=body()+b' '*(4096-len(body()))
        self.assertEqual(status(await invoke(app,scope(auth,'POST'),padded)),201)
        self.assertEqual(status(await invoke(app,scope(auth,'POST'),padded+b' ')),413)
        self.assertEqual(len(repo.calls),1)

    async def test_strict_path_query_method_no_redirects_or_generic_dispatch(self):
        repo=Repo();app,auth,_=self.app(repo)
        changes=[({'query_string':b''},400),({'query_string':('project_id='+PROJECT).encode()},400),
            ({'query_string':('project_id='+PROJECT+'&request_id='+RID+'&owner_id=x').encode()},400),
            ({'query_string':('project_id='+PROJECT+'&project_id='+PROJECT+'&request_id='+RID).encode()},400),
            ({'query_string':('%70roject_id='+PROJECT+'&request_id='+RID).encode()},400),
            ({'root_path':'/prefix'},404),({'method':'DELETE'},405)]
        for change,expected in changes:
            s=scope(auth);s.update(change);self.assertEqual(status(await invoke(app,s)),expected)
        original=scope(auth)['path']
        for path in (original+'/',original+'/extra',original.replace('/datasets/','//datasets/'),
                original.replace('preprocessing-jobs','%70reprocessing-jobs'),original.replace(DATASET,'bad-id')):
            s=scope(auth);s.update(path=path,raw_path=path.encode())
            self.assertEqual(status(await invoke(app,s)),404)
        self.assertEqual(repo.calls,[]);self.assertEqual(auth.calls,[])

    async def test_origin_media_duplicate_authorization_and_body_protocol(self):
        repo=Repo();app,auth,_=self.app(repo)
        for key,val,expected in [(b'origin',b'https://other.invalid',403),(b'cookie',b'x=y',403),
                (b'x-owner-id',OWNER.encode(),403),(b'content-type',b'text/plain',415),
                (b'content-length',b'4097',413)]:
            s=scope(auth,'POST');s['headers']=[x for x in s['headers'] if x[0]!=key]+[(key,val)]
            self.assertEqual(status(await invoke(app,s,body())),expected)
        s=scope(auth);s['headers'].append(s['headers'][1])
        self.assertEqual(status(await invoke(app,s)),401)
        self.assertEqual(status(await invoke(app,scope(auth),b'x')),400)
        s=scope(auth,'POST');s['headers'].extend([(b'content-length',b'1'),(b'transfer-encoding',b'chunked')])
        self.assertEqual(status(await invoke(app,s,body())),400)
        self.assertEqual(repo.calls,[])

    async def test_wrong_source_context_params_hashes_or_state_never_leak_as_queued(self):
        mutations=[lambda v:v['source'].update(project_id=OTHER),lambda v:v['source'].update(dataset_version_id=OTHER),
            lambda v:v['source'].update(owner_id=OTHER),lambda v:v['source'].update(source_sha256='bad'),
            lambda v:v.update(request_id=OTHER),lambda v:v.update(parameters_sha256='0'*64),
            lambda v:v.update(execution_sha256='/PRIVATE/path'),lambda v:v.update(status='succeeded'),
            lambda v:v.update(worker_started=0),lambda v:v.update(result={'path':'/PRIVATE/path'}),
            lambda v:v.update(quant_family='LFQ intensity')]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                repo=Repo();repo.saved=dto();repo.mutate=mutate;app,auth,_=self.app(repo)
                get=await invoke(app,scope(auth));post=await invoke(app,scope(auth,'POST'),body())
                self.assertEqual((status(get),code(get)),(503,'preprocessing_persisted_invalid'))
                self.assertEqual((status(post),code(post)),(504,'preprocessing_submission_unknown'))
                self.assertNotIn(b'PRIVATE',post[-1]['body']);self.assertEqual(len(repo.calls),2)
        repo=Repo();repo.saved=dto();repo.mutate=lambda v:v.update(job_id=OTHER);app,auth,_=self.app(repo)
        self.assertEqual(status(await invoke(app,scope(auth,detail=True))),503)

    async def test_observation_requires_confirmed_commit_and_teardown(self):
        for flags in ((False,True),(True,False),(1,True)):
            repo=Repo();repo.flags=flags;app,auth,_=self.app(repo)
            out=await invoke(app,scope(auth,'POST'),body())
            self.assertEqual((status(out),code(out)),(504,'preprocessing_submission_unknown'))
            self.assertEqual(len(repo.calls),1)

    async def test_repository_typed_errors_preserved_and_exception_text_cleaned(self):
        for http,error in [(404,'dataset_not_found'),(409,'preprocessing_request_conflict'),
                (422,'preprocessing_flags_unavailable'),(503,'preprocessing_unavailable'),(504,'preprocessing_submission_unknown')]:
            repo=Repo();repo.error=Fault(http,error);app,auth,_=self.app(repo)
            out=await invoke(app,scope(auth,'POST'),body())
            self.assertEqual((status(out),code(out)),(http,error));self.assertEqual(len(repo.calls),1)
        repo=Repo();repo.error=RuntimeError('PRIVATE credential or path');app,auth,_=self.app(repo)
        out=await invoke(app,scope(auth,'POST'),body())
        self.assertEqual((status(out),code(out)),(504,'preprocessing_submission_unknown'));self.assertNotIn(b'PRIVATE',out[-1]['body'])
        repo=Repo();repo.error=Fault([],{'PRIVATE':'unsafe error object'});app,auth,_=self.app(repo)
        out=await invoke(app,scope(auth))
        self.assertEqual((status(out),code(out)),(503,'preprocessing_unavailable'));self.assertNotIn(b'PRIVATE',out[-1]['body'])

    async def test_unknown_POST_once_then_manual_GET_reconciles_same_request(self):
        class CommitThenUnknown(Repo):
            async def enqueue(self,*args):
                await super().enqueue(*args)
                raise TimeoutError('PRIVATE COMMIT RECEIPT LOST')
        repo=CommitThenUnknown();app,auth,_=self.app(repo)
        out=await invoke(app,scope(auth,'POST'),body())
        self.assertEqual((status(out),code(out)),(504,'preprocessing_submission_unknown'))
        recovered=await invoke(app,scope(auth))
        self.assertEqual(status(recovered),200);self.assertEqual(value(recovered),repo.saved)
        self.assertEqual([x[0] for x in repo.calls],['enqueue','request']);self.assertEqual(len(auth.calls),2)

    async def test_expired_budget_does_not_verify_or_submit(self):
        repo=Repo();app,auth,_=self.app(repo);expired=Budget();expired.deadline=0
        with patch.object(api,'import_budget',return_value=expired):
            out=await invoke(app,scope(auth,'POST'),body())
        self.assertEqual((status(out),code(out)),(504,'preprocessing_timeout'))
        self.assertEqual(repo.calls,[]);self.assertEqual(auth.calls,[])

    async def test_disconnect_cancels_owned_request_without_private_response(self):
        entered=asyncio.Event();cancelled=asyncio.Event();out=[];q=asyncio.Queue()
        class Waiting(Repo):
            async def enqueue(self,*args):
                self.calls.append(('enqueue',args[0]));entered.set()
                try:await asyncio.Future()
                finally:cancelled.set()
        repo=Waiting();app,auth,_=self.app(repo)
        q.put_nowait({'type':'http.request','body':body(),'more_body':False})
        async def send(message):out.append(message)
        task=asyncio.create_task(app(scope(auth,'POST'),q.get,send))
        await asyncio.wait_for(entered.wait(),1);q.put_nowait({'type':'http.disconnect'})
        await asyncio.wait_for(task,1);await asyncio.wait_for(cancelled.wait(),1)
        self.assertEqual(out,[]);self.assertEqual(len(repo.calls),1)

    async def test_timed_out_cancellation_ignoring_repository_cannot_send_late_success(self):
        entered=asyncio.Event();release=asyncio.Event();returned=asyncio.Event()
        class Late(Repo):
            async def enqueue(self,*args):
                result=await super().enqueue(*args);entered.set()
                try:await release.wait()
                except asyncio.CancelledError:await release.wait()
                returned.set();return result
        repo=Late();app,auth,_=self.app(repo)
        with patch.object(api,'import_budget',side_effect=lambda *_:Budget(0.05)):
            task=asyncio.create_task(invoke(app,scope(auth,'POST'),body()))
            await asyncio.wait_for(entered.wait(),1);out=await asyncio.wait_for(task,1)
        self.assertEqual((status(out),code(out)),(504,'preprocessing_submission_unknown'))
        snapshot=copy.deepcopy(out);release.set();await asyncio.wait_for(returned.wait(),1);await asyncio.sleep(0)
        self.assertEqual(out,snapshot);self.assertEqual(len(repo.calls),1)

    async def test_GET_timeout_is_not_a_queued_or_completed_result(self):
        cancelled=asyncio.Event()
        class Waiting(Repo):
            async def read_request(self,*args):
                self.calls.append(('request',args[0]))
                try:await asyncio.Future()
                finally:cancelled.set()
        repo=Waiting();app,auth,_=self.app(repo)
        with patch.object(api,'import_budget',side_effect=lambda *_:Budget(0.05)):
            out=await invoke(app,scope(auth))
        self.assertEqual((status(out),code(out)),(504,'preprocessing_timeout'))
        await asyncio.wait_for(cancelled.wait(),1);self.assertEqual(len(repo.calls),1)

    async def test_post_watcher_error_is_unknown_not_a_repository_rejection(self):
        entered=asyncio.Event();out=[];q=asyncio.Queue()
        class Waiting(Repo):
            async def enqueue(self,*args):
                self.calls.append(('enqueue',args[0]));entered.set();await asyncio.Future()
        repo=Waiting();app,auth,_=self.app(repo)
        q.put_nowait({'type':'http.request','body':body(),'more_body':False})
        async def send(message):out.append(message)
        task=asyncio.create_task(app(scope(auth,'POST'),q.get,send))
        await asyncio.wait_for(entered.wait(),1)
        q.put_nowait({'type':'http.request','body':b'','more_body':False})
        await asyncio.wait_for(task,1)
        self.assertEqual((status(out),code(out)),(504,'preprocessing_submission_unknown'))
        self.assertEqual(len(repo.calls),1)

    async def test_dispatch_before_broad_samples_and_production_gate_unchanged(self):
        repo=Repo();repo.saved=dto();app,auth,_=self.app(repo)
        class Broad:
            calls=0
            def owns(self,s):return True
            async def __call__(self,*args):self.calls+=1
        broad=Broad()
        boundary=app_source.ProjectBoundary(None,samples=broad,preprocessing=app.state.preprocessing_jobs)
        self.assertEqual(status(await invoke(boundary,scope(auth))),200);self.assertEqual(broad.calls,0)
        with patch.object(app_source,'PreprocessingJobs',side_effect=AssertionError('gate must reject first')):
            with self.assertRaises(app_source.production_gate.__globals__['RuntimeNotApproved']):app_source.create_app()


if __name__=='__main__':unittest.main(verbosity=2)
