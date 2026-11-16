import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { setImmediate as nextTurn } from 'node:timers/promises';
import { createAuthStatusBridge } from '../auth-status-bridge.mjs';

const target = 'http://127.0.0.1:57421/auth/v1/health';
const key = 'AUTH_STATUS_SYNTHETIC_KEY';
const good = { name: 'GoTrue', version: 'v2.197.0', description: 'discard this' };
const ok = { status: 'ok', service: 'Cy_Omics_v2.0-auth', version: '2.197.0' };
const unavailable = { error: { code: 'auth_unavailable' } };
const deferred = () => {
  let resolve;
  const promise = new Promise(r => { resolve = r; });
  return { promise, resolve };
};
function request(method = 'GET', url = '/api/auth/status') {
  return Object.assign(new EventEmitter(), { method, url,
    headers: { authorization: 'Bearer client-secret', cookie: 'session=client', origin: 'https://outside.invalid', apikey: 'client-key' },
    body: 'must not forward' });
}
function response() {
  return Object.assign(new EventEmitter(), {
    destroyed: false, writableEnded: false, writes: 0,
    writeHead(status, headers) { this.status = status; this.headers = headers; this.writes++; },
    end(body) { this.body = body ? JSON.parse(body) : undefined; this.writableEnded = true; },
  });
}
function harness(options = {}) {
  const calls = { keys: 0, fetch: [] };
  const bridge = createAuthStatusBridge({ enabled: true,
    keyProvider: async () => { calls.keys++; return key; },
    fetchImpl: async (...args) => { calls.fetch.push(args); return Response.json(good); }, ...options });
  return { bridge, calls };
}
async function invoke(bridge, req = request()) {
  const res = response();
  const handled = await bridge(req, res);
  return { res, handled };
}
function safeHeaders(res) {
  assert.equal(res.headers['Content-Type'], 'application/json; charset=utf-8');
  assert.equal(res.headers['Cache-Control'], 'no-store');
  assert.equal(res.headers['X-Content-Type-Options'], 'nosniff');
  assert.equal(res.headers['Referrer-Policy'], 'no-referrer');
  assert.deepEqual(Object.keys(res.headers).sort(), ['Cache-Control', 'Content-Type', 'Referrer-Policy', 'X-Content-Type-Options'].sort());
  assert.equal(res.writes, 1);
}

test('exact GET uses only the fixed target and service key, returning constants without upstream headers or extra data', async () => {
  const { bridge, calls } = harness({ fetchImpl: async (...args) => {
    calls.fetch.push(args);
    return Response.json({ ...good, private_extra: 'never return' }, { headers: {
      'set-cookie': 'private=upstream', location: 'https://outside.invalid', 'access-control-allow-origin': '*',
    } });
  } });
  const { res, handled } = await invoke(bridge);
  assert.equal(handled, true); assert.equal(res.status, 200); assert.deepEqual(res.body, ok); safeHeaders(res);
  assert.equal(calls.keys, 1); assert.equal(calls.fetch.length, 1);
  const [url, options] = calls.fetch[0]; assert.equal(url, target);
  assert.deepEqual(Object.keys(options).sort(), ['method', 'redirect', 'credentials', 'cache', 'signal', 'headers'].sort());
  assert.deepEqual({ ...options, signal: undefined }, { method: 'GET', redirect: 'error', credentials: 'omit', cache: 'no-store',
    signal: undefined, headers: { Accept: 'application/json', apikey: key } });
  assert.equal(options.signal.aborted, true);
});

test('methods and URL variants are rejected before key access; unrelated routes remain untouched', async () => {
  const { bridge, calls } = harness();
  for (const method of ['HEAD', 'POST', 'OPTIONS']) {
    const { res } = await invoke(bridge, request(method)); assert.equal(res.status, 405); assert.equal(res.headers.Allow, 'GET');
    if (method === 'HEAD') assert.equal(res.body, undefined);
  }
  for (const url of ['/api/auth/status?', '/api/auth/status?target=http://outside.invalid', '/api/auth/status/',
    'http://outside.invalid/api/auth/status', '/api/auth/./status', '/api/auth/%73tatus', '/api/auth/status#fragment']) {
    const { res } = await invoke(bridge, request('GET', url)); assert.equal(res.status, 404);
  }
  const untouched = await invoke(bridge, request('GET', '/api/health'));
  assert.equal(untouched.handled, false); assert.equal(untouched.res.writes, 0);
  assert.equal(calls.keys, 0); assert.equal(calls.fetch.length, 0);
});

test('default disabled and explicitly disabled perform no key access or network', async () => {
  assert.notEqual(process.env.CYOMICS_AUTH_STATUS_ENABLED, '1');
  let effects = 0;
  const options = { keyProvider: async () => { effects++; throw Error('must not read'); },
    fetchImpl: async () => { effects++; throw Error('must not fetch'); } };
  for (const config of [options, { ...options, enabled: false }]) {
    const { res } = await invoke(createAuthStatusBridge(config)); assert.equal(res.status, 503); assert.deepEqual(res.body, unavailable); safeHeaders(res);
  }
  assert.equal(effects, 0);
});

