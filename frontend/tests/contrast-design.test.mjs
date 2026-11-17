import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash, webcrypto} from 'node:crypto';
import {createContrastDesignDraft, reviewContrastDraft, CONTRAST_CONTRACT, MAX_CONTRASTS} from '../private-contrast-design.js';
import {mountContrastDesignEditor} from '../private-contrast-design-editor.js';

const fixtureBytes = await readFile(new URL('../../tests/fixtures/contrast-common.synthetic.json', import.meta.url));
const contractBytes = await readFile(new URL('../../docs/contracts/contrast-contract-v0.1.json', import.meta.url));
const fixture = JSON.parse(fixtureBytes), contract = JSON.parse(contractBytes);
const clarification = JSON.parse(await readFile(new URL('../../docs/contracts/contrast-clarification-01.json', import.meta.url), 'utf8'));
const clone = structuredClone, source = () => clone(fixture.source), draft = () => clone(fixture.comparisonDraft);
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const canonical = value => Array.isArray(value) ? '[' + value.map(canonical).join(',') + ']' : value && typeof value === 'object'
  ? '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}' : JSON.stringify(value);
const rehash = s => { s.sample_design_record.design_sha256 = sha(Buffer.from(canonical(s.sample_design_record.design))); return s; };
const uuid = n => String(n).padStart(8, '0') + '-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const pair = (a, b, n = 1) => ({contrast_id: uuid(n), numerator_group: a, denominator_group: b});
const payload = (s, contrasts) => ({schemaVersion: 1, dataset_version_id: s.dataset_version_id, sample_design_version_id: s.sample_design_record.sample_design_version_id, quant_family: s.quant_family, contrasts});
const groups = s => [...new Set(s.sample_design_record.design.rows.map(row => row.group).filter(value => value.trim()))];
const complete = s => pair(groups(s)[1], groups(s)[0]);
async function ready(s = source(), options = {}) { const model = createContrastDesignDraft({cryptoImpl: webcrypto, ...options}); assert.equal(await model.bindSource(s), true); return model; }
const codes = value => value.source_issues.map(issue => issue.code);
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; };
function heldCrypto() {
  const hold = deferred(); let first = true;
  return {release: () => hold.resolve(), value: {randomUUID: () => webcrypto.randomUUID(), subtle: {digest(...args) { if (!first) return webcrypto.subtle.digest(...args); first = false; return hold.promise.then(() => webcrypto.subtle.digest(...args)); }}}};
}
function dom() {
  const make = tag => { let own = ''; return {tag, children: [], attrs: {}, listeners: new Map(), disabled: false, hidden: false, value: '',
    get textContent() { return own + this.children.map(child => child.textContent).join(''); }, set textContent(value) { own = String(value); this.children = []; },
    append(...nodes) { this.children.push(...nodes); }, replaceChildren(...nodes) { own = ''; this.children = nodes; }, setAttribute(key, value) { this.attrs[key] = value; },
    addEventListener(key, fn) { this.listeners.set(key, fn); }, removeEventListener(key, fn) { if (this.listeners.get(key) === fn) this.listeners.delete(key); }}; };
  const doc = {createElement: tag => Object.assign(make(tag), {ownerDocument: doc})}, root = doc.createElement('root');
  const walk = node => [node, ...node.children.flatMap(walk)]; return {root, all: () => walk(root)};
}
const action = (d, value) => d.all().find(node => node.attrs['data-pcd-action'] === value);
const field = (d, id, value) => d.all().find(node => node.attrs['data-pcd-id'] === id && node.attrs['data-pcd-field'] === value);

test('uses the exact shared synthetic fixture, frozen contract and clarification, with independently pinned hashes', () => {
  assert.equal(fixture.synthetic_fixture, true);
  assert.equal(sha(fixtureBytes), '5e9f717a27b28d34c19ae6f9801f45a0c256b602bcde0c9e468ce87ce3e827e8');
  assert.equal(sha(contractBytes), '095aac9e858e114b4f19c9c16fcf6f20c9cfc038a711a25bb1660152e3809566');
  assert.equal(clarification.originalContractSHA256, sha(contractBytes)); assert.equal(clarification.payloadShapeChanged, false);
  assert.equal(CONTRAST_CONTRACT, contract.contractId); assert.equal(MAX_CONTRASTS, contract.bounds.maxContrasts);
});

