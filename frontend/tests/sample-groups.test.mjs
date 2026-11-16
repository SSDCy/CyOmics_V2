import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {createSampleGroups, mountSampleGroups} from '../sample-groups.js';
import {mountImportWorkspace} from '../import-workspace.js';
import {parseProteinGroups} from '../protein-groups.js';
import {createFastaPreviewParser} from '../fasta-preview.js';
import {parseProteinGroupsForAssociation, createFastaAssociationParser, associateProteinGroups, releaseAssociationInputs} from '../protein-association.js';

function preview(a = ['s1', 's2'], b = a) {
  const cols = (names, prefix, offset) => names.map((sampleName, i) => Object.freeze({sampleName, index: offset + i, columnName: prefix + ' ' + sampleName}));
  return Object.freeze({intensityColumns: Object.freeze(cols(a, 'Intensity', 1)), lfqColumns: Object.freeze(cols(b, 'LFQ intensity', 1 + a.length))});
}
const settle = () => new Promise(resolve => setImmediate(resolve));
function fill(model, type, value) { for (const row of model.view(type).rows) model.setGroup(type, row.index, value); }

test('exact unique names reuse assignments, with separate confirmations and edit invalidation', () => {
  const p = preview(['s1', 's2'], ['s2', 's1']), before = JSON.stringify(p), model = createSampleGroups();
  model.load(p);
  assert.equal(model.view('intensity').status, 'empty');
  assert.equal(model.confirm('intensity'), false);
  fill(model, 'intensity', '  单组  ');
  assert.equal(model.confirm('intensity'), true);
  assert.equal(model.view('lfq').status, 'draft');
  assert.deepEqual(model.view('lfq').rows.map(x => x.group), ['单组', '单组']);
  assert.equal(model.confirm('lfq'), true);
  model.setGroup('lfq', p.lfqColumns[0].index, 'changed');
  assert.equal(model.view('lfq').status, 'draft');
  assert.equal(model.view('intensity').status, 'draft');
  assert.equal(model.view('intensity').rows[1].group, 'changed');
  assert.equal(model.view('intensity').counts.get('单组'), 1);
  assert.equal(JSON.stringify(p), before);
});

test('blank/duplicate identities block only the affected type; spelling, case and spacing never imply a pairing', () => {
  const model = createSampleGroups();
  model.load(preview(['x', 'x', ''], ['x']));
  fill(model, 'intensity', 'A'); fill(model, 'lfq', 'B');
  assert.ok(model.view('intensity').rows.every(x => x.ambiguous));
  assert.equal(model.confirm('intensity'), false);
  assert.equal(model.confirm('lfq'), true);
  assert.deepEqual(model.view('intensity').rows.map(x => x.group), ['A', 'A', 'A']);
  model.load(preview(['Name', 'name', 's1 '], ['Name', 's1', ' ' ]));
  fill(model, 'intensity', 'I');
  assert.deepEqual(model.view('lfq').rows.map(x => x.group), ['I', '', '']);
  assert.equal(model.view('lfq').rows[2].ambiguous, true);
  model.load(preview([], []));
  assert.equal(model.confirm('intensity'), false);
});

test('groups use safe text keys, trim only the ends, enforce 80 Unicode characters and reject controls without truncation', () => {
  const model = createSampleGroups();
  model.load(preview(['__proto__', 'constructor', 'third']));
  for (const [i, group] of ['__proto__', 'constructor', '  A  a 中文  '].entries()) model.setGroup('intensity', i + 1, group);
  assert.equal(model.confirm('intensity'), true);
  assert.deepEqual([...model.view('intensity').counts], [['__proto__', 1], ['constructor', 1], ['A  a 中文', 1]]);
  assert.equal({}.polluted, undefined);
  model.setGroup('intensity', 1, '中'.repeat(80));
  assert.equal(model.view('intensity').canConfirm, true);
  model.setGroup('intensity', 1, '中'.repeat(81));
  assert.equal(model.view('intensity').rows[0].error, 'length');
  assert.equal(model.view('intensity').rows[0].group.length, 81);
  for (const group of ['\tvalid', 'valid\n', 'a\u0000b', 'a\u0085b']) {
    model.setGroup('intensity', 1, group);
    assert.equal(model.view('intensity').rows[0].error, 'control');
    assert.equal(model.confirm('intensity'), false);
  }
});

