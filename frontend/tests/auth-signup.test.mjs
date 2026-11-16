import {createPrivateProjectRoutes,createPrivateProjectTransport,mountPrivateProjects} from '../private-projects.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import http from 'node:http';
import {once} from 'node:events';
import {validateSignup, createSignupController, createSignupRoutes, createSignupTransport, mountSignup} from '../auth-signup.js';
import {createAuthStatusRoutes, mountAuthStatus} from '../auth-status.js';
import {scienceTools} from '../science-pages.js';
import {createLoginRoutes, createLoginTransport, mountLogin} from '../auth-login.js';

const valid = () => ({email: 'scientist@gmail.com', password: ' synthetic "密碼" 🔬 ', terms: true});
const result = (status = 202, body = {status: 'pending_confirmation'}, headers = {'content-type': 'application/json'}) => new Response(JSON.stringify(body), {status, headers});
const settle = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; }
function clock() {
  let id = 0; const timers = new Map();
  return {timers, setTimer(fn, delay) { assert.equal(delay, 16000); timers.set(++id, fn); return id; }, clearTimer(key) { timers.delete(key); },
    expire() { for (const [key, fn] of [...timers]) { timers.delete(key); fn(); } }};
}
function element(doc, tagName = 'div') {
  return {ownerDocument: doc, tagName, children: [], attrs: {}, dataset: {}, listeners: new Map(), style: {}, value: '', checked: false, disabled: false, textContent: '',
    setAttribute(k, v) { this.attrs[k] = v; }, append(...nodes) { this.children.push(...nodes); }, replaceChildren(...nodes) { this.children = nodes; },
    addEventListener(k, fn) { this.listeners.set(k, fn); }, removeEventListener(k, fn) { if (this.listeners.get(k) === fn) this.listeners.delete(k); }};
}
function documentStub() { const doc = {createElement(tag) { return element(doc, tag); }}; return doc; }
function signupForm(doc) {
  const form = element(doc, 'form'), email = element(doc, 'input'), password = element(doc, 'input'), terms = element(doc, 'input'), button = element(doc, 'button'), status = element(doc);
  const toggle = element(doc, 'button'), passwordHelp = element(doc, 'small'), loginState = element(doc);
  const errors = Object.fromEntries(['email', 'password', 'terms'].map(k => [k, element(doc)]));
  const nodes = new Map([['[name=email]', email], ['[name=password]', password], ['[name=terms]', terms], ['[type=submit]', button], ['[data-signup-state]', status],
    ['[data-signup-toggle]', toggle], ['#signup-password-help', passwordHelp],
    ['[data-login-toggle]', toggle], ['[data-login-submit]', button], ['[data-login-state]', loginState], ['[data-login-recheck]', element(doc, 'button')], ['[data-login-logout]', element(doc, 'button')],
    ...['email', 'password'].map(key => ['[data-login-error="' + key + '"]', errors[key]]),
    ...Object.entries(errors).map(([key, node]) => ['[data-signup-error="' + key + '"]', node])]);
  form.querySelector = selector => nodes.get(selector) || null;
  return {form, email, password, terms, button, status, errors, toggle, passwordHelp, loginState,
    fill(data = valid()) { email.value = data.email; password.value = data.password; terms.checked = data.terms; },
    submit() { let blocked = false; form.listeners.get('submit')({target: form, preventDefault() { blocked = true; }}); return blocked; }};
}

