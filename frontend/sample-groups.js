// Page-memory configuration only: no matrix mutation, persistence or network access.
const types = ['intensity', 'lfq'];
const controls = /[\u0000-\u001f\u007f-\u009f]/;
function groupError(value) {
  if (controls.test(value)) return 'control';
  if (!value.trim()) return 'required';
  if (Array.from(value.trim()).length > 80) return 'length';
  return '';
}

export function createSampleGroups() {
  let rows = new Map(), assignments = new Map(), confirmed = new Set(), expanded = new Set();
  function reset() { rows.clear(); assignments.clear(); confirmed.clear(); expanded.clear(); }
  function load(preview) {
    reset();
    const columns = new Map([
      ['intensity', preview.intensityColumns], ['lfq', preview.lfqColumns],
    ]);
    const counts = new Map(types.map(type => {
      const names = new Map();
      for (const col of columns.get(type)) names.set(col.sampleName, (names.get(col.sampleName) || 0) + 1);
      return [type, names];
    }));
    const shared = new Map();
    for (const type of types) {
      rows.set(type, columns.get(type).map(col => {
        const name = col.sampleName;
        const ambiguous = !name.trim() || counts.get(type).get(name) !== 1;
        const reusable = !ambiguous && types.every(k => counts.get(k).get(name) === 1);
        if (reusable && !shared.has(name)) shared.set(name, {});
        return {index: col.index, sampleName: name, columnName: col.columnName,
          ambiguous, key: reusable ? shared.get(name) : {}};
      }));
    }
  }
  function view(type) {
    const items = (rows.get(type) || []).map(row => {
      const group = assignments.get(row.key) || '';
      return {index: row.index, sampleName: row.sampleName, columnName: row.columnName,
        ambiguous: row.ambiguous, group, error: groupError(group)};
    });
    const counts = new Map();
    for (const row of items) if (!row.error) counts.set(row.group.trim(), (counts.get(row.group.trim()) || 0) + 1);
    return {rows: items, counts, expanded: expanded.has(type),
      status: confirmed.has(type) ? 'confirmed' : items.some(x => x.group !== '') ? 'draft' : 'empty',
      canConfirm: items.length > 0 && items.every(x => !x.ambiguous && !x.error)};
  }
  function setGroup(type, index, value) {
    const row = rows.get(type)?.find(x => x.index === index);
    if (!row || typeof value !== 'string' || (assignments.get(row.key) || '') === value) return;
    assignments.set(row.key, value);
    for (const k of types) if (rows.get(k)?.some(x => x.key === row.key)) confirmed.delete(k);
  }
  function confirm(type) {
    if (!view(type).canConfirm) return false;
    for (const row of rows.get(type)) assignments.set(row.key, assignments.get(row.key).trim());
    confirmed.add(type);
    return true;
  }
  return {load, reset, view, setGroup, confirm,
    setExpanded(type, open) { if (open) expanded.add(type); else expanded.delete(type); },
    clear() { assignments.clear(); confirmed.clear(); },
  };
}

