import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {validateProjectInput, isProject, isProjectList, createPrivateProjectTransport, createPrivateProjectController, createPrivateProjectRoutes, mountPrivateProjects} from '../private-projects.js';
import {createLoginRoutes, createLoginTransport, mountLogin} from '../auth-login.js';
import {createAuthStatusRoutes, mountAuthStatus} from '../auth-status.js';
import {createSignupRoutes, createSignupTransport, mountSignup} from '../auth-signup.js';
import {scienceTools} from '../science-pages.js';

const a = '11111111-1111-4111-8111-111111111111', b = '22222222-2222-4222-8222-222222222222', c = '33333333-3333-4333-8333-333333333333';
const contextA = 'A'.repeat(22), contextB = 'B'.repeat(22);
const project = (project_id = a, name = 'Private project') => ({project_id, name, description: '', species: '', visibility: 'private', createdAt: '2026-11-16T00:00:00Z', updatedAt: '2026-11-16T00:00:00Z'});
const input = () => ({name: ' New project ', description: '', species: ''});
const me = (email = 'a@example.org', context = contextA) => Response.json({status: 'authenticated', user: {email, emailVerified: true}}, {headers: context === null ? {} : {'X-Cyomics-Account-Context': context}});
const page = (items = [], nextCursor = null) => Response.json({items, nextCursor});
const detail = item => Response.json({project: item});
const created = item => Response.json({project: item}, {status: 201});
const error = (status, code) => Response.json({error: {code}}, {status});
const settle = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; }
function clock() {
  let id = 0; const timers = new Map();
  return {timers, setTimer(fn, delay) { assert.equal(delay, 15000); timers.set(++id, fn); return id; }, clearTimer(key) { timers.delete(key); }, expire() { for (const fn of [...timers.values()]) fn(); }};
}
function harness(replies) {
  const calls = [], fileCalls = [], time = clock(), transport = createPrivateProjectTransport({enabled: true, fetchImpl: (...args) => { if (args[1].method === 'GET' && /^\/api\/v1\/projects\/[0-9a-f-]{36}\/files$/.test(args[0])) { fileCalls.push(args); return Response.json({items: [], nextCursor: null}); } calls.push(args); const reply = replies.shift(); assert.ok(reply, 'unexpected synthetic project request'); return reply(...args); }});
  return {calls, fileCalls, time, transport, model: createPrivateProjectController({...time, transport})};
}

test('Project schema uses only project_id/private/UTC server fields and form validation permits optional empty metadata with scalar bounds', () => {
  assert.deepEqual(validateProjectInput({name: '研究 🔬'}), {}); assert.deepEqual(validateProjectInput({name: '🔬'.repeat(80), description: 'a\n\tb', species: ''}), {});
  assert.equal(validateProjectInput({name: ' '}).name, 'invalid_name'); assert.equal(validateProjectInput({name: '🔬'.repeat(81)}).name, 'invalid_name');
  for (const extra of [{owner: 'forged'}, {taxonomy_id: 3702}, {visibility: 'public'}]) assert.equal(validateProjectInput({name: 'x', ...extra}).form, 'invalid_project');
  assert.equal(validateProjectInput({...input(), description: 'x\0'}).description, 'invalid_description'); assert.equal(validateProjectInput({...input(), species: '\uD800'}).species, 'invalid_species');
  assert.equal(isProject(project()), true); assert.equal(isProject({...project(), createdAt: '2026-11-16T00:00:00+00:00', updatedAt: '2026-11-16T00:00:00+00:00'}), true);
  const {project_id, ...withoutId} = project();
  for (const item of [{...withoutId, id: project_id}, {...project(), owner: 'forged'}, {...project(), visibility: 'public'}, {...project(), project_id: a.toUpperCase().replace('1', 'A')}, {...project(), createdAt: '2026-02-30T00:00:00Z'}]) assert.equal(isProject(item), false);
  assert.equal(isProjectList({items: [project()], nextCursor: null}), true); assert.equal(isProjectList({items: [project(), project()], nextCursor: null}), false); assert.equal(isProjectList({items: [], nextCursor: '../other'}), false);
});

