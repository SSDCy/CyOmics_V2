import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash, webcrypto} from 'node:crypto';
import {createPrivateSampleDesign, validateSampleDesignDraft, mountPrivateSampleDesign, SAMPLE_DESIGN_CONTRACT} from '../private-sample-design.js';

const fixture = JSON.parse(await readFile(new URL('./fixtures/sample-design-source.synthetic.json', import.meta.url), 'utf8'));
const clone = v => structuredClone(v), tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; };
function source(labels = ['WT', '100-6']) {
  const f = clone(fixture); f.file.bytes = 4096;
  const d = f.dto.importReport; d.report.observed_bytes = 4096;
  for (const s of [f.dto.datasetVersion.source, d.report.source]) s.expected_bytes = 4096;
  const specs = [['Intensity', labels[0]], [null, 'Protein IDs'], ['Intensity', labels[1]], [null, 'Score'], ['LFQ intensity', 'WT'], ['LFQ intensity', '100-6']];
  d.columns = specs.map(([family, label], ordinal) => ({ordinal, name_raw: family ? family + ' ' + label : label, quant_family: family, sample_label_raw: family ? label : null}));
  d.sample_columns = d.columns.filter(c => c.quant_family !== null); d.report.column_count = 6; d.report.intensity_sample_count = d.report.lfq_sample_count = 2;
  d.header_span = {start_byte: 0, end_byte: 256, start_line: 1, end_line: 1};
  d.row_source_spans = [0, 1].map(i => ({record_number: i + 1, start_byte: 257 + i * 100, end_byte: 350 + i * 100, start_line: i + 2, end_line: i + 2}));
  return f;
}
const descriptor = f => ({dataset_version_id: f.dto.datasetVersion.dataset_version_id, sample_columns: f.dto.importReport.sample_columns});
const bind = (m, f = source()) => m.bindSource({file: f.file, importDTO: f.dto});
const complete = (m, ordinal, values = {}) => m.update(ordinal, {group: 'A', condition: ' salt stress ', control: 'yes', replicate: ' Rep α ', replicate_type: 'biological', ...values});
async function ready(options = {}, f = source()) { const m = createPrivateSampleDesign(options); assert.equal(await bind(m, f), true); return m; }
function distinctSource(f) {
  f = clone(f); const pid = '22222222-2222-4222-8222-222222222222', fid = '66666666-6666-4666-8666-666666666666';
  f.file.project_id = pid; f.file.file_id = fid; const v = f.dto.datasetVersion; v.project_id = pid; v.file_id = fid;
  const bytes = createHash('sha1').update(Buffer.from('3bc2494484f25e4380cb9d536a8b9231', 'hex')).update(fid + ':protein_groups_txt_v1:' + v.config_sha256).digest().subarray(0, 16);
  bytes[6] = bytes[6] & 15 | 80; bytes[8] = bytes[8] & 63 | 128; const s = bytes.toString('hex');
  v.dataset_version_id = [s.slice(0, 8), s.slice(8, 12), s.slice(12, 16), s.slice(16, 20), s.slice(20)].join('-'); f.dto.importReport.report.dataset_version_id = v.dataset_version_id;
  for (const ref of [v.source, f.dto.importReport.report.source]) Object.assign(ref, {project_id: pid, raw_file_id: fid, raw_version_key: 'raw_bytea_v1:' + fid + ':' + f.file.sha256});
  return f;
}
function dom() {
  const make = tag => { let own = ''; return {tag, children: [], attrs: {}, listeners: new Map(), value: '', checked: false, disabled: false,
    get textContent() { return own + this.children.map(c => c.textContent).join(''); }, set textContent(v) { own = String(v); this.children = []; },
    append(...n) { this.children.push(...n); }, replaceChildren(...n) { own = ''; this.children = n; }, setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(k, v) { this.listeners.set(k, v); }, removeEventListener(k, v) { if (this.listeners.get(k) === v) this.listeners.delete(k); }}; };
  const doc = {createElement: tag => Object.assign(make(tag), {ownerDocument: doc})}, root = doc.createElement('root');
  const walk = n => [n, ...n.children.flatMap(walk)]; return {root, all: () => walk(root)};
}
const action = (d, name) => d.all().find(n => n.attrs['data-psd-action'] === name);
const field = (d, ordinal, key) => d.all().find(n => n.attrs['data-psd-field'] === key && n.attrs['data-psd-ordinal'] === String(ordinal));

