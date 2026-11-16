import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {parseProteinGroups} from '../protein-groups.js';
import {createSampleQCCollector} from '../sample-qc.js';
import {parseProteinGroupsForAssociation, releaseAssociationInputs} from '../protein-association.js';
import {mountImportWorkspace} from '../import-workspace.js';
import {QUALITY_FLAG_NAMES, createProteinFilterCollector, evaluateProteinFilter, createProteinFilterPreview, mountProteinFilterPreview} from '../protein-filter-preview.js';

const v5URL = process.env.V5_PARSER_PATH ? pathToFileURL(process.env.V5_PARSER_PATH)
  : new URL('../../../sample-composition001/Cy_Omics_v2.0/protein-groups.js', import.meta.url);
const {parseProteinGroups: parseV5} = await import(v5URL.href);
const header = ['Protein IDs', 'Intensity s', 'LFQ intensity s', ...QUALITY_FLAG_NAMES].join('\t');
const small = header + '\nP1\t1\t0\t+\t\t\nP2\t2\t0\t+\t\t+\nP3\t3\t0\t\t?\t\nP4\t0\t\t\t\t\n';
function collect(text) {
  const collector = createProteinFilterCollector();
  try {
    const p = parseProteinGroups(text, 10, {onQualityFlagRow: collector.onQualityFlagRow});
    const association = parseProteinGroupsForAssociation(text);
    try { return {preview: p, summary: collector.finish(p)}; }
    finally { releaseAssociationInputs(association); }
  } finally { collector.abort(); }
}

test('all 27 tri-states and all 8 selections conserve records and mutually exclusive union reasons', () => {
  const states = [];
  for (const a of [0, 1, 2]) for (const b of [0, 1, 2]) for (const c of [0, 1, 2]) states.push([a, b, c]);
  const tokens = ['', '+', '?'];
  const text = header + '\n' + states.map((s, i) => `P${i}\t1\t0\t${s.map(x => tokens[x]).join('\t')}\n`).join('');
  const {summary} = collect(text);
  assert.deepEqual(summary.stateBins, Array(27).fill(1));
  for (let mask = 0; mask < 8; mask++) {
    const ref = {selectedMask: mask, retained: 0, excluded: 0, pending: 0, reasons: Array(8).fill(0)};
    for (const state of states) {
      const chosen = [0, 1, 2].filter(i => mask & (1 << i)), hit = chosen.filter(i => state[i] === 1);
      if (hit.length) { ref.excluded++; ref.reasons[hit.reduce((bits, i) => bits + 2 ** i, 0)]++; }
      else if (chosen.some(i => state[i] === 2)) ref.pending++;
      else ref.retained++;
    }
    const actual = evaluateProteinFilter(summary, mask);
    assert.deepEqual(actual, ref);
    assert.equal(actual.retained + actual.excluded + actual.pending, 27);
    assert.equal(actual.reasons.reduce((a, b) => a + b, 0), actual.excluded);
  }
  assert.equal(evaluateProteinFilter(summary, 0).retained, 27);
});