test('fixed transport requires a valid me context, defaults closed, sends only exact inputs and keeps context separate from cookies and state', async () => {
  let count = 0; assert.equal(createPrivateProjectTransport({fetchImpl: () => count++}), null); const offline = createPrivateProjectController(); await offline.start(); assert.equal(count, 0); offline.dispose();
  const h = harness([() => me(), () => page(), () => page(), () => detail(project()), () => created(project(c))]), signal = new AbortController().signal;
  assert.throws(() => h.transport.list(null, {signal}), /project_context_changed/); await h.transport.me({signal});
  await h.transport.list(null, {signal}); await h.transport.list('next_page', {signal}); await h.transport.detail(a, {signal}); await h.transport.create({name: ' New '}, {signal});
  assert.deepEqual(h.calls.map(([path]) => path), ['/api/v1/me', '/api/v1/projects?limit=20', '/api/v1/projects?limit=20&cursor=next_page', '/api/v1/projects/' + a, '/api/v1/projects']);
  assert.equal(h.calls[0][1].headers, undefined);
  for (const [, options] of h.calls) { assert.equal(options.credentials, 'same-origin'); assert.equal(options.redirect, 'error'); assert.equal(options.cache, 'no-store'); assert.equal(options.signal, signal); assert.equal(options.headers?.Authorization, undefined); }
  for (const [, options] of h.calls.slice(1)) assert.equal(options.headers['X-Cyomics-Account-Context'], contextA);
  assert.deepEqual(JSON.parse(h.calls.at(-1)[1].body), {name: 'New', description: '', species: ''}); assert.equal(h.calls.at(-1)[1].headers['X-Cyomics-Request'], '1');
  assert.throws(() => h.transport.detail('../files', {signal})); assert.throws(() => h.transport.list('x'.repeat(513), {signal}));
  assert.equal(JSON.stringify(h.model.status()).includes(contextA), false); h.transport.forgetContext(); assert.equal(h.transport.hasContext(), false); h.model.dispose();
});

test('anonymous, unavailable or non-contract me and missing/duplicate/malformed context headers never request project data or fall back to an empty/demo list', async () => {
  for (const reply of [() => error(401, 'unauthenticated'), () => error(401, 'authentication_required'), () => error(503, 'session_unavailable'),
    () => me('a@example.org', null), () => me('a@example.org', 'bad'), () => me('a@example.org', contextA + ',' + contextB),
    () => Response.json({status: 'authenticated', user: {email: 'a@example.org', emailVerified: false}}, {headers: {'X-Cyomics-Account-Context': contextA}})]) {
    const h = harness([reply]); await h.model.start(); assert.equal(h.calls.length, 1); assert.equal(h.model.status().ready, false); assert.equal(h.model.status().canCreate, false); assert.deepEqual(h.model.status().items, []); assert.notEqual(h.model.status().phase, 'ready'); assert.equal(h.transport.hasContext(), false); h.model.dispose();
  }
});

test('only a valid list response creates the empty state; malformed fields, visibility and list failures clear private context', async () => {
  const empty = harness([() => me(), () => page()]); await empty.model.start(); assert.equal(empty.model.status().phase, 'ready'); assert.equal(empty.model.status().canCreate, true); empty.model.dispose();
  for (const reply of [() => Response.json({items: [{...project(), owner: 'private_other'}], nextCursor: null}), () => page([{...project(), visibility: 'public'}]), () => page([], 'unsafe/cursor'), () => Response.json({items: []}), () => error(429, 'project_rate_limited'), () => error(503, 'projects_unavailable')]) {
    const h = harness([() => me(), reply]); await h.model.start(); assert.notEqual(h.model.status().phase, 'ready'); assert.equal(h.model.status().email, null); assert.equal(h.transport.hasContext(), false); assert.deepEqual(h.model.status().items, []); h.model.dispose();
  }
});

test('pagination loads only on manual action with the returned cursor, retains real IDs, and stops on repeated cursors', async () => {
  const h = harness([() => me(), () => page([project()], 'page_two'), () => page([project(b)], null)]); await h.model.start(); await settle(); assert.equal(h.calls.length, 2);
  await h.model.more(); assert.equal(h.calls.length, 3); assert.match(h.calls[2][0], /cursor=page_two$/); assert.deepEqual(h.model.status().items.map(item => item.project_id), [a, b]); await h.model.more(); assert.equal(h.calls.length, 3); h.model.dispose();
  const repeated = harness([() => me(), () => page([project()], 'same_cursor'), () => page([project(b)], 'same_cursor')]); await repeated.model.start(); await repeated.model.more(); assert.equal(repeated.model.status().ready, false); assert.deepEqual(repeated.model.status().items, []); repeated.model.dispose();
});

