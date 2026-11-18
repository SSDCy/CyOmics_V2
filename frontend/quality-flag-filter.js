// Controlled presentation only: the host owns source identity, tasks and results.
const DEFINITIONS = Object.freeze([
  ['reverse', 'Reverse', '反向库匹配标记；勾选后请求后端剔除这些行。', 'Reverse-database match flag; selecting requests removal of these rows.'],
  ['potentialContaminant', 'Potential contaminant', '潜在污染物标记；勾选后请求后端剔除这些行。', 'Potential-contaminant flag; selecting requests removal of these rows.'],
  ['onlyIdentifiedBySite', 'Only identified by site', '仅位点鉴定标记；勾选后请求后端剔除这些行。', 'Site-only identification flag; selecting requests removal of these rows.']
]);
const KEYS = DEFINITIONS.map(([key]) => key);
const textId = value => typeof value === 'string' && value.length > 0 && value.length <= 512 && !/[\x00-\x1f\x7f]/.test(value);
const contextId = value => textId(value) && !/[^\x21-\x7e]/.test(value);
const sameFlags = (a, b) => KEYS.every(key => a[key] === b[key]);
const sameSource = (a, b) => a.contextKey === b.contextKey && a.sourceVersion === b.sourceVersion && a.quantFamily === b.quantFamily;
const count = value => Number.isSafeInteger(value) && value >= 0 ? String(value) : null;

function normalize(input = {}) {
  const flags = {}, availableFlags = {};
  let validFlags = true;
  for (const key of KEYS) {
    const value = input.flags?.[key];
    if (value !== undefined && typeof value !== 'boolean') validFlags = false;
    flags[key] = value === true;
    availableFlags[key] = typeof input.availableFlags?.[key] === 'boolean' ? input.availableFlags[key] : null;
  }
  let result = null;
  const dto = input.result;
  if (dto && typeof dto === 'object' && KEYS.every(key => typeof dto.flags?.[key] === 'boolean')) {
    const resultFlags = Object.freeze(Object.fromEntries(KEYS.map(key => [key, dto.flags[key]])));
    result = Object.freeze({
      contextKey: dto.contextKey, sourceVersion: dto.sourceVersion, quantFamily: dto.quantFamily,
      flags: resultFlags,
      status: ['queued', 'running', 'succeeded', 'failed', 'unknown'].includes(dto.status) ? dto.status : 'unknown',
      counts: Object.freeze(Object.fromEntries(['before', 'after', 'removed'].map(key => [key, count(dto.counts?.[key])]))),
      reasonCounts: Object.freeze(Object.fromEntries(KEYS.map(key => [key, count(dto.reasonCounts?.[key])]))),
      overlapCount: count(dto.overlapCount)
    });
  }
  return Object.freeze({
    featureEnabled: input.featureEnabled === true, lang: input.lang === 'en' ? 'en' : 'zh',
    contextKey: contextId(input.contextKey) ? input.contextKey : '',
    sourceVersion: textId(input.sourceVersion) ? input.sourceVersion : '',
    quantFamily: ['Intensity', 'LFQ'].includes(input.quantFamily) ? input.quantFamily : '',
    flags: Object.freeze(flags), availableFlags: Object.freeze(availableFlags),
    validFlags, validBusy: input.busy === undefined || typeof input.busy === 'boolean', busy: input.busy === true,
    result, onApply: typeof input.onApply === 'function' ? input.onApply : null,
    onChange: typeof input.onChange === 'function' ? input.onChange : null
  });
}

