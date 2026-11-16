import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {createSampleMetadata, mountSampleMetadata, METADATA_FIELDS} from '../sample-metadata.js';
import {createSampleGroups} from '../sample-groups.js';
import {mountImportWorkspace} from '../import-workspace.js';
import {parseProteinGroups} from '../protein-groups.js';
import {parseProteinGroupsForAssociation, createFastaAssociationParser, associateProteinGroups, releaseAssociationInputs} from '../protein-association.js';
import {createFastaPreviewParser} from '../fasta-preview.js';

const digest = value => createHash('sha256').update(value).digest('hex');
const settle = () => new Promise(resolve => setImmediate(resolve));
function preview(a = ['one', 'two'], b = a) {
  const columns = (names, offset, prefix) => names.map((sampleName, i) => Object.freeze({index: offset + i, columnName: prefix + sampleName, sampleName}));
  return Object.freeze({intensityColumns: Object.freeze(columns(a, 1, 'Intensity ')), lfqColumns: Object.freeze(columns(b, a.length + 1, 'LFQ intensity '))});
}
const complete = {condition: 'Manual condition', control: 'no', replicate: 'R-1', replicateType: 'technical'};
function fill(model, kind = 'intensity', patch = complete) { for (const row of model.view(kind).rows) model.update(kind, row.index, patch); }

test('default facts remain unknown, never infer names or replicate authenticity, and do not count I/LFQ as double samples', () => {
  const model = createSampleMetadata(), p = preview(['coded-7-A', 'control-like-name']); model.load(p);
  for (const kind of ['intensity', 'lfq']) {
    const view = model.view(kind); assert.equal(view.rows.length, 2); assert.equal(view.canConfirm, false); assert.equal(model.confirm(kind), false);
    assert.ok(view.rows.every(row => row.status === 'empty' && row.fields.condition === ''
      && row.fields.control === 'unknown' && row.fields.replicateType === 'unknown'));
  }
  assert.equal(model.view().expanded, false); assert.equal(model.scope().selectedCount, 0);
});

test('only unique exact names on both sides share fields and reviewed facts; case, whitespace, absent, duplicate and blank stay separate', () => {
  const model = createSampleMetadata();
  model.load(preview(['a', 'b'], ['b', 'a'])); model.update('intensity', 1, complete);
  assert.deepEqual(model.view('lfq').rows[1].fields, complete); assert.equal(model.view('lfq').rows[0].fields.condition, '');
  for (const [a, b, shared] of [[['x', 'x', ''], ['x', ''], false], [['Name', 'trail ', 'only'], ['name', 'trail', 'other'], false]]) {
    model.load(preview(a, b)); fill(model); assert.ok(model.view('lfq').rows.every(row => row.fields.condition === '')); assert.equal(shared, false);
  }
  model.load(preview([' ', 'dup', 'dup'], ['unique'])); fill(model); assert.equal(model.view('intensity').canConfirm, false);
  fill(model, 'lfq'); assert.equal(model.confirm('lfq'), true); assert.ok(model.view('intensity').rows.every(row => row.status !== 'confirmed'));
  model.load(preview(['solo'], [])); fill(model); assert.equal(model.confirm('intensity'), true);
});

test('field completeness never auto-confirms; shared edits revoke only affected row and preserve other reviewed facts', () => {
  const model = createSampleMetadata(); model.load(preview()); fill(model);
  assert.ok(model.view().rows.every(row => row.status === 'ready')); assert.equal(model.view().confirmedCount, 0);
  assert.equal(model.confirm('intensity'), true); assert.equal(model.view('lfq').confirmedCount, 2);
  assert.equal(model.update('lfq', 3, {condition: 'Changed'}), true);
  assert.deepEqual(model.view('intensity').rows.map(row => row.status), ['ready', 'confirmed']);
  assert.deepEqual(model.view('lfq').rows.map(row => row.status), ['ready', 'confirmed']);
  assert.equal(model.update('intensity', 2, {condition: complete.condition}), false); assert.equal(model.view().confirmedCount, 1);
});

