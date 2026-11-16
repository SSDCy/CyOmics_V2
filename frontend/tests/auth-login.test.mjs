import {createPrivateProjectRoutes,createPrivateProjectTransport,mountPrivateProjects} from '../private-projects.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {validateLogin, isVerifiedSession, createLoginTransport, createLoginController, createLoginRoutes, mountLogin} from '../auth-login.js';
import {createAuthStatusRoutes, mountAuthStatus} from '../auth-status.js';
import {createSignupRoutes, createSignupTransport} from '../auth-signup.js';
import {scienceTools} from '../science-pages.js';

const mailbox = 'scientist@example.org', credentials = () => ({email: mailbox, password: ' p '});
const authBody = (email = mailbox) => ({status: 'authenticated', user: {email, emailVerified: true}});
const auth = () => Response.json(authBody()), anon = () => Response.json({error: {code: 'unauthenticated'}}, {status: 401});
const failure = (status, code) => () => Response.json({error: {code}}, {status});
const signedOut = () => Response.json({status: 'signed_out'});
const settle = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; }
function clock() {
  let id = 0; const timers = new Map();
  return {timers, setTimer(fn, delay) { assert.equal(delay, 16000); timers.set(++id, fn); return id; }, clearTimer(key) { timers.delete(key); }, expire() { for (const fn of [...timers.values()]) fn(); }};
}
function harness(replies = []) {
  const calls = [], time = clock(), fetchImpl = (...args) => { calls.push(args); const reply = replies.shift(); assert.ok(reply, 'unexpected synthetic request'); return reply(...args); };
  const transport = createLoginTransport({enabled: true, fetchImpl}); return {calls, time, transport, model: createLoginController({...time, transport})};
}
function node(doc) {
  return {ownerDocument: doc, children: [], dataset: {}, attrs: {}, listeners: new Map(), value: '', textContent: '', hidden: false, disabled: false, style: {},
    setAttribute(k, v) { this.attrs[k] = v; }, append(...values) { this.children.push(...values); }, replaceChildren(...values) { this.children = values; },
    addEventListener(k, fn) { this.listeners.set(k, fn); }, removeEventListener(k, fn) { if (this.listeners.get(k) === fn) this.listeners.delete(k); },
    click() { if (!this.disabled && !this.hidden) this.listeners.get('click')?.({preventDefault() {}}); }};
}
function documentStub() { const doc = {createElement() { return node(doc); }}; return doc; }
function loginForm(doc) {
  const form = node(doc), email = node(doc), password = node(doc), submit = node(doc), toggle = node(doc), recheck = node(doc), logout = node(doc), status = node(doc);
  const projects = node(doc); projects.hidden = true; projects.href = '#/workspace/projects';
  const errors = {email: node(doc), password: node(doc)};
  const nodes = new Map([['[name=email]', email], ['[name=password]', password], ['[data-login-submit]', submit], ['[data-login-toggle]', toggle],
    ['[data-login-recheck]', recheck], ['[data-login-logout]', logout], ['[data-login-state]', status], ['[data-login-projects]', projects], ...Object.entries(errors).map(([key, value]) => ['[data-login-error="' + key + '"]', value])]);
  form.querySelector = key => nodes.get(key) || null;
  return {form, email, password, submit, toggle, recheck, logout, status, errors, projects,
    fill(input = credentials()) { email.value = input.email; password.value = input.password; },
    send() { let prevented = false; form.listeners.get('submit')({target: form, preventDefault() { prevented = true; }}); return prevented; }};
}

