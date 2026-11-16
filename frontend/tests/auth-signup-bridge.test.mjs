import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { setImmediate as nextTurn } from 'node:timers/promises';
import { createAuthSignupBridge } from '../auth-signup-bridge.mjs';

const password = 'SYNTHETIC-password-123';
const email = 'researcher@gmail.com';
const pending = { status: 'pending_confirmation' };
const failed = code => ({ error: { code } });
const good = address => ({ data: { user: { id: 'synthetic-user', email: address, email_confirmed_at: null }, session: null }, error: null });
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
class Clock {
  time = 0; next = 0; timers = new Map();
  now = () => this.time;
  setTimer = (fn, delay) => { assert.equal(delay, 15000); const id = ++this.next; this.timers.set(id, { fn, at: this.time + delay }); return id; };
  clearTimer = id => { this.timers.delete(id); };
  advance(ms) { this.time += ms; for (const [id, task] of [...this.timers]) if (task.at <= this.time) { this.timers.delete(id); task.fn(); } }
}
function req(options = {}) {
  const request = new PassThrough();
  Object.assign(request, { method: 'POST', url: '/api/auth/signup', aborted: false,
    headers: { host: '127.0.0.1:4206', origin: 'http://127.0.0.1:4206', 'content-type': 'application/json',
      authorization: 'Bearer SYNTHETIC-client-secret', cookie: 'SYNTHETIC-cookie', 'x-forwarded-for': '203.0.113.9' },
    socket: { remoteAddress: '127.0.0.1' } }, options);
  return request;
}
function res() {
  return Object.assign(new EventEmitter(), { writes: 0, destroyed: false, writableEnded: false,
    writeHead(status, headers) { this.status = status; this.headers = headers; this.writes++; },
    end(body) { this.body = body ? JSON.parse(body) : undefined; this.writableEnded = true; } });
}
function harness(options = {}) {
  const clock = new Clock(), calls = { factories: [], signup: [], clients: [] };
  const createAuthClient = async settings => {
    calls.factories.push(settings);
    const client = { signUp: async input => { calls.signup.push(input); return good(input.email); } };
    calls.clients.push(client); return client;
  };
  const bridge = createAuthSignupBridge({ enabled: true, createAuthClient, now: clock.now,
    setTimer: clock.setTimer, clearTimer: clock.clearTimer, ...options });
  return { bridge, clock, calls };
}
async function invoke(bridge, input = { email, password }, options = {}, wire) {
  const request = req(options), response = res();
  const completion = bridge(request, response);
  request.end(wire ?? Buffer.from(JSON.stringify(input)));
  const handled = await completion;
  request.destroy(); return { request, response, handled };
}
function safe(response, status, body) {
  assert.equal(response.status, status); assert.deepEqual(response.body, body); assert.equal(response.writes, 1);
  const h = response.headers;
  assert.equal(h['Content-Type'], 'application/json; charset=utf-8'); assert.equal(h['Cache-Control'], 'no-store');
  assert.equal(h['X-Content-Type-Options'], 'nosniff'); assert.equal(h['Referrer-Policy'], 'no-referrer');
  assert.equal(h.Connection, 'close');
  assert.ok(Object.keys(h).every(k => ['Content-Type', 'Cache-Control', 'X-Content-Type-Options', 'Referrer-Policy', 'Connection', 'Allow', 'Retry-After'].includes(k)));
}

test('pending new and sanitized duplicate users have the same constant response and an isolated client per attempt', async () => {
  const h = harness();
  const one = await invoke(h.bridge, { email, password: '  abcdefghijkl  ' });
  const two = await invoke(h.bridge, { email: 'researcher@163.com', password });
  safe(one.response, 202, pending); safe(two.response, 202, pending);
  assert.equal(h.calls.factories.length, 2); assert.notEqual(h.calls.clients[0], h.calls.clients[1]);
  assert.deepEqual(Object.keys(h.calls.factories[0]), ['signal']);
  assert.deepEqual(h.calls.signup[0], { email, password: '  abcdefghijkl  ', options: { emailRedirectTo: 'http://127.0.0.1:4206/auth/callback' } });
  assert.ok(h.calls.factories.every(x => x.signal.aborted)); assert.equal(h.clock.timers.size, 0);
  for (const result of [
    { data: { user: { id: 'different-synthetic-id', email, confirmed_at: null, identities: [] }, session: null }, error: null },
    { data: { user: null, session: null }, error: { code: 'user_already_exists', message: 'SYNTHETIC-existing-account' } },
  ]) {
    const d = harness({ createAuthClient: () => ({ signUp: async () => result }) });
    safe((await invoke(d.bridge)).response, 202, pending);
  }
});