test('common comparison matches Root expected state and exact payload, with counts from confirmed source columns', async () => {
  const result = await reviewContrastDraft(draft(), source(), {cryptoImpl: webcrypto});
  assert.equal(result.valid, true); assert.equal(result.state, fixture.expectedState); assert.equal(result.analysis_ready, fixture.analysis_ready);
  const model = await ready(), row = draft().contrasts[0]; assert.equal(model.addContrast(row), row.contrast_id);
  assert.deepEqual(model.exportDraft(), fixture.comparisonDraft); assert.equal(model.view().state, fixture.expectedState);
  assert.deepEqual(model.view().group_summary.map(g => [g.name, g.total_columns, g.confirmed_columns, g.column_ordinals]),
    [['control-synthetic', 3, 3, [3, 4, 5]], ['treatment-synthetic', 3, 3, [6, 7, 8]]]);
  assert.equal(model.view().persisted, false); assert.equal(model.view().analysis_ready, false); model.dispose();
});

test('waiting, zero comparisons, partial comparisons and mixed-completeness follow clarified priority without automatic group selection', async () => {
  const model = createContrastDesignDraft({cryptoImpl: webcrypto}); assert.equal(model.view().state, 'waiting_source'); assert.equal(model.exportDraft(), null); assert.equal(model.addContrast(), null);
  await model.bindSource(source()); assert.equal(model.view().state, 'draft_incomplete'); assert.deepEqual(model.exportDraft().contrasts, []);
  const id = model.addContrast(); assert.equal(model.view().state, 'draft_incomplete'); assert.deepEqual(model.exportDraft().contrasts[0], {contrast_id: id, numerator_group: '', denominator_group: ''});
  assert.equal(model.updateContrast(id, {numerator_group: 'treatment-synthetic'}), true); assert.equal(model.view().state, 'draft_incomplete');
  model.updateContrast(id, {denominator_group: 'control-synthetic'}); assert.equal(model.view().state, 'ready_for_method_selection');
  const partial = model.addContrast({numerator_group: 'control-synthetic'}); assert.equal(model.view().state, 'draft_incomplete');
  assert.equal(model.removeContrast(partial), true); assert.equal(model.view().state, 'ready_for_method_selection');
  model.removeContrast(id); assert.equal(model.view().state, 'draft_incomplete'); assert.equal(model.view().analysis_ready, false); model.dispose();
});

test('unconfirmed and incomplete saved sources remain editable, keep visible issues, and cannot reach method readiness', async () => {
  const s = source(), rows = s.sample_design_record.design.rows; rows[0].confirmed = false; rows[0].group = ''; rows[0].condition = ''; rows[0].replicate_type = 'unknown'; rehash(s);
  const model = await ready(s); assert.equal(model.view().total_columns, 6); assert.equal(model.view().confirmed_columns, 5); assert.equal(model.view().unassigned_columns, 1);
  assert.ok(codes(model.view()).includes('sample_design_unconfirmed')); assert.equal(model.view().state, 'draft_incomplete');
  model.addContrast(complete(s)); assert.equal(model.view().state, 'blocked_by_sample_design'); assert.equal(model.view().analysis_ready, false);
  model.addContrast(); assert.equal(model.view().state, 'draft_incomplete'); assert.ok(model.view().source_issues.length > 0); model.dispose();
});

