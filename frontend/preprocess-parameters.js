// Parameter confirmation only. No I/O, data transformation or task execution.
const FAMILIES = Object.freeze(['Intensity', 'LFQ intensity']);
const FLAGS = Object.freeze(['contaminant', 'reverse', 'site_only']);
const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
const context = value => typeof value === 'string' && value.length > 0 && value.length <= 512 && !/[^\x21-\x7e]/.test(value);
const emptyFlags = () => ({contaminant: false, reverse: false, site_only: false});
function normalizeSource(input) {
  if (!input || typeof input !== 'object' || !context(input.contextKey)) return null;
  const version = input.datasetVersion, available = input.availableQuantFamilies;
  if (!version || !['project_id', 'dataset_version_id', 'file_id'].every(key => uuid(version[key]))) return null;
  if (!Array.isArray(available) || available.length > 2 || available.some(value => !FAMILIES.includes(value)) || new Set(available).size !== available.length) return null;
  return Object.freeze({contextKey: input.contextKey, project_id: version.project_id, dataset_version_id: version.dataset_version_id, file_id: version.file_id,
    availableQuantFamilies: Object.freeze(FAMILIES.filter(value => available.includes(value)))});
}
const sourceKey = source => source ? JSON.stringify([source.contextKey, source.project_id, source.dataset_version_id, source.file_id, source.availableQuantFamilies]) : '';
const normalize = (input = {}) => Object.freeze({featureEnabled: input.featureEnabled === true, lang: input.lang === 'en' ? 'en' : 'zh', source: normalizeSource(input.source), onConfirm: typeof input.onConfirm === 'function' ? input.onConfirm : null});
function parameters(flags) {
  return Object.freeze({blank_policy: 'to_NA', filter_flags: Object.freeze({...flags}), imputation: 'none', input_scale: 'raw_nonnegative', log2: 'positive_only', normalization: 'none', output_scale: 'log2', preset: 'positive_log2_zero_na_no_impute_v1', pseudocount: 0, seed: null,
    steps: Object.freeze(['flags_filter', 'zero_and_blank_to_NA', 'log2_positive', 'normalization_none', 'imputation_none']), zero_policy: 'to_NA'});
}

