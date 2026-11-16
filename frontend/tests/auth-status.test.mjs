import {createPrivateProjectRoutes,createPrivateProjectTransport,mountPrivateProjects} from '../private-projects.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {once} from 'node:events';
import {scienceTools} from '../science-pages.js';
import {createPreviewServer} from '../server.mjs';
import {createSignupRoutes, createSignupTransport, mountSignup} from '../auth-signup.js';
import {createLoginRoutes, createLoginTransport, mountLogin} from '../auth-login.js';
import {isAuthStatus, createAuthStatusController, createAuthStatusRoutes, mountAuthStatus} from '../auth-status.js';

const health = {status: 'ok', service: 'Cy_Omics_v2.0-auth', version: '2.197.0'};
const json = (value = health, options = {}) => new Response(JSON.stringify(value), {headers: {'content-type': 'application/json'}, ...options});
const settle = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; }
function clock() {
  let id = 0; const timers = new Map();
  return {timers, setTimer(fn, delay) { assert.equal(delay, 2500); timers.set(++id, fn); return id; }, clearTimer(key) { timers.delete(key); },
    fire() { for (const [key, fn] of [...timers]) { timers.delete(key); fn(); } }};
}
function node(doc, tag = 'div') {
  return {ownerDocument: doc, tagName: tag.toUpperCase(), children: [], dataset: {}, attrs: {}, listeners: new Map(), textContent: '', disabled: false, style: {}, value: '',
    setAttribute(k, value) { this.attrs[k] = value; }, append(...items) { this.children.push(...items); }, replaceChildren(...items) { this.children = items; },
    addEventListener(k, fn) { this.listeners.set(k, fn); }, removeEventListener(k, fn) { if (this.listeners.get(k) === fn) this.listeners.delete(k); },
    click() { if (!this.disabled) this.listeners.get('click')?.({target: this}); }};
}
function dom() { const doc = {createElement: tag => node(doc, tag)}; return {doc, root: node(doc)}; }
const statusNodes = root => { const line = root.children[0]; return {line, status: line.children[0], button: line.children[1], note: line.children[2]}; };

test('accepts only the frozen three-field identity/version contract and rejects backend, extras, inherited fields and non-objects', () => {
  assert.equal(isAuthStatus(health), true); assert.equal(isAuthStatus(Object.assign(Object.create(null), health)), true);
  for (const value of [null, [], 'ok', 200, {}, {...health, status: 'healthy'}, {...health, service: 'Cy_Omics_v2.0-backend'},
    {...health, version: '0.1.0'}, {...health, version: '2.197.1'}, {...health, extra: true}, {...health, version: '<script>'}, Object.create(health)]) assert.equal(isAuthStatus(value), false);
});

test('view starts once, uses exact credential-free same-origin GET and provides accessible fixed bilingual three-state text', async () => {
  const calls = [], time = clock(), model = createAuthStatusController({...time, fetchImpl: async (...args) => { calls.push(args); return json(); }}), {root} = dom();
  assert.equal(calls.length, 0); const dispose = mountAuthStatus(root, model, (_zh, en) => en), view = statusNodes(root);
  assert.equal(view.status.textContent, 'Authentication service: Checking…'); assert.equal(view.button.disabled, true);
  await settle(); assert.equal(view.status.textContent, 'Authentication service: Available'); assert.equal(view.note.textContent, 'Service connectivity only');
  assert.deepEqual(view.status.attrs, {role: 'status', 'aria-live': 'polite', 'aria-atomic': 'true'}); assert.equal(view.button.type, 'button');
  const [url, options] = calls[0]; assert.equal(url, '/api/auth/status');
  assert.deepEqual(Object.keys(options).sort(), ['cache', 'credentials', 'method', 'redirect', 'signal']);
  assert.deepEqual([options.method, options.credentials, options.cache, options.redirect], ['GET', 'omit', 'no-store', 'error']);
  await model.start(); assert.equal(calls.length, 1); view.button.click(); await settle(); assert.equal(calls.length, 2);
  assert.equal(time.timers.size, 0); dispose(); assert.equal(view.button.listeners.size, 0); model.dispose();
});