test('confirmation depends only on the four retained fields; deleted fields cannot re-enter through old patches or batch drafts', () => {
  const model = createSampleMetadata(); model.load(preview(['one']));
  assert.deepEqual(METADATA_FIELDS, ['condition', 'control', 'replicate', 'replicateType']);
  fill(model); assert.equal(model.view().canConfirm, true); assert.equal(model.confirm(), true);
  const before = JSON.stringify(model.view());
  for (const field of ['timeMode', 'timeValue', 'timeUnit']) {
    assert.equal(model.update('intensity', 1, {[field]: 'obsolete value'}), false);
    assert.ok(!Object.hasOwn(model.view().rows[0].fields, field));
    assert.ok(!Object.hasOwn(model.view().rows[0].errors, field));
    model.setBatch(field, {enabled: true, value: 'obsolete value'});
    assert.ok(!Object.hasOwn(model.view().batch, field));
  }
  assert.equal(JSON.stringify(model.view()), before); model.selectAll(true); assert.equal(model.applyBatch(), false);
  assert.equal(model.view().rows[0].status, 'confirmed');
  model.update('intensity', 1, {replicateType: 'unknown'}); assert.equal(model.confirm(), false);
  model.update('intensity', 1, {replicateType: 'biological', control: 'unknown'}); assert.equal(model.confirm(), false);
  model.update('intensity', 1, {control: 'no'}); assert.equal(model.view().rows[0].status, 'ready');
  model.clear(); assert.deepEqual(Object.keys(model.view().rows[0].fields), METADATA_FIELDS);
});

test('validation retains invalid manual text without truncation, rejects controls/length and never mutates frozen views', () => {
  const model = createSampleMetadata(); model.load(preview(['__proto__'])); fill(model);
  model.update('intensity', 1, {condition: '中'.repeat(161)}); assert.equal(model.view().rows[0].errors.condition, 'length');
  assert.equal(model.view().rows[0].fields.condition.length, 161);
  for (const field of ['condition', 'replicate']) { model.update('intensity', 1, {[field]: 'x\u0085y'}); assert.equal(model.view().rows[0].errors[field], 'control'); }
  const view = model.view(); assert.ok(Object.isFrozen(view) && Object.isFrozen(view.rows) && Object.isFrozen(view.rows[0].fields));
  assert.equal(Reflect.set(view.rows[0].fields, 'condition', 'changed'), false); assert.equal({}.polluted, undefined);
});

test('batch scope is explicit by current column index, enables fields individually, preserves disabled fields and clears selection/draft on apply/type changes', () => {
  const model = createSampleMetadata(); model.load(preview()); fill(model); model.confirm();
  assert.equal(model.applyBatch(), false); model.select(1, true); model.setBatch('condition', {enabled: true, value: 'Batch condition'});
  model.setBatch('replicate', {value: 'MUST_NOT_APPLY'}); assert.deepEqual(model.scope(), {selectedCount: 1, linkedCount: 1});
  assert.equal(model.applyBatch(), true); assert.equal(model.view().rows[0].fields.condition, 'Batch condition');
  assert.equal(model.view().rows[0].fields.replicate, 'R-1'); assert.equal(model.view().rows[1].status, 'confirmed');
  assert.equal(model.view('lfq').rows[0].fields.condition, 'Batch condition'); assert.equal(model.scope().selectedCount, 0);
  assert.ok(METADATA_FIELDS.every(field => !model.view().batch[field].enabled));
  model.selectAll(true); model.setBatch('replicate', {enabled: true, clear: true}); model.applyBatch();
  assert.ok(model.view().rows.every(row => row.fields.replicate === '')); assert.equal(model.view().canConfirm, false);
  assert.equal(model.view().rows[0].fields.condition, 'Batch condition');
  model.selectAll(true); model.setBatch('control', {enabled: true, clear: true}); model.setType('lfq');
  assert.equal(model.scope().selectedCount, 0); assert.ok(!model.view('lfq').batch.control.enabled);
  model.setType('intensity'); assert.equal(model.scope().selectedCount, 0);
});

test('metadata review/clear and existing manual group review are independent in both directions', () => {
  const p = preview(), metadata = createSampleMetadata(), groups = createSampleGroups(); metadata.load(p); groups.load(p);
  for (const row of groups.view('intensity').rows) groups.setGroup('intensity', row.index, 'Temporary manual group');
  groups.confirm('intensity'); const before = JSON.stringify(groups.view('intensity'));
  fill(metadata); metadata.confirm(); metadata.update('intensity', 1, {replicate: 'other'}); metadata.clear();
  assert.equal(JSON.stringify(groups.view('intensity')), before); assert.ok(metadata.view().rows.every(row => row.status === 'empty'));
  fill(metadata); metadata.confirm(); const facts = JSON.stringify(metadata.view()); groups.clear();
  assert.equal(JSON.stringify(metadata.view()), facts);
});