test('reset clears a file lifetime, while clear-all clears both types and retains editor expansion', () => {
  const model = createSampleGroups(); model.load(preview());
  fill(model, 'intensity', 'A'); model.confirm('intensity');
  model.setExpanded('intensity', true); model.setExpanded('lfq', true);
  model.clear();
  for (const type of ['intensity', 'lfq']) {
    assert.equal(model.view(type).status, 'empty'); assert.equal(model.view(type).expanded, true);
  }
  fill(model, 'lfq', 'B'); model.confirm('lfq'); model.reset();
  assert.equal(model.view('lfq').rows.length, 0);
  assert.equal(model.view('lfq').expanded, false);
  model.load(preview()); assert.equal(model.view('intensity').status, 'empty');
});

// A small DOM port exercises controller events/lifetimes, not browser HTML/layout.
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
const inputNodes = root => descendants(root).filter(x => x.tagName === 'INPUT');
const action = (root, name) => descendants(root).find(x => x.dataset.sgAction === name);
function fillUI(root, value) { for (const input of inputNodes(root)) { input.value = value; input.emit('input'); } }

test('editor safely displays untrusted text, supports bilingual status/validation and removes listeners on unmount', () => {
  const model = createSampleGroups(), {root} = dom();
  const unsafe = '<img src=x onerror=alert(1)>';
  model.load(preview([unsafe]));
  const dispose = mountSampleGroups(root, model, 'intensity', (_zh, en) => en);
  assert.ok(descendants(root).some(x => x.textContent === unsafe));
  assert.equal(descendants(root).filter(x => ['IMG', 'SCRIPT'].includes(x.tagName)).length, 0);
  fillUI(root, '__proto__'); action(root, 'confirm').emit('click');
  assert.equal(model.view('intensity').status, 'confirmed');
  assert.ok(descendants(root).some(x => x.attrs.role === 'status' && x.textContent.includes('Confirmed')));
  fillUI(root, 'x'.repeat(81));
  assert.equal(action(root, 'confirm').disabled, true);
  assert.equal(inputNodes(root)[0].value.length, 81);
  assert.equal(inputNodes(root)[0].attrs['aria-invalid'], 'true');
  assert.ok(descendants(root).some(x => x.textContent.includes('input has not been truncated')));
  const old = descendants(root);
  dispose();
  assert.ok(old.every(x => x.listeners.size === 0));
  assert.equal(root.children.length, 0);
});

function workspace() {
  const {root, doc} = dom(), fixed = new Map();
  const area = node(doc), tabs = ['table', 'fasta', 'join'].map(itab => Object.assign(node(doc, 'button'), {dataset: {itab}}));
  let html = '', groups = null;
  Object.defineProperty(area, 'innerHTML', {get: () => html, set(value) { html = value; groups = value.includes('data-sample-groups') ? node(doc) : null; }});
  root.querySelector = selector => {
    if (selector === '[data-iw-content]') return area;
    if (selector === '[data-sample-groups]') return groups;
    if (!fixed.has(selector)) fixed.set(selector, node(doc));
    return fixed.get(selector);
  };
  root.querySelectorAll = () => tabs;
  const dispose = mountImportWorkspace(root);
  async function select(type, file) {
    root.onchange({target: {dataset: {ufile: type}, files: [file]}});
    for (let i = 0; i < 30; i++) {
      await settle();
      const state = root.querySelector(`[data-state="${type}"]`).dataset.status;
      if (['ready', 'error'].includes(state)) return state;
    }
    throw Error('READ_DID_NOT_FINISH');
  }
  function click(dataset) { root.onclick({target: Object.assign(node(doc, 'button'), {dataset})}); }
  function quant(value) { root.onchange({target: {dataset: {}, value, hasAttribute: k => k === 'data-iw-quant'}}); }
  return {root, area, select, click, quant, dispose, get groups() { return groups; }};
}
const tinyTXT = () => new File(['Protein IDs\tIntensity s1\tIntensity s2\tLFQ intensity s1\tLFQ intensity s2\nP1\t0\t\t2\t3\n'], 'same.txt');
const tinyFASTA = () => new File(['>P1\nACGT\n'], 'sequences.fasta');