test('detail switching cancels old reads, never overwrites a later selection, and denied resources do not reveal other project data', async () => {
  const late = deferred(); let cancelled = 0;
  const h = harness([() => me(), () => page([project(a, 'First'), project(b, 'Second')]), () => late.promise, () => detail(project(b, 'Second')), () => error(404, 'project_not_found'), () => error(401, 'unauthenticated')]);
  await h.model.start(); const first = h.model.select(a); await h.model.select(b); await first; assert.equal(h.model.status().selected.project_id, b);
  late.resolve(new Response(new ReadableStream({cancel() { cancelled++; }}), {headers: {'content-type': 'application/json'}})); await settle(); assert.equal(cancelled, 1); assert.equal(h.model.status().selected.project_id, b);
  await h.model.select(c); assert.equal(h.model.status().selected, null); assert.equal(h.model.status().detailCode, 'project_not_found');
  await h.model.select(a); assert.equal(h.model.status().identity, 'anonymous'); assert.deepEqual(h.model.status().items, []); assert.equal(h.transport.hasContext(), false); h.model.dispose();
});

test('creation uses only the server UUID and declares success only after matching detail and homepage list readback, never an optimistic card', async () => {
  const readback = deferred(), made = project(c, 'Persisted'), h = harness([() => me(), () => page([project()], 'old_cursor'), () => created(made), () => readback.promise, () => page([made, project()])]);
  await h.model.start(); h.model.openDialog(); const task = h.model.create(input()); await settle();
  assert.equal(h.model.status().createPhase, 'confirming'); assert.equal(h.model.status().createdProjectId, null); assert.deepEqual(h.model.status().items, []); assert.equal(h.model.status().nextCursor, null);
  await h.model.create(input()); assert.equal(h.calls.filter(call => call[1].method === 'POST').length, 1);
  readback.resolve(detail(made)); assert.equal(await task, true); assert.equal(h.model.status().createPhase, 'created'); assert.equal(h.model.status().createdProjectId, c); assert.equal(h.model.status().selected.project_id, c); assert.equal(h.model.status().dialogOpen, false);
  assert.equal(h.calls.at(-1)[0], '/api/v1/projects?limit=20'); assert.equal(h.calls.at(-2)[0], '/api/v1/projects/' + c); assert.deepEqual(h.model.status().draft, {name: '', description: '', species: ''}); h.model.dispose();
});

test('UUID missing from the fresh homepage or inconsistent readback is only partially confirmed, without matching by name or automatically crawling more pages', async () => {
  const made = project(c, 'Same name'), other = project(b, 'Same name');
  for (const pageReply of [() => page([other], 'more_exists'), () => page([{...made, description: 'different'}])]) {
    const h = harness([() => me(), () => page(), () => created(made), () => detail(made), pageReply]); await h.model.start(); assert.equal(await h.model.create(input()), false);
    assert.equal(h.model.status().createPhase, 'unknown'); assert.equal(h.model.status().createdProjectId, null); assert.equal(h.model.status().canCreate, false); await settle(); assert.equal(h.calls.length, 5); h.model.dispose();
  }
});

test('one 15s POST deadline includes body reading; unknown outcomes block repeat POST until an explicit read-only reload', async () => {
  let cancelled = 0;
  const h = harness([() => me(), () => page(), () => new Response(new ReadableStream({cancel() { cancelled++; }}), {status: 201, headers: {'content-type': 'application/json'}}), () => me(), () => page([project(b, 'New project')])]);
  await h.model.start(); const task = h.model.create(input()); await settle(); h.time.expire(); await task; assert.equal(cancelled, 1); assert.equal(h.model.status().createPhase, 'unknown');
  await h.model.create(input()); assert.equal(h.calls.length, 3); h.model.closeDialog(); h.model.openDialog(); await h.model.create(input()); assert.equal(h.calls.length, 3);
  await h.model.refresh(); assert.equal(h.calls.filter(call => call[1].method === 'POST').length, 1); assert.equal(h.model.status().createdProjectId, null); assert.equal(h.model.status().canCreate, true); assert.equal(h.model.status().items[0].project_id, b); h.model.dispose();
});

