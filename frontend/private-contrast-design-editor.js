let editorSerial = 0;

export function mountContrastDesignEditor(root, model, {t = zh => zh} = {}) {
  const doc = root.ownerDocument, serial = ++editorSerial;
  const make = (tag, text = '', cls = '') => { const node = doc.createElement(tag); node.textContent = text; node.className = cls; return node; };
  const button = (text, action) => { const node = make('button', text); node.type = 'button'; node.setAttribute('data-pcd-action', action); return node; };
  const panel = make('section', '', 'pcd'); panel.setAttribute('role', 'region'); panel.setAttribute('aria-label', t('比较设计（草稿）', 'Comparison design (draft)'));
  const header = make('div', '', 'pcd-heading'); header.append(make('h3', t('比较设计（草稿）', 'Comparison design (draft)')),
    make('span', t('仅当前页面 · 未保存', 'Page-local · not saved'), 'pcd-badge'));
  const intro = make('p', t('从来源样本设计的原始组名逐项选择比较方向，不自动推断对照、处理或重复。',
    'Choose each comparison direction from the source design’s exact group names. Control, treatment and replication are not inferred.'), 'pcd-note');
  const state = make('p', '', 'pcd-state'); state.setAttribute('role', 'status'); state.setAttribute('aria-live', 'polite');
  const counts = make('p', '', 'pcd-note'), sourceIssues = make('details', '', 'pcd-issues'), sourceSummary = make('summary', t('来源样本的待核对事项', 'Source sample review items')), sourceIssueList = make('div');
  sourceIssues.append(sourceSummary, sourceIssueList);
  const groupsScroll = make('div', '', 'pcd-source-scroll'); groupsScroll.tabIndex = 0; groupsScroll.setAttribute('role', 'region');
  groupsScroll.setAttribute('aria-label', t('来源组别与确认列数，可横向滚动', 'Scrollable source groups and confirmed-column counts'));
  const groupsTable = make('table'), head = make('thead'), headings = make('tr'), groupsBody = make('tbody');
  for (const text of [t('原始组名', 'Exact group name'), t('样本列数', 'Columns'), t('已确认列数', 'Confirmed columns')]) { const th = make('th', text); th.setAttribute('scope', 'col'); headings.append(th); }
  head.append(headings); groupsTable.append(head, groupsBody); groupsScroll.append(groupsTable);
  const list = make('div', '', 'pcd-list'), actions = make('div', '', 'pcd-actions'), add = button(t('添加比较', 'Add comparison'), 'add'); add.className = 'primary'; actions.append(add);
  const feedback = make('p', '', 'pcd-error'); feedback.setAttribute('role', 'status'); feedback.setAttribute('aria-live', 'polite');
  const direction = make('p', t('方向定义：FC = 分子 / 分母；log2FC = log2(FC)。正向变化表示分子组高于分母组。',
    'Direction: FC = numerator / denominator; log2FC = log2(FC). A positive change means the numerator is greater than the denominator.'), 'pcd-note');
  const boundary = make('p', t('这里只定义比较方向，不计算 FC、P 值或统计结果。组别汇总和模型效应由后续统计方法确定；还需处理矩阵、有效重复数、批次、配对及阈值。当前草稿未保存到服务器。',
    'Direction labels only: no FC, P values or statistics are calculated. Group summaries and model effects depend on the later method, processed matrix, effective replication, batches, pairing and thresholds. This draft is not saved to a server.'), 'pcd-note');
  const trace = make('details'), traceSummary = make('summary', t('来源版本', 'Source version')), traceText = make('p', '', 'pcd-trace'); trace.append(traceSummary, traceText);
  panel.append(header, intro, state, counts, groupsScroll, sourceIssues, list, actions, feedback, direction, boundary, trace); root.replaceChildren(panel);
  let disposed = false, unsubscribe = () => {}, token = 0, renderKey = null, groupKey = null, rowBindings = [], stableBindings = [], rows = new Map();
  const bind = (node, event, fn, row = false) => { node.addEventListener(event, fn); (row ? rowBindings : stableBindings).push(() => node.removeEventListener(event, fn)); };
  const wipe = node => { for (const child of [...(node.childNodes || node.children || [])]) wipe(child); if ('value' in node) node.value = ''; node.textContent = ''; };
  function clearRows() { token++; rowBindings.forEach(stop => stop()); rowBindings = []; rows.clear(); wipe(list); list.replaceChildren(); }
  const current = (epoch, captured) => !disposed && token === captured && model?.view().source_epoch === epoch && model.view().can_edit;
  function buildRows(value) {
    clearRows(); const captured = token, epoch = value.source_epoch;
    for (const [index, row] of value.contrasts.entries()) {
      const card = make('section', '', 'pcd-contrast'), top = make('div', '', 'pcd-heading'), label = make('strong', t('比较 ', 'Comparison ') + (index + 1));
      const remove = button(t('移除此比较', 'Remove comparison'), 'remove'); remove.setAttribute('data-pcd-id', row.contrast_id); top.append(label, remove);
      const grid = make('div', '', 'pcd-grid'), selectors = new Map();
      for (const [field, zh, english] of [['numerator_group', '分子组', 'Numerator group'], ['denominator_group', '分母组', 'Denominator group']]) {
        const select = make('select'), name = make('label', t(zh, english), 'field'), id = 'pcd-' + serial + '-' + captured + '-' + row.contrast_id + '-' + field;
        name.setAttribute('for', id); select.id = id; select.setAttribute('aria-label', t(zh, english) + ' · ' + (index + 1));
        select.setAttribute('data-pcd-field', field); select.setAttribute('data-pcd-id', row.contrast_id);
        const blank = make('option', t('请选择组别', 'Choose a group')); blank.value = ''; select.append(blank);
        for (const group of value.group_summary) { const option = make('option', group.name); option.value = group.name; select.append(option); }
        name.append(select); grid.append(name); selectors.set(field, select);
        bind(select, 'change', () => { if (current(epoch, captured)) { model.updateContrast(row.contrast_id, {[field]: select.value}); paint(); } }, true);
      }
      const formula = make('p', '', 'pcd-formula'); card.append(top, grid, formula); list.append(card); rows.set(row.contrast_id, {selectors, remove, formula});
      bind(remove, 'click', () => { if (current(epoch, captured)) model.removeContrast(row.contrast_id); }, true);
    }
  }
  const labels = {
    sample_design_no_columns: ['来源没有该量化类型的样本列。', 'No source columns for this quantification.'],
    sample_design_unconfirmed: ['来源样本信息尚未全部人工确认。', 'Not all source sample facts are explicitly confirmed.'],
    sample_design_ambiguous_sample_label: ['来源样本名为空或重复，原始列仍独立保留。', 'Empty or duplicate source labels; original columns remain separate.'],
    sample_design_group_control_conflict: ['同组对照标记不一致。', 'Control flags are inconsistent within a group.'],
    biological_replicate_policy_required: ['所选组包含技术、未知或混合重复类型，需先明确重复策略。', 'Selected groups contain technical, unknown or mixed replication; an explicit policy is required.'],
    replicate_labels_not_unique: ['所选组的重复编号为空或不唯一。', 'Replicate identifiers in a selected group are blank or not unique.'],
    contrast_source_invalid: ['来源样本设计的身份或完整性校验未通过。', 'Source sample-design identity or integrity validation failed.'],
    contrast_same_group: ['分子组和分母组不能相同；此修改未应用。', 'Numerator and denominator must differ; this change was not applied.'],
    contrast_duplicate_pair: ['该方向的比较已存在；此修改未应用。', 'That ordered comparison already exists; this change was not applied.'],
    contrast_duplicate_id: ['比较标识重复；此修改未应用。', 'Duplicate comparison identifier; this change was not applied.'],
    contrast_group_choice: ['请选择来源中的原始非空组名；此修改未应用。', 'Choose an exact nonblank source group; this change was not applied.'],
    contrast_count: ['最多允许 32 条比较；此修改未应用。', 'At most 32 comparisons are allowed; this change was not applied.'],
  };
  const message = code => t(...(labels[code] || ['此项仍需核对；没有自动修正信息。', 'This item needs review; no information was corrected automatically.']));
  const states = {
    waiting_source: ['请先提供已保存并核对来源的样本设计。', 'Provide a saved sample design with a verified source first.'],
    invalid: ['来源或比较填写未通过校验。', 'Source or comparison validation failed.'],
    draft_incomplete: ['请逐项填写每条比较的分子组和分母组。', 'Choose numerator and denominator groups for every comparison.'],
    blocked_by_sample_design: ['比较方向完整，来源样本信息或重复策略仍需核对。', 'Comparison directions are complete; source samples or replication policy still need review.'],
    ready_for_method_selection: ['比较方向已完整，可继续选择统计方法；尚不可运行分析。', 'Comparison directions are complete. Method selection may follow; analysis is not ready to run.'],
  };
  function paint() {
    if (disposed) return;
    const value = model?.view?.() || {state: 'waiting_source', group_summary: [], source_issues: [], contrasts: [], can_add: false, can_edit: false};
    if (value.state === 'disposed') { stop(); return; }
    const nextGroupKey = value.source_epoch + ':' + (value.binding?.design_sha256 || 'none');
    if (nextGroupKey !== groupKey) {
      groupKey = nextGroupKey; wipe(groupsBody); groupsBody.replaceChildren();
      for (const group of value.group_summary) { const row = make('tr'); row.append(make('td', group.name, 'pcd-group'), make('td', String(group.total_columns)), make('td', String(group.confirmed_columns))); groupsBody.append(row); }
    }
    const nextRenderKey = nextGroupKey + ':' + value.contrasts.map(row => row.contrast_id).join(',');
    if (nextRenderKey !== renderKey) { renderKey = nextRenderKey; buildRows(value); }
    state.textContent = value.source_checking ? t('正在核对来源版本与完整性…', 'Checking source version and integrity…') : t(...(states[value.state] || states.invalid));
    panel.setAttribute('aria-busy', String(!!value.source_checking));
    counts.textContent = value.binding ? t('当前量化类型：', 'Quantification: ') + value.binding.quant_family + ' · ' + t('已确认列：', 'Confirmed columns: ')
      + value.confirmed_columns + ' / ' + value.total_columns + ' · ' + t('未分组列：', 'Ungrouped columns: ') + value.unassigned_columns : '';
    sourceIssues.hidden = !value.source_issues.length; sourceIssueList.replaceChildren();
    for (const issue of value.source_issues) sourceIssueList.append(make('p', message(issue.code) + (issue.column_ordinals.length ? ' · ' + t('原列序号：', 'Source ordinals: ') + issue.column_ordinals.join(', ') : '')));
    add.disabled = !value.can_add; feedback.textContent = value.action_error ? message(value.action_error.code) : '';
    for (const row of value.contrasts) {
      const nodes = rows.get(row.contrast_id);
      for (const [field, select] of nodes.selectors) { select.value = row[field]; select.disabled = !value.can_edit || !value.group_summary.length; }
      nodes.remove.disabled = !value.can_edit;
      nodes.formula.textContent = 'FC = ' + (row.numerator_group || t('［分子组］', '[numerator]')) + ' / ' + (row.denominator_group || t('［分母组］', '[denominator]')) + ' · log2FC = log2(FC)';
    }
    traceText.textContent = value.binding ? t('数据版本：', 'Dataset version: ') + value.binding.dataset_version_id + '\n'
      + t('样本设计版本：', 'Sample design version: ') + value.binding.sample_design_version_id + '\nSHA256: ' + value.binding.design_sha256 : '';
  }
  function stop() {
    if (disposed) return; disposed = true; unsubscribe(); rowBindings.forEach(remove => remove()); stableBindings.forEach(remove => remove());
    rowBindings = stableBindings = []; token++; rows.clear(); wipe(panel); root.replaceChildren();
  }
  bind(add, 'click', () => { if (!disposed && model?.view().can_add) model.addContrast(); });
  unsubscribe = model?.subscribe?.(paint) || (() => {}); paint(); return stop;
}
