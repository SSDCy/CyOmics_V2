import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import vm from 'node:vm';
import worker from '../dist/server/index.js';

const source = await readFile(new URL('../auth-callback.js', import.meta.url), 'utf8');
const html = await readFile(new URL('../auth-callback.html', import.meta.url), 'utf8');
const csp = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self' https://chatgpt.com https://*.chatgpt.com; base-uri 'self'; form-action 'self'";
const hash = value => createHash('sha256').update(value).digest('hex');
const settle = () => new Promise(resolve => setImmediate(resolve));
const fixtures = ['', '?code=synthetic_code', '#access_token=synthetic_access&refresh_token=synthetic_refresh',
  '?error=synthetic_error&error_description=%3Cimg%20src=x%3E', '?success=true&verified=true&next=https://outside.invalid/',
  '?redirect_to=//outside.invalid/#/demo/analysis', '#/workspace/private'];

function headers(response, type) {
  assert.equal(response.headers.get('content-type'), type + '; charset=utf-8');
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.equal(response.headers.get('referrer-policy'), 'no-referrer');
  assert.equal(response.headers.get('content-security-policy'), csp);
  assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
  assert.equal(response.headers.get('set-cookie'), null);
  assert.equal(response.headers.get('location'), null);
}
test('exact callback GET/HEAD and fixed script are available, neutral and non-reflecting, with original security headers', async () => {
  for (const suffix of fixtures) {
    const response = await worker.fetch(new Request('http://127.0.0.1:4206/auth/callback' + suffix), {});
    assert.equal(response.status, 200); headers(response, 'text/html'); assert.equal(await response.text(), html);
  }
  const head = await worker.fetch(new Request('http://127.0.0.1:4206/auth/callback', {method: 'HEAD'}), {});
  assert.equal(head.status, 200); headers(head, 'text/html'); assert.equal(await head.text(), '');
  const script = await worker.fetch(new Request('http://127.0.0.1:4206/auth-callback.js'), {});
  assert.equal(script.status, 200); headers(script, 'text/javascript'); assert.equal(await script.text(), source);
  for (const text of ['synthetic_code', 'synthetic_access', 'synthetic_refresh', 'synthetic_error', 'outside.invalid', '验证成功', '已登录']) assert.ok(!html.includes(text));
});

test('no SPA/auth wildcard fallback or callback write endpoint is introduced', async () => {
  for (const path of ['/auth/callback/', '/auth/Callback', '/auth/callback/extra', '/auth/other', '/auth/callback.html', '/auth/%63allback']) {
    assert.equal((await worker.fetch(new Request('https://preview.invalid' + path), {})).status, 404);
  }
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']) assert.equal((await worker.fetch(new Request('https://preview.invalid/auth/callback', {method}), {})).status, 405);
  assert.equal((await worker.fetch(new Request('https://preview.invalid/unknown'), {})).status, 404);
});

test('all sixteen original built resources preserve exact source bytes and headers, including unchanged root login routing', async () => {
  const paths = ['/', '/app.js', '/protein-groups.js', '/fasta-preview.js', '/protein-association.js', '/association-ui.js', '/style.css', '/upgrade.css', '/import-workspace.js', '/backend-status.js', '/sample-groups.js', '/sample-metadata.js', '/sample-qc.js', '/protein-filter-preview.js', '/filtered-workset.js', '/science-pages.js'];
  assert.equal(paths.length, 16);
  for (const path of paths) {
    const response = await worker.fetch(new Request('https://preview.invalid' + path), {}), bytes = Buffer.from(await response.arrayBuffer());
    assert.equal(response.status, 200); headers(response, path === '/' ? 'text/html' : path.endsWith('.css') ? 'text/css' : 'text/javascript');
    const file = await readFile(new URL('../' + (path === '/' ? 'index.html' : path.slice(1)), import.meta.url));
    assert.equal(hash(bytes), hash(file)); assert.deepEqual(bytes, file);
  }
  const login = await worker.fetch(new Request('https://preview.invalid/#/login'), {});
  assert.equal(login.status, 200); assert.equal(await login.text(), await readFile(new URL('../index.html', import.meta.url), 'utf8'));
});

