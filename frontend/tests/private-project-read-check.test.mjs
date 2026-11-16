import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createProjectReadCheck, mountProjectReadCheck} from '../private-project-read-check.js';

const emails = ['owner-a@example.invalid', 'owner-b@example.invalid'];
const ids = ['11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222'];
const nonce = 'A'.repeat(22), address = () => new URL('http://127.0.0.1:4206/__private-test/project-read');
const path = id => '/api/v1/projects/' + id;
const project = id => ({project_id: id, name: 'Synthetic fixture', description: '', species: '', visibility: 'private', createdAt: '2026-11-16T00:00:00Z', updatedAt: '2026-11-16T00:00:00Z'});
const reply = (body, status = 200, headers = {}) => () => Response.json(body, {status, headers});
const me = (i = 0, context = nonce) => reply({status: 'authenticated', user: {email: emails[i], emailVerified: true}}, 200, context === null ? {} : {'X-Cyomics-Account-Context': context});
const own = i => reply({project: project(ids[i])});
const missing = () => reply({error: {code: 'project_not_found'}}, 404);
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return {resolve, promise}; }
const tick = () => new Promise(resolve => setImmediate(resolve));
function harness(replies = [], location = address()) {
  const calls = [], timers = new Map(); let serial = 0;
  const model = createProjectReadCheck({address: location,
    fetchImpl(url, options) { calls.push([url, options]); assert.ok(replies.length, 'Unexpected synthetic request'); return replies.shift()(url, options); },
    setTimer(fn, delay) { assert.equal(delay, 12000); timers.set(++serial, fn); return serial; }, clearTimer(id) { timers.delete(id); }});
  return {model, calls, timers, expire() { for (const fn of [...timers.values()]) fn(); }};
}

test('both fixed owner directions require exact own positive control then strict other404, with exactly three credentialed GETs', async () => {
  for (const i of [0, 1]) {
    const h = harness([me(i), own(i), missing()]); assert.equal(h.calls.length, 0);
    const value = await h.model.run(); assert.equal(value.verdict, 'PASS'); assert.equal(value.email, emails[i]);
    assert.deepEqual(h.calls.map(([url]) => url), ['/api/v1/me', path(ids[i]), path(ids[1 - i])]);
    h.calls.forEach(([, o], j) => { assert.equal(o.method, 'GET'); assert.equal(o.credentials, 'same-origin'); assert.equal(o.cache, 'no-store'); assert.equal(o.redirect, 'error'); assert.equal(o.body, undefined); assert.ok(o.signal instanceof AbortSignal); assert.deepEqual(o.headers, j ? {'X-Cyomics-Account-Context': nonce} : undefined); });
    assert.equal(h.timers.size, 0); assert.equal(JSON.stringify(value).includes(nonce), false);
    await h.model.run(); assert.equal(h.calls.length, 3); h.model.dispose(); assert.equal(h.model.status().email, null);
  }
});

test('wrong origin, port, path, query or hash is rejected without fetching', async () => {
  for (const url of ['https://127.0.0.1:4206/__private-test/project-read','http://localhost:4206/__private-test/project-read','http://127.0.0.1:4208/__private-test/project-read','http://127.0.0.1:4206/','http://127.0.0.1:4206/__private-test/project-read?x=1','http://127.0.0.1:4206/__private-test/project-read#x']) {
    const h = harness([], new URL(url)); assert.equal((await h.model.run()).verdict, 'BLOCKED'); assert.equal(h.calls.length, 0); h.model.dispose();
  }
  const location = address(), h = harness([me(), own(0), missing()], location);
  h.model.subscribe(() => { if (h.model.status().email) location.hash = '#changed'; });
  assert.equal((await h.model.run()).verdict, 'BLOCKED'); assert.equal(h.calls.length, 1); h.model.dispose();
});

