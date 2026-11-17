"""Synthetic fixtures; fake DB/Auth boundaries. No sockets, services or DB calls."""
import asyncio
import base64
import time
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import types
import unittest
from uuid import UUID, uuid4

TASK=Path(__file__).resolve().parents[1]
BASE=Path(os.environ.get('CYOMICS_TEST_BASELINE',str(TASK/'cyomics_projects')))
MODEL=Path(os.environ.get('CYOMICS_TEST_MODEL',str(BASE)))
# Explicit local stand-ins only when the real dependency is unavailable.
# They cannot open connections; real-driver verification is a separate gate.
FAKE_DEPENDENCIES=[]
if importlib.util.find_spec('psycopg') is None:
    from enum import IntEnum
    pg=types.ModuleType('psycopg');pq=types.ModuleType('psycopg.pq')
    class TS(IntEnum): IDLE=0; ACTIVE=1; INTRANS=2; INERROR=3; UNKNOWN=4
    pq.TransactionStatus=TS
    class NoConnection:
        @staticmethod
        async def connect(*a,**kw): raise AssertionError('NETWORK FORBIDDEN')
    pg.AsyncConnection=NoConnection;pg.pq=pq
    sys.modules.update({'psycopg':pg,'psycopg.pq':pq});FAKE_DEPENDENCIES.append('psycopg')
if importlib.util.find_spec('httpx') is None:
    hx=types.ModuleType('httpx')
    class NoClient:
        def __init__(self,*a,**kw): raise AssertionError('NETWORK FORBIDDEN')
    hx.AsyncClient=hx.AsyncHTTPTransport=NoClient
    sys.modules['httpx']=hx;FAKE_DEPENDENCIES.append('httpx')
sys.path.insert(0, str(BASE.parent))
from cyomics_projects.sample_design import new_draft, bind_trusted_source
from cyomics_projects.sample_persistence import *
from cyomics_projects.sample_repository import SampleDesignRepository
from cyomics_projects.sample_http import SampleDesigns, route_input, validate_headers, error_response
from cyomics_projects.settings import Budget, Fault
from cyomics_projects.import_persistence import configuration, canonical_json, CONFIG_MAX_BYTES, build_candidate, StoredRaw, trusted_source as raw_source, version_key
from psycopg.pq import TransactionStatus as T

OWNER='11111111-1111-4111-8111-111111111111'
OTHER='22222222-2222-4222-8222-222222222222'
PROJECT='33333333-3333-4333-8333-333333333333'
FILE='44444444-4444-4444-8444-444444444444'
RAW=b'Protein IDs\tIntensity s\nP001\t1\n'
RAW_HASH=hashlib.sha256(RAW).hexdigest()
CONFIG=canonical_json(configuration(),CONFIG_MAX_BYTES)
DATASET=version_key(FILE,CONFIG)
SOURCE=raw_source(StoredRaw(FILE,PROJECT,'proteinGroups_txt',len(RAW),RAW_HASH,'stored',RAW),PROJECT,FILE,lambda:None)
CANDIDATE=build_candidate(StoredRaw(FILE,PROJECT,'proteinGroups_txt',len(RAW),RAW_HASH,'stored',RAW),SOURCE,CONFIG,DATASET,lambda:None,lambda:False)
STAMP=datetime(2026,11,17,tzinfo=timezone.utc)
DATASET_ROW=(UUID(DATASET),UUID(PROJECT),UUID(FILE),'protein_groups_txt_v1',sha(CONFIG),CONFIG,len(RAW),RAW_HASH,'raw_backed',False,CANDIDATE.report_json,CANDIDATE.report_sha256,STAMP)
TRUSTED=trusted_source(DATASET_ROW,PROJECT,DATASET)
DESIGN=new_draft(TRUSTED,'Intensity').design.to_payload()

def body(head=None, request=None):return {'schemaVersion':1,'request_id':request or str(uuid4()),'expected_head_version_id':head,'design':json.loads(json.dumps(DESIGN))}

class Cursor:
    def __init__(self,row):self.row=row
    async def fetchone(self):return self.row

