import assert from 'node:assert/strict';
import { once } from 'node:events';
import { test } from 'node:test';
import { createPreviewServer } from '../server.mjs';

const health = { status: 'ok', service: 'Cy_Omics_v2.0-backend', version: '0.1.0' };
const unavailable = { error: { code: 'backend_unavailable' } };
async function preview(t, fetchImpl) {
  const server = createPreviewServer({ fetchImpl });
  assert.equal(server.listening, false);
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => new Promise((resolve, reject) => {
    server.close(error => error ? reject(error) : resolve());
    server.closeAllConnections();
  }));
  assert.equal(server.address().address, '127.0.0.1');
  return `http://127.0.0.1:${server.address().port}`;
}
async function check(base, status, expected, path = '/api/health', options = {}) {
  const response = await fetch(base + path, options);
  assert.equal(response.status, status);
  assert.equal(response.headers.get('content-type'), 'application/json; charset=utf-8');
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.equal(response.headers.get('access-control-allow-origin'), null);
  assert.equal(response.headers.get('set-cookie'), null);
  if (status === 405) assert.equal(response.headers.get('allow'), 'GET');
  if (options.method === 'HEAD') assert.equal(await response.text(), '');
  else assert.deepEqual(await response.json(), expected);
}

test('real HTTP bridge uses fixed target, strips client input and upstream extras', async t => {
  let calls = 0;
  const base = await preview(t, async (url, options) => {
    calls++;
    assert.equal(url, 'http://127.0.0.1:4186/api/health');
    assert.deepEqual(Object.keys(options).sort(), ['cache', 'credentials', 'headers', 'method', 'redirect', 'signal']);
    assert.equal(options.method, 'GET');
    assert.equal(options.redirect, 'error');
    assert.equal(options.credentials, 'omit');
    assert.equal(options.cache, 'no-store');
    assert.deepEqual(options.headers, { Accept: 'application/json' });
    assert.ok(options.signal instanceof AbortSignal);
    return Response.json({ ...health, internal: 'must not escape' }, { headers: { 'set-cookie': 'must-not-escape=1' } });
  });
  await check(base, 200, health, '/api/health', { headers: { Authorization: 'test-only', Cookie: 'test-only', 'X-Target': 'ignored' } });
  assert.equal(calls, 1);
});

test('non-GET and query variants never invoke upstream or prototype fallback', async t => {
  let calls = 0;
  const base = await preview(t, async () => { calls++; throw Error('unexpected upstream'); });
  for (const method of ['POST', 'PUT', 'DELETE', 'OPTIONS', 'HEAD']) {
    await check(base, 405, { error: { code: 'method_not_allowed' } }, '/api/health',
      { method, ...(['POST', 'PUT'].includes(method) ? { body: 'must not forward' } : {}) });
  }
  await check(base, 404, { error: { code: 'not_found' } }, '/api/health?target=http://example.invalid');
  assert.equal(calls, 0);
});

test('prototype, wrong identity and invalid or unbounded versions become finite 503', async t => {
  let body;
  const base = await preview(t, async () => Response.json(body));
  for (body of [{ status: 'prototype' }, { ...health, service: 'wrong' }, null,
    ...['01.1.0', '1.2', '1.2.3-01', '1.2.3\n', '1'.repeat(65)].map(version => ({ ...health, version }))]) {
    await check(base, 503, unavailable);
  }
  body = { ...health, version: '1.2.3-alpha.1+build.2' };
  await check(base, 200, body);
});

test('network failure, redirect, upstream error, malformed JSON and body limit become 503', async t => {
  let mode;
  const base = await preview(t, async () => {
    if (mode === 'network') throw Error('private internal detail');
    if (mode === 'redirect') return new Response(null, { status: 302, headers: { Location: 'http://example.invalid' } });
    if (mode === 'status') return Response.json({ internal: 'hidden' }, { status: 500 });
    if (mode === 'type') return new Response(JSON.stringify(health));
    return new Response(mode === 'large' ? 'x'.repeat(4097) : '{invalid', { headers: { 'Content-Type': 'application/json' } });
  });
  for (mode of ['network', 'redirect', 'status', 'type', 'json', 'large']) await check(base, 503, unavailable);
});

test('two-second deadline covers a stalled upstream body without stopping real backend', async t => {
  let signal, cancelled = false;
  const base = await preview(t, async (_, options) => {
    signal = options.signal;
    return new Response(new ReadableStream({ cancel() { cancelled = true; } }), {
      headers: { 'Content-Type': 'application/json' },
    });
  });
  const start = Date.now();
  await check(base, 503, unavailable);
  assert.ok(Date.now() - start >= 1800 && Date.now() - start < 5000);
  assert.equal(signal.aborted, true);
  assert.equal(cancelled, true);
});