test('actual workspace preserves grouping across tabs/types and FASTA replacement, with zero added network/storage calls', async () => {
  const oldFetch = globalThis.fetch, oldStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage'), requests = [];
  globalThis.fetch = async (url, options) => { requests.push([url, options]); return {ok: true, json: async () => ({status: 'prototype'})}; };
  Object.defineProperty(globalThis, 'localStorage', {configurable: true, get() { throw Error('STORAGE_FORBIDDEN'); }});
  let w;
  try {
    w = workspace(); await w.select('table', tinyTXT());
    const unchangedPreview = w.area.innerHTML;
    assert.ok(unchangedPreview.indexOf('data-sample-groups') > unchangedPreview.indexOf('iw-toolbar'));
    assert.ok(unchangedPreview.indexOf('data-sample-groups') < unchangedPreview.indexOf('iw-scroll'));
    assert.equal(w.groups.children[0].open, false);
    w.groups.children[0].open = true;
    fillUI(w.groups, '中文单组'); action(w.groups, 'confirm').emit('click');
    w.click({itab: 'fasta'}); assert.equal(w.groups, null);
    w.click({itab: 'table'});
    assert.equal(w.groups.children[0].open, true);
    assert.equal(w.groups.children[0].dataset.groupState, 'confirmed');
    assert.ok(inputNodes(w.groups).every(x => x.value === '中文单组'));
    w.quant('lfq');
    assert.equal(w.groups.children[0].dataset.groupState, 'draft');
    action(w.groups, 'confirm').emit('click');
    fillUI(w.groups, 'changed'); w.quant('intensity');
    assert.equal(w.groups.children[0].dataset.groupState, 'draft');
    assert.ok(inputNodes(w.groups).every(x => x.value === 'changed'));
    fillUI(w.groups, '中文单组'); action(w.groups, 'confirm').emit('click');
    assert.equal(w.area.innerHTML, unchangedPreview);
    await w.select('fasta', tinyFASTA()); w.click({itab: 'table'});
    assert.equal(w.groups.children[0].dataset.groupState, 'confirmed');
    w.click({iw: 'join'}); const originalJoin = w.area.innerHTML;
    w.click({itab: 'table'}); fillUI(w.groups, 'new'); w.click({iw: 'join'});
    assert.equal(w.area.innerHTML, originalJoin);
    await w.select('fasta', tinyFASTA()); w.click({itab: 'table'});
    assert.ok(inputNodes(w.groups).every(x => x.value === 'new'));
    action(w.groups, 'clear').emit('click'); w.quant('lfq');
    assert.ok(inputNodes(w.groups).every(x => x.value === ''));
    assert.equal(w.groups.children[0].dataset.groupState, 'empty');
    assert.deepEqual(requests.map(x => x[0]), ['/api/health']);
  } finally {
    w?.dispose(); globalThis.fetch = oldFetch;
    if (oldStorage) Object.defineProperty(globalThis, 'localStorage', oldStorage); else delete globalThis.localStorage;
  }
});

test('actual workspace clears grouping on same-name reselection, failure, read cancellation, clear-all and unmount', async () => {
  const oldFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ok: true, json: async () => ({status: 'prototype'})});
  const w = workspace();
  async function prepared() { await w.select('table', tinyTXT()); fillUI(w.groups, 'A'); action(w.groups, 'confirm').emit('click'); }
  async function emptyAfterReload() { await w.select('table', tinyTXT()); assert.ok(inputNodes(w.groups).every(x => x.value === '')); assert.equal(w.groups.children[0].dataset.groupState, 'empty'); }
  try {
    await prepared(); await emptyAfterReload();
    await prepared(); assert.equal(await w.select('table', new File(['invalid'], 'same.txt')), 'error');
    assert.equal(w.groups, null); await emptyAfterReload();
    await prepared();
    let finish, cancelled = false;
    const pending = new Promise(resolve => { finish = resolve; });
    const reader = {read: () => pending, cancel: async () => { cancelled = true; finish({done: true}); }, releaseLock() {}};
    w.root.onchange({target: {dataset: {ufile: 'table'}, files: [{name: 'same.txt', size: 1, stream: () => ({getReader: () => reader})}]}});
    assert.equal(w.groups, null);
    w.click({cancel: 'table'}); await settle(); assert.equal(cancelled, true);
    await emptyAfterReload();
    await prepared(); w.click({iw: 'clear'}); assert.equal(w.groups, null); await emptyAfterReload();
    await prepared(); const oldNodes = descendants(w.groups); w.dispose();
    assert.ok(oldNodes.every(x => x.listeners.size === 0));
    assert.equal(w.root.onchange, null); assert.equal(w.root.onclick, null);
  } finally { w.dispose(); globalThis.fetch = oldFetch; }
});

