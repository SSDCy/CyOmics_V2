import {validateImportDTO, canonicalUUID, canonicalJSON} from './private-import-dto.js';

export const SAMPLE_DESIGN_CONTRACT = 'sample_design_review_v0.2';
export const QUANT_FAMILIES = Object.freeze(['Intensity', 'LFQ intensity']);
export const MANUAL_FIELDS = Object.freeze(['group', 'condition', 'control', 'replicate', 'replicate_type']);
const rowKeys = ['column_ordinal', 'name_raw', 'sample_label_raw', ...MANUAL_FIELDS, 'confirmed'];
const limits = Object.freeze({group: 80, condition: 160, replicate: 40});
const enums = Object.freeze({control: ['unknown', 'yes', 'no'], replicate_type: ['unknown', 'biological', 'technical']});
const exact = (v, keys) => v !== null && typeof v === 'object' && !Array.isArray(v)
  && Object.keys(v).length === keys.length && keys.every(k => Object.hasOwn(v, k));
const scalar = v => typeof v === 'string' && !/[\uD800-\uDFFF]/u.test(v);
const controls = /[\u0000-\u001f\u007f-\u009f]/u;
const pointsWithin = (s, max) => { let n = 0; for (const _ of s) if (++n > max) return false; return true; };
const freeze = v => { if (v && typeof v === 'object' && !Object.isFrozen(v)) { Object.values(v).forEach(freeze); Object.freeze(v); } return v; };
const blank = () => ({group: '', condition: '', control: 'unknown', replicate: '', replicate_type: 'unknown', confirmed: false});
const issue = (code, ordinal = null, field = null, fatal = true) => ({code, column_ordinal: ordinal, field, fatal});
function checkedSource(source) {
  if (!exact(source, ['dataset_version_id', 'sample_columns']) || !canonicalUUID(source.dataset_version_id)
    || !Array.isArray(source.sample_columns) || source.sample_columns.length > 512) return false;
  const ids = new Set();
  for (const c of source.sample_columns) {
    if (!exact(c, ['ordinal', 'name_raw', 'quant_family', 'sample_label_raw']) || !Number.isSafeInteger(c.ordinal) || c.ordinal < 0 || c.ordinal > 511 || ids.has(c.ordinal)
      || !QUANT_FAMILIES.includes(c.quant_family) || !scalar(c.name_raw) || !scalar(c.sample_label_raw)
      || c.name_raw !== c.quant_family + ' ' + c.sample_label_raw) return false;
    ids.add(c.ordinal);
  }
  return true;
}
function selectedColumns(source, family) { return source.sample_columns.filter(c => c.quant_family === family); }
function ambiguousColumns(columns) {
  const counts = new Map(); for (const c of columns) counts.set(c.sample_label_raw, (counts.get(c.sample_label_raw) || 0) + 1);
  return new Set(columns.filter(c => !c.sample_label_raw.trim() || counts.get(c.sample_label_raw) !== 1).map(c => c.ordinal));
}
function factIssues(row, ambiguous, confirmed = row.confirmed) {
  const found = [], ordinal = row.column_ordinal;
  for (const [field, max] of Object.entries(limits)) {
    if (!scalar(row[field])) found.push(issue('field_type', ordinal, field));
    else if (controls.test(row[field])) found.push(issue('field_controls', ordinal, field));
    else if (!pointsWithin(row[field], max)) found.push(issue('field_limit', ordinal, field));
  }
  for (const field of ['control', 'replicate_type']) if (!enums[field].includes(row[field])) found.push(issue('field_enum', ordinal, field));
  if (typeof row.confirmed !== 'boolean') found.push(issue('confirmed_type', ordinal, 'confirmed'));
  if (confirmed) {
    if (ambiguous) found.push(issue('ambiguous_confirmation', ordinal, 'confirmed'));
    for (const field of ['group', 'condition', 'replicate']) if (scalar(row[field]) && !row[field].trim()) found.push(issue('confirmation_required', ordinal, field));
    if (scalar(row.group) && row.group !== row.group.trim()) found.push(issue('group_trim', ordinal, 'group'));
    for (const field of ['control', 'replicate_type']) if (row[field] === 'unknown') found.push(issue('confirmation_required', ordinal, field));
  }
  return found;
}
function groupConflicts(rows) {
  const groups = new Map();
  for (const row of rows) if (scalar(row.group) && row.group.trim() && row.group === row.group.trim() && ['yes', 'no'].includes(row.control)) {
    const entries = groups.get(row.group) || []; entries.push(row); groups.set(row.group, entries);
  }
  return [...groups.values()].filter(entries => new Set(entries.map(r => r.control)).size > 1)
    .flatMap(entries => entries.map(row => issue('group_control_conflict', row.column_ordinal, 'control', false)));
}