test('local validation accepts common mail providers, preserves Unicode/password spaces, and enforces character, UTF-8 and serialized-body bounds', () => {
  for (const email of ['researcher@qq.com', 'researcher@163.com', 'researcher@gmail.com', 'name+tag@lab.example.org']) assert.deepEqual(validateSignup(email, valid().password), {});
  for (const email of ['missing', 'a@@lab.org', '.a@lab.org', 'a..b@lab.org', 'a@-lab.org', 'a@lab', 'a'.repeat(65) + '@lab.org']) assert.equal(validateSignup(email, valid().password).email, 'email_format');
  const maxEmail = 'a'.repeat(64) + '@' + 'b'.repeat(63) + '.' + 'c'.repeat(63) + '.' + 'd'.repeat(61);
  assert.equal(new TextEncoder().encode(maxEmail).length, 254); assert.deepEqual(validateSignup(maxEmail, 'a'.repeat(12)), {});
  assert.equal(validateSignup(maxEmail + 'd', 'a'.repeat(12)).email, 'email_format');
  for (const password of ['a'.repeat(12), 'a'.repeat(72), '🔬'.repeat(18), '密'.repeat(24), ' '.repeat(12), 'e\u0301'.repeat(6)]) assert.deepEqual(validateSignup('a@lab.org', password), {});
  for (const password of ['a'.repeat(11), '🔬'.repeat(11)]) assert.equal(validateSignup('a@lab.org', password).password, 'password_short');
  for (const password of ['a'.repeat(73), '🔬'.repeat(19), '密'.repeat(25)]) assert.equal(validateSignup('a@lab.org', password).password, 'password_long');
  assert.equal(validateSignup('a@lab.org', '\uD800'.repeat(12)).password, 'password_format');
  const over = validateSignup('a@lab.org', '\u0001'.repeat(700)); assert.equal(over.form, 'signup_body_too_large');
});

test('production-default controller validates locally without terms gating, transport, credentials or password in snapshots', async () => {
  const model = createSignupController({transport: null});
  await model.submit({email: 'invalid', password: 'short', terms: false}); assert.equal(model.status().phase, 'invalid'); assert.equal(model.status().errors.terms, undefined);
  await model.submit({...valid(), terms: false}); assert.equal(model.status().phase, 'disabled');
  assert.deepEqual(Object.keys(model.status()).sort(), ['busy', 'code', 'errors', 'phase']); assert.ok(!JSON.stringify(model.status()).includes(valid().password)); model.dispose();
});

test('explicit mock transport receives only email/password, requires user consent, blocks duplicate submission and accepts only neutral pending confirmation', async () => {
  const pending = deferred(), time = clock(), calls = [], model = createSignupController({...time, transport: (payload, options) => { calls.push({payload, options}); return pending.promise; }});
  await model.submit({...valid(), terms: false}); assert.equal(model.status().errors.terms, 'terms_required'); assert.equal(calls.length, 0);
  const task = model.submit(valid()); assert.equal(model.status().phase, 'submitting'); assert.equal(model.status().busy, true);
  await model.submit(valid()); assert.equal(calls.length, 1); assert.deepEqual(Object.keys(calls[0].payload).sort(), ['email', 'password']);
  assert.equal(calls[0].payload.password, valid().password); assert.deepEqual(Object.keys(calls[0].options), ['signal']);
  assert.equal(new TextEncoder().encode(JSON.stringify(calls[0].payload)).length <= 4096, true);
  pending.resolve(result()); assert.equal(await task, true); assert.equal(model.status().phase, 'pending'); assert.equal(model.status().busy, false); assert.equal(time.timers.size, 0);
  await model.submit(valid()); assert.equal(calls.length, 1); model.dispose();
});

test('HTTP and response validation rejects fabricated success, sessions, extras, bad MIME, malformed JSON, invalid UTF-8 and oversized bodies without reflecting content', async () => {
  const responses = [() => result(200), () => result(201), () => result(202, {status: 'pending_confirmation', user: {email: 'secret'}}),
    () => result(202, {status: 'pending_confirmation', access_token: 'secret'}), () => result(202, ['pending_confirmation']),
    () => result(202, {status: 'success'}), () => result(202, null), () => result(202, {status: 'pending_confirmation'}, {'content-type': 'text/html'}),
    () => new Response('{secret', {status: 202, headers: {'content-type': 'application/json'}}),
    () => new Response(new Uint8Array([255]), {status: 202, headers: {'content-type': 'application/json'}}),
    () => new Response(' '.repeat(4097), {status: 202, headers: {'content-type': 'application/json'}}),
    () => result(202, undefined, {'content-type': 'application/json', 'content-length': '4097'}),
    () => ({status: 202, redirected: true, body: null}), () => result(429, {error: {code: 'signup_unavailable'}}),
    () => result(503, {error: {code: 'signup_unavailable', message: 'secret'}}), () => result(500, {error: {code: 'raw_secret'}})];
  for (const response of responses) {
    const model = createSignupController({transport: async () => response()}); await model.submit(valid());
    assert.equal(model.status().phase, 'failed'); assert.equal(model.status().code, 'signup_unavailable'); assert.ok(!JSON.stringify(model.status()).includes('secret')); model.dispose();
  }
  const model = createSignupController({transport() { throw Error('private exception'); }}); await model.submit(valid()); assert.equal(model.status().code, 'signup_unavailable'); model.dispose();
});

