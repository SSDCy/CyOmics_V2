import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash, webcrypto} from 'node:crypto';
import {createRawTransport, createPrivateTxtController, isRawFile, isRawList, txtFilenameHeader, verifyRawContent, mountPrivateTxt} from '../private-txt-upload.js';
import {createPrivateProjectController, createPrivateProjectTransport} from '../private-projects.js';

const a = '11111111-1111-4111-8111-111111111111', b = '22222222-2222-4222-8222-222222222222', rid = '33333333-3333-4333-8333-333333333333', fid = '44444444-4444-4444-8444-444444444444';
const raw = new Uint8Array([239, 187, 191, 80, 114, 111, 116, 101, 105, 110, 13, 10, 0, 255, 10]);
const file = () => new File([raw], '原样 🧪.TXT');
const hash = createHash('sha256').update(raw).digest('hex');
const record = (project_id = a, extra = {}) => ({file_id: fid, project_id, request_id: rid, filename: file().name, format: 'proteinGroups_txt', bytes: raw.length, sha256: hash, status: 'stored', createdAt: '2026-11-16T00:00:00Z', ...extra});
const list = (items = []) => () => Response.json({items, nextCursor: null});
const receipt = (value = record(), status = 200) => () => Response.json({file: value}, {status});
const error = (status, code) => () => Response.json({error: {code}}, {status});
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; }
function harness(replies = [], projectId = a, options = {}) {
  const importCalls = []; const calls = [], timers = new Map(), denials = []; let active = true, serial = 0, randomCalls = 0, clockNow = 0;
  const transport = createRawTransport({projectId, fetchImpl(path, options) { if(path.startsWith('/api/v1/uploads/')) { importCalls.push([path,options]); assert.equal(options.method,'GET'); assert.ok(importCalls.length<=4); return error(404,'dataset_version_not_found')(); } calls.push([path, options]); assert.ok(replies.length, 'Unexpected synthetic raw request'); return replies.shift()(path, options); },
    getOptions(signal) { return {method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', headers: {'X-Cyomics-Account-Context': options.fixtureContext || 'A'.repeat(22)}, signal}; }, isCurrent: () => active});
  const model = createPrivateTxtController({projectId, transport, onDenied(response) { denials.push(response.status); },
    cryptoImpl: {subtle: webcrypto.subtle, randomUUID() { randomCalls++; return rid; }}, now: () => clockNow,
    setTimer(fn, delay) { assert.ok([15000, 60000].includes(delay)); timers.set(++serial, fn); return serial; }, clearTimer(id) { timers.delete(id); }, ...options});
  return {calls, importCalls, timers, denials, model, transport, randomCalls: () => randomCalls, advance(ms) { clockNow += ms; }, loseBinding() { active = false; }, expire() { for (const fn of [...timers.values()]) fn(); }};
}

test('choose makes no request; explicit save posts the identical File, original SHA and exact encoded headers, then receipt and list confirm', async () => {
  const h = harness([receipt(record(), 201), receipt(), list([record()])]); const input = file();
  assert.equal(h.model.select(input), true); assert.equal(h.calls.length, 0); assert.equal(h.model.status().phase, 'chosen');
  const pending = h.model.save(); assert.equal(await h.model.save(), false); assert.equal(await pending, true);
  assert.equal(h.model.status().phase, 'saved'); assert.deepEqual(h.model.status().receipt, record()); assert.equal(h.model.status().chosen, null);
  assert.deepEqual(h.calls.map(([path, o]) => [o.method, path]), [['POST', '/api/v1/projects/' + a + '/raw-uploads'], ['GET', '/api/v1/projects/' + a + '/raw-uploads/' + rid], ['GET', '/api/v1/projects/' + a + '/files']]);
  const options = h.calls[0][1]; assert.equal(options.body, input); assert.deepEqual(new Uint8Array(await options.body.arrayBuffer()), raw);
  assert.deepEqual(options.headers, {'X-Cyomics-Account-Context': 'A'.repeat(22), 'Content-Type': 'application/octet-stream', 'Idempotency-Key': rid,
    'X-Cyomics-Filename': Buffer.from(input.name, 'utf8').toString('base64url'), 'X-Cyomics-File-Format': 'proteinGroups_txt', 'X-Cyomics-File-Bytes': String(raw.length), 'X-Cyomics-File-Sha256': hash, 'X-Cyomics-Request': '1'});
  for (const [, o] of h.calls) { assert.equal(o.credentials, 'same-origin'); assert.equal(o.cache, 'no-store'); assert.equal(o.redirect, 'error'); assert.ok(o.signal instanceof AbortSignal); assert.equal(o.headers.Authorization, undefined); assert.equal(o.headers['X-Cyomics-Remaining-Ms'], undefined); }
  assert.equal(h.randomCalls(), 1); assert.equal(h.timers.size, 0); assert.equal(await h.model.save(), false); h.model.dispose();
});

test('idempotent200 is not saved until both exact receipt and server list readbacks agree', async () => {
  for (const replies of [[receipt(record(), 200), receipt(), list([])], [receipt(record(), 201), receipt(record(a, {file_id: b})), list([record()])], [receipt(record(), 201), receipt(), list([record(a, {sha256: '0'.repeat(64)})])]]) {
    const h = harness(replies); h.model.select(file()); assert.equal(await h.model.save(), false); assert.equal(h.model.status().unknown, true); assert.equal(h.model.status().receipt, null); h.model.dispose();
  }
  const h = harness([receipt(record(), 200), receipt(), list([record()])]); h.model.select(file()); assert.equal(await h.model.save(), true); h.model.dispose();
});

test('reload of a new controller uses bounded server list, never selection, filename matching or client persistence', async () => {
  const h = harness([list([record()])]); await h.model.start(); assert.equal(h.model.status().phase, 'ready'); assert.deepEqual(h.model.status().items, [record()]);
  assert.equal(h.model.status().receipt, null); assert.equal(h.model.status().canSave, false); assert.equal(h.calls.length, 1); await h.model.start(); assert.equal(h.calls.length, 1); h.model.dispose();
});

test('unknown POST never repeats or allows replacement; explicit receipt404 stays unknown, later exact readback resolves it', async () => {
  const h = harness([error(504, 'upload_result_unknown'), error(404, 'file_not_found'), receipt(), list([record()])]);
  h.model.select(file()); assert.equal(await h.model.save(), false); assert.equal(h.model.status().unknown, true);
  assert.equal(h.model.select(file()), false); assert.equal(await h.model.save(), false); assert.equal(await h.model.reload(), false); assert.equal(h.calls.length, 1);
  assert.equal(await h.model.check(), false); assert.equal(h.model.status().unknown, true); assert.equal(h.calls.length, 2);
  assert.equal(await h.model.check(), true); assert.equal(h.model.status().phase, 'saved'); assert.equal(h.calls.filter(([, o]) => o.method === 'POST').length, 1); h.model.dispose();
});

test('strict nine-field metadata, wrapper, project/request identity, bytes/hash/name and list bounds reject forged success', async () => {
  const invalid = [{...record(), owner: a}, {...record(), project_id: b}, {...record(), request_id: 'BAD'}, {...record(), bytes: 0}, {...record(), bytes: 26214401}, {...record(), sha256: hash.toUpperCase()}, {...record(), status: 'parsed'}, {...record(), filename: '../x.txt'}, {...record(), filename: 'x\0.txt'}, {...record(), createdAt: '2026-02-30T00:00:00Z'}];
  for (const value of invalid) assert.equal(isRawFile(value, a), false);
  for (const value of [{items: [record(), record()], nextCursor: null}, {items: [], nextCursor: 'cursor'}, {items: [record(b)], nextCursor: null}, {items: [], nextCursor: null, extra: true}]) assert.equal(isRawList(value, a), false);
  for (const first of [() => Response.json({file: record(), extra: true}, {status: 201}), receipt(record(b), 201), receipt(record(a, {request_id: b}), 201), receipt(record(a, {sha256: '0'.repeat(64)}), 201)]) {
    const h = harness([first]); h.model.select(file()); assert.equal(await h.model.save(), false); assert.equal(h.model.status().unknown, true); assert.equal(h.calls.length, 1); h.model.dispose();
  }
});

test('single TXT byte/name bounds preserve Unicode and reject invalid names, empty/oversize files without requests', () => {
  const h = harness();
  for (const value of [null, new File([], 'x.txt'), new File(['x'], 'x.fasta'), {name: 'x.txt', size: 26214401, arrayBuffer() {}}, {name: 'x.txt', size: 1.5, arrayBuffer() {}}, {name: '\uD800.txt', size: 1, arrayBuffer() {}}, new File(['x'], 'x\u0085.txt')]) assert.equal(h.model.select(value), false);
  assert.equal(txtFilenameHeader(' 原样.txt'), Buffer.from(' 原样.txt').toString('base64url')); assert.equal(h.calls.length, 0); h.model.dispose();
});

test('gateoff and unavailable list show unavailable, never a fabricated empty success or saved receipt', async () => {
  for (const first of [error(503, 'uploads_unavailable'), () => Response.json({status: 'prototype'}), () => new Response('no', {status: 404})]) {
    const h = harness([first]); await h.model.start(); assert.equal(h.model.status().phase, 'unavailable'); assert.equal(h.model.status().canChoose, false); assert.equal(h.model.status().canSave, false); assert.deepEqual(h.model.status().items, []); h.model.dispose();
  }
  const disabled = createPrivateTxtController({projectId: a}); await disabled.start(); assert.equal(disabled.status().phase, 'unavailable'); disabled.dispose();
});

test('known rejection remains failed, while malformed errors or uncertain503 never enable an automatic write retry', async () => {
  for (const [status, code] of [[400, 'upload_checksum_mismatch'], [409, 'upload_request_conflict'], [413, 'upload_quota_exceeded'], [415, 'unsupported_media_type'], [429, 'upload_busy']]) {
    const h = harness([error(status, code)]); h.model.select(file()); await h.model.save(); assert.equal(h.model.status().phase, 'failed'); assert.equal(h.model.status().code, code); assert.equal(h.calls.length, 1); h.model.dispose();
  }
  for (const response of [error(503, 'uploads_unavailable'), () => Response.json({error: {code: 'private-data'}}, {status: 400})]) {
    const h = harness([response]); h.model.select(file()); await h.model.save(); assert.equal(h.model.status().unknown, true); assert.equal(JSON.stringify(h.model.status()).includes('private-data'), false); assert.equal(h.calls.length, 1); h.model.dispose();
  }
});

test('all auth/context failures clear raw state, even malformed401/409; no old receipt appears after session loss', async () => {
  for (const response of [error(401, 'unauthenticated'), error(403, 'auth_origin_rejected'), error(409, 'project_context_changed'), () => new Response('malformed', {status: 401}), () => Response.json({private: true}, {status: 409})]) {
    const h = harness([response]); h.model.select(file()); await h.model.save(); assert.equal(h.model.status().phase, 'disposed'); assert.equal(h.model.status().chosen, null); assert.deepEqual(h.model.status().items, []); assert.equal(h.model.status().receipt, null); assert.equal(h.denials.length, 1); h.model.dispose();
  }
});

test('project/account binding loss or route disposal during POST aborts and ignores late results', async () => {
  for (const action of ['dispose', 'binding']) {
    const late = deferred(), h = harness([() => late.promise]); h.model.select(file()); const pending = h.model.save();
    for (let i = 0; !h.calls.length && i < 20; i++) await tick(); assert.equal(h.calls.length, 1);
    if (action === 'dispose') h.model.dispose(); else h.loseBinding();
    late.resolve(Response.json({file: record()}, {status: 201})); await pending;
    assert.equal(h.model.status().phase, 'disposed'); assert.equal(h.model.status().chosen, null); assert.deepEqual(h.model.status().items, []); assert.equal(h.calls.length, 1); assert.equal(h.timers.size, 0);
    if (action === 'dispose') assert.equal(h.calls[0][1].signal.aborted, true);
  }
});

test('60s upload deadline includes hashing/body, metadata waits retain15s cap, late results cannot save or rePOST', {timeout: 2000}, async () => {
  const late = deferred(), dispatched = deferred(), h = harness([() => { dispatched.resolve(); return late.promise; }]); h.model.select(file()); const pending = h.model.save();
  await Promise.race([dispatched.promise, pending.then(() => assert.fail('Save ended before the synthetic POST was dispatched'))]);
  assert.equal(h.calls.length, 1); assert.equal(h.model.status().phase, 'saving'); h.expire(); await pending;
  assert.equal(h.model.status().unknown, true); assert.equal(h.calls[0][1].signal.aborted, true);
  late.resolve(Response.json({file: record()}, {status: 201})); await tick(); assert.equal(h.calls.length, 1); assert.equal(await h.model.save(), false); h.model.dispose();
  const hang = harness([() => new Response(new ReadableStream(), {headers: {'content-type': 'application/json'}})]); const load = hang.model.start(); await tick(); hang.expire(); await load;
  assert.equal(hang.model.status().phase, 'unavailable'); assert.equal(hang.timers.size, 0); hang.model.dispose();
});

test('content route is scoped GET with context; original bytes/hash verification rejects corruption and length mismatch (no download UI)', async () => {
  const h = harness([() => new Response(raw, {headers: {'content-type': 'application/octet-stream'}})]);
  const response = await h.transport.content(fid, {signal: new AbortController().signal});
  assert.equal(h.calls[0][0], '/api/v1/projects/' + a + '/files/' + fid + '/content'); assert.equal(h.calls[0][1].method, 'GET');
  assert.equal(await verifyRawContent(await response.arrayBuffer(), record(), webcrypto), true);
  const corrupt = raw.slice(); corrupt[0] ^= 1; assert.equal(await verifyRawContent(corrupt.buffer, record(), webcrypto), false); assert.equal(await verifyRawContent(raw.slice(1).buffer, record(), webcrypto), false);
  assert.throws(() => h.transport.content('../other', {signal: new AbortController().signal})); h.model.dispose();
});

test('monotonic entry deadline refuses late success even if the timer callback has not fired', async () => {
  const h = harness([() => { h.advance(60000); return receipt(record(), 201)(); }]);
  h.model.select(file()); assert.equal(await h.model.save(), false); assert.equal(h.model.status().unknown, true); assert.equal(h.calls.length, 1); assert.equal(h.timers.size, 0); h.model.dispose();
  const read = harness([() => { read.advance(15000); return list([record()])(); }]);
  await read.model.start(); assert.equal(read.model.status().phase, 'unavailable'); assert.deepEqual(read.model.status().items, []); read.model.dispose();
});

const project = id => ({project_id: id, name: 'Mock project', description: '', species: '', visibility: 'private', createdAt: '2026-11-16T00:00:00Z', updatedAt: '2026-11-16T00:00:00Z'});
test('existing me/context and confirmed UUID alone bind the child; switching projects or identities disposes old selected File/list', async () => {
  const calls = [], responses = [() => Response.json({status: 'authenticated', user: {email: 'a@example.org', emailVerified: true}}, {headers: {'X-Cyomics-Account-Context': 'A'.repeat(22)}}), () => Response.json({items: [project(a), project(b)], nextCursor: null}), () => Response.json({project: project(a)}), list([record()]), () => Response.json({project: project(b)}), list([])];
  const transport = createPrivateProjectTransport({enabled: true, fetchImpl(url, options) { calls.push([url, options]); assert.ok(responses.length); return responses.shift()(); }});
  const model = createPrivateProjectController({transport}); assert.equal(model.files(), null); await model.start(); await model.select(a); const old = model.files(); await old.start(); old.select(file());
  await model.select(b); assert.equal(old.status().phase, 'disposed'); assert.deepEqual(old.status().items, []); assert.equal(old.status().chosen, null); assert.notEqual(model.files(), old); await model.files().start();
  assert.deepEqual(model.files().status().items, []); assert.ok(calls.every(([, o]) => o.headers?.Authorization === undefined)); const fresh = model.files(); model.dispose(); assert.equal(fresh.status().phase, 'disposed'); assert.equal(transport.hasContext(), false);
});

function dom() {
  const node = tag => ({tag, children: [], listeners: new Map(), textContent: '', value: '', disabled: false, hidden: false, attrs: {}, append(...x) { this.children.push(...x); }, replaceChildren(...x) { this.children = x; }, setAttribute(k,v) { this.attrs[k] = v; }, addEventListener(k,fn) { this.listeners.set(k,fn); }, removeEventListener(k,fn) { if (this.listeners.get(k) === fn) this.listeners.delete(k); }});
  const doc = {createElement: tag => Object.assign(node(tag), {ownerDocument: doc})}, root = node('root'); root.ownerDocument = doc;
  const all = n => [n, ...n.children.flatMap(all)]; return {root, all: () => all(root)};
}
test('one picker, explicit save, safe server-only list and language remount preserve pending state without duplicate requests', async () => {
  const h = harness([list([]), receipt(record(), 201), receipt(), list([record()])]), d = dom(); const stop = mountPrivateTxt(d.root, h.model);
  await tick(); assert.equal(h.calls.length, 1); assert.equal(d.all().filter(n => n.tag === 'input').length, 1);
  const input = d.all().find(n => n.tag === 'input'); input.files = [file()]; input.listeners.get('change')(); assert.equal(h.calls.length, 1); assert.equal(input.value, '');
  stop(); assert.equal(input.listeners.size, 0); const en = dom(), end = mountPrivateTxt(en.root, h.model, (_, english) => english); assert.equal(h.calls.length, 1);
  const save = en.all().find(n => n.tag === 'button' && n.textContent === 'Save to this project'); save.listeners.get('click')();
  for (let i = 0; h.model.status().busy && i < 30; i++) await tick();
  assert.equal(h.model.status().phase, 'saved'); assert.equal(h.calls.length, 4); assert.ok(en.all().some(n => n.textContent === 'SHA256: ' + record().sha256));
  end(); h.model.dispose(); assert.deepEqual(en.root.children, []);
});

test('unavailable UI does not infer persistence and stored row keeps file_id inside collapsed details in both languages', async () => {
  for (const en of [false, true]) {
    const h = harness([error(503, 'uploads_unavailable')]), d = dom(), stop = mountPrivateTxt(d.root, h.model, en ? (_, english) => english : zh => zh);
    await tick();
    const message = d.all().find(n => n.attrs.role === 'status').textContent;
    assert.match(message, en ? /Server file status cannot be confirmed right now/ : /暂时无法确认服务器文件状态/);
    assert.doesNotMatch(message, /未保存文件|No file saved/); stop(); h.model.dispose();
    const stored = harness([list([record()])]), shown = dom(), end = mountPrivateTxt(shown.root, stored.model, en ? (_, english) => english : zh => zh);
    await tick();
    const row = shown.all().find(n => n.className === 'list-row'), info = row.children[0], details = info.children.find(n => n.tag === 'details');
    assert.equal(details.open, undefined); assert.equal(info.children.some(n => n.textContent.includes(fid)), false);
    assert.ok(details.children.some(n => n.textContent === 'file_id: ' + fid)); assert.ok(details.children.some(n => n.textContent === 'SHA256: ' + hash));
    assert.equal(info.children[0].textContent, record().filename); assert.equal(info.children[1].textContent, raw.length + ' bytes · ' + record().createdAt);
    end(); stored.model.dispose();
  }
});

const binaryHeaders = () => ({'Content-Type': 'application/octet-stream', 'Content-Length': String(raw.length), 'X-Cyomics-File-Sha256': hash,
  'Content-Disposition': 'attachment; filename="untrusted-name.txt"', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'});
const binary = (body = raw, overrides = {}) => () => new Response(body, {headers: {...binaryHeaders(), ...overrides}});
function downloadHarness(replies = [], projectId = a, fixtureContext = 'A'.repeat(22)) {
  let time = 0, serial = 0;
  const hooks = {}, timers = new Map(), created = [], revoked = [], clicks = [], anchors = [], blobs = [];
  const documentImpl = {body: {append(anchor) { anchors.push(anchor); hooks.append?.(); }}, createElement(tag) {
    assert.equal(tag, 'a'); hooks.anchor?.();
    return {style: {}, href: '', download: '', removeAttribute(k) { this[k] = ''; }, remove() { const i = anchors.indexOf(this); if (i >= 0) anchors.splice(i, 1); },
      get click() { hooks.clickGetter?.(); return function () { clicks.push({href: this.href, filename: this.download}); hooks.click?.(); }; }};
  }};
  const urlImpl = {createObjectURL(blob) { const value = 'blob:offline-' + (created.length + 1); created.push(value); blobs.push(blob); hooks.url?.(); return value; }, revokeObjectURL(value) { revoked.push(value); hooks.revoke?.(); }};
  class DownloadBlob extends Blob { constructor(parts, options) { super(parts, options); hooks.blob?.(); } }
  const h = harness(replies, projectId, {documentImpl, urlImpl, BlobImpl: DownloadBlob, now: () => time, fixtureContext,
    cryptoImpl: {randomUUID: () => rid, subtle: {digest(...args) { return hooks.digest ? hooks.digest(...args) : webcrypto.subtle.digest(...args); }}},
    setTimer(fn, ms) { assert.ok(Number.isInteger(ms) && ms > 0 && ms <= 60000); timers.set(++serial, {fn, ms}); return serial; }, clearTimer(id) { timers.delete(id); }});
  return {...h, hooks, timers, created, revoked, clicks, anchors, blobs,
    advance(ms) { time += ms; }, expire() { for (const [id, value] of [...timers]) { timers.delete(id); value.fn(); } },
    flushURLs() { for (const [id, value] of [...timers]) if (value.ms === 1000) { timers.delete(id); value.fn(); } }};
}

test('explicit download makes exactly one authenticated GET, validates original bytes, uses receipt filename and revokes URL in a later task', async () => {
  const h = downloadHarness([list([record()]), binary()]);
  await h.model.start(); assert.equal(h.calls.length, 1); assert.equal(h.model.canDownload(fid), true);
  assert.equal(await h.model.download(fid), true); assert.equal(h.calls.length, 2);
  const [path, options] = h.calls[1]; assert.equal(path, '/api/v1/projects/' + a + '/files/' + fid + '/content'); assert.equal(options.method, 'GET');
  assert.equal(options.credentials, 'same-origin'); assert.equal(options.cache, 'no-store'); assert.equal(options.redirect, 'error'); assert.equal(options.body, undefined);
  assert.deepEqual(options.headers, {'X-Cyomics-Account-Context': 'A'.repeat(22)});
  assert.equal(h.created.length, 1); assert.deepEqual(h.revoked, []); assert.deepEqual(h.clicks, [{href: h.created[0], filename: record().filename}]);
  assert.deepEqual(new Uint8Array(await h.blobs[0].arrayBuffer()), raw); assert.equal(h.model.status().download.phase, 'handed_off');
  assert.deepEqual([...h.timers.values()].map(x => x.ms), [1000]); h.flushURLs(); assert.deepEqual(h.revoked, h.created); assert.equal(h.anchors.length, 0); assert.equal(h.timers.size, 0);
  h.model.dispose(); assert.equal(h.revoked.length, 1);
});

test('listing, refresh and selection never fetch content; disabled direct download cannot bypass unknown/unavailable/busy/disposed or receipt membership', async () => {
  const h = downloadHarness([list([record()]), list([record()]), error(504, 'upload_result_unknown')]);
  assert.equal(await h.model.download(fid), false); assert.equal(h.calls.length, 0);
  await h.model.start(); await h.model.reload(); assert.equal(h.calls.length, 2); assert.equal(await h.model.download(b), false); assert.equal(await h.model.download(record()), false);
  h.model.select(file()); assert.equal(h.calls.length, 2); await h.model.save(); assert.equal(h.model.status().unknown, true);
  assert.equal(h.model.canDownload(fid), false); assert.equal(await h.model.download(fid), false); assert.equal(h.calls.length, 3); h.model.dispose();
  assert.equal(await h.model.download(fid), false); assert.equal(h.calls.length, 3);
  const gateoff = downloadHarness([error(503, 'uploads_unavailable')]); await gateoff.model.start(); assert.equal(await gateoff.model.download(fid), false);
  // Keep the previously recorded select boundary unchanged, but never inherit it in download.
  assert.equal(gateoff.model.select(file()), true); assert.equal(gateoff.model.canDownload(fid), false); assert.equal(await gateoff.model.download(fid), false); assert.equal(gateoff.calls.length, 1); gateoff.model.dispose();
  const late = deferred(), busy = downloadHarness([() => late.promise]); const task = busy.model.start();
  assert.equal(await busy.model.download(fid), false); assert.equal(busy.calls.length, 1); late.resolve(list([record()])()); await task; busy.model.dispose();
  const identity = downloadHarness([list([record()])]); await identity.model.start(); identity.loseBinding();
  assert.equal(await identity.model.download(fid), false); assert.equal(identity.calls.length, 1); assert.equal(identity.model.status().phase, 'disposed');
});

test('double click and another programmatic download during a pending request never send a second GET or POST', async () => {
  const late = deferred(), h = downloadHarness([list([record()]), () => late.promise]); await h.model.start();
  const task = h.model.download(fid); assert.equal(h.model.canDownload(fid), false); assert.equal(await h.model.download(fid), false); assert.equal(h.calls.length, 2);
  assert.equal(h.model.select(file()), false); assert.equal(await h.model.save(), false); assert.equal(await h.model.reload(), false);
  late.resolve(binary()()); assert.equal(await task, true); assert.equal(h.calls.length, 2); assert.equal(h.calls.some(([, o]) => o.method === 'POST'), false); h.model.dispose();
});

test('all existing binary header requirements are strict; no invalid header reaches Blob/URL/click', async () => {
  for (const [key, value] of [['Content-Type', 'text/plain'], ['Content-Type', 'application/octet-stream; charset=utf-8'], ['Content-Length', '0'], ['Content-Length', '0' + raw.length],
    ['Content-Length', String(raw.length + 1)], ['Content-Length', raw.length + ', ' + raw.length], ['X-Cyomics-File-Sha256', hash.toUpperCase()], ['X-Cyomics-File-Sha256', '0'.repeat(64)],
    ['Cache-Control', 'public'], ['X-Content-Type-Options', ''], ['Referrer-Policy', 'origin'], ['Content-Disposition', 'inline; filename="x.txt"']]) {
    const h = downloadHarness([list([record()]), binary(raw, {[key]: value})]); await h.model.start(); assert.equal(await h.model.download(fid), false, key);
    assert.equal(h.calls.length, 2); assert.equal(h.created.length, 0); assert.equal(h.clicks.length, 0); assert.equal(h.timers.size, 0); h.model.dispose();
  }
  const malformed = {status: 200, redirected: false, headers: {get(key) { return key === 'Content-Disposition' ? 'attachment;\r\nprivate' : binaryHeaders()[key]; }}, body: {getReader() { assert.fail('invalid attachment must not read'); }, cancel() { return Promise.resolve(); }}};
  const h = downloadHarness([list([record()]), () => malformed]); await h.model.start(); assert.equal(await h.model.download(fid), false); assert.equal(h.created.length, 0); h.model.dispose();
  for (const key of Object.keys(binaryHeaders())) {
    const headers = binaryHeaders(); delete headers[key];
    const missing = downloadHarness([list([record()]), () => new Response(raw, {headers})]); await missing.model.start();
    assert.equal(await missing.model.download(fid), false, key); assert.equal(missing.created.length, 0); assert.equal(missing.clicks.length, 0); missing.model.dispose();
  }
});

test('short, overlong or checksum-corrupt streams never create a Blob URL or trigger a download', async () => {
  const corrupt = raw.slice(); corrupt[0] ^= 1;
  for (const bytes of [raw.slice(1), new Uint8Array([...raw, 1]), corrupt]) {
    let cancelled = 0;
    const h = downloadHarness([list([record()]), () => ({status: 200, redirected: false, headers: new Headers(binaryHeaders()), body: {getReader() {
      let sent = false; return {read() { if (sent) return Promise.resolve({done: true}); sent = true; return Promise.resolve({done: false, value: bytes}); }, cancel() { cancelled++; return Promise.resolve(); }, releaseLock() {}};
    }, cancel() { cancelled++; return Promise.resolve(); }}})]);
    await h.model.start(); assert.equal(await h.model.download(fid), false); assert.equal(h.created.length, 0); assert.equal(h.clicks.length, 0); assert.ok(cancelled >= 1); assert.equal(h.timers.size, 0); h.model.dispose();
  }
});

test('binary HTTP failures and redirects never retry/read unsafe body; auth/context failures dispose private state', async () => {
  for (const status of [401, 403, 404, 409, 429, 503, 504, 302, 206]) {
    let reads = 0, cancelled = 0;
    const response = {status, redirected: false, body: {getReader() { reads++; throw Error('must not read'); }, cancel() { cancelled++; return Promise.resolve(); }}};
    const h = downloadHarness([list([record()]), () => response]); await h.model.start(); assert.equal(await h.model.download(fid), false);
    assert.equal(reads, 0); assert.equal(h.calls.length, 2); assert.equal(h.created.length, 0); assert.ok(cancelled >= 1);
    if ([401, 403, 409].includes(status)) { assert.equal(h.model.status().phase, 'disposed'); assert.deepEqual(h.model.status().items, []); }
    h.model.dispose();
  }
  const h = downloadHarness([list([record()]), () => ({status: 200, redirected: true, body: {cancel() { return Promise.resolve(); }}})]); await h.model.start(); assert.equal(await h.model.download(fid), false); h.model.dispose();
});

test('emit listeners disposing or switching identity fence before GET, verification and browser handoff', async () => {
  for (const phase of ['reading', 'verifying', 'handoff', 'handed_off']) {
    const h = downloadHarness([list([record()]), binary()]); await h.model.start();
    h.model.subscribe(() => { if (h.model.status().download.phase === phase) h.model.dispose(); });
    assert.equal(await h.model.download(fid), false); assert.equal(h.calls.length, phase === 'reading' ? 1 : 2);
    assert.equal(h.clicks.length, phase === 'handed_off' ? 1 : 0); assert.deepEqual(h.revoked, h.created); assert.equal(h.timers.size, 0);
    assert.equal(h.model.status().download.phase, 'idle'); assert.deepEqual(h.model.status().items, []);
  }
  const identity = downloadHarness([list([record()]), binary()]); await identity.model.start();
  identity.model.subscribe(() => { if (identity.model.status().download.phase === 'verifying') identity.loseBinding(); });
  assert.equal(await identity.model.download(fid), false); assert.equal(identity.created.length, 0); assert.equal(identity.model.status().phase, 'disposed');
});

test('synchronous Blob/URL/anchor/append/click-getter reentry cannot slip past the final click fence or leak a URL', async () => {
  for (const stage of ['blob', 'url', 'anchor', 'append', 'clickGetter']) {
    const h = downloadHarness([list([record()]), binary()]); await h.model.start(); h.hooks[stage] = () => h.model.dispose();
    assert.equal(await h.model.download(fid), false, stage); assert.equal(h.clicks.length, 0, stage); assert.deepEqual(h.revoked, h.created, stage); assert.equal(h.anchors.length, 0, stage); assert.equal(h.timers.size, 0, stage);
  }
});

test('60 second content budget covers late fetch, reading and SHA; monotonic expiry cannot produce a handoff', async () => {
  const late = deferred(), h = downloadHarness([list([record()]), () => late.promise]); await h.model.start(); const task = h.model.download(fid);
  assert.deepEqual([...h.timers.values()].map(x => x.ms), [60000]); h.advance(60000); h.expire(); assert.equal(await task, false);
  let cancelled = 0; late.resolve({body: {cancel() { cancelled++; return Promise.resolve(); }}}); await tick(); assert.equal(cancelled, 1); assert.equal(h.created.length, 0); assert.equal(h.calls.length, 2); h.model.dispose();
  const body = downloadHarness([list([record()]), () => new Response(new ReadableStream(), {headers: binaryHeaders()})]); await body.model.start(); const waiting = body.model.download(fid); await tick(); body.expire(); assert.equal(await waiting, false); assert.equal(body.timers.size, 0); assert.equal(body.created.length, 0); body.model.dispose();
  const digest = deferred(), sha = downloadHarness([list([record()]), binary()]); await sha.model.start(); sha.hooks.digest = () => digest.promise; const hashing = sha.model.download(fid); await tick(); sha.advance(60000); sha.expire(); assert.equal(await hashing, false);
  digest.resolve(await webcrypto.subtle.digest('SHA-256', raw)); await tick(); assert.equal(sha.created.length, 0); assert.equal(sha.timers.size, 0); sha.model.dispose();
  const mono = downloadHarness([list([record()]), binary()]); await mono.model.start(); mono.hooks.url = () => mono.advance(60000);
  assert.equal(await mono.model.download(fid), false); assert.equal(mono.clicks.length, 0); assert.deepEqual(mono.revoked, mono.created); mono.model.dispose();
});

test('pending body is cancelled on project switch/route disposal; a new project and receipt cannot inherit an old download', async () => {
  let cancelled = 0;
  const old = downloadHarness([list([record()]), () => new Response(new ReadableStream({cancel() { cancelled++; }}), {headers: binaryHeaders()})]); await old.model.start();
  const task = old.model.download(fid); await tick(); old.model.dispose(); assert.equal(await task, false); assert.ok(cancelled >= 1); assert.equal(old.calls[1][1].signal.aborted, true); assert.equal(old.created.length, 0);
  const nextId = '55555555-5555-4555-8555-555555555555', nextReceipt = record(b, {file_id: nextId});
  const next = downloadHarness([list([nextReceipt]), binary()], b, 'B'.repeat(22)); await next.model.start();
  assert.equal(await next.model.download(fid), false); assert.equal(next.calls.length, 1); assert.equal(await next.model.download(nextId), true);
  assert.equal(next.calls[1][0], '/api/v1/projects/' + b + '/files/' + nextId + '/content');
  assert.equal(old.calls[1][1].headers['X-Cyomics-Account-Context'], 'A'.repeat(22)); assert.equal(next.calls[1][1].headers['X-Cyomics-Account-Context'], 'B'.repeat(22));
  assert.equal(old.calls.length, 2); assert.equal(old.clicks.length, 0); next.model.dispose();
});

test('view unmount cancels an active download and revokes a successful URL; disabled DOM handlers still share controller permission', async () => {
  const late = deferred(), h = downloadHarness([list([record()]), () => late.promise]), d = dom(); const stop = mountPrivateTxt(d.root, h.model); await tick();
  const button = d.all().find(n => n.tag === 'button' && n.textContent === '下载原始 TXT'); assert.equal(button.disabled, false);
  const click = button.listeners.get('click'); click(); click(); assert.equal(h.calls.length, 2); stop(); assert.equal(button.listeners.size, 0);
  late.resolve(binary()()); await tick(); assert.equal(h.clicks.length, 0); assert.equal(h.created.length, 0); assert.equal(h.timers.size, 0); h.model.dispose();
  const complete = downloadHarness([list([record()]), binary()]), shown = dom(), end = mountPrivateTxt(shown.root, complete.model); await tick();
  await complete.model.download(fid); assert.ok(shown.all().some(n => n.textContent === '已校验并交给浏览器保存。')); assert.deepEqual(complete.revoked, []); end(); assert.deepEqual(complete.revoked, complete.created); assert.equal(complete.anchors.length, 0); complete.model.dispose();
  const blocked = downloadHarness([list([record()]), error(503, 'uploads_unavailable')]), ui = dom(), finish = mountPrivateTxt(ui.root, blocked.model); await tick();
  const oldButton = ui.all().find(n => n.tag === 'button' && n.textContent === '下载原始 TXT'), oldHandler = oldButton.listeners.get('click');
  await blocked.model.reload(); oldHandler(); assert.equal(blocked.calls.length, 2); assert.equal(await blocked.model.download(fid), false); finish(); blocked.model.dispose();
});

test('R1 completed handoff lease rejects a replacement-button detail1 click and direct call, then expiry re-enables one independent download', async () => {
  const h = downloadHarness([list([record()]), binary(), binary()]), view = dom(), stop = mountPrivateTxt(view.root, h.model);
  const button = () => view.all().find(n => n.tag === 'button' && n.textContent === '下载原始 TXT');
  const settled = async () => { for (let i = 0; i < 100; i++) { await tick(); if (!h.model.status().busy) return; } assert.fail('Synthetic operation did not settle'); };
  await settled(); const first = button(); assert.equal(first.disabled, false);
  first.listeners.get('click')({detail: 1}); await settled();
  assert.equal(h.model.status().download.phase, 'handed_off'); assert.equal(h.model.status().busy, false);
  assert.deepEqual([...h.timers.values()].map(x => x.ms), [1000]);
  const replacement = button(); assert.notEqual(replacement, first); assert.equal(replacement.disabled, true); assert.equal(h.model.canDownload(fid), false);
  replacement.listeners.get('click')({detail: 1}); assert.equal(await h.model.download(fid), false); await settled();
  assert.equal(h.calls.filter(([path]) => path.endsWith('/content')).length, 1); assert.equal(h.clicks.length, 1); assert.equal(h.created.length, 1); assert.deepEqual(h.revoked, []);
  h.advance(1000); h.flushURLs();
  const enabled = button(); assert.notEqual(enabled, replacement); assert.equal(enabled.disabled, false); assert.equal(h.model.canDownload(fid), true);
  assert.deepEqual(h.revoked, h.created); assert.equal(h.timers.size, 0); assert.equal(h.calls.filter(([path]) => path.endsWith('/content')).length, 1);
  enabled.listeners.get('click')({detail: 1}); await settled();
  assert.equal(h.calls.filter(([path]) => path.endsWith('/content')).length, 2); assert.equal(h.clicks.length, 2); assert.equal(h.created.length, 2);
  assert.equal(h.calls.some(([, options]) => options.method === 'POST'), false); assert.equal(button().disabled, true);
  stop(); h.model.dispose(); assert.deepEqual(h.revoked, h.created); assert.equal(h.anchors.length, 0); assert.equal(h.timers.size, 0);
});

test('R1 expiry never emits a stale repaint after identity loss/disposal/cancellation, and all URL cleanup stays idempotent', async () => {
  for (const action of ['identity-during-revoke', 'dispose-before-expiry', 'cancel-before-expiry', 'dispose-during-revoke']) {
    const h = downloadHarness([list([record()]), binary()]), view = dom(), stop = mountPrivateTxt(view.root, h.model); await tick();
    assert.equal(await h.model.download(fid), true); const expiry = [...h.timers.values()].find(x => x.ms === 1000).fn;
    let emits = 0; const unsubscribe = h.model.subscribe(() => { emits++; });
    if (action === 'identity-during-revoke') h.hooks.revoke = () => h.loseBinding();
    if (action === 'dispose-during-revoke') h.hooks.revoke = () => h.model.dispose();
    if (action === 'dispose-before-expiry') h.model.dispose();
    if (action === 'cancel-before-expiry') h.model.cancelDownload();
    const before = emits;
    if (action.endsWith('during-revoke')) h.flushURLs(); else expiry();
    assert.equal(emits, before + (action === 'dispose-during-revoke' ? 1 : 0), action);
    assert.deepEqual(h.revoked, h.created, action); assert.equal(h.created.length, 1); assert.equal(h.anchors.length, 0); assert.equal(h.timers.size, 0);
    assert.equal(h.calls.filter(([path]) => path.endsWith('/content')).length, 1); assert.equal(h.clicks.length, 1);
    if (action.startsWith('dispose')) assert.equal(view.all().some(n => n.className === 'list-row'), false);
    unsubscribe(); stop(); h.model.dispose(); assert.equal(h.revoked.length, 1);
  }
});

test('R2 upload expiry while SHA is pending never sends POST or becomes an unknown committed write', {timeout: 2000}, async () => {
  const started = deferred(), digest = deferred();
  const h = harness([], a, {cryptoImpl: {randomUUID: () => rid, subtle: {digest() { started.resolve(); return digest.promise; }}}});
  h.model.select(file()); const pending = h.model.save(); await started.promise;
  for (let i = 0; i < 20; i++) await tick();
  assert.equal(h.calls.length, 0); assert.equal(h.model.status().phase, 'hashing'); h.expire(); await pending;
  assert.equal(h.model.status().unknown, false); assert.equal(h.model.status().phase, 'failed'); assert.equal(h.calls.length, 0); assert.equal(h.timers.size, 0);
  digest.resolve(await webcrypto.subtle.digest('SHA-256', raw)); await tick();
  assert.equal(h.calls.length, 0); assert.equal(h.model.status().unknown, false); h.model.dispose();
});

// New import-route accounting; raw regression assertions above remain unchanged.
test('report restore has a separate single read-only route and does not consume raw fixtures', async()=>{
 const h=harness([list([record()])]),d=dom(),stop=mountPrivateTxt(d.root,h.model);await tick();
 assert.equal(h.calls.length,1);assert.equal(h.importCalls.length,1);assert.equal(h.importCalls[0][0],'/api/v1/uploads/'+fid+'/complete?project_id='+a);
 stop();const e=dom(),end=mountPrivateTxt(e.root,h.model,(_,en)=>en);await tick();assert.equal(h.importCalls.length,1);end();h.model.dispose();
});

test('capacity synthetic selection accepts18.11MiB and exactly25MiB without reading bytes;25MiB+1 is a specific error',()=>{
  const h=harness(),cap=26214400;
  const selected=size=>({name:'capacity-synthetic.txt',size,arrayBuffer(){assert.fail('selection must not read bytes');}});
  for(const size of[18988424,cap]){
    assert.equal(h.model.select(selected(size)),true);assert.equal(h.model.status().chosen.bytes,size);assert.equal(h.model.status().selectionError,null);assert.equal(h.calls.length,0);
  }
  assert.equal(h.model.select(selected(cap+1)),false);assert.deepEqual(h.model.status().selectionError,{code:'too_large',actualBytes:cap+1,maxBytes:cap});
  const snapshot=h.model.status();snapshot.selectionError.actualBytes=1;assert.equal(h.model.status().selectionError.actualBytes,cap+1);
  assert.equal(h.model.status().canSave,false);assert.equal(h.calls.length,0);h.model.dispose();assert.equal(h.model.status().selectionError,null);
});

test('capacity invalid reasons and actual/allowed byte sizes are clear in Chinese and English and never issue an upload',async()=>{
  for(const en of[false,true]){
    const h=harness([list([])]),d=dom(),stop=mountPrivateTxt(d.root,h.model,en?(_,eng)=>eng:zh=>zh);await tick();
    const cases=[
      [new File([],'empty.txt'),'empty_file',0,en?'The file is empty.':'文件为空。'],
      [new File(['abc'],'wrong.csv'),'not_txt',3,en?'Only TXT files are supported.':'仅支持 TXT 文件。'],
      [{name:'large.txt',size:26214401,arrayBuffer(){}},'too_large',26214401,en?'The file exceeds the single-file size limit.':'文件超过单文件大小上限。'],
      [{name:'broken.txt',size:1.5,arrayBuffer(){}},'invalid_file',null,en?'The file cannot be read':'文件无法读取'],
    ];
    for(const[input,code,actual,reason]of cases){
      assert.equal(h.model.select(input),false);assert.equal(h.model.status().selectionError.code,code);
      const message=d.all().find(n=>n.attrs.role==='status').textContent;assert.ok(message.includes(reason));assert.ok(message.includes('26214400 bytes (25 MiB)'));
      assert.ok(message.includes(actual===null?(en?'Unknown':'未知'):actual+' bytes'));assert.ok(message.includes(en?'Actual size':'实际大小'));assert.ok(message.includes(en?'Allowed range':'允许范围'));assert.equal(h.calls.length,1);
    }
    assert.equal(h.model.select(file()),true);assert.equal(h.model.status().selectionError,null);assert.equal(d.all().find(n=>n.attrs.role==='status').textContent,'');
    assert.ok(d.all().some(n=>n.tag==='small'&&n.textContent.includes('25 MiB')));stop();h.model.dispose();
  }
});

test('capacity file metadata25MiB is valid but project32MiB and four-file caps remain unchanged',()=>{
  const large=record(a,{bytes:26214400}),other=record(a,{file_id:b,request_id:'66666666-6666-4666-8666-666666666666',bytes:7340032});
  assert.equal(isRawFile(large,a),true);assert.equal(isRawFile({...large,bytes:26214401},a),false);
  assert.equal(isRawList({items:[large,other],nextCursor:null},a),true);assert.equal(isRawList({items:[large,{...other,bytes:other.bytes+1}],nextCursor:null},a),false);
  assert.equal(isRawList({items:Array(5).fill(record()),nextCursor:null},a),false);
});

test('capacity synthetic25MiB download still requires exact original bytes/SHA and hands off once',async()=>{
  const bytes=new Uint8Array(26214400),digest=createHash('sha256').update(bytes).digest('hex'),stored=record(a,{bytes:bytes.length,sha256:digest});
  const h=downloadHarness([list([stored]),()=>new Response(bytes,{headers:{...binaryHeaders(),'Content-Length':String(bytes.length),'X-Cyomics-File-Sha256':digest}})]);
  await h.model.start();assert.equal(await h.model.download(fid),true);assert.equal(h.calls.length,2);assert.equal(h.clicks.length,1);assert.equal(h.blobs[0].size,bytes.length);
  assert.equal(createHash('sha256').update(new Uint8Array(await h.blobs[0].arrayBuffer())).digest('hex'),digest);h.flushURLs();assert.deepEqual(h.revoked,h.created);h.model.dispose();
});