test('unconfirmed exact edge-whitespace group is a valid draft choice; whitespace-only, normalized or unknown choices are invalid', async () => {
  const s = source(); for (const row of s.sample_design_record.design.rows.slice(0, 3)) { row.group = ' A '; row.confirmed = false; } rehash(s);
  const exact = payload(s, [pair('treatment-synthetic', ' A ')]), good = await reviewContrastDraft(exact, s, {cryptoImpl: webcrypto});
  assert.equal(good.valid, true); assert.equal(good.state, 'blocked_by_sample_design'); assert.equal(good.group_summary[0].name, ' A ');
  for (const choice of ['A', ' ', '\u00a0', 'treatment-Synthetic', 'missing', null, 0, true, '\uD800', 'X'.repeat(81)]) {
    const p = clone(exact); p.contrasts[0].denominator_group = choice; const result = await reviewContrastDraft(p, s, {cryptoImpl: webcrypto});
    assert.equal(result.valid, false); assert.equal(result.state, 'invalid'); assert.ok(result.source_issues.length > 0);
  }
  const p = clone(exact); p.contrasts[0].denominator_group = ''; assert.equal((await reviewContrastDraft(p, s, {cryptoImpl: webcrypto})).state, 'draft_incomplete');
});

test('duplicate directed pairs/IDs and same groups are rejected atomically, while reversed pairs are distinct', async () => {
  const model = await ready(), first = complete(source()); model.addContrast(first); const before = model.exportDraft();
  assert.equal(model.addContrast({...first, contrast_id: uuid(2)}), null); assert.equal(model.view().action_error.code, 'contrast_duplicate_pair'); assert.deepEqual(model.exportDraft(), before);
  assert.equal(model.addContrast({...first, denominator_group: '', numerator_group: ''}), null); assert.equal(model.view().action_error.code, 'contrast_duplicate_id');
  assert.equal(model.updateContrast(first.contrast_id, {denominator_group: first.numerator_group}), false); assert.equal(model.view().action_error.code, 'contrast_same_group'); assert.deepEqual(model.exportDraft(), before);
  assert.ok(model.addContrast(pair(first.denominator_group, first.numerator_group, 2))); assert.equal(model.exportDraft().contrasts.length, 2); assert.equal(model.view().state, 'ready_for_method_selection'); model.dispose();
});

test('direct payload validation checks exact schema, binding, IDs and bounds using safe integral JSON semantics', async () => {
  const mutations = [p => p.owner = 'forged', p => p.schemaVersion = true, p => p.schemaVersion = 1.5, p => p.dataset_version_id = uuid(90),
    p => p.sample_design_version_id = uuid(91), p => p.quant_family = 'LFQ intensity', p => p.contrasts = null,
    p => p.contrasts[0].extra = 'x', p => p.contrasts[0].contrast_id = 'bad', p => p.contrasts[0].contrast_id = 'AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA',
    p => p.contrasts.push(clone(p.contrasts[0])), p => p.contrasts = Array.from({length: 33}, (_, i) => pair('', '', i + 1))];
  for (const mutate of mutations) { const p = draft(); mutate(p); const result = await reviewContrastDraft(p, source(), {cryptoImpl: webcrypto}); assert.equal(result.valid, false); assert.equal(result.state, 'invalid'); }
  const p = draft(); p.schemaVersion = 1.0; assert.equal((await reviewContrastDraft(p, source(), {cryptoImpl: webcrypto})).valid, true);
  const model = await ready(); assert.equal(model.addContrast({numerator_group: null}), null); assert.equal(model.addContrast({time_value: 3}), null); assert.equal(model.addContrast({contrast_id: null}), null); assert.deepEqual(model.exportDraft().contrasts, []); model.dispose();
});

test('maximum32 means32 user-added incomplete rows, never a fixed scientific design; unknown/remove/update inputs do not alter the draft', async () => {
  const model = await ready(); const ids = Array.from({length: 32}, () => model.addContrast()); assert.equal(new Set(ids).size, 32); assert.equal(model.view().can_add, false);
  assert.equal(model.addContrast(), null); assert.equal(model.view().action_error.code, 'contrast_count'); assert.equal(model.view().state, 'draft_incomplete');
  const before = model.exportDraft(); assert.equal(model.updateContrast(uuid(900), {numerator_group: 'control-synthetic'}), false); assert.equal(model.removeContrast(uuid(900)), false);
  assert.equal(model.updateContrast(ids[0], {contrast_id: uuid(901)}), false); assert.equal(model.updateContrast(ids[0], {denominator_group: () => 'secret'}), false); assert.deepEqual(model.exportDraft(), before);
  assert.equal(model.removeContrast(ids[0]), true); assert.equal(model.view().can_add, true); assert.equal(model.view().analysis_ready, false); model.dispose();
});