class Connection:
    def __init__(self):
        self.closed=False;self.pgconn=self;self.transaction_status=T.IDLE;self.rows=[];self.pending=[];self.subject=None;self.sql=[];self.bad_report=False;self.missing=False;self.commit_error=False;self.cancel_insert=False
    def finish(self):self.closed=True;self.transaction_status=T.UNKNOWN
    async def execute(self,sql,params=()):
        self.sql.append(sql)
        if sql.startswith('BEGIN'):self.transaction_status=T.INTRANS;return Cursor(None)
        if sql=='COMMIT':
            self.rows+=self.pending;self.pending=[];self.transaction_status=T.IDLE
            if self.commit_error:raise OSError('synthetic lost commit receipt')
            return Cursor(None)
        if sql=='ROLLBACK':self.pending=[];self.transaction_status=T.IDLE;return Cursor(None)
        if "set_config('app.user_id'" in sql:self.subject=params[0]
        if 'FROM cyomics_app.projects' in sql:return Cursor((UUID(PROJECT),) if self.subject==OWNER and str(params[1])==PROJECT else None)
        if 'FROM cyomics_app.dataset_versions' in sql:
            row=list(DATASET_ROW)
            if self.bad_report:row[10]='{}'
            return Cursor(tuple(row) if not self.missing and str(params[0])==PROJECT and str(params[1])==DATASET else None)
        if 'FROM cyomics_app.sample_design_versions' in sql:
            rows=[r for r in self.rows+self.pending if r[1:4]==tuple(params[:3])]
            if 'AND request_id=' in sql:rows=[r for r in rows if r[6]==params[3]]
            rows.sort(key=lambda r:r[5],reverse=True)
            return Cursor(self.record(rows[0]) if rows else None)
        if sql.startswith('INSERT INTO cyomics_app.sample_design_versions'):
            if self.cancel_insert:raise asyncio.CancelledError()
            self.pending.append(tuple(params));return Cursor(self.record(params))
        return Cursor(None)
    def record(self,p):return (p[0],p[4],p[5],p[6],p[7],p[8],p[9],STAMP,p[10],p[11])

class Pool:
    def __init__(self):self.conn=Connection();self.releases=0
    async def getconn(self):return self.conn
    def release(self,conn):self.releases+=1

class RepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):self.pool=Pool();self.repo=SampleDesignRepository(self.pool)
    async def save(self,p=None,owner=OWNER,family='Intensity'):
        return await self.repo.save(owner,PROJECT,DATASET,family,p or body(),Budget())
    async def assert_fault(self,code,op):
        with self.assertRaises(Fault) as cm:await op
        self.assertEqual(cm.exception.code,code)
    async def test_no_record(self):
        self.assertIsNone((await self.repo.read(OWNER,PROJECT,DATASET,'Intensity',Budget()))['record'])
    async def test_append_history_replay(self):
        b=body();first=await self.save(b);head=first['record']['sample_design_version_id'];second=await self.save(body(head));
        self.assertEqual(second['record']['revision'],2)
        self.assertEqual(first,await self.save(b))
        self.assertEqual(first,await self.repo.read(OWNER,PROJECT,DATASET,'Intensity',Budget(),b['request_id']))
        self.assertEqual(second,await self.repo.read(OWNER,PROJECT,DATASET,'Intensity',Budget()))
        self.assertEqual(len(self.pool.conn.rows),2)
        self.assertTrue(any('pg_advisory_xact_lock' in sql for sql in self.pool.conn.sql))
    async def test_request_conflict_and_head_conflict(self):
        b=body();await self.save(b);b['design']['rows'][0]['group']='changed'
        await self.assert_fault('sample_design_request_conflict',self.save(b))
        await self.assert_fault('sample_design_head_conflict',self.save(body()))
    async def test_owner_dataset_family(self):
        await self.assert_fault('dataset_not_found',self.save(owner=OTHER))
        self.pool.conn.missing=True;await self.assert_fault('dataset_not_found',self.save())
        self.pool.conn.missing=False;await self.assert_fault('invalid_sample_design',self.save(family='LFQ intensity'))
        self.assertEqual(self.pool.conn.rows,[])
    async def test_invalid_report_and_request_lookup(self):
        self.pool.conn.bad_report=True;await self.assert_fault('sample_design_persisted_invalid',self.save())
        self.pool.conn.bad_report=False
        await self.assert_fault('sample_design_not_found',self.repo.read(OWNER,PROJECT,DATASET,'Intensity',Budget(),str(uuid4())))
    async def test_100_limit(self):
        head=None
        for i in range(100):head=(await self.save(body(head)))['record']['sample_design_version_id']
        await self.assert_fault('sample_design_version_limit',self.save(body(head)))
        self.assertEqual(len(self.pool.conn.rows),100)
    async def test_cancel_before_commit(self):
        self.pool.conn.cancel_insert=True
        with self.assertRaises(asyncio.CancelledError):await self.save()
        self.assertEqual(self.pool.conn.rows,[]);self.assertIn('ROLLBACK',self.pool.conn.sql)
    async def test_timeout_acquiring_no_write(self):
        class BlockedPool(Pool):
            async def getconn(self):await asyncio.Future()
        pool=BlockedPool();repo=SampleDesignRepository(pool)
        await self.assert_fault('sample_design_timeout',repo.save(OWNER,PROJECT,DATASET,'Intensity',body(),Budget(.001)))
        self.assertEqual(pool.conn.rows,[])
    async def test_unknown_commit_no_retry(self):
        self.pool.conn.commit_error=True
        await self.assert_fault('sample_design_result_unknown',self.save())
        self.assertEqual(self.pool.conn.sql.count('COMMIT'),1);self.assertEqual(len(self.pool.conn.rows),1)
    async def test_pin_mismatch(self):
        await self.save();r=list(self.pool.conn.rows[0]);r[-1]='0'*64;self.pool.conn.rows[0]=tuple(r)
        await self.assert_fault('sample_design_persisted_invalid',self.repo.read(OWNER,PROJECT,DATASET,'Intensity',Budget()))

class DocumentTests(unittest.TestCase):
    def test_canonical_integer_unicode(self):
        self.assertEqual(canonical({'z':1.0,'a':'叶'}),'{"a":"叶","z":1}')
        for value in (float('nan'),1.1,9007199254740992,'\ud800'):
            with self.assertRaises((ValueError,UnicodeError)):canonical(value)
    def test_strict_invalid_wrapper(self):
        for change in ({'schemaVersion':True},{'source':{}},{'request_id':'bad'}):
            b=body();b.update(change)
            with self.assertRaises(Fault) as cm:request_input(b,TRUSTED,'Intensity')
            self.assertEqual((cm.exception.status,cm.exception.code),(400,'invalid_sample_design_request'))
    def test_error_codes_preserved(self):
        for status,code in ((409,'sample_design_head_conflict'),(409,'sample_design_request_conflict'),(422,'sample_design_version_limit')):
            response=error_response(Fault(status,code));self.assertEqual(response.status_code,status);self.assertIn(code.encode(),response.body)

TOKEN='.'.join(base64.urlsafe_b64encode(json.dumps(x).encode()).decode().rstrip('=') for x in ({'alg':'HS256'},{'exp':time.time()+300})) + '.eA'

class FakeAuthority:
    calls=0
    async def verify(self,request,budget):
        self.calls+=1
        if request.headers.get('authorization')!='Bearer '+TOKEN:raise Fault(401,'unauthenticated')
        return OWNER