test('default disabled, explicit disabled and missing factory are unavailable without reading a body or invoking auth', async () => {
  let effects = 0;
  for (const options of [{}, { enabled: false, createAuthClient: () => { effects++; } },
    { createAuthClient: () => { effects++; } }, { enabled: true }]) {
    const request = req(), response = res();
    await createAuthSignupBridge(options)(request, response);
    safe(response, 503, failed('signup_unavailable')); assert.equal(request.listenerCount('data'), 0); request.destroy();
  }
  assert.equal(effects, 0);
});

test('URL variants and non-POST requests are rejected before any factory; unrelated paths are untouched', async () => {
  const h = harness();
  for (const url of ['/api/auth/signup?', '/api/auth/signup?role=admin', '/api/auth/signup/',
    'http://outside.invalid/api/auth/signup', '/api/auth/%73ignup', '/api/auth/./signup', '/api/auth/signup#fragment'])
    safe((await invoke(h.bridge, undefined, { url })).response, 404, failed('not_found'));
  for (const method of ['GET', 'HEAD', 'PUT', 'OPTIONS']) {
    const { response } = await invoke(h.bridge, undefined, { method });
    assert.equal(response.status, 405); assert.equal(response.headers.Allow, 'POST');
    assert.deepEqual(response.body, method === 'HEAD' ? undefined : failed('method_not_allowed'));
  }
  const other = await invoke(h.bridge, undefined, { url: '/api/auth/status' });
  assert.equal(other.handled, false); assert.equal(other.response.writes, 0); assert.equal(h.calls.factories.length, 0);
});

test('Origin, Host and JSON MIME must be exact single headers; forwarding headers cannot authorize a request', async () => {
  const h = harness();
  const base = req().headers;
  for (const headers of [{ ...base, origin: undefined }, { ...base, host: undefined },
    { ...base, origin: 'http://localhost:4206' }, { ...base, host: 'outside.invalid', 'x-forwarded-host': '127.0.0.1:4206' },
    { ...base, origin: ['http://127.0.0.1:4206'] }, { ...base, host: '127.0.0.1:4206,127.0.0.1:4206' }])
    safe((await invoke(h.bridge, undefined, { headers })).response, 403, failed('signup_origin_rejected'));
  const rawHeaders = Object.entries(base).flat(); rawHeaders.push('HOST', '127.0.0.1:4206');
  safe((await invoke(h.bridge, undefined, { rawHeaders })).response, 403, failed('signup_origin_rejected'));
  for (const type of ['text/plain', 'application/json; charset=latin1', 'application/json, text/plain'])
    safe((await invoke(h.bridge, undefined, { headers: { ...base, 'content-type': type } })).response, 415, failed('unsupported_media_type'));
  assert.equal(h.calls.factories.length, 0);
  safe((await invoke(h.bridge, undefined, { headers: { ...base, 'content-type': 'application/json; charset=UTF-8' } })).response, 202, pending);
});

test('strict UTF-8/JSON and the two string fields reject profile, roles, redirects and duplicate members without side effects', async () => {
  const h = harness();
  const bad = [Buffer.from([0xff]), Buffer.from('{'), Buffer.from('null'), Buffer.from('[]'),
    Buffer.from(JSON.stringify({ email, password, role: 'service_role' })),
    Buffer.from(JSON.stringify({ email, password, redirect_to: 'https://outside.invalid' })),
    Buffer.from(JSON.stringify({ email, password: 123 })),
    Buffer.from(`{"email":"${email}","password":"${password}","email":"other@gmail.com"}`)];
  for (const wire of bad) safe((await invoke(h.bridge, undefined, {}, wire)).response, 400, failed('invalid_signup'));
  assert.equal(h.calls.factories.length, 0);
});