test('v0.2 source bind preserves zero-based ordinals/read-only names and starts with blank independent manual facts', async () => {
  const f = source(), m = await ready({}, f); assert.equal(SAMPLE_DESIGN_CONTRACT, 'sample_design_review_v0.2');
  const p = m.exportDraft(); assert.deepEqual(Object.keys(p).sort(), ['schemaVersion', 'dataset_version_id', 'quant_family', 'rows'].sort());
  assert.deepEqual(p.rows.map(r => r.column_ordinal), [0, 2]); assert.deepEqual(p.rows.map(r => r.sample_label_raw), ['WT', '100-6']);
  for (const r of p.rows) { assert.equal(r.group, ''); assert.equal(r.condition, ''); assert.equal(r.control, 'unknown'); assert.equal(r.replicate, ''); assert.equal(r.replicate_type, 'unknown'); assert.equal(r.confirmed, false); }
  assert.equal(m.view().validation.valid, true); assert.equal(m.view().analysis_ready, false); assert.equal(m.view().persistence_verified, false);
  assert.equal(m.update(0, {name_raw: 'changed'}), false); assert.equal(m.update(0, {confirmed: true}), false); assert.equal(m.update(0, {time_value: '12'}), false);
  assert.throws(() => { p.rows[0].group = 'mutated'; }); m.dispose();
});

test('wrong/repeated/missing ordinals, altered raw source, extra identity keys and wrong version are rejected; rows never reorder', async () => {
  const f = source(), m = await ready({}, f), p = m.exportDraft(), s = descriptor(f);
  for (const change of [v => v.rows.reverse(), v => v.rows[1].column_ordinal = 0, v => v.rows.pop(), v => v.rows[0].column_ordinal = 1,
    v => v.rows[0].name_raw += 'x', v => v.rows[0].sample_label_raw = 'WT ', v => v.rows[0].owner_id = 'forged', v => v.project_id = f.file.project_id,
    v => v.dataset_version_id = f.file.project_id, v => v.quant_family = 'lfq', v => v.rows[0].confirmed = 'yes']) {
    const v = clone(p); change(v); assert.equal(validateSampleDesignDraft(v, s).valid, false);
  }
  assert.equal(validateSampleDesignDraft(p, {...s, dataset_version_id: 'BAD'}).source_valid, false);
  m.setQuantFamily('LFQ intensity'); assert.deepEqual(m.exportDraft().rows.map(r => r.column_ordinal), [4, 5]); m.dispose();
});

test('Unicode limits count code points, controls are invalid even in drafts, and values are never truncated or normalized', async () => {
  const m = await ready();
  m.update(0, {group: '🧪'.repeat(80), condition: '🧬'.repeat(160), replicate: 'α'.repeat(40)}); assert.equal(m.view().validation.valid, true);
  for (const [key, max] of [['group', 80], ['condition', 160], ['replicate', 40]]) {
    const original = m.exportDraft().rows[0][key]; m.update(0, {[key]: '🧪'.repeat(max + 1)}); assert.equal(m.view().validation.valid, false); assert.equal(Array.from(m.exportDraft().rows[0][key]).length, max + 1); m.update(0, {[key]: original});
  }
  for (const value of ['a\tb', 'a\u0085b', 'a\0b']) { m.update(0, {condition: value}); assert.equal(m.view().validation.valid, false); assert.equal(m.exportDraft().rows[0].condition, value); }
  m.update(0, {group: 'e\u0301', condition: '  literal condition  ', replicate: '  rep 1  '}); assert.equal(m.view().validation.valid, true); assert.equal(m.exportDraft().rows[0].group, 'e\u0301'); m.dispose();
});

