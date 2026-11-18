import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {mountPrivateImportReport} from './private-import-report-view.js';

const binding = {project_id: '11111111-1111-4111-8111-111111111111', raw_file_id: '22222222-2222-4222-8222-222222222222', expected_raw_sha256: 'a'.repeat(64)};
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; }
function ready(seed = 7) {
  const columns = Array.from({length: 3 + seed % 4}, (_, ordinal) => ({ordinal, name_raw: '原始列 ' + ordinal,
    quant_family: ordinal === 0 ? null : ordinal % 2 ? 'Intensity' : 'LFQ intensity', sample_label_raw: ordinal === 0 ? null : '  样本_' + ordinal + '  '}));
  return {phase: 'ready', source_verified: true, report_verified: true, report_saved: true, binding: {...binding}, source_filename: 'synthetic-' + seed + '.txt', report_version_id: 'report-version-' + seed,
    report: {source: {...binding}, status: 'parsed', source_complete: true, observed_raw_sha256: binding.expected_raw_sha256, parser_contract_version: 'fixture-parser-v' + seed,
      association_status: 'not_requested', scientific_analysis_status: 'not_started', row_count: seed + 2, column_count: columns.length,
      intensity_sample_count: columns.filter(c => c.quant_family === 'Intensity').length, lfq_sample_count: columns.filter(c => c.quant_family === 'LFQ intensity').length,
      quant_exact_zero_string_cells: seed % 3, quant_empty_string_cells: seed % 2, columns}};
}
function model(initial) {
  let state = initial; const listeners = new Set(), calls = {start: 0, check: 0}; let operation = Promise.resolve();
  return {calls, listeners, status: () => state, subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    start() { calls.start++; return operation; }, check() { calls.check++; return operation; },
    set(v) { state = v; for (const fn of listeners) fn(); }, pending(p) { operation = p; }};
}
function dom() {
  const create = tag => { let ownText = ''; return {tag, children: [], listeners: new Map(), attrs: {}, hidden: false, disabled: false,
    get textContent() { return ownText + this.children.map(c => c.textContent).join(''); }, set textContent(v) { ownText = String(v); this.children = []; },
    setAttribute(k, v) { this.attrs[k] = v; }, append(...children) { this.children.push(...children); }, replaceChildren(...children) { ownText = ''; this.children = children; },
    addEventListener(k, f) { this.listeners.set(k, f); }, removeEventListener(k, f) { if (this.listeners.get(k) === f) this.listeners.delete(k); }}; };
  const root = create('root'); root.ownerDocument = {createElement: create};
  const walk = n => [n, ...n.children.flatMap(walk)]; return {root, all: () => walk(root)};
}
const mount = (v, en = false) => { const m = model(v), d = dom(), dispose = mountPrivateImportReport(d.root, m, {binding, t: en ? (_, english) => english : zh => zh}); return {...d, m, dispose}; };
const action = (d, name) => d.all().find(n => n.tag === 'button' && n.textContent === name);

test('ready uses dynamic verified values, ordered original names and separate quantification-only zero/empty counts, without automatic callbacks', () => {
  for (const seed of [3, 8, 13]) {
    const value = ready(seed), before = JSON.stringify(value), d = mount(value);
    assert.match(d.root.textContent, /导入报告已保存/); assert.deepEqual(d.all().filter(n => n.tag === 'dd' && n.className === '').slice(0, 4).map(n => n.textContent), [value.report.row_count, value.report.column_count, value.report.intensity_sample_count, value.report.lfq_sample_count].map(String));
    assert.ok(d.root.textContent.includes('定量列的原始“0”数量: ' + value.report.quant_exact_zero_string_cells));
    assert.ok(d.root.textContent.includes('定量列空字符串数量: ' + value.report.quant_empty_string_cells));
    const rows = d.all().find(n => n.tag === 'tbody').children;
    assert.deepEqual(rows.map(n => n.children[1].textContent), value.report.columns.map(c => c.name_raw));
    assert.deepEqual(rows.slice(1).map(n => n.children[3].textContent), value.report.columns.slice(1).map(c => c.sample_label_raw));
    assert.equal(d.m.calls.start, 0); assert.equal(d.m.calls.check, 0); assert.equal(JSON.stringify(value), before);
    assert.match(d.root.textContent, /未关联 FASTA。本区展示导入检查结果，后续分析请查看相应模块。/); d.dispose();
  }
});