function node(doc, tag = 'div') {
  return {ownerDocument: doc, tagName: tag.toUpperCase(), children: [], dataset: {}, attrs: {}, listeners: new Map(), value: '', textContent: '', disabled: false, open: false, checked: false, isConnected: true,
    classList: {toggle() {}}, setAttribute(k, v) { this.attrs[k] = v; }, hasAttribute(k) { return Object.hasOwn(this.attrs, k); },
    append(...children) { this.children.push(...children); }, replaceChildren(...children) { this.children = children; },
    addEventListener(k, fn) { this.listeners.set(k, fn); }, removeEventListener(k) { this.listeners.delete(k); }, emit(k) { if (!this.disabled) this.listeners.get(k)?.({target: this}); }, closest() { return this; }};
}
function dom() { const doc = {createElement: tag => node(doc, tag)}; return {doc, root: node(doc)}; }
const all = root => [root, ...root.children.flatMap(all)];
const find = (root, key, value) => all(root).find(n => n.dataset[key] === String(value));
const field = (root, name, index) => all(root).find(n => n.dataset.smField === name && n.dataset.smIndex === String(index));
function edit(root, name, index, value) { const input = field(root, name, index); input.value = value; input.emit(input.tagName === 'SELECT' ? 'change' : 'input'); }
function fillUI(root, index, values = complete) { for (const [name, value] of Object.entries(values)) edit(root, name, index, value); }

test('metadata editor has safe bilingual text, no prefilled design, manual confirmation, explicit batch targets and full listener cleanup', () => {
  const model = createSampleMetadata(), {root} = dom(); model.load(preview(['<script>bad</script>']));
  const dispose = mountSampleMetadata(root, model, 'intensity', (_zh, en) => en);
  assert.equal(root.children[0].open, false); assert.equal(find(root, 'smAction', 'confirm').disabled, true);
  assert.ok(all(root).some(n => n.textContent === '<script>bad</script>')); assert.equal(all(root).filter(n => ['SCRIPT', 'IMG', 'CANVAS'].includes(n.tagName)).length, 0);
  assert.equal(field(root, 'replicateType', 1).value, 'unknown');
  assert.equal(all(root).filter(n => n.dataset.smField).length, 4);
  assert.equal(all(root).filter(n => n.dataset.smBatchField).length, 4);
  assert.ok(!all(root).some(n => /时间因素|时间值|时间单位/.test(n.textContent) || ['timeMode', 'timeValue', 'timeUnit'].includes(n.dataset.smField)));
  assert.ok(all(root).filter(n => n.dataset.smField).every(n => n.attrs['aria-invalid'] === 'false'));
  edit(root, 'condition', 1, 'invalid\u0000content'); assert.equal(field(root, 'condition', 1).attrs['aria-invalid'], 'true');
  fillUI(root, 1); assert.equal(find(root, 'smAction', 'confirm').disabled, false);
  assert.equal(model.view().confirmedCount, 0); find(root, 'smAction', 'confirm').emit('click'); assert.equal(model.view().confirmedCount, 1);
  const selection = find(root, 'smSelect', 1); selection.checked = true; selection.emit('change');
  const enabled = find(root, 'smBatchEnable', 'condition'); enabled.checked = true; enabled.emit('change');
  const batch = find(root, 'smBatchField', 'condition'); batch.value = 'Explicit batch'; batch.emit('input');
  find(root, 'smAction', 'apply').emit('click'); assert.equal(model.view().rows[0].fields.condition, 'Explicit batch'); assert.equal(selection.checked, false);
  assert.ok(all(root).some(n => n.textContent.includes('replicate authenticity are not validated')));
  const old = all(root); dispose(); assert.ok(old.every(n => n.listeners.size === 0)); assert.equal(root.children.length, 0);
});

