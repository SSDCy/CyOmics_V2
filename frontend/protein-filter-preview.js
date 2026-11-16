// Preview only. Bounded tri-state counts, never IDs, row masks or filtered data.
export const QUALITY_FLAG_NAMES = Object.freeze(['Reverse', 'Potential contaminant', 'Only identified by site']);
const failure = code => Object.assign(new Error(code), {code});

export function createProteinFilterCollector() {
  const bins = Array(27).fill(0);
  let records = 0, metadata = null, active = true;
  function abort() { bins.fill(0); records = 0; metadata = null; active = false; }
  function reject() { abort(); throw failure('QUALITY_FLAGS_INVALID_STREAM'); }
  function onQualityFlagRow({recordNumber, flags}) {
    if (!active || recordNumber !== records + 1 || !Array.isArray(flags) || flags.length !== 3) reject();
    let bin = 0;
    for (let i = 0; i < 3; i++) {
      const flag = flags[i];
      if (!flag || flag.name !== QUALITY_FLAG_NAMES[i] || !Array.isArray(flag.indices)
          || flag.indices.some(x => !Number.isSafeInteger(x) || x < 0)
          || new Set(flag.indices).size !== flag.indices.length
          || flag.available !== (flag.indices.length === 1)
          || (flag.available ? typeof flag.value !== 'string' : flag.value !== null)) reject();
      if (metadata && (flag.available !== metadata[i].available
          || flag.indices.length !== metadata[i].indices.length
          || flag.indices.some((x, k) => x !== metadata[i].indices[k]))) reject();
      const state = !flag.available ? 2 : flag.value === '+' ? 1 : flag.value === '' ? 0 : 2;
      bin += state * 3 ** i;
    }
    if (!metadata) metadata = Object.freeze(flags.map(({name, indices, available}) =>
      Object.freeze({name, indices: Object.freeze([...indices]), available})));
    bins[bin]++; records++;
  }
  function finish(preview) {
    try {
      if (!active || !records || records !== preview.rowCount || bins.reduce((a, b) => a + b, 0) !== records) reject();
      return Object.freeze({recordCount: records, flags: metadata, stateBins: Object.freeze([...bins])});
    } finally { abort(); }
  }
  return {onQualityFlagRow, finish, abort};
}

export function evaluateProteinFilter(summary, selectedMask) {
  if (!Number.isInteger(selectedMask) || selectedMask < 0 || selectedMask > 7
      || summary.flags.some((flag, i) => (selectedMask & (1 << i)) && !flag.available)) throw failure('QUALITY_RULE_UNAVAILABLE');
  let retained = 0, excluded = 0, pending = 0;
  const reasons = Array(8).fill(0);
  for (let bin = 0; bin < 27; bin++) {
    let hit = 0, unknown = false;
    for (let i = 0; i < 3; i++) if (selectedMask & (1 << i)) {
      const state = Math.floor(bin / 3 ** i) % 3;
      if (state === 1) hit |= 1 << i;
      else if (state === 2) unknown = true;
    }
    if (hit) { excluded += summary.stateBins[bin]; reasons[hit] += summary.stateBins[bin]; }
    else if (unknown) pending += summary.stateBins[bin];
    else retained += summary.stateBins[bin];
  }
  return Object.freeze({selectedMask, retained, excluded, pending, reasons: Object.freeze(reasons)});
}

export function createProteinFilterPreview() {
  let summary = null, selectedMask = 0, expanded = false;
  function reset() { summary = null; selectedMask = 0; expanded = false; }
  return {reset,
    load(next) { reset(); summary = next; },
    view() { return {summary, expanded, ...(summary ? evaluateProteinFilter(summary, selectedMask) : {selectedMask: 0})}; },
    setExpanded(open) { expanded = Boolean(open); },
    setRule(index, enabled) {
      if (!summary || !Number.isInteger(index) || index < 0 || index > 2 || !summary.flags[index].available) return false;
      selectedMask = enabled ? selectedMask | (1 << index) : selectedMask & ~(1 << index);
      return true;
    },
  };
}

