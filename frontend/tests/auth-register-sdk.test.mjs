import test from 'node:test';
import assert from 'node:assert/strict';
import { createRegistrationClient } from '../auth-register-sdk.mjs';
import { createRegistrationDiagnostics } from '../auth-register-diagnostics.mjs';
import { createHash } from 'node:crypto';

const email = 'synthetic@example.com', key = 'SYNTHETIC_ANON';
const user = { id: 'synthetic-user', email, email_confirmed_at: '2026-11-15T00:00:00Z', aud: 'authenticated' };
const token = { access_token: 'synthetic.access.token', refresh_token: 'synthetic-refresh', token_type: 'bearer',
  expires_in: 3600, user };
function harness(reply) {
  const controller = new AbortController(), calls = [];
  const flow = { id: 'abcdefghijklmnopqrstuv', email, expiresAt: Date.now() + 900000, closed: false };
  const create = (extra = {}) => createRegistrationClient({ flow, signal: controller.signal, keyProvider: async () => key,
    fetchImpl: async (url, options) => { calls.push({ url, options }); return reply(url, options); }, ...extra });
  return { controller, calls, flow, create };
}
test('official pinned SDK creates PKCE and exchanges once, then verifies the authoritative same email', async () => {
  const h = harness(url => Response.json(url.includes('/signup?') ? { ...user, email_confirmed_at: null }
    : url.includes('/token?') ? token : user));
  let client = await h.create();
  const pending = await client.signUp({ email, password: 'SYNTHETIC-password-123' });
  assert.equal(pending.error, null); assert.equal(pending.data.session, null);
  assert.equal(typeof h.flow.verifier, 'string');
  const wire = JSON.parse(h.calls[0].options.body);
  assert.equal(wire.code_challenge_method, 's256'); assert.equal(typeof wire.code_challenge, 'string');
  assert.ok(!h.calls[0].options.body.includes(h.flow.verifier));
  assert.equal(new URL(h.calls[0].url).searchParams.get('redirect_to'),
    'http://127.0.0.1:4206/auth/callback?flow=' + h.flow.id);
  await client.dispose(); client = await h.create();
  assert.equal(await client.confirm('SYNTHETIC-code'), true); await client.dispose();
  assert.deepEqual(h.calls.map(c => new URL(c.url).pathname), ['/auth/v1/signup', '/auth/v1/token', '/auth/v1/user']);
  assert.equal(JSON.parse(h.calls[1].options.body).code_verifier, JSON.parse(h.flow.verifier).split('/')[0]);
  assert.equal(h.calls[2].options.headers.Authorization, 'Bearer ' + token.access_token);
  for (const { options } of h.calls) {
    assert.equal(options.headers.apikey, key); assert.equal(options.signal, h.controller.signal);
    assert.equal(options.redirect, 'error'); assert.equal(options.credentials, 'omit'); assert.equal(options.cache, 'no-store');
    assert.ok(!Object.keys(options.headers).some(k => /cookie/i.test(k)));
  }
  assert.deepEqual(Object.keys(h.flow).sort(), ['closed', 'email', 'expiresAt', 'id', 'verifier']);
});
test('exchange success alone does not confirm an unverified or different authoritative email', async () => {
  for (const current of [{ ...user, email: 'someone-else@example.com' }, { ...user, email_confirmed_at: null },
    { ...user, email_confirmed_at: 'invalid' }]) {
    const h = harness(url => Response.json(url.includes('/token?') ? token : current)); h.flow.verifier = 'SYNTHETIC-verifier';
    const client = await h.create(); assert.equal(await client.confirm('SYNTHETIC-code'), false); await client.dispose();
  }
});
test('HTTP, redirect, malformed, oversized and transport failures fail closed without a raw exception', async () => {
  for (const reply of [() => { throw Error('SYNTHETIC-private-error'); }, () => new Response('private', { status: 302 }),
    () => new Response('not json', { headers: { 'content-type': 'application/json' } }),
    () => Response.json({ huge: 'x'.repeat(32768) }),
    () => Response.json({ message: 'SYNTHETIC-private-error' }, { status: 500 })]) {
    const h = harness(reply); h.flow.verifier = 'SYNTHETIC-verifier'; const client = await h.create();
    await assert.rejects(client.confirm('SYNTHETIC-code'), /registration_unavailable/); await client.dispose();
    assert.equal(h.calls.length, 1);
  }
});
test('aborted and expired clients cannot read a key, sign up or exchange', async () => {
  let keys = 0, fetches = 0;
  const controller = new AbortController(); controller.abort();
  await assert.rejects(createRegistrationClient({ flow: { id: 'abcdefghijklmnopqrstuv', expiresAt: 1 },
    signal: controller.signal, keyProvider: async () => { keys++; return key; },
    fetchImpl: async () => { fetches++; } }), /registration_unavailable/);
  assert.equal(fetches, 0); assert.equal(keys, 0);
  const h = harness(() => { throw Error('must not fetch'); }); const client = await h.create();
  h.flow.closed = true;
  await assert.rejects(client.signUp({ email, password: 'SYNTHETIC-password-123' }));
  await assert.rejects(client.confirm('SYNTHETIC-code')); await client.dispose(); assert.equal(h.calls.length, 0);
});