test('only approved error/status pairs produce controlled errors; 429 waits, 504 is unknown and neither retries automatically', async () => {
  const pairs = {invalid_signup: 400, signup_origin_rejected: 403, unsupported_media_type: 415, signup_body_too_large: 413, signup_unavailable: 503, signup_rate_limited: 429, signup_result_unknown: 504};
  for (const [code, status] of Object.entries(pairs)) {
    let calls = 0; const model = createSignupController({transport: async () => { calls++; return result(status, {error: {code}}); }});
    await model.submit(valid()); assert.equal(model.status().code, code); assert.equal(model.status().phase, status === 429 ? 'limited' : status === 504 ? 'unknown' : 'failed');
    await settle(); assert.equal(calls, 1); model.dispose();
  }
});

test('a single deadline covers transport and body, aborts waiting, returns unknown and cancels late response bodies', async () => {
  const time = clock(), pending = deferred(); let signal, cancelled = 0;
  const model = createSignupController({...time, transport: (_, options) => { signal = options.signal; return pending.promise; }});
  const view = signupForm(documentStub()), unmount = mountSignup(view.form, model);
  const task = model.submit(valid()); assert.equal(view.button.disabled, true); assert.equal(view.email.disabled, true);
  time.expire(); await task; assert.equal(view.button.disabled, false); assert.equal(view.email.disabled, false); assert.equal(view.password.disabled, false); assert.equal(view.form.attrs['aria-busy'], 'false');
  assert.match(view.status.textContent, /结果暂时未知/); unmount(); assert.equal(model.status().phase, 'unknown'); assert.equal(model.status().busy, false); assert.equal(signal.aborted, true);
  pending.resolve(new Response(new ReadableStream({cancel() { cancelled++; }}), {status: 202, headers: {'content-type': 'application/json'}})); await settle(); assert.equal(cancelled, 1); assert.equal(model.status().phase, 'unknown'); model.dispose();
  let bodyCancelled = 0; const time2 = clock(), bodyModel = createSignupController({...time2, transport: async () => new Response(new ReadableStream({cancel() { bodyCancelled++; }}), {status: 202, headers: {'content-type': 'application/json'}})});
  const bodyTask = bodyModel.submit(valid()); await settle(); time2.expire(); await bodyTask; assert.equal(bodyModel.status().phase, 'unknown'); assert.equal(bodyCancelled, 1); assert.equal(time2.timers.size, 0); bodyModel.dispose();
});

test('views clear passwords, expose field accessibility, retain state across language replacement, and route disposal prevents stale writes', async () => {
  const time = clock(), pending = deferred(), routes = createSignupRoutes({...time, transport: () => pending.promise}), doc = documentStub();
  assert.equal(routes.enter('/login'), null); const model = routes.enter('/register'), view = signupForm(doc), unmount = mountSignup(view.form, model);
  view.fill({...valid(), email: 'bad'}); assert.equal(view.submit(), true); assert.equal(view.password.value, ''); assert.equal(view.email.attrs['aria-invalid'], 'true'); assert.match(view.errors.email.textContent, /有效邮箱/); assert.equal(view.terms.checked, true);
  view.fill(); view.submit(); assert.equal(view.button.disabled, true); assert.equal(view.form.attrs['aria-busy'], 'true'); assert.equal(view.password.value, '');
  view.password.value = 'typed while detached'; const oldText = view.status.textContent; unmount(); assert.equal(view.password.value, ''); assert.equal(view.form.listeners.size, 0);
  const translated = signupForm(doc), disposeTranslated = mountSignup(translated.form, routes.enter('/register'), (_, en) => en);
  assert.equal(translated.button.textContent, 'Submitting…'); assert.equal(translated.terms.checked, false); assert.equal(time.timers.size, 1);
  routes.enter('/about'); disposeTranslated(); pending.resolve(result()); await settle(); assert.equal(view.status.textContent, oldText); assert.equal(time.timers.size, 0); routes.dispose();
});

