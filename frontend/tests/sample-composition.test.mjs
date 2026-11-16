import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {mountSampleQC, createSampleQCCollector} from '../sample-qc.js';
import {parseProteinGroups} from '../protein-groups.js';
import {parseProteinGroupsForAssociation, releaseAssociationInputs} from '../protein-association.js';

const categories = ['positive', 'zero', 'empty', 'abnormal'];
function row(index, sampleName, counts, prefix = 'Intensity') {
  const [positive, zero, empty, abnormal] = counts;
  return Object.freeze({index, sampleName, columnName: prefix + ' ' + sampleName,
    positive, zero, empty, abnormal, emptyString: empty, whitespace: 0, negative: abnormal, format: 0, nonFinite: 0});
}
const summary = (total, intensity, lfq = []) => Object.freeze({recordCount: total, intensity: Object.freeze(intensity), lfq: Object.freeze(lfq)});
// Minimal DOM port; browser geometry/focus is verified separately in the real preview.
function node(doc, tag = 'div') {
  return {ownerDocument: doc, tagName: tag.toUpperCase(), children: [], dataset: {}, attrs: {}, listeners: new Map(),
    textContent: '', open: false, className: '', setAttribute(k, v) { this.attrs[k] = String(v); },
    append(...items) { this.children.push(...items); }, replaceChildren(...items) { this.children = items; },
    addEventListener(k, fn) { this.listeners.set(k, fn); }, removeEventListener(k) { this.listeners.delete(k); },
    emit(k) { this.listeners.get(k)?.({target: this}); },
  };
}
function dom() { const doc = {createElement: tag => node(doc, tag)}; return node(doc); }
function descendants(root) { return [root, ...root.children.flatMap(descendants)]; }
const chartRows = root => descendants(root).filter(x => x.dataset.compositionColumn !== undefined);
const segments = row => descendants(row).filter(x => x.dataset.compositionCategory !== undefined);
const width = segment => Number(segment.attrs.style.match(/flex:0 0 ([^%]+)%/)[1]);
const allText = root => descendants(root).map(x => x.textContent).join('\n');

test('composition uses unrounded 100% geometry, true zero widths and honest tiny nonzero labels', () => {
  const root = dom();
  let dispose = mountSampleQC(root, summary(4, [row(1, 's', [1, 1, 1, 1])]), 'intensity', new Set());
  assert.deepEqual(segments(chartRows(root)[0]).map(width), [25, 25, 25, 25]); dispose();
  dispose = mountSampleQC(root, summary(10000000, [row(1, 'small', [9999999, 1, 0, 0])]), 'intensity', new Set());
  const items = segments(chartRows(root)[0]);
  assert.equal(width(items[0]), 9999999 / 10000000 * 100);
  assert.equal(width(items[1]), 1 / 10000000 * 100);
  assert.equal(width(items[2]), 0); assert.equal(width(items[3]), 0);
  assert.ok(Math.abs(items.reduce((n, x) => n + width(x), 0) - 100) < 1e-10);
  assert.ok(allText(root).includes('零值: 1 (<0.01%)'));
  assert.ok(allText(root).includes('空值: 0 (0%)'));
  dispose();
});

test('original count table stays first; chart preserves raw index/order/type identities and safe bilingual focusable text', () => {
  const root = dom(), unsafe = '<img src=x onerror=alert(1)>', p = summary(4,
    [row(8, unsafe, [1, 1, 1, 1]), row(2, unsafe, [4, 0, 0, 0]), row(6, '', [0, 4, 0, 0])],
    [row(9, unsafe, [0, 0, 0, 4], 'LFQ intensity')]);
  let dispose = mountSampleQC(root, p, 'intensity', new Set(), (_zh, en) => en);
  assert.equal(root.children[0].className, 'iw-sample-qc');
  assert.equal(root.children[1].className, 'iw-sample-composition');
  assert.equal(root.children[1].open, false);
  const countTables = descendants(root.children[0]).filter(x => x.tagName === 'TABLE');
  assert.equal(countTables.length, 1);
  assert.equal(countTables[0].attrs['aria-label'], 'Per-sample value counts');
  assert.equal(descendants(countTables[0]).filter(x => x.tagName === 'TH').length, 6);
  assert.equal(descendants(countTables[0]).filter(x => x.dataset.qcColumn !== undefined).length, 3);
  const items = chartRows(root);
  assert.deepEqual(items.map(x => Number(x.dataset.compositionColumn)), [8, 2, 6]);
  assert.ok(items.every(x => x.attrs.tabindex === '0' && x.attrs.role === 'group'));
  assert.ok(items.every(x => x.attrs['aria-label'].startsWith('#' + (Number(x.dataset.compositionColumn) + 1) + '; ')
    && x.attrs.title === x.attrs['aria-label']));
  assert.ok(items[0].attrs['aria-label'].includes(unsafe));
  for (const name of ['Positive: 1 (25.00%)', 'Zero: 1 (25.00%)', 'Empty: 1 (25.00%)', 'Anomalies: 1 (25.00%)']) {
    assert.ok(allText(items[0]).includes(name)); assert.ok(items[0].attrs['aria-label'].includes(name));
  }
  assert.ok(allText(root).includes('Sample data composition'));
  assert.equal(descendants(root).filter(x => ['IMG', 'SCRIPT', 'INPUT', 'BUTTON'].includes(x.tagName)).length, 0);
  dispose(); dispose = mountSampleQC(root, p, 'lfq', new Set());
  assert.deepEqual(chartRows(root).map(x => Number(x.dataset.compositionColumn)), [9]);
  assert.deepEqual(segments(chartRows(root)[0]).map(width), [0, 0, 0, 100]); dispose();
});