export function mountPreprocessParameters(root, initialProps = {}) {
  const doc = root.ownerDocument;
  let props = normalize(initialProps), family = '', flags = emptyFlags(), epoch = 0, pending = null, confirmed = false, message = '', disposed = false, ui = null;
  const t = (zh, en) => props.lang === 'en' ? en : zh;
  const make = (tag, cls = '') => { const node = doc.createElement(tag); node.className = cls; return node; };
  function invalidate() { epoch++; pending = null; confirmed = false; message = ''; }
  function clear() { invalidate(); family = ''; flags = emptyFlags(); }
  const current = token => !disposed && props.featureEnabled && epoch === token;
  const editable = () => !disposed && props.featureEnabled && props.source !== null && props.source.availableQuantFamilies.length > 0;
  const canConfirm = () => editable() && props.source.availableQuantFamilies.includes(family) && props.onConfirm !== null && pending === null && !confirmed;
  function wipe(node) { for (const child of [...node.children]) wipe(child); if ('value' in node) node.value = ''; if ('checked' in node) node.checked = false; node.textContent = ''; }
  function removeUI() { if (!ui) return; ui.stops.forEach(stop => stop()); wipe(ui.panel); ui.panel.remove(); ui = null; }
  function changeFamily() {
    if (!editable()) { render(); return; }
    const value = ui.select.value;
    if (!props.source.availableQuantFamilies.includes(value)) { render(); return; }
    if (value !== family) { invalidate(); family = value; message = 'changed'; }
    render();
  }
  function changeFlag(key) {
    if (!editable()) { render(); return; }
    const value = ui.flags.get(key).input.checked === true;
    if (value !== flags[key]) { invalidate(); flags = {...flags, [key]: value}; message = 'changed'; }
    render();
  }
  function confirm() {
    if (!canConfirm()) return;
    const token = ++epoch, source = props.source, callback = props.onConfirm;
    const request = Object.freeze({contextKey: source.contextKey, source: Object.freeze({project_id: source.project_id, dataset_version_id: source.dataset_version_id, file_id: source.file_id}), quant_family: family, parameters: parameters(flags)});
    pending = token; message = ''; render();
    const settle = accepted => { if (current(token) && pending === token) { pending = null; confirmed = accepted; message = accepted ? 'confirmed' : 'error'; render(); } };
    try { Promise.resolve(callback(request)).then(() => settle(true), () => settle(false)); }
    catch { settle(false); }
  }
  function build() {
    const panel = make('section', 'qpp'), title = make('h3'), intro = make('p', 'qpp-note'), source = make('p', 'qpp-source'), controls = make('div', 'qpp-controls');
    panel.setAttribute('role', 'region');
    const familyLabel = make('label', 'qpp-family'), familyText = make('span'), select = make('select'); select.setAttribute('data-qpp-field', 'family'); familyLabel.append(familyText, select); controls.append(familyLabel);
    const fieldset = make('fieldset'), legend = make('legend'), flagRows = new Map(), stops = []; fieldset.append(legend);
    for (const key of FLAGS) {
      const row = make('label', 'qpp-flag'), input = make('input'), label = make('span'); input.type = 'checkbox'; input.setAttribute('data-qpp-flag', key); row.append(input, label); fieldset.append(row); flagRows.set(key, {input, label});
      const change = () => changeFlag(key); input.addEventListener('change', change); stops.push(() => input.removeEventListener('change', change));
    }
    const algorithm = make('section', 'qpp-algorithm'), algorithmTitle = make('h4'), algorithmList = make('ul'); algorithm.append(algorithmTitle, algorithmList);
    const actions = make('div', 'qpp-actions'), button = make('button'), state = make('p', 'qpp-state'), boundary = make('p', 'qpp-note'); button.type = 'button'; button.setAttribute('data-qpp-action', 'confirm'); actions.append(button); state.setAttribute('role', 'status'); state.setAttribute('aria-live', 'polite');
    button.addEventListener('click', confirm); stops.push(() => button.removeEventListener('click', confirm)); select.addEventListener('change', changeFamily); stops.push(() => select.removeEventListener('change', changeFamily));
    panel.append(title, intro, source, controls, fieldset, algorithm, actions, state, boundary); root.append(panel);
    ui = {panel, title, intro, source, familyText, select, legend, flags: flagRows, algorithmTitle, algorithmList, button, state, boundary, stops};
  }
  function render() {
    if (disposed) return;
    if (!props.featureEnabled) { removeUI(); return; }
    if (!ui) build();
    ui.panel.setAttribute('aria-label', t('数据预处理参数', 'Data preprocessing parameters'));
    ui.title.textContent = t('数据预处理参数', 'Data preprocessing parameters');
    ui.intro.textContent = t('选择一种定量类型和需要剔除的标记，再明确确认参数。', 'Choose one quantification type and flags to remove, then explicitly confirm the parameters.');
    ui.source.textContent = t('数据源版本：', 'Dataset version: ') + (props.source?.dataset_version_id || t('待提供', 'Not provided'));
    ui.familyText.textContent = t('定量类型（单选）', 'Quantification type (choose one)'); ui.select.setAttribute('aria-label', ui.familyText.textContent);
    const placeholder = make('option'); placeholder.value = ''; placeholder.textContent = t('请选择', 'Choose'); ui.select.textContent = ''; ui.select.append(placeholder);
    for (const value of props.source?.availableQuantFamilies || []) { const option = make('option'); option.value = value; option.textContent = value; ui.select.append(option); }
    ui.select.value = family; ui.select.disabled = !editable();
    ui.legend.textContent = t('质量标记过滤（默认不剔除）', 'Quality flags (no removal by default)');
    const labels = {contaminant: ['Potential contaminant · 潜在污染物', 'Potential contaminant'], reverse: ['Reverse · 反向库匹配', 'Reverse'], site_only: ['Only identified by site · 仅位点鉴定', 'Only identified by site']};
    for (const key of FLAGS) { const row = ui.flags.get(key); row.label.textContent = t(...labels[key]); row.input.setAttribute('aria-label', row.label.textContent); row.input.checked = flags[key]; row.input.disabled = !editable(); }
    ui.algorithmTitle.textContent = t('固定算法', 'Fixed method'); ui.algorithmList.textContent = '';
    const rules = [
      ['先按明确勾选的质量标记过滤。', 'Filter only the explicitly selected quality flags first.'],
      ['原始 0 与原始空值分别留痕，再转为 NA。', 'Preserve separate traces of original zeros and blanks, then convert both to NA.'],
      ['原始正值使用 R base::log2；不加伪计数。', 'Apply R base::log2 to original positive values; no pseudocount.'],
      ['不做标准化，不填补缺失值。', 'No normalization or missing-value imputation.']
    ];
    for (const pair of rules) { const row = make('li'); row.textContent = t(...pair); ui.algorithmList.append(row); }
    ui.button.textContent = t('确认预处理参数', 'Confirm preprocessing parameters'); ui.button.disabled = !canConfirm();
    const state = !props.source ? ['等待提供已验证的数据源。', 'Waiting for a validated data source.'] : props.source.availableQuantFamilies.length === 0 ? ['当前数据源没有可用定量类型。', 'No quantification type is available for this source.'] : !props.onConfirm ? ['参数处理回调尚未接入，不能确认。', 'The parameter handler is not connected; confirmation is disabled.'] : !family ? ['请明确选择一种定量类型。', 'Explicitly choose one quantification type.'] : pending !== null ? ['正在回传参数；未提交或运行任务。', 'Returning parameters; no task has been submitted or run.'] : confirmed ? ['本页参数已确认；未提交或运行任务。', 'Parameters confirmed on this page; no task has been submitted or run.'] : message === 'error' ? ['参数回传未能确认，请手动重试；不会自动重试。', 'Parameter handoff could not be confirmed. Retry manually; no automatic retry occurs.'] : message === 'changed' ? ['参数已更改，请重新确认。', 'Parameters changed; confirm them again.'] : ['参数尚未确认。', 'Parameters have not been confirmed.'];
    ui.state.textContent = t(...state);
    ui.boundary.textContent = t('本卡只确认参数，不执行计算。Intensity/LFQ 是替代定量类型；不推断组别、时间或重复。', 'This card confirms parameters only; it does not compute. Intensity/LFQ are alternative quantification types. Groups, time points and replicates are not inferred.');
  }
  function update(nextProps = {}) {
    if (disposed) return false;
    const next = normalize(nextProps);
    if (sourceKey(props.source) !== sourceKey(next.source) || props.featureEnabled !== next.featureEnabled) clear();
    else if (props.onConfirm !== next.onConfirm) invalidate();
    props = next; render(); return true;
  }
  function dispose() { if (disposed) return false; disposed = true; clear(); removeUI(); props = normalize(); return true; }
  render();
  return Object.freeze({update, dispose});
}
