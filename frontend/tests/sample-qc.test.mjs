import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import {parseProteinGroups} from '../protein-groups.js';
import {classifySampleValue, createSampleQCCollector, mountSampleQC} from '../sample-qc.js';
import {mountImportWorkspace} from '../import-workspace.js';
import {createSampleGroups} from '../sample-groups.js';
import {createFastaPreviewParser} from '../fasta-preview.js';
import {parseProteinGroupsForAssociation, createFastaAssociationParser, associateProteinGroups, releaseAssociationInputs} from '../protein-association.js';

const v3URL = process.env.V3_PARSER_PATH ? pathToFileURL(process.env.V3_PARSER_PATH)
  : new URL('../../../sample-group001/Cy_Omics_v2.0/protein-groups.js', import.meta.url);
const {parseProteinGroups: parseV3} = await import(v3URL.href);
const small = 'Protein IDs\tIntensity sample\tLFQ intensity sample\nP1;P2\t 2 \t0\nP3\t\t-1\n';

test('QC classification distinguishes decimal sign, true zero, blank subtypes and all range/format boundaries', () => {
  const cases = {
    positive: ['1', '+.5', '1.', '1e+3', ' 2.5 ', '5e-324', '0001'],
    zero: ['0', '-0', '+0.000', '-0e-999', '0e999', '.0', '000.00e999999'],
    emptyString: [''], whitespace: [' ', '\t', '\r\n', '\u00a0'],
    negative: ['-1', '-.01', '-2e2', '-5e-324'],
    format: ['0x10', '0b1', '0o7', '1_000', '1,000', '1e', '.', '+', 'nan', 'Infinityx', '1e-9999', '-1e-9999', '1\u00002'],
    nonFinite: ['NaN', 'Infinity', '+Infinity', '-Infinity', '1e999', '-1e999'],
  };
  for (const [category, values] of Object.entries(cases)) {
    for (const value of values) assert.equal(classifySampleValue(value), category);
  }
});

test('new quantification callback is frozen, synchronous, follows the unchanged ID callback and cannot change original values', () => {
  const order = [], cells = [];
  const result = parseProteinGroups(small, 10, {
    onRecord(record) { order.push('id' + record.recordNumber); record.tokens.length = 0; record.proteinIds = 'changed'; },
    onQuantificationRow(record) {
      order.push('quant' + record.recordNumber);
      assert.equal(Object.isFrozen(record), true); assert.equal(Object.isFrozen(record.cells), true);
      assert.ok(record.cells.every(Object.isFrozen));
      assert.equal(Reflect.set(record.cells[0], 'value', 'changed'), false);
      assert.equal(Reflect.set(record, 'recordNumber', 50), false);
      cells.push(record.cells.map(x => ({...x})));
    },
  });
  assert.deepEqual(result, parseV3(small));
  assert.deepEqual(order, ['id1', 'quant1', 'id2', 'quant2']);
  assert.deepEqual(cells, [[{columnIndex: 1, value: ' 2 '}, {columnIndex: 2, value: '0'}], [{columnIndex: 1, value: ''}, {columnIndex: 2, value: '-1'}]]);
  assert.equal(result.previewRows[0][0], 'P1;P2');
});

test('callback options reject getters, symbols, unknown keys, async/generator functions and thenables without leaking cell text', async () => {
  let getterCalled = false, asyncCalled = false;
  const accessor = Object.defineProperty({}, 'onQuantificationRow', {get() { getterCalled = true; return () => {}; }});
  const spoofed = async function () { asyncCalled = true; }; Object.defineProperty(spoofed, Symbol.toStringTag, {value: 'Function'});
  for (const options of [null, [], {unknown: () => {}}, {[Symbol('x')]: () => {}}, accessor,
    {onQuantificationRow: 1}, {onQuantificationRow: async () => {}}, {onQuantificationRow: function* () {}},
    {onQuantificationRow: async function* () {}}, {onQuantificationRow: spoofed}]) {
    assert.throws(() => parseProteinGroups(small, 10, options), {code: 'INVALID_CALLBACKS'});
  }
  assert.equal(getterCalled, false); assert.equal(asyncCalled, false);
  for (const callback of [() => Promise.reject(new Error('CELL_SECRET')), () => ({then(resolve) { resolve(); }}), () => { throw Error('CELL_SECRET'); }]) {
    assert.throws(() => parseProteinGroups(small, 10, {onQuantificationRow: callback}), error => error.code === 'CALLBACK_FAILED' && !error.message.includes('CELL_SECRET'));
  }
  await new Promise(resolve => setImmediate(resolve));
});