class HttpTests(unittest.IsolatedAsyncioTestCase):
    def scope(self,method='GET',path=None,extra=()):
        p=path or '/api/v1/datasets/'+DATASET+'/sample-designs'
        return {'type':'http','method':method,'path':p,'raw_path':p.encode(),'root_path':'','query_string':('project_id='+PROJECT+'&quant_family=intensity').encode(),
                'headers':[(b'host',b'127.0.0.1:57430'),(b'authorization',('Bearer '+TOKEN).encode())]+list(extra)}
    async def invoke(self,handler,scope,raw=b''):
        q=asyncio.Queue();await q.put({'type':'http.request','body':raw,'more_body':False});output=[]
        async def send(m):output.append(m)
        await handler(scope,q.get,send)
        return output
    async def test_default_off_unknown_path(self):
        a=FakeAuthority();h=SampleDesigns(a,Pool())
        self.assertEqual((await self.invoke(h,self.scope()))[0]['status'],503)
        self.assertEqual((await self.invoke(h,self.scope(path='/api/v1/datasets/nope')))[0]['status'],404)
        self.assertEqual(a.calls,0)
    async def test_headers_caps(self):
        h=SampleDesigns(FakeAuthority(),Pool(),enabled=True)
        s=self.scope('POST',extra=((b'origin',b'http://127.0.0.1:4206'),(b'x-cyomics-request',b'1'),(b'content-type',b'application/json')))
        bad=dict(s,headers=s['headers']+[(b'content-length',b'131073')])
        self.assertEqual((await self.invoke(h,bad))[0]['status'],413)
        bad=dict(s,headers=s['headers']+[(b'x-user-id',OWNER.encode())])
        self.assertEqual((await self.invoke(h,bad))[0]['status'],403)
        self.assertEqual((await self.invoke(h,s,b'x'*131073))[0]['status'],413)
    async def test_save_reload_security_headers(self):
        a=FakeAuthority();h=SampleDesigns(a,Pool(),enabled=True)
        s=self.scope('POST',extra=((b'origin',b'http://127.0.0.1:4206'),(b'x-cyomics-request',b'1'),(b'content-type',b'application/json')))
        out=await self.invoke(h,s,json.dumps(body()).encode())
        self.assertEqual(out[0]['status'],200)
        saved=json.loads(out[1]['body'])
        reloaded=await self.invoke(h,self.scope())
        self.assertEqual(json.loads(reloaded[1]['body']),saved)
        self.assertEqual(a.calls,2)
        for pair in ((b'cache-control',b'no-store'),(b'x-content-type-options',b'nosniff'),(b'referrer-policy',b'no-referrer')):
            self.assertIn(pair,out[0]['headers'])
    async def test_error_classification(self):
        h=SampleDesigns(FakeAuthority(),Pool(),enabled=True)
        s=self.scope('POST',extra=((b'origin',b'http://127.0.0.1:4206'),(b'x-cyomics-request',b'1'),(b'content-type',b'application/json')))
        for raw,status,code in ((b'{"schemaVersion":1,"schemaVersion":1}',400,'invalid_sample_design_request'),(json.dumps(dict(body(),request_id='bad')).encode(),400,'invalid_sample_design_request')):
            out=await self.invoke(h,s,raw)
            self.assertEqual(out[0]['status'],status);self.assertEqual(json.loads(out[1]['body'])['error']['code'],code)
        b=body();b['design']['rows'][0]['column_ordinal']=99
        out=await self.invoke(h,s,json.dumps(b).encode());self.assertEqual(out[0]['status'],422)
        self.assertEqual(json.loads(out[1]['body'])['error']['code'],'invalid_sample_design')
        class Damaged:
            async def read(self,*args):return {'record':None}
        h.repository=Damaged()
        out=await self.invoke(h,self.scope());self.assertEqual(out[0]['status'],503)
        self.assertEqual(json.loads(out[1]['body'])['error']['code'],'sample_design_persisted_invalid')
    async def test_response_cap_and_wrong_body_length(self):
        h=SampleDesigns(FakeAuthority(),Pool(),enabled=True)
        class Oversized:
            async def read(self,*args):return {'x':'x'*MAX_RESPONSE}
        h.repository=Oversized()
        self.assertEqual((await self.invoke(h,self.scope()))[0]['status'],503)
        s=self.scope('POST',extra=((b'origin',b'http://127.0.0.1:4206'),(b'x-cyomics-request',b'1'),(b'content-type',b'application/json'),(b'content-length',b'2')))
        self.assertEqual((await self.invoke(h,s,b'{}x'))[0]['status'],400)
    async def test_disconnect_cancels_worker(self):
        h=SampleDesigns(FakeAuthority(),Pool(),enabled=True);output=[]
        async def receive():return {'type':'http.disconnect'}
        async def send(value):output.append(value)
        await h(self.scope(),receive,send)
        self.assertEqual(output,[])
    async def test_authentication_required(self):
        a=FakeAuthority();h=SampleDesigns(a,Pool(),enabled=True);s=self.scope();s['headers']=s['headers'][:1]
        out=await self.invoke(h,s)
        self.assertEqual(out[0]['status'],401);self.assertEqual(a.calls,1)
        self.assertIn((b'cache-control',b'no-store'),out[0]['headers'])

