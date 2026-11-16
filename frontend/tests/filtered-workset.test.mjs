import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {parseProteinGroups} from '../protein-groups.js';
import {createProteinFilterCollector, evaluateProteinFilter} from '../protein-filter-preview.js';
import {parseProteinGroupsForAssociation, releaseAssociationInputs} from '../protein-association.js';
import {createFilteredWorkset, mountFilteredWorkset, WORKSET_LIMITS} from '../filtered-workset.js';
import {mountImportWorkspace} from '../import-workspace.js';

const header = 'Protein IDs\tIntensity s\tLFQ intensity s\tReverse\tPotential contaminant\tOnly identified by site\n';
const small = header + 'P1;P11\t1\t\t+\t\t\nP2\t2\t3\t+\t\t+\nP3\t0\tNaN\t\t\t\nP4\t\t4\t\t\t\n';
const hash = value => createHash('sha256').update(value).digest('hex');
function source(text = small, fileName = 'same.txt') {
  const collector = createProteinFilterCollector();
  try {
    const preview = parseProteinGroups(text, 10, {onQualityFlagRow: collector.onQualityFlagRow});
    const association = parseProteinGroupsForAssociation(text);
    try { return {text, fileName, byteSize: new TextEncoder().encode(text).byteLength, preview, flags: collector.finish(preview)}; }
    finally { releaseAssociationInputs(association); }
  } finally { collector.abort(); }
}
const setMask = (model, src, selectedMask) => model.updateRules({summary: src.flags, selectedMask});
const immediate = async () => {};
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; }

test('generation gates, frozen shared rows, exact raw values and genuine empty worksets preserve the original source', async () => {
  const model = createFilteredWorkset({yieldTask: immediate}); assert.equal(model.status().canGenerate, false);
  const src = source(), before = JSON.stringify(src.preview); model.acceptSource(src);
  assert.equal(model.status().snapshot, null); setMask(model, src, 7); assert.equal(await model.generate(), true);
  const snapshot = model.status().snapshot;
  assert.deepEqual(snapshot.keptRecords.map(r => r.recordNumber), [3, 4]);
  assert.ok(Object.isFrozen(snapshot) && Object.isFrozen(snapshot.headers) && Object.isFrozen(snapshot.originalRecords));
  assert.ok(snapshot.originalRecords.every(r => Object.isFrozen(r) && Object.isFrozen(r.cells)));
  assert.ok(snapshot.keptRecords.every(r => r === snapshot.originalRecords[r.recordNumber - 1]));
  assert.deepEqual(snapshot.originalRecords.map(r => r.cells.slice(0, 3)), [['P1;P11', '1', ''], ['P2', '2', '3'], ['P3', '0', 'NaN'], ['P4', '', '4']]);
  assert.equal(Reflect.set(snapshot.keptRecords[0].cells, '0', 'changed'), false);
  assert.equal(JSON.stringify(src.preview), before); assert.equal(Object.hasOwn(snapshot, 'text'), false);
  const uncertain = source(header + 'P1\t1\t0\t?\t\t\n'); model.acceptSource(uncertain); setMask(model, uncertain, 1);
  assert.equal(model.status().blocked, 'PENDING_FLAGS'); assert.equal(await model.generate(), false);
  const missing = source('Protein IDs\tIntensity s\nP1\t0\n'); model.acceptSource(missing); setMask(model, missing, 1);
  assert.equal(model.status().blocked, 'RULE_UNAVAILABLE'); assert.equal(await model.generate(), false);
  const excessive = {...src, preview: {...src.preview, rowCount: 400000}, flags: {...src.flags, recordCount: 400000, stateBins: Object.freeze([400000, ...Array(26).fill(0)])}};
  model.acceptSource(excessive); assert.equal(model.status().blocked, 'CELL_LIMIT'); assert.equal(await model.generate(), false);
  assert.throws(() => model.acceptSource({...src, byteSize: WORKSET_LIMITS.maxBytes + 1}), {code: 'WORKSET_SOURCE_INVALID'});
  const none = source(header + 'P1\t0\t\t+\t\t\nP2\t\tNaN\t+\t\t\n');
  model.acceptSource(none); setMask(model, none, 1); assert.equal(await model.generate(), true);
  assert.equal(model.getPage().total, 0); assert.equal(model.getPage().records.length, 0);
  model.setView('original'); assert.deepEqual(model.getPage().records.map(r => r.recordNumber), [1, 2]); model.reset();
  assert.equal(model.status().hasSource, false); assert.equal(model.status().snapshot, null);
});