test('missing, invalid or failed key providers are unavailable without a network request', async () => {
  for (const keyProvider of [async () => undefined, async () => 'bad\r\nkey', async () => { throw Error('private detail'); }]) {
    const { bridge, calls } = harness({ keyProvider }); const { res } = await invoke(bridge);
    assert.equal(res.status, 503); assert.deepEqual(res.body, unavailable); safeHeaders(res); assert.equal(calls.fetch.length, 0);
  }
});

test('wrong identity/version, malformed JSON, HTTP failures and redirects never produce healthy output or leak data', async () => {
  const replies = [
    () => Response.json({ ...good, name: 'OtherService' }),
    () => Response.json({ ...good, version: '2.197.0' }),
    () => Response.json({ ...good, version: 'v2.198.0' }),
    () => Response.json({ ...good, version: 'v2.197.0\nprivate' }),
    () => Response.json([good]), () => new Response('{private', { headers: { 'content-type': 'application/json' } }),
    () => new Response(JSON.stringify(good), { headers: { 'content-type': 'text/plain' } }),
    () => Response.json(good, { status: 401 }), () => Response.json(good, { status: 503 }),
    () => new Response(null, { status: 302, headers: { location: 'https://outside.invalid' } }),
    () => { const reply = Response.json(good); Object.defineProperty(reply, 'redirected', { value: true }); return reply; },
    () => { throw Error('private network exception'); },
  ];
  for (const reply of replies) {
    const { bridge } = harness({ fetchImpl: async () => reply() }); const { res } = await invoke(bridge);
    assert.equal(res.status, 503); assert.deepEqual(res.body, unavailable); safeHeaders(res);
  }
});

test('4 KiB bodies are accepted and the first excess byte is rejected and cancelled', async () => {
  for (const bytes of [4096, 4097]) {
    const body = JSON.stringify(good).padEnd(bytes, ' ');
    let cancelled = false;
    const { bridge } = harness({ fetchImpl: async () => new Response(new ReadableStream({
      start(controller) { controller.enqueue(new TextEncoder().encode(body)); if (bytes === 4096) controller.close(); },
      cancel() { cancelled = true; },
    }), { headers: { 'content-type': 'application/json' } }) });
    const { res } = await invoke(bridge); assert.equal(res.status, bytes === 4096 ? 200 : 503);
    assert.deepEqual(res.body, bytes === 4096 ? ok : unavailable);
    if (bytes === 4097) { await nextTurn(); assert.equal(cancelled, true); }
  }
});

test('the two-second deadline starts before key loading; a late key cannot issue fetch', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const keyResult = deferred(), entered = deferred();
  const { bridge, calls } = harness({ keyProvider: signal => { entered.resolve(signal); return keyResult.promise; } });
  const pending = invoke(bridge); const signal = await entered.promise;
  t.mock.timers.tick(2000); const { res } = await pending;
  assert.equal(res.status, 503); assert.deepEqual(res.body, unavailable); assert.equal(signal.aborted, true);
  keyResult.resolve(key); await nextTurn(); assert.equal(calls.fetch.length, 0);
});

test('key loading and stalled body reads share one two-second budget and cancel the stream', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const keyResult = deferred(), keyEntered = deferred(), entered = deferred(); let cancelled = false;
  const { bridge } = harness({ keyProvider: async () => { keyEntered.resolve(); await keyResult.promise; return key; },
    fetchImpl: async () => new Response(new ReadableStream({
    pull() { entered.resolve(); }, cancel() { cancelled = true; },
  }), { headers: { 'content-type': 'application/json' } }) });
  const pending = invoke(bridge); await keyEntered.promise;
  t.mock.timers.tick(1500); keyResult.resolve(); await entered.promise;
  t.mock.timers.tick(500); const { res } = await pending;
  assert.equal(res.status, 503); assert.deepEqual(res.body, unavailable); await nextTurn(); assert.equal(cancelled, true);
});

test('dispose and client disconnect cancel pending keys; subsequent or late work cannot fetch', async () => {
  for (const mode of ['dispose', 'disconnect']) {
    const keyResult = deferred(), entered = deferred();
    const { bridge, calls } = harness({ keyProvider: signal => { entered.resolve(signal); return keyResult.promise; } });
    const req = request(), res = response(); const pending = bridge(req, res); const signal = await entered.promise;
    if (mode === 'dispose') bridge.dispose(); else { res.destroyed = true; res.emit('close'); }
    await pending; assert.equal(signal.aborted, true);
    keyResult.resolve(key); await nextTurn(); assert.equal(calls.fetch.length, 0);
    if (mode === 'dispose') { const next = await invoke(bridge); assert.equal(next.res.status, 503); }
    else assert.equal(res.writes, 0);
  }
});

test('a fetch that resolves after disposal has its body cancelled and cannot overwrite the fixed failure', async () => {
  const result = deferred(), entered = deferred(); let cancelled = false;
  const { bridge } = harness({ fetchImpl: async () => { entered.resolve(); return result.promise; } });
  const pending = invoke(bridge); await entered.promise; bridge.dispose();
  const { res } = await pending; assert.equal(res.status, 503); assert.deepEqual(res.body, unavailable);
  result.resolve(new Response(new ReadableStream({ cancel() { cancelled = true; } }), { headers: { 'content-type': 'application/json' } }));
  await nextTurn();
  assert.equal(cancelled, true); assert.equal(res.writes, 1);
});
