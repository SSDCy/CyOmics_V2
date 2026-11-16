// Manual sample facts only, held in page memory. No parsing of experimental design.
const TYPES = ['intensity', 'lfq'];
export const METADATA_FIELDS = Object.freeze(['condition', 'control', 'replicate', 'replicateType']);
const blank = () => ({condition: '', control: 'unknown', replicate: '', replicateType: 'unknown'});
const enums = {control: ['unknown', 'yes', 'no'], replicateType: ['unknown', 'biological', 'technical']};
const limits = {condition: 160, replicate: 40};
const controlChars = /[\u0000-\u001f\u007f-\u009f]/;
function errors(fields) {
  const result = {};
  for (const field of Object.keys(limits)) {
    if (controlChars.test(fields[field])) result[field] = 'control';
    else if (Array.from(fields[field]).length > limits[field]) result[field] = 'length';
  }
  for (const field of ['condition', 'replicate']) if (!fields[field].trim()) result[field] ||= 'required';
  if (fields.control === 'unknown') result.control = 'required';
  if (fields.replicateType === 'unknown') result.replicateType = 'required';
  return result;
}
const empty = fields => METADATA_FIELDS.every(field => fields[field] === blank()[field]);
export function createSampleMetadata() {
  let rows = new Map(), expanded = false, batchExpanded = false, type = 'intensity', selected = new Set(), draft;
  function clearBatch() { selected.clear(); draft = Object.fromEntries(METADATA_FIELDS.map(field => [field, {enabled: false, clear: false, value: blank()[field]}])); }
  function reset() { rows.clear(); expanded = false; batchExpanded = false; type = 'intensity'; clearBatch(); }
  reset();
  function load(preview) {
    reset();
    const columns = {intensity: preview.intensityColumns, lfq: preview.lfqColumns};
    const counts = Object.fromEntries(TYPES.map(kind => {
      const map = new Map(); for (const col of columns[kind]) map.set(col.sampleName, (map.get(col.sampleName) || 0) + 1); return [kind, map];
    }));
    const shared = new Map();
    for (const kind of TYPES) rows.set(kind, columns[kind].map(col => {
      const name = col.sampleName, ambiguous = !name.trim() || counts[kind].get(name) !== 1;
      const reusable = !ambiguous && TYPES.every(k => counts[k].get(name) === 1);
      if (reusable && !shared.has(name)) shared.set(name, {fields: blank(), confirmed: false});
      return {index: col.index, sampleName: name, columnName: col.columnName, ambiguous,
        entry: reusable ? shared.get(name) : {fields: blank(), confirmed: false}};
    }));
  }
  function view(kind = type) {
    const items = (rows.get(kind) || []).map(row => {
      const fields = Object.freeze({...row.entry.fields}), problems = Object.freeze(errors(fields));
      return Object.freeze({index: row.index, sampleName: row.sampleName, columnName: row.columnName, ambiguous: row.ambiguous, fields, errors: problems,
        status: row.entry.confirmed ? 'confirmed' : empty(fields) ? 'empty' : Object.keys(problems).length ? 'draft' : 'ready'});
    });
    return Object.freeze({rows: Object.freeze(items), expanded, batchExpanded, type,
      confirmedCount: items.filter(row => row.status === 'confirmed').length,
      canConfirm: items.length > 0 && items.every(row => !row.ambiguous && !Object.keys(row.errors).length),
      selected: Object.freeze(kind === type ? [...selected] : []),
      batch: Object.freeze(Object.fromEntries(METADATA_FIELDS.map(field => [field, Object.freeze({...draft[field]})])))});
  }
  function patchEntry(entry, patch) {
    let changed = false;
    for (const field of METADATA_FIELDS) if (Object.hasOwn(patch, field) && typeof patch[field] === 'string'
      && (!Object.hasOwn(enums, field) || enums[field].includes(patch[field])) && entry.fields[field] !== patch[field]) {
      entry.fields[field] = patch[field]; changed = true;
    }
    if (changed) entry.confirmed = false;
    return changed;
  }
  function update(kind, index, patch) {
    const row = rows.get(kind)?.find(row => row.index === index);
    return Boolean(row && patch && patchEntry(row.entry, patch));
  }
  function confirm(kind = type) {
    if (!view(kind).canConfirm) return false;
    for (const row of rows.get(kind)) row.entry.confirmed = true;
    return true;
  }
  function scope() {
    const chosen = (rows.get(type) || []).filter(row => selected.has(row.index)), entries = new Set(chosen.map(row => row.entry));
    return {selectedCount: chosen.length, linkedCount: (rows.get(type === 'intensity' ? 'lfq' : 'intensity') || []).filter(row => entries.has(row.entry)).length};
  }
  return {load, reset, view, update, confirm, scope,
    setType(kind) { if (TYPES.includes(kind) && type !== kind) { type = kind; clearBatch(); } },
    setExpanded(open) { expanded = Boolean(open); }, setBatchExpanded(open) { batchExpanded = Boolean(open); },
    select(index, checked) { if (!rows.get(type)?.some(row => row.index === index)) return false; if (checked) selected.add(index); else selected.delete(index); return true; },
    selectAll(checked) { selected = new Set(checked ? (rows.get(type) || []).map(row => row.index) : []); },
    setBatch(field, patch) {
      if (!METADATA_FIELDS.includes(field) || !patch) return;
      for (const flag of ['enabled', 'clear']) if (typeof patch[flag] === 'boolean') draft[field][flag] = patch[flag];
      if (typeof patch.value === 'string' && (!Object.hasOwn(enums, field) || enums[field].includes(patch.value))) draft[field].value = patch.value;
    },
    applyBatch() {
      if (!selected.size || !METADATA_FIELDS.some(field => draft[field].enabled)) return false;
      const patch = Object.fromEntries(METADATA_FIELDS.filter(field => draft[field].enabled).map(field => [field, draft[field].clear ? blank()[field] : draft[field].value]));
      for (const row of rows.get(type)) if (selected.has(row.index)) patchEntry(row.entry, patch);
      clearBatch(); return true;
    },
    clear() { for (const kind of TYPES) for (const row of rows.get(kind) || []) { row.entry.fields = blank(); row.entry.confirmed = false; } clearBatch(); },
  };
}