async function appHarness({route = '/register', transport} = {}) {
  const source = await readFile(new URL('../app.js', import.meta.url), 'utf8');
  assert.match(source, /const signupRoutes=createSignupRoutes\(\{transport:createSignupTransport\(\{enabled:true\}\)\}\)/);
  const app = source.replace(/^import .*?;\r?\n/gm, ''), doc = documentStub(), events = new Map(), writes = [], calls = [], loginCalls = [], time = clock();
  const root = element(doc), toast = element(doc); let html = '', view, statusRoot, lang;
  Object.defineProperty(root, 'innerHTML', {get() { return html; }, set(value) {
    html = value; view = value.includes('id="auth-form"') ? signupForm(doc) : null; statusRoot = value.includes('data-auth-status') ? element(doc) : null;
    lang = element(doc, 'button'); lang.dataset.action = 'lang';
  }});
  doc.querySelector = selector => selector === '#app' ? root : selector === '#toast' ? toast : selector === '#auth-form' ? view?.form : selector === '[data-auth-status]' ? statusRoot : null;
  doc.querySelectorAll = selector => selector === '[data-action]' ? [lang] : []; doc.documentElement = {lang: ''};
  const location = {hash: '#' + route}, window = {addEventListener(k, fn) { events.set(k, fn); }, scrollTo() {}};
  const context = {createPrivateProjectRoutes,createPrivateProjectTransport,mountPrivateProjects,document: doc, window, location, setTimeout() { return 1; }, clearTimeout() {},
    localStorage: {getItem() { return null; }, setItem(...args) { writes.push(args); }},
    createAuthStatusRoutes: () => createAuthStatusRoutes({fetchImpl: async (...args) => { calls.push(args); return Response.json({status: 'ok', service: 'Cy_Omics_v2.0-auth', version: '2.197.0'}); }}), mountAuthStatus,
    createSignupTransport: options => createSignupTransport({...options, fetchImpl: async (...args) => { calls.push(args); return result(503, {error: {code: 'signup_unavailable'}}); }}),
    createLoginRoutes, mountLogin, createLoginTransport: options => createLoginTransport({...options, fetchImpl: async (...args) => { loginCalls.push(args); return result(args[0] === '/api/v1/me' ? 401 : 503, {error: {code: args[0] === '/api/v1/me' ? 'unauthenticated' : 'login_unavailable'}}); }}),
    createSignupRoutes: options => createSignupRoutes({...time, transport: transport === undefined ? options.transport : transport}), mountSignup, scienceTools, sciencePage() { return ''; },
    mountImportWorkspace() { throw Error('unexpected science import'); }, mountProteinAssociation() { throw Error('unexpected association'); }};
  vm.runInNewContext(app, context, {timeout: 1000}); await settle();
  return {get html() { return html; }, get view() { return view; }, toast, calls, loginCalls, writes, time,
    async language() { lang.onclick(); await settle(); }, async navigate(path) { location.hash = '#' + path; events.get('hashchange')(); await settle(); }, hide() { events.get('pagehide')(); }};
}

