"""New input HTTP boundary only; no old suite, actual Auth/SQL/R or test SDK."""
import base64
import json
import socket
import time
import unittest
from unittest.mock import patch
import httpx
from cyomics_projects.app import create_app
from cyomics_projects.preprocess_repository import PreprocessRepository
from cyomics_projects.quant_diff_http import selection_input, response_value
from cyomics_projects.quant_diff_input import Result, BoundInput
from cyomics_projects.contrast_design import Contrast
from cyomics_projects.settings import Fault, EXPECTED_ISSUER
from fakes import Pool, A, PROJECT

PROJECT_ID = str(PROJECT)
V = '77777777-7777-4777-8777-777777777777'
S = '88888888-8888-4888-8888-888888888888'
R = '99999999-9999-4999-8999-999999999999'
BODY = {'processed_dataset_version_id': V, 'sample_design_version_id': S,
        'sample_design_request_id': R, 'contrast': {}, 'replicate_model': 'independent_biological'}


def token(role='authenticated'):
    def enc(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    now = int(time.time())
    return '.'.join((enc({'alg': 'HS256'}), enc({'sub': A, 'role': role, 'iss': EXPECTED_ISSUER,
        'aud': 'authenticated', 'iat': now - 5, 'exp': now + 120, 'is_anonymous': False}), enc('offline-only')))


class ProjectionTests(unittest.TestCase):
    def test_browser_owner_path_and_duplicate_keys_are_rejected(self):
        choice = selection_input(json.dumps(BODY).encode())
        self.assertEqual(choice.processed_dataset_version_id, V)
        for raw in (json.dumps(dict(BODY, owner_id=A)).encode(),
                    json.dumps(dict(BODY, path='/tmp/matrix')).encode(),
                    ('{"processed_dataset_version_id":"' + V + '",' + json.dumps(BODY)[1:]).encode()):
            with self.assertRaises(Fault) as got:
                selection_input(raw)
            self.assertEqual((got.exception.status, got.exception.code), (400, 'invalid_differential_input'))

    def test_projection_carries_no_matrix_or_statistical_result(self):
        choice = selection_input(json.dumps(BODY).encode())
        blocked = response_value(PROJECT_ID, choice, Result(False, ('confirmed_sample_design_required',), False))
        self.assertFalse(blocked['calculation_started'])
        self.assertIsNone(blocked['input_summary'])
        self.assertEqual(blocked['reasons'], ['confirmed_sample_design_required'])
        # Projection-only synthetic object, not authority, scientific or binder evidence.
        bound = BoundInput(b'{}', False, None, Contrast(V, 'treated', 'control'), (), (),
            (object(),) * 4, (object(),) * 3, (), 2, (1, -1), b'matrix', b'design', b'preset', b'rows', ())
        ready = response_value(PROJECT_ID, choice, Result(True, (), False, bound))
        self.assertEqual(ready['input_summary']['row_count'], 3)
        self.assertEqual(ready['input_summary']['sample_count'], 4)
        self.assertEqual(ready['input_summary']['contrast']['numerator_group'], 'treated')
        self.assertEqual(set(ready['input_summary']['input_pins']), {'matrix', 'design', 'preset'})
        self.assertNotIn('matrix_tsv', json.dumps(ready))
        self.assertFalse(ready['source_analysis_ready'])
        self.assertFalse(ready['calculation_started'])


class HTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.socket_guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('network prohibited'))
        self.guard = self.socket_guard.start()
        self.pool = Pool()
        self.apps = []
        self.clients = []
        self.auth_calls = []
        self.auth_status = 200
        async def auth(request):
            self.auth_calls.append(str(request.url))
            self.assertEqual(str(request.url), 'http://127.0.0.1:57421/auth/v1/user')
            return httpx.Response(self.auth_status, json={'id': A, 'aud': 'authenticated',
                'role': 'authenticated', 'is_anonymous': False, 'email': 'qa@example.invalid',
                'email_confirmed_at': '2026-01-01T00:00:00Z'})
        self.transport = httpx.MockTransport(auth)
        self.headers = {'origin': 'http://127.0.0.1:4206', 'x-cyomics-request': '1',
                        'sec-fetch-site': 'same-origin', 'authorization': 'Bearer ' + token()}
        self.path = '/api/v1/projects/' + PROJECT_ID + '/differential-input'

    def client(self, enabled):
        app = create_app(auth_transport=self.transport, pool=self.pool, anon_key_loader=lambda: token('anon'),
            preprocessing_repository=PreprocessRepository(self.pool), imports_enabled=True,
            sample_designs_enabled=True, differential_inputs_enabled=enabled)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:57430')
        self.apps.append(app)
        self.clients.append(client)
        return client

    async def asyncTearDown(self):
        for client in self.clients:
            await client.aclose()
        for app in self.apps:
            await app.state.authority.close()
            app.state.complete_imports.close()
        self.assertEqual(self.guard.call_count, 0)
        self.socket_guard.stop()
        self.assertEqual(self.pool.calls, [])

    async def test_default_off_is_no_auth_or_repository_io(self):
        response = await self.client(False).post(self.path, json=BODY, headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.auth_calls, [])
        self.assertEqual(response.headers['cache-control'], 'no-store')

    async def test_normal_authority_precedes_input_projection(self):
        client = self.client(True)
        response = await client.post(self.path, json=BODY, headers={k: v for k, v in self.headers.items() if k != 'authorization'})
        self.assertEqual(response.status_code, 401)
        response = await client.post(self.path, json=dict(BODY, owner_id=A), headers=self.headers)
        self.assertEqual((response.status_code, response.json()['error']['code']), (400, 'invalid_differential_input'))
        self.assertEqual(len(self.auth_calls), 1)
        self.auth_status = 401
        response = await client.post(self.path, json=BODY, headers=self.headers)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(len(self.auth_calls), 2)

    async def test_cross_origin_method_and_ambiguous_request_rejected(self):
        client = self.client(True)
        bad = dict(self.headers, origin='http://other.invalid')
        self.assertEqual((await client.post(self.path, json=BODY, headers=bad)).status_code, 403)
        self.assertEqual((await client.get(self.path, headers=self.headers)).status_code, 405)
        self.assertEqual((await client.post(self.path + '?owner=' + A, json=BODY, headers=self.headers)).status_code, 404)
        self.assertEqual(self.auth_calls, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