test('standalone document cleans before styles, retains fixed navigation and brand, and never loads main app, SDK or form', () => {
  assert.ok(html.indexOf('name="referrer" content="no-referrer"') < html.indexOf('<script src="/auth-callback.js"></script>'));
  assert.ok(html.indexOf('<script src="/auth-callback.js"></script>') < html.indexOf('<link rel="stylesheet"'));
  assert.equal((html.match(/<script\b/g) || []).length, 1); assert.ok(!/\bdefer\b|\basync\b|type="module"/.test(html));
  assert.ok(!/<form\b|\/app\.js|supabase|pkce|<iframe|<img/i.test(html));
  assert.match(html, /href="\/#\/login"[^>]*id="callback-login"/);
  assert.match(html, /href="\/#\/"[^>]*id="callback-home"/);
  assert.match(html, /class="panel auth-card"/); assert.match(html, /Cy_Omics_v2\.0/);
  assert.match(html, /id="callback-message" role="status" aria-live="polite"/);
  assert.match(html, /暂时无法确认验证结果/);
});

function harness({suffix = '', failCleanup = false, ready = 'loading', response = () => Response.json({status: 'unconfirmed', emailVerified: false})} = {}) {
  const events = [], forbidden = [], changes = [], elements = new Map(), domEvents = new Map(), windowEvents = new Map(), calls = [], timers = new Map();
  const incoming = 'http://127.0.0.1:4206/auth/callback' + suffix;
  let visibleAddress = incoming;
  for (const id of ['callback-title', 'callback-message', 'callback-login', 'callback-home', 'callback-brand', 'callback-language']) {
    const attrs = id === 'callback-login' ? {href: '/#/login'} : id === 'callback-home' || id === 'callback-brand' ? {href: '/#/'} : {};
    elements.set(id, {textContent: '', attrs, listeners: new Map(), setAttribute(key, value) { this.attrs[key] = value; }, addEventListener(key, fn) { this.listeners.set(key, fn); }, removeEventListener(key, fn) { if (this.listeners.get(key) === fn) this.listeners.delete(key); }});
  }
  const trap = name => () => { forbidden.push(name); throw Error('Forbidden access: ' + name); };
  const location = {};
  for (const key of ['search', 'hash', 'href', 'origin', 'pathname', 'toString']) Object.defineProperty(location, key, {get: trap('location.' + key)});
  const window = {history: {replaceState(...args) { events.push('replace'); changes.push(args); if (failCleanup) throw Error('synthetic_private_exception_' + incoming); visibleAddress = new URL(args[2], 'http://127.0.0.1:4206').href; }}};
  Object.defineProperty(window, 'location', {get: trap('window.location')});
  window.addEventListener = (name, fn, options) => { assert.equal(name, 'pagehide'); assert.equal(options.once, true); events.push('pagehide-listener'); windowEvents.set(name, fn); };
  const document = {readyState: ready, documentElement: {lang: ''}, title: '', getElementById(id) { events.push('dom'); return elements.get(id); },
    addEventListener(name, fn, options) { events.push('listener'); assert.equal(name, 'DOMContentLoaded'); assert.equal(options.once, true); domEvents.set(name, fn); },
    removeEventListener(name, fn) { if (domEvents.get(name) === fn) domEvents.delete(name); }};
  for (const key of ['URL', 'documentURI', 'cookie', 'referrer']) Object.defineProperty(document, key, {get: trap('document.' + key), set: trap('document.' + key)});
  const context = {window, document, AbortController, TextDecoder,
    fetch(path, options) { events.push('fetch'); calls.push([path, options]); return response(path, options); }};
  Object.defineProperty(context, 'location', {get: trap('location')});
  for (const key of ['localStorage', 'sessionStorage', 'indexedDB', 'XMLHttpRequest', 'WebSocket', 'EventSource', 'navigator']) {
    Object.defineProperty(context, key, {get: trap(key)}); Object.defineProperty(window, key, {get: trap('window.' + key)});
  }
  context.console = Object.fromEntries(['log', 'error', 'warn', 'debug', 'info'].map(name => [name, trap('console.' + name)]));
  let timerId = 0;
  context.setTimeout = (fn, delay) => { assert.equal(delay, 16000); timers.set(++timerId, fn); return timerId; };
  context.clearTimeout = key => timers.delete(key); context.setInterval = trap('setInterval');
  vm.runInNewContext(source, context, {timeout: 1000});
  return {events, forbidden, changes, elements, domEvents, context, calls, timers,
    initialize() { const fn = domEvents.get('DOMContentLoaded'); domEvents.delete('DOMContentLoaded'); fn?.(); },
    clickLanguage() { elements.get('callback-language').listeners.get('click')(); },
    hide() { windowEvents.get('pagehide')(); },
    expire() { for (const fn of [...timers.values()]) fn(); },
    get visibleAddress() { return visibleAddress; }};
}