test('actual app with explicitly disabled injected transport keeps zero POST, disabled profile fields, accessible errors, unchanged login/health and language-only existing storage writes', async () => {
  const h = await appHarness({transport: null}); assert.equal(h.calls.length, 1); assert.equal(h.calls[0][0], '/api/auth/status'); assert.equal(h.calls[0][1].method, 'GET');
  for (const name of ['name', 'institution', 'field', 'country']) assert.match(h.html, new RegExp('<input disabled name="' + name + '"'));
  assert.match(h.html, /<form id="auth-form" novalidate>/); assert.match(h.html, /id="signup-email" aria-describedby="signup-email-error"/); assert.match(h.html, /data-signup-state role="status" aria-live="polite"/);
  assert.doesNotMatch(h.html, /name="terms"[^>]*required/); assert.match(h.html, /不采集/);
  h.view.fill({...valid(), email: 'bad', terms: false}); h.view.submit(); assert.equal(h.view.password.value, ''); assert.match(h.view.errors.email.textContent, /有效邮箱/); assert.equal(h.view.errors.terms.textContent, '');
  await h.language(); assert.match(h.view.errors.email.textContent, /valid email/); assert.equal(h.calls.length, 1);
  h.view.fill({...valid(), terms: false}); assert.equal(h.view.submit(), true); assert.match(h.view.status.textContent, /Registration is not open/); assert.equal(h.view.password.value, ''); assert.equal(h.view.terms.checked, false); assert.equal(h.calls.length, 1);
  await h.navigate('/login'); h.view.fill(); assert.equal(h.view.submit(), true); assert.equal(h.view.password.value, ''); await settle(); assert.match(h.view.loginState.textContent, /session outcome is unconfirmed/); assert.equal(h.loginCalls.filter(call => call[1].method === 'POST').length, 1); assert.equal(h.calls.length, 2);
  for (const path of ['/forgot-password', '/reset-password', '/workspace/private', '/about']) await h.navigate(path);
  assert.equal(h.calls.length, 2); assert.deepEqual(h.writes.map(x => x[0]), ['pp-lang']); h.hide();
});

test('actual app mock-only submitting/pending states survive EN rerender without duplicate calls, do not claim success, and clear every replaced password', async () => {
  const pending = deferred(); let calls = 0;
  const h = await appHarness({transport: () => { calls++; return pending.promise; }}); h.view.fill(); const old = h.view; old.submit(); old.submit();
  assert.equal(old.password.value, ''); assert.equal(calls, 1); assert.match(old.status.textContent, /提交/); old.password.value = 'synthetic retyped';
  await h.language(); assert.equal(old.password.value, ''); assert.equal(old.form.listeners.size, 0); assert.equal(h.view.button.textContent, 'Submitting…'); assert.equal(calls, 1); assert.equal(h.calls.length, 1);
  pending.resolve(result()); await settle(); assert.match(h.view.status.textContent, /^If this email can be used to register/); assert.doesNotMatch(h.view.status.textContent, /success|delivered|verified/); assert.equal(h.view.button.disabled, true); assert.equal(h.time.timers.size, 0); h.hide();
});

test('actual app route leave and pagehide cancel mock waits; late responses never update the login page or make a session', async () => {
  for (const hide of [false, true]) {
    const pending = deferred(); let signal;
    const h = await appHarness({transport: (_, options) => { signal = options.signal; return pending.promise; }}); h.view.fill(); const old = h.view; old.submit(); const oldMessage = old.status.textContent;
    if (hide) h.hide(); else await h.navigate('/login');
    assert.equal(signal.aborted, true); assert.equal(h.time.timers.size, 0); assert.equal(old.password.value, ''); assert.equal(old.form.listeners.size, 0);
    pending.resolve(result()); await settle(); assert.equal(old.status.textContent, oldMessage); assert.deepEqual(h.writes, []); assert.ok(h.calls.every(call => call[0] === '/api/auth/status' && call[1].method === 'GET')); h.hide();
  }
});

test('editing corrects stale field errors and summary immediately; exactly 12 ordinary characters pass while the Unicode byte ceiling remains enforced', () => {
  const model = createSignupController({transport: null}), view = signupForm(documentStub()), unmount = mountSignup(view.form, model);
  view.fill({...valid(), email: 'invalid', password: 'short', terms: false}); view.submit();
  assert.match(view.errors.password.textContent, /至少 12/); assert.equal(view.password.attrs['aria-invalid'], 'true');
  view.password.value = 'abcdefgh1234'; view.password.listeners.get('input')();
  assert.equal(view.errors.password.textContent, ''); assert.equal(view.password.attrs['aria-invalid'], 'false'); assert.match(view.passwordHelp.textContent, /长度符合要求/);
  view.email.value = 'researcher@qq.com'; view.email.listeners.get('input')();
  assert.equal(view.errors.email.textContent, ''); assert.equal(view.email.attrs['aria-invalid'], 'false'); assert.equal(view.status.textContent, ''); assert.equal(model.status().phase, 'idle');
  view.password.value = '🔬'.repeat(19); view.password.listeners.get('input')(); assert.match(view.errors.password.textContent, /过长/); assert.equal(view.password.attrs['aria-invalid'], 'true'); assert.doesNotMatch(view.passwordHelp.textContent, /符合要求/);
  view.password.value = '🔬'.repeat(18); view.password.listeners.get('input')(); assert.equal(view.errors.password.textContent, ''); assert.match(view.passwordHelp.textContent, /符合要求/);
  assert.equal(view.terms.checked, false); assert.ok(!JSON.stringify(model.status()).includes('abcdefgh1234')); unmount(); model.dispose();
});