test('HTTP status, redirect, MIME, JSON and exact contract failures are unavailable, never reflected, and allow manual recovery', async () => {
  for (const fail of [() => json(health, {status: 201}), () => json(health, {status: 503}), () => json({error: {code: 'auth_unavailable'}}, {status: 503}),
    () => new Response('synthetic_secret', {headers: {'content-type': 'text/html'}}), () => new Response('{bad', {headers: {'content-type': 'application/json'}}),
    () => json({...health, extra: 'synthetic_secret'}), () => json({...health, service: 'wrong'}), () => ({status: 200, redirected: true, body: new ReadableStream()}),
    () => { throw Error('synthetic_private_exception'); }]) {
    let recovered = false; const time = clock(), model = createAuthStatusController({...time, fetchImpl: async () => recovered ? json() : fail()}), {root} = dom();
    const dispose = mountAuthStatus(root, model); await settle(); assert.equal(statusNodes(root).status.textContent, '认证服务：不可用'); assert.equal(time.timers.size, 0);
    recovered = true; statusNodes(root).button.click(); await settle(); assert.equal(statusNodes(root).status.textContent, '认证服务：可用');
    dispose(); model.dispose();
  }
});

test('4KiB body bound, declared lengths and UTF-8 validation are enforced with stream cancellation/release', async () => {
  const valid = JSON.stringify(health), padded = valid + ' '.repeat(4096 - Buffer.byteLength(valid));
  for (const [body, headers, expected] of [[padded, {}, 'available'], [padded + ' ', {}, 'unavailable'], [valid, {'content-length': '4097'}, 'unavailable'],
    [valid, {'content-length': 'bad'}, 'unavailable'], [new Uint8Array([0xff]), {}, 'unavailable']]) {
    const response = new Response(body, {headers: {'content-type': 'application/json', ...headers}}), model = createAuthStatusController({fetchImpl: async () => response});
    await model.start(); assert.equal(model.status().state, expected); assert.equal(response.body.locked, false); model.dispose();
  }
  let canceled = false, reads = 0;
  const body = new ReadableStream({pull(controller) { reads++; controller.enqueue(new Uint8Array(4097)); }, cancel() { canceled = true; }});
  const model = createAuthStatusController({fetchImpl: async () => new Response(body, {headers: {'content-type': 'application/json'}})});
  await model.start(); assert.equal(model.status().state, 'unavailable'); assert.equal(canceled, true); assert.ok(reads < 4); assert.equal(body.locked, false); model.dispose();
});

test('one total 2.5s deadline covers a stalled JSON stream; old fetch/body responses cannot overwrite retry results', async () => {
  for (const pendingBody of [false, true]) {
    const pending = deferred(), time = clock(); let requestSignal, first = true, controller, canceled = false;
    const model = createAuthStatusController({...time, fetchImpl: async (_url, options) => {
      if (!first) return json(); first = false; requestSignal = options.signal;
      if (!pendingBody) return pending.promise;
      return new Response(new ReadableStream({start(value) { controller = value; }, cancel() { canceled = true; }}), {headers: {'content-type': 'application/json'}});
    }});
    const run = model.start(); await settle(); time.fire(); assert.equal(requestSignal.aborted, true); assert.equal(model.status().state, 'unavailable');
    assert.equal(time.timers.size, 0); await model.check(); assert.equal(model.status().state, 'available');
    if (pendingBody) { assert.equal(canceled, true); try { controller.close(); } catch {} } else pending.resolve(json({...health, extra: true}));
    await run; await settle(); assert.equal(model.status().state, 'available'); model.dispose();
  }
});

test('route ownership avoids same-route rerender/language requests, cancels leaving and limits checks to exact login/register', async () => {
  const calls = [], routes = createAuthStatusRoutes({fetchImpl: async (...args) => { calls.push(args); return json(); }}), a = dom(), b = dom();
  for (const path of ['/forgot-password', '/reset-password', '/workspace/private', '/login/extra', '/about', '/demo/account']) assert.equal(routes.enter(path), null);
  const model = routes.enter('/login'), disposeA = mountAuthStatus(a.root, model); await settle(); assert.equal(calls.length, 1); disposeA();
  assert.equal(routes.enter('/login'), model); const disposeB = mountAuthStatus(b.root, routes.current(), (_zh, en) => en); await settle(); assert.equal(calls.length, 1);
  assert.equal(statusNodes(b.root).status.textContent, 'Authentication service: Available'); disposeB();
  const reg = routes.enter('/register'); assert.notEqual(reg, model); await reg.start(); assert.equal(calls.length, 2);
  routes.enter('/about'); assert.equal(routes.current(), null); await model.check(); await reg.check(); assert.equal(calls.length, 2); routes.dispose();
});

