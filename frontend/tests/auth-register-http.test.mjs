import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { createPreviewServer } from '../server.mjs';

const email = 'synthetic@example.com', origin = 'http://127.0.0.1:4206';
async function temporary(t, enabled, fetchImpl, extra = {}) {
  let keys = 0;
  const server = createPreviewServer({ registerEnabled: enabled, fetchImpl,
    registerOptions: { keyProvider: async () => { keys++; return 'SYNTHETIC_ANON'; } }, ...extra });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  t.after(() => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }));
  const send = (path, { method = 'GET', cookie, body, headers } = {}) => new Promise((resolve, reject) => {
    const wire = body === undefined ? undefined : JSON.stringify(body);
    const req = http.request({ hostname: '127.0.0.1', port: server.address().port, path, method,
      headers: { host: '127.0.0.1:4206', origin, ...(cookie ? { cookie } : {}),
        ...(wire ? { 'content-type': 'application/json', 'content-length': Buffer.byteLength(wire) } : {}), ...headers } }, res => {
      const chunks = []; res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks).toString() }));
      res.on('error', reject);
    }); req.on('error', reject); req.end(wire);
  });
  return { send, keys: () => keys };
}
test('temporary loopback HTTP: SDK signup, bound callback, clean 303, confirmation and no query exposure', async t => {
  const calls = []; let flow;
  const user = { id: 'synthetic-user', email, email_confirmed_at: '2026-11-15T00:00:00Z', aud: 'authenticated' };
  const h = await temporary(t, true, async (wire, options) => {
    const url = new URL(wire); calls.push({ url, options });
    assert.equal(url.origin, 'http://127.0.0.1:57421');
    assert.ok(!Object.keys(options.headers).some(key => /cookie/i.test(key)));
    if (url.pathname === '/auth/v1/signup') {
      flow = new URL(url.searchParams.get('redirect_to')).searchParams.get('flow');
      return Response.json({ ...user, email_confirmed_at: null });
    }
    if (url.pathname === '/auth/v1/token') return Response.json({ access_token: 'synthetic.access.token',
      refresh_token: 'synthetic-refresh', expires_in: 3600, token_type: 'bearer', user });
    assert.equal(url.pathname, '/auth/v1/user'); return Response.json(user);
  });
  const root = await h.send('/'); assert.equal(root.status, 200); assert.equal(h.keys(), 0);
  const cookie = root.headers['set-cookie'][0].split(';')[0];
  assert.match(root.headers['set-cookie'][0], /HttpOnly; SameSite=Lax; Max-Age=900$/);
  const signup = await h.send('/api/auth/signup', { method: 'POST', cookie,
    body: { email, password: 'SYNTHETIC-password-123' }, headers: { authorization: 'Bearer SYNTHETIC-client-token' } });
  assert.equal(signup.status, 202); assert.deepEqual(JSON.parse(signup.body), { status: 'pending_confirmation' });
  const callback = await h.send('/auth/callback?flow=' + flow + '&code=SYNTHETIC-code', { cookie });
  assert.equal(callback.status, 303); assert.equal(callback.headers.location, '/auth/callback'); assert.equal(callback.body, '');
  const receipt = callback.headers['set-cookie'][0].split(';')[0];
  const clean = await h.send('/auth/callback', { cookie }); assert.equal(clean.status, 200);
  const result = await h.send('/api/auth/confirmation', { cookie: cookie + '; ' + receipt });
  assert.equal(result.status, 200); assert.deepEqual(JSON.parse(result.body), { status: 'verified', emailVerified: true });
  assert.equal(result.headers['cache-control'], 'no-store'); assert.equal(result.headers['set-cookie'], undefined);
  assert.equal(h.keys(), 2); assert.equal(calls.length, 3);
  assert.ok(!result.body.includes(email)); assert.ok(!result.body.includes('token'));
  const without = await h.send('/api/auth/confirmation');
  assert.deepEqual(JSON.parse(without.body), { status: 'unconfirmed', emailVerified: false }); assert.equal(calls.length, 3);
});
test('temporary loopback HTTP: disabled server calls no provider or upstream; aliases and canceled routes stay unavailable', async t => {
  let calls = 0; const h = await temporary(t, false, async () => { calls++; throw Error('no network permitted'); });
  const root = await h.send('/'); assert.equal(root.status, 200); assert.equal(root.headers['set-cookie'], undefined);
  const signup = await h.send('/api/auth/signup', { method: 'POST', body: { email, password: 'SYNTHETIC-password-123' } });
  assert.equal(signup.status, 503); assert.equal((await h.send('/api/auth/confirmation')).status, 503);
  assert.equal((await h.send('/api/auth/confirm', { method: 'POST' })).status, 401);
  assert.equal((await h.send('/auth/v1/verify?token=synthetic')).status, 404);
  assert.equal(calls, 0); assert.equal(h.keys(), 0);
});