test('stale rules, cancellation, reentry, failure and late source changes never overwrite a valid workset', async () => {
  let pause = false, fail = false; const gates = [];
  const model = createFilteredWorkset({yieldTask: () => { if (fail) throw Error('PRIVATE_SOURCE_TEXT'); if (pause) { const d = deferred(); gates.push(d); return d.promise; } return Promise.resolve(); }});
  const src = source(); model.acceptSource(src); assert.equal(await model.generate(), true); const first = model.status().snapshot;
  setMask(model, src, 7); assert.equal(model.status().stale, true); assert.equal(model.status().snapshot, first);
  setMask(model, src, 0); assert.equal(model.status().stale, false);
  fail = true; assert.equal(await model.generate(), false); assert.equal(model.status().error, 'GENERATION_FAILED'); assert.equal(model.status().snapshot, first); fail = false;
  pause = true; let running = model.generate(); assert.equal(model.status().busy, true); assert.equal(await model.generate(), false);
  model.cancel(); gates.shift().resolve(); assert.equal(await running, false); assert.equal(model.status().snapshot, first);
  running = model.generate(); setMask(model, src, 7); gates.shift().resolve(); assert.equal(await running, false); assert.equal(model.status().snapshot, first);
  running = model.generate(); const other = source(header + 'NEW\t8\t0\t\t\t\n'); model.acceptSource(other);
  pause = false; assert.equal(await model.generate(), true); const latest = model.status().snapshot;
  gates.shift().resolve(); assert.equal(await running, false); assert.equal(model.status().snapshot, latest);
  assert.equal(latest.originalRecords.length, 1); assert.equal(latest.originalRecords[0].cells[0], 'NEW'); model.reset();
});

// Small DOM port for component/controller events, not actual browser layout.
function node(doc, tag = 'div') {
  return {ownerDocument: doc, tagName: tag.toUpperCase(), children: [], dataset: {}, attrs: {}, listeners: new Map(),
    value: '', textContent: '', checked: false, disabled: false, open: false, isConnected: true,
    classList: {toggle() {}}, setAttribute(k, v) { this.attrs[k] = v; }, hasAttribute(k) { return Object.hasOwn(this.attrs, k); },
    append(...items) { this.children.push(...items); }, replaceChildren(...items) { this.children = items; },
    addEventListener(k, fn) { this.listeners.set(k, fn); }, removeEventListener(k) { this.listeners.delete(k); },
    emit(k) { if (!this.disabled) this.listeners.get(k)?.({target: this}); }, closest() { return this; },
  };
}
function dom() { const doc = {createElement: tag => node(doc, tag)}; return {doc, root: node(doc)}; }
function descendants(root) { return [root, ...root.children.flatMap(descendants)]; }
const action = (root, name) => descendants(root).find(x => x.dataset.worksetAction === name);
const recordRows = root => descendants(root).filter(x => x.dataset.worksetRecord !== undefined);
const inputs = root => descendants(root).filter(x => x.tagName === 'INPUT');
const sgAction = (root, name) => descendants(root).find(x => x.dataset.sgAction === name);
function setRules(root, mask) { descendants(root).filter(x => x.dataset.qualityRule !== undefined).forEach((x, i) => { x.checked = Boolean(mask & (1 << i)); x.emit('change'); }); }
const allText = root => descendants(root).map(x => x.textContent).join('\n');
const serialize = root => JSON.stringify(descendants(root).map(x => ({tag: x.tagName, text: x.textContent, dataset: x.dataset, attrs: x.attrs, open: x.open})));
const settle = () => new Promise(resolve => setImmediate(resolve));
async function until(check) { for (let i = 0; i < 250; i++) { if (check()) return; await new Promise(resolve => setTimeout(resolve, 1)); } throw Error('ASYNC_RESULT_TIMEOUT'); }