test('quality callback snapshots are immutable, strictly synchronous, sanitized and run after unchanged v5 ID/quant callbacks', async () => {
  const order = [], flags = [], oldIDs = [], oldQuant = [];
  const result = parseProteinGroups(small, 10, {
    onRecord(record) { order.push('id' + record.recordNumber); oldIDs.push(structuredClone(record)); record.tokens.length = 0; },
    onQuantificationRow(record) { order.push('quant' + record.recordNumber); oldQuant.push(structuredClone(record)); },
    onQualityFlagRow(record) {
      order.push('flag' + record.recordNumber);
      assert.ok(Object.isFrozen(record) && Object.isFrozen(record.flags));
      assert.ok(record.flags.every(f => Object.isFrozen(f) && Object.isFrozen(f.indices)));
      assert.equal(Reflect.set(record.flags[0], 'value', 'changed'), false);
      assert.equal(Reflect.set(record.flags[0].indices, '0', 99), false);
      flags.push(record);
    },
  });
  const ids = [], quant = [];
  const old = parseV5(small, 10, {onRecord: r => { ids.push(structuredClone(r)); r.tokens.length = 0; }, onQuantificationRow: r => { quant.push(structuredClone(r)); }});
  assert.deepEqual(result, old); assert.deepEqual(oldIDs, ids); assert.deepEqual(oldQuant, quant);
  assert.deepEqual(order, Array.from({length: 4}, (_, i) => ['id', 'quant', 'flag'].map(x => x + (i + 1))).flat());
  assert.deepEqual(flags[0].flags.map(f => f.indices), [[3], [4], [5]]);
  let getterCalled = false;
  const accessor = Object.defineProperty({}, 'onQualityFlagRow', {get() { getterCalled = true; return () => {}; }});
  for (const options of [accessor, {[Symbol('bad')]: () => {}}, {unknown: () => {}}, {onQualityFlagRow: 1},
    {onQualityFlagRow: async () => {}}, {onQualityFlagRow: function* () {}}, {onQualityFlagRow: async function* () {}}]) {
    assert.throws(() => parseProteinGroups(small, 10, options), {code: 'INVALID_CALLBACKS'});
  }
  assert.equal(getterCalled, false);
  for (const fn of [() => Promise.reject(Error('FLAG_SECRET')), () => ({then(resolve) { resolve(); }}), () => { throw Error('FLAG_SECRET'); }]) {
    assert.throws(() => parseProteinGroups(small, 10, {onQualityFlagRow: fn}), e => e.code === 'CALLBACK_FAILED' && !e.message.includes('FLAG_SECRET'));
  }
  await new Promise(resolve => setImmediate(resolve));
});

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
const inputs = root => descendants(root).filter(x => x.tagName === 'INPUT');
const action = (root, name) => descendants(root).find(x => x.dataset.sgAction === name);
function selectRules(root, mask) { inputs(root).forEach((input, i) => { input.checked = Boolean(mask & (1 << i)); input.emit('change'); }); }
function metrics(root) { return Object.fromEntries(descendants(root).filter(x => x.dataset.qualityCount).map(x => [x.dataset.qualityCount, Number(x.textContent)])); }
const serialize = root => JSON.stringify(descendants(root).map(x => ({tag: x.tagName, text: x.textContent, attrs: x.attrs, dataset: x.dataset, open: x.open})));
const settle = () => new Promise(resolve => setImmediate(resolve));

test('exact flag strings, missing/duplicate columns and default-off UI cannot silently retain unknown selected values', () => {
  const values = ['', '+', '+ ', ' +', ' ', '1', 'TRUE'];
  const {summary} = collect(header + '\n' + values.map((v, i) => `P${i}\t1\t0\t${v}\t\t\n`).join(''));
  assert.deepEqual([...Object.values(evaluateProteinFilter(summary, 1)).slice(1, 4)], [1, 1, 5]);
  const missing = collect('Protein IDs\tIntensity s\tReverse\tReverse\tOnly identified by site\nP1\t1\t+\t\t+\n').summary;
  assert.deepEqual(missing.flags.map(f => [f.available, f.indices.length]), [[false, 2], [false, 0], [true, 1]]);
  assert.throws(() => evaluateProteinFilter(missing, 1), {code: 'QUALITY_RULE_UNAVAILABLE'});
  const model = createProteinFilterPreview(); model.load(missing);
  const {root} = dom(), dispose = mountProteinFilterPreview(root, model, (_zh, en) => en);
  assert.equal(root.children[0].open, false); assert.ok(inputs(root).every(x => !x.checked));
  assert.deepEqual(inputs(root).map(x => x.disabled), [true, true, false]);
  assert.equal(model.setRule(0, true), false); assert.equal(model.view().selectedMask, 0);
  assert.deepEqual(metrics(root), {retained: 1, excluded: 0, pending: 0});
  selectRules(root, 4); assert.deepEqual(metrics(root), {retained: 0, excluded: 1, pending: 0});
  assert.equal(descendants(root).filter(x => ['BUTTON', 'SCRIPT', 'IMG'].includes(x.tagName)).length, 0);
  const before = descendants(root); dispose(); assert.ok(before.every(x => x.listeners.size === 0));
  assert.deepEqual(Object.keys(summary).sort(), ['flags', 'recordCount', 'stateBins']);
  assert.equal(summary.stateBins.length, 27); assert.ok(Object.isFrozen(summary.stateBins));
});