function workspace() {
  const {root, doc} = dom(), fixed = new Map(), area = node(doc), tabs = ['table', 'fasta', 'join'].map(itab => Object.assign(node(doc, 'button'), {dataset: {itab}}));
  let html = '', metadata = null, groups = null;
  Object.defineProperty(area, 'innerHTML', {get: () => html, set(value) { html = value; metadata = value.includes('data-sample-metadata') ? node(doc) : null; groups = value.includes('data-sample-groups') ? node(doc) : null; }});
  root.querySelector = selector => {
    if (selector === '[data-iw-content]') return area;
    if (selector === '[data-sample-metadata]') return metadata;
    if (selector === '[data-sample-groups]') return groups;
    if (!fixed.has(selector)) fixed.set(selector, node(doc)); return fixed.get(selector);
  };
  root.querySelectorAll = () => tabs; const dispose = mountImportWorkspace(root);
  return {root, area, dispose, get metadata() { return metadata; }, get groups() { return groups; },
    async select(kind, file) {
      root.onchange({target: {dataset: {ufile: kind}, files: [file]}});
      for (let n = 0; n < 150; n++) { await settle(); const status = root.querySelector(`[data-state="${kind}"]`).dataset.status; if (['ready', 'error'].includes(status)) return status; }
      throw Error('READ_DID_NOT_FINISH');
    },
    click(dataset) { root.onclick({target: Object.assign(node(doc, 'button'), {dataset})}); },
    quant(value) { root.onchange({target: {dataset: {}, value, hasAttribute: key => key === 'data-iw-quant'}}); },
  };
}
const tiny = () => new File(['Protein IDs\tIntensity one\tIntensity two\tLFQ intensity one\tLFQ intensity two\nP1;P11\t0\t\t3\t4\nP2\t5\t6\t7\t8\n'], 'same.txt');
const fasta = () => new File(['>P1\nACGT\n>P11\nAAAA\n>P2\nGGGG\n'], 'same.fasta');

test('actual controller preserves shared facts across quantification, preview and FASTA changes; type change clears batch drafts, metadata clear preserves groups', async () => {
  const savedFetch = globalThis.fetch, storage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage'), calls = [];
  globalThis.fetch = async url => { calls.push(url); return {ok: true, json: async () => ({status: 'prototype'})}; };
  Object.defineProperty(globalThis, 'localStorage', {configurable: true, get() { throw Error('NO_STORAGE'); }});
  const w = workspace();
  try {
    await w.select('table', tiny()); const original = w.area.innerHTML; w.metadata.children[0].open = true;
    fillUI(w.metadata, 1); fillUI(w.metadata, 2); find(w.metadata, 'smAction', 'confirm').emit('click');
    const groupInputs = all(w.groups).filter(n => n.tagName === 'INPUT'); for (const input of groupInputs) { input.value = 'Manual group'; input.emit('input'); }
    find(w.groups, 'sgAction', 'confirm').emit('click');
    const selected = find(w.metadata, 'smSelect', 1); selected.checked = true; selected.emit('change');
    const enabled = find(w.metadata, 'smBatchEnable', 'replicate'); enabled.checked = true; enabled.emit('change');
    w.quant('lfq'); assert.equal(field(w.metadata, 'condition', 3).value, complete.condition);
    assert.equal(find(w.metadata, 'smBatchEnable', 'replicate').checked, false); assert.equal(find(w.metadata, 'smSelect', 3).checked, false);
    assert.equal(w.metadata.children[0].open, true); assert.equal(w.metadata.children[0].dataset.metadataState, 'confirmed');
    edit(w.metadata, 'condition', 3, 'Updated'); w.quant('intensity'); assert.equal(field(w.metadata, 'condition', 1).value, 'Updated');
    assert.ok(all(w.groups).some(n => n.textContent.includes('已确认')));
    await w.select('fasta', fasta()); assert.equal(w.metadata, null); w.click({itab: 'table'}); assert.equal(field(w.metadata, 'condition', 1).value, 'Updated');
    w.click({iw: 'join'}); const joined = w.area.innerHTML; w.click({itab: 'table'});
    find(w.metadata, 'smAction', 'clear').emit('click'); assert.equal(field(w.metadata, 'condition', 1).value, '');
    assert.ok(all(w.groups).some(n => n.textContent.includes('已确认'))); assert.equal(w.area.innerHTML, original);
    w.click({iw: 'join'}); assert.equal(w.area.innerHTML, joined); assert.deepEqual(calls, ['/api/health']);
  } finally { w.dispose(); globalThis.fetch = savedFetch; if (storage) Object.defineProperty(globalThis, 'localStorage', storage); else delete globalThis.localStorage; }
});