test('login accepts existing short and whitespace passwords, preserves strings, and enforces UTF8/surrogate/body limits without a registration minimum', () => {
  for (const password of ['x', ' ', ' p ', '🔬', 'a'.repeat(72), '密'.repeat(24)]) assert.deepEqual(validateLogin(mailbox, password), {});
  assert.equal(validateLogin(mailbox, '').password, 'password_required');
  for (const password of ['a'.repeat(73), '密'.repeat(25)]) assert.equal(validateLogin(mailbox, password).password, 'password_long');
  assert.equal(validateLogin(mailbox, '\uD800').password, 'password_format'); assert.equal(validateLogin('bad', 'x').email, 'email_format');
  assert.equal(validateLogin(mailbox, '\u0001'.repeat(700)).form, 'auth_body_too_large');
  assert.equal(isVerifiedSession(authBody()), true); for (const value of [{...authBody(), token: 'synthetic'}, {status: 'authenticated', user: {...authBody().user, id: 'synthetic'}}, {status: 'authenticated', user: {email: mailbox, emailVerified: false}}, []]) assert.equal(isVerifiedSession(value), false);
});

test('transport defaults to null and uses only the frozen three routes, credential mode, exact POST header/body and AbortSignal', async () => {
  let calls = 0; assert.equal(createLoginTransport({fetchImpl: () => calls++}), null); const offline = createLoginController(); await offline.start(); await offline.login(credentials()); await offline.logout(); assert.equal(calls, 0); assert.equal(offline.status().phase, 'disabled'); offline.dispose();
  const h = harness([anon, auth, signedOut]), signal = new AbortController().signal;
  await h.transport.me({signal}); await h.transport.login(credentials(), {signal}); await h.transport.logout({signal});
  assert.deepEqual(h.calls.map(([path]) => path), ['/api/v1/me', '/api/auth/login', '/api/auth/logout']);
  for (const [, options] of h.calls) { assert.equal(options.credentials, 'same-origin'); assert.equal(options.cache, 'no-store'); assert.equal(options.redirect, 'error'); assert.equal(options.signal, signal); assert.equal(options.headers?.Origin, undefined); }
  assert.equal(h.calls[0][1].method, 'GET'); assert.equal(h.calls[0][1].body, undefined); assert.equal(h.calls[0][1].headers, undefined);
  for (const [, options] of h.calls.slice(1)) assert.deepEqual(options.headers, {'Content-Type': 'application/json', 'X-Cyomics-Request': '1'});
  assert.deepEqual(JSON.parse(h.calls[1][1].body), credentials()); assert.equal(h.calls[2][1].body, '{}');
  assert.throws(() => h.transport.login({...credentials(), token: 'synthetic'}, {signal}), /invalid_login/); h.model.dispose();
});

test('bootstrap accepts only exact anonymous401 or verified200; prototype401, health, outages and extra fields never enable password submission', async () => {
  for (const reply of [failure(401, 'authentication_required'), failure(503, 'session_unavailable'), () => Response.json({status: 'ok'}), () => Response.json({...authBody(), extra: true}), () => Response.json({error: {code: 'unauthenticated', detail: 'synthetic'}}, {status: 401})]) {
    const h = harness([reply]); await h.model.start(); assert.equal(h.model.status().identity, 'unknown'); assert.equal(h.model.status().canLogin, false);
    await h.model.login(credentials()); assert.equal(h.calls.length, 1); h.model.dispose();
  }
  const anonymous = harness([anon]); await anonymous.model.start(); assert.equal(anonymous.model.status().canLogin, true); await anonymous.model.start(); assert.equal(anonymous.calls.length, 1); anonymous.model.dispose();
  for (const [later, identity, allowed] of [[anon, 'anonymous', true], [failure(503, 'session_unavailable'), 'unknown', false]]) {
    const existing = harness([auth, later]); await existing.model.start(); assert.equal(existing.model.status().identity, 'authenticated'); assert.equal(existing.model.status().email, mailbox);
    await existing.model.checkSession(); assert.equal(existing.model.status().identity, identity); assert.equal(existing.model.status().email, null); assert.equal(existing.model.status().canLogin, allowed); existing.model.dispose();
  }
});