test('409 from an old-tab creation clears all A content, rechecks only me for B, and never creates or loads B projects automatically', async () => {
  const h = harness([() => me(), () => page([project(a, 'Account A project')]), () => detail(project()), () => error(409, 'project_context_changed'), () => me('b@example.org', contextB), () => me('b@example.org', contextB), () => page([project(b, 'Account B project')])]);
  await h.model.start(); await h.model.select(a); h.model.openDialog(); h.model.edit('description', 'Account A private draft'); await h.model.create(input()); await settle();
  assert.equal(h.model.status().email, 'b@example.org'); assert.equal(h.model.status().phase, 'identity_changed'); assert.equal(h.model.status().ready, false); assert.equal(h.model.status().selected, null); assert.deepEqual(h.model.status().items, []); assert.deepEqual(h.model.status().draft, {name: '', description: '', species: ''}); assert.equal(h.model.status().dialogOpen, false);
  assert.equal(h.calls.length, 5); assert.equal(h.calls.filter(call => call[1].method === 'POST').length, 1); await h.model.create(input()); assert.equal(h.calls.length, 5);
  assert.equal(JSON.stringify(h.model.status()).includes(contextA), false); assert.equal(JSON.stringify(h.model.status()).includes(contextB), false);
  await h.model.refresh(); assert.equal(h.model.status().items[0].project_id, b); assert.equal(h.calls.at(-1)[1].headers['X-Cyomics-Account-Context'], contextB); h.model.dispose();
});

test('session401 or503 clears the previous nonce and private content, and expired/cancelled old me cannot restore a newer context', async () => {
  for (const reply of [() => error(401, 'unauthenticated'), () => error(503, 'session_unavailable')]) {
    const h = harness([() => me(), () => page([project()]), reply]); await h.model.start(); await h.model.refresh(); assert.equal(h.transport.hasContext(), false); assert.deepEqual(h.model.status().items, []); assert.equal(h.model.status().email, null); h.model.dispose();
  }
  const late = deferred(), h = harness([() => late.promise, () => me('b@example.org', contextB), () => page([project(b)])]); const old = h.model.start(); h.model.dispose();
  const newer = createPrivateProjectController({...h.time, transport: h.transport}); await newer.start(); late.resolve(me()); await old; await settle(); assert.equal(newer.status().email, 'b@example.org');
  assert.equal(h.calls.at(-1)[1].headers['X-Cyomics-Account-Context'], contextB); assert.equal(h.transport.hasContext(), true); newer.dispose();
});

test('bad MIME, malformed or invalid UTF8, oversized list streams and transport errors do not render raw results or retain private metadata', {timeout: 1500}, async () => {
  for (const reply of [() => new Response('{synthetic_private', {headers: {'content-type': 'application/json'}}),
    () => new Response(new Uint8Array([255]), {headers: {'content-type': 'application/json'}}), () => new Response(' '.repeat(512 * 1024 + 1), {headers: {'content-type': 'application/json'}}),
    () => Response.json({items: [], nextCursor: null}, {headers: {'content-type': 'text/html'}}), () => { throw Error('synthetic_private'); }]) {
    const h = harness([() => me(), reply]); await h.model.start();
    assert.notEqual(h.model.status().phase, 'ready'); assert.equal(h.transport.hasContext(), false); assert.deepEqual(h.model.status().items, []); assert.equal(JSON.stringify(h.model.status()).includes('synthetic_private'), false);
    assert.equal(h.time.timers.size, 0); h.model.dispose();
  }
  let cancelled = 0;
  const stream = harness([() => me(), () => new Response(new ReadableStream({start(controller) { controller.enqueue(new Uint8Array(512 * 1024 + 1).fill(32)); }, cancel() { cancelled++; }}), {headers: {'content-type': 'application/json'}})]);
  await stream.model.start(); assert.equal(cancelled, 1); assert.equal(stream.model.status().ready, false); stream.model.dispose();
});

