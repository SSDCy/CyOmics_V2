import test from 'node:test';
import assert from 'node:assert/strict';
import {mountQualityFlagFilter} from '../quality-flag-filter.js';
import {dom, deferred, flush} from './support/quality-filter-dom.mjs';

const flags = {reverse: false, potentialContaminant: false, onlyIdentifiedBySite: false};
const availableFlags = {reverse: true, potentialContaminant: true, onlyIdentifiedBySite: true};
const base = () => ({featureEnabled: true, contextKey: 'synthetic-owner:project-A:version-1:Intensity:revision-1', sourceVersion: 'synthetic-version-1', quantFamily: 'Intensity', flags: {...flags}, availableFlags: {...availableFlags}, busy: false, onChange() {}, onApply() {}});
const result = (props, extra = {}) => ({contextKey: props.contextKey, sourceVersion: props.sourceVersion, quantFamily: props.quantFamily, flags: {...props.flags}, status: 'succeeded', counts: {before: 31, after: 19, removed: 12}, reasonCounts: {reverse: 8, potentialContaminant: 7, onlyIdentifiedBySite: 4}, overlapCount: 5, ...extra});
function setup(input = base()) { const d = dom(), ui = mountQualityFlagFilter(d.root, input); return {d, ui, button: () => d.by('data-qff-action', 'apply'), flag: key => d.by('data-qff-flag', key), count: key => d.by('data-qff-count', key)?.textContent, text: () => d.root.textContent}; }