test('unverified, extra-field, unrecognized or case-changed accounts and missing/invalid context stop after me', async () => {
  const bodies = [null, {}, {status: 'authenticated', user: {email: emails[0], emailVerified: false}}, {status: 'authenticated', user: {email: emails[0], emailVerified: true}, token: 'synthetic'}, {status: 'authenticated', user: {email: 'other@example.org', emailVerified: true}}, {status: 'authenticated', user: {email: emails[0].toUpperCase(), emailVerified: true}}];
  for (const first of [...bodies.map(body => reply(body, 200, {'X-Cyomics-Account-Context': nonce})), ...[null, '', 'A'.repeat(21), 'A'.repeat(23), 'A'.repeat(21) + '!', nonce + ', ' + nonce].map(c => me(0, c))]) {
    const h = harness([first]); const value = await h.model.run(); assert.equal(value.verdict, 'BLOCKED'); assert.equal(value.email, null); assert.equal(h.calls.length, 1); h.model.dispose();
  }
});

test('own denial, wrong UUID, extra wrapper or project fields never request the other project', async () => {
  const badOwn = [reply({project: project(ids[1])}), reply({project: project(ids[0]), extra: true}), reply({project: {...project(ids[0]), extra: true}}), reply({project: {...project(ids[0]), visibility: 'public'}}), reply({error: {code: 'project_not_found'}}, 404)];
  for (const second of badOwn) {
    const h = harness([me(), second]); assert.equal((await h.model.run()).verdict, 'BLOCKED'); assert.equal(h.calls.length, 2); h.model.dispose();
  }
});

test('other200 immediately fails and never obtains or invokes a body reader/json/text', async () => {
  let reads = 0, cancelled = 0;
  const other = {status: 200, redirected: false, get headers() { reads++; throw Error('Body metadata must not be inspected'); }, body: {getReader() { reads++; throw Error('Do not read'); }, cancel() { cancelled++; return Promise.resolve(); }}, json() { reads++; }, text() { reads++; }};
  const h = harness([me(), own(0), () => other]); const value = await h.model.run();
  assert.equal(value.verdict, 'FAIL'); assert.equal(value.code, 'OTHER_VISIBLE'); assert.equal(reads, 0); assert.ok(cancelled >= 1); assert.equal(h.calls.length, 3); h.model.dispose();
});

test('other404 must have exact project_not_found schema, not a generic404 or decorated error', async () => {
  for (const body of [{}, {error: {code: 'not_found'}}, {error: {code: 'project_not_found', message: 'private'}}, {error: {code: 'project_not_found'}, extra: true}, {project: project(ids[1])}]) {
    const h = harness([me(), own(0), reply(body, 404)]); assert.equal((await h.model.run()).verdict, 'BLOCKED'); assert.equal(h.calls.length, 3); h.model.dispose();
  }
});

test('401/403/409/503 and non-contract status/redirect at every stage are blocked without later requests', async () => {
  for (const stage of [0, 1, 2]) for (const code of [401, 403, 409, 503, 302, 204]) {
    const response = code === 204 ? () => new Response(null, {status: 204}) : reply({error: {code: 'project_context_changed'}}, code);
    const h = harness([...([me(), own(0)].slice(0, stage)), response]); assert.equal((await h.model.run()).verdict, 'BLOCKED'); assert.equal(h.calls.length, stage + 1); h.model.dispose();
  }
  for (const stage of [0, 1, 2]) {
    const h = harness([...([me(), own(0)].slice(0, stage)), () => ({status: 200, redirected: true, body: null})]);
    assert.equal((await h.model.run()).verdict, 'BLOCKED'); assert.equal(h.calls.length, stage + 1); h.model.dispose();
  }
});

test('MIME, invalid JSON/UTF8 and oversize bodies are blocked; unknown exception text never enters output', async () => {
  for (const first of [() => new Response('{raw-private', {headers: {'content-type': 'application/json'}}), () => new Response(new Uint8Array([255]), {headers: {'content-type': 'application/json'}}), () => new Response(' '.repeat(4097), {headers: {'content-type': 'application/json'}}), reply({}, 200, {'content-type': 'text/html'}), () => { throw Error('raw-private'); }]) {
    const h = harness([first]); const value = await h.model.run(); assert.equal(value.verdict, 'BLOCKED'); assert.equal(JSON.stringify(value).includes('raw-private'), false); assert.equal(h.calls.length, 1); h.model.dispose();
  }
});