class Element {
  constructor(doc, tag = 'div') { this.ownerDocument = doc; this.tagName = tag; this.children = []; this.attrs = {}; this.dataset = {}; this.listeners = new Map(); this.value = ''; this.disabled = false; this.hidden = false; this.open = false; this.ownText = ''; this.style = {}; }
  set textContent(value) { this.ownText = String(value); this.children = []; } get textContent() { return this.ownText + this.children.map(child => child.textContent).join(''); }
  append(...nodes) { this.children.push(...nodes); } replaceChildren(...nodes) { this.ownText = ''; this.children = nodes; }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  addEventListener(key, fn) { this.listeners.set(key, fn); } removeEventListener(key, fn) { if (this.listeners.get(key) === fn) this.listeners.delete(key); }
  showModal() { this.open = true; } close() { this.open = false; }
  click() { if (!this.disabled && !this.hidden) { this.listeners.get('click')?.({target: this, preventDefault() {}}); this.onclick?.(); } }
  querySelectorAll(selector) {
    const matches = node => {
      if (selector.startsWith('#')) return node.id === selector.slice(1);
      const attr = /^\[([^=\]]+)(?:="([^"]*)")?\]$/.exec(selector);
      if (!attr) return node.tagName === selector;
      const key = attr[1], data = key.startsWith('data-') ? node.dataset[key.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] : node[key];
      const value = Object.hasOwn(node.attrs, key) ? node.attrs[key] : data;
      return attr[2] === undefined ? value !== undefined : value === attr[2];
    };
    const result = []; const walk = node => { for (const child of node.children) { if (matches(child)) result.push(child); walk(child); } }; walk(this); return result;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
function dom() { const doc = {createElement: tag => new Element(doc, tag)}; return {doc, root: new Element(doc)}; }
function fill(root, values = input()) { for (const [key, value] of Object.entries(values)) { const node = root.querySelector('[name="' + key + '"]'); node.value = value; node.listeners.get('input')?.(); } }
function submit(root) { let blocked = false; root.querySelector('[data-private-form]').listeners.get('submit')({preventDefault() { blocked = true; }}); return blocked; }

test('DOM uses literal text for project metadata, provides only the three allowed inputs and accessible errors, and retains draft/busy across language remount without duplicate requests', async () => {
  const pending = deferred(), malicious = project(a, '<img src=x onerror=alert(1)>'), made = project(c, 'Created'), h = harness([() => me(), () => page([malicious]), () => pending.promise, () => detail(made), () => page([made])]);
  const {root} = dom(), unmount = mountPrivateProjects(root, h.model); await settle(); assert.ok(root.textContent.includes(malicious.name)); assert.equal(root.querySelectorAll('img').length, 0);
  root.querySelector('[data-private-create]').click(); assert.equal(root.querySelector('dialog').open, true); assert.equal(root.querySelector('[name="species"]').value, ''); assert.equal(root.querySelector('[name="species"]').required, false);
  assert.equal(root.querySelector('[name="owner"]'), null); assert.equal(root.querySelector('[name="taxonomy_id"]'), null); submit(root); assert.equal(root.querySelector('[name="name"]').attrs['aria-invalid'], 'true');
  fill(root); assert.equal(root.querySelector('[name="name"]').attrs['aria-invalid'], 'false'); assert.equal(root.querySelector('[data-private-create-state]').textContent, '');
  const oldName = root.querySelector('[name="name"]'); assert.equal(submit(root), true); assert.equal(h.calls.length, 3); const before = h.calls.length; unmount(); assert.deepEqual(root.children, []);
  const other = dom(), stop = mountPrivateProjects(other.root, h.model, (_, en) => en); assert.equal(h.calls.length, before); assert.equal(other.root.querySelector('dialog').open, true); assert.equal(other.root.querySelector('[name="name"]').value, input().name); assert.equal(other.root.querySelector('[name="name"]').disabled, true);
  pending.resolve(created(made)); await settle(); assert.match(other.root.textContent, /confirmed by detail and list readback/); assert.equal(other.root.querySelector('dialog').open, false); assert.equal(oldName.listeners.size, 0); stop(); h.model.dispose();
});

async function appHarness(replies) {
  const source = await readFile(process.env.PRIVATE_TXT_FIXTURE_APP || new URL('../app.js', import.meta.url), 'utf8'), {doc} = dom(), root = new Element(doc), events = new Map(), calls = [], fileCalls = [], writes = [], time = clock();
  let html = '', privateRoot, language, transport;
  Object.defineProperty(root, 'innerHTML', {get() { return html; }, set(value) { html = value; privateRoot = value.includes('id="private-projects"') ? new Element(doc) : null; language = new Element(doc); language.dataset.action = 'lang'; }});
  doc.querySelector = key => key === '#app' ? root : key === '#private-projects' ? privateRoot : null;
  doc.querySelectorAll = key => key === '[data-action]' ? [language] : []; doc.documentElement = {lang: ''}; const location = {hash: '#/workspace/projects'};
  const context = {document: doc, location, window: {addEventListener(k, fn) { events.set(k, fn); }, scrollTo() {}}, setTimeout() { return 1; }, clearTimeout() {},
    localStorage: {getItem(key) { return key === 'pp-demo-projects' ? JSON.stringify([{id: 'demo-fixture', name: 'DEMO_ONLY', species: 'demo', date: '2026-11-14'}]) : null; }, setItem(...args) { writes.push(args); }},
    createPrivateProjectTransport: options => transport = createPrivateProjectTransport({...options, fetchImpl: (...args) => { if (args[1].method === 'GET' && /^\/api\/v1\/projects\/[0-9a-f-]{36}\/files$/.test(args[0])) { fileCalls.push(args); return Response.json({items: [], nextCursor: null}); } calls.push(args); const reply = replies.shift(); assert.ok(reply, 'unexpected app private request'); return reply(...args); }}),
    createPrivateProjectRoutes: options => createPrivateProjectRoutes({...time, ...options}), mountPrivateProjects,
    createLoginRoutes, createLoginTransport, mountLogin, createAuthStatusRoutes, mountAuthStatus, createSignupRoutes, createSignupTransport, mountSignup,
    scienceTools, sciencePage() { return ''; }, mountImportWorkspace() { throw Error('unexpected scientific input'); }, mountProteinAssociation() { throw Error('unexpected association'); }};
  vm.runInNewContext(source.replace(/^import .*?;\r?\n/gm, ''), context, {timeout: 1000}); await settle();
  return {get html() { return html; }, get privateRoot() { return privateRoot; }, calls, writes, time, get transport() { return transport; },
    async language() { language.onclick(); await settle(); }, async navigate(path) { location.hash = '#' + path; events.get('hashchange')(); await settle(); }, hide() { events.get('pagehide')(); }};
}

test('actual private route never consumes demo records or persists private data, retains a pending create across EN and leaves all demo behavior separate', async () => {
  const pending = deferred(), made = project(c, 'PRIVATE_CREATED'), h = await appHarness([() => me(), () => page([project(a, 'PRIVATE_LIST')]), () => pending.promise, () => detail(made), () => page([made])]);
  assert.match(h.html, /Private workspace|私人工作空间/); assert.ok(h.privateRoot.textContent.includes('PRIVATE_LIST')); assert.ok(!h.privateRoot.textContent.includes('DEMO_ONLY')); assert.equal(h.calls.length, 2);
  h.privateRoot.querySelector('[data-private-create]').click(); fill(h.privateRoot); submit(h.privateRoot); const before = h.calls.length; await h.language(); assert.equal(h.calls.length, before); assert.equal(h.privateRoot.querySelector('dialog').open, true);
  pending.resolve(created(made)); await settle(); assert.match(h.privateRoot.textContent, /confirmed by detail and list readback/); assert.deepEqual(h.writes.map(value => value[0]), ['pp-lang']);
  await h.navigate('/demo/projects'); assert.match(h.html, /DEMO_ONLY/); assert.doesNotMatch(h.html, /PRIVATE_CREATED|PRIVATE_LIST/); assert.equal(h.transport.hasContext(), false); assert.equal(h.calls.length, 5);
  await h.navigate('/'); assert.match(h.html, /data-go="\/workspace\/projects"/); assert.equal(h.calls.length, 5); h.hide();
});

test('actual route missing a context stays unavailable; pagehide and route leave clear memory and cancel late requests without rendering private metadata elsewhere', async () => {
  const blocked = await appHarness([() => me('a@example.org', null)]); assert.equal(blocked.calls.length, 1); assert.match(blocked.privateRoot.textContent, /无法确认登录身份/); assert.equal(blocked.privateRoot.querySelector('[data-private-create]').disabled, true); blocked.hide();
  for (const hide of [false, true]) {
    const pending = deferred(), h = await appHarness([() => me(), () => pending.promise]), old = h.privateRoot;
    if (hide) h.hide(); else await h.navigate('/about');
    assert.equal(h.calls[1][1].signal.aborted, true); assert.equal(h.transport.hasContext(), false); assert.deepEqual(old.children, []); pending.resolve(page([project()])); await settle(); assert.deepEqual(old.children, []); assert.deepEqual(h.writes, []); assert.equal(h.time.timers.size, 0); h.hide();
  }
});

async function assertCreateReadback503ClearsContext(stage) {
  const old = project(a, 'Account A project'), made = project(c, 'New project');
  const replies = [() => me(), () => page([old], 'old_cursor'), () => detail(old), () => created(made),
    ...(stage === 'detail' ? [() => error(503, 'projects_unavailable')] : [() => detail(made), () => error(503, 'projects_unavailable')]),
    () => me(), () => page([project(b, 'New project')])];
  const h = harness(replies); await h.model.start(); const {root} = dom(), unmount = mountPrivateProjects(root, h.model);
  await h.model.select(a); h.model.openDialog(); h.model.edit('name', 'New project'); h.model.edit('description', 'Account A private draft');
  assert.equal(h.model.status().email, 'a@example.org'); assert.equal(h.model.status().selected.project_id, a); assert.equal(h.transport.hasContext(), true); assert.equal(root.querySelector('dialog').open, true);
  await h.model.create({name: 'New project', description: 'Account A private draft', species: ''});
  const value = h.model.status();
  assert.equal(value.identity, 'unknown'); assert.equal(value.email, null); assert.equal(value.ready, false); assert.equal(value.canCreate, false);
  assert.deepEqual(value.items, []); assert.equal(value.selected, null); assert.equal(value.nextCursor, null); assert.equal(value.createdProjectId, null); assert.deepEqual(value.draft, {name: '', description: '', species: ''});
  assert.equal(value.dialogOpen, false); assert.equal(value.createPhase, 'unknown'); assert.equal(value.createCode, 'project_create_result_unknown'); assert.equal(value.createBlocked, true); assert.equal(h.transport.hasContext(), false);
  assert.equal(root.querySelector('dialog').open, false); assert.equal(root.querySelector('[name="description"]').value, ''); assert.equal(root.querySelector('[data-private-identity]').textContent, '');
  assert.match(root.querySelector('[data-private-state]').textContent, /项目可能已保存/); assert.ok(!root.textContent.includes('Account A project')); assert.ok(!root.textContent.includes('Account A private draft'));
  const requests = h.calls.length; await h.model.create(input()); await settle(); assert.equal(h.calls.length, requests); assert.equal(h.calls.filter(call => call[1].method === 'POST').length, 1);
  await h.model.refresh(); assert.equal(h.model.status().canCreate, true); assert.equal(h.model.status().items[0].project_id, b); assert.equal(h.model.status().createdProjectId, null); assert.notEqual(h.model.status().createPhase, 'created'); assert.equal(h.model.status().selected, null);
  assert.equal(h.calls.filter(call => call[1].method === 'POST').length, 1); assert.equal(h.calls.at(-2)[0], '/api/v1/me'); assert.equal(h.calls.at(-1)[0], '/api/v1/projects?limit=20');
  unmount(); h.model.dispose();
}

test('POST201 followed by detail503 clears old account, private metadata, draft and nonce while preserving an unknown possibly-saved outcome', async () => {
  await assertCreateReadback503ClearsContext('detail');
});

test('POST201/detail200 followed by list503 clears the same context and only manual read refresh restores operations without matching names as success', async () => {
  await assertCreateReadback503ClearsContext('list');
});

test('new file-list fixture traffic is counted separately without consuming any original project reply', async () => {
  const h = harness([() => me(), () => page([project()]), () => detail(project())]);
  await h.model.start(); const {root} = dom(), stop = mountPrivateProjects(root, h.model);
  await h.model.select(a); await settle();
  assert.deepEqual(h.calls.map(([path]) => path), ['/api/v1/me', '/api/v1/projects?limit=20', '/api/v1/projects/' + a]);
  assert.equal(h.fileCalls.length, 1); assert.equal(h.fileCalls[0][0], '/api/v1/projects/' + a + '/files'); assert.equal(h.fileCalls[0][1].method, 'GET');
  assert.equal(h.model.files().status().phase, 'ready'); assert.deepEqual(h.model.files().status().items, []);
  stop(); h.model.dispose();
});