// The caller owns source authority. This validator supplies neither identity nor
// project authorization; its source must come from a validated server report.
export function validateSampleDesignDraft(payload, source) {
  if (!checkedSource(source)) return freeze({valid: false, source_valid: false, issues: [issue('source_invalid')], manual_review_complete: false, analysis_ready: false});
  const invalid = code => freeze({valid: false, source_valid: true, issues: [issue(code)], manual_review_complete: false, analysis_ready: false});
  if (!exact(payload, ['schemaVersion', 'dataset_version_id', 'quant_family', 'rows']) || payload.schemaVersion !== 1
    || payload.dataset_version_id !== source.dataset_version_id || !QUANT_FAMILIES.includes(payload.quant_family)) return invalid('draft_binding');
  const columns = selectedColumns(source, payload.quant_family);
  if (!Array.isArray(payload.rows) || payload.rows.length !== columns.length) return invalid('row_membership');
  const ambiguous = ambiguousColumns(columns), issues = [];
  for (let i = 0; i < columns.length; i++) {
    const row = payload.rows[i], c = columns[i];
    if (!exact(row, rowKeys)) return invalid('row_schema');
    if (row.column_ordinal !== c.ordinal) return invalid('row_order');
    if (row.name_raw !== c.name_raw || row.sample_label_raw !== c.sample_label_raw) return invalid('row_source');
    issues.push(...factIssues(row, ambiguous.has(c.ordinal)));
    if (ambiguous.has(c.ordinal)) issues.push(issue('ambiguous_sample_label', c.ordinal, null, false));
  }
  issues.push(...groupConflicts(payload.rows));
  const valid = !issues.some(v => v.fatal);
  return freeze({valid, source_valid: true, issues, manual_review_complete: valid && payload.rows.length > 0 && payload.rows.every(r => r.confirmed)
    && ambiguous.size === 0 && !issues.some(v => v.code === 'group_control_conflict'), analysis_ready: false});
}