test('default output and all existing onRecord semantics match the approved v3 parser', () => {
  const quoted = '\ufeffProtein IDs\tIntensity sample\tLFQ intensity sample\r\n"P1;P2"\t"1\n"\t"0"\r\nP3\t""\t"text\tvalue"\r\n';
  for (const limit of [0, 1, 10]) {
    const a = [], b = [];
    assert.deepEqual(parseProteinGroups(quoted, limit, {onRecord: r => { a.push(structuredClone(r)); r.tokens.push('mutated'); }}),
      parseV3(quoted, limit, {onRecord: r => { b.push(structuredClone(r)); r.tokens.push('mutated'); }}));
    assert.deepEqual(a, b);
  }
  for (const parser of [parseProteinGroups, parseV3]) {
    assert.throws(() => parser(small, 10, {onRecord: async () => {}}), {code: 'INVALID_CALLBACKS'});
    assert.throws(() => parser(small, 10, {onRecord: function* () {}}), {code: 'INVALID_CALLBACKS'});
    assert.throws(() => parser(small, 10, {onRecord: () => Promise.resolve()}), {code: 'CALLBACK_FAILED'});
    assert.throws(() => parser(small, 10, {onRecord() { throw Error('private'); }}), error => error.code === 'CALLBACK_FAILED' && !error.message.includes('private'));
  }
});

function summarize(text) {
  const collector = createSampleQCCollector();
  try {
    const p = parseProteinGroups(text, 10, {onQuantificationRow: collector.onQuantificationRow});
    const association = parseProteinGroupsForAssociation(text);
    try { return {preview: p, summary: collector.finish(p)}; }
    finally { releaseAssociationInputs(association); }
  } finally { collector.abort(); }
}
test('collector counts complete records by column index, keeps only frozen totals, and refuses aborted/invalid streams', () => {
  const values = ['1', '0', '', ' ', '-2', 'hex', 'Infinity', '1e-9999'];
  const text = 'Protein IDs\tIntensity same\tIntensity same\tIntensity \tLFQ intensity same\n'
    + Array.from({length: 24}, (_, i) => `P${i}\t${values[i % 8]}\t1\t0\t\n`).join('');
  const {preview, summary} = summarize(text);
  assert.equal(preview.previewRows.length, 10); assert.equal(summary.recordCount, 24);
  assert.equal(summary.intensity.length, 3); assert.equal(summary.lfq.length, 1);
  assert.deepEqual(summary.intensity.map(x => x.index), [1, 2, 3]);
  assert.deepEqual([summary.intensity[0].positive, summary.intensity[0].zero, summary.intensity[0].empty, summary.intensity[0].abnormal], [3, 3, 6, 12]);
  assert.equal(summary.intensity[0].format, 6);
  assert.equal(summary.intensity[1].positive, 24); assert.equal(summary.intensity[2].zero, 24);
  assert.equal(summary.lfq[0].emptyString, 24);
  for (const row of [...summary.intensity, ...summary.lfq]) {
    assert.equal(row.positive + row.zero + row.empty + row.abnormal, 24);
    assert.equal(row.abnormal, row.negative + row.format + row.nonFinite);
    assert.equal(Object.isFrozen(row), true);
    assert.deepEqual(Object.keys(row).sort(), ['index', 'columnName', 'sampleName', 'positive', 'zero', 'emptyString', 'whitespace', 'negative', 'format', 'nonFinite', 'empty', 'abnormal'].sort());
  }
  assert.ok(Object.isFrozen(summary) && Object.isFrozen(summary.intensity));
  const collector = createSampleQCCollector(); collector.abort();
  assert.throws(() => collector.finish(preview), {code: 'SAMPLE_QC_INVALID_STREAM'});
  assert.deepEqual(summarize('Protein IDs\tOther\nP1\t2\n').summary.intensity, []);
  const broken = createSampleQCCollector();
  assert.throws(() => broken.onQuantificationRow({recordNumber: 2, cells: []}), {code: 'SAMPLE_QC_INVALID_STREAM'});
  assert.throws(() => broken.finish(preview), {code: 'SAMPLE_QC_INVALID_STREAM'});
});