function workspace() {
  const {root, doc} = dom(), fixed = new Map(), area = node(doc);
  const tabs = ['table', 'fasta', 'join'].map(itab => Object.assign(node(doc, 'button'), {dataset: {itab}}));
  let html = '', groups = null, qc = null, quality = null;
  Object.defineProperty(area, 'innerHTML', {get: () => html, set(value) {
    html = value; groups = value.includes('data-sample-groups') ? node(doc) : null; qc = value.includes('data-sample-qc') ? node(doc) : null;
    quality = value.includes('data-protein-filter-preview') ? node(doc) : null;
  }});
  root.querySelector = selector => {
    if (selector === '[data-iw-content]') return area;
    if (selector === '[data-sample-groups]') return groups;
    if (selector === '[data-sample-qc]') return qc;
    if (selector === '[data-protein-filter-preview]') return quality;
    if (!fixed.has(selector)) fixed.set(selector, node(doc)); return fixed.get(selector);
  };
  root.querySelectorAll = () => tabs;
  const dispose = mountImportWorkspace(root);
  async function select(type, file) {
    root.onchange({target: {dataset: {ufile: type}, files: [file]}});
    for (let i = 0; i < 50; i++) { await settle(); const state = root.querySelector(`[data-state="${type}"]`).dataset.status; if (['ready', 'error'].includes(state)) return state; }
    throw Error('READ_DID_NOT_FINISH');
  }
  function click(dataset) { root.onclick({target: Object.assign(node(doc, 'button'), {dataset})}); }
  function quant(value) { root.onchange({target: {dataset: {}, value, hasAttribute: k => k === 'data-iw-quant'}}); }
  return {root, area, select, click, quant, dispose, get groups() { return groups; }, get qc() { return qc; }, get quality() { return quality; }};
}
const tinyTXT = () => new File([small], 'same.txt');
const tinyFASTA = () => new File(['>P1\nAC\n>P2\nAC\n>P3\nAC\n>P4\nAC\n'], 'sequences.fasta');

test('actual import keeps whole-table rules across types/tabs/FASTA, without rereading or changing QC/chart/groups/association', async () => {
  const oldFetch = globalThis.fetch, oldStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage'), requests = [];
  globalThis.fetch = async url => { requests.push(url); return {ok: true, json: async () => ({status: 'prototype'})}; };
  Object.defineProperty(globalThis, 'localStorage', {configurable: true, get() { throw Error('STORAGE_FORBIDDEN'); }});
  const w = workspace();
  try {
    let reads = 0; const file = tinyTXT(), stream = file.stream.bind(file); file.stream = () => { reads++; return stream(); };
    assert.equal(await w.select('table', file), 'ready');
    const qc = serialize(w.qc), original = w.area.innerHTML;
    w.quality.children[0].open = true;
    inputs(w.groups)[0].value = '验收组'; inputs(w.groups)[0].emit('input'); action(w.groups, 'confirm').emit('click');
    const group = serialize(w.groups);
    selectRules(w.quality, 7);
    assert.deepEqual(metrics(w.quality), {retained: 1, excluded: 2, pending: 1});
    assert.equal(serialize(w.qc), qc); assert.equal(serialize(w.groups), group); assert.equal(w.area.innerHTML, original); assert.equal(reads, 1);
    w.quant('lfq'); assert.deepEqual(metrics(w.quality), {retained: 1, excluded: 2, pending: 1});
    w.click({itab: 'join'}); w.click({itab: 'table'}); w.quant('intensity');
    assert.ok(inputs(w.quality).every(x => x.checked)); assert.equal(w.quality.children[0].open, true);
    assert.equal(serialize(w.qc), qc); assert.equal(w.groups.children[0].dataset.groupState, 'confirmed');
    await w.select('fasta', tinyFASTA()); w.click({iw: 'join'}); const joined = w.area.innerHTML;
    w.click({itab: 'table'}); selectRules(w.quality, 0); w.click({iw: 'join'}); assert.equal(w.area.innerHTML, joined);
    w.click({itab: 'table'}); selectRules(w.quality, 7);
    await w.select('fasta', tinyFASTA()); w.click({itab: 'table'});
    assert.deepEqual(metrics(w.quality), {retained: 1, excluded: 2, pending: 1}); assert.equal(serialize(w.qc), qc);
    assert.equal(reads, 1); assert.deepEqual(requests, ['/api/health']);
  } finally {
    w.dispose(); globalThis.fetch = oldFetch;
    if (oldStorage) Object.defineProperty(globalThis, 'localStorage', oldStorage); else delete globalThis.localStorage;
  }
});