class WiringTests(unittest.TestCase):
    def test_default_off_factory_and_authority_wiring(self):
        import inspect
        from cyomics_projects.app import create_app, ProjectBoundary
        self.assertIs(inspect.signature(create_app).parameters['sample_designs_enabled'].default,False)
        source=inspect.getsource(create_app)
        self.assertIn('authority = Authority(auth_transport, anon_key_loader)',source)
        self.assertIn('SampleDesigns(authority, pool, enabled=sample_designs_enabled)',source)
        self.assertIn('samples=samples',source)
    def test_sql_source_boundary_only(self):
        sql='\n'.join(line for line in (TASK/'migrations/006_private_sample_designs.sql').read_text(encoding='utf-8').splitlines() if not line.lstrip().startswith('--'))
        for item in ('FORCE ROW LEVEL SECURITY','ENABLE ROW LEVEL SECURITY','current_database()',"'businessdev'",'server_version_num','GRANT SELECT','GRANT INSERT','parent_version_id','validator_sha256'):
            self.assertIn(item,sql)
        for item in ('GRANT UPDATE','GRANT DELETE','GRANT ALL','SECURITY DEFINER','CREATE TRIGGER','ALTER TABLE cyomics_app.dataset_versions'):
            self.assertNotIn(item,sql)

@unittest.skipIf('httpx' in FAKE_DEPENDENCIES,'real httpx unavailable locally; run with existing audit venv')
class AuthorityChainTests(unittest.IsolatedAsyncioTestCase):
    scope=HttpTests.scope
    invoke=HttpTests.invoke
    async def test_real_authority_offline_transport(self):
        import httpx
        from cyomics_projects.identity import Authority
        from cyomics_projects.settings import EXPECTED_ISSUER
        now=time.time()
        def token(payload):
            return '.'.join(base64.urlsafe_b64encode(json.dumps(x).encode()).decode().rstrip('=') for x in ({'alg':'HS256'},payload)) + '.eA'
        anon=token({'role':'anon'})
        bearer=token({'sub':OWNER,'iss':EXPECTED_ISSUER,'aud':'authenticated','role':'authenticated','is_anonymous':False,'iat':now-1,'exp':now+300})
        calls=[]
        def respond(request):
            calls.append(request)
            self.assertEqual(str(request.url),'http://127.0.0.1:57421/auth/v1/user')
            self.assertEqual(request.headers['authorization'],'Bearer '+bearer)
            return httpx.Response(200,json={'id':OWNER,'aud':'authenticated','role':'authenticated','is_anonymous':False,'email':'fixture@example.test','email_confirmed_at':'2026-01-01T00:00:00Z'})
        authority=Authority(httpx.MockTransport(respond),lambda:anon)
        h=SampleDesigns(authority,Pool(),enabled=True)
        s=self.scope();s['headers']=[(b'host',b'127.0.0.1:57430'),(b'authorization',('Bearer '+bearer).encode())]
        try:
            for i in range(2):self.assertEqual((await self.invoke(h,s))[0]['status'],200)
            self.assertEqual(len(calls),2)
        finally:await authority.close()

if __name__=='__main__':
    print('Explicit missing-dependency stand-ins:',','.join(FAKE_DEPENDENCIES) or 'none')
    unittest.main(verbosity=2)