test('password visibility preserves values without submission, resets on submit and unmount, and is disabled while a mock request is busy', async () => {
  const pending = deferred(); let calls = 0;
  const model = createSignupController({transport: () => { calls++; return pending.promise; }}), view = signupForm(documentStub()), unmount = mountSignup(view.form, model);
  view.fill(); const original = view.password.value;
  assert.equal(view.password.type, 'password'); assert.equal(view.toggle.attrs['aria-pressed'], 'false'); assert.equal(view.toggle.attrs['aria-label'], '显示密码');
  view.toggle.listeners.get('click')(); assert.equal(view.password.type, 'text'); assert.equal(view.password.value, original); assert.equal(view.toggle.attrs['aria-label'], '隐藏密码'); assert.equal(view.toggle.attrs['aria-pressed'], 'true'); assert.equal(calls, 0);
  view.toggle.listeners.get('click')(); assert.equal(view.password.type, 'password'); assert.equal(view.password.value, original); assert.equal(calls, 0);
  view.toggle.listeners.get('click')(); view.submit(); assert.equal(view.password.type, 'password'); assert.equal(view.password.value, ''); assert.equal(view.toggle.attrs['aria-pressed'], 'false'); assert.equal(view.toggle.disabled, true); assert.equal(calls, 1);
  view.toggle.listeners.get('click')(); assert.equal(view.password.type, 'password'); assert.equal(calls, 1);
  pending.resolve(result(503, {error: {code: 'signup_unavailable'}})); await settle();
  view.fill(); view.toggle.listeners.get('click')(); assert.equal(view.password.type, 'text'); unmount(); assert.equal(view.password.type, 'password'); assert.equal(view.password.value, ''); assert.equal(view.toggle.listeners.size, 0);
  assert.equal(view.password.listeners.size, 0); assert.equal(view.email.listeners.size, 0); assert.equal(view.terms.listeners.size, 0); model.dispose();
});

test('actual register markup and bindings expose a bilingual eye button, clear corrected errors, and reset visibility on language change and pagehide without altering login', async () => {
  const h = await appHarness(); assert.match(h.html, /type="button" class="signup-password-toggle" data-signup-toggle/); assert.match(h.html, /aria-controls="signup-password" aria-pressed="false"/);
  h.view.fill({...valid(), password: 'short'}); h.view.submit(); h.view.password.value = 'abcdefgh1234'; h.view.password.listeners.get('input')();
  assert.equal(h.view.errors.password.textContent, ''); assert.equal(h.view.status.textContent, ''); assert.match(h.view.passwordHelp.textContent, /符合要求/);
  h.view.toggle.listeners.get('click')(); const old = h.view; assert.equal(old.password.type, 'text'); await h.language();
  assert.equal(old.password.type, 'password'); assert.equal(old.password.value, ''); assert.equal(h.view.password.type, 'password'); assert.equal(h.view.toggle.attrs['aria-label'], 'Show password'); assert.equal(h.calls.length, 1);
  h.view.fill(); h.view.toggle.listeners.get('click')(); assert.equal(h.view.toggle.attrs['aria-label'], 'Hide password'); h.hide(); assert.equal(h.view.password.value, ''); assert.equal(h.view.password.type, 'password');
  await h.navigate('/login'); assert.doesNotMatch(h.html, /data-signup-toggle|signup-password-help/); assert.match(h.html, /data-login-toggle/); assert.ok(h.calls.every(call => call[0] === '/api/auth/status' && call[1].method === 'GET')); h.hide();
});

