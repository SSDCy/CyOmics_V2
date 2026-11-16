import test from 'node:test';
import assert from 'node:assert/strict';
import {isBackendHealth, mountBackendStatus} from '../backend-status.js';

const health = {status: 'ok', service: 'Cy_Omics_v2.0-backend', version: '0.1.0'};
const response = (data = health) => ({ok: true, json: async () => data});
const settle = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}

// Minimal DOM surface for lifecycle and text checks; browser layout is checked separately.
function element(doc) {
  return {
    ownerDocument: doc, children: [], dataset: {}, attributes: {}, listeners: new Map(),
    textContent: '', disabled: false,
    setAttribute(name, value) { this.attributes[name] = value; },
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren(...nodes) { this.children = nodes; },
    addEventListener(name, listener) { this.listeners.set(name, listener); },
    removeEventListener(name, listener) { if (this.listeners.get(name) === listener) this.listeners.delete(name); },
    click() { if (!this.disabled) this.listeners.get('click')?.(); },
  };
}
function harness(fetchImpl, t = zh => zh) {
  const doc = {createElement: () => element(doc)};
  const root = element(doc), timers = new Map(), calls = [];
  let timerId = 0;
  const dispose = mountBackendStatus(root, t, {
    fetchImpl: (...args) => { calls.push(args); return fetchImpl(...args); },
    setTimer(fn, delay) { assert.equal(delay, 2500); timers.set(++timerId, fn); return timerId; },
    clearTimer(id) { timers.delete(id); },
  });
  const line = root.children[0], [status, button, note] = line.children;
  return {root, line, status, button, note, calls, timers, dispose,
    timeout() { for (const [id, fn] of [...timers]) { timers.delete(id); fn(); } },
  };
}

test('accepts the exact health contract; rejects prototype, wrong service and unsafe/invalid versions', () => {
  for (const version of ['0.1.0', '1.2.3-rc.1+build.4']) assert.equal(isBackendHealth({...health, version}), true);
  for (const input of [null, [], 'ok', 1, {}, {status: 'prototype'}, {...health, status: 'prototype'},
    {...health, service: 'different'}, {...health, version: '<img src=x>'}, {...health, version: '1.2'},
    {...health, version: '01.2.3'}, {...health, version: '1.2.3-rc.01'}, {...health, version: '1'.repeat(65)},
    {...health, version: 1}]) assert.equal(isBackendHealth(input), false);
});

test('mount checks once, uses only the credential-free same-origin GET, and supports bilingual accessible text', async () => {
  const h = harness(async () => response(), (_zh, en) => en);
  assert.equal(h.status.textContent, 'Backend: Checking…');
  assert.equal(h.button.disabled, true);
  await settle();
  assert.equal(h.status.textContent, 'Backend: Connected (v0.1.0)');
  assert.equal(h.line.dataset.connection, 'connected');
  assert.equal(h.status.attributes.role, 'status');
  assert.equal(h.status.attributes['aria-live'], 'polite');
  assert.equal(h.button.textContent, 'Recheck');
  assert.match(h.note.textContent, /authentication, storage and computation are not connected/);
  assert.equal(h.calls.length, 1);
  const [url, options] = h.calls[0];
  assert.equal(url, '/api/health');
  assert.deepEqual(Object.keys(options).sort(), ['cache', 'credentials', 'method', 'redirect', 'signal']);
  assert.equal(options.method, 'GET');
  assert.equal(options.credentials, 'omit');
  assert.equal(options.cache, 'no-store');
  assert.equal(options.redirect, 'error');
  assert.equal(h.timers.size, 0);
  await settle();
  assert.equal(h.calls.length, 1);
  h.button.click();
  await settle();
  assert.equal(h.calls.length, 2);
  h.dispose();
});

test('HTTP 200 prototype and network/HTTP/JSON failures remain disconnected and permit a manual recovery', async () => {
  for (const result of [async () => response({status: 'prototype'}), async () => { throw new Error('network'); },
    async () => ({ok: false, json: async () => health}),
    async () => ({ok: true, json: async () => { throw new Error('invalid JSON'); }})]) {
    let recovered = false;
    const h = harness(() => recovered ? response() : result());
    await settle();
    assert.equal(h.status.textContent, '后端：未连接');
    assert.equal(h.button.disabled, false);
    assert.equal(h.timers.size, 0);
    recovered = true;
    h.button.click();
    await settle();
    assert.equal(h.status.textContent, '后端：已连接 (v0.1.0)');
    h.dispose();
  }
});

test('2.5-second timeout aborts and ignores an old response after a newer successful retry', async () => {
  const old = deferred();
  let first = true;
  const h = harness(() => { if (first) { first = false; return old.promise; } return response(); });
  h.timeout();
  assert.equal(h.calls[0][1].signal.aborted, true);
  assert.equal(h.status.textContent, '后端：未连接');
  h.button.click();
  await settle();
  assert.equal(h.line.dataset.connection, 'connected');
  old.resolve(response({status: 'prototype'}));
  await settle();
  assert.equal(h.line.dataset.connection, 'connected');
  assert.equal(h.timers.size, 0);
  h.dispose();
});

test('timeout also bounds JSON reading and never accepts a late body', async () => {
  const body = deferred();
  const h = harness(async () => ({ok: true, json: () => body.promise}));
  await settle();
  h.timeout();
  body.resolve(health);
  await settle();
  assert.equal(h.line.dataset.connection, 'disconnected');
  h.dispose();
});

test('unmount cancels both pending fetch and pending body, clears timer/listener and prevents late DOM writes', async () => {
  for (const pendingBody of [false, true]) {
    const pending = deferred();
    const h = harness(() => pendingBody ? responseWithPendingBody() : pending.promise);
    function responseWithPendingBody() { return {ok: true, json: () => pending.promise}; }
    await settle();
    const before = h.status.textContent;
    h.dispose();
    assert.equal(h.calls[0][1].signal.aborted, true);
    assert.equal(h.timers.size, 0);
    assert.equal(h.button.listeners.size, 0);
    assert.deepEqual(h.root.children, []);
    pending.resolve(pendingBody ? health : response());
    await settle();
    assert.equal(h.status.textContent, before);
    h.button.click();
    assert.equal(h.calls.length, 1);
  }
});