test('workset UI shows all pages with safe text/applied rules and refreshes a replacement mount after async completion', async () => {
  const unsafe = '<img src=x onerror=alert(1)>', text = header + Array.from({length: 105}, (_, i) => `${i === 0 ? unsafe : 'P' + i}\t${i}\t\t${i % 2 ? '+' : ''}\t\t\n`).join('');
  const src = source(text, 'folder/<script>.txt'), model = createFilteredWorkset({yieldTask: immediate}); model.acceptSource(src); await model.generate();
  const {root} = dom(); let ui = mountFilteredWorkset(root, model, 'intensity');
  assert.equal(root.children[0].open, false); assert.equal(recordRows(root).length, 50);
  assert.ok(allText(root).includes(unsafe)); assert.equal(descendants(root).filter(x => ['IMG', 'SCRIPT'].includes(x.tagName)).length, 0);
  assert.ok(!allText(root).includes('folder/'));
  const jump = descendants(root).find(x => x.attrs['aria-label'] === '工作集页码'); jump.value = '3'; action(root, 'page').emit('click');
  assert.deepEqual(recordRows(root).map(x => Number(x.dataset.worksetRecord)), [101, 102, 103, 104, 105]);
  setMask(model, src, 1); ui.refresh(); assert.equal(root.children[0].dataset.worksetStale, 'true');
  assert.equal(model.status().snapshot.appliedMask, 0); assert.ok(allText(root).includes('无（全部保留）'));
  const old = descendants(root); ui.dispose(); assert.ok(old.every(x => x.listeners.size === 0));
  const gate = deferred(); let once = true;
  const asyncModel = createFilteredWorkset({yieldTask: () => { if (once) { once = false; return gate.promise; } return Promise.resolve(); }});
  asyncModel.acceptSource(source()); ui = mountFilteredWorkset(root, asyncModel, 'intensity'); action(root, 'generate').emit('click');
  assert.equal(asyncModel.status().busy, true); ui.dispose(); ui = mountFilteredWorkset(root, asyncModel, 'lfq', (_zh, en) => en);
  gate.resolve(); await until(() => !asyncModel.status().busy);
  assert.equal(recordRows(root).length, 4); assert.ok(allText(root).includes('LFQ intensity s'));
  assert.ok(allText(root).includes('Original record number')); ui.dispose(); model.reset(); asyncModel.reset();
});

function workspace() {
  const {root, doc} = dom(), fixed = new Map(), area = node(doc);
  const tabs = ['table', 'fasta', 'join'].map(itab => Object.assign(node(doc, 'button'), {dataset: {itab}}));
  let html = '', groups = null, qc = null, quality = null, workset = null;
  Object.defineProperty(area, 'innerHTML', {get: () => html, set(value) {
    html = value; groups = value.includes('data-sample-groups') ? node(doc) : null; qc = value.includes('data-sample-qc') ? node(doc) : null;
    quality = value.includes('data-protein-filter-preview') ? node(doc) : null; workset = value.includes('data-filtered-workset') ? node(doc) : null;
  }});
  root.querySelector = selector => {
    if (selector === '[data-iw-content]') return area;
    if (selector === '[data-sample-groups]') return groups;
    if (selector === '[data-sample-qc]') return qc;
    if (selector === '[data-protein-filter-preview]') return quality;
    if (selector === '[data-filtered-workset]') return workset;
    if (!fixed.has(selector)) fixed.set(selector, node(doc)); return fixed.get(selector);
  };
  root.querySelectorAll = () => tabs; const dispose = mountImportWorkspace(root);
  async function select(type, file) {
    root.onchange({target: {dataset: {ufile: type}, files: [file]}});
    for (let i = 0; i < 100; i++) { await settle(); const state = root.querySelector(`[data-state="${type}"]`).dataset.status; if (['ready', 'error'].includes(state)) return state; }
    throw Error('READ_DID_NOT_FINISH');
  }
  function click(dataset) { root.onclick({target: Object.assign(node(doc, 'button'), {dataset})}); }
  function quant(value) { root.onchange({target: {dataset: {}, value, hasAttribute: k => k === 'data-iw-quant'}}); }
  return {root, area, select, click, quant, dispose, get groups() { return groups; }, get qc() { return qc; }, get quality() { return quality; }, get workset() { return workset; }};
}
const tinyTXT = () => new File([small], 'same.txt');
const tinyFASTA = () => new File(['>P1\nAC\n>P11\nAC\n>P2\nAC\n>P3\nAC\n>P4\nAC\n'], 'sequences.fasta');