test('signup transport defaults to disabled and explicit opt-in sends only the fixed credential-compatible JSON POST with exact password and AbortSignal', async () => {
  const calls = [], fetchImpl = async (...args) => { calls.push(args); return result(); };
  assert.equal(createSignupTransport({fetchImpl}), null); assert.equal(createSignupTransport({enabled: false, fetchImpl}), null); assert.equal(calls.length, 0);
  const transport = createSignupTransport({enabled: true, fetchImpl}), controller = new AbortController(), input = valid();
  const response = await transport({email: input.email, password: input.password}, {signal: controller.signal}); assert.equal(response.status, 202);
  const [path, options] = calls[0]; assert.equal(path, '/api/auth/signup');
  assert.deepEqual(Object.keys(options).sort(), ['body', 'cache', 'credentials', 'headers', 'method', 'redirect', 'signal']);
  assert.equal(options.method, 'POST'); assert.equal(options.credentials, 'same-origin'); assert.equal(options.cache, 'no-store'); assert.equal(options.redirect, 'error'); assert.equal(options.signal, controller.signal);
  assert.deepEqual(options.headers, {'Content-Type': 'application/json'}); assert.deepEqual(JSON.parse(options.body), {email: input.email, password: input.password});
  assert.throws(() => transport({...input, extra: true}, {signal: controller.signal}), /invalid_signup/);
  assert.throws(() => transport({email: input.email, password: 'short'}, {signal: controller.signal}), /invalid_signup/); assert.equal(calls.length, 1);
});

test('explicit candidate binding uses the fixed transport only after valid input and manual terms; backend-disabled result stays unavailable without enabling login', async () => {
  const h = await appHarness(); assert.match(h.html, /注册请求仅提交至本平台/); assert.equal(h.calls.length, 1);
  h.view.fill({...valid(), terms: false}); h.view.submit(); await settle(); assert.equal(h.calls.length, 1); assert.match(h.view.errors.terms.textContent, /自行确认/);
  h.view.fill(); h.view.submit(); await settle(); assert.equal(h.calls.length, 2); assert.equal(h.calls[1][0], '/api/auth/signup'); assert.equal(h.calls[1][1].method, 'POST');
  assert.equal(h.view.password.value, ''); assert.equal(h.view.password.type, 'password'); assert.match(h.view.status.textContent, /暂时无法完成/); assert.doesNotMatch(h.view.status.textContent, /已创建|已发送|已验证/);
  await h.language(); assert.equal(h.calls.length, 2); assert.match(h.view.status.textContent, /could not be completed/);
  await h.navigate('/login'); h.view.fill(); h.view.submit(); await settle(); assert.equal(h.calls.length, 3); assert.equal(h.calls[2][0], '/api/auth/status'); assert.match(h.view.loginState.textContent, /session outcome is unconfirmed/); assert.equal(h.loginCalls.filter(call => call[1].method === 'POST').length, 1); h.hide();
});

test('fixed transport and controller handle synthetic loopback HTTP pending and backend refusal without calling an authentication service', async t => {
  const seen = []; let status = 202;
  const server = http.createServer(async (req, res) => {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    seen.push({url: req.url, method: req.method, type: req.headers['content-type'], body: JSON.parse(Buffer.concat(chunks))});
    res.writeHead(status, {'content-type': 'application/json', 'cache-control': 'no-store'});
    res.end(JSON.stringify(status === 202 ? {status: 'pending_confirmation'} : {error: {code: 'signup_unavailable'}}));
  });
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  server.listen(0, '127.0.0.1'); await once(server, 'listening'); const origin = 'http://127.0.0.1:' + server.address().port;
  const transport = createSignupTransport({enabled: true, fetchImpl: (path, options) => fetch(origin + path, options)});
  const pending = createSignupController({transport}); assert.equal(await pending.submit(valid()), true); assert.equal(pending.status().phase, 'pending'); pending.dispose();
  status = 503; const disabled = createSignupController({transport}); assert.equal(await disabled.submit(valid()), false); assert.equal(disabled.status().code, 'signup_unavailable'); disabled.dispose();
  assert.equal(seen.length, 2); for (const request of seen) { assert.equal(request.url, '/api/auth/signup'); assert.equal(request.method, 'POST'); assert.equal(request.type, 'application/json'); assert.deepEqual(request.body, {email: valid().email, password: valid().password}); }
});