test('the streamed 4096-byte limit is enforced even with a false small Content-Length', async () => {
  for (const size of [4096, 4097]) {
    const h = harness(); const body = Buffer.from(JSON.stringify({ email, password }).padEnd(size, ' '));
    const headers = { ...req().headers, 'content-length': '1' };
    const { response } = await invoke(h.bridge, undefined, { headers }, body);
    safe(response, size === 4096 ? 202 : 413, size === 4096 ? pending : failed('signup_body_too_large'));
    assert.equal(h.calls.factories.length, size === 4096 ? 1 : 0);
  }
});

test('email domains are not restricted and passwords count Unicode code points/UTF-8 bytes without normalization', async () => {
  for (const address of ['scientist@qq.com', 'scientist@163.com', 'scientist@gmail.com']) {
    const h = harness(); safe((await invoke(h.bridge, { email: address, password })).response, 202, pending);
  }
  for (const secret of ['😀'.repeat(12), '😀'.repeat(18), 'a\u0301'.repeat(6)]) {
    const h = harness(); safe((await invoke(h.bridge, { email, password: secret })).response, 202, pending);
    assert.equal(h.calls.signup[0].password, secret);
  }
  for (const input of [{ email: 'bad@@gmail.com', password }, { email: 'a..b@gmail.com', password },
    { email: 'a@-bad.example', password }, { email, password: 'a'.repeat(11) },
    { email, password: '😀'.repeat(19) }, { email, password: 'a'.repeat(12) + '\ud800' }]) {
    const h = harness(); safe((await invoke(h.bridge, input)).response, 400, failed('invalid_signup')); assert.equal(h.calls.factories.length, 0);
  }
});

test('sessions, tokens, verified users and malformed official shapes are never pending success', async () => {
  const results = [null, {}, { data: null, error: null },
    { data: { user: null, session: null }, error: null },
    { ...good(email), data: { ...good(email).data, session: {} } },
    { ...good(email), access_token: 'SYNTHETIC-token' },
    { ...good(email), data: { ...good(email).data, refresh_token: 'SYNTHETIC-token' } },
    { ...good(email), data: { ...good(email).data, user: { ...good(email).data.user, email_confirmed_at: '2026-01-01' } } },
    { ...good(email), data: { ...good(email).data, user: { ...good(email).data.user, confirmed_at: '' } } },
    { data: { user: { ...good(email).data.user, confirmed_at: '2026-01-01' }, session: null }, error: { code: 'user_already_exists' } },
  ];
  for (const value of results) {
    const h = harness({ createAuthClient: () => ({ signUp: async () => value }) });
    safe((await invoke(h.bridge)).response, 503, failed('signup_unavailable'));
  }
});

test('SDK errors use finite constants; transport exceptions express an unknown outcome without leaking details', async () => {
  for (const [code, status, result] of [['weak_password', 400, 'invalid_signup'],
    ['over_email_send_rate_limit', 429, 'signup_rate_limited'], ['smtp_error', 503, 'signup_unavailable']]) {
    const h = harness({ createAuthClient: () => ({ signUp: async () => ({ data: { user: null, session: null }, error: { code, message: password } }) }) });
    const { response } = await invoke(h.bridge); safe(response, status, failed(result));
    if (status === 429) assert.equal(response.headers['Retry-After'], '60');
  }
  for (const createAuthClient of [() => { throw Error(password); }, () => ({}), () => ({ signUp() { throw Error(password); } })]) {
    const h = harness({ createAuthClient }); const { response } = await invoke(h.bridge);
    assert.ok([503, 504].includes(response.status)); assert.ok(!JSON.stringify(response.body).includes(password));
  }
});

test('email cooldown and the five-per-source window use trusted socket identity, with expiring counters', async () => {
  const h = harness(); safe((await invoke(h.bridge)).response, 202, pending);
  safe((await invoke(h.bridge, { email: email.toUpperCase(), password })).response, 429, failed('signup_rate_limited'));
  assert.equal(h.calls.factories.length, 1);
  h.clock.advance(60000); safe((await invoke(h.bridge)).response, 202, pending);
  for (let i = 0; i < 3; i++) {
    const headers = { ...req().headers, 'x-forwarded-for': `198.51.100.${i + 1}` };
    safe((await invoke(h.bridge, { email: `r${i}@gmail.com`, password }, { headers })).response, 202, pending);
  }
  safe((await invoke(h.bridge, { email: 'sixth@gmail.com', password })).response, 429, failed('signup_rate_limited'));
  assert.equal(h.calls.factories.length, 5);
  h.clock.advance(540000); safe((await invoke(h.bridge, { email: 'sixth@gmail.com', password })).response, 202, pending);
});