export function createPrivateSampleDesign({saveDraft = null, loadDraft = null, cryptoImpl = globalThis.crypto} = {}) {
  let disposed = false, generation = 0, source = null, binding = null, sourcePhase = 'unbound', sourceError = null;
  let family = 'Intensity', drafts = new Map(), operation = null;
  let saveState = typeof saveDraft === 'function' ? 'idle' : 'not_connected', loadState = typeof loadDraft === 'function' ? 'idle' : 'not_connected', ioError = null;
  const listeners = new Set(), emit = () => { if (!disposed) for (const fn of listeners) fn(); };
  function stopIO() { const old = operation; operation = null; old?.abort.abort(); }
  function resetIO() { saveState = typeof saveDraft === 'function' ? 'idle' : 'not_connected'; loadState = typeof loadDraft === 'function' ? 'idle' : 'not_connected'; ioError = null; }
  function payload() {
    if (!source) return null;
    return {schemaVersion: 1, dataset_version_id: source.dataset_version_id, quant_family: family, rows: drafts.get(family).rows.map(r => ({...r}))};
  }
  function active(work) { return !disposed && source !== null && operation === work && work.generation === generation && work.family === family && !work.abort.signal.aborted; }
  function clearSource() {
    generation++; stopIO(); source = binding = null; drafts.clear(); sourcePhase = 'unbound'; sourceError = null; family = 'Intensity'; resetIO(); emit();
  }
  async function bindSource(input) {
    if (disposed) return false;
    clearSource(); if (input === null || input === undefined) return false;
    const current = generation; sourcePhase = 'validating'; emit();
    try {
      // Snapshot before the async proof so caller edits cannot change the source
      // between validation and publication into this model.
      const snapshot = structuredClone(input);
      if (!exact(snapshot, ['file', 'importDTO'])) throw Error();
      await validateImportDTO(snapshot.importDTO, snapshot.file, cryptoImpl, {method: 'GET', versionId: snapshot.importDTO.datasetVersion.dataset_version_id});
      if (disposed || current !== generation) return false;
      source = freeze({dataset_version_id: snapshot.importDTO.datasetVersion.dataset_version_id,
        sample_columns: snapshot.importDTO.importReport.sample_columns.map(c => ({...c}))});
      if (!checkedSource(source)) throw Error();
      binding = freeze({project_id: snapshot.file.project_id, file_id: snapshot.file.file_id, dataset_version_id: source.dataset_version_id, raw_sha256: snapshot.file.sha256});
      drafts = new Map(QUANT_FAMILIES.map(type => [type, {dirty: false, rows: selectedColumns(source, type).map(c => ({column_ordinal: c.ordinal, name_raw: c.name_raw, sample_label_raw: c.sample_label_raw, ...blank()}))}]));
      sourcePhase = 'ready'; sourceError = null; emit(); return true;
    } catch {
      if (!disposed && current === generation) { source = binding = null; drafts.clear(); sourcePhase = 'invalid'; sourceError = 'source_invalid'; emit(); }
      return false;
    }
  }
  function changed() { drafts.get(family).dirty = true; resetIO(); emit(); }
  function canEdit() { return !disposed && source !== null && !operation; }
  function patchValid(patch) {
    return patch && typeof patch === 'object' && !Array.isArray(patch) && Object.keys(patch).length > 0
      && Object.keys(patch).every(k => MANUAL_FIELDS.includes(k) && typeof patch[k] === 'string' && (!enums[k] || enums[k].includes(patch[k])));
  }
  function batchUpdate(ordinals, patch) {
    if (!canEdit() || !patchValid(patch) || !Array.isArray(ordinals) || !ordinals.length || new Set(ordinals).size !== ordinals.length) return false;
    const rows = drafts.get(family).rows, chosen = ordinals.map(id => rows.find(r => r.column_ordinal === id)); if (chosen.some(r => !r)) return false;
    let edited = false;
    for (const row of chosen) if (Object.keys(patch).some(k => row[k] !== patch[k])) { Object.assign(row, patch); row.confirmed = false; edited = true; }
    if (edited) changed(); return edited;
  }
  function setConfirmed(ordinal, value) {
    if (!canEdit() || typeof value !== 'boolean') return false;
    const row = drafts.get(family).rows.find(r => r.column_ordinal === ordinal); if (!row || row.confirmed === value) return false;
    if (value && factIssues(row, ambiguousColumns(selectedColumns(source, family)).has(ordinal), true).length) return false;
    row.confirmed = value; changed(); return true;
  }
  function confirmAll() {
    if (!canEdit()) return false;
    const p = payload(); p.rows.forEach(r => { r.confirmed = true; });
    if (!validateSampleDesignDraft(p, source).manual_review_complete) return false;
    let edited = false; for (const row of drafts.get(family).rows) if (!row.confirmed) { row.confirmed = true; edited = true; }
    if (edited) changed(); return edited;
  }
  function setQuantFamily(next) {
    if (disposed || !source || !QUANT_FAMILIES.includes(next) || next === family) return false;
    generation++; stopIO(); family = next; resetIO();
    const draft = drafts.get(family); for (const row of draft.rows) if (row.confirmed) { row.confirmed = false; draft.dirty = true; }
    emit(); return true;
  }
  async function transfer(kind, discardDirty = false) {
    const adapter = kind === 'save' ? saveDraft : loadDraft;
    if (disposed || !source || operation || typeof adapter !== 'function') return false;
    const original = payload(), draft = drafts.get(family);
    if (kind === 'save' && !validateSampleDesignDraft(original, source).valid) { saveState = 'failed'; ioError = 'draft_invalid'; emit(); return false; }
    if (kind === 'load' && draft.dirty && discardDirty !== true) { loadState = 'failed'; ioError = 'dirty_draft'; emit(); return false; }
    const work = {generation, family, abort: new AbortController()}; operation = work; let failureCode = kind + '_failed';
    if (kind === 'save') saveState = 'saving'; else loadState = 'loading'; ioError = null; emit();
    const cancelled = new Promise((_, reject) => work.abort.signal.addEventListener('abort', () => reject(Error()), {once: true})); void cancelled.catch(() => {});
    try {
      if (!active(work)) return false;
      const argument = kind === 'save' ? freeze(original) : freeze({dataset_version_id: source.dataset_version_id, quant_family: family});
      const returned = await Promise.race([Promise.resolve(adapter(argument, {signal: work.abort.signal})), cancelled]);
      if (!active(work)) return false;
      if (kind === 'load' && returned === null) { loadState = 'not_found'; return false; }
      const result = structuredClone(returned);
      if (kind === 'load' && result?.quant_family !== work.family) { failureCode = 'load_family_mismatch'; throw Error(); }
      const checked = validateSampleDesignDraft(result, source); if (!checked.valid) throw Error();
      if (kind === 'save' && canonicalJSON(result) !== canonicalJSON(original)) throw Error();
      if (kind === 'load') { draft.rows = result.rows.map(r => ({...r})); loadState = 'loaded'; } else saveState = 'acknowledged';
      draft.dirty = false; return true;
    } catch {
      if (active(work)) { if (kind === 'save') saveState = 'failed'; else loadState = 'failed'; ioError = failureCode; }
      return false;
    } finally {
      if (operation === work) { operation = null; work.abort.abort(); emit(); }
    }
  }
  function view() {
    const p = payload(), validation = p ? validateSampleDesignDraft(p, source) : null, columns = source ? selectedColumns(source, family) : [], ambiguous = ambiguousColumns(columns);
    const rows = p ? p.rows.map(r => ({...r, ambiguous: ambiguous.has(r.column_ordinal), confirmation_issues: factIssues(r, ambiguous.has(r.column_ordinal), true)})) : [];
    const all = p && {...p, rows: p.rows.map(r => ({...r, confirmed: true}))};
    return freeze({contract: SAMPLE_DESIGN_CONTRACT, source_phase: disposed ? 'disposed' : sourcePhase, source_error: sourceError, source_epoch: generation,
      binding: binding && {...binding}, quant_family: family, rows, family_counts: Object.fromEntries(QUANT_FAMILIES.map(k => [k, source ? selectedColumns(source, k).length : 0])),
      dirty: !!source && drafts.get(family).dirty, busy: !!operation, save_state: saveState, load_state: loadState, io_error: ioError,
      can_save: canEdit() && typeof saveDraft === 'function' && !!validation?.valid, can_load: canEdit() && typeof loadDraft === 'function',
      can_confirm_all: canEdit() && !!all && validateSampleDesignDraft(all, source).manual_review_complete,
      validation, persistence_verified: false, analysis_ready: false});
  }
  function dispose() {
    if (disposed) return; generation++; stopIO(); source = binding = null; drafts.clear(); sourcePhase = 'disposed'; sourceError = null; resetIO(); disposed = true;
    for (const fn of listeners) fn(); listeners.clear();
  }
  return {bindSource, clearSource, setQuantFamily, batchUpdate, update: (ordinal, patch) => batchUpdate([ordinal], patch), setConfirmed, confirmAll,
    exportDraft: () => { const p = payload(); return p && freeze(p); }, save: () => transfer('save'), load: ({discardDirty = false} = {}) => transfer('load', discardDirty), view, dispose,
    subscribe(fn) { if (disposed) return () => {}; listeners.add(fn); return () => listeners.delete(fn); }};
}