test('POST success remains unconfirmed until the follow-up me proves the same verified cookie identity, with duplicate attempts suppressed', async () => {
  const after = deferred(), h = harness([anon, auth, () => after.promise]); await h.model.start();
  const task = h.model.login(credentials()); await settle(); assert.equal(h.calls.length, 3); assert.equal(h.model.status().identity, 'unknown'); assert.equal(h.model.status().busy, true);
  await h.model.login(credentials()); assert.equal(h.calls.length, 3); assert.equal(JSON.stringify(h.model.status()).includes(credentials().password), false);
  after.resolve(auth()); assert.equal(await task, true); assert.equal(h.model.status().identity, 'authenticated'); assert.equal(h.model.status().email, mailbox); assert.equal(h.time.timers.size, 0); h.model.dispose();
});

test('missing cookie, mismatched me user, unverified or extra POST identities and me503 cannot claim authenticated', async () => {
  for (const followup of [anon, () => Response.json(authBody('other@example.org')), failure(503, 'session_unavailable')]) {
    const h = harness([anon, auth, followup]); await h.model.start(); assert.equal(await h.model.login(credentials()), false); assert.notEqual(h.model.status().identity, 'authenticated'); assert.equal(h.model.status().email, null); h.model.dispose();
  }
  for (const post of [() => Response.json({...authBody(), access_token: 'synthetic'}), () => Response.json({status: 'authenticated', user: {email: mailbox, emailVerified: false}})]) {
    const h = harness([anon, post]); await h.model.start(); await h.model.login(credentials()); assert.equal(h.calls.length, 2); assert.equal(h.model.status().identity, 'unknown'); h.model.dispose();
  }
});

test('finite login errors preserve non-enumerating feedback, context gating and manual bounded retries without assuming anonymous on503/504', async () => {
  for (const [status, code, phase] of [[401, 'invalid_credentials', 'failed'], [400, 'invalid_login', 'failed'], [409, 'login_context_required', 'unknown'], [429, 'login_rate_limited', 'limited'], [503, 'login_unavailable', 'unknown'], [504, 'login_result_unknown', 'unknown']]) {
    const h = harness([anon, failure(status, code)]); await h.model.start(); await h.model.login(credentials()); assert.equal(h.model.status().phase, phase); assert.equal(h.model.status().code, code); assert.equal(h.model.status().identity, 'unknown'); await settle(); assert.equal(h.calls.length, 2); h.model.dispose();
  }
  const h = harness([anon, () => { throw Error('synthetic_private'); }, anon]); await h.model.start(); await h.model.login(credentials()); assert.equal(h.model.status().code, 'login_result_unknown'); assert.equal(h.model.status().canLogin, false);
  await h.model.checkSession(); assert.equal(h.model.status().canLogin, true); assert.equal(h.calls.length, 3); h.model.dispose();
});

test('logout requires signed_out plus me401; retained sessions, outage and malformed success stay unconfirmed, while anonymous logout is idempotent', async () => {
  const good = harness([auth, signedOut, anon]); await good.model.start(); await good.model.logout(); assert.equal(good.model.status().phase, 'signed_out'); assert.equal(good.model.status().identity, 'anonymous'); assert.equal(good.model.status().email, null); good.model.dispose();
  for (const followup of [auth, failure(503, 'session_unavailable'), failure(401, 'authentication_required')]) {
    const h = harness([auth, signedOut, followup]); await h.model.start(); await h.model.logout(); assert.equal(h.model.status().phase, 'unknown'); assert.equal(h.model.status().email, null); h.model.dispose();
  }
  for (const post of [failure(503, 'logout_unavailable'), () => Response.json({status: 'signed_out', extra: true})]) {
    const h = harness([auth, post]); await h.model.start(); await h.model.logout(); assert.equal(h.calls.length, 2); assert.equal(h.model.status().phase, 'unknown'); h.model.dispose();
  }
  const anonymous = harness([anon, signedOut, anon]); await anonymous.model.start(); await anonymous.model.logout(); assert.equal(anonymous.model.status().phase, 'signed_out'); anonymous.model.dispose();
});