test('full saved source identity/record schema/source ordinals/hash are checked before dependent success', async () => {
  const mutations = [s => s.extra = 'x', s => s.project_id = 'bad', s => s.quant_family = 'LFQ intensity', s => s.dataset_version_id = uuid(70),
    s => s.sample_design_record = null, s => s.sample_design_record.extra = 'x', s => s.sample_design_record.sample_design_version_id = 'bad',
    s => s.sample_design_record.design_sha256 = '0'.repeat(64), s => s.sample_design_record.revision = true, s => s.sample_design_record.revision = 1.5,
    s => s.sample_design_record.created_at = '2026-02-30T00:00:00.000000Z', s => s.sample_design_record.parent_version_id = s.sample_design_record.sample_design_version_id,
    s => s.sample_columns[0].ordinal = true, s => s.sample_columns.reverse(), s => s.sample_columns.pop(), s => s.sample_columns[0].name_raw += 'changed',
    s => { s.sample_design_record.design.rows[0].group = 'tampered'; }, s => { s.sample_design_record.design.rows[0].condition = '\uD800'; rehash(s); },
    s => { s.sample_design_record.design.rows[0].group = 'X'.repeat(81); rehash(s); }, s => { s.sample_design_record.design.schemaVersion = true; rehash(s); }];
  for (const mutate of mutations) {
    const s = source(); mutate(s); const result = await reviewContrastDraft(draft(), s, {cryptoImpl: webcrypto}); assert.equal(result.state, 'invalid'); assert.equal(result.valid, false);
    const model = await ready(); model.addContrast(complete(source())); assert.equal(await model.bindSource(s), false); assert.equal(model.exportDraft(), null); assert.equal(model.view().state, 'invalid'); assert.deepEqual(model.view().contrasts, []); model.dispose();
  }
});

test('source integer1.0 is valid; invalid fractional/boolean ordinals and scalar/control violations remain rejected', async () => {
  const s = source(); s.sample_columns[0].ordinal = 3.0; s.sample_design_record.revision = 1.0; s.sample_design_record.design.schemaVersion = 1.0; rehash(s);
  assert.equal((await reviewContrastDraft(draft(), s, {cryptoImpl: webcrypto})).state, 'ready_for_method_selection');
  for (const mutate of [s => s.sample_columns[0].ordinal = 3.5, s => { s.sample_design_record.design.rows[0].column_ordinal = true; rehash(s); },
    s => { s.sample_design_record.design.rows[0].condition = 'a\u0085b'; rehash(s); }, s => { s.sample_design_record.design.rows[0].confirmed = 'true'; rehash(s); }]) {
    const value = source(); mutate(value); assert.equal((await reviewContrastDraft(draft(), value, {cryptoImpl: webcrypto})).state, 'invalid');
  }
});