// Minimal DOM port for component/controller lifetimes, not a browser substitute.
function node(doc, tag = 'div') {
  return {ownerDocument: doc, tagName: tag.toUpperCase(), children: [], dataset: {}, attrs: {}, listeners: new Map(),
    value: '', textContent: '', disabled: false, open: false, isConnected: true,
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
const fillGroups = root => { for (const input of inputs(root)) { input.value = '验收组'; input.emit('input'); } };
const settle = () => new Promise(resolve => setImmediate(resolve));
function qcCounts(root) {
  return descendants(root).filter(x => x.dataset.qcColumn !== undefined).map(tr => ({
    index: Number(tr.dataset.qcColumn), counts: tr.children.slice(2).map(td => Number(td.children.length ? td.children[0].children[0].textContent : td.textContent)),
  }));
}

test('QC view defaults collapsed, exposes only counts and safe labels, preserves expansion and cleans up', () => {
  const {summary} = summarize('Protein IDs\tIntensity <img src=x>\tLFQ intensity <img src=x>\nP1\t-1\t0\n');
  const {root} = dom(), expanded = new Set();
  let dispose = mountSampleQC(root, summary, 'intensity', expanded, (_zh, en) => en);
  assert.equal(root.children[0].open, false);
  assert.ok(descendants(root).some(x => x.textContent === '<img src=x>'));
  assert.equal(descendants(root).filter(x => ['IMG', 'SCRIPT', 'INPUT', 'BUTTON'].includes(x.tagName)).length, 0);
  assert.deepEqual(qcCounts(root), [{index: 1, counts: [0, 0, 0, 1]}]);
  root.children[0].open = true; const old = descendants(root); dispose();
  assert.ok(old.every(x => x.listeners.size === 0)); assert.equal(root.children.length, 0);
  dispose = mountSampleQC(root, summary, 'intensity', expanded);
  assert.equal(root.children[0].open, true); dispose();
});

function workspace() {
  const {root, doc} = dom(), fixed = new Map(), area = node(doc);
  const tabs = ['table', 'fasta', 'join'].map(itab => Object.assign(node(doc, 'button'), {dataset: {itab}}));
  let html = '', groups = null, qc = null;
  Object.defineProperty(area, 'innerHTML', {get: () => html, set(value) {
    html = value; groups = value.includes('data-sample-groups') ? node(doc) : null; qc = value.includes('data-sample-qc') ? node(doc) : null;
  }});
  root.querySelector = selector => {
    if (selector === '[data-iw-content]') return area;
    if (selector === '[data-sample-groups]') return groups;
    if (selector === '[data-sample-qc]') return qc;
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
  return {root, area, select, click, quant, dispose, get groups() { return groups; }, get qc() { return qc; }};
}
const tinyTXT = () => new File(['Protein IDs\tIntensity s1\tIntensity s2\tLFQ intensity s1\tLFQ intensity s2\n'
  + Array.from({length: 25}, (_, i) => `P${i}\t${i < 10 ? 1 : 0}\t\t2\tNaN\n`).join('')], 'same.txt');
const tinyFASTA = () => new File(['>P0\nACGT\n'], 'sequences.fasta');

test('actual import controller preserves complete QC across tabs/types, grouping and FASTA; sends no added requests or storage', async () => {
  const oldFetch = globalThis.fetch, oldStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage'), requests = [];
  globalThis.fetch = async url => { requests.push(url); return {ok: true, json: async () => ({status: 'prototype'})}; };
  Object.defineProperty(globalThis, 'localStorage', {configurable: true, get() { throw Error('STORAGE_FORBIDDEN'); }});
  const w = workspace();
  try {
    assert.equal(await w.select('table', tinyTXT()), 'ready');
    assert.equal(w.qc.children[0].open, false);
    assert.ok(w.area.innerHTML.indexOf('data-sample-groups') < w.area.innerHTML.indexOf('data-sample-qc'));
    assert.ok(w.area.innerHTML.indexOf('data-sample-qc') < w.area.innerHTML.indexOf('iw-scroll'));
    const counts = qcCounts(w.qc);
    assert.deepEqual(counts, [{index: 1, counts: [10, 15, 0, 0]}, {index: 2, counts: [0, 0, 25, 0]}]);
    w.qc.children[0].open = true;
    fillGroups(w.groups); action(w.groups, 'confirm').emit('click');
    w.quant('lfq'); assert.equal(w.qc.children[0].open, false);
    assert.deepEqual(qcCounts(w.qc), [{index: 3, counts: [25, 0, 0, 0]}, {index: 4, counts: [0, 0, 0, 25]}]);
    action(w.groups, 'confirm').emit('click');
    w.click({itab: 'fasta'}); w.click({itab: 'table'}); w.quant('intensity');
    assert.equal(w.qc.children[0].open, true); assert.deepEqual(qcCounts(w.qc), counts);
    assert.equal(w.groups.children[0].dataset.groupState, 'confirmed');
    await w.select('fasta', tinyFASTA()); w.click({iw: 'join'}); const join = w.area.innerHTML;
    w.click({itab: 'table'}); assert.deepEqual(qcCounts(w.qc), counts);
    inputs(w.groups)[0].value = ''; inputs(w.groups)[0].emit('input'); assert.deepEqual(qcCounts(w.qc), counts);
    w.click({iw: 'join'}); assert.equal(w.area.innerHTML, join);
    await w.select('fasta', tinyFASTA()); w.click({itab: 'table'});
    assert.deepEqual(qcCounts(w.qc), counts);
    assert.equal(inputs(w.groups)[1].value, '验收组');
    assert.deepEqual(requests, ['/api/health']);
  } finally {
    w.dispose(); globalThis.fetch = oldFetch;
    if (oldStorage) Object.defineProperty(globalThis, 'localStorage', oldStorage); else delete globalThis.localStorage;
  }
});

test('actual import clears QC on reselection, late parse failure, ID postvalidation failure, cancellation, clear and unmount', async () => {
  const oldFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ok: true, json: async () => ({status: 'prototype'})});
  const w = workspace();
  async function prepared() { assert.equal(await w.select('table', tinyTXT()), 'ready'); w.qc.children[0].open = true; }
  try {
    await prepared(); await w.select('table', tinyTXT()); assert.equal(w.qc.children[0].open, false);
    await prepared(); assert.equal(await w.select('table', new File([await tinyTXT().text(), 'bad\trow\n'], 'same.txt')), 'error'); assert.equal(w.qc, null);
    await prepared();
    const tooManyIDs = 'Protein IDs\tIntensity s1\n' + 'P;'.repeat(100001) + '\t2\n';
    assert.equal(await w.select('table', new File([tooManyIDs], 'same.txt')), 'error'); assert.equal(w.qc, null);
    await prepared();
    let resolve, cancelled = false;
    const pending = new Promise(done => { resolve = done; });
    const reader = {read: () => pending, cancel: async () => { cancelled = true; resolve({done: true}); }, releaseLock() {}};
    w.root.onchange({target: {dataset: {ufile: 'table'}, files: [{name: 'same.txt', size: 1, stream: () => ({getReader: () => reader})}]}});
    assert.equal(w.qc, null); w.click({cancel: 'table'}); await settle(); assert.equal(cancelled, true); assert.equal(w.qc, null);
    await prepared(); w.click({iw: 'clear'}); assert.equal(w.qc, null);
    await prepared(); const oldNodes = descendants(w.qc); w.dispose(); assert.ok(oldNodes.every(x => x.listeners.size === 0));
    assert.equal(w.root.onclick, null); assert.equal(w.root.onchange, null);
  } finally { w.dispose(); globalThis.fetch = oldFetch; }
});

// Independent Python stdlib TSV/Decimal oracle: stdin only, stdout only aggregate counts.
const oracleCode = String.raw`
import sys,csv,io,re,json,decimal,math
rows=csv.reader(io.StringIO(sys.stdin.read()),delimiter='\t')
head=next(rows)
indices=[i for i,s in enumerate(head) if s.startswith('Intensity ') or s.startswith('LFQ intensity ')]
totals={i:[0]*7 for i in indices}
pattern=re.compile(r'[+-]?(?:[0-9]+(?:[.][0-9]*)?|[.][0-9]+)(?:[eE][+-]?[0-9]+)?\Z')
n=0
for row in rows:
    assert len(row)==len(head)
    n+=1
    for i in indices:
        raw=row[i];s=raw.strip()
        if raw=='':k=2
        elif s=='':k=3
        elif s in ('NaN','Infinity','+Infinity','-Infinity'):k=6
        elif pattern.fullmatch(s) is None:k=5
        else:
            exact=decimal.Decimal(s)
            if exact.is_zero():k=1
            else:
                value=float(s)
                k=6 if not math.isfinite(value) else 5 if value==0 else 4 if exact<0 else 0
        totals[i][k]+=1
print(json.dumps({'records':n,'columns':[[i]+v for i,v in totals.items()]}))
`;

test('authorized real QC: all 2252 records and 42 indexed columns match independent reference; v3 preview, grouping, FASTA and IDs unchanged', async () => {
  const paths = [process.env.TXT_PREVIEW_INPUT, process.env.FASTA_PREVIEW_INPUT];
  assert.ok(paths.every(Boolean), 'Explicit real input paths required; no demo fallback');
  const buffers = await Promise.all(paths.map(p => readFile(p)));
  const hash = b => createHash('sha256').update(b).digest('hex');
  const expected = ['301bc95ff440a4ea70912fb5ede26aa54cb15f2cb06a9efeed6e55fd749f95fb', '8cb4a7994a80b6fecef0310e288c4763261026989b73a4e467d3310aad5daba5'];
  assert.ok(buffers.every((b, i) => hash(b) === expected[i]), 'Real input hash mismatch');
  const text = buffers[0].toString('utf8'), collector = createSampleQCCollector();
  const newIDs = createHash('sha256'), oldIDs = createHash('sha256');
  const p = parseProteinGroups(text, 10, {onRecord: r => { newIDs.update(JSON.stringify(r)); }, onQuantificationRow: collector.onQuantificationRow});
  const old = parseV3(text, 10, {onRecord: r => { oldIDs.update(JSON.stringify(r)); }});
  assert.ok(JSON.stringify(p) === JSON.stringify(old), 'Default v3 preview changed');
  assert.ok(newIDs.digest('hex') === oldIDs.digest('hex'), 'Existing v3 ID callback changed');
  const table = parseProteinGroupsForAssociation(text);
  const fasta = createFastaAssociationParser(), summary = createFastaPreviewParser(), fastaText = buffers[1].toString('utf8');
  for (let i = 0; i < fastaText.length; i += 65536) { const chunk = fastaText.slice(i, i + 65536); fasta.push(chunk); summary.push(chunk); }
  const index = fasta.finish(), info = summary.finish();
  try {
    const before = JSON.stringify(associateProteinGroups(table, index)), qc = collector.finish(p);
    const oracle = spawnSync(process.env.PYTHON_BIN || 'python', ['-c', oracleCode], {input: text, encoding: 'utf8', maxBuffer: 1024 * 1024});
    assert.equal(oracle.status, 0, 'Independent aggregate reference failed');
    const reference = JSON.parse(oracle.stdout);
    const actual = [...qc.intensity, ...qc.lfq].map(c => [c.index, c.positive, c.zero, c.emptyString, c.whitespace, c.negative, c.format, c.nonFinite]);
    assert.equal(qc.recordCount, 2252); assert.equal(reference.records, 2252);
    assert.equal(qc.intensity.length, 21); assert.equal(qc.lfq.length, 21); assert.equal(actual.length, 42);
    assert.ok(JSON.stringify(actual) === JSON.stringify(reference.columns), 'Per-column independent count mismatch');
    assert.ok([...qc.intensity, ...qc.lfq].every(c => c.positive + c.zero + c.empty + c.abnormal === 2252), 'Count conservation failed');
    const groups = createSampleGroups(); groups.load(p);
    for (const row of groups.view('intensity').rows) groups.setGroup('intensity', row.index, '验收组');
    assert.equal(groups.confirm('intensity'), true); assert.equal(groups.confirm('lfq'), true); groups.reset();
    assert.ok(JSON.stringify(p) === JSON.stringify(old), 'Original values changed');
    const joined = associateProteinGroups(table, index);
    assert.ok(JSON.stringify(joined) === before, 'ID association changed');
    assert.deepEqual([joined.summary.rowCount, joined.summary.allMatchRows, joined.summary.partialMatchRows, joined.summary.zeroMatchRows], [2252, 2223, 0, 29]);
    assert.equal(joined.rows[1204].matches.length, 23);
    assert.equal(info.recordCount, 38387); assert.equal(info.totalSequenceLength, 16367314);
    assert.ok((await Promise.all(paths.map(p => readFile(p)))).every((b, i) => hash(b) === expected[i]), 'Input hash changed');
  } finally { collector.abort(); releaseAssociationInputs(table, index); }
});