test('one total deadline covers pending fetch at every stage, cancels late arrivals and never continues or retries', async () => {
  for (const stage of [0, 1, 2]) {
    const late = deferred(); let cancelled = 0;
    const h = harness([...([me(), own(0)].slice(0, stage)), () => late.promise]); const pending = h.model.run(); await tick();
    assert.equal(h.calls.length, stage + 1); h.expire(); assert.equal((await pending).verdict, 'BLOCKED'); assert.equal(h.model.status().code, 'TIMEOUT');
    late.resolve({body: {cancel() { cancelled++; return Promise.resolve(); }}}); await tick(); assert.equal(cancelled, 1); assert.equal(h.calls.length, stage + 1);
    await h.model.run(); assert.equal(h.calls.length, stage + 1); assert.equal(h.timers.size, 0); h.model.dispose();
  }
});

test('timeout while reading body and disposal while pending abort readers, clear safe identity, and block late writes', async () => {
  let cancelled = 0;
  const h = harness([() => new Response(new ReadableStream({cancel() { cancelled++; }}), {headers: {'content-type': 'application/json'}})]);
  const pending = h.model.run(); await tick(); h.expire(); assert.equal((await pending).code, 'TIMEOUT'); assert.ok(cancelled >= 1); assert.equal(h.calls.length, 1); h.model.dispose();
  const late = deferred(), d = harness([me(), () => late.promise]); const task = d.model.run(); await tick(); assert.equal(d.model.status().email, emails[0]);
  d.model.dispose(); assert.equal(d.calls[1][1].signal.aborted, true); await task; assert.equal(d.model.status().email, null); assert.deepEqual(d.model.status().rows, []);
  late.resolve(Response.json({project: project(ids[0])})); await tick(); assert.equal(d.calls.length, 2); assert.equal(d.model.status().email, null); assert.equal(d.timers.size, 0);
});

function dom() {
  const node = () => ({textContent: '', disabled: false, children: [], events: new Map(), append(x) { this.children.push(x); }, replaceChildren() { this.children = []; }, addEventListener(k, f) { this.events.set(k, f); }, removeEventListener(k, f) { if (this.events.get(k) === f) this.events.delete(k); }});
  const entries = Object.fromEntries(['start', 'result', 'email', 'rows'].map(k => [k, node()]));
  const root = {ownerDocument: {createElement: node}, querySelector(s) { return entries[s.slice(12, -1)]; }};
  const host = {events: new Map(), addEventListener(k, f) { this.events.set(k, f); }, removeEventListener(k, f) { if (this.events.get(k) === f) this.events.delete(k); }};
  return {root, host, ...entries};
}

test('mount does no automatic network; synchronous double click sends one me, pagehide cleans listeners and prevents continuation', async () => {
  const late = deferred(), h = harness([() => late.promise]), view = dom();
  const dispose = mountProjectReadCheck(view.root, {host: view.host, model: h.model}); assert.equal(h.calls.length, 0); assert.equal(view.start.disabled, false);
  const click = view.start.events.get('click'); click(); assert.equal(view.start.disabled, true); click(); assert.equal(h.calls.length, 1);
  view.host.events.get('pagehide')(); late.resolve(me()()); await tick();
  assert.equal(h.calls.length, 1); assert.equal(view.email.textContent, ''); assert.equal(view.result.textContent, ''); assert.equal(view.rows.children.length, 0);
  assert.equal(view.start.events.size, 0); assert.equal(view.host.events.size, 0); dispose();
});

test('standalone assets use fixed same-origin modules/styles and links, with no navigation integration or user-supplied input', async () => {
  const html = await readFile(new URL('../private-project-read-check.html', import.meta.url), 'utf8');
  const js = await readFile(new URL('../private-project-read-check.js', import.meta.url), 'utf8');
  assert.match(html, /临时私有测试，仅检查现有两项目，不修改数据/); assert.match(html, /src="\/__private-test\/project-read\.js"/);
  assert.match(html, /href="\/#\/login"/); assert.match(html, /href="\/#\/workspace\/projects"/);
  assert.doesNotMatch(html, /<input|<form|https?:|<script(?! type="module" src=)/);
  assert.match(js, /from '\/auth-login\.js'/); assert.match(js, /from '\/private-projects\.js'/);
  assert.doesNotMatch(js, /document\.cookie|localStorage|sessionStorage|indexedDB|console\.|innerHTML|\.json\(|\.text\(|method: 'POST'/);
});