test('view replacement and route disposal prevent detached DOM writes, abort pending bodies and remove listeners/timers', async () => {
  const pending = deferred(), time = clock(); let signal;
  const routes = createAuthStatusRoutes({...time, fetchImpl: (_url, options) => { signal = options.signal; return pending.promise; }});
  const model = routes.enter('/login'), a = dom(), dispose = mountAuthStatus(a.root, model), old = statusNodes(a.root); dispose();
  const b = dom(), disposeB = mountAuthStatus(b.root, routes.enter('/login')); assert.equal(time.timers.size, 1);
  routes.enter('/about'); assert.equal(signal.aborted, true); assert.equal(time.timers.size, 0); disposeB();
  pending.resolve(json()); await settle(); assert.equal(old.status.textContent, '认证服务：检查中…'); assert.equal(old.button.listeners.size, 0);
  assert.deepEqual(a.root.children, []); assert.deepEqual(b.root.children, []); routes.dispose();
});

async function appHarness(initialRoute = '/login') {
  const app = (await readFile(new URL('../app.js', import.meta.url), 'utf8')).replace(/^import .*?;\r?\n/gm, '');
  const {doc} = dom(), events = new Map(), nodes = new Map(), calls = [], loginCalls = [], writes = [], reads = [], time = clock(), pending = [];
  let html = '', statusRoot, form, lang, password;
  const root = node(doc), toast = node(doc); nodes.set('#app', root); nodes.set('#toast', toast);
  Object.defineProperty(root, 'innerHTML', {get: () => html, set(value) {
    html = value; statusRoot = value.includes('data-auth-status') ? node(doc) : null;
    form = value.includes('id="auth-form"') ? node(doc, 'form') : null; lang = node(doc, 'button'); lang.dataset.action = 'lang';
    password = node(doc, 'input'); password.value = 'synthetic_password';
    if (form) {
      const email = node(doc, 'input'); email.value = 'researcher@example.org';
      const terms = node(doc, 'input'); terms.checked = false;
      const fields = new Map([['[name=password]', password], ['[name=email]', email], ['[name=terms]', terms],
        ['[type=submit]', node(doc, 'button')], ['[data-signup-state]', node(doc)],
        ...['submit','toggle','recheck','logout','state'].map(key => ['[data-login-' + key + ']', node(doc)]),
        ...['email','password'].map(key => ['[data-login-error="' + key + '"]', node(doc)]),
        ...['email', 'password', 'terms'].map(key => ['[data-signup-error="' + key + '"]', node(doc)])]);
      form.querySelector = selector => fields.get(selector) || null;
    }
  }});
  doc.querySelector = selector => selector === '[data-auth-status]' ? statusRoot : selector === '#auth-form' ? form : nodes.get(selector) || null;
  doc.querySelectorAll = selector => selector === '[data-action]' ? [lang] : [];
  doc.documentElement = {lang: ''};
  const location = {hash: '#' + initialRoute};
  const window = {addEventListener(name, fn) { events.set(name, fn); }, scrollTo() {}};
  const localStorage = {getItem(key) { reads.push(key); return null; }, setItem(key, value) { writes.push([key, value]); }};
  const harnessFetch = async (...args) => { calls.push(args); return pending.length ? pending.shift() : json(); };
  const context = {createPrivateProjectRoutes,createPrivateProjectTransport,mountPrivateProjects,document: doc, location, window, localStorage, setTimeout() { return 1; }, clearTimeout() {},
    createAuthStatusRoutes: () => createAuthStatusRoutes({...time, fetchImpl: harnessFetch}), mountAuthStatus,
    createLoginRoutes, mountLogin, createLoginTransport: options => createLoginTransport({...options, fetchImpl: async (...args) => { loginCalls.push(args); return Response.json({error: {code: args[0] === '/api/v1/me' ? 'unauthenticated' : 'login_unavailable'}}, {status: args[0] === '/api/v1/me' ? 401 : 503}); }}),
    createSignupRoutes, createSignupTransport: options => createSignupTransport({...options, fetchImpl: async () => { throw Error('unexpected signup request'); }}), mountSignup,
    scienceTools, sciencePage() { return ''; }, mountImportWorkspace() { throw Error('unexpected import'); }, mountProteinAssociation() { throw Error('unexpected association'); }};
  vm.runInNewContext(app, context, {timeout: 1000}); await settle();
  return {calls, loginCalls, get loginState() { return form?.querySelector('[data-login-state]'); }, writes, reads, time, pending, events, get html() { return html; }, get statusRoot() { return statusRoot; },
    get password() { return password; }, toast,
    async changeLanguage() { lang.onclick(); await settle(); },
    async navigate(path) { location.hash = '#' + path; events.get('hashchange')(); await settle(); },
    submit() { let blocked = false; form.listeners.get('submit')({preventDefault() { blocked = true; }, target: form}); return blocked; },
    hide() { events.get('pagehide')(); },
  };
}