test('logout cancels older login and me operations, cancels late response bodies and cannot be undone by stale authenticated data', async () => {
  for (const initial of ['login', 'me']) {
    const late = deferred(); let cancelled = 0;
    const h = harness(initial === 'login' ? [anon, () => late.promise, signedOut, anon] : [() => late.promise, signedOut, anon]);
    let old; if (initial === 'login') { await h.model.start(); old = h.model.login(credentials()); } else old = h.model.start();
    await h.model.logout(); await old; assert.equal(h.model.status().phase, 'signed_out');
    late.resolve(new Response(new ReadableStream({cancel() { cancelled++; }}), {headers: {'content-type': 'application/json'}})); await settle();
    assert.equal(cancelled, 1); assert.equal(h.model.status().phase, 'signed_out'); assert.equal(h.time.timers.size, 0); h.model.dispose();
  }
});

test('16s deadlines cover fetch and body, cancel streams, and do not convert an unknown session or sign-in into anonymous', async () => {
  const pending = deferred(); let lateCancelled = 0;
  const h = harness([() => pending.promise]); const task = h.model.start(); h.time.expire(); await task; assert.equal(h.model.status().identity, 'unknown'); assert.equal(h.model.status().canLogin, false);
  pending.resolve(new Response(new ReadableStream({cancel() { lateCancelled++; }}), {headers: {'content-type': 'application/json'}})); await settle(); assert.equal(lateCancelled, 1); h.model.dispose();
  let cancelled = 0; const body = harness([anon, () => new Response(new ReadableStream({cancel() { cancelled++; }}), {headers: {'content-type': 'application/json'}})]);
  await body.model.start(); const login = body.model.login(credentials()); await settle(); body.time.expire(); await login; assert.equal(cancelled, 1); assert.equal(body.model.status().code, 'login_result_unknown'); assert.equal(body.model.status().identity, 'unknown'); assert.equal(body.time.timers.size, 0); body.model.dispose();
});

test('MIME, invalid UTF8/JSON, 4KiB overflow and redirects are rejected without leaking errors or accepting arbitrary status codes', async () => {
  for (const reply of [() => Response.json(authBody(), {headers: {'content-type': 'text/html'}}), () => new Response('{synthetic_private', {headers: {'content-type': 'application/json'}}),
    () => new Response(new Uint8Array([255]), {headers: {'content-type': 'application/json'}}), () => new Response(' '.repeat(4097), {headers: {'content-type': 'application/json'}}),
    () => Response.json(authBody(), {headers: {'content-length': '4097'}}), () => ({redirected: true, body: null}), () => Response.json(authBody(), {status: 202})]) {
    const h = harness([reply]); await h.model.start(); assert.equal(h.model.status().identity, 'unknown'); assert.equal(JSON.stringify(h.model.status()).includes('synthetic_private'), false); assert.equal(h.time.timers.size, 0); h.model.dispose();
  }
});

test('view preserves short-password values when toggling, clears on submit/unmount, retains busy state across EN and exposes accessible errors and distinct logout', async () => {
  const pending = deferred(), h = harness([anon, () => pending.promise, auth, signedOut, anon]), doc = documentStub(), view = loginForm(doc);
  const unmount = mountLogin(view.form, h.model); assert.equal(view.submit.disabled, true); await settle(); assert.equal(view.submit.disabled, false);
  view.fill({...credentials(), password: ''}); view.send(); assert.equal(view.password.attrs['aria-invalid'], 'true'); view.password.value = 'x'; view.password.listeners.get('input')(); assert.equal(view.errors.password.textContent, '');
  view.fill(); view.toggle.click(); assert.equal(view.password.type, 'text'); assert.equal(view.password.value, credentials().password); view.send(); assert.equal(view.password.value, ''); assert.equal(view.password.type, 'password'); assert.equal(view.submit.disabled, true); assert.equal(view.logout.hidden, false);
  const prior = h.calls.length; view.password.value = 'synthetic_retyped'; unmount(); assert.equal(view.password.value, ''); assert.equal(view.form.listeners.size, 0);
  const translated = loginForm(doc), stop = mountLogin(translated.form, h.model, (_, en) => en); assert.match(translated.status.textContent, /confirming this browser/); assert.equal(h.calls.length, prior);
  pending.resolve(auth()); await settle(); assert.match(translated.status.textContent, /Private projects can now be accessed; research computation is not connected yet/); assert.equal(translated.logout.hidden, false); translated.logout.click(); await settle(); assert.match(translated.status.textContent, /Other devices are unaffected/); stop(); h.model.dispose();
});