test('[diag] pinned official SDK observations distinguish exchange and authoritative getUser without leaking sentinels or hashes', async () => {
  const events = [], diagnostic = createRegistrationDiagnostics({ enabled: true, sink: value => events.push(value) });
  const secrets = { email: 'diag-secret@example.com', user: 'DIAG_SECRET_USER', code: 'DIAG_SECRET_CODE',
    verifier: 'DIAG_SECRET_VERIFIER', access: 'diag_secret.access.token', refresh: 'DIAG_SECRET_REFRESH', key: 'DIAG_SECRET_KEY' };
  const current = { ...user, id: secrets.user, email: secrets.email, extra: 'DIAG_SECRET_EXTRA' };
  const h = harness(url => Response.json(url.includes('/token?') ? { ...token, user: current,
    access_token: secrets.access, refresh_token: secrets.refresh } : current));
  h.flow.email = secrets.email; h.flow.verifier = JSON.stringify(secrets.verifier);
  const client = await h.create({ diagnostics: diagnostic, keyProvider: async () => secrets.key });
  assert.equal(await client.confirm(secrets.code), true); await client.dispose();
  assert.ok(events.some(e => e.stage === 'exchange' && e.result === 'attempted'));
  assert.ok(events.some(e => e.stage === 'exchange' && e.result === 'succeeded'));
  assert.ok(events.some(e => e.stage === 'get_user' && e.result === 'succeeded' && e.emailConfirmed && e.emailMatches));
  assert.deepEqual(events.filter(e => e.stage === 'transport' && e.result === 'received').map(e => e.operation), ['exchange', 'get_user']);
  const output = JSON.stringify(events);
  for (const secret of [...Object.values(secrets), h.flow.id, 'DIAG_SECRET_EXTRA']) {
    assert.equal(output.includes(secret), false, 'diagnostic contains a secret sentinel');
    assert.equal(output.includes(createHash('sha256').update(secret).digest('hex')), false, 'diagnostic contains a sentinel hash');
  }
});
test('[diag] fixed upstream PKCE errors remain private classifications while the public SDK failure contract stays unchanged', async () => {
  for (const code of ['flow_state_not_found', 'flow_state_expired', 'bad_code_verifier', 'validation_failed', 'DIAG_SECRET_UNKNOWN']) {
    const events = [], diagnostic = createRegistrationDiagnostics({ enabled: true, sink: e => events.push(e) });
    const h = harness(() => Response.json({ code, message: 'DIAG_SECRET_MESSAGE', user: { id: 'DIAG_SECRET_USER' },
      access_token: 'DIAG_SECRET_TOKEN' }, { status: 400 }));
    h.flow.verifier = JSON.stringify('DIAG_SECRET_VERIFIER'); const client = await h.create({ diagnostics: diagnostic });
    await assert.rejects(client.confirm('DIAG_SECRET_CODE'), /registration_unavailable/); await client.dispose();
    const expected = code === 'DIAG_SECRET_UNKNOWN' ? 'other' : code;
    assert.ok(events.some(e => e.stage === 'exchange' && e.result === 'failed' && e.errorCode === expected));
    assert.equal(events.some(e => e.stage === 'get_user'), false); assert.equal(h.calls.length, 1);
    assert.equal(JSON.stringify(events).includes('DIAG_SECRET'), false);
  }
});
test('[diag] getUser failure, mismatched email and unconfirmed email are distinguishable and never report verified', async () => {
  for (const mode of ['http-error', 'different-email', 'unconfirmed']) {
    const events = [], diagnostic = createRegistrationDiagnostics({ enabled: true, sink: e => events.push(e) });
    const h = harness(url => url.includes('/token?') ? Response.json(token)
      : mode === 'http-error' ? Response.json({ code: 'bad_jwt', message: 'DIAG_SECRET_UPSTREAM' }, { status: 401 })
      : Response.json({ ...user, ...(mode === 'different-email' ? { email: 'DIAG_SECRET_EMAIL@example.com' } : { email_confirmed_at: null }) }));
    h.flow.verifier = JSON.stringify('DIAG_SECRET_VERIFIER'); const client = await h.create({ diagnostics: diagnostic });
    if (mode === 'http-error') {
      await assert.rejects(client.confirm('DIAG_SECRET_CODE'), /registration_unavailable/);
      assert.ok(events.some(e => e.stage === 'get_user' && e.result === 'failed' && e.errorCode === 'bad_jwt'));
    } else {
      assert.equal(await client.confirm('DIAG_SECRET_CODE'), false);
      assert.ok(events.some(e => e.stage === 'get_user' && e.result === 'succeeded'
        && e.emailMatches === (mode !== 'different-email') && e.emailConfirmed === (mode !== 'unconfirmed')));
    }
    await client.dispose(); assert.equal(h.calls.length, 2); assert.equal(JSON.stringify(events).includes('DIAG_SECRET'), false);
  }
});
test('[diag] an aborted late transport cannot produce exchange success and faulting or pending sinks do not delay the SDK', async () => {
  let resolve;
  const waiting = new Promise(r => { resolve = r; });
  const events = [], diagnostic = createRegistrationDiagnostics({ enabled: true, sink: e => events.push(e) });
  const h = harness(() => waiting); h.flow.verifier = JSON.stringify('DIAG_SECRET_VERIFIER');
  const client = await h.create({ diagnostics: diagnostic }); const completion = client.confirm('DIAG_SECRET_CODE');
  await new Promise(r => setImmediate(r)); h.controller.abort(); resolve(Response.json(token));
  await assert.rejects(completion, /registration_unavailable/); await client.dispose();
  assert.ok(events.some(e => e.stage === 'exchange' && e.result === 'aborted'));
  assert.equal(events.some(e => e.stage === 'exchange' && e.result === 'succeeded'), false);
  for (const sink of [() => { throw Error('DIAG_SECRET_SINK'); }, () => Promise.reject(Error('DIAG_SECRET_SINK')),
    () => new Promise(() => {})]) {
    const k = harness(url => Response.json(url.includes('/token?') ? token : user)); k.flow.verifier = JSON.stringify('DIAG_SECRET_VERIFIER');
    const safe = createRegistrationDiagnostics({ enabled: true, sink }), official = await k.create({ diagnostics: safe });
    assert.equal(await official.confirm('DIAG_SECRET_CODE'), true); await official.dispose(); assert.equal(k.calls.length, 2);
  }
});