test('actual import resets rules on same-name selection and discards both summaries on failure, cancellation, stale read, clear and unmount', async () => {
  const oldFetch = globalThis.fetch; globalThis.fetch = async () => ({ok: true, json: async () => ({status: 'prototype'})});
  const w = workspace();
  async function prepared() { assert.equal(await w.select('table', tinyTXT()), 'ready'); w.quality.children[0].open = true; selectRules(w.quality, 7); }
  try {
    await prepared(); await w.select('table', tinyTXT());
    assert.ok(inputs(w.quality).every(x => !x.checked)); assert.equal(w.quality.children[0].open, false);
    assert.deepEqual(metrics(w.quality), {retained: 4, excluded: 0, pending: 0});
    await prepared(); assert.equal(await w.select('table', new File([small, 'bad\trow\n'], 'same.txt')), 'error');
    assert.equal(w.quality, null); assert.equal(w.qc, null);
    await prepared(); const hugeIDs = header + '\n' + 'P;'.repeat(100001) + '\t1\t0\t+\t\t\n';
    assert.equal(await w.select('table', new File([hugeIDs], 'same.txt')), 'error'); assert.equal(w.quality, null); assert.equal(w.qc, null);
    await prepared(); let resolve;
    const pending = new Promise(done => { resolve = done; }); let cancelled = false;
    const reader = {read: () => pending, cancel: async () => { cancelled = true; }, releaseLock() {}};
    w.root.onchange({target: {dataset: {ufile: 'table'}, files: [{name: 'same.txt', size: 1, stream: () => ({getReader: () => reader})}]}});
    assert.equal(w.quality, null); assert.equal(w.qc, null); w.click({cancel: 'table'});
    assert.equal(cancelled, true); await w.select('table', tinyTXT());
    resolve({value: new TextEncoder().encode(small), done: false}); await settle();
    assert.deepEqual(metrics(w.quality), {retained: 4, excluded: 0, pending: 0});
    assert.ok(inputs(w.quality).every(x => !x.checked));
    await prepared(); w.click({iw: 'clear'}); assert.equal(w.quality, null); assert.equal(w.qc, null);
    await prepared(); const old = descendants(w.quality); w.dispose(); assert.ok(old.every(x => x.listeners.size === 0));
    assert.equal(w.root.onchange, null); assert.equal(w.root.onclick, null);
  } finally { w.dispose(); globalThis.fetch = oldFetch; }
});

test('authorized real filter: all 2252 rows and 8 selections match independent csv bins/reasons with v5 defaults and callbacks unchanged', async () => {
  assert.ok(process.env.TXT_PREVIEW_INPUT, 'Explicit authorized TXT path required; no demo fallback');
  const referencePath = process.env.FILTER_REFERENCE_PATH || new URL('../../independent-filter-counts.json', import.meta.url);
  const [raw, refText] = await Promise.all([readFile(process.env.TXT_PREVIEW_INPUT), readFile(referencePath, 'utf8')]);
  const hash = b => createHash('sha256').update(b).digest('hex'), digest = hash(raw), ref = JSON.parse(refText.replace(/^\uFEFF/, ''));
  assert.equal(digest, '301bc95ff440a4ea70912fb5ede26aa54cb15f2cb06a9efeed6e55fd749f95fb');
  assert.equal(ref.inputSha256, digest); assert.equal(ref.rowCount, 2252); assert.deepEqual(ref.flagOrder, QUALITY_FLAG_NAMES);
  const text = raw.toString('utf8'), flags = createProteinFilterCollector(), qc = createSampleQCCollector();
  const current = createHash('sha256'), prior = createHash('sha256');
  const parsed = parseProteinGroups(text, 10, {onRecord: r => { current.update('id' + JSON.stringify(r)); },
    onQuantificationRow: r => { current.update('quant' + JSON.stringify(r)); qc.onQuantificationRow(r); }, onQualityFlagRow: flags.onQualityFlagRow});
  const old = parseV5(text, 10, {onRecord: r => { prior.update('id' + JSON.stringify(r)); }, onQuantificationRow: r => { prior.update('quant' + JSON.stringify(r)); }});
  assert.ok(JSON.stringify(parsed) === JSON.stringify(old), 'Default v5 output changed');
  assert.equal(current.digest('hex'), prior.digest('hex')); assert.equal(parsed.rowCount, 2252); assert.equal(parsed.columnCount, ref.columnCount);
  const association = parseProteinGroupsForAssociation(text);
  try {
    const summary = flags.finish(parsed), originalQC = qc.finish(parsed);
    assert.equal(originalQC.recordCount, 2252); assert.equal(originalQC.intensity.length, 21); assert.equal(originalQC.lfq.length, 21);
    assert.deepEqual(summary.flags.map(f => f.indices), [[169], [170], [168]]); assert.ok(summary.flags.every(f => f.available));
    assert.deepEqual(summary.stateBins, ref.stateBins);
    for (let mask = 0; mask < 8; mask++) assert.deepEqual(evaluateProteinFilter(summary, mask), ref.previews.find(x => x.selectedMask === mask));
    const all = evaluateProteinFilter(summary, 7);
    assert.deepEqual([all.retained, all.excluded, all.pending], [2199, 53, 0]);
    assert.deepEqual(all.reasons, [0, 22, 4, 0, 24, 3, 0, 0]);
    assert.deepEqual([1, 2, 4].map(mask => evaluateProteinFilter(summary, mask).excluded), [25, 4, 27]);
    assert.equal(association.rowCount, 2252); assert.equal(hash(await readFile(process.env.TXT_PREVIEW_INPUT)), digest);
  } finally { flags.abort(); qc.abort(); releaseAssociationInputs(association); }
});
