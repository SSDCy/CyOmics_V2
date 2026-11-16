import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { setImmediate as nextTurn } from 'node:timers/promises';
import { createAuthRegisterRuntime } from '../auth-register-runtime.mjs';
import { createHash } from 'node:crypto';

const origin = 'http://127.0.0.1:4206', email = 'synthetic@example.com';
const browserName = 'cyomics_register_browser', receiptName = 'cyomics_register_receipt';
const pending = { status: 'pending_confirmation' }, unconfirmed = { status: 'unconfirmed', emailVerified: false };
const confirmed = { status: 'verified', emailVerified: true };
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
function response() {
  return Object.assign(new EventEmitter(), { destroyed: false, writableEnded: false, writes: 0, headers: {},
    getHeader(name) { return this.headers[name]; }, setHeader(name, value) { this.headers[name] = value; },
    writeHead(status, headers) { this.status = status; Object.assign(this.headers, headers); this.writes++; },
    end(body) { this.body = body ? JSON.parse(body) : undefined; this.writableEnded = true; } });
}
function request(url, method = 'GET', cookie, extra = {}) {
  return Object.assign(new PassThrough(), { url, method, headers: { host: '127.0.0.1:4206', origin,
    'content-type': 'application/json', ...(cookie ? { cookie } : {}) }, socket: { remoteAddress: '127.0.0.1' } }, extra);
}
function harness(t, options = {}) {
  let time = 0, sequence = 0;
  const timers = new Map(), calls = [], signup = [], exchange = [], flows = [];
  const settings = { enabled: true, now: () => time,
    setTimer: (fn, delay) => { assert.equal(delay, 15000); timers.set(++sequence, { fn, at: time + delay }); return sequence; },
    clearTimer: id => timers.delete(id), keyProvider: () => { throw Error('no private key read'); },
    fetchImpl: () => { throw Error('no real network'); },
    createClient: async config => {
      calls.push(config); const { flow } = config; if (!flows.includes(flow)) flows.push(flow);
      return { signUp: async input => {
        signup.push(input); flow.verifier = 'SYNTHETIC-verifier';
        return { data: { user: { id: 'synthetic-user', email: input.email }, session: null }, error: null };
      }, confirm: async code => { exchange.push(code); return true; }, dispose: async () => {} };
    }, ...options };
  const runtime = createAuthRegisterRuntime(settings); t.after(() => runtime.dispose());
  const invoke = async (url, { method = 'GET', cookie, extra, body } = {}) => {
    const req = request(url, method, cookie, extra), res = response(); const completion = runtime(req, res);
    req.end(body === undefined ? undefined : JSON.stringify(body));
    const handled = await completion; req.destroy(); return { req, res, handled };
  };
  const bootstrap = async () => {
    const { res, handled } = await invoke('/'); assert.equal(handled, false);
    const wire = res.headers['Set-Cookie'][0];
    assert.match(wire, /; Path=\/; HttpOnly; SameSite=Lax; Max-Age=900$/);
    assert.ok(!wire.includes('Domain=')); assert.ok(!wire.includes('Secure')); return wire.split(';')[0];
  };
  const register = async (cookie, address = email) => invoke('/api/auth/signup', {
    method: 'POST', cookie, body: { email: address, password: 'SYNTHETIC-password-123' },
  });
  const callback = (flow = flows[0]) => '/auth/callback?flow=' + flow.id + '&code=SYNTHETIC-code';
  const receipt = res => res.headers['Set-Cookie'][0].split(';')[0];
  const advance = ms => { time += ms; for (const [id, task] of [...timers]) if (task.at <= time) { timers.delete(id); task.fn(); } };
  return { runtime, settings, calls, signup, exchange, flows, invoke, bootstrap, register, callback, receipt, advance, timers };
}
test('V2 uses same browser flow, 303 clean redirect and an HttpOnly receipt with one GET confirmation and no session', async t => {
  const h = harness(t), cookie = await h.bootstrap(); const post = await h.register(cookie);
  assert.equal(post.res.status, 202); assert.deepEqual(post.res.body, pending);
  assert.equal(h.calls.length, 1); assert.equal(h.signup.length, 1);
  const result = await h.invoke(h.callback(), { cookie, extra: { headers: { host: '127.0.0.1:4206', cookie } } });
  assert.equal(result.res.status, 303); assert.equal(result.res.headers.Location, '/auth/callback');
  assert.equal(result.res.body, undefined); assert.equal(h.exchange.length, 1); assert.equal(h.calls.length, 2);
  const wire = result.res.headers['Set-Cookie'][0];
  assert.match(wire, /; Path=\/api\/auth\/confirmation; HttpOnly; SameSite=Lax; Max-Age=900$/);
  assert.equal(h.flows[0].verifier, undefined); assert.equal(h.flows[0].closed, true);
  const receipt = h.receipt(result.res);
  const clean = await h.invoke('/auth/callback', { cookie }); assert.equal(clean.handled, false);
  const confirmation = await h.invoke('/api/auth/confirmation', { cookie: cookie + '; ' + receipt });
  assert.deepEqual(confirmation.res.body, confirmed); assert.equal(confirmation.res.status, 200);
  assert.equal(confirmation.res.headers['Cache-Control'], 'no-store'); assert.equal(h.calls.length, 2);
  const replay = await h.invoke(h.callback(), { cookie }); assert.equal(replay.res.status, 303); assert.equal(h.calls.length, 2);
  assert.equal(h.receipt(replay.res), receipt); assert.equal(h.timers.size, 0);
});
test('default disabled runtime never creates a client, reads keys, sets cookies or calls Auth', async t => {
  const h = harness(t, { enabled: false });
  const root = await h.invoke('/'); assert.equal(root.res.headers['Set-Cookie'], undefined);
  assert.equal((await h.register()).res.status, 503);
  assert.equal((await h.invoke('/api/auth/confirmation')).res.status, 503);
  assert.equal((await h.invoke('/auth/callback?flow=x&code=y')).res.status, 303);
  assert.equal(h.calls.length, 0);
});
test('signup requires a preexisting browser cookie and retains the reviewed email/source rate limit', async t => {
  const h = harness(t); assert.equal((await h.register()).res.status, 409); assert.equal(h.calls.length, 0);
  const cookie = await h.bootstrap(); assert.equal((await h.register(cookie)).res.status, 202);
  assert.equal((await h.register(cookie)).res.status, 429); assert.equal(h.calls.length, 1);
  assert.equal((await h.register(cookie, 'other@example.com')).res.status, 202);
  assert.equal(h.flows.length, 2); assert.notEqual(h.flows[0].id, h.flows[1].id);
});
test('no receipt, foreign browser, forged or duplicate cookies give unconfirmed with zero Auth calls', async t => {
  const h = harness(t), cookie = await h.bootstrap(); await h.register(cookie);
  const result = await h.invoke(h.callback(), { cookie }); const receipt = h.receipt(result.res), before = h.calls.length;
  const other = await h.bootstrap();
  for (const value of [undefined, cookie, receipt, other + '; ' + receipt, cookie + '; ' + receiptName + '=' + 'X'.repeat(43),
    cookie + '; ' + cookie + '; ' + receipt, cookie + '; ' + receipt + '; ' + receipt]) {
    const check = await h.invoke('/api/auth/confirmation', { cookie: value });
    assert.equal(check.res.status, 200); assert.deepEqual(check.res.body, unconfirmed);
  }
  assert.equal(h.calls.length, before);
});
test('unknown, foreign, expired or malformed callbacks cannot exchange and always remove sensitive query by 303', async t => {
  const h = harness(t), cookie = await h.bootstrap(); await h.register(cookie); const other = await h.bootstrap();
  const urls = ['/auth/callback?', h.callback() + '&flow=duplicate', h.callback() + '&unexpected=1',
    '/auth/callback?flow=' + 'X'.repeat(22) + '&code=synthetic', '/auth/callback?error=access_denied',
    h.callback().replace('SYNTHETIC-code', '%0A'), h.callback() + '#' + 'fragment'];
  for (const url of urls) { const { res } = await h.invoke(url, { cookie }); assert.equal(res.status, 303); assert.equal(res.headers.Location, '/auth/callback'); }
  for (const value of [undefined, other, cookie + '; ' + cookie]) assert.equal((await h.invoke(h.callback(), { cookie: value })).res.status, 303);
  assert.equal(h.calls.length, 1); h.advance(900001);
  assert.equal((await h.invoke(h.callback(), { cookie })).res.status, 303); assert.equal(h.calls.length, 1);
  assert.equal(h.flows[0].verifier, undefined);
});
test('wrong host/origin, duplicate headers, methods and route aliases cannot authorize confirmation', async t => {
  const h = harness(t);
  for (const url of ['/api/auth/confirmation?', '/api/auth/confirmation/', '/api/auth/%63onfirmation',
    'http://outside.invalid/api/auth/confirmation', '/auth/%63allback', '/auth/callback/'])
    assert.equal((await h.invoke(url)).res.status, 404);
  for (const method of ['POST', 'HEAD', 'OPTIONS']) {
    const { res } = await h.invoke('/api/auth/confirmation', { method }); assert.equal(res.status, 405); assert.equal(res.headers.Allow, 'GET');
  }
  for (const extra of [{ headers: { host: 'evil.invalid', origin } }, { headers: { host: '127.0.0.1:4206', origin: 'https://evil.invalid' } },
    { rawHeaders: ['host', '127.0.0.1:4206', 'host', '127.0.0.1:4206'] }])
    assert.equal((await h.invoke('/api/auth/confirmation', { extra })).res.status, 403);
  assert.equal(h.calls.length, 0);
});
test('callback exchange/getUser false or thrown never creates a verified receipt and cannot retry exchange', async t => {
  for (const confirm of [async () => false, async () => { throw Error('SYNTHETIC-private-detail'); }]) {
    const h = harness(t), cookie = await h.bootstrap(); await h.register(cookie);
    const base = h.settings.createClient;
    h.runtime.dispose();
    // A new isolated runtime uses the same mock registration path and an unsuccessful confirmation.
    const k = harness(t, { createClient: async settings => ({ ...await base(settings), confirm }) });
    const browser = await k.bootstrap(); await k.register(browser);
    // The registration factory above records its flow in h, never in k.
    const flow = h.flows.at(-1), url = '/auth/callback?flow=' + flow.id + '&code=SYNTHETIC-code';
    const result = await k.invoke(url, { cookie: browser }); assert.equal(result.res.status, 303);
    assert.match(result.res.headers['Set-Cookie'][0], /Max-Age=0$/);
    assert.deepEqual((await k.invoke('/api/auth/confirmation', { cookie: browser })).res.body, unconfirmed);
    const count = h.calls.length; await k.invoke(url, { cookie: browser }); assert.equal(h.calls.length, count);
  }
});
test('one callback deadline includes a slow factory; late factory cannot exchange or issue a receipt', async t => {
  const h = harness(t), cookie = await h.bootstrap(); await h.register(cookie);
  // Replace behavior through the injected function without touching any live SDK or service.
  const slow = deferred(); let confirmCalls = 0, createCalls = 0;
  const k = harness(t, { createClient: async settings => {
    if (++createCalls === 1) {
      h.flows.push(settings.flow);
      return { signUp: async input => { settings.flow.verifier = 'SYNTHETIC-verifier'; return { data: { user: { id: 'synthetic', email: input.email }, session: null }, error: null }; } };
    }
    await slow.promise; return { confirm: async () => { confirmCalls++; return true; }, dispose: async () => {} };
  } });
  const browser = await k.bootstrap(); await k.register(browser); const flow = h.flows.at(-1);
  const req = request('/auth/callback?flow=' + flow.id + '&code=SYNTHETIC-code', 'GET', browser), res = response();
  const completion = k.runtime(req, res); await nextTurn(); k.advance(15000); await completion;
  assert.equal(res.status, 303); assert.match(res.headers['Set-Cookie'][0], /Max-Age=0$/);
  slow.resolve(); await nextTurn(); assert.equal(confirmCalls, 0); assert.equal(res.writes, 1);
  assert.equal(flow.verifier, undefined); req.destroy();
});
test('receipt expires, and dispose closes all verifiers without creating product login state', async t => {
  const h = harness(t), cookie = await h.bootstrap(); await h.register(cookie);
  const result = await h.invoke(h.callback(), { cookie }), receipt = h.receipt(result.res);
  h.advance(900001);
  assert.deepEqual((await h.invoke('/api/auth/confirmation', { cookie: cookie + '; ' + receipt })).res.body, unconfirmed);
  h.runtime.dispose(); assert.equal((await h.invoke('/api/auth/confirmation', { cookie })).res.status, 503);
  assert.equal(h.flows[0].verifier, undefined);
});
test('parallel callbacks exchange only once and a late confirmation after deadline cannot become verified', async t => {
  const slow = deferred(); let createCount = 0, confirms = 0, flow;
  const h = harness(t, { createClient: async settings => {
    createCount++; flow = settings.flow;
    return { signUp: async input => {
      flow.verifier = 'SYNTHETIC-verifier';
      return { data: { user: { id: 'synthetic', email: input.email }, session: null }, error: null };
    }, confirm: async () => { confirms++; return slow.promise; }, dispose: async () => {} };
  } });
  const cookie = await h.bootstrap(); await h.register(cookie);
  const url = '/auth/callback?flow=' + flow.id + '&code=SYNTHETIC-code';
  const req = request(url, 'GET', cookie), res = response(), completion = h.runtime(req, res);
  await nextTurn(); const concurrent = await h.invoke(url, { cookie });
  assert.equal(concurrent.res.status, 303); assert.equal(createCount, 2); assert.equal(confirms, 1);
  h.advance(15000); await completion; assert.match(res.headers['Set-Cookie'][0], /Max-Age=0$/);
  slow.resolve(true); await nextTurn();
  assert.equal(res.writes, 1); assert.equal(flow.verifier, undefined);
  assert.deepEqual((await h.invoke('/api/auth/confirmation', { cookie })).res.body, unconfirmed); req.destroy();
});
test('disconnect and dispose abort a callback and prevent a late verified result', async t => {
  for (const mode of ['disconnect', 'dispose']) {
    const slow = deferred(); let flow, signal;
    const h = harness(t, { createClient: async settings => {
      flow = settings.flow; signal = settings.signal;
      return { signUp: async input => {
        flow.verifier = 'SYNTHETIC-verifier';
        return { data: { user: { id: 'synthetic', email: input.email }, session: null }, error: null };
      }, confirm: async () => slow.promise, dispose: async () => {} };
    } });
    const cookie = await h.bootstrap(); await h.register(cookie);
    const req = request('/auth/callback?flow=' + flow.id + '&code=SYNTHETIC-code', 'GET', cookie), res = response();
    const completion = h.runtime(req, res); await nextTurn();
    if (mode === 'disconnect') { res.destroyed = true; res.emit('close'); } else h.runtime.dispose();
    await completion; assert.equal(signal.aborted, true); slow.resolve(true); await nextTurn();
    assert.equal(flow.verifier, undefined);
    assert.ok(!res.headers['Set-Cookie']?.some(value => value.includes('Max-Age=900'))); req.destroy();
  }
});
test('a signup timeout retains its isolated verifier so a delivered email can still confirm in the same browser', async t => {
  const slow = deferred(); let flow, signups = 0;
  const h = harness(t, { createClient: async settings => {
    flow = settings.flow;
    return { signUp: async () => { signups++; flow.verifier = 'SYNTHETIC-verifier'; return slow.promise; },
      confirm: async () => true, dispose: async () => {} };
  } });
  const cookie = await h.bootstrap(), req = request('/api/auth/signup', 'POST', cookie), res = response();
  const completion = h.runtime(req, res); req.end(JSON.stringify({ email, password: 'SYNTHETIC-password-123' }));
  await nextTurn(); h.advance(15000); await completion;
  assert.equal(res.status, 504); assert.equal(res.body.error.code, 'signup_result_unknown');
  assert.equal(signups, 1); assert.equal(flow.closed, false); assert.equal(typeof flow.verifier, 'string');
  const callback = await h.invoke('/auth/callback?flow=' + flow.id + '&code=SYNTHETIC-code', { cookie });
  const receipt = h.receipt(callback.res);
  assert.deepEqual((await h.invoke('/api/auth/confirmation', { cookie: cookie + '; ' + receipt })).res.body, confirmed);
  slow.resolve({ data: { user: { id: 'synthetic', email }, session: null }, error: null }); await nextTurn();
  assert.equal(res.writes, 1); req.destroy();
});
test('a callback already disconnected before dispatch cannot create an SDK client or consume the flow', async t => {
  const h = harness(t), cookie = await h.bootstrap(); await h.register(cookie);
  for (const mode of ['aborted', 'destroyed', 'ended']) {
    const req = request(h.callback(), 'GET', cookie), res = response();
    if (mode === 'aborted') req.aborted = true;
    else if (mode === 'destroyed') res.destroyed = true;
    else res.writableEnded = true;
    assert.equal(await h.runtime(req, res), true); assert.equal(h.calls.length, 1);
    assert.equal(h.flows[0].used, false); assert.equal(res.writes, 0); req.destroy();
  }
});