test('actual import generates only on click, keeps old modules unchanged and preserves worksets across types/tabs/FASTA without disk rereads', async () => {
  const oldFetch = globalThis.fetch, oldStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage'), requests = [];
  globalThis.fetch = async url => { requests.push(url); return {ok: true, json: async () => ({status: 'prototype'})}; };
  Object.defineProperty(globalThis, 'localStorage', {configurable: true, get() { throw Error('STORAGE_FORBIDDEN'); }});
  const w = workspace();
  try {
    let reads = 0; const file = tinyTXT(), stream = file.stream.bind(file); file.stream = () => { reads++; return stream(); };
    await w.select('table', file); assert.equal(recordRows(w.workset).length, 0);
    const qc = serialize(w.qc), original = w.area.innerHTML;
    inputs(w.groups)[0].value = '验收组'; inputs(w.groups)[0].emit('input'); sgAction(w.groups, 'confirm').emit('click');
    const groups = serialize(w.groups); setRules(w.quality, 7);
    w.workset.children[0].open = true; action(w.workset, 'generate').emit('click');
    w.quant('lfq'); await until(() => recordRows(w.workset).length === 2);
    assert.deepEqual(recordRows(w.workset).map(x => Number(x.dataset.worksetRecord)), [3, 4]);
    w.quant('intensity'); assert.equal(serialize(w.qc), qc); assert.equal(serialize(w.groups), groups); assert.equal(w.area.innerHTML, original);
    setRules(w.quality, 0); assert.equal(w.workset.children[0].dataset.worksetStale, 'true'); assert.equal(recordRows(w.workset).length, 2);
    setRules(w.quality, 7); assert.equal(w.workset.children[0].dataset.worksetStale, 'false');
    w.click({itab: 'fasta'}); w.click({itab: 'table'}); assert.equal(recordRows(w.workset).length, 2); assert.equal(w.workset.children[0].open, true);
    await w.select('fasta', tinyFASTA()); w.click({iw: 'join'}); const joined = w.area.innerHTML;
    w.click({itab: 'table'}); setRules(w.quality, 0); action(w.workset, 'generate').emit('click'); await until(() => recordRows(w.workset).length === 4);
    w.click({iw: 'join'}); assert.equal(w.area.innerHTML, joined);
    await w.select('fasta', tinyFASTA()); w.click({itab: 'table'}); assert.equal(recordRows(w.workset).length, 4);
    assert.equal(serialize(w.qc), qc); assert.equal(serialize(w.groups), groups); assert.equal(reads, 1); assert.deepEqual(requests, ['/api/health']);
  } finally {
    w.dispose(); globalThis.fetch = oldFetch;
    if (oldStorage) Object.defineProperty(globalThis, 'localStorage', oldStorage); else delete globalThis.localStorage;
  }
});

test('actual import drops retained source and workset on reselection, failed/cancelled reads, late attempts, clear and unload', async () => {
  const oldFetch = globalThis.fetch; globalThis.fetch = async () => ({ok: true, json: async () => ({status: 'prototype'})});
  const w = workspace();
  async function prepared() { await w.select('table', tinyTXT()); setRules(w.quality, 7); action(w.workset, 'generate').emit('click'); await until(() => recordRows(w.workset).length === 2); }
  try {
    await prepared(); await w.select('table', tinyTXT()); assert.equal(recordRows(w.workset).length, 0); assert.equal(w.workset.children[0].open, false);
    await prepared(); action(w.workset, 'generate').emit('click'); await w.select('table', tinyTXT());
    await new Promise(resolve => setTimeout(resolve, 5)); assert.equal(recordRows(w.workset).length, 0);
    await prepared(); assert.equal(await w.select('table', new File([small, 'bad\trow\n'], 'same.txt')), 'error'); assert.equal(w.workset, null);
    await prepared(); let resolve; const pending = new Promise(done => { resolve = done; });
    const reader = {read: () => pending, cancel: async () => {}, releaseLock() {}};
    w.root.onchange({target: {dataset: {ufile: 'table'}, files: [{name: 'same.txt', size: 1, stream: () => ({getReader: () => reader})}]}});
    assert.equal(w.workset, null); w.click({cancel: 'table'}); resolve({done: true}); await settle(); assert.equal(w.workset, null);
    await prepared(); w.click({iw: 'clear'}); assert.equal(w.workset, null);
    await prepared(); const old = descendants(w.workset); w.dispose(); assert.ok(old.every(x => x.listeners.size === 0)); assert.equal(w.root.onchange, null);
  } finally { w.dispose(); globalThis.fetch = oldFetch; }
});

