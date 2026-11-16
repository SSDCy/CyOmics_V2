"""Controlled-clock ASGI boundary tests; no real timeouts, network or secrets."""
import asyncio
import json
import unittest
from unittest.mock import patch
import httpx
import cyomics_projects.app as api
import cyomics_projects.settings as settings
from fakes import A, Pool
from test_projects import ANON, token

REMAINING = b'x-cyomics-remaining-ms'
DEADLINE = b'x-cyomics-deadline-unix-ms'
WALL = 1790000000.0


class Clock:
    def __init__(self):
        self.mono = 1000.0
        self.wall = WALL
        self.calls = []
        self.mono_values = []

    def monotonic(self):
        self.calls.append('monotonic')
        return self.mono_values.pop(0) if self.mono_values else self.mono

    def time(self):
        self.calls.append('wall')
        return self.wall


class DelayedHeaders(list):
    def __init__(self, values, clock, delay):
        super().__init__(values)
        self.clock, self.delay = clock, delay

    def __iter__(self):
        self.clock.mono += self.delay
        self.delay = 0
        return super().__iter__()


def pair(remaining=b'1000', deadline=None):
    if deadline is None:
        deadline = str(int(WALL * 1000) + 60000).encode('ascii')
    return [(REMAINING, remaining), (DEADLINE, deadline)]


class BudgetHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = Clock()
        self.pool = Pool()
        self.auth_calls = []

        async def auth(request):
            self.auth_calls.append(request)
            return httpx.Response(200, json={
                'id': A, 'aud': 'authenticated', 'role': 'authenticated',
                'is_anonymous': False, 'email': 'offline@example.invalid',
                'email_confirmed_at': '2026-01-01T00:00:00Z'
            })

        self.app = api.create_app(auth_transport=httpx.MockTransport(auth),
                                  pool=self.pool, anon_key_loader=lambda: ANON)

    async def asyncTearDown(self):
        await self.app.state.authority.close()
        self.pool.close()

    async def request(self, headers=(), *, parse_delay=0, authenticated=True):
        raw = list(headers)
        if authenticated:
            raw.append((b'authorization', ('Bearer ' + token()).encode('ascii')))
        scope = {'type': 'http', 'asgi': {'version': '3.0'},
                 'http_version': '1.1', 'method': 'GET', 'scheme': 'http',
                 'path': '/api/v1/projects', 'raw_path': b'/api/v1/projects',
                 'query_string': b'', 'root_path': '',
                 'server': ('offline.invalid', 80), 'client': ('127.0.0.1', 1234),
                 'headers': DelayedHeaders(raw, self.clock, parse_delay)}
        incoming = asyncio.Queue()
        incoming.put_nowait({'type': 'http.request', 'body': b'', 'more_body': False})
        reads, sent = [], []

        async def receive():
            reads.append(True)
            return await incoming.get()

        async def send(message):
            sent.append(message)

        with patch.object(api, 'time', self.clock), patch.object(settings, 'time', self.clock), \
             patch.object(asyncio, 'create_task', wraps=asyncio.create_task) as scheduled:
            await self.app(scope, receive, send)
            actual_budget = scope.get('cyomics.budget')
            self.remaining_at_response = actual_budget.remaining() if actual_budget is not None else None
        status = next(item['status'] for item in sent if item['type'] == 'http.response.start')
        value = json.loads(b''.join(item.get('body', b'') for item in sent if item['type'] == 'http.response.body'))
        return status, value, scope.get('cyomics.budget'), len(reads), scheduled.call_count

    def assert_rejected_before_scheduling(self, result, status, code):
        actual, value, budget, reads, tasks = result
        self.assertEqual(actual, status)
        self.assertEqual(value, {'error': {'code': code}})
        self.assertIsNone(budget)
        self.assertEqual(reads, 0)
        self.assertEqual(tasks, 0)
        self.assertEqual(self.auth_calls, [])
        self.assertEqual(self.pool.checkouts, 0)

    async def test_no_headers_anchor_nine_seconds_at_http_entry(self):
        status, _, budget, _, _ = await self.request()
        self.assertEqual(status, 200)
        self.assertEqual(budget.deadline, 1009.0)
        self.assertEqual(self.remaining_at_response, 9.0)
        self.assertEqual(self.clock.calls[:2], ['monotonic', 'wall'])
        self.assertEqual(len(self.auth_calls), 1)
        self.assertEqual(self.pool.checkouts, 1)

    async def test_future_wall_and_maximum_remaining_never_extend_nine_seconds(self):
        headers = [(REMAINING.upper(), b'12000'), (DEADLINE.upper(), b'9999999999999')]
        status, _, budget, _, _ = await self.request(headers)
        self.assertEqual(status, 200)
        self.assertEqual(budget.deadline, 1009.0)
        self.assertEqual(self.remaining_at_response, 9.0)

    async def test_eleven_seconds_spent_leaves_one_second_and_delivery_consumes_two_hundred_ms(self):
        deadline = str(int(WALL * 1000) + 1000).encode('ascii')
        status, _, budget, _, _ = await self.request(pair(deadline=deadline))
        self.assertEqual(status, 200)
        self.assertAlmostEqual(budget.deadline, 1001.0)
        self.assertEqual(self.remaining_at_response, 1.0)
        self.clock.wall = WALL + .2
        status, _, budget, _, _ = await self.request(pair(deadline=deadline))
        self.assertEqual(status, 200)
        self.assertLessEqual(self.remaining_at_response, .800001)
        self.assertAlmostEqual(self.remaining_at_response, .8, places=5)
        self.assertAlmostEqual(budget.deadline, 1000.8, places=5)

    async def test_header_parsing_time_is_consumed_instead_of_restarting_budget(self):
        status, _, budget, _, _ = await self.request(pair(), parse_delay=.2)
        self.assertEqual(status, 200)
        self.assertEqual(budget.deadline, 1001.0)
        self.assertAlmostEqual(self.remaining_at_response, .8)

    async def test_no_header_parsing_delay_also_consumes_original_nine_seconds(self):
        status, _, budget, _, _ = await self.request(parse_delay=3)
        self.assertEqual(status, 200)
        self.assertEqual(budget.deadline, 1009.0)
        self.assertEqual(self.remaining_at_response, 6.0)

    async def test_bad_raw_pairs_reject_before_auth_pool_body_or_worker(self):
        valid = pair()
        bad = [valid[:1], valid[1:], valid + [valid[0]], valid + [valid[1]],
               valid + [(REMAINING.upper(), b'1000')],
               valid + [(DEADLINE.upper(), valid[1][1])]]
        for value in [b'', b'0', b'00', b'01', b'01000', b'12001', b'999999',
                      b'+1', b'-1', b'1.0', b'1e3', b' 1', b'1 ', b'1\t',
                      b'1,1', b'\xff', '1000', b'9' * 10000]:
            bad.append(pair(value))
        for value in [b'', b'0', b'0123456789012', b'179000000000', b'17900000000000',
                      b'+1790000000000', b'-1790000000000', b'1790000000000 ',
                      b'1790000000000,1790000000000', b'179000000000.', b'\xff' * 13,
                      '1790000000000']:
            bad.append(pair(deadline=value))
        for headers in bad:
            with self.subTest(headers=headers):
                self.assert_rejected_before_scheduling(await self.request(headers), 400, 'invalid_project_query')

    async def test_valid_expired_wall_or_parse_exhaustion_never_schedules_work(self):
        for delta in [0, -1000]:
            with self.subTest(wallDeltaMs=delta):
                headers = pair(deadline=str(int(WALL * 1000) + delta).encode('ascii'))
                self.assert_rejected_before_scheduling(await self.request(headers), 503, 'projects_unavailable')
        self.assert_rejected_before_scheduling(await self.request(pair(), parse_delay=1.1), 503, 'projects_unavailable')

    async def test_nonfinite_entry_wall_and_constructor_clocks_fail_closed(self):
        for field in ['mono', 'wall']:
            for value in [float('nan'), float('inf'), float('-inf')]:
                with self.subTest(clock=field, value=value):
                    self.clock.mono, self.clock.wall = 1000.0, WALL
                    setattr(self.clock, field, value)
                    self.assert_rejected_before_scheduling(await self.request(pair()), 503, 'projects_unavailable')
        self.clock.mono, self.clock.wall = 1000.0, WALL
        for value in [float('nan'), float('inf'), float('-inf')]:
            with self.subTest(constructorClock=value):
                self.clock.mono_values = [1000.0, value]
                self.assert_rejected_before_scheduling(await self.request(pair()), 503, 'projects_unavailable')

    async def test_timing_headers_supply_no_identity_or_authorization(self):
        status, value, _, _, _ = await self.request(pair(), authenticated=False)
        self.assertEqual(status, 401)
        self.assertEqual(value, {'error': {'code': 'unauthenticated'}})
        self.assertEqual(self.auth_calls, [])
        self.assertEqual(self.pool.checkouts, 0)