export function mountSampleMetadata(root, model, type, t = zh => zh) {
  model.setType(type);
  const doc = root.ownerDocument, listeners = [], controls = new Map(), selections = new Map(), statuses = new Map(), batchControls = new Map();
  let disposed = false;
  const make = (tag, text, cls) => { const node = doc.createElement(tag); if (text !== undefined) node.textContent = String(text); if (cls) node.className = cls; return node; };
  const listen = (node, event, fn) => { node.addEventListener(event, fn); listeners.push(() => node.removeEventListener(event, fn)); };
  const labels = {
    condition: t('处理条件', 'Treatment condition'),
    control: t('是否对照', 'Control sample'), replicate: t('重复编号', 'Replicate identifier'), replicateType: t('重复类型', 'Replicate type'),
  };
  const options = {
    control: [['unknown', t('未指定', 'Not specified')], ['yes', t('是', 'Yes')], ['no', t('否', 'No')]],
    replicateType: [['unknown', t('未知', 'Unknown')], ['biological', t('生物学重复', 'Biological')], ['technical', t('技术重复', 'Technical')]],
  };
  function fieldInput(field, label) {
    const node = make(options[field] ? 'select' : 'input');
    node.setAttribute('aria-label', label);
    if (options[field]) for (const [value, title] of options[field]) { const option = make('option', title); option.value = value; node.append(option); }
    else { node.type = 'text'; node.autocomplete = 'off'; node.spellcheck = false; }
    return node;
  }
  function button(label, action) { const node = make('button', label); node.type = 'button'; node.dataset.smAction = action; return node; }
  function checkbox(label) { const node = make('input'); node.type = 'checkbox'; node.setAttribute('aria-label', label); return node; }
  const details = make('details', undefined, 'iw-sample-metadata'); details.open = model.view(type).expanded;
  const summary = make('summary', t('样本信息', 'Sample information')), state = make('span', '', 'iw-sm-status'); summary.append(state); details.append(summary);
  details.append(make('p', t('仅本页内存中的人工填写信息，不推断样本名、处理或重复。Intensity / LFQ 两侧唯一且完全同名才共享；两种量化不表示两倍生物样本。原手动组名及确认独立。',
    'Manual facts in page memory only. Names do not infer treatment or replicates. Only exact, unique names in both Intensity and LFQ share facts; two quantifications do not double the biological samples. Manual groups and their confirmation remain independent.'), 'note'));
  const batch = make('details', undefined, 'iw-sm-batch'); batch.open = model.view(type).batchExpanded;
  batch.append(make('summary', t('批量填写选中样本', 'Batch edit selected samples')));
  const selectAll = checkbox(t('全选当前量化类型', 'Select all in current quantification')), selectLabel = make('label', undefined, 'iw-sm-all');
  selectLabel.append(selectAll, make('span', t('全选当前类型样本', 'Select all current-type samples'))); batch.append(selectLabel);
  const scope = make('p', '', 'note'); batch.append(scope);
  const batchGrid = make('div', undefined, 'iw-sm-batch-grid');
  for (const field of METADATA_FIELDS) {
    const wrap = make('div', undefined, 'iw-sm-batch-field'), enabled = checkbox(t('修改：', 'Modify: ') + labels[field]), clear = checkbox(t('清空或设回未知：', 'Clear or reset to unknown: ') + labels[field]);
    enabled.dataset.smBatchEnable = field; clear.dataset.smBatchClear = field;
    const title = make('label'); title.append(enabled, make('span', labels[field]));
    const input = fieldInput(field, t('批量：', 'Batch: ') + labels[field]); input.dataset.smBatchField = field;
    const clearLabel = make('label'); clearLabel.append(clear, make('span', t('清空 / 设回未知', 'Clear / reset to unknown')));
    wrap.append(title, input, clearLabel); batchGrid.append(wrap); batchControls.set(field, {enabled, clear, input});
    listen(enabled, 'change', () => { if (!disposed) { model.setBatch(field, {enabled: enabled.checked}); paint(); } });
    listen(clear, 'change', () => { if (!disposed) { model.setBatch(field, {clear: clear.checked}); paint(); } });
    listen(input, options[field] ? 'change' : 'input', () => { if (!disposed) { model.setBatch(field, {value: input.value}); paint(); } });
  }
  batch.append(batchGrid, make('p', t('只修改勾选字段；其余字段原样保留。重复编号将原样赋给所选样本，不自动连号。切量化类型会清除选中项与批量草稿。',
    'Only enabled fields change; all others remain untouched. Replicate identifiers are copied literally, never numbered automatically. Changing quantification clears selections and batch drafts.'), 'note'));
  const apply = button(t('应用到选中样本（不自动确认）', 'Apply to selected samples (without confirmation)'), 'apply'); batch.append(apply); details.append(batch);
  const wrapper = make('div', undefined, 'iw-scroll iw-sm-scroll'), table = make('table'); table.setAttribute('aria-label', t('样本信息表', 'Sample metadata table'));
  const head = make('thead'), tr = make('tr');
  for (const label of [t('选择', 'Select'), t('原始样本名（只读）', 'Original sample name (read only)'), ...METADATA_FIELDS.map(field => labels[field]), t('核对状态', 'Review status')]) { const th = make('th', label); th.setAttribute('scope', 'col'); tr.append(th); }
  head.append(tr); const body = make('tbody');
  const first = model.view(type);
  for (const row of first.rows) {
    const line = make('tr'), selectedCell = make('td'), selected = checkbox(t('选择样本列 ', 'Select sample column ') + (row.index + 1)); selected.dataset.smSelect = String(row.index);
    selectedCell.append(selected); selections.set(row.index, selected); line.append(selectedCell);
    const name = make('td'); name.append(make('span', row.sampleName));
    const identity = make('small', '#' + (row.index + 1) + ' · ' + row.columnName, 'note'); name.append(identity);
    if (row.ambiguous) name.append(make('small', t('身份有歧义：独立记录，不能整表确认。', 'Ambiguous identity: kept separate; table confirmation blocked.'), 'iw-sg-error'));
    line.append(name); const fields = new Map();
    for (const field of METADATA_FIELDS) {
      const cell = make('td'), input = fieldInput(field, labels[field] + ' · #' + (row.index + 1) + ' · ' + row.sampleName), error = make('small', '', 'iw-sg-error');
      input.dataset.smField = field; input.dataset.smIndex = String(row.index);
      const id = 'sm-' + type + '-' + row.index + '-' + field; error.id = id; input.setAttribute('aria-describedby', id);
      cell.append(input, error); fields.set(field, {input, error}); line.append(cell);
      listen(input, options[field] ? 'change' : 'input', () => { if (!disposed) { model.update(type, row.index, {[field]: input.value}); paint(); } });
    }
    controls.set(row.index, fields); const status = make('td'); status.dataset.smRowStatus = String(row.index); statuses.set(row.index, status); line.append(status); body.append(line);
    listen(selected, 'change', () => { if (!disposed) { model.select(row.index, selected.checked); paint(); } });
  }
  table.append(head, body); wrapper.append(table); details.append(wrapper);
  const actions = make('div', undefined, 'row iw-sm-actions'), confirm = button(t('确认当前类型样本信息', 'Confirm current-type sample information'), 'confirm'), clear = button(t('清空全部样本信息（保留手动组名）', 'Clear all sample information (keep manual groups)'), 'clear'); actions.append(confirm, clear);
  const note = make('p', '', 'note'); note.setAttribute('role', 'status'); note.setAttribute('aria-live', 'polite'); details.append(actions, note);
  root.replaceChildren(details);
  const errorLabels = {required: t('未提供，保持待确认。', 'Not provided; remains unconfirmed.'), control: t('不能含控制字符。', 'Control characters are not allowed.'), length: t('内容过长，未截断。', 'Too long; not truncated.')};
  function paint() {
    if (disposed) return;
    const current = model.view(type), selected = new Set(current.selected), counts = model.scope();
    state.textContent = ' · ' + t('已人工核对 ', 'Manually checked ') + current.confirmedCount + '/' + current.rows.length;
    details.dataset.metadataState = current.rows.length && current.confirmedCount === current.rows.length ? 'confirmed' : 'unconfirmed';
    scope.textContent = t('当前类型选中 ', 'Selected in current type: ') + counts.selectedCount + t(' 项；关联另一量化类型同步 ', '; linked columns synchronized in the other type: ') + counts.linkedCount + t(' 项（不重复计算生物样本）', ' (not additional biological samples)');
    selectAll.checked = current.rows.length > 0 && selected.size === current.rows.length;
    selectAll.indeterminate = selected.size > 0 && selected.size < current.rows.length;
    apply.disabled = !selected.size || !METADATA_FIELDS.some(field => current.batch[field].enabled);
    confirm.disabled = !current.canConfirm || current.confirmedCount === current.rows.length;
    note.textContent = t('已人工核对填写信息，不验证实验设计 / 重复真实性。未知或不完整信息保持待确认；草稿不阻止导入，不用于科研计算。',
      'Manually checked entered information; experimental design and replicate authenticity are not validated. Unknown or incomplete facts remain unconfirmed. Drafts do not block import and are not used for scientific computation.');
    for (const field of METADATA_FIELDS) {
      const nodes = batchControls.get(field), value = current.batch[field]; nodes.enabled.checked = value.enabled; nodes.clear.checked = value.clear; nodes.clear.disabled = !value.enabled;
      nodes.input.disabled = !value.enabled || value.clear; if (nodes.input.value !== value.value) nodes.input.value = value.value;
    }
    for (const row of current.rows) {
      selections.get(row.index).checked = selected.has(row.index);
      statuses.get(row.index).textContent = row.status === 'confirmed' ? t('已人工核对', 'Manually checked') : row.status === 'ready' ? t('字段齐备，待确认', 'Complete fields, awaiting confirmation') : t('待确认', 'Unconfirmed');
      for (const field of METADATA_FIELDS) {
        const nodes = controls.get(row.index).get(field); if (nodes.input.value !== row.fields[field]) nodes.input.value = row.fields[field];
        const invalid = row.errors[field] && row.errors[field] !== 'required';
        nodes.input.setAttribute('aria-invalid', String(Boolean(invalid))); nodes.error.textContent = invalid ? errorLabels[row.errors[field]] : '';
      }
    }
  }
  listen(selectAll, 'change', () => { if (!disposed) { model.selectAll(selectAll.checked); paint(); } });
  listen(apply, 'click', () => { if (!disposed) { model.applyBatch(); paint(); } });
  listen(confirm, 'click', () => { if (!disposed) { model.confirm(type); paint(); } });
  listen(clear, 'click', () => { if (!disposed) { model.clear(); paint(); } });
  listen(details, 'toggle', () => { if (!disposed) model.setExpanded(details.open); });
  listen(batch, 'toggle', () => { if (!disposed) model.setBatchExpanded(batch.open); });
  paint();
  return () => { if (disposed) return; model.setExpanded(details.open); model.setBatchExpanded(batch.open); disposed = true; for (const remove of listeners) remove(); root.replaceChildren(); };
}