test('confirmation is explicit, requires complete facts and exact trimmed group, and editing any fact clears only that row', async () => {
  const m = await ready(); assert.equal(m.confirmAll(), false); complete(m, 0, {group: ' A '}); assert.equal(m.view().validation.valid, true);
  assert.equal(m.setConfirmed(0, true), false); assert.equal(m.exportDraft().rows[0].group, ' A '); m.update(0, {group: 'A'}); assert.equal(m.setConfirmed(0, true), true);
  complete(m, 2, {replicate: '2'}); assert.equal(m.setConfirmed(2, true), true); assert.equal(m.view().validation.manual_review_complete, true); assert.equal(m.view().analysis_ready, false);
  assert.equal(m.exportDraft().rows[0].condition, ' salt stress '); assert.equal(m.exportDraft().rows[0].replicate, ' Rep α ');
  for (const [key, value] of [['group', 'B'], ['condition', 'new'], ['control', 'no'], ['replicate', 'literal'], ['replicate_type', 'technical']]) {
    m.update(0, {[key]: value}); assert.equal(m.exportDraft().rows[0].confirmed, false); assert.equal(m.exportDraft().rows[1].confirmed, true); assert.equal(m.setConfirmed(0, true), true);
  }
  m.dispose();
});

test('same-group mixed controls block overall review readiness but retain valid unconfirmed drafts', async () => {
  const m = await ready(); complete(m, 0, {control: 'yes'}); complete(m, 2, {control: 'no'});
  assert.equal(m.view().validation.valid, true); assert.equal(m.confirmAll(), false); assert.ok(m.view().validation.issues.some(i => i.code === 'group_control_conflict'));
  assert.equal(m.setConfirmed(0, true), true); assert.equal(m.setConfirmed(2, true), true); assert.equal(m.view().validation.manual_review_complete, false); assert.equal(m.view().analysis_ready, false);
  m.update(2, {control: 'yes'}); assert.equal(m.confirmAll(), true); m.dispose();
});

test('empty/whitespace/duplicate labels stay distinct by ordinal, cannot confirm, and are not merged across families', async () => {
  for (const labels of [['same', 'same'], ['', ' '], ['  ', 'valid']]) {
    const m = await ready({}, source(labels)); assert.equal(m.view().rows.length, 2); assert.equal(m.view().validation.valid, true);
    for (const row of m.view().rows) { complete(m, row.column_ordinal); if (row.ambiguous) assert.equal(m.setConfirmed(row.column_ordinal, true), false); }
    assert.equal(m.confirmAll(), false); assert.equal(m.view().rows[0].column_ordinal, 0); assert.equal(m.view().rows[1].column_ordinal, 2); m.dispose();
  }
  const m = await ready(); assert.ok(m.view().rows.every(r => !r.ambiguous)); m.setQuantFamily('LFQ intensity'); assert.ok(m.view().rows.every(r => !r.ambiguous)); m.dispose();
});

test('quantification switch never shares facts or silently inherits confirmation, including switching back', async () => {
  const m = await ready(); complete(m, 0); complete(m, 2); assert.equal(m.confirmAll(), true);
  m.setQuantFamily('LFQ intensity'); assert.ok(m.view().rows.every(r => !r.confirmed && r.group === '' && r.condition === '')); complete(m, 4, {group: 'L'}); m.setConfirmed(4, true);
  m.setQuantFamily('Intensity'); assert.ok(m.view().rows.every(r => r.group === 'A' && !r.confirmed)); m.setQuantFamily('LFQ intensity'); assert.equal(m.view().rows[0].group, 'L'); assert.equal(m.view().rows[0].confirmed, false); m.dispose();
});

test('batch changes only selected source ordinals and enabled facts, clears their confirmations and never auto-numbers replicates', async () => {
  const m = await ready(); complete(m, 0); complete(m, 2); m.confirmAll();
  assert.equal(m.batchUpdate([0], {condition: ' literal '}), true); assert.equal(m.view().rows[0].confirmed, false); assert.equal(m.view().rows[1].confirmed, true);
  assert.equal(m.batchUpdate([0, 2], {replicate: 'same text'}), true); assert.deepEqual(m.view().rows.map(r => r.replicate), ['same text', 'same text']);
  const before = m.exportDraft(); assert.equal(m.batchUpdate([0, 999], {group: 'bad'}), false); assert.equal(m.batchUpdate([0, 0], {group: 'bad'}), false); assert.deepEqual(m.exportDraft(), before); m.dispose();
});

test('default adapters are disconnected; valid local drafts never imply saved persistence or scientific readiness', async () => {
  const m = await ready(); m.update(0, {condition: 'draft'}); assert.equal(m.view().dirty, true); assert.equal(await m.save(), false); assert.equal(await m.load(), false);
  assert.equal(m.view().save_state, 'not_connected'); assert.equal(m.view().load_state, 'not_connected'); assert.equal(m.view().persistence_verified, false); assert.equal(m.view().analysis_ready, false); m.dispose();
});