test('[diag] runtime observations require opt-in and preserve the complete V2 success and receipt contract', async t => {
  const silent = [], off = harness(t, { diagnosticsSink: value => silent.push(value) });
  const offCookie = await off.bootstrap(); await off.register(offCookie); await off.invoke(off.callback(), { cookie: offCookie });
  assert.equal(silent.length, 0);
  const events = [], h = harness(t, { diagnosticsEnabled: true, diagnosticsSink: value => events.push(value) });
  const browser = await h.bootstrap(); await h.register(browser);
  const callback = await h.invoke(h.callback(), { cookie: browser });
  assert.equal(callback.res.status, 303); assert.equal(callback.res.headers.Location, '/auth/callback');
  const receipt = h.receipt(callback.res);
  assert.deepEqual((await h.invoke('/api/auth/confirmation', { cookie: browser + '; ' + receipt })).res.body, confirmed);
  assert.ok(events.some(e => e.stage === 'callback' && e.result === 'eligible' && e.browserMatches && e.verifierPresent));
  assert.ok(events.some(e => e.stage === 'receipt' && e.result === 'issued' && e.receiptIssued));
  assert.ok(events.some(e => e.stage === 'confirmation' && e.result === 'verified' && e.receiptState === 'matched'));
  assert.equal(h.calls.length, 2); assert.equal(h.exchange.length, 1);
  const secretValues = [browser.split('=')[1], receipt.split('=')[1], h.flows[0].id, email, 'SYNTHETIC-password-123', 'SYNTHETIC-code', 'SYNTHETIC-verifier'];
  const output = JSON.stringify(events);
  for (const value of secretValues) {
    assert.equal(output.includes(value), false, 'diagnostic contains a secret sentinel');
    assert.equal(output.includes(createHash('sha256').update(value).digest('hex')), false, 'diagnostic contains a sentinel hash');
  }
});
test('[diag] callback shape is a fixed enum; query error hints are untrusted and malformed names/values never reach the sink', async t => {
  const events = [], h = harness(t, { diagnosticsEnabled: true, diagnosticsSink: e => events.push(e) });
  const cookie = await h.bootstrap(); await h.register(cookie);
  const cases = [
    ['/auth/callback?error=DIAG_SECRET_ERROR&error_description=DIAG_SECRET_DETAIL', 'error_query'],
    [h.callback() + '&code=DIAG_SECRET_DUPLICATE', 'duplicate'],
    [h.callback() + '&DIAG_SECRET_PARAM=DIAG_SECRET_VALUE', 'unexpected'],
    ['/auth/callback?flow=' + h.flows[0].id, 'missing'],
    [h.callback() + '&extra=' + 'DIAG_SECRET_LONG'.repeat(400), 'oversized'],
  ];
  for (const [url, shape] of cases) {
    const start = events.length; const { res } = await h.invoke(url, { cookie });
    assert.equal(res.status, 303); assert.equal(res.headers.Location, '/auth/callback');
    const event = events.slice(start).find(e => e.stage === 'callback' && e.result === 'rejected');
    assert.equal(event.shape, shape); assert.equal(event.queryErrorUntrusted, shape === 'error_query');
  }
  assert.equal(h.exchange.length, 0); assert.equal(JSON.stringify(events).includes('DIAG_SECRET'), false);
});
test('[diag] absent, malformed, duplicate and mismatched browser bindings stay distinguishable without changing rejection', async t => {
  const events = [], h = harness(t, { diagnosticsEnabled: true, diagnosticsSink: e => events.push(e) });
  const cookie = await h.bootstrap(); await h.register(cookie); const foreign = await h.bootstrap();
  for (const [wire, present, shape] of [[undefined, false, false],
    ['cyomics_register_browser=DIAG_SECRET_COOKIE', true, false], ['cyomics_register_browser', true, false],
    [cookie + '; ' + cookie, true, false]]) {
    const start = events.length; const { res } = await h.invoke(h.callback(), { cookie: wire });
    assert.equal(res.status, 303); const event = events.slice(start).find(e => e.stage === 'callback');
    assert.equal(event.reason, 'cookie'); assert.equal(event.browserPresent, present); assert.equal(event.browserShapeValid, shape);
  }
  const start = events.length; await h.invoke(h.callback(), { cookie: foreign });
  const mismatch = events.slice(start).find(e => e.stage === 'callback');
  assert.equal(mismatch.reason, 'binding'); assert.equal(mismatch.browserMatches, false); assert.equal(mismatch.flowState, 'active');
  assert.equal(h.exchange.length, 0); assert.equal(JSON.stringify(events).includes('DIAG_SECRET'), false);
});
test('[diag] an observed expiry is explicit, but a flow already removed remains absent rather than being guessed expired', async t => {
  const events = [], h = harness(t, { diagnosticsEnabled: true, diagnosticsSink: e => events.push(e) });
  const cookie = await h.bootstrap(); await h.register(cookie); h.advance(900000);
  let start = events.length; await h.invoke(h.callback(), { cookie });
  assert.ok(events.slice(start).some(e => e.stage === 'flow' && e.result === 'expired' && e.flowAge === '15m_or_more'));
  let event = events.slice(start).find(e => e.stage === 'callback');
  assert.equal(event.reason, 'expired_flow'); assert.equal(event.flowState, 'expired');
  start = events.length; await h.invoke(h.callback(), { cookie });
  event = events.slice(start).find(e => e.stage === 'callback');
  assert.equal(event.reason, 'missing_flow'); assert.equal(event.flowState, 'absent'); assert.equal(event.flowAge, 'unknown');
  assert.equal(h.exchange.length, 0);
});
test('[diag] receipt presence, shape, match and replay are observed without granting foreign or forged confirmation', async t => {
  const events = [], h = harness(t, { diagnosticsEnabled: true, diagnosticsSink: e => events.push(e) });
  const cookie = await h.bootstrap(); await h.register(cookie);
  const callback = await h.invoke(h.callback(), { cookie }), receipt = h.receipt(callback.res), foreign = await h.bootstrap();
  for (const [wire, state] of [[cookie, 'absent'], [foreign + '; ' + receipt, 'mismatch'],
    [cookie + '; cyomics_register_receipt=DIAG_SECRET_RECEIPT', 'invalid']]) {
    const start = events.length, { res } = await h.invoke('/api/auth/confirmation', { cookie: wire });
    assert.deepEqual(res.body, unconfirmed);
    const event = events.slice(start).find(e => e.stage === 'confirmation');
    assert.equal(event.result, 'unconfirmed'); assert.equal(event.receiptState, state); assert.equal(event.receiptMatches, false);
  }
  const start = events.length; const replay = await h.invoke(h.callback(), { cookie });
  assert.equal(replay.res.status, 303); assert.ok(events.slice(start).some(e => e.stage === 'callback' && e.result === 'replay' && e.flowState === 'used'));
  assert.equal(h.exchange.length, 1); assert.equal(JSON.stringify(events).includes('DIAG_SECRET'), false);
});
test('[diag] expiry between pruning and the original validation clock read is observed without an extra clock read or SDK call', async t => {
  let boundary = false, reads = 0;
  const events = [], h = harness(t, { diagnosticsEnabled: true, diagnosticsSink: e => events.push(e),
    now: () => boundary ? (++reads === 1 ? 899999 : 900000) : 0 });
  const cookie = await h.bootstrap(); await h.register(cookie); boundary = true;
  const start = events.length; const { res } = await h.invoke(h.callback(), { cookie });
  const event = events.slice(start).find(e => e.stage === 'callback');
  assert.equal(res.status, 303); assert.equal(reads, 2); assert.equal(h.exchange.length, 0);
  assert.equal(event.reason, 'expired_flow'); assert.equal(event.flowState, 'expired'); assert.equal(event.flowAge, '15m_or_more');
});
test('[diag] invalid code is rejected as parameters before lookup even when its contextual flow is expired', async t => {
  const events = [], h = harness(t, { diagnosticsEnabled: true, diagnosticsSink: e => events.push(e) });
  const cookie = await h.bootstrap(); await h.register(cookie); h.advance(900001);
  const start = events.length; const { res } = await h.invoke(h.callback().replace('SYNTHETIC-code', '%0A'), { cookie });
  const emitted = events.slice(start), event = emitted.find(e => e.stage === 'callback');
  assert.equal(res.status, 303); assert.equal(h.exchange.length, 0);
  assert.ok(emitted.some(e => e.stage === 'flow' && e.result === 'expired'));
  assert.equal(event.shape, 'code_flow'); assert.equal(event.reason, 'parameters');
  assert.equal(event.flowState, 'unknown'); assert.equal(event.flowAge, 'unknown'); assert.equal(event.browserMatches, undefined);
});
test('[diag] throwing, rejecting and never-settling sinks leave successful and refused authorization outcomes unchanged', async t => {
  for (const sink of [() => { throw Error('DIAG_SECRET_SINK'); }, () => Promise.reject(Error('DIAG_SECRET_SINK')),
    () => new Promise(() => {})]) {
    const h = harness(t, { diagnosticsEnabled: true, diagnosticsSink: sink });
    const cookie = await h.bootstrap(); await h.register(cookie);
    const refused = await h.invoke(h.callback(), { cookie: 'cyomics_register_browser=' + 'Z'.repeat(43) });
    assert.equal(refused.res.status, 303); assert.equal(h.exchange.length, 0);
    const success = await h.invoke(h.callback(), { cookie }); assert.equal(success.res.status, 303);
    assert.deepEqual((await h.invoke('/api/auth/confirmation', { cookie: cookie + '; ' + h.receipt(success.res) })).res.body, confirmed);
    assert.equal(h.exchange.length, 1);
  }
  await nextTurn();
});
test('[diag] a faulting sink cannot delay the callback deadline or let a late client mint a receipt', async t => {
  const slow = deferred(); let flow, confirms = 0;
  const h = harness(t, { diagnosticsEnabled: true, diagnosticsSink: () => { throw Error('DIAG_SECRET_SINK'); },
    createClient: async settings => {
      flow = settings.flow;
      if (flow.email) { await slow.promise; return { confirm: async () => { confirms++; return true; }, dispose: async () => {} }; }
      return { signUp: async input => {
        flow.verifier = 'DIAG_SECRET_VERIFIER'; return { data: { user: { id: 'synthetic', email: input.email }, session: null }, error: null };
      }, dispose: async () => {} };
    } });
  const cookie = await h.bootstrap(); await h.register(cookie);
  const completion = h.invoke('/auth/callback?flow=' + flow.id + '&code=DIAG_SECRET_CODE', { cookie });
  await nextTurn(); h.advance(15000); const { res } = await completion;
  assert.equal(res.status, 303); assert.ok(res.headers['Set-Cookie'].every(value => !value.includes('Max-Age=900')));
  slow.resolve(); await nextTurn(); assert.equal(confirms, 0); assert.equal(flow.verifier, undefined);
  assert.deepEqual((await h.invoke('/api/auth/confirmation', { cookie })).res.body, unconfirmed);
});
test('[diag] a bound receipt expiring at the original confirmation clock read stays unconfirmed and is diagnosed expired with the same two reads', async t => {
  let boundary = false, reads = 0;
  const events = [], h = harness(t, { diagnosticsEnabled: true, diagnosticsSink: e => events.push(e),
    now: () => boundary ? (++reads === 1 ? 899999 : 900000) : 0 });
  const cookie = await h.bootstrap(); await h.register(cookie);
  const callback = await h.invoke(h.callback(), { cookie });
  const receipt = h.receipt(callback.res); boundary = true;
  const start = events.length;
  const { res } = await h.invoke('/api/auth/confirmation', { cookie: cookie + '; ' + receipt });
  const event = events.slice(start).find(e => e.stage === 'confirmation');
  assert.equal(res.status, 200); assert.deepEqual(res.body, unconfirmed); assert.equal(reads, 2);
  assert.equal(event.result, 'unconfirmed'); assert.equal(event.receiptState, 'expired');
  assert.equal(event.receiptMatches, false); assert.equal(event.receiptPresent, true);
  assert.equal(event.receiptShapeValid, true); assert.equal(h.exchange.length, 1);
});