test('actual app login/register renders retain initial checks across EN/repaints and preserve form-submit blocking even when health is available', async () => {
  const h = await appHarness(); assert.equal(h.calls.length, 1); assert.match(h.html, /登录信息由服务器管理/);
  assert.equal(statusNodes(h.statusRoot).status.textContent, '认证服务：可用'); await h.changeLanguage(); assert.equal(h.calls.length, 1);
  assert.equal(statusNodes(h.statusRoot).status.textContent, 'Authentication service: Available');
  assert.equal(h.submit(), true); assert.equal(h.password.value, ''); assert.equal(h.calls.length, 1); await settle(); assert.match(h.loginState.textContent, /session outcome is unconfirmed/); assert.equal(h.loginCalls.filter(call => call[1].method === 'POST').length, 1);
  await h.navigate('/register'); assert.equal(h.calls.length, 2); assert.match(h.html, /Registration requests are sent only to this platform/); assert.match(h.html, /After verifying your email/);
  assert.equal(h.submit(), true); assert.equal(h.password.value, ''); assert.equal(h.calls.length, 2);
  for (const path of ['/forgot-password', '/reset-password', '/workspace/private', '/about', '/demo/help']) {
    await h.navigate(path); assert.equal(h.statusRoot, null); assert.equal(h.calls.length, 2);
  }
  assert.deepEqual(h.reads, ['pp-lang', 'pp-candidates', 'pp-demo-projects', 'pp-demo-files']); assert.deepEqual(h.writes.map(x => x[0]), ['pp-lang']);
  h.hide(); assert.equal(h.time.timers.size, 0);
});

test('actual app route leave/pagehide cancel requests and same-page language changes do not duplicate pending checks', async () => {
  const h = await appHarness('/about'); assert.equal(h.calls.length, 0); const late = deferred(); h.pending.push(late.promise);
  await h.navigate('/login'); const old = statusNodes(h.statusRoot); assert.equal(h.calls.length, 1); assert.equal(h.time.timers.size, 1);
  await h.changeLanguage(); assert.equal(h.calls.length, 1); assert.equal(h.time.timers.size, 1);
  await h.navigate('/register'); assert.equal(h.calls[0][1].signal.aborted, true); assert.equal(h.calls.length, 2);
  late.resolve(json({...health, extra: true})); await settle(); assert.equal(statusNodes(h.statusRoot).status.textContent, 'Authentication service: Available');
  assert.equal(old.status.textContent, '认证服务：检查中…'); const pending = deferred(); h.pending.push(pending.promise); statusNodes(h.statusRoot).button.click(); await settle();
  const signal = h.calls.at(-1)[1].signal; h.hide(); assert.equal(signal.aborted, true); assert.equal(h.time.timers.size, 0);
  pending.resolve(json()); await settle(); assert.deepEqual(h.statusRoot.children, []);
});

test('actual preview server wires the disabled auth bridge before Worker routing and never calls upstream', async t => {
  assert.notEqual(process.env.CYOMICS_AUTH_STATUS_ENABLED, '1', 'This local test must run without enabling real authentication connectivity');
  let upstreamCalls = 0;
  const server = createPreviewServer({fetchImpl: async () => { upstreamCalls++; throw Error('unexpected upstream'); }});
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const base = 'http://127.0.0.1:' + server.address().port;
  const status = await fetch(base + '/api/auth/status');
  assert.equal(status.status, 503); assert.deepEqual(await status.json(), {error: {code: 'auth_unavailable'}});
  assert.equal(status.headers.get('cache-control'), 'no-store'); assert.equal(status.headers.get('set-cookie'), null);
  const variant = await fetch(base + '/api/auth/status?check=1');
  assert.equal(variant.status, 404); assert.deepEqual(await variant.json(), {error: {code: 'not_found'}});
  const head = await fetch(base + '/api/auth/status', {method: 'HEAD'});
  assert.equal(head.status, 405); assert.equal(head.headers.get('allow'), 'GET'); assert.equal(await head.text(), '');
  const asset = await fetch(base + '/auth-status.js'); assert.equal(asset.status, 200);
  assert.equal(await asset.text(), await readFile(new URL('../auth-status.js', import.meta.url), 'utf8'));
  assert.equal(upstreamCalls, 0);
});