export function mountQualityFlagFilter(root, initialProps = {}) {
  const doc = root.ownerDocument;
  let props = normalize(initialProps), disposed = false, generation = 0, pending = null, feedback = '';
  let ui = null;
  const t = (zh, en) => props.lang === 'en' ? en : zh;
  const make = (tag, cls = '') => { const node = doc.createElement(tag); node.className = cls; return node; };
  const currentResult = () => readySource() && props.result && props.validFlags && KEYS.every(key => !props.flags[key] || props.availableFlags[key] === true) && sameSource(props, props.result) && sameFlags(props.flags, props.result.flags) ? props.result : null;
  const readySource = () => Boolean(props.contextKey && props.sourceVersion && props.quantFamily);
  function busy() { return props.busy || pending !== null || ['queued', 'running'].includes(currentResult()?.status); }
  function block() {
    if (!props.onApply) return 'not_connected';
    if (!readySource()) return 'source';
    if (!props.validFlags || !props.validBusy) return 'configuration';
    if (!KEYS.some(key => props.availableFlags[key] === true)) return 'no_columns';
    if (KEYS.some(key => props.flags[key] && props.availableFlags[key] !== true)) return 'selected_missing';
    return busy() ? 'busy' : '';
  }
  const request = flags => Object.freeze({contextKey: props.contextKey, sourceVersion: props.sourceVersion, quantFamily: props.quantFamily, flags: Object.freeze({...flags})});
  const isCurrent = token => !disposed && props.featureEnabled && generation === token;
  function reset() { generation++; pending = null; feedback = ''; }
  function wipe(node) { for (const child of [...node.children]) wipe(child); if ('checked' in node) node.checked = false; node.textContent = ''; }
  function removeUI() {
    if (!ui) return;
    ui.stops.forEach(stop => stop());
    wipe(ui.panel); ui.panel.remove(); ui = null;
  }
  function change(key, input) {
    if (disposed || !props.featureEnabled || !props.onChange || !readySource() || !props.validFlags || !props.validBusy || busy() || props.availableFlags[key] !== true) { render(); return; }
    const next = {...props.flags, [key]: input.checked === true}, callback = props.onChange, token = generation;
    try {
      const returned = callback(request(next));
      Promise.resolve(returned).catch(() => { if (isCurrent(token)) { feedback = 'settings_error'; render(); } });
    } catch { if (isCurrent(token)) feedback = 'settings_error'; }
    render(); // The host must explicitly update controlled values.
  }
  function apply() {
    if (disposed || !props.featureEnabled || block()) return;
    const token = ++generation, snapshot = request(props.flags), callback = props.onApply;
    pending = token; feedback = ''; render();
    const settle = message => { if (isCurrent(token) && pending === token) { pending = null; feedback = message; render(); } };
    try { Promise.resolve(callback(snapshot)).then(() => settle('awaiting'), () => settle('request_error')); }
    catch { settle('request_error'); }
  }
  function build() {
    const panel = make('section', 'qff'), title = make('h3'), intro = make('p', 'qff-note'), source = make('p', 'qff-source');
    panel.setAttribute('role', 'region');
    const fieldset = make('fieldset'), legend = make('legend'); fieldset.append(legend);
    const rows = new Map(), stops = [];
    for (const [key, name] of DEFINITIONS) {
      const row = make('label', 'qff-option'), checkbox = make('input'), copy = make('span'), heading = make('strong'), description = make('span', 'qff-description'), availability = make('span', 'qff-availability');
      checkbox.type = 'checkbox'; checkbox.setAttribute('aria-label', name); checkbox.setAttribute('data-qff-flag', key);
      heading.textContent = name; copy.append(heading, description, availability); row.append(checkbox, copy); fieldset.append(row);
      const handler = () => change(key, checkbox); checkbox.addEventListener('change', handler); stops.push(() => checkbox.removeEventListener('change', handler));
      rows.set(key, {checkbox, description, availability});
    }
    const actions = make('div', 'qff-actions'), button = make('button'), state = make('p', 'qff-state'), message = make('p', 'qff-message');
    button.type = 'button'; button.setAttribute('data-qff-action', 'apply'); button.addEventListener('click', apply); stops.push(() => button.removeEventListener('click', apply)); actions.append(button);
    for (const node of [state, message]) { node.setAttribute('role', 'status'); node.setAttribute('aria-live', 'polite'); }
    const results = make('section', 'qff-results'), resultsTitle = make('h4'), counts = make('dl', 'qff-counts'), values = new Map(), labels = new Map();
    for (const key of ['before', 'after', 'removed', ...KEYS, 'overlap']) {
      const item = make('div'), label = make('dt'), value = make('dd'); value.setAttribute('data-qff-count', key); item.append(label, value); counts.append(item); values.set(key, value); labels.set(key, label);
    }
    const overlapNote = make('p', 'qff-note'), boundary = make('p', 'qff-note'); results.append(resultsTitle, counts, overlapNote); panel.append(title, intro, source, fieldset, actions, state, message, results, boundary); root.append(panel);
    ui = {panel, title, intro, source, legend, rows, button, state, message, results, resultsTitle, values, labels, overlapNote, boundary, stops};
  }
  function render() {
    if (disposed) return;
    if (!props.featureEnabled) { removeUI(); return; }
    if (!ui) build();
    ui.panel.setAttribute('aria-label', t('质量标记过滤', 'Quality flag filtering'));
    ui.title.textContent = t('质量标记过滤', 'Quality flag filtering');
    ui.intro.textContent = t('明确勾选需要剔除的标记；默认全部保留。过滤由后端执行。', 'Explicitly select flags to remove; all rows are retained by default. Filtering runs on the backend.');
    ui.source.textContent = t('源数据版本：', 'Source version: ') + (props.sourceVersion || t('待确认', 'Not confirmed')) + ' · ' + t('定量类型：', 'Quantification type: ') + (props.quantFamily || t('待确认', 'Not confirmed'));
    ui.legend.textContent = t('选择过滤标记', 'Select filtering flags');
    for (const [key, , zh, en] of DEFINITIONS) {
      const row = ui.rows.get(key), available = props.availableFlags[key];
      row.checkbox.checked = props.flags[key];
      row.checkbox.disabled = !props.onChange || !readySource() || !props.validFlags || !props.validBusy || busy() || available !== true;
      row.description.textContent = t(zh, en);
      row.availability.textContent = available === true ? t('来源列存在', 'Source column present') : available === false ? t('来源缺少此列，不能应用该标记。', 'Source column missing; this flag cannot be applied.') : t('来源列尚未确认；不按空列处理。', 'Source column not confirmed; it is not treated as an empty column.');
    }
    const blocked = block(), result = currentResult();
    ui.button.disabled = Boolean(blocked);
    ui.button.textContent = busy() ? t('处理中…', 'Processing…') : feedback === 'request_error' ? t('重试应用过滤', 'Retry applying filters') : t('应用过滤', 'Apply filters');
    const notices = {
      not_connected: ['过滤服务尚未接入。', 'Filtering service is not connected.'],
      source: ['等待宿主提供有效的来源版本与数据类型。', 'Waiting for a valid source version and quantification type from the host.'],
      configuration: ['过滤开关或忙碌状态配置无效，请核对宿主设置。', 'Invalid flag or busy-state configuration; check the host settings.'],
      no_columns: ['尚无可用质量标记列；缺列与未确认状态不会按空列处理。', 'No quality flag columns are available; missing or unconfirmed columns are not treated as empty.'],
      selected_missing: ['已选标记列缺失或尚未确认，请先核对来源。', 'A selected flag column is missing or unconfirmed; check the source first.'],
      busy: ['正在处理，请勿重复应用。', 'Processing; do not apply again.']
    };
    const states = {queued: ['后端任务排队中。', 'Backend task queued.'], running: ['后端任务运行中。', 'Backend task running.'], succeeded: ['宿主提供的过滤结果已就绪。', 'Filtering results supplied by the host are ready.'], failed: ['后端任务未完成；请核对任务后手动重试。', 'Backend task did not complete; check the task before retrying manually.'], unknown: ['任务状态未确认，请先核对任务。', 'Task status is unconfirmed; check the task first.']};
    const state = blocked && blocked !== 'busy' ? notices[blocked] : result ? states[result.status] : blocked ? notices[blocked] : feedback === 'awaiting' ? ['请求回调已结束；等待宿主更新任务状态。', 'Request callback finished; waiting for a task-state update from the host.'] : ['尚未应用过滤。', 'Filters have not been applied.'];
    ui.state.textContent = t(...state);
    ui.message.textContent = feedback === 'request_error' ? t('本次请求结果未确认。请核对任务状态后再手动重试；不会自动重发。', 'The request outcome is unconfirmed. Check task status before retrying manually; no automatic retry occurs.') : feedback === 'settings_error' ? t('未能确认设置变更，请重新操作。', 'The settings change could not be confirmed; try again.') : !props.onChange ? t('开关设置回调尚未接入，当前为只读。', 'The flag-change callback is not connected; flags are read-only.') : '';
    ui.resultsTitle.textContent = t('过滤结果摘要', 'Filtering result summary');
    const labelPairs = {before: ['过滤前行数', 'Rows before'], after: ['过滤后行数', 'Rows after'], removed: ['剔除行数', 'Rows removed'], reverse: ['Reverse 剔除命中', 'Reverse removal matches'], potentialContaminant: ['Potential contaminant 剔除命中', 'Potential-contaminant removal matches'], onlyIdentifiedBySite: ['Only identified by site 剔除命中', 'Site-only removal matches'], overlap: ['至少两项标记重叠命中', 'Rows matching at least two selected flags']};
    for (const [key, node] of ui.values) {
      const value = result?.status === 'succeeded' ? key === 'overlap' ? result.overlapCount : KEYS.includes(key) ? result.reasonCounts[key] : result.counts[key] : null;
      node.textContent = value ?? t('待计算', 'Pending calculation'); ui.labels.get(key).textContent = t(...labelPairs[key]);
    }
    ui.overlapNote.textContent = t('各剔除原因可能重叠，不能相加；重叠数仅显示后端提供的值。', 'Removal reasons can overlap and must not be summed; overlap counts are shown only when supplied by the backend.');
    ui.boundary.textContent = t('原文件与原始预览保持不变。此面板不做标准化、填补或差异分析，不判断分析就绪。', 'The original file and raw preview stay unchanged. This panel performs no normalization, imputation or differential analysis and does not determine analysis readiness.');
  }
  function update(nextProps = {}) {
    if (disposed) return false;
    const next = normalize(nextProps);
    if (!sameSource(props, next) || !sameFlags(props.flags, next.flags) || !sameFlags(props.availableFlags, next.availableFlags) || props.validFlags !== next.validFlags || props.featureEnabled !== next.featureEnabled) reset();
    props = next; render(); return true;
  }
  function dispose() { if (disposed) return false; disposed = true; reset(); removeUI(); props = normalize(); return true; }
  render();
  return Object.freeze({update, dispose});
}