test('replicate and control blockers are explicit, selected-group scoped where required, and no minimum repeat count is invented', async () => {
  for (const change of [s => s.sample_design_record.design.rows[0].replicate_type = 'technical',
    s => s.sample_design_record.design.rows[0].replicate = s.sample_design_record.design.rows[1].replicate,
    s => s.sample_design_record.design.rows[0].control = 'no']) {
    const s = source(); change(s); rehash(s); const result = await reviewContrastDraft(payload(s, [complete(s)]), s, {cryptoImpl: webcrypto});
    assert.equal(result.valid, true); assert.equal(result.state, 'blocked_by_sample_design'); assert.ok(result.source_issues.length); assert.equal(result.analysis_ready, false);
  }
  const s = source(); s.sample_design_record.design.rows[0].group = 'unused technical'; s.sample_design_record.design.rows[0].replicate_type = 'technical'; rehash(s);
  const p = payload(s, [pair('treatment-synthetic', 'control-synthetic')]); assert.equal((await reviewContrastDraft(p, s, {cryptoImpl: webcrypto})).state, 'ready_for_method_selection');
  const small = source(); small.sample_columns = [small.sample_columns[0], small.sample_columns[3]]; small.sample_design_record.design.rows = [small.sample_design_record.design.rows[0], small.sample_design_record.design.rows[3]]; rehash(small);
  assert.equal((await reviewContrastDraft(payload(small, [complete(small)]), small, {cryptoImpl: webcrypto})).state, 'ready_for_method_selection');
});

test('replicate IDs stay literal and exact, shared labels across groups are valid and unconfirmed unused rows still block', async () => {
  const s = source(); s.sample_design_record.design.rows[0].replicate = ' R '; s.sample_design_record.design.rows[1].replicate = 'R'; rehash(s);
  assert.equal((await reviewContrastDraft(payload(s, [complete(s)]), s, {cryptoImpl: webcrypto})).state, 'ready_for_method_selection');
  s.sample_design_record.design.rows[0].group = 'unused'; s.sample_design_record.design.rows[0].confirmed = false; rehash(s);
  const p = payload(s, [pair('treatment-synthetic', 'control-synthetic')]), value = await reviewContrastDraft(p, s, {cryptoImpl: webcrypto});
  assert.equal(value.state, 'blocked_by_sample_design'); assert.ok(codes(value).includes('sample_design_unconfirmed'));
});

test('empty/duplicate raw labels remain separate source columns and blockers; zero selected columns never become ready', async () => {
  for (const label of ['', 'duplicate']) {
    const s = source(); for (const index of [0, 1]) {
      const c = s.sample_columns[index], row = s.sample_design_record.design.rows[index]; c.sample_label_raw = label; c.name_raw = 'Intensity ' + label;
      row.sample_label_raw = label; row.name_raw = c.name_raw; row.confirmed = false;
    } rehash(s);
    const result = await reviewContrastDraft(payload(s, [complete(s)]), s, {cryptoImpl: webcrypto}); assert.equal(result.state, 'blocked_by_sample_design'); assert.equal(result.total_columns, 6);
    assert.deepEqual(result.group_summary[0].column_ordinals, [3, 4, 5]); assert.ok(codes(result).includes('sample_design_ambiguous_sample_label'));
  }
  const empty = source(); empty.sample_columns = []; empty.sample_design_record.design.rows = []; rehash(empty);
  const value = await reviewContrastDraft(payload(empty, []), empty, {cryptoImpl: webcrypto}); assert.equal(value.state, 'draft_incomplete'); assert.ok(codes(value).includes('sample_design_no_columns')); assert.equal(value.analysis_ready, false);
});

test('arbitrary group count/order, nonASCII80-point names, case and Unicode spelling are preserved without inference or normalization', async () => {
  const s = source(), names = ['z', 'a', '组🧪']; s.sample_design_record.design.rows.forEach((row, index) => { row.group = names[index % 3]; row.control = 'yes'; row.replicate = String(index); }); rehash(s);
  const result = await reviewContrastDraft(payload(s, [pair('组🧪', 'z')]), s, {cryptoImpl: webcrypto}); assert.equal(result.state, 'ready_for_method_selection'); assert.deepEqual(result.group_summary.map(g => g.name), names);
  assert.deepEqual(result.group_summary.map(g => [g.total_columns, g.confirmed_columns]), [[2, 2], [2, 2], [2, 2]]);
  const long = source(), control = '对照' + '🧪'.repeat(78), treatment = 'e\u0301';
  long.sample_design_record.design.rows.forEach((row, i) => { row.group = i < 3 ? control : treatment; }); rehash(long);
  const model = await ready(long); model.addContrast(pair(treatment, control)); assert.equal(model.view().state, 'ready_for_method_selection'); assert.equal(model.exportDraft().contrasts[0].denominator_group, control);
  assert.equal(model.updateContrast(uuid(1), {numerator_group: 'é'}), false); assert.equal(model.exportDraft().contrasts[0].numerator_group, treatment); model.dispose();
});