test('actual controller clears metadata on same-name reselect, missing/cancelled input, validation failure, clear and unmount', async () => {
  const saved = globalThis.fetch; globalThis.fetch = async () => ({ok: true, json: async () => ({status: 'prototype'})}); const w = workspace();
  async function prepared() { await w.select('table', tiny()); fillUI(w.metadata, 1); }
  async function blankAgain() { await w.select('table', tiny()); assert.equal(field(w.metadata, 'condition', 1).value, ''); }
  try {
    await prepared(); await blankAgain();
    await prepared(); assert.equal(await w.select('table', new File(['bad'], 'same.txt')), 'error'); assert.equal(w.metadata, null); await blankAgain();
    await prepared(); w.root.onchange({target: {dataset: {ufile: 'table'}, files: []}}); assert.equal(w.metadata, null); await blankAgain();
    await prepared(); let release, cancelled = false; const pending = new Promise(resolve => { release = resolve; });
    const reader = {read: () => pending, cancel: async () => { cancelled = true; release({done: true}); }, releaseLock() {}};
    w.root.onchange({target: {dataset: {ufile: 'table'}, files: [{name: 'same.txt', size: 1, stream: () => ({getReader: () => reader})}]}});
    assert.equal(w.metadata, null); w.click({cancel: 'table'}); await settle(); assert.equal(cancelled, true); await blankAgain();
    await prepared(); w.click({iw: 'clear'}); assert.equal(w.metadata, null); await blankAgain();
    await prepared(); const old = all(w.metadata); w.dispose(); assert.ok(old.every(n => n.listeners.size === 0)); assert.equal(w.root.onchange, null); assert.equal(w.root.onclick, null);
    await settle(); assert.equal(w.metadata.children.length, 0);
  } finally { w.dispose(); globalThis.fetch = saved; }
});

test('late old file stream cannot restore old metadata after a new file has loaded', async () => {
  const saved = globalThis.fetch; globalThis.fetch = async () => ({ok: true, json: async () => ({status: 'prototype'})}); const w = workspace(); let release;
  const pending = new Promise(resolve => { release = resolve; });
  try {
    await w.select('table', tiny()); fillUI(w.metadata, 1);
    const reader = {read: () => pending, cancel: async () => {}, releaseLock() {}};
    w.root.onchange({target: {dataset: {ufile: 'table'}, files: [{name: 'same.txt', size: 1, stream: () => ({getReader: () => reader})}]}});
    await w.select('table', tiny()); edit(w.metadata, 'condition', 1, 'New file facts');
    release({done: false, value: new TextEncoder().encode('invalid late content')}); await settle(); await settle();
    assert.equal(field(w.metadata, 'condition', 1).value, 'New file facts');
  } finally { w.dispose(); globalThis.fetch = saved; }
});