test('missing/unverified model and malformed/mismatched ready report never show success, counts or provenance', () => {
  for (const change of [v => { v.source_verified = false; }, v => { v.report_verified = false; }, v => { v.report_saved = false; }, v => { v.binding.raw_file_id = binding.project_id; },
    v => { v.report.source.expected_raw_sha256 = 'b'.repeat(64); }, v => { v.report.observed_raw_sha256 = 'b'.repeat(64); }, v => { v.report.column_count++; },
    v => { v.report.columns.reverse(); }, v => { v.report.intensity_sample_count++; }, v => { v.report.quant_empty_string_cells = -1; }, v => { v.report.quant_exact_zero_string_cells = 100000; }, v => { v.report_version_id = null; }]) {
    const v = ready(), filename = v.source_filename; change(v); const d = mount(v);
    assert.doesNotMatch(d.root.textContent, /导入报告已保存/); assert.equal(d.root.textContent.includes(filename), false); assert.equal(d.all().some(n => n.tag === 'dd'), false); assert.equal(action(d, '解析此文件').disabled, true); d.dispose();
  }
  for (const m of [null, {}, {status() { throw Error('private'); }, subscribe() {}, start() {}, check() {}}]) {
    const d = dom(), stop = mountPrivateImportReport(d.root, m, {binding}); assert.equal(action(d, '解析此文件').disabled, true); assert.doesNotMatch(d.root.textContent, /private|导入报告已保存/); stop();
  }
});

test('all non-ready phases clear a previously ready report; unknown has only explicit read-only check', () => {
  const d = mount(ready()), retained = d.all().find(n => n.tag === 'dd' && n.textContent === ready().source_filename);
  for (const phase of ['idle', 'parsing', 'checking', 'failed', 'unknown', 'unavailable']) {
    d.m.set({...ready(), phase, can_start: true, can_check: true, error: '<secret response>'});
    assert.doesNotMatch(d.root.textContent, /导入报告已保存|secret response|synthetic-/); assert.equal(d.all().some(n => n.tag === 'dd'), false);
    if (phase === 'unknown') { assert.match(d.root.textContent, /结果待确认/); assert.equal(action(d, '解析此文件').hidden, true); assert.equal(action(d, '查询导入结果').hidden, false); }
    if (['parsing', 'checking'].includes(phase)) { assert.equal(action(d, '解析此文件').disabled, true); assert.equal(action(d, '查询导入结果').disabled, true); }
  }
  assert.equal(retained.textContent, ''); assert.equal(d.m.calls.start, 0); assert.equal(d.m.calls.check, 0); d.dispose();
});

test('explicit start and unknown check suppress double invocation even before the model emits busy; disposal suppresses late repaint', async () => {
  for (const kind of ['start', 'check']) {
    const pending = deferred(), d = mount({...ready(), phase: kind === 'start' ? 'idle' : 'unknown', can_start: true, can_check: true}); d.m.pending(pending.promise);
    const button = action(d, kind === 'start' ? '解析此文件' : '查询导入结果'), click = button.listeners.get('click'); click(); click();
    assert.equal(d.m.calls[kind], 1); assert.equal(button.disabled, true); assert.equal(d.root.children[0].attrs['aria-busy'], 'true');
    d.m.set(ready()); assert.doesNotMatch(d.root.textContent, /导入报告已保存/); d.dispose();
    pending.resolve(); await tick(); assert.equal(d.root.textContent, ''); assert.equal(button.listeners.size, 0); assert.equal(d.m.listeners.size, 0);
  }
});

