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
function harness(replies = [], projectId = a) {
  const calls = [], timers = new Map(), denials = []; let active = true, serial = 0, randomCalls = 0, clockNow = 0;
  const transport = createRawTransport({projectId, fetchImpl(path, options) { calls.push([path, options]); assert.ok(replies.length, 'Unexpected synthetic raw request'); return replies.shift()(path, options); },
    getOptions(signal) { return {method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', headers: {'X-Cyomics-Account-Context': 'A'.repeat(22)}, signal}; }, isCurrent: () => active});
  const model = createPrivateTxtController({projectId, transport, onDenied(response) { denials.push(response.status); },
    cryptoImpl: {subtle: webcrypto.subtle, randomUUID() { randomCalls++; return rid; }}, now: () => clockNow,
    setTimer(fn, delay) { assert.ok([15000, 60000].includes(delay)); timers.set(++serial, fn); return serial; }, clearTimer(id) { timers.delete(id); }});
  return {calls, timers, denials, model, transport, randomCalls: () => randomCalls, advance(ms) { clockNow += ms; }, loseBinding() { active = false; }, expire() { for (const fn of [...timers.values()]) fn(); }};
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
  const invalid = [{...record(), owner: a}, {...record(), project_id: b}, {...record(), request_id: 'BAD'}, {...record(), bytes: 0}, {...record(), bytes: 10485761}, {...record(), sha256: hash.toUpperCase()}, {...record(), status: 'parsed'}, {...record(), filename: '../x.txt'}, {...record(), filename: 'x\0.txt'}, {...record(), createdAt: '2026-02-30T00:00:00Z'}];
  for (const value of invalid) assert.equal(isRawFile(value, a), false);
  for (const value of [{items: [record(), record()], nextCursor: null}, {items: [], nextCursor: 'cursor'}, {items: [record(b)], nextCursor: null}, {items: [], nextCursor: null, extra: true}]) assert.equal(isRawList(value, a), false);
  for (const first of [() => Response.json({file: record(), extra: true}, {status: 201}), receipt(record(b), 201), receipt(record(a, {request_id: b}), 201), receipt(record(a, {sha256: '0'.repeat(64)}), 201)]) {
    const h = harness([first]); h.model.select(file()); assert.equal(await h.model.save(), false); assert.equal(h.model.status().unknown, true); assert.equal(h.calls.length, 1); h.model.dispose();
  }
});

test('single TXT byte/name bounds preserve Unicode and reject invalid names, empty/oversize files without requests', () => {
  const h = harness();
  for (const value of [null, new File([], 'x.txt'), new File(['x'], 'x.fasta'), {name: 'x.txt', size: 10485761, arrayBuffer() {}}, {name: 'x.txt', size: 1.5, arrayBuffer() {}}, {name: '\uD800.txt', size: 1, arrayBuffer() {}}, new File(['x'], 'x\u0085.txt')]) assert.equal(h.model.select(value), false);
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

test('60s upload deadline includes hashing/body, metadata waits retain15s cap, late results cannot save or rePOST', async () => {
  const late = deferred(), h = harness([() => late.promise]); h.model.select(file()); const pending = h.model.save();
  for (let i = 0; !h.calls.length && i < 20; i++) await tick(); h.expire(); await pending;
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
  const doc = {createElement: node}, root = node('root'); root.ownerDocument = doc;
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