let componentSerial = 0;
export function mountPrivateSampleDesign(root, model, {t = zh => zh} = {}) {
  const doc = root.ownerDocument, id = ++componentSerial;
  let disposed = false, renderKey = null, token = 0, rows = new Map(), selections = new Set();
  let rowListeners = [], stableListeners = [], batchValues = {}, batchEnabled = new Set();
  const make = (tag, text = '', cls = '') => { const n = doc.createElement(tag); n.textContent = text; n.className = cls; return n; };
  const bind = (node, event, fn, row = false) => { node.addEventListener(event, fn); (row ? rowListeners : stableListeners).push(() => node.removeEventListener(event, fn)); };
  const labels = {group: t('人工组名', 'Manual group'), condition: t('处理条件', 'Treatment condition'), control: t('是否对照', 'Control sample'),
    replicate: t('重复编号', 'Replicate identifier'), replicate_type: t('重复类型', 'Replicate type')};
  const options = {control: [['unknown', t('未指定', 'Not specified')], ['yes', t('是', 'Yes')], ['no', t('否', 'No')]],
    replicate_type: [['unknown', t('未知', 'Unknown')], ['biological', t('生物学重复', 'Biological')], ['technical', t('技术重复', 'Technical')]]};
  function input(field, label) {
    const n = make(options[field] ? 'select' : 'input'); n.setAttribute('aria-label', label);
    if (options[field]) for (const [value, title] of options[field]) { const o = make('option', title); o.value = value; n.append(o); }
    else { n.type = 'text'; n.autocomplete = 'off'; n.spellcheck = false; }
    return n;
  }
  const check = label => { const n = make('input'); n.type = 'checkbox'; n.setAttribute('aria-label', label); return n; };
  const button = (label, action) => { const n = make('button', label); n.type = 'button'; n.setAttribute('data-psd-action', action); return n; };
  const panel = make('section', '', 'psd'); panel.setAttribute('role', 'region'); panel.setAttribute('aria-label', t('样本设计草稿', 'Sample design draft'));
  const heading = make('div', '', 'psd-heading'); heading.append(make('h3', t('样本设计（草稿）', 'Sample design (draft)')), make('span', t('未接入正式保存服务', 'Persistence service not connected'), 'psd-badge'));
  const intro = make('p', t('按原始列序号逐列填写，不推断名称。Intensity / LFQ 是替代量化，不代表两倍生物样本。',
    'Edit by original column ordinal; names do not infer design. Intensity / LFQ are alternative quantifications, not double the biological samples.'), 'psd-note');
  const family = make('select'); family.setAttribute('aria-label', t('量化类型', 'Quantification type')); family.setAttribute('data-psd-control', 'quant-family');
  for (const value of QUANT_FAMILIES) { const o = make('option', value); o.value = value; family.append(o); }
  const familyLabel = make('label', t('量化类型 ', 'Quantification type ')); familyLabel.append(family);
  const summary = make('p', '', 'psd-note'), trace = make('p', '', 'psd-trace');
  const state = make('p', '', 'psd-state'); state.setAttribute('role', 'status'); state.setAttribute('aria-live', 'polite');
  const batch = make('details'); batch.append(make('summary', t('批量填写当前类型选中列', 'Batch edit selected columns in this type')));
  const selectAll = check(t('全选当前类型样本列', 'Select all columns in this type')), allLabel = make('label', '', 'psd-select-all');
  allLabel.append(selectAll, make('span', t('全选当前类型', 'Select all in this type'))); batch.append(allLabel);
  const batchGrid = make('div', '', 'psd-batch'), batchControls = new Map();
  for (const field of MANUAL_FIELDS) {
    const enabled = check(t('批量修改：', 'Batch change: ') + labels[field]), value = input(field, t('批量：', 'Batch: ') + labels[field]), label = make('label');
    label.append(enabled, make('span', labels[field]), value); batchGrid.append(label); batchControls.set(field, {enabled, value});
    bind(enabled, 'change', () => { if (!disposed) { if (enabled.checked) batchEnabled.add(field); else batchEnabled.delete(field); paint(); } });
    bind(value, options[field] ? 'change' : 'input', () => { if (!disposed) batchValues[field] = value.value; });
  }
  const apply = button(t('应用到选中列（不自动确认）', 'Apply to selected columns (without confirmation)'), 'batch');
  batch.append(batchGrid, make('p', t('只修改勾选字段；文字原样复制，重复编号不自动连号。未勾选字段保持不变。',
    'Only enabled fields change. Text is copied literally; replicate identifiers are never numbered automatically. Other fields remain unchanged.'), 'psd-note'), apply);
  const scroll = make('div', '', 'psd-scroll'); scroll.tabIndex = 0; scroll.setAttribute('role', 'region'); scroll.setAttribute('aria-label', t('样本设计表，可横向滚动', 'Scrollable sample design table'));
  const table = make('table'), head = make('thead'), title = make('tr'), body = make('tbody');
  for (const name of [t('选择', 'Select'), t('原列序号（从0开始）', 'Source ordinal (from 0)'), t('原始样本名 / 列名（只读）', 'Raw sample / column (read only)'), ...MANUAL_FIELDS.map(k => labels[k]), t('人工核对', 'Manual review')]) {
    const th = make('th', name); th.setAttribute('scope', 'col'); title.append(th);
  }
  head.append(title); table.append(head, body); scroll.append(table);
  const actions = make('div', '', 'psd-actions'), confirm = button(t('确认当前类型填写信息', 'Confirm facts in this type'), 'confirm'),
    save = button(t('保存草稿', 'Save draft'), 'save'), load = button(t('载入并替换当前草稿', 'Load and replace current draft'), 'load');
  save.className = 'primary'; actions.append(confirm, save, load);
  const boundary = make('p', t('草稿校验或适配器确认不代表服务端持久化完成，也不代表可运行差异分析；比较、统计设计和重复策略仍需后续确认。',
    'Draft validation or adapter acknowledgement proves neither server persistence nor readiness for differential analysis. Contrast, statistical design and replication policy remain later gates.'), 'psd-note');
  panel.append(heading, intro, familyLabel, summary, trace, state, batch, scroll, actions, boundary); root.replaceChildren(panel);
  function wipe(node) {
    for (const child of [...(node.childNodes || node.children || [])]) wipe(child);
    if ('value' in node) node.value = ''; if ('checked' in node) node.checked = false; node.textContent = '';
  }
  function resetRows() {
    token++; rowListeners.forEach(stop => stop()); rowListeners = []; wipe(body); body.replaceChildren(); rows.clear(); selections.clear();
    batchEnabled.clear(); batchValues = Object.fromEntries(MANUAL_FIELDS.map(k => [k, enums[k] ? 'unknown' : '']));
  }
  function buildRows(value) {
    resetRows(); const currentToken = token;
    for (const row of value.rows) {
      const tr = make('tr'), selected = check(t('选择列 ', 'Select column ') + row.column_ordinal), selectedCell = make('td'); selectedCell.append(selected); tr.append(selectedCell, make('td', String(row.column_ordinal)));
      const source = make('td', '', 'psd-source'); source.append(make('span', row.sample_label_raw), make('small', row.name_raw)); tr.append(source);
      const fields = new Map();
      for (const field of MANUAL_FIELDS) {
        const cell = make('td'), n = input(field, labels[field] + ' · #' + row.column_ordinal), error = make('small', '', 'psd-error');
        error.id = 'psd-' + id + '-' + token + '-' + row.column_ordinal + '-' + field; n.setAttribute('aria-describedby', error.id);
        n.setAttribute('data-psd-field', field); n.setAttribute('data-psd-ordinal', String(row.column_ordinal)); cell.append(n, error); tr.append(cell); fields.set(field, {input: n, error});
        bind(n, options[field] ? 'change' : 'input', () => { if (!disposed && token === currentToken) model.update(row.column_ordinal, {[field]: n.value}); }, true);
      }
      const reviewed = check(t('已人工核对列 ', 'Reviewed column ') + row.column_ordinal), reviewCell = make('td'), note = make('small'); reviewCell.append(reviewed, note); tr.append(reviewCell); body.append(tr);
      rows.set(row.column_ordinal, {selected, fields, reviewed, note});
      bind(selected, 'change', () => { if (!disposed && token === currentToken) { if (selected.checked) selections.add(row.column_ordinal); else selections.delete(row.column_ordinal); paint(); } }, true);
      bind(reviewed, 'change', () => { if (!disposed && token === currentToken) { model.setConfirmed(row.column_ordinal, reviewed.checked); paint(); } }, true);
    }
  }
  const errorLabels = {field_type: t('文字无效', 'Invalid text'), field_controls: t('不能含控制字符', 'Control characters are not allowed'), field_limit: t('字符数超限，未截断', 'Character limit exceeded; not truncated'),
    field_enum: t('选项无效', 'Invalid option'), confirmed_type: t('确认状态无效', 'Invalid review flag'), group_trim: t('请手动去除组名首尾空白', 'Manually correct surrounding group whitespace')};
  function paint() {
    if (disposed) return; const value = model.view(), key = value.source_epoch + ':' + value.source_phase + ':' + value.quant_family;
    if (key !== renderKey) { renderKey = key; buildRows(value); }
    panel.setAttribute('aria-busy', String(value.busy || value.source_phase === 'validating')); family.value = value.quant_family; family.disabled = value.source_phase !== 'ready';
    summary.textContent = t('当前类型原始样本列：', 'Source columns in selected type: ') + value.rows.length + ' · ' + t('已人工核对：', 'Manually reviewed: ') + value.rows.filter(r => r.confirmed).length;
    trace.textContent = value.binding ? t('输入版本：', 'Input version: ') + value.binding.dataset_version_id : '';
    let message = value.source_phase === 'validating' ? t('正在校验输入版本…', 'Validating source version…') : value.source_phase === 'invalid' ? t('输入版本未通过校验，不能编辑或保存。', 'Source validation failed; editing and saving are blocked.')
      : value.source_phase !== 'ready' ? t('请先提供已经校验的导入报告。', 'A validated import report is required.')
      : value.save_state === 'saving' ? t('正在向适配器保存草稿…', 'Saving draft through adapter…') : value.load_state === 'loading' ? t('正在从适配器载入草稿…', 'Loading draft through adapter…')
      : value.io_error === 'load_family_mismatch' ? t('载入草稿的量化类型不匹配，当前草稿未改变。', 'Loaded quantification type does not match; current draft is unchanged.')
      : value.io_error ? t('本次操作未确认，请检查草稿或适配器。', 'This operation is unconfirmed. Check the draft or adapter.')
      : value.save_state === 'acknowledged' ? t('适配器已确认草稿；尚未验证服务端持久化。', 'Adapter acknowledged the draft; server persistence is unverified.')
      : value.load_state === 'loaded' ? t('已载入适配器草稿；尚未验证服务端持久化。', 'Adapter draft loaded; server persistence is unverified.')
      : value.load_state === 'not_found' ? t('适配器未返回草稿，当前内容未改变。', 'Adapter returned no draft; current content is unchanged.')
      : value.dirty ? t('本地草稿已修改，尚未保存。', 'Local draft modified; not saved.') : t('本地草稿，尚未保存。', 'Local draft; not saved.');
    if (value.validation?.issues.some(v => v.code === 'group_control_conflict')) message += ' ' + t('同组对照标记不一致，不能整体确认。', 'Conflicting control flags in one group block overall confirmation.');
    state.textContent = message; save.disabled = !value.can_save; load.disabled = !value.can_load;
    confirm.disabled = !value.can_confirm_all || value.rows.every(r => r.confirmed);
    selectAll.disabled = !value.rows.length || value.busy; selectAll.checked = value.rows.length > 0 && selections.size === value.rows.length; selectAll.indeterminate = selections.size > 0 && selections.size < value.rows.length;
    for (const [field, controls] of batchControls) {
      controls.enabled.checked = batchEnabled.has(field); controls.enabled.disabled = value.busy || !value.rows.length;
      controls.value.value = batchValues[field]; controls.value.disabled = controls.enabled.disabled || !batchEnabled.has(field);
    }
    apply.disabled = value.busy || !selections.size || !batchEnabled.size;
    for (const row of value.rows) {
      const nodes = rows.get(row.column_ordinal); nodes.selected.checked = selections.has(row.column_ordinal); nodes.selected.disabled = value.busy;
      nodes.reviewed.checked = row.confirmed; nodes.reviewed.disabled = value.busy || !row.confirmed && row.confirmation_issues.length > 0;
      nodes.note.textContent = row.ambiguous ? t('样本名为空或重复；独立保留，不能确认。', 'Empty or duplicate label; retained independently, cannot confirm.')
        : row.confirmation_issues.some(e => e.code === 'group_trim') ? errorLabels.group_trim : row.confirmed ? t('已人工核对', 'Manually reviewed') : t('未确认', 'Unconfirmed');
      for (const field of MANUAL_FIELDS) {
        const controls = nodes.fields.get(field); if (controls.input.value !== row[field]) controls.input.value = row[field]; controls.input.disabled = value.busy;
        const invalid = value.validation?.issues.find(e => e.fatal && e.column_ordinal === row.column_ordinal && e.field === field);
        controls.input.setAttribute('aria-invalid', String(!!invalid)); controls.error.textContent = invalid ? errorLabels[invalid.code] || t('此字段未通过校验', 'This field failed validation') : '';
      }
    }
  }
  bind(family, 'change', () => { if (!disposed) model.setQuantFamily(family.value); });
  bind(selectAll, 'change', () => { if (!disposed) { selections = new Set(selectAll.checked ? model.view().rows.map(r => r.column_ordinal) : []); paint(); } });
  bind(apply, 'click', () => { if (!disposed) model.batchUpdate([...selections], Object.fromEntries([...batchEnabled].map(k => [k, batchValues[k]]))); });
  bind(confirm, 'click', () => { if (!disposed) model.confirmAll(); });
  bind(save, 'click', () => { if (!disposed) void model.save(); }); bind(load, 'click', () => { if (!disposed) void model.load({discardDirty: true}); });
  const unsubscribe = model.subscribe(paint); paint();
  return () => {
    if (disposed) return; disposed = true; unsubscribe(); rowListeners.forEach(stop => stop()); stableListeners.forEach(stop => stop());
    rowListeners = stableListeners = []; rows.clear(); selections.clear(); wipe(panel); root.replaceChildren();
  };
}