test('authorized real TXT/FASTA keep 2252x182 values, 21 shared sample identities, all ID links and original file hashes', async () => {
  const paths = [process.env.TXT_PREVIEW_INPUT, process.env.FASTA_PREVIEW_INPUT]; assert.ok(paths.every(Boolean), 'Explicit real inputs required; no Demo fallback');
  const bytes = await Promise.all(paths.map(path => readFile(path))), expected = ['301bc95ff440a4ea70912fb5ede26aa54cb15f2cb06a9efeed6e55fd749f95fb', '8cb4a7994a80b6fecef0310e288c4763261026989b73a4e467d3310aad5daba5'];
  assert.ok(bytes.every((value, i) => digest(value) === expected[i]), 'Authorized real input hash mismatch');
  const parsed = parseProteinGroups(bytes[0].toString('utf8')), before = JSON.stringify(parsed), model = createSampleMetadata(), groups = createSampleGroups(); model.load(parsed); groups.load(parsed);
  assert.deepEqual([parsed.rowCount, parsed.columnCount, parsed.intensityColumns.length, parsed.lfqColumns.length], [2252, 182, 21, 21]);
  assert.equal(new Set(parsed.intensityColumns.map(c => c.sampleName)).size, 21); assert.ok(parsed.intensityColumns.every((c, i) => c.sampleName === parsed.lfqColumns[i].sampleName));
  assert.equal(model.view('intensity').rows.length, 21); assert.equal(model.view('lfq').rows.length, 21); assert.ok(model.view().rows.every(row => row.status === 'empty'));
  const table = parseProteinGroupsForAssociation(bytes[0].toString('utf8')), fastaParser = createFastaAssociationParser(), summary = createFastaPreviewParser();
  const text = bytes[1].toString('utf8'); for (let i = 0; i < text.length; i += 65536) { fastaParser.push(text.slice(i, i + 65536)); summary.push(text.slice(i, i + 65536)); }
  const index = fastaParser.finish(), info = summary.finish();
  try {
    const links = JSON.stringify(associateProteinGroups(table, index)), groupBefore = JSON.stringify(groups.view('intensity'));
    model.selectAll(true); assert.deepEqual(model.scope(), {selectedCount: 21, linkedCount: 21});
    for (const [field, value] of Object.entries({...complete, condition: 'Acceptance temporary fact', replicateType: 'unknown'})) model.setBatch(field, {enabled: true, value});
    model.applyBatch(); assert.equal(model.confirm(), false); assert.ok(model.view('lfq').rows.every(row => row.fields.condition === 'Acceptance temporary fact' && row.status === 'draft'));
    assert.equal(JSON.stringify(parsed), before); assert.equal(JSON.stringify(groups.view('intensity')), groupBefore);
    assert.equal(JSON.stringify(associateProteinGroups(table, index)), links);
    assert.deepEqual([info.recordCount, info.totalSequenceLength], [38387, 16367314]);
    const joined = associateProteinGroups(table, index); assert.deepEqual([joined.summary.rowCount, joined.summary.allMatchRows, joined.summary.partialMatchRows, joined.summary.zeroMatchRows], [2252, 2223, 0, 29]);
    model.clear(); assert.ok(model.view().rows.every(row => row.status === 'empty'));
    assert.ok((await Promise.all(paths.map(path => readFile(path)))).every((value, i) => digest(value) === expected[i]), 'Input hashes changed');
  } finally { model.reset(); releaseAssociationInputs(table, index); }
});

test('authorized real controller uses both actual files, retains facts through FASTA replacement and resets them at same-name TXT selection start', async () => {
  const paths = [process.env.TXT_PREVIEW_INPUT, process.env.FASTA_PREVIEW_INPUT]; assert.ok(paths.every(Boolean), 'Real input paths required'); const bytes = await Promise.all(paths.map(path => readFile(path)));
  const saved = globalThis.fetch, calls = []; globalThis.fetch = async url => { calls.push(url); return {ok: true, json: async () => ({status: 'prototype'})}; }; const w = workspace();
  try {
    await w.select('table', new File([bytes[0]], 'authorized.txt')); assert.equal(all(w.metadata).filter(n => n.dataset.smField === 'condition').length, 21);
    const firstInput = all(w.metadata).find(n => n.dataset.smField === 'condition'), firstIndex = firstInput.dataset.smIndex; edit(w.metadata, 'condition', firstIndex, 'Acceptance temporary fact');
    await w.select('fasta', new File([bytes[1]], 'authorized.fasta')); assert.ok(w.area.innerHTML.includes('38387'));
    w.click({itab: 'table'}); assert.equal(field(w.metadata, 'condition', firstIndex).value, 'Acceptance temporary fact');
    w.quant('lfq'); assert.ok(all(w.metadata).some(n => n.dataset.smField === 'condition' && n.value === 'Acceptance temporary fact'));
    await w.select('fasta', new File([bytes[1]], 'authorized.fasta')); w.click({itab: 'table'}); assert.ok(all(w.metadata).some(n => n.dataset.smField === 'condition' && n.value === 'Acceptance temporary fact'));
    const pending = w.select('table', new File([bytes[0]], 'authorized.txt')); assert.equal(w.metadata, null); await pending;
    assert.ok(all(w.metadata).filter(n => n.dataset.smField === 'condition').every(n => n.value === ''));
    assert.deepEqual(calls, ['/api/health']); w.click({iw: 'clear'}); assert.equal(w.metadata, null);
  } finally { w.dispose(); globalThis.fetch = saved; }
});
