import asyncio
import base64
import json
import socket
import time
import unittest
from uuid import UUID
from unittest.mock import patch
import httpx
from cyomics_projects.app import create_app
from cyomics_projects.repository import Repository, FixedPool
from cyomics_projects.settings import Budget, Fault, RuntimeNotApproved, EXPECTED_ISSUER
from cyomics_projects.validation import encode_cursor, decode_cursor
from fakes import A, B, PROJECT, SECOND, NOW, Pool, row


def encoded(value):
    raw = value if isinstance(value,bytes) else json.dumps(value,separators=(',',':')).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def token(subject=A, **changes):
    now = int(time.time())
    claims = {'sub':subject,'iss':EXPECTED_ISSUER,'aud':'authenticated','role':'authenticated','iat':now-30,'exp':now+300,'is_anonymous':False}
    claims.update(changes)
    return encoded({'alg':'HS256','typ':'JWT'}) + '.' + encoded(claims) + '.' + encoded(b'offline-signature-only')


ANON = token(role='anon')  # Synthetic key for MockTransport, never a real key.


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.network_guard = patch.object(socket.socket,'connect',side_effect=AssertionError('Real socket prohibited in offline tests'))
        self.network_mock = self.network_guard.start()
        self.pool = Pool()
        self.auth_calls = []
        self.auth_status = 200
        self.user_changes = {}
        self.auth_pause = None
        self.auth_started = asyncio.Event()
        async def auth(request):
            self.auth_calls.append(request)
            self.auth_started.set()
            if self.auth_pause is not None:
                await self.auth_pause.wait()
            self.assertEqual(str(request.url),'http://127.0.0.1:57421/auth/v1/user')
            self.assertEqual(request.headers['apikey'],ANON)
            subject = json.loads(base64.urlsafe_b64decode(request.headers['authorization'][7:].split('.')[1]+'=='))['sub']
            user = {'id':subject,'aud':'authenticated','role':'authenticated','is_anonymous':False,'email':'security@example.invalid','email_confirmed_at':'2026-01-01T00:00:00Z'}
            user.update(self.user_changes)
            return httpx.Response(self.auth_status,json=user,headers={'location':'http://unapproved.invalid/user'})
        self.app = create_app(auth_transport=httpx.MockTransport(auth),pool=self.pool,anon_key_loader=lambda:ANON)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://offline.invalid')

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.app.state.authority.close()
        self.pool.close()
        self.assertEqual(self.network_mock.call_count,0)
        self.network_guard.stop()

    async def request(self, method='GET', path='/api/v1/projects', subject=A, bearer=None, **kwargs):
        headers = {'Authorization':'Bearer '+(bearer or token(subject))}
        headers.update(kwargs.pop('headers',{}))
        return await self.client.request(method,path,headers=headers,**kwargs)

    async def test_own_list_detail_and_same_connection_identity_cleanup(self):
        first = await self.request(headers={'X-User-Id':B})
        second = await self.request(subject=B)
        self.assertEqual(first.status_code,200)
        self.assertEqual(first.json()['items'][0]['project_id'],str(PROJECT))
        self.assertEqual(second.json()['items'][0]['project_id'],str(SECOND))
        self.assertEqual(set(first.json()),{'items','nextCursor'})
        self.assertEqual(set(first.json()['items'][0]),{'project_id','name','description','species','visibility','createdAt','updatedAt'})
        self.assertEqual(self.pool.subject_history,[A,B])
        self.assertEqual(self.pool.release_subjects,[None,None])
        foreign = await self.request(path='/api/v1/projects/'+str(PROJECT),subject=B)
        absent = await self.request(path='/api/v1/projects/66666666-6666-4666-8666-666666666666',subject=B)
        self.assertEqual(foreign.json(),absent.json())
        self.assertEqual(foreign.status_code,404)
        self.assertEqual(len(self.auth_calls),4)

    async def test_create_concurrent_workspace_no_update_and_server_id(self):
        self.pool.workspace_exists = False
        self.pool.concurrent_workspace = True
        response = await self.request('POST',json={'name':'  新项目  ','description':'\nnotes\t','species':'label'})
        self.assertEqual(response.status_code,201)
        self.assertEqual(response.json()['project']['project_id'],str(PROJECT))
        self.assertEqual(response.json()['project']['name'],'新项目')
        self.assertEqual(self.pool.workspace_reads,2)
        inserts = [(sql,params) for sql,params in self.pool.calls if sql.startswith('INSERT')]
        self.assertIn('ON CONFLICT (owner_id) DO NOTHING',inserts[0][0])
        self.assertFalse(any('DO UPDATE' in sql for sql,_ in self.pool.calls))
        self.assertNotIn('新项目',inserts[-1][0])
        self.assertEqual(inserts[-1][1][-3: ],('新项目','notes','label'))
        self.assertEqual(self.pool.release_subjects,[None])

    async def test_strict_json_and_deep_nesting_reject_before_checkout(self):
        bodies = [b'{"name":"a","name":"b"}',b'{"name":"a","owner_id":"x"}',b'{"name":"\\ud800"}',b'{"name":"\xff"}',b'[]',b'{"name":1}',b'{"name":'+b'['*1100+b'"x"'+b']'*1100+b'}']
        for body in bodies:
            with self.subTest(bodyLength=len(body)):
                response = await self.request('POST',content=body,headers={'Content-Type':'application/json'})
                self.assertEqual(response.status_code,400)
                self.assertEqual(response.json(),{'error':{'code':'invalid_project'}})
        self.assertEqual(self.pool.checkouts,0)
        too_large = await self.request('POST',content=b'x'*8193,headers={'Content-Type':'application/json'})
        self.assertEqual(too_large.status_code,413)

    async def test_no_positive_auth_cache_redirect_or_unverified_checkout(self):
        bearer = token()
        self.assertEqual((await self.request(bearer=bearer)).status_code,200)
        self.auth_status = 401
        self.assertEqual((await self.request(bearer=bearer)).status_code,401)
        self.auth_status = 302
        self.assertEqual((await self.request()).status_code,503)
        self.auth_status = 200
        self.user_changes = {'is_anonymous':True}
        self.assertEqual((await self.request()).status_code,401)
        self.user_changes = {}
        for claims in ({'iss':'http://attacker.invalid'},{'exp':True},{'iat':0,'exp':time.time()+1}):
            self.assertEqual((await self.request(bearer=token(**claims))).status_code,401)
        self.assertEqual(len(self.auth_calls),7)
        self.assertEqual(self.pool.checkouts,1)

    async def test_cursor_limit_plus_one_and_strict_queries(self):
        self.pool.rows[A] = [row(),row(SECOND)]
        response = await self.request(path='/api/v1/projects?limit=1')
        self.assertEqual(len(response.json()['items']),1)
        cursor = response.json()['nextCursor']
        self.assertEqual(decode_cursor(cursor)[0],NOW)
        self.assertEqual(str(decode_cursor(cursor)[1]),str(PROJECT))
        await self.request(path='/api/v1/projects?limit=1&cursor='+cursor)
        self.assertTrue(any('(created_at, project_id) < (%s, %s)' in sql for sql,_ in self.pool.calls))
        count = self.pool.checkouts
        bad_cursor = encoded(b'{"v":1,"v":1,"createdAt":"2026-11-16T01:02:03.456789Z","project_id":"'+str(PROJECT).encode()+b'"}')
        for query in ('limit=1&limit=2','unknown=x','cursor='+cursor+'=','cursor='+bad_cursor):
            self.assertEqual((await self.request(path='/api/v1/projects?'+query)).status_code,400)
        self.assertEqual(self.pool.checkouts,count)

    async def test_route_shapes_no_docs_redirect_422_or_extra_detail_query(self):
        for path in ('/docs','/openapi.json','/api/v1/projects/','/api/v1/projects/a/b'):
            response = await self.request(path=path)
            self.assertEqual(response.status_code,404)
            self.assertEqual(response.json(),{'error':{'code':'not_found'}})
        response = await self.request(path='/api/v1/projects/not-a-uuid')
        self.assertEqual(response.json(),{'error':{'code':'project_not_found'}})
        response = await self.request(path='/api/v1/projects/'+str(PROJECT)+'?anything=x')
        self.assertEqual(response.status_code,400)
        self.assertEqual((await self.request('PUT')).status_code,405)
        self.assertEqual((await self.request('POST',content='x')).status_code,415)
        self.assertEqual(self.pool.checkouts,0)

    async def test_raw_path_canonical_uuid_and_encoded_aliases(self):
        self.pool.rows[A] = [row(UUID(A))]
        normal = await self.request(path='/api/v1/projects/'+A)
        self.assertEqual(normal.status_code,200)
        for path, code in (('/api/v1/projects/%31'+A[1:],'project_not_found'),('/api/v1/projects/AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA','project_not_found'),('/%61pi/v1/projects','not_found')):
            response = await self.request(path=path)
            self.assertEqual(response.status_code,404)
            self.assertEqual(response.json(),{'error':{'code':code}})
        async def missing_raw(scope,receive,send):
            scope = dict(scope)
            scope.pop('raw_path',None)
            await self.app(scope,receive,send)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=missing_raw),base_url='http://offline.invalid') as client:
            response = await client.get('/api/v1/projects/'+A,headers={'Authorization':'Bearer '+token()})
            self.assertEqual(response.json(),{'error':{'code':'project_not_found'}})
        self.assertEqual(self.pool.checkouts,1)
        self.assertEqual(len(self.auth_calls),1)

    async def test_commit_unknown_and_known_rollback_are_not_success_or_retry(self):
        self.pool.commit_lost = True
        response = await self.request('POST',json={'name':'commit response lost'})
        self.assertEqual(response.status_code,504)
        self.assertEqual(response.json(),{'error':{'code':'project_create_result_unknown'}})
        self.assertEqual(sum(sql.startswith('INSERT INTO cyomics_app.projects') for sql,_ in self.pool.calls),1)
        self.assertEqual(len(self.pool.rows[A]),2)  # Modelled commit, not a real PG assertion.
        self.pool.commit_lost = False
        self.pool.commit_rejected = True
        response = await self.request('POST',json={'name':'known rollback'})
        self.assertEqual(response.status_code,503)
        self.assertEqual(len(self.pool.rows[A]),2)
        self.assertEqual(self.pool.release_subjects,[None,None])
        self.assertNotIn('opaque',response.text)

    async def test_disconnect_cancels_authority_without_checkout_or_late_response(self):
        self.auth_pause = asyncio.Event()
        incoming = asyncio.Queue()
        await incoming.put({'type':'http.request','body':b'','more_body':False})
        sent = []
        async def receive(): return await incoming.get()
        async def send(message): sent.append(message)
        scope = {'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'GET','scheme':'http','path':'/api/v1/projects','raw_path':b'/api/v1/projects','query_string':b'','root_path':'','server':('offline.invalid',80),'client':('127.0.0.1',1234),'headers':[(b'authorization',('Bearer '+token()).encode())]}
        task = asyncio.create_task(self.app(scope,receive,send))
        await asyncio.wait_for(self.auth_started.wait(),.5)
        await incoming.put({'type':'http.disconnect'})
        await asyncio.wait_for(task,.5)
        self.assertEqual(self.pool.checkouts,0)
        self.assertEqual(sent,[])

    async def test_auth_body_consumption_inherits_remaining_total_budget(self):
        from starlette.requests import Request
        from cyomics_projects.identity import Authority
        class SlowBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                await asyncio.sleep(10)
                yield b'{}'
        async def transport(request):
            return httpx.Response(200,stream=SlowBody())
        authority = Authority(httpx.MockTransport(transport),lambda:ANON)
        request = Request({'type':'http','headers':[(b'authorization',('Bearer '+token()).encode())]})
        try:
            with self.assertRaises(Fault) as failure:
                await authority.verify(request,Budget(.02))
            self.assertEqual(failure.exception.status,503)
            self.assertEqual(self.pool.checkouts,0)
        finally:
            await authority.close()


class TransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_expired_budget_does_not_start_io_or_create_awaitable(self):
        pool = Pool()
        with self.assertRaises(Fault) as failure:
            await Repository(pool).list(A,20,None,Budget(0))
        self.assertEqual(failure.exception.status,503)
        self.assertEqual(pool.checkouts,0)
        self.assertEqual(pool.calls,[])

    async def test_statement_and_commit_timeout_discard_subject(self):
        for during_commit, expected_status in ((False,503),(True,504)):
            with self.subTest(commit=during_commit):
                pool = Pool()
                pool.stall_commit = during_commit
                pool.stall_read = not during_commit
                before = time.monotonic()
                with self.assertRaises(Fault) as failure:
                    if during_commit:
                        await Repository(pool).create(A,{'name':'x','description':'','species':''},Budget(.2))
                    else:
                        await Repository(pool).list(A,20,None,Budget(.2))
                phase = 'commit_entered' if during_commit else 'read_entered'
                self.assertTrue(getattr(pool.connection,phase), 'Fixture precondition failed: target '+phase+' was not reached before timeout')
                self.assertEqual(failure.exception.status,expected_status)
                self.assertLess(time.monotonic()-before,.6)
                self.assertTrue(pool.connection.closed)
                self.assertIsNone(pool.connection.subject)
                await asyncio.sleep(0)


class PoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_during_connect_aborts_late_connection_and_restores_slot(self):
        started, resume = asyncio.Event(), asyncio.Event()
        class LateConnection:
            def __init__(self):
                self.closed = False
                self.pgconn = self
                self.finish_calls = 0
            def finish(self):
                self.finish_calls += 1
                self.closed = True
        connection = LateConnection()
        class DeferredDriver:
            @staticmethod
            async def connect(**kwargs):
                started.set()
                await resume.wait()
                return connection
        pool = FixedPool()
        with patch.dict('os.environ', {}, clear=True), \
             patch('cyomics_projects.repository.AsyncConnection', DeferredDriver), \
             patch('cyomics_projects.repository.load_runtime_password', return_value='offline-synthetic-only') as loader:
            task = asyncio.create_task(pool.getconn())
            try:
                await asyncio.wait_for(started.wait(), .5)
                self.assertEqual(pool._slots._value, 2)
                pool.close()
                self.assertFalse(task.done())
                self.assertFalse(connection.closed)
                resume.set()
                with self.assertRaises(Fault) as failure:
                    await asyncio.wait_for(task, .5)
                self.assertEqual(failure.exception.status, 503)
                self.assertTrue(connection.closed)
                self.assertEqual(connection.finish_calls, 1)
                self.assertEqual(pool._active, set())
                self.assertEqual(list(pool._idle), [])
                self.assertEqual(pool._slots._value, 3)
                loader.assert_called_once_with()
            finally:
                resume.set()
                await asyncio.gather(task, return_exceptions=True)
                pool.close()


class FactoryTests(unittest.TestCase):
    def test_default_factory_and_pool_constructor_are_io_free_and_closed(self):
        with patch('os.open',side_effect=AssertionError('Private file forbidden')), patch.object(socket.socket,'connect',side_effect=AssertionError('Network forbidden')):
            with self.assertRaises(RuntimeNotApproved):
                create_app()
            pool = FixedPool()
            pool.close()