async function appHarness(replies = [anon]) {
  const source = await readFile(new URL('../app.js', import.meta.url), 'utf8'), doc = documentStub(), events = new Map(), writes = [], calls = [], health = [], time = clock();
  const root = node(doc), toast = node(doc); let html = '', view, statusRoot, language;
  Object.defineProperty(root, 'innerHTML', {get() { return html; }, set(value) { html = value; view = value.includes('id="auth-form"') ? loginForm(doc) : null; statusRoot = value.includes('data-auth-status') ? node(doc) : null; language = node(doc); language.dataset.action = 'lang'; }});
  doc.querySelector = key => key === '#app' ? root : key === '#toast' ? toast : key === '#auth-form' ? view?.form : key === '[data-auth-status]' ? statusRoot : null;
  doc.querySelectorAll = key => key === '[data-action]' ? [language] : []; doc.documentElement = {lang: ''}; const location = {hash: '#/login'};
  const context = {createPrivateProjectRoutes,createPrivateProjectTransport,mountPrivateProjects,document: doc, location, window: {addEventListener(k, fn) { events.set(k, fn); }, scrollTo() {}}, setTimeout() { return 1; }, clearTimeout() {},
    localStorage: {getItem() { return null; }, setItem(...args) { writes.push(args); }},
    createLoginTransport: options => createLoginTransport({...options, fetchImpl: (...args) => { calls.push(args); const reply = replies.shift(); assert.ok(reply, 'unexpected app synthetic request'); return reply(...args); }}),
    createLoginRoutes: options => createLoginRoutes({...time, ...options}), mountLogin,
    createAuthStatusRoutes: () => createAuthStatusRoutes({fetchImpl: async (...args) => { health.push(args); return Response.json({status: 'ok', service: 'Cy_Omics_v2.0-auth', version: '2.197.0'}); }}), mountAuthStatus,
    createSignupRoutes, createSignupTransport, mountSignup() { return () => {}; }, scienceTools, sciencePage() { return ''; }, mountImportWorkspace() { throw Error('unexpected import'); }, mountProteinAssociation() { throw Error('unexpected association'); }};
  vm.runInNewContext(source.replace(/^import .*?;\r?\n/gm, ''), context, {timeout: 1000}); await settle();
  return {get html() { return html; }, get view() { return view; }, calls, health, writes, time,
    async language() { language.onclick(); await settle(); }, async navigate(path) { location.hash = '#' + path; events.get('hashchange')(); await settle(); }, hide() { events.get('pagehide')(); }};
}