test('authorized real TXT/FASTA: 21 unique shared samples, unchanged raw values/IDs/join and unchanged input hashes', async () => {
  const paths = [process.env.TXT_PREVIEW_INPUT, process.env.FASTA_PREVIEW_INPUT];
  assert.ok(paths.every(Boolean), 'Explicit real input paths required; no demo fallback');
  const buffers = await Promise.all(paths.map(p => readFile(p)));
  const digest = b => createHash('sha256').update(b).digest('hex');
  const expected = ['301bc95ff440a4ea70912fb5ede26aa54cb15f2cb06a9efeed6e55fd749f95fb', '8cb4a7994a80b6fecef0310e288c4763261026989b73a4e467d3310aad5daba5'];
  assert.ok(buffers.every((b, i) => digest(b) === expected[i]), 'Real input hash mismatch');
  const p = parseProteinGroups(buffers[0].toString('utf8')), originalPreview = JSON.stringify(p), model = createSampleGroups();
  assert.equal(p.rowCount, 2252); assert.equal(p.columnCount, 182);
  assert.equal(p.intensityColumns.length, 21); assert.equal(p.lfqColumns.length, 21);
  assert.ok(new Set(p.intensityColumns.map(x => x.sampleName)).size === 21, 'Real sample uniqueness mismatch');
  assert.ok(p.intensityColumns.every((x, i) => x.sampleName === p.lfqColumns[i].sampleName), 'Real sample correspondence mismatch');
  const table = parseProteinGroupsForAssociation(buffers[0].toString('utf8'));
  const fasta = createFastaAssociationParser(), summary = createFastaPreviewParser();
  const text = buffers[1].toString('utf8');
  for (let i = 0; i < text.length; i += 65536) { fasta.push(text.slice(i, i + 65536)); summary.push(text.slice(i, i + 65536)); }
  const index = fasta.finish(), info = summary.finish();
  try {
    const before = JSON.stringify(associateProteinGroups(table, index));
    model.load(p); fill(model, 'intensity', '手动单组'); assert.equal(model.confirm('intensity'), true);
    assert.equal(model.view('intensity').rows.length, 21); assert.equal(model.view('intensity').counts.size, 1);
    assert.equal(model.view('lfq').rows.length, 21); assert.equal(model.view('lfq').status, 'draft');
    assert.ok(model.view('lfq').rows.every(x => x.group === '手动单组'), 'Manual assignments were not reused');
    assert.equal(model.confirm('lfq'), true);
    assert.ok(JSON.stringify(p) === originalPreview, 'Original preview changed');
    const joined = associateProteinGroups(table, index);
    assert.ok(JSON.stringify(joined) === before, 'ID association changed after grouping');
    assert.equal(info.recordCount, 38387); assert.equal(info.totalSequenceLength, 16367314);
    assert.deepEqual([joined.summary.rowCount, joined.summary.allMatchRows, joined.summary.partialMatchRows, joined.summary.zeroMatchRows], [2252, 2223, 0, 29]);
    assert.equal(joined.rows[1204].matches.length, 23);
    assert.ok((await Promise.all(paths.map(p => readFile(p)))).every((b, i) => digest(b) === expected[i]), 'Input hash changed');
  } finally { model.reset(); releaseAssociationInputs(table, index); }
});