export function mountProteinFilterPreview(root, model, t = zh => zh, onRuleChange = () => {}) {
  const initial = model.view();
  if (!initial.summary) { root.replaceChildren(); return () => {}; }
  const doc = root.ownerDocument, listeners = [], inputs = [], metrics = new Map();
  let disposed = false;
  function make(tag, text, cls) {
    const el = doc.createElement(tag);
    if (text !== undefined) el.textContent = String(text);
    if (cls) el.className = cls;
    return el;
  }
  function listen(el, event, fn) { el.addEventListener(event, fn); listeners.push(() => el.removeEventListener(event, fn)); }
  const details = make('details', undefined, 'iw-protein-filter'); details.open = initial.expanded;
  details.append(make('summary', t('蛋白条目过滤预览', 'Protein entry filter preview')),
    make('p', t('仅预览整表 ', 'Preview only: all ') + initial.summary.recordCount + t(' 条记录。未实际过滤、删行或改变任何数据与分析结果。', ' records. No filtering, row deletion or changes to data or analysis results.'), 'note'));
  const fieldset = make('fieldset'); fieldset.append(make('legend', t('预览规则（全部默认关闭）', 'Preview rules (all off by default)')));
  const explanations = [t('反向匹配条目', 'Reverse matches'), t('潜在污染物', 'Potential contaminants'), t('仅由位点鉴定的条目', 'Identified by site only')];
  initial.summary.flags.forEach((flag, index) => {
    const label = make('label', undefined, 'iw-quality-rule'), input = make('input');
    input.type = 'checkbox'; input.dataset.qualityRule = String(index); input.disabled = !flag.available;
    input.setAttribute('aria-label', flag.name);
    const text = make('span'); text.append(make('strong', flag.name), make('span', ' · ' + explanations[index]));
    text.append(make('small', flag.available ? t('原列号 #', 'Original column #') + (flag.indices[0] + 1)
      : !flag.indices.length ? t('列缺失：此规则不可用', 'Column missing: rule unavailable') : t('重复列：此规则不可用（', 'Duplicate columns: rule unavailable (') + flag.indices.length + ')'));
    label.append(input, text); fieldset.append(label); inputs.push(input);
    listen(input, 'change', () => { if (!disposed) { model.setRule(index, input.checked); paint(); onRuleChange(model.view()); } });
  });
  const ruleNote = make('p', t('仅原字符串“+”命中，空字符串不命中，其余值为未知；不去空白或改写。已选规则无确定命中但有未知时，列为待确认。',
    'Only the exact string "+" matches; an empty string does not. All other values are unknown, without trimming or rewriting. Selected rules with no definite match but an unknown value are pending.'), 'note');
  const totals = make('div', undefined, 'iw-quality-metrics');
  totals.setAttribute('role', 'status'); totals.setAttribute('aria-live', 'polite'); totals.setAttribute('aria-atomic', 'true');
  for (const [key, label] of [['retained', t('拟保留', 'Would retain')], ['excluded', t('拟排除（去重）', 'Would exclude (unique)')], ['pending', t('待确认', 'Pending')]]) {
    const cell = make('div'), value = make('strong'); value.dataset.qualityCount = key;
    cell.append(make('small', label), value); totals.append(cell); metrics.set(key, value);
  }
  const reasonTitle = make('p', t('已确定命中的所选规则组合（互斥，合计＝拟排除）', 'Definite selected-rule combinations (mutually exclusive; sum = would exclude)'), 'iw-quality-reason-title');
  const reasons = make('div'), state = make('p', '', 'note'); state.dataset.qualityState = '';
  details.append(fieldset, ruleNote, totals, state, reasonTitle, reasons); root.replaceChildren(details);
  function paint() {
    const view = model.view();
    inputs.forEach((input, i) => { input.checked = Boolean(view.selectedMask & (1 << i)); });
    for (const key of ['retained', 'excluded', 'pending']) metrics.get(key).textContent = String(view[key]);
    state.textContent = view.selectedMask === 0 ? t('未启用规则；以下仍只是整表预览。', 'No rules enabled; this remains a whole-table preview.')
      : t('规则仅影响本区预览；原表、QC、组成图、分组和ID关联均保持不变。', 'Rules affect this preview only; the table, QC, composition, groups and ID association remain unchanged.');
    const nodes = [];
    for (let mask = 1; mask < 8; mask++) if (view.reasons[mask]) {
      const names = QUALITY_FLAG_NAMES.filter((_, i) => mask & (1 << i));
      const item = make('p', names.join(' + ') + ': ' + view.reasons[mask]); item.dataset.qualityReason = String(mask); nodes.push(item);
    }
    reasons.replaceChildren(...(nodes.length ? nodes : [make('p', t('当前没有拟排除条目。', 'No entries would be excluded.'))]));
  }
  listen(details, 'toggle', event => { if (!disposed && event.target === details) model.setExpanded(details.open); });
  paint();
  return () => { model.setExpanded(details.open); disposed = true; for (const remove of listeners) remove(); root.replaceChildren(); };
}