test('save adapter must acknowledge the exact source-bound draft; saving/dirty/failure states never leak dynamic errors', async () => {
  const later = deferred(); let received, signal;
  const m = await ready({saveDraft: (p, options) => { received = p; signal = options.signal; return later.promise; }}); m.update(0, {group: ' draft '}); const pending = m.save();
  assert.equal(m.view().save_state, 'saving'); assert.equal(m.view().busy, true); assert.equal(m.update(0, {group: 'blocked while saving'}), false);
  assert.equal(Object.hasOwn(received, 'project_id'), false); assert.equal(Object.hasOwn(received, 'owner_id'), false); assert.equal(signal.aborted, false); later.resolve(received); assert.equal(await pending, true);
  assert.equal(m.view().save_state, 'acknowledged'); assert.equal(m.view().dirty, false); assert.equal(m.view().persistence_verified, false); m.dispose();
  const bad = await ready({saveDraft: p => ({...p, rows: p.rows.map(r => ({...r, group: r.group.trim()}))})}); bad.update(0, {group: ' A '}); assert.equal(await bad.save(), false); assert.equal(bad.view().save_state, 'failed'); assert.equal(bad.exportDraft().rows[0].group, ' A '); bad.dispose();
  const broken = await ready({saveDraft() { throw Error('PRIVATE EXCEPTION'); }}); assert.equal(await broken.save(), false); assert.equal(broken.view().io_error, 'save_failed'); assert.equal(JSON.stringify(broken.view()).includes('PRIVATE EXCEPTION'), false); broken.dispose();
});

test('load is explicit, validates exact source/order before applying and does not silently overwrite dirty edits', async () => {
  let next, selector; const m = await ready({loadDraft: p => { selector = p; return next; }}); next = clone(m.exportDraft()); next.rows[0].condition = ' returned literal ';
  assert.equal(await m.load(), true); assert.equal(m.view().load_state, 'loaded'); assert.equal(m.view().rows[0].condition, ' returned literal '); assert.deepEqual(Object.keys(selector).sort(), ['dataset_version_id', 'quant_family']);
  m.update(0, {group: 'local'}); assert.equal(await m.load(), false); assert.equal(m.view().io_error, 'dirty_draft'); const before = m.exportDraft(); next.rows.reverse();
  assert.equal(await m.load({discardDirty: true}), false); assert.deepEqual(m.exportDraft(), before); assert.equal(m.view().load_state, 'failed'); m.dispose();
});

test('source project/version change aborts pending save and ignores late acknowledged payload', async () => {
  const later = deferred(); let old, signal; const m = await ready({saveDraft: (p, o) => { old = p; signal = o.signal; return later.promise; }}); complete(m, 0); const pending = m.save();
  const next = distinctSource(source()); assert.equal(await bind(m, next), true); assert.equal(signal.aborted, true); assert.equal(await pending, false); later.resolve(old); await tick();
  assert.equal(m.view().binding.project_id, next.file.project_id); assert.equal(m.view().binding.dataset_version_id, next.dto.datasetVersion.dataset_version_id);
  assert.ok(m.view().rows.every(r => r.group === '' && !r.confirmed)); assert.equal(m.view().save_state, 'idle'); m.dispose();
});

test('load rejects a valid same-version draft from the opposite quantification and preserves original facts/dirty state', async () => {
  for (const requested of ['Intensity', 'LFQ intensity']) {
    let reply; const m = await ready({loadDraft: () => reply});
    m.setQuantFamily(requested === 'Intensity' ? 'LFQ intensity' : 'Intensity'); reply = clone(m.exportDraft());
    assert.equal(validateSampleDesignDraft(reply, descriptor(source())).valid, true);
    m.setQuantFamily(requested); const ordinal = m.view().rows[0].column_ordinal; m.update(ordinal, {group: 'keep local'});
    const before = m.exportDraft(), d = dom(), stop = mountPrivateSampleDesign(d.root, m);
    assert.equal(await m.load({discardDirty: true}), false); assert.equal(m.view().load_state, 'failed'); assert.equal(m.view().io_error, 'load_family_mismatch');
    assert.deepEqual(m.exportDraft(), before); assert.equal(m.view().dirty, true); assert.equal(m.view().quant_family, requested);
    assert.match(d.root.textContent, /量化类型不匹配/); assert.equal(field(d, ordinal, 'group').value, 'keep local'); stop(); m.dispose();
  }
});