test('dispose clears retained sensitive DOM text/toggle handlers and subscription, and is idempotent', () => {
  const d = mount(ready()), retained = d.all().slice(1), details = retained.filter(n => n.tag === 'details');
  d.dispose(); d.dispose(); assert.deepEqual(d.root.children, []); assert.equal(d.m.listeners.size, 0);
  for (const n of retained) { assert.equal(n.textContent, ''); assert.equal(n.listeners.size, 0); assert.equal(n.ontoggle, null); }
  for (const n of details) assert.equal(n.ontoggle, null);
  d.m.set(ready(4)); assert.equal(d.root.textContent, '');
});

test('XSS and long names stay literal, whitespace/order stay intact, details and scroll region remain keyboard accessible', () => {
  const v = ready(), payload = '<img src=x onerror=alert(1)>.txt'; v.source_filename = payload; v.report.columns[1].name_raw = '<script>literal</script>' + '列'.repeat(600); v.report.columns[1].sample_label_raw = '  <b>sample</b>  ';
  const d = mount(v); assert.ok(d.root.textContent.includes(payload)); assert.ok(d.root.textContent.includes(v.report.columns[1].name_raw));
  assert.equal(d.all().some(n => ['img', 'script', 'b', 'input'].includes(n.tag)), false);
  assert.equal(d.all().filter(n => n.tag === 'details').length, 2); assert.ok(d.all().find(n => n.className === 'pirv-scroll').tabIndex === 0);
  assert.ok(d.all().filter(n => n.tag === 'th').every(n => n.attrs.scope === 'col')); d.dispose();
});

test('English interface uses t, unverified-to-verified notifications enable explicit action without auto start', () => {
  const d = mount({...ready(), source_verified: false, phase: 'idle', can_start: true}, true);
  assert.equal(action(d, 'Parse this file').disabled, true); d.m.set({...ready(), phase: 'idle', can_start: true}); assert.equal(action(d, 'Parse this file').disabled, false);
  d.m.set(ready()); assert.match(d.root.textContent, /Import report saved/); assert.match(d.root.textContent, /Original "0" strings in quantification columns/); assert.match(d.root.textContent, /FASTA not linked/);
  assert.equal(d.m.calls.start, 0); d.dispose();
});

test('component has no network/storage/timer/file/account IO and styles are local with responsive/wrapping/scroll/focus affordances', async () => {
  const js = await readFile(new URL('./private-import-report-view.js', import.meta.url), 'utf8'), css = await readFile(new URL('./private-import-report-view.css', import.meta.url), 'utf8');
  assert.doesNotMatch(js, /fetch\(|XMLHttpRequest|localStorage|sessionStorage|document\.cookie|FileReader|setTimeout\(|setInterval\(|innerHTML/);
  assert.doesNotMatch(js, /2252|38387|20250612proteinGroups|timeMode|timeValue|timeUnit/);
  for (const value of ['overflow:auto', 'overflow-wrap:anywhere', 'white-space:pre-wrap', ':focus-visible', '@media']) assert.ok(css.includes(value));
  assert.doesNotMatch(css, /@import|https?:|(?:^|\n)(?:body|html|button|table|\.panel)\s*\{/);
});

test('an invalidated binding during the pre-action repaint prevents start/check callback invocation', () => {
  for (const kind of ['start', 'check']) {
    const value = {...ready(), phase: kind === 'start' ? 'idle' : 'unknown', can_start: true, can_check: true};
    const m = model(value); let armed = false, reads = 0;
    m.status = () => armed && ++reads > 1 ? {...value, binding: {...binding, raw_file_id: binding.project_id}} : value;
    const d = dom(), stop = mountPrivateImportReport(d.root, m, {binding}); armed = true;
    action(d, kind === 'start' ? '解析此文件' : '查询导入结果').listeners.get('click')();
    assert.equal(m.calls[kind], 0); assert.equal(action(d, '解析此文件').disabled, true); assert.doesNotMatch(d.root.textContent, /导入报告已保存/); stop();
  }
});