test('first action cleans the address without reading URL or storage; only a fixed confirmation GET follows initialization', async () => {
  for (const suffix of fixtures) {
    const h = harness({suffix}); assert.deepEqual(h.events, ['replace', 'pagehide-listener', 'listener']);
    assert.equal(h.changes.length, 1); assert.equal(JSON.stringify(h.changes[0]), '[null,"","/auth/callback"]');
    assert.equal(h.visibleAddress, 'http://127.0.0.1:4206/auth/callback'); assert.deepEqual(h.forbidden, []);
    h.initialize(); await settle(); assert.deepEqual(h.forbidden, []); assert.equal(h.elements.get('callback-title').textContent, '暂时无法确认验证结果');
    assert.equal(h.elements.get('callback-login').attrs.href, '/#/login'); assert.equal(h.elements.get('callback-home').attrs.href, '/#/');
    assert.equal(h.changes.length, 1);
    assert.equal(h.calls.length, 1); assert.equal(h.calls[0][0], '/api/auth/confirmation'); assert.equal(h.calls[0][1].method, 'GET'); assert.equal(h.timers.size, 0);
  }
});

test('code, error, token, success and redirect fixtures cannot override an unconfirmed backend result or create a session', async () => {
  let expected;
  for (const suffix of fixtures) {
    const h = harness({suffix, ready: 'complete'}); await settle();
    const rendered = [...h.elements].map(([id, node]) => [id, node.textContent, node.attrs]);
    expected ||= JSON.stringify(rendered); assert.equal(JSON.stringify(rendered), expected);
    assert.equal(h.context.document.documentElement.lang, 'zh-CN'); assert.deepEqual(h.forbidden, []);
    assert.deepEqual(Object.keys(h.context.window), ['history', 'addEventListener']);
    assert.ok(!JSON.stringify(rendered).includes('synthetic_')); assert.ok(!JSON.stringify(rendered).includes('outside.invalid'));
  }
});

test('language switching stays in memory with fixed bilingual text and links, without repeating confirmation or URL side effects', async () => {
  const h = harness(); h.initialize(); await settle(); h.clickLanguage();
  assert.equal(h.context.document.documentElement.lang, 'en'); assert.equal(h.elements.get('callback-language').textContent, '中文');
  assert.equal(h.elements.get('callback-title').textContent, 'Unable to confirm the verification result');
  assert.equal(h.elements.get('callback-login').textContent, 'Return to sign in'); assert.equal(h.elements.get('callback-login').attrs.href, '/#/login');
  h.clickLanguage(); assert.equal(h.context.document.documentElement.lang, 'zh-CN'); assert.equal(h.elements.get('callback-title').textContent, '暂时无法确认验证结果');
  assert.deepEqual(h.forbidden, []); assert.equal(h.changes.length, 1); assert.equal(h.calls.length, 1);
});

test('failed address cleanup remains neutral, does not leak the exception or incoming address, and does not retry or navigate', () => {
  const h = harness({suffix: '?code=synthetic_secret', failCleanup: true}); h.initialize();
  assert.equal(h.elements.get('callback-title').textContent, '暂时无法确认验证结果');
  assert.equal(h.elements.get('callback-message').textContent, '暂时无法继续处理此链接。请返回登录页面继续操作。');
  h.clickLanguage(); assert.equal(h.elements.get('callback-message').textContent, 'This link cannot be processed right now. Please return to the sign-in page.');
  assert.equal(h.changes.length, 1); assert.deepEqual(h.forbidden, []);
  assert.ok(![...h.elements.values()].map(node => node.textContent).join(' ').includes('synthetic_'));
  assert.equal(h.calls.length, 0); assert.equal(h.timers.size, 0);
});

test('only the exact two-field backend verification result succeeds after one fixed same-origin credential-compatible GET', async () => {
  const h = harness({ready: 'complete', suffix: '?success=false', response: () => Response.json({status: 'verified', emailVerified: true})});
  assert.equal(h.elements.get('callback-title').textContent, '正在确认邮箱验证'); assert.equal(h.calls.length, 1); assert.equal(h.events[0], 'replace');
  assert.ok(h.events.indexOf('replace') < h.events.indexOf('fetch'));
  const [path, options] = h.calls[0]; assert.equal(path, '/api/auth/confirmation');
  assert.deepEqual(Object.keys(options).sort(), ['cache', 'credentials', 'method', 'redirect', 'signal']);
  assert.equal(options.method, 'GET'); assert.equal(options.credentials, 'same-origin'); assert.equal(options.cache, 'no-store'); assert.equal(options.redirect, 'error');
  await settle(); assert.equal(h.elements.get('callback-title').textContent, '邮箱已验证'); assert.match(h.elements.get('callback-message').textContent, /请返回登录页继续/);
  assert.equal(h.elements.get('callback-message').attrs['aria-busy'], 'false'); h.clickLanguage();
  assert.equal(h.elements.get('callback-title').textContent, 'Email verified'); assert.match(h.elements.get('callback-message').textContent, /Return to the sign-in page/);
  assert.equal(h.calls.length, 1); assert.equal(h.timers.size, 0); assert.deepEqual(h.forbidden, []); h.hide();
});

