"""R1: only fake ASGI sends. Inherit unchanged Authority/transport/guard setup."""
import asyncio
import json
from unittest.mock import patch
from test_import_http import ImportHttpTests,PATH,ORIGIN
from test_projects import token
from cyomics_projects import import_http as api
from cyomics_projects.settings import Budget

class ErrorBudgetTests(ImportHttpTests):
    async def drive(self,send,body=b'{}'):
        queue=asyncio.Queue();await queue.put({'type':'http.request','body':body,'more_body':False})
        scope={'type':'http','http_version':'1.1','method':'POST','scheme':'http','path':PATH,'raw_path':PATH.encode(),'query_string':b'','root_path':'',
               'headers':[(b'host',b'127.0.0.1:57430'),(b'origin',ORIGIN.encode()),(b'x-cyomics-request',b'1'),(b'content-type',b'application/json'),(b'authorization',('Bearer '+token()).encode())],
               'server':('127.0.0.1',57430),'client':('127.0.0.1',12345)}
        await self.app(scope,queue.get,send)
    async def released(self):
        for _ in range(3):await asyncio.sleep(0)
        self.assertFalse(self.app.state.complete_imports.leases)
        self.assertFalse(self.app.state.complete_imports.subjects)

    async def test_r1_slow_error_start_stops_at_original_budget_and_releases(self):
        attempts=[];closed=[]
        async def send(message):
            attempts.append(message)
            try:await asyncio.Future()
            finally:closed.append(True)
        with patch.object(api,'import_budget',return_value=Budget(.05)):
            await asyncio.wait_for(self.drive(send),.7)
        self.assertEqual([m['type'] for m in attempts],['http.response.start'])
        self.assertEqual(attempts[0]['status'],400);self.assertEqual(closed,[True])
        self.assertEqual(len(self.auth_calls),1);self.assertEqual(self.raw_calls(),0);await self.released()

    async def test_r1_slow_error_body_stops_without_second_start(self):
        attempts=[];closed=[]
        async def send(message):
            attempts.append(message)
            if message['type']=='http.response.body':
                try:await asyncio.Future()
                finally:closed.append(True)
        with patch.object(api,'import_budget',return_value=Budget(.05)):
            await asyncio.wait_for(self.drive(send),.7)
        self.assertEqual([m['type'] for m in attempts],['http.response.start','http.response.body'])
        self.assertEqual(attempts[0]['status'],400);self.assertEqual(closed,[True]);self.assertEqual(self.raw_calls(),0);await self.released()

    async def test_r1_expired_budget_only_synchronous_error_no_new_budget_or_wait(self):
        original=api.strict_json
        for waiting in [False,True]:
            budget=Budget(2);attempts=[];closed=[]
            def expire(raw):budget.deadline=0;return original(raw)
            async def send(message):
                attempts.append(message)
                if waiting:
                    try:await asyncio.Future()
                    finally:closed.append(True)
            with patch.object(api,'import_budget',return_value=budget),patch.object(api,'strict_json',side_effect=expire),patch.object(api,'Budget',side_effect=AssertionError('No fresh error budget permitted')):
                await asyncio.wait_for(self.drive(send),.7)
            self.assertEqual(attempts[0]['status'],400)
            if waiting:
                self.assertEqual(len(attempts),1);self.assertEqual(closed,[True])
            else:
                self.assertEqual([m['type'] for m in attempts],['http.response.start','http.response.body'])
                self.assertEqual(json.loads(attempts[1]['body']),{'error':{'code':'invalid_import_request'}})
            await self.released()
        self.assertEqual(self.raw_calls(),0)

    async def test_r1_pre_admission_public_error_has_finite_send_budget(self):
        self.app.state.complete_imports.enabled=False;attempts=[]
        async def send(message):attempts.append(message);await asyncio.Future()
        with patch.object(api,'PUBLIC_ERROR_SEND_SECONDS',.05):
            await asyncio.wait_for(self.drive(send),.7)
        self.assertEqual(len(attempts),1);self.assertEqual(attempts[0]['status'],503)
        self.assertEqual(self.auth_calls,[]);self.assertEqual(self.raw_calls(),0);await self.released()

    async def test_r1_error_body_failure_or_uncertain_success_start_never_restarts(self):
        attempts=[]
        async def fail_body(message):
            attempts.append(message)
            if message['type']=='http.response.body':raise ConnectionError('SYNTHETIC_PRIVATE_SEND_ERROR')
        await self.drive(fail_body)
        self.assertEqual([m['type'] for m in attempts],['http.response.start','http.response.body']);await self.released()
        attempts=[]
        async def fail_start(message):attempts.append(message);raise ConnectionError('SYNTHETIC_PRIVATE_START_ERROR')
        await self.drive(fail_start,body=b'{"project_id":"44444444-4444-4444-8444-444444444444"}')
        self.assertEqual(len(attempts),1);self.assertEqual(attempts[0]['status'],200);self.assertEqual(len(self.pool.versions),1);await self.released()
        attempts=[];closed=[]
        async def slow_success_start(message):
            attempts.append(message)
            try:await asyncio.Future()
            finally:closed.append(True)
        with patch.object(api,'import_budget',return_value=Budget(.05)):
            await asyncio.wait_for(self.drive(slow_success_start,body=b'{"project_id":"44444444-4444-4444-8444-444444444444"}'),.7)
        self.assertEqual(len(attempts),1);self.assertEqual(attempts[0]['status'],200)
        self.assertEqual(closed,[True]);self.assertEqual(len(self.pool.versions),1);await self.released()


def load_tests(loader, standard_tests, pattern):
    # Include the five added cases once; inherited baseline cases have their own module.
    import unittest
    return unittest.TestSuite(ErrorBudgetTests(name) for name in ErrorBudgetTests.__dict__ if name.startswith("test_r1_"))