test('tuple comparison and safe Maps handle delimiter/prototype-like names without merging different ordered pairs', async () => {
  const s = source(), names = ['a/b', 'c', 'a', 'b/c', '__proto__', 'constructor'];
  s.sample_design_record.design.rows.forEach((row, i) => { row.group = names[i]; row.control = 'yes'; }); rehash(s);
  const model = await ready(s); assert.ok(model.addContrast(pair('a/b', 'c'))); assert.ok(model.addContrast(pair('a', 'b/c', 2))); assert.ok(model.addContrast(pair('__proto__', 'constructor', 3)));
  assert.equal(model.view().state, 'ready_for_method_selection'); assert.deepEqual(model.view().group_summary.map(g => g.name), names); model.dispose();
});

test('any explicit source rebind clears old comparisons; dataset/family/project/sample-version changes never silently reuse them', async () => {
  const mutations = [s => s.sample_design_record.sample_design_version_id = uuid(51), s => { s.dataset_version_id = uuid(52); s.sample_design_record.design.dataset_version_id = s.dataset_version_id; rehash(s); },
    s => s.project_id = uuid(53), s => { s.quant_family = 'LFQ intensity'; s.sample_design_record.design.quant_family = s.quant_family;
      for (const c of s.sample_columns) { c.quant_family = s.quant_family; c.name_raw = 'LFQ intensity ' + c.sample_label_raw; }
      for (const r of s.sample_design_record.design.rows) r.name_raw = 'LFQ intensity ' + r.sample_label_raw; rehash(s); },
    s => { s.sample_design_record.design.rows[0].group = 'new manual group'; rehash(s); }];
  for (const mutate of mutations) { const model = await ready(); model.addContrast(complete(source())); const s = source(); mutate(s); assert.equal(await model.bindSource(s), true); assert.deepEqual(model.exportDraft().contrasts, []); assert.equal(model.view().state, 'draft_incomplete'); model.dispose(); }
  const model = await ready(); model.addContrast(complete(source())); await model.bindSource(source()); assert.deepEqual(model.exportDraft().contrasts, []); await model.bindSource(null); assert.equal(model.view().state, 'waiting_source'); model.dispose();
});

test('source snapshots and frozen exports cannot mutate original saved sample information or rebind via caller mutation', async () => {
  const input = source(), before = clone(input), hold = heldCrypto(), model = createContrastDesignDraft({cryptoImpl: hold.value});
  const pending = model.bindSource(input); input.sample_design_record.design.rows[0].group = 'caller mutation'; hold.release(); assert.equal(await pending, true);
  assert.equal(model.view().group_summary[0].name, before.sample_design_record.design.rows[0].group); assert.equal(Object.isFrozen(input), false);
  model.addContrast(complete(before)); const p = model.exportDraft(); assert.ok(Object.isFrozen(p) && Object.isFrozen(p.contrasts[0])); assert.throws(() => p.contrasts[0].numerator_group = 'tamper', TypeError);
  const stable = clone(model.exportDraft()); assert.equal(await model.bindSource(input), false); assert.equal(model.exportDraft(), null); assert.equal(model.view().state, 'invalid'); assert.equal(stable.sample_design_version_id, before.sample_design_record.sample_design_version_id); model.dispose();
});