test('authorized real worksets: all 8 complete 182-field retained-record fingerprints and original numbering match independent csv', async () => {
  assert.ok(process.env.TXT_PREVIEW_INPUT, 'Explicit authorized TXT path required; no demo fallback');
  const referencePath = process.env.WORKSET_REFERENCE_PATH || new URL('../../independent-workset-reference.json', import.meta.url);
  const [raw, refText] = await Promise.all([readFile(process.env.TXT_PREVIEW_INPUT), readFile(referencePath, 'utf8')]);
  const ref = JSON.parse(refText.replace(/^\uFEFF/, '')), digest = hash(raw);
  assert.equal(digest, '301bc95ff440a4ea70912fb5ede26aa54cb15f2cb06a9efeed6e55fd749f95fb');
  assert.equal(ref.inputSha256, digest); assert.equal(ref.rowCount, 2252); assert.equal(ref.columnCount, 182);
  const src = source(raw.toString('utf8')), model = createFilteredWorkset({yieldTask: immediate}); model.acceptSource(src);
  try {
    for (let mask = 0; mask < 8; mask++) {
      setMask(model, src, mask); assert.equal(await model.generate(), true);
      const snap = model.status().snapshot, expected = ref.cases.find(x => x.selectedMask === mask), fingerprint = createHash('sha256');
      assert.equal(hash(JSON.stringify(snap.headers)), ref.headersSha256);
      assert.equal(snap.originalRecords.length, 2252); assert.equal(snap.headers.length, 182);
      for (const record of snap.keptRecords) {
        assert.equal(record.cells.length, 182); assert.ok(record === snap.originalRecords[record.recordNumber - 1], 'Retained row must reference its original frozen row');
        fingerprint.update(JSON.stringify([record.recordNumber, ...record.cells]) + '\n');
      }
      assert.equal(fingerprint.digest('hex'), expected.retainedRowsSha256);
      assert.deepEqual([snap.counts.retained, snap.counts.excluded, snap.counts.pending], [expected.retained, expected.excluded, expected.pending]);
      assert.deepEqual(snap.counts.reasons, expected.reasons);
      assert.equal(snap.keptRecords[0]?.recordNumber ?? null, expected.firstRecordNumber);
      assert.equal(snap.keptRecords.at(-1)?.recordNumber ?? null, expected.lastRecordNumber);
      if (mask === 7) {
        assert.equal(snap.counts.retained, 2199); assert.equal(snap.keptRecords[0].recordNumber, 5); assert.equal(snap.keptRecords.at(-1).recordNumber, 2227);
        assert.equal(expected.retainedRowsSha256, 'b6b5ddd9af684c0fe6f36f75a01163e4a1fb7203212eb1ccb9b21daa52ed4d80');
        assert.equal(model.setPage(44), true); assert.equal(model.getPage().records.length, 49); assert.equal(model.getPage().records.at(-1).recordNumber, 2227);
        assert.equal(model.getPage('intensity').columns.length, 21); assert.equal(model.getPage('lfq').columns.length, 21);
        model.setView('original'); assert.equal(model.setPage(46), true); assert.deepEqual(model.getPage().records.map(r => r.recordNumber), [2251, 2252]);
      }
    }
    assert.equal(hash(await readFile(process.env.TXT_PREVIEW_INPUT)), digest);
  } finally { model.reset(); }
});