test('unconfirmed, malformed, extra/session fields, mismatched booleans, HTTP failures, redirect and invalid or oversized JSON never verify or expose raw details', async () => {
  const responses = [() => Response.json({status: 'unconfirmed', emailVerified: false}),
    () => Response.json({status: 'verified', emailVerified: false}), () => Response.json({status: 'unconfirmed', emailVerified: true}),
    () => Response.json({status: 'verified', emailVerified: true, access_token: 'synthetic_private'}),
    () => Response.json([{status: 'verified', emailVerified: true}]), () => Response.json({status: 'verified'}),
    () => Response.json({status: 'verified', emailVerified: true}, {status: 202}),
    () => Response.json({error: {code: 'synthetic_private'}}, {status: 503}),
    () => new Response('{synthetic_private', {headers: {'content-type': 'application/json'}}),
    () => new Response(new Uint8Array([255]), {headers: {'content-type': 'application/json'}}),
    () => new Response(' '.repeat(4097), {headers: {'content-type': 'application/json'}}),
    () => Response.json({status: 'verified', emailVerified: true}, {headers: {'content-type': 'text/html'}}),
    () => Response.json({status: 'verified', emailVerified: true}, {headers: {'content-length': '4097'}}),
    () => ({status: 200, redirected: true, body: null}), () => { throw Error('synthetic_private'); },
    () => Promise.reject(Error('synthetic_private'))];
  for (const response of responses) {
    const h = harness({ready: 'complete', response}); await settle(); assert.equal(h.elements.get('callback-title').textContent, '暂时无法确认验证结果');
    assert.ok(![...h.elements.values()].map(node => node.textContent).join(' ').includes('synthetic_private'));
    assert.equal(h.calls.length, 1); assert.equal(h.timers.size, 0); assert.deepEqual(h.forbidden, []); h.hide();
  }
});

test('one total deadline bounds confirmation fetch/body; late responses are cancelled and language never repeats the request', async () => {
  let resolve, cancelled = 0;
  const pending = new Promise(done => { resolve = done; }), h = harness({ready: 'complete', response: () => pending});
  h.clickLanguage(); assert.equal(h.calls.length, 1); assert.equal(h.elements.get('callback-title').textContent, 'Confirming email verification');
  h.expire(); await settle(); assert.equal(h.calls[0][1].signal.aborted, true); assert.equal(h.elements.get('callback-title').textContent, 'Unable to confirm the verification result');
  resolve(new Response(new ReadableStream({cancel() { cancelled++; }}), {headers: {'content-type': 'application/json'}})); await settle();
  assert.equal(cancelled, 1); assert.equal(h.calls.length, 1); assert.equal(h.timers.size, 0); h.hide();
  let bodyCancelled = 0;
  const stream = harness({ready: 'complete', response: () => new Response(new ReadableStream({cancel() { bodyCancelled++; }}), {headers: {'content-type': 'application/json'}})});
  await settle(); stream.expire(); await settle(); assert.equal(bodyCancelled, 1); assert.equal(stream.elements.get('callback-title').textContent, '暂时无法确认验证结果'); assert.equal(stream.timers.size, 0); stream.hide();
});

test('pagehide before initialization makes no request; disposing pending confirmation clears waits and prevents stale DOM writes', async () => {
  const before = harness(); before.hide(); before.initialize(); assert.equal(before.calls.length, 0); assert.equal(before.timers.size, 0);
  let resolve;
  const h = harness({ready: 'complete', response: () => new Promise(done => { resolve = done; })}), initial = h.elements.get('callback-title').textContent;
  h.hide(); assert.equal(h.calls[0][1].signal.aborted, true); assert.equal(h.timers.size, 0); assert.equal(h.elements.get('callback-language').listeners.size, 0);
  resolve(Response.json({status: 'verified', emailVerified: true})); await settle(); assert.equal(h.elements.get('callback-title').textContent, initial); assert.deepEqual(h.forbidden, []);
});