test('disposed and superseded asynchronous hash checks cannot republish old private source or comparisons', async () => {
  for (const mode of ['dispose', 'new source']) {
    const hold = heldCrypto(), model = createContrastDesignDraft({cryptoImpl: hold.value}), first = model.bindSource(source()); assert.equal(model.view().source_checking, true);
    if (mode === 'dispose') model.dispose(); else { const next = source(); next.sample_design_record.sample_design_version_id = uuid(77); assert.equal(await model.bindSource(next), true); model.addContrast(complete(next)); }
    hold.release(); assert.equal(await first, false);
    if (mode === 'dispose') { assert.equal(model.view().state, 'disposed'); assert.equal(model.exportDraft(), null); assert.equal(model.addContrast(), null); assert.equal(model.updateContrast(uuid(1), {}), false); assert.equal(await model.bindSource(source()), false); }
    else { assert.equal(model.view().binding.sample_design_version_id, uuid(77)); assert.equal(model.exportDraft().contrasts.length, 1); model.dispose(); }
  }
});

test('source issues stay visible beside invalid or incomplete primary states; absence/disposal have priority and fixed diagnostics contain no manual text', async () => {
  assert.equal((await reviewContrastDraft({invalid: true}, null)).state, 'waiting_source');
  const s = source(); s.sample_design_record.design.rows[0].confirmed = false; rehash(s);
  const p = payload(s, [complete(s), pair('', '', 2)]); const incomplete = await reviewContrastDraft(p, s, {cryptoImpl: webcrypto});
  assert.equal(incomplete.state, 'draft_incomplete'); assert.ok(incomplete.source_issues.length);
  p.contrasts[0].numerator_group = 'SECRET_MANUAL_NAME'; const invalid = await reviewContrastDraft(p, s, {cryptoImpl: webcrypto}); assert.equal(invalid.state, 'invalid'); assert.ok(invalid.source_issues.length);
  assert.ok(!JSON.stringify(invalid.diagnostics).includes('SECRET_MANUAL_NAME'));
  const uncloneable = await reviewContrastDraft({fn() { throw Error('secret'); }}, s, {cryptoImpl: webcrypto}); assert.equal(uncloneable.state, 'invalid'); assert.ok(uncloneable.source_issues.length);
});

test('bilingual editor starts blank, adds only user-chosen direction pairs, shows definitions and has no save/run/upload action', async () => {
  const model = await ready(), d = dom(), stop = mountContrastDesignEditor(d.root, model);
  assert.match(d.root.textContent, /比较设计（草稿）/); assert.match(d.root.textContent, /仅当前页面 · 未保存/); assert.equal(d.all().filter(n => n.tag === 'select').length, 0);
  action(d, 'add').listeners.get('click')(); const id = model.exportDraft().contrasts[0].contrast_id;
  assert.equal(field(d, id, 'numerator_group').value, ''); assert.equal(field(d, id, 'denominator_group').value, '');
  const numerator = field(d, id, 'numerator_group'); numerator.value = 'treatment-synthetic'; numerator.listeners.get('change')();
  const denominator = field(d, id, 'denominator_group'); denominator.value = 'control-synthetic'; denominator.listeners.get('change')();
  assert.equal(model.view().state, 'ready_for_method_selection'); assert.match(d.root.textContent, /FC = treatment-synthetic \/ control-synthetic/); assert.match(d.root.textContent, /log2FC = log2\(FC\)/);
  assert.match(d.root.textContent, /尚不可运行分析/); assert.ok(d.all().filter(n => n.tag === 'button').every(n => ['add', 'remove'].includes(n.attrs['data-pcd-action']))); assert.equal(d.all().filter(n => n.tag === 'input').length, 0);
  stop(); assert.equal(d.root.textContent, ''); const en = dom(), stopEn = mountContrastDesignEditor(en.root, model, {t: (_, english) => english});
  assert.match(en.root.textContent, /Comparison design \(draft\)/); assert.match(en.root.textContent, /Page-local · not saved/); assert.match(en.root.textContent, /positive change/); assert.equal(field(en, id, 'numerator_group').value, 'treatment-synthetic'); stopEn(); model.dispose();
});