test('[diag] temporary HTTP server requires an explicit diagnostic opt-in even when a private sink is injected', async t => {
  const events = []; const h = await temporary(t, false, () => { throw Error('forbidden network'); }, {
    registerDiagnosticSink: e => events.push(e),
  });
  const result = await h.send('/api/auth/confirmation'); assert.equal(result.status, 503);
  assert.deepEqual(JSON.parse(result.body), { error: { code: 'confirmation_unavailable' } });
  assert.equal(events.length, 0); assert.equal(h.keys(), 0);
});
test('[diag] temporary HTTP keeps diagnostics private while official mocked PKCE and receipt confirmation succeed', async t => {
  const events = []; let flow;
  const current = { id: 'DIAG_SECRET_USER', email, email_confirmed_at: '2026-11-16T00:00:00Z', aud: 'authenticated' };
  const h = await temporary(t, true, async wire => {
    const url = new URL(wire);
    if (url.pathname === '/auth/v1/signup') { flow = new URL(url.searchParams.get('redirect_to')).searchParams.get('flow'); return Response.json({ ...current, email_confirmed_at: null }); }
    if (url.pathname === '/auth/v1/token') return Response.json({ access_token: 'diag_secret.access.token',
      refresh_token: 'DIAG_SECRET_REFRESH', expires_in: 3600, token_type: 'bearer', user: current });
    assert.equal(url.pathname, '/auth/v1/user'); return Response.json(current);
  }, { registerDiagnosticsEnabled: true, registerDiagnosticSink: e => events.push(e) });
  const root = await h.send('/'), cookie = root.headers['set-cookie'][0].split(';')[0];
  const signup = await h.send('/api/auth/signup', { method: 'POST', cookie, body: { email, password: 'DIAG_SECRET_PASSWORD_123' } });
  assert.equal(signup.status, 202); assert.deepEqual(JSON.parse(signup.body), { status: 'pending_confirmation' });
  const callback = await h.send('/auth/callback?flow=' + flow + '&code=DIAG_SECRET_CODE', { cookie });
  assert.equal(callback.status, 303); assert.equal(callback.headers.location, '/auth/callback');
  const receipt = callback.headers['set-cookie'][0].split(';')[0];
  const confirmation = await h.send('/api/auth/confirmation', { cookie: cookie + '; ' + receipt });
  assert.deepEqual(JSON.parse(confirmation.body), { status: 'verified', emailVerified: true });
  assert.equal(Object.keys(confirmation.headers).some(key => key.includes('diagnostic')), false);
  assert.ok(events.some(e => e.stage === 'transport' && e.operation === 'exchange' && e.result === 'received'));
  assert.ok(events.some(e => e.stage === 'get_user' && e.result === 'succeeded' && e.emailMatches && e.emailConfirmed));
  assert.ok(events.some(e => e.stage === 'confirmation' && e.result === 'verified' && e.receiptMatches));
  const output = JSON.stringify(events);
  for (const secret of [cookie.split('=')[1], receipt.split('=')[1], flow, email, 'DIAG_SECRET', '/auth/callback', 'http://'])
    assert.equal(output.includes(secret), false, 'private diagnostic leaked a sentinel or URL');
  assert.equal((await h.send('/api/auth/diagnostics')).status, 401);
  assert.equal(h.keys(), 2);
});
test('[diag] temporary HTTP faulting private sinks cannot alter confirmation refusal or enable Auth', async t => {
  for (const sink of [() => { throw Error('DIAG_SECRET_SINK'); }, () => Promise.reject(Error('DIAG_SECRET_SINK'))]) {
    const h = await temporary(t, true, () => { throw Error('forbidden network'); }, {
      registerDiagnosticsEnabled: true, registerDiagnosticSink: sink,
    });
    const result = await h.send('/api/auth/confirmation'); assert.equal(result.status, 200);
    assert.deepEqual(JSON.parse(result.body), { status: 'unconfirmed', emailVerified: false });
    assert.equal(h.keys(), 0);
  }
});