export function mountSampleGroups(root, model, type, t = zh => zh) {
  const doc = root.ownerDocument, listeners = [], inputs = new Map();
  let disposed = false;
  function make(tag, text, cls) {
    const el = doc.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  }
  function listen(el, name, fn) { el.addEventListener(name, fn); listeners.push(() => el.removeEventListener(name, fn)); }
  const details = make('details', undefined, 'iw-sample-groups');
  details.open = model.view(type).expanded;
  const summary = make('summary', t('设置样本分组', 'Set sample groups'));
  const status = make('span', '', 'iw-sg-status');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  summary.append(status);
  const note = make('p', t('仅本页内存配置；未做 QC、未计算、未保存服务器。不推断组别或重复；仅同名且各类型内唯一的样本共用手动组名。',
    'Page-memory configuration only; no QC, computation or server save. Groups and replicates are not inferred. Manual group names are shared only for exactly matching, unique sample names in each type.'), 'note');
  const wrapper = make('div', undefined, 'iw-scroll');
  const table = make('table');
  table.setAttribute('aria-label', t('样本分组设置', 'Sample group settings'));
  const head = make('thead'), headRow = make('tr');
  for (const title of [t('原始样本名（只读）', 'Original sample name (read only)'), t('组名（最多80字符）', 'Group name (up to 80 characters)')]) {
    const th = make('th', title); th.setAttribute('scope', 'col'); headRow.append(th);
  }
  head.append(headRow);
  const body = make('tbody');
  for (const row of model.view(type).rows) {
    const tr = make('tr'), name = make('td'), cell = make('td');
    name.append(make('span', row.sampleName));
    if (row.ambiguous) name.append(make('small', t('身份有歧义：', 'Ambiguous identity: ') + '#' + (row.index + 1) + ' · ' + row.columnName, 'iw-sg-error'));
    const input = make('input');
    input.type = 'text'; input.value = row.group; input.autocomplete = 'off'; input.spellcheck = false;
    input.dataset.sgColumn = String(row.index);
    input.setAttribute('aria-label', t('组名：', 'Group: ') + row.sampleName + ' · ' + (row.index + 1));
    const error = make('small', '', 'iw-sg-error');
    const errorId = 'sg-' + type + '-' + row.index + '-error';
    error.id = errorId;
    input.setAttribute('aria-describedby', errorId);
    inputs.set(row.index, {input, error});
    listen(input, 'input', () => { if (!disposed) { model.setGroup(type, row.index, input.value); paint(); } });
    cell.append(input, error); tr.append(name, cell); body.append(tr);
  }
  table.append(head, body); wrapper.append(table);
  const counts = make('p', '', 'iw-sg-counts');
  const actions = make('div', undefined, 'row iw-sg-actions');
  const confirm = make('button', t('确认当前类型分组', 'Confirm groups for this type'));
  confirm.type = 'button'; confirm.dataset.sgAction = 'confirm';
  const clear = make('button', t('清空本表全部分组', 'Clear all groups for this table'), 'link');
  clear.type = 'button'; clear.dataset.sgAction = 'clear';
  actions.append(confirm, clear);
  const message = make('p', '', 'note');
  details.append(summary, note, wrapper, counts, actions, message);
  root.replaceChildren(details);
  function paint() {
    const current = model.view(type);
    status.textContent = ' · ' + (current.status === 'confirmed' ? t('已确认', 'Confirmed') : current.status === 'draft' ? t('待确认', 'Unconfirmed changes') : t('未分组', 'Not grouped'));
    details.dataset.groupState = current.status;
    confirm.disabled = !current.canConfirm || current.status === 'confirmed';
    counts.textContent = t('当前量化类型：', 'Current quantification type: ') + current.rows.length + t(' 个样本列；组数：', ' sample columns; groups: ') + current.counts.size
      + (current.counts.size ? ' · ' + [...current.counts].map(([name, n]) => name + ': ' + n).join(' / ') : '');
    message.textContent = current.rows.some(x => x.ambiguous)
      ? t('原始样本名重复或为空：不猜测配对，此量化类型不能确认。', 'Duplicate or blank original names: pairing is not inferred and this type cannot be confirmed.')
      : !current.rows.length ? t('此类型没有样本列。', 'No sample columns for this type.')
        : current.status === 'confirmed' ? t('已确认本页配置；修改任一共享组名会撤销相关确认。', 'Page configuration confirmed; editing a shared group name revokes affected confirmations.')
          : t('请填写所有样本的有效组名，再确认当前类型。允许单组；此处不检查统计设计。', 'Fill every sample with a valid group name, then confirm this type. A single group is allowed; statistical design is not checked.');
    for (const row of current.rows) {
      const {input, error} = inputs.get(row.index);
      if (input.value !== row.group) input.value = row.group;
      input.setAttribute('aria-invalid', String(row.error === 'control' || row.error === 'length'));
      error.textContent = row.error === 'control' ? t('组名不能包含控制字符。', 'Group names cannot contain control characters.')
        : row.error === 'length' ? t('组名最多80个字符；内容未截断。', 'Group names must be at most 80 characters; input has not been truncated.') : '';
    }
  }
  listen(details, 'toggle', () => { if (!disposed) model.setExpanded(type, details.open); });
  listen(confirm, 'click', () => { if (!disposed) { model.confirm(type); paint(); } });
  listen(clear, 'click', () => { if (!disposed) { model.clear(); paint(); } });
  paint();
  return () => { model.setExpanded(type, details.open); disposed = true; for (const remove of listeners) remove(); root.replaceChildren(); };
}