test('editor displays partial/unconfirmed source counts, exact long malicious names and blockers as literal text', async () => {
  const s = source(), long = '<img src=x onerror=bad>组' + '🧪'.repeat(40); for (const row of s.sample_design_record.design.rows.slice(0, 3)) { row.group = long; row.confirmed = false; } rehash(s);
  const model = await ready(s); model.addContrast(pair('treatment-synthetic', long)); const d = dom(), stop = mountContrastDesignEditor(d.root, model);
  assert.match(d.root.textContent, /来源样本信息或重复策略仍需核对/); assert.ok(d.root.textContent.includes(long)); assert.match(d.root.textContent, /已确认列：3 \/ 6/);
  assert.ok(!d.all().some(n => ['img', 'svg', 'script'].includes(n.tag))); assert.ok(d.all().find(n => n.className === 'pcd-group').textContent === long); stop(); model.dispose();
});

test('editor preserves current selection/focus nodes, rejects invalid same-group choice and safely removes comparisons', async () => {
  const model = await ready(); model.addContrast(complete(source())); const d = dom(), stop = mountContrastDesignEditor(d.root, model), id = uuid(1), denominator = field(d, id, 'denominator_group');
  denominator.value = 'treatment-synthetic'; denominator.listeners.get('change')(); assert.equal(field(d, id, 'denominator_group'), denominator); assert.equal(denominator.value, 'control-synthetic'); assert.match(d.root.textContent, /分子组和分母组不能相同/);
  action(d, 'remove').listeners.get('click')(); assert.deepEqual(model.exportDraft().contrasts, []); assert.equal(d.all().filter(n => n.tag === 'select').length, 0); stop(); model.dispose();
});

test('stale DOM handlers cannot edit a replacement source/removed-and-readded ID, and disposal wipes retained private text/values/listeners', async () => {
  const model = await ready(); model.addContrast(complete(source())); const d = dom(), stop = mountContrastDesignEditor(d.root, model), old = field(d, uuid(1), 'numerator_group'), oldHandler = old.listeners.get('change');
  const next = source(); next.sample_design_record.sample_design_version_id = uuid(88); await model.bindSource(next); model.addContrast(pair('', '', 1));
  old.value = 'treatment-synthetic'; oldHandler(); assert.equal(model.exportDraft().contrasts[0].numerator_group, ''); assert.equal(old.listeners.size, 0);
  const current = field(d, uuid(1), 'numerator_group'), currentHandler = current.listeners.get('change'); model.removeContrast(uuid(1)); model.addContrast(pair('', '', 1));
  current.value = 'treatment-synthetic'; currentHandler(); assert.equal(model.exportDraft().contrasts[0].numerator_group, '');
  const retained = field(d, uuid(1), 'denominator_group'); retained.value = 'private retained text'; model.dispose(); assert.equal(retained.value, ''); assert.equal(retained.listeners.size, 0); assert.equal(d.root.textContent, ''); stop();
});

test('no-source editor stays disabled; standalone product has no IO/persistence and scoped CSS supplies basic narrow-screen controls', async () => {
  const model = createContrastDesignDraft(), d = dom(), stop = mountContrastDesignEditor(d.root, model); assert.equal(action(d, 'add').disabled, true); action(d, 'add').listeners.get('click')(); assert.equal(model.exportDraft(), null); stop(); model.dispose();
  const js = await readFile(new URL('../private-contrast-design.js', import.meta.url), 'utf8'), editor = await readFile(new URL('../private-contrast-design-editor.js', import.meta.url), 'utf8'), css = await readFile(new URL('../private-contrast-design.css', import.meta.url), 'utf8');
  assert.doesNotMatch(js + editor, /fetch\(|XMLHttpRequest|localStorage|sessionStorage|document\.cookie|innerHTML|setInterval|setTimeout|process\.env|window\./);
  assert.match(css, /@media\(max-width:520px\)/); assert.match(css, /grid-template-columns:1fr/); assert.match(css, /overflow:auto/); assert.match(css, /focus-visible/); assert.match(css, /overflow-wrap:anywhere/);
  assert.doesNotMatch(css, /@import|url\(|body\s*\{|:root/);
});