test('TTL maps have a hard capacity, fail closed, and recover when entries expire', async () => {
  const h = harness();
  for (let i = 0; i < 256; i++) {
    const socket = { remoteAddress: `10.0.${Math.floor(i / 250)}.${i % 250 + 1}` };
    safe((await invoke(h.bridge, { email: `researcher${i}@gmail.com`, password }, { socket })).response, 202, pending);
  }
  const input = { email: 'capacity@gmail.com', password }, options = { socket: { remoteAddress: '10.1.0.1' } };
  safe((await invoke(h.bridge, input, options)).response, 503, failed('signup_unavailable')); assert.equal(h.calls.factories.length, 256);
  h.clock.advance(600000); safe((await invoke(h.bridge, input, options)).response, 202, pending);
});

test('one 15-second deadline covers slow body, factory and SDK; a late SDK result cannot overwrite unknown', async () => {
  const h = harness(), factoryEntered = deferred(), clientResult = deferred(), signupEntered = deferred(), sdkResult = deferred();
  const b = createAuthSignupBridge({ enabled: true, now: h.clock.now, setTimer: h.clock.setTimer, clearTimer: h.clock.clearTimer,
    createAuthClient: async ({ signal }) => { factoryEntered.resolve(signal); return clientResult.promise; } });
  const request = req(), response = res(); const completion = b(request, response);
  h.clock.advance(10000); request.end(Buffer.from(JSON.stringify({ email, password }))); const signal = await factoryEntered.promise;
  h.clock.advance(3000); clientResult.resolve({ signUp: () => { signupEntered.resolve(); return sdkResult.promise; } }); await signupEntered.promise;
  h.clock.advance(2000); await completion; safe(response, 504, failed('signup_result_unknown')); assert.equal(signal.aborted, true);
  sdkResult.resolve(good(email)); await nextTurn(); assert.equal(response.writes, 1); assert.equal(h.clock.timers.size, 0); request.destroy();
});

test('a timed-out body cannot later create a client and a timed-out factory cannot later call signUp', async () => {
  const h = harness(); const request = req(), response = res(); const completion = h.bridge(request, response);
  h.clock.advance(15000); await completion; safe(response, 504, failed('signup_result_unknown'));
  request.end(Buffer.from(JSON.stringify({ email, password }))); await nextTurn(); assert.equal(h.calls.factories.length, 0); request.destroy();
  const entered = deferred(), result = deferred(); let signupCalls = 0;
  const f = harness({ createAuthClient: async () => { entered.resolve(); return result.promise; } });
  const pendingCall = invoke(f.bridge); await entered.promise; f.clock.advance(15000);
  safe((await pendingCall).response, 504, failed('signup_result_unknown'));
  result.resolve({ signUp() { signupCalls++; return good(email); } }); await nextTurn(); assert.equal(signupCalls, 0);
});

test('dispose and client disconnect abort pending factories and prevent all late signup or response writes', async () => {
  for (const mode of ['dispose', 'disconnect']) {
    const entered = deferred(), result = deferred(); let signupCalls = 0;
    const h = harness({ createAuthClient: async ({ signal }) => { entered.resolve(signal); return result.promise; } });
    const request = req(), response = res(); const completion = h.bridge(request, response);
    request.end(Buffer.from(JSON.stringify({ email, password }))); const signal = await entered.promise;
    if (mode === 'dispose') h.bridge.dispose(); else { response.destroyed = true; response.emit('close'); }
    await completion; assert.equal(signal.aborted, true);
    result.resolve({ signUp() { signupCalls++; return good(email); } }); await nextTurn(); assert.equal(signupCalls, 0);
    if (mode === 'dispose') {
      safe(response, 504, failed('signup_result_unknown'));
      safe((await invoke(h.bridge)).response, 503, failed('signup_unavailable'));
    } else assert.equal(response.writes, 0);
    assert.equal(h.clock.timers.size, 0); request.destroy();
  }
});