test('default off creates no DOM, callbacks, upload inputs or fake results', () => {
  for (const value of [undefined, false, 1, 'true']) {
    const d = dom(); let calls = 0; const ui = mountQualityFlagFilter(d.root, {featureEnabled: value, onApply() { calls++; }, onChange() { calls++; }});
    assert.equal(d.created.length, 1); assert.equal(d.root.children.length, 0); assert.equal(calls, 0); assert.equal(d.listenerCount(), 0); ui.dispose();
  }
});
test('three switches start false and no action occurs on mount, update or language change', () => {
  let calls = 0; const p = base(); p.onApply = p.onChange = () => { calls++; }; const h = setup(p);
  assert.equal(h.d.all().filter(n => n.tag === 'input').length, 3);
  for (const key of Object.keys(flags)) assert.equal(h.flag(key).checked, false);
  h.ui.update({...p, lang: 'en'}); assert.match(h.text(), /Quality flag filtering/); assert.equal(calls, 0); assert.equal(h.count('before'), 'Pending calculation');
  assert.equal(h.d.all().some(n => n.type === 'file'), false); assert.equal(h.d.all().some(n => ['a', 'form'].includes(n.tag)), false);
});
test('missing backend callback disables apply and cannot fake success even under synthetic click dispatch', async () => {
  const p = base(); p.onApply = null; const h = setup(p); assert.equal(h.button().disabled, true); assert.match(h.text(), /尚未接入/); h.button().fire('click'); await flush(); assert.equal(h.count('after'), '待计算'); assert.doesNotMatch(h.text(), /已就绪/);
});
test('switches are controlled; change emits one immutable proposal and requires explicit host update', () => {
  const p = base(), calls = []; p.onChange = request => calls.push(request); const h = setup(p);
  h.flag('reverse').checked = true; h.flag('reverse').fire('change'); assert.equal(calls.length, 1); assert.equal(calls[0].flags.reverse, true); assert.equal(h.flag('reverse').checked, false);
  assert.ok(Object.isFrozen(calls[0])); assert.ok(Object.isFrozen(calls[0].flags)); assert.deepEqual(Object.keys(calls[0]).sort(), ['contextKey', 'flags', 'quantFamily', 'sourceVersion']);
  h.ui.update({...p, flags: calls[0].flags}); assert.equal(h.flag('reverse').checked, true);
});
test('synchronous controlled host update is supported without resetting the chosen flag', () => {
  const p = base(); let h; p.onChange = request => { p.flags = request.flags; h.ui.update(p); }; h = setup(p); h.flag('onlyIdentifiedBySite').checked = true; h.flag('onlyIdentifiedBySite').fire('change'); assert.equal(h.flag('onlyIdentifiedBySite').checked, true);
});
test('missing onChange keeps controls read-only and explains the state', () => {
  const p = base(); p.onChange = null; const h = setup(p); for (const key of Object.keys(flags)) assert.equal(h.flag(key).disabled, true); assert.match(h.text(), /当前为只读/); h.flag('reverse').checked = true; h.flag('reverse').fire('change'); assert.equal(h.flag('reverse').checked, false);
});
for (const value of [false, null, undefined]) test('unavailable flag is disabled and explained without being treated as empty: ' + value, () => {
  const p = base(); let calls = 0; p.onChange = () => calls++; p.availableFlags.reverse = value; const h = setup(p); assert.equal(h.flag('reverse').disabled, true); assert.match(h.text(), value === false ? /来源缺少此列/ : /来源列尚未确认/); h.flag('reverse').checked = true; h.flag('reverse').fire('change'); assert.equal(calls, 0); assert.equal(h.flag('reverse').checked, false); assert.equal(h.button().disabled, false);
});
test('selected missing column blocks apply without silently turning that controlled flag off', () => {
  const p = base(); let calls = 0; p.flags.reverse = true; p.availableFlags.reverse = false; p.onApply = () => calls++; p.result = result(p); const h = setup(p); assert.equal(h.flag('reverse').checked, true); assert.equal(h.flag('reverse').disabled, true); assert.equal(h.button().disabled, true); assert.equal(h.count('before'), '待计算'); h.button().fire('click'); assert.equal(calls, 0); assert.match(h.text(), /已选标记列缺失/);
});
test('all unavailable flags block requests and display pending counters rather than zero', () => {
  const p = base(); p.availableFlags = {reverse: false, potentialContaminant: null}; const h = setup(p); assert.equal(h.button().disabled, true); assert.match(h.text(), /尚无可用质量标记列/); for (const key of ['before', 'after', 'removed', 'overlap']) assert.equal(h.count(key), '待计算');
});
for (const change of [{contextKey: null}, {contextKey: ' '}, {sourceVersion: null}, {quantFamily: 'lfq'}, {busy: 'false'}, {flags: {reverse: 'true'}}]) test('malformed required source/config blocks apply: ' + JSON.stringify(change), () => {
  const p = {...base(), ...change}; let calls = 0; p.onApply = () => calls++; const h = setup(p); assert.equal(h.button().disabled, true); h.button().fire('click'); assert.equal(calls, 0);
});
test('all-false explicit apply is allowed, passes original binding and never auto-filters', async () => {
  const p = base(), calls = []; p.onApply = request => calls.push(request); const h = setup(p); h.button().click(); assert.equal(calls.length, 1); assert.deepEqual(calls[0].flags, flags); assert.equal(calls[0].sourceVersion, p.sourceVersion); assert.equal(calls[0].quantFamily, 'Intensity'); assert.equal(calls[0].contextKey, p.contextKey); await flush(); assert.match(h.text(), /等待宿主更新任务状态/); assert.equal(h.count('before'), '待计算');
});
test('pending callback and host busy prevent repeated calls even with direct event dispatch', async () => {
  const held = deferred(), p = base(); let calls = 0; p.onApply = () => { calls++; return held.promise; }; const h = setup(p); h.button().fire('click'); h.button().fire('click'); assert.equal(calls, 1); assert.equal(h.button().disabled, true);
  for (const key of Object.keys(flags)) assert.equal(h.flag(key).disabled, true);
  h.ui.update({...p, lang: 'en'}); h.button().fire('click'); assert.equal(calls, 1); held.resolve(); await flush(); h.ui.update({...p, busy: true}); h.button().fire('click'); assert.equal(calls, 1); assert.equal(h.button().disabled, true);
});
for (const status of ['queued', 'running']) test('backend ' + status + ' is busy and does not show premature counts', () => {
  const p = base(); let calls = 0; p.onApply = () => calls++; p.result = result(p, {status}); const h = setup(p); assert.equal(h.button().disabled, true); assert.equal(h.count('before'), '待计算'); h.button().fire('click'); assert.equal(calls, 0); assert.match(h.text(), status === 'queued' ? /排队中/ : /运行中/);
});
test('even a returned succeeded DTO is ignored until the host explicitly supplies matching result props', async () => {
  const p = base(); const dto = result(p); p.onApply = () => Promise.resolve(dto); const h = setup(p); h.button().click(); await flush(); assert.equal(h.count('removed'), '待计算'); assert.doesNotMatch(h.text(), /结果已就绪/); h.ui.update({...p, result: dto}); assert.equal(h.count('removed'), '12'); assert.match(h.text(), /结果已就绪/);
});
test('counts render only provided backend values including zero, with no difference or reason summation', () => {
  const p = base(); p.result = result(p, {counts: {before: 100, after: 80, removed: 7}, reasonCounts: {reverse: 0, potentialContaminant: 9, onlyIdentifiedBySite: null}, overlapCount: 3}); const h = setup(p);
  assert.equal(h.count('before'), '100'); assert.equal(h.count('after'), '80'); assert.equal(h.count('removed'), '7'); assert.equal(h.count('reverse'), '0'); assert.equal(h.count('potentialContaminant'), '9'); assert.equal(h.count('onlyIdentifiedBySite'), '待计算'); assert.equal(h.count('overlap'), '3'); assert.match(h.text(), /不能相加/);
});
for (const value of [null, undefined, -1, NaN, Infinity, 1.5, Number.MAX_SAFE_INTEGER + 1, '0', false]) test('invalid/missing DTO counts remain pending: ' + String(value), () => {
  const p = base(); p.result = result(p, {counts: {before: value, after: 0}, reasonCounts: {reverse: value}, overlapCount: value}); const h = setup(p); assert.equal(h.count('before'), '待计算'); assert.equal(h.count('after'), '0'); assert.equal(h.count('removed'), '待计算'); assert.equal(h.count('reverse'), '待计算'); assert.equal(h.count('overlap'), '待计算');
});
for (const mismatch of [{contextKey: 'other-owner'}, {sourceVersion: 'other-version'}, {quantFamily: 'LFQ'}, {flags: {...flags, reverse: true}}, {flags: {}}]) test('result binding mismatch never renders prior counts: ' + JSON.stringify(mismatch), () => {
  const p = base(); p.result = result(p, mismatch); const h = setup(p); assert.equal(h.count('before'), '待计算'); assert.doesNotMatch(h.text(), /结果已就绪/);
});
for (const change of [{contextKey: 'synthetic-owner-B:project-A:revision-2'}, {contextKey: 'synthetic-owner:project-B:revision-2'}, {sourceVersion: 'synthetic-version-2'}, {quantFamily: 'LFQ'}, {flags: {...flags, reverse: true}}]) test('context/version/family/flags change clears displayed result and fences late rejection: ' + JSON.stringify(change), async () => {
  const held = deferred(), p = base(); p.onApply = () => held.promise; p.result = result(p); const h = setup(p); assert.equal(h.count('before'), '31'); h.button().click(); const oldValues = h.d.all().filter(n => n.attrs['data-qff-count']); h.ui.update({...p, ...change}); assert.equal(h.count('before'), '待计算'); held.reject(Error('PRIVATE_OLD_CONTEXT_ERROR')); await flush(); assert.doesNotMatch(h.text(), /PRIVATE|请求结果未确认/); assert.equal(h.button().disabled, false); assert.ok(oldValues.every(n => n.textContent === '待计算'));
});
test('late completion cannot clear a new context pending operation or inject results', async () => {
  const a = deferred(), b = deferred(), p = base(); let calls = 0; p.onApply = () => (++calls === 1 ? a.promise : b.promise); const h = setup(p); h.button().click(); const next = {...p, contextKey: 'synthetic-owner:project-B:revision-2'}; h.ui.update(next); h.button().click(); a.resolve(result(p)); await flush(); assert.equal(h.button().disabled, true); assert.equal(h.count('before'), '待计算'); b.resolve(); await flush(); assert.equal(h.button().disabled, false); assert.equal(calls, 2);
});
test('callback failure is sanitized, never automatically retried, and accepts one explicit retry', async () => {
  const p = base(); let calls = 0; p.onApply = () => { calls++; return Promise.reject(Error('PRIVATE_ENDPOINT_TOKEN')); }; const h = setup(p); h.button().click(); await flush(); assert.equal(calls, 1); assert.match(h.text(), /不会自动重发/); assert.doesNotMatch(h.text(), /PRIVATE_ENDPOINT_TOKEN/); assert.equal(h.button().disabled, false); assert.equal(h.button().textContent, '重试应用过滤'); await flush(); assert.equal(calls, 1); h.button().click(); await flush(); assert.equal(calls, 2); assert.equal(h.count('before'), '待计算');
});
test('synchronous callback throw and asynchronous onChange failure remain generic and retryable', async () => {
  const p = base(); p.onApply = () => { throw Error('PRIVATE_SYNC'); }; p.onChange = () => Promise.reject(Error('PRIVATE_SETTINGS')); const h = setup(p); h.button().click(); assert.match(h.text(), /请求结果未确认/); h.flag('reverse').checked = true; h.flag('reverse').fire('change'); await flush(); assert.match(h.text(), /未能确认设置变更/); assert.doesNotMatch(h.text(), /PRIVATE/); assert.equal(h.flag('reverse').checked, false);
});
test('source and original host nodes stay untouched; user text is rendered literally without markup', () => {
  const d = dom(), original = d.doc.createElement('input'); original.type = 'file'; original.value = 'ORIGINAL_UPLOAD_SENTINEL'; d.root.append(original); const p = base(); p.sourceVersion = '<img src=x onerror=PRIVATE_SOURCE>'; p.result = result(p); const before = JSON.stringify(p); const ui = mountQualityFlagFilter(d.root, p);
  assert.equal(original.parentNode, d.root); assert.equal(original.value, 'ORIGINAL_UPLOAD_SENTINEL'); assert.match(d.root.textContent, /<img src=x/); assert.equal(d.all().filter(n => n.tag === 'input' && n.type === 'file').length, 1); assert.equal(d.all().some(n => ['img', 'script', 'iframe'].includes(n.tag)), false); assert.equal(JSON.stringify(p), before); assert.doesNotMatch(d.root.textContent, /synthetic-owner/); ui.dispose(); assert.equal(d.root.children.length, 1); assert.equal(original.value, 'ORIGINAL_UPLOAD_SENTINEL');
});
test('disabling or disposing wipes owned strings/listeners and late completions cannot remount', async () => {
  for (const method of ['disable', 'dispose']) {
    const held = deferred(), p = base(); p.sourceVersion = 'PRIVATE_SOURCE_VERSION'; p.onApply = () => held.promise; p.result = result(p); const h = setup(p); h.button().click(); const retained = h.d.created.slice(1); if (method === 'disable') h.ui.update({...p, featureEnabled: false}); else h.ui.dispose(); assert.equal(h.d.root.children.length, 0); assert.equal(h.d.listenerCount(), 0); assert.ok(retained.every(n => n.textContent === '')); held.resolve(result(p)); await flush(); assert.equal(h.d.root.children.length, 0); assert.equal(h.d.listenerCount(), 0); if (method === 'dispose') { assert.equal(h.ui.update(p), false); assert.equal(h.ui.dispose(), false); }
  }
});
test('language-only update preserves controlled settings and matching result without submitting again', () => {
  const p = base(); p.flags.reverse = true; let calls = 0; p.onApply = () => calls++; p.result = result(p); const h = setup(p); h.ui.update({...p, lang: 'en'}); assert.equal(h.flag('reverse').checked, true); assert.equal(h.count('before'), '31'); assert.match(h.text(), /Source version: synthetic-version-1/); assert.match(h.text(), /host are ready/); assert.equal(calls, 0);
});
test('full update does not inherit old source, callbacks or backend results', () => {
  const p = base(); p.result = result(p); const h = setup(p); h.ui.update({featureEnabled: true}); assert.equal(h.count('before'), '待计算'); assert.equal(h.button().disabled, true); assert.match(h.text(), /尚未接入/); assert.doesNotMatch(h.text(), /synthetic-version-1/);
});
test('source missing cannot display a matching-but-empty fake result binding', () => {
  const p = {...base(), contextKey: '', sourceVersion: '', quantFamily: ''}; p.result = result(p); const h = setup(p); assert.equal(h.count('before'), '待计算'); assert.equal(h.button().disabled, true);
});
test('synchronous reentrant click from the host cannot submit twice', async () => {
  const p = base(); let h, calls = 0; p.onApply = () => { calls++; h.button().fire('click'); }; h = setup(p); h.button().click(); await flush(); assert.equal(calls, 1);
});
test('synchronous host context switch followed by a callback throw cannot pollute the new context', () => {
  const p = base(); let h; p.onApply = () => { h.ui.update({...p, contextKey: 'synthetic-owner:project-B:revision-new'}); throw Error('PRIVATE_LATE_SYNC'); }; h = setup(p); h.button().click(); assert.doesNotMatch(h.text(), /请求结果未确认|PRIVATE/); assert.equal(h.button().disabled, false); assert.equal(h.count('before'), '待计算');
});
test('late rejected settings notification after source change cannot publish an old error', async () => {
  const held = deferred(), p = base(); p.onChange = () => held.promise; const h = setup(p); h.flag('reverse').checked = true; h.flag('reverse').fire('change'); h.ui.update({...p, sourceVersion: 'synthetic-version-2'}); held.reject(Error('PRIVATE_OLD_CHANGE')); await flush(); assert.doesNotMatch(h.text(), /未能确认设置变更|PRIVATE/); assert.equal(h.flag('reverse').checked, false);
});
test('availability change clears pending local feedback and selected-column result until confirmed again', async () => {
  const held = deferred(), p = base(); p.flags.reverse = true; p.result = result(p); p.onApply = () => held.promise; const h = setup(p); h.button().click(); h.ui.update({...p, availableFlags: {...availableFlags, reverse: null}}); assert.equal(h.count('before'), '待计算'); assert.equal(h.button().disabled, true); held.reject(Error('PRIVATE_OLD_AVAILABILITY')); await flush(); assert.doesNotMatch(h.text(), /请求结果未确认|PRIVATE/); assert.match(h.text(), /已选标记列缺失或尚未确认/);
});