test('actual app binds only exact login, accepts short existing passwords, confirms cookie before displaying identity, keeps demo/workspace private gates and persists only existing language preference', async () => {
  const h = await appHarness([anon, auth, auth]); assert.match(h.html, /data-login-toggle/); assert.match(h.html, /autocomplete="current-password"/); assert.doesNotMatch(h.html, /minlength=/);
  assert.equal(h.calls.length, 1); assert.equal(h.calls[0][0], '/api/v1/me'); h.view.fill(); assert.equal(h.view.send(), true); await settle();
  assert.deepEqual(h.calls.map(call => call[0]), ['/api/v1/me', '/api/auth/login', '/api/v1/me']); assert.match(h.view.status.textContent, /可进入私人项目，科研计算尚未接入/); assert.equal(h.view.password.value, '');
  await h.language(); assert.equal(h.calls.length, 3); assert.match(h.view.status.textContent, /Private projects/); assert.deepEqual(h.writes.map(value => value[0]), ['pp-lang']);
  await h.navigate('/workspace/private'); assert.doesNotMatch(h.html, /data-login-state/); assert.match(h.html, /auth-form/); assert.equal(h.calls.length, 3);
  for (const route of ['/about', '/demo/help', '/forgot-password']) await h.navigate(route); assert.equal(h.calls.length, 3); h.hide();
  const source = await readFile(new URL('../auth-login.js', import.meta.url), 'utf8'); assert.doesNotMatch(source, /localStorage|sessionStorage|document\.cookie|console\.|persist\(|\/workspace|\/tasks|\/files/);
});

test('actual app rejects legacy non-contract401 bootstrap and aborts pending login on hash leave/pagehide without a late identity or extra requests', async () => {
  const blocked = await appHarness([failure(401, 'authentication_required')]); assert.equal(blocked.view.submit.disabled, true); assert.match(blocked.view.status.textContent, /无法确认登录状态/); blocked.view.fill(); blocked.view.send(); await settle(); assert.equal(blocked.calls.length, 1); blocked.hide();
  for (const hide of [true, false]) {
    const pending = deferred(), h = await appHarness([anon, () => pending.promise]); h.view.fill(); const old = h.view; old.send(); const prior = old.status.textContent;
    if (hide) h.hide(); else await h.navigate('/about');
    assert.equal(h.calls[1][1].signal.aborted, true); assert.equal(old.password.value, ''); assert.equal(old.password.type, 'password'); assert.equal(old.form.listeners.size, 0); assert.equal(h.time.timers.size, 0);
    pending.resolve(auth()); await settle(); assert.equal(old.status.textContent, prior); assert.equal(h.calls.length, 2); assert.deepEqual(h.writes, []); h.hide();
  }
});

test('refresh bootstrap displays only the server-confirmed mailbox, keeps password empty/hidden, and clears that identity after sign-out without storage writes', async () => {
  const h = await appHarness([auth, signedOut, anon]);
  assert.equal(h.calls.length, 1); assert.equal(h.calls[0][0], '/api/v1/me'); assert.equal(h.view.email.value, mailbox); assert.equal(h.view.email.disabled, true);
  assert.equal(h.view.password.value, ''); assert.equal(h.view.password.type, 'password'); assert.equal(h.view.toggle.attrs['aria-pressed'], 'false'); assert.deepEqual(h.writes, []);
  h.view.logout.click(); await settle(); assert.equal(h.view.email.value, ''); assert.equal(h.view.password.value, ''); assert.equal(h.view.password.type, 'password');
  assert.match(h.view.status.textContent, /已退出本浏览器/); assert.deepEqual(h.writes, []); h.hide();
  const unavailable = await appHarness([failure(503, 'session_unavailable')]); unavailable.view.email.value = 'typed@example.org'; unavailable.view.email.listeners.get('input')();
  assert.equal(unavailable.view.email.value, 'typed@example.org'); assert.deepEqual(unavailable.writes, []); unavailable.hide();
});

test('private project entry is fail-closed for every non-confirmed phase, including stale authenticated identity and invalid mailbox, and is concealed on view disposal', async () => {
  const view = loginForm(documentStub()); let listener, starts = 0;
  let value = {phase: 'idle', identity: 'unknown', ready: false, busy: false, email: null, errors: {}, canLogin: false};
  const model = {status: () => value, subscribe(fn) { listener = fn; return () => { listener = null; }; }, start() { starts++; return Promise.resolve(); }};
  const stop = mountLogin(view.form, model);
  assert.equal(view.projects.hidden, true); assert.equal(starts, 1);
  for (const phase of ['idle', 'checking', 'login', 'logout', 'failed', 'limited', 'unknown', 'disabled', 'invalid', 'signed_out', 'expired']) {
    value = {...value, phase, identity: 'authenticated', email: mailbox, ready: true}; listener(); assert.equal(view.projects.hidden, true, phase);
  }
  for (const patch of [{identity: 'unknown'}, {identity: 'anonymous'}, {ready: false}, {busy: true}, {email: null}, {email: 'invalid'}]) {
    value = {phase: 'authenticated', identity: 'authenticated', email: mailbox, ready: true, busy: false, errors: {}, ...patch}; listener(); assert.equal(view.projects.hidden, true);
  }
  value = {...value, phase: 'authenticated', identity: 'authenticated', email: mailbox, ready: true, busy: false}; listener();
  assert.equal(view.projects.hidden, false); assert.equal(view.projects.href, '#/workspace/projects'); assert.equal(view.projects.textContent, '进入私人项目');
  assert.match(view.status.textContent, /科研计算尚未接入.*15 分钟.*服务重启后失效/);
  stop(); stop(); assert.equal(view.projects.hidden, true); assert.equal(listener, null); assert.equal(view.form.listeners.size, 0); assert.equal(view.projects.listeners.size, 0);
});

test('actual app entry requires follow-up me, hides during recheck and logout, retains fixed href across EN, and never adds auth requests or private storage', async () => {
  const confirmation = deferred(), recheck = deferred();
  const h = await appHarness([anon, auth, () => confirmation.promise, () => recheck.promise, signedOut, anon]);
  assert.match(h.html, /<a class="primary" data-login-projects href="#\/workspace\/projects" hidden>/);
  assert.match(h.html, /私人项目需登录后访问，科研计算仍未接入/); assert.equal(h.view.projects.hidden, true);
  h.view.fill(); h.view.send(); await settle(); assert.equal(h.view.projects.hidden, true); assert.equal(h.calls.length, 3);
  confirmation.resolve(auth()); await settle(); assert.equal(h.view.projects.hidden, false); assert.equal(h.view.projects.href, '#/workspace/projects');
  h.view.recheck.click(); assert.equal(h.view.projects.hidden, true); await settle(); assert.equal(h.calls.length, 4);
  recheck.resolve(auth()); await settle(); assert.equal(h.view.projects.hidden, false);
  const old = h.view; await h.language(); assert.equal(old.projects.hidden, true); assert.equal(old.form.listeners.size, 0);
  assert.equal(h.view.projects.hidden, false); assert.equal(h.view.projects.href, '#/workspace/projects'); assert.equal(h.view.projects.textContent, 'Enter private projects');
  assert.match(h.html, /Sign in to access private projects; research computation is not connected yet/);
  assert.match(h.view.status.textContent, /at most 15 minutes and ends on a server restart/); assert.equal(h.calls.length, 4);
  h.view.logout.click(); assert.equal(h.view.projects.hidden, true); await settle(); assert.equal(h.view.projects.hidden, true);
  assert.deepEqual(h.calls.map(call => call[0]), ['/api/v1/me', '/api/auth/login', '/api/v1/me', '/api/v1/me', '/api/auth/logout', '/api/v1/me']);
  assert.deepEqual(h.writes, [['pp-lang', 'en']]); h.hide(); assert.equal(h.view.projects.hidden, true);
});

test('entry disappears for anonymous, failed or expired revalidation and never returns from a late response or route disposal', async () => {
  for (const reply of [anon, failure(503, 'session_unavailable')]) {
    const h = await appHarness([auth, reply]); assert.equal(h.view.projects.hidden, false);
    h.view.recheck.click(); assert.equal(h.view.projects.hidden, true); await settle(); assert.equal(h.view.projects.hidden, true);
    assert.equal(h.calls.length, 2); assert.deepEqual(h.writes, []); h.hide();
  }
  const late = deferred(), h = await appHarness([auth, () => late.promise]); assert.equal(h.view.projects.hidden, false);
  h.view.recheck.click(); assert.equal(h.view.projects.hidden, true); h.time.expire(); await settle();
  assert.equal(h.view.projects.hidden, true); late.resolve(auth()); await settle(); assert.equal(h.view.projects.hidden, true);
  assert.equal(h.calls.length, 2); const old = h.view; await h.navigate('/about');
  assert.equal(old.projects.hidden, true); assert.equal(old.form.listeners.size, 0); assert.doesNotMatch(h.html, /data-login-projects/); assert.deepEqual(h.writes, []); h.hide();
});