test('count and chart expansion are independent per type, with complete listener/DOM cleanup', () => {
  const root = dom(), p = summary(4, [row(1, 's', [4, 0, 0, 0])], [row(2, 's', [0, 4, 0, 0])]), expanded = new Set();
  let dispose = mountSampleQC(root, p, 'intensity', expanded);
  root.children[1].open = true; root.children[1].emit('toggle');
  assert.deepEqual([...expanded], ['composition:intensity']);
  const old = descendants(root); dispose();
  assert.ok(old.every(x => x.listeners.size === 0)); assert.equal(root.children.length, 0);
  dispose = mountSampleQC(root, p, 'lfq', expanded);
  assert.equal(root.children[0].open, false); assert.equal(root.children[1].open, false);
  root.children[0].open = true; dispose();
  dispose = mountSampleQC(root, p, 'intensity', expanded);
  assert.equal(root.children[0].open, false); assert.equal(root.children[1].open, true); dispose();
  expanded.clear(); dispose = mountSampleQC(root, p, 'intensity', expanded);
  assert.equal(root.children[0].open, false); assert.equal(root.children[1].open, false); dispose();
  dispose = mountSampleQC(root, null, 'intensity', expanded); assert.equal(root.children.length, 0); dispose();
});

test('mount only reads the frozen count summary, without any added network or storage operation', () => {
  const root = dom(), p = summary(4, [row(1, '__proto__', [1, 1, 1, 1])]), before = JSON.stringify(p);
  const originalFetch = globalThis.fetch;
  const names = ['localStorage', 'sessionStorage', 'indexedDB'];
  const descriptors = names.map(name => Object.getOwnPropertyDescriptor(globalThis, name));
  globalThis.fetch = () => { throw Error('NETWORK_FORBIDDEN'); };
  for (const name of names) Object.defineProperty(globalThis, name, {configurable: true, get() { throw Error('STORAGE_FORBIDDEN'); }});
  try {
    const dispose = mountSampleQC(root, p, 'intensity', new Set());
    assert.equal(JSON.stringify(p), before); assert.equal({}.polluted, undefined); dispose();
  } finally {
    globalThis.fetch = originalFetch;
    names.forEach((name, i) => { if (descriptors[i]) Object.defineProperty(globalThis, name, descriptors[i]); else delete globalThis[name]; });
  }
});

test('authorized real composition: all 42 original columns and exact counts/proportions match the independent real-input reference', async () => {
  const path = process.env.TXT_PREVIEW_INPUT;
  assert.ok(path, 'Explicit authorized TXT path required; no demo fallback');
  const referencePath = process.env.COMPOSITION_REFERENCE_PATH || new URL('../../../sample-qc001/independent-counts.json', import.meta.url);
  const [raw, referenceText] = await Promise.all([readFile(path), readFile(referencePath, 'utf8')]);
  const reference = JSON.parse(referenceText.replace(/^\uFEFF/, ''));
  const hash = createHash('sha256').update(raw).digest('hex');
  assert.equal(hash, '301bc95ff440a4ea70912fb5ede26aa54cb15f2cb06a9efeed6e55fd749f95fb');
  assert.equal(reference.inputSha256, hash); assert.equal(reference.rowCount, 2252);
  const collector = createSampleQCCollector(), text = raw.toString('utf8');
  const parsed = parseProteinGroups(text, 10, {onQuantificationRow: collector.onQuantificationRow});
  assert.equal(parsed.rowCount, reference.rowCount); assert.equal(parsed.columnCount, reference.columnCount);
  const association = parseProteinGroupsForAssociation(text);
  try {
    const counts = collector.finish(parsed), before = JSON.stringify(counts), root = dom();
    let checked = 0;
    for (const type of ['intensity', 'lfq']) {
      const dispose = mountSampleQC(root, counts, type, new Set());
      const expected = reference.samples.filter(x => x.quantification === type), rows = chartRows(root);
      assert.equal(rows.length, 21); assert.equal(expected.length, 21);
      assert.ok(rows.every((item, i) => Number(item.dataset.compositionColumn) === expected[i].columnIndex), 'Original index or order changed');
      for (let i = 0; i < rows.length; i++) {
        const ref = expected[i], expectedCounts = [ref.positive, ref.zero, ref.blank, ref.abnormal], parts = segments(rows[i]);
        assert.equal(parts.length, 4); assert.deepEqual(parts.map(x => x.dataset.compositionCategory), categories);
        assert.deepEqual(parts.map(x => Number(x.dataset.compositionCount)), expectedCounts);
        assert.ok(parts.every((x, k) => width(x) === expectedCounts[k] / 2252 * 100), 'Unrounded geometry differs');
        assert.ok(Math.abs(parts.reduce((n, x) => n + width(x), 0) - 100) < 1e-10);
        assert.ok(rows[i].attrs['aria-label'].includes(ref.sampleName) && rows[i].attrs['aria-label'].includes(ref.columnName), 'Original labels changed');
        checked++;
      }
      dispose();
    }
    assert.equal(checked, 42); assert.equal(JSON.stringify(counts), before);
    assert.equal(createHash('sha256').update(await readFile(path)).digest('hex'), hash);
  } finally { collector.abort(); releaseAssociationInputs(association); }
});