test('quantification switch/dispose abort pending adapter work; delayed loads cannot repopulate private facts', async () => {
  const later = deferred(); const m = await ready({loadDraft: () => later.promise}); const old = m.exportDraft(), pending = m.load(); m.setQuantFamily('LFQ intensity'); assert.equal(await pending, false);
  later.resolve(old); await tick(); assert.equal(m.view().quant_family, 'LFQ intensity'); assert.deepEqual(m.view().rows.map(r => r.column_ordinal), [4, 5]); m.dispose(); assert.equal(m.view().rows.length, 0); assert.equal(m.view().binding, null);
});

test('source proof rejects tampered report before edit/save and snapshots caller data before asynchronous validation', async () => {
  let saves = 0; const m = createPrivateSampleDesign({saveDraft() { saves++; }}), bad = source(); bad.dto.importReport.sample_columns[0].sample_label_raw = 'forged';
  assert.equal(await bind(m, bad), false); assert.equal(m.view().source_phase, 'invalid'); assert.equal(m.update(0, {group: 'bad'}), false); assert.equal(await m.save(), false); assert.equal(saves, 0);
  const original = source(), proof = bind(m, original); original.dto.importReport.columns[0].sample_label_raw = 'changed after call'; assert.equal(await proof, true); assert.equal(m.view().rows[0].sample_label_raw, 'WT'); m.dispose();
});

test('mounted bilingual component keeps source read-only, actions disconnected, state literal and manual input in model across remount', async () => {
  const m = await ready(), d = dom(); let stop = mountPrivateSampleDesign(d.root, m);
  assert.equal(action(d, 'save').disabled, true); assert.equal(action(d, 'load').disabled, true); assert.match(d.root.textContent, /未接入正式保存服务/); assert.equal(d.all().some(n => ['name_raw', 'sample_label_raw'].includes(n.attrs['data-psd-field'])), false);
  const input = field(d, 0, 'group'); input.value = '<img src=x>'; input.listeners.get('input')(); assert.equal(m.view().rows[0].group, '<img src=x>'); assert.equal(d.all().some(n => n.tag === 'img'), false);
  stop(); assert.equal(input.value, ''); const en = dom(); stop = mountPrivateSampleDesign(en.root, m, {t: (_, english) => english}); assert.equal(field(en, 0, 'group').value, '<img src=x>'); assert.match(en.root.textContent, /Persistence service not connected/);
  m.dispose(); assert.equal(field(en, 0, 'group'), undefined); stop(); assert.equal(en.root.textContent, '');
});

test('stale row handlers cannot edit another family/version; component disposal clears retained text, values and subscriptions', async () => {
  const m = await ready(), d = dom(), stop = mountPrivateSampleDesign(d.root, m); const input = field(d, 0, 'group'), oldHandler = input.listeners.get('input');
  input.value = 'stale'; m.setQuantFamily('LFQ intensity'); oldHandler(); assert.ok(m.view().rows.every(r => r.group === '')); assert.equal(input.value, '');
  const retained = d.all(); stop(); for (const n of retained) { assert.equal(n.textContent, ''); assert.equal(n.listeners.size, 0); if ('value' in n) assert.equal(n.value, ''); }
  m.update(4, {group: 'retained only in model'}); assert.equal(d.root.textContent, ''); m.dispose();
});

test('module and styles have no automatic network/storage/scripts or global CSS and preserve keyboard/scroll/wrapping affordances', async () => {
  const js = await readFile(new URL('../private-sample-design.js', import.meta.url), 'utf8'), css = await readFile(new URL('../private-sample-design.css', import.meta.url), 'utf8');
  assert.doesNotMatch(js, /fetch\(|XMLHttpRequest|document\.cookie|localStorage|sessionStorage|innerHTML|console\.|setInterval|setTimeout|timeMode|timeValue|timeUnit/);
  for (const text of ['overflow:auto', ':focus-visible', 'white-space:pre-wrap', 'overflow-wrap:anywhere', '@media']) assert.ok(css.includes(text));
  assert.doesNotMatch(css, /@import|https?:|(?:^|\n)(?:body|html|button|input|table)\s*\{/);
});
