const uuid = v => typeof v === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v);
const sha = v => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v);
const text = (v, max) => typeof v === 'string' && v.length > 0 && v.length <= max;
const count = v => Number.isSafeInteger(v) && v >= 0;
const bound = v => v && uuid(v.project_id) && uuid(v.raw_file_id) && sha(v.expected_raw_sha256);
const matches = (a, b) => bound(a) && bound(b) && ['project_id', 'raw_file_id', 'expected_raw_sha256'].every(k => a[k] === b[k]);
const phases = new Set(['idle', 'parsing', 'checking', 'ready', 'failed', 'unknown', 'unavailable']);
const reasons = Object.freeze({
  invalid_import_request: ['导入请求无效。', 'The import request is invalid.'],
  method_not_allowed: ['导入请求方式不可用。', 'The import request method is unavailable.'],
  import_report_too_large: ['导入报告超出大小限制。', 'The import report exceeds the size limit.'],
  import_request_too_large: ['导入请求超出大小限制。', 'The import request exceeds the size limit.'],
  unsupported_media_type: ['导入请求格式不受支持。', 'The import request format is unsupported.'],
  txt_import_failed: ['TXT 格式或内容校验未通过。', 'TXT format or content validation failed.'],
  import_busy: ['导入服务繁忙，请稍后手动操作。', 'Import is busy. Try again manually later.'],
  import_rate_limited: ['导入请求过于频繁。', 'Too many import requests.'],
  import_timeout: ['导入请求超时，结果尚未确认。', 'The import request timed out. Its outcome is unconfirmed.'],
  import_result_unknown: ['导入结果尚未确认，请只读查询。', 'The import outcome is unconfirmed. Use read-only lookup.'],
  dataset_version_not_found: ['尚未查到已保存的导入报告。', 'A saved import report has not been found.'],
  import_unavailable: ['暂时无法确认导入服务。', 'The import service cannot be confirmed right now.'],
  import_executor_unavailable: ['解析执行服务暂不可用。', 'The parsing executor is unavailable.'],
  import_source_integrity_failed: ['原始文件完整性校验未通过。', 'Original-file integrity validation failed.'],
  parser_release_mismatch: ['解析器版本校验未通过。', 'Parser release validation failed.'],
  import_persisted_report_invalid: ['已保存报告校验未通过。', 'Saved-report validation failed.'],
  import_control_failed: ['导入运行状态未能确认。', 'Import execution status could not be confirmed.'],
});
const issueCodes = new Set(['INVALID_SOURCE_REF','INVALID_LIMITS','INVALID_CONTROL','CONTROL_FAILED','INPUT_READ_FAILED','INVALID_CHUNK','SOURCE_INTEGRITY_MISMATCH','INVALID_UTF8','NUL_BYTE','EMPTY_FILE','TXT_HEADER_REQUIRED','TXT_PROTEIN_IDS_HEADER_COUNT','TXT_ROW_WIDTH','TXT_QUOTE_SYNTAX','RESOURCE_LIMIT','CANCELLED']);
const limitKeys = new Set(['max_bytes','max_records','max_columns','max_cells','max_field_units','max_member_occurrences','max_id_units']);
const safeIssue = v => v && Object.keys(v).length === 5 && ['code','record_number','line','column_ordinal','limit_key'].every(k => Object.hasOwn(v,k))
  && issueCodes.has(v.code) && ['record_number','line','column_ordinal'].every(k => v[k] === null || count(v[k]) && v[k] <= 26214401)
  && (v.limit_key === null || limitKeys.has(v.limit_key));

function readyReport(value, binding) {
  const r = value.report;
  if (value.report_verified !== true || value.report_saved !== true || !r || !matches(r.source, binding)
    || !text(value.source_filename, 1024) || !text(value.report_version_id, 256)
    || r.status !== 'parsed' || r.source_complete !== true || r.observed_raw_sha256 !== binding.expected_raw_sha256
    || !text(r.parser_contract_version, 128) || r.association_status !== 'not_requested' || r.scientific_analysis_status !== 'not_started') return false;
  for (const k of ['row_count', 'column_count', 'intensity_sample_count', 'lfq_sample_count', 'quant_exact_zero_string_cells', 'quant_empty_string_cells']) if (!count(r[k])) return false;
  if (r.row_count < 1 || r.row_count > 10000 || r.column_count < 1 || r.column_count > 512 || r.row_count * r.column_count > 2000000
    || r.intensity_sample_count + r.lfq_sample_count > r.column_count || !Array.isArray(r.columns) || r.columns.length !== r.column_count) return false;
  let intensity = 0, lfq = 0, units = 0;
  for (let i = 0; i < r.columns.length; i++) {
    const c = r.columns[i];
    if (!c || c.ordinal !== i || typeof c.name_raw !== 'string' || ![null, 'Intensity', 'LFQ intensity'].includes(c.quant_family)) return false;
    if (c.quant_family === null ? c.sample_label_raw !== null : typeof c.sample_label_raw !== 'string') return false;
    units += c.name_raw.length + (c.sample_label_raw?.length || 0); if (units > 2000000) return false;
    if (c.quant_family === 'Intensity') intensity++;
    if (c.quant_family === 'LFQ intensity') lfq++;
  }
  return intensity === r.intensity_sample_count && lfq === r.lfq_sample_count
    && r.quant_exact_zero_string_cells + r.quant_empty_string_cells <= r.row_count * (intensity + lfq);
}

// Pure display adapter. Caller validation/authorization and persistence proof
// remain controller responsibilities; this component never interprets HTTP.
export function mountPrivateImportReport(root, model = null, options = {}) {
  const t = options.t || (zh => zh), doc = root.ownerDocument;
  let binding = bound(options.binding) ? {...options.binding} : null;
  let disposed = false, pending = null, columnsOpen = false, traceOpen = false, unsubscribe = () => {};
  const handlers = [];
  const make = (tag, value = '', cls = '') => { const n = doc.createElement(tag); n.textContent = value; n.className = cls; return n; };
  const listen = (node, event, fn) => { node.addEventListener(event, fn); handlers.push([node, event, fn]); };
  const panel = make('section', '', 'pirv'); panel.setAttribute('role', 'region'); panel.setAttribute('aria-label', t('TXT 导入报告', 'TXT import report'));
  const header = make('div', '', 'pirv-header'), title = make('h3', t('导入报告', 'Import report'));
  const actions = make('div', '', 'pirv-actions'), start = make('button', t('解析此文件', 'Parse this file'), 'primary'), check = make('button', t('查询导入结果', 'Check import result'));
  start.type = check.type = 'button'; actions.append(start, check); header.append(title, actions);
  const status = make('p', '', 'pirv-status'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite'); status.setAttribute('aria-atomic', 'true');
  const content = make('div'), boundary = make('p', t('未关联 FASTA。本区展示导入检查结果，后续分析请查看相应模块。', 'FASTA not linked. This section shows import checks. See the relevant modules for subsequent analyses.'), 'pirv-boundary');
  panel.append(header, status, content, boundary); root.replaceChildren(panel);
  let stopPreprocessing = () => {};
  if (options.preprocessing?.featureEnabled === true) {
    const preprocessingRoot = make('div'); panel.append(preprocessingRoot);
    const config = {...options.preprocessing, reportModel: model, binding, t};
    let active = true, stop = () => {};
    stopPreprocessing = () => { active = false; stop(); };
    void import('./private-preprocess-owner.js').then(({mountPrivatePreprocessOwner}) => {
      if (active && !disposed) stop = mountPrivatePreprocessOwner(preprocessingRoot, config);
    }).catch(() => {
      if (active && !disposed) preprocessingRoot.append(make('p', t('预处理模块暂不可用；导入报告不受影响。', 'The preprocessing module is unavailable; the import report is unaffected.'), 'pirv-boundary'));
    });
  }
  function get() {
    try {
      if (!binding || !model || !['status', 'subscribe', 'start', 'check'].every(k => typeof model[k] === 'function')) return null;
      const v = model.status();
      return v && phases.has(v.phase) && v.source_verified === true && matches(v.binding, binding) ? v : null;
    } catch { return null; }
  }
  const wipe = node => { for (const child of [...(node.childNodes || node.children || [])]) wipe(child); node.ontoggle = null; node.textContent = ''; };
  function clearContent() { wipe(content); content.replaceChildren(); }
  function allowed(kind, value, ownPending = false) {
    return !disposed && (!pending || ownPending) && !!value && (kind === 'start'
      ? ['idle', 'failed'].includes(value.phase) && value.can_start === true
      : ['unknown', 'failed', 'unavailable'].includes(value.phase) && value.can_check === true);
  }
  function paint() {
    if (disposed) return;
    let v = get(), ready = false;
    try { ready = v?.phase === 'ready' && readyReport(v, binding); } catch { ready = false; }
    if (v?.phase === 'ready' && !ready) v = null;
    const phase = pending ? (pending === 'start' ? 'parsing' : 'checking') : v?.phase || 'unavailable';
    panel.setAttribute('aria-busy', String(['parsing', 'checking'].includes(phase)));
    start.disabled = !allowed('start', v); start.hidden = ['ready', 'unknown'].includes(phase);
    check.disabled = !allowed('check', v); check.hidden = !v || !['unknown', 'failed', 'checking', 'unavailable'].includes(phase);
    const labels = {idle: ['可解析此已保存文件。', 'This stored file is ready to parse.'], parsing: ['正在解析此文件…', 'Parsing this file…'], checking: ['正在确认导入结果…', 'Checking import result…'],
      ready: ['导入报告已保存', 'Import report saved'], failed: ['导入未完成，请重新检查。', 'Import is incomplete. Please check again.'], unknown: ['结果待确认', 'Result awaiting confirmation'], unavailable: ['暂时无法确认导入服务或报告。', 'Import service or report could not be confirmed.']};
    status.textContent = t(...labels[phase]); clearContent();
    if (!ready || phase !== 'ready') {
      if (['failed','unknown','unavailable'].includes(phase) && v?.error_verified === true && Object.hasOwn(reasons, v.error_code)) {
        content.append(make('p', t(...reasons[v.error_code]), 'pirv-boundary'));
        if (v.error_code === 'txt_import_failed' && Array.isArray(v.issues) && v.issues.length <= 8 && v.issues.every(safeIssue)) {
          for (const issue of v.issues) {
            const fields = [issue.code];
            for (const [key,zh,en] of [['record_number','记录','Record'],['line','行','Line'],['column_ordinal','列序号（从 0 开始）','Column ordinal (from 0)']]) if (issue[key] !== null) fields.push(t(zh,en) + ': ' + issue[key]);
            if (issue.limit_key !== null) fields.push(issue.limit_key);
            content.append(make('p', fields.join(' · '), 'pirv-boundary'));
          }
        }
      }
      return;
    }
    const r = v.report, metrics = make('dl', '', 'pirv-metrics');
    for (const [key, zh, en] of [['row_count', '记录行数', 'Data rows'], ['column_count', '原始列数', 'Original columns'], ['intensity_sample_count', 'Intensity 样本列', 'Intensity sample columns'], ['lfq_sample_count', 'LFQ 样本列', 'LFQ sample columns']]) {
      const item = make('div'); item.append(make('dt', t(zh, en)), make('dd', String(r[key]))); metrics.append(item);
    }
    const counts = make('p', t('定量列的原始“0”数量', 'Original "0" strings in quantification columns') + ': ' + r.quant_exact_zero_string_cells
      + ' · ' + t('定量列空字符串数量', 'Empty strings in quantification columns') + ': ' + r.quant_empty_string_cells, 'pirv-counts');
    const columns = make('details'), summary = make('summary', t('原始列与样本名', 'Original columns and sample names')); columns.open = columnsOpen;
    const scroll = make('div', '', 'pirv-scroll'); scroll.tabIndex = 0; scroll.setAttribute('role', 'region'); scroll.setAttribute('aria-label', t('原始列与样本名表格，可横向滚动', 'Scrollable original columns and sample names'));
    const table = make('table'), head = make('thead'), hrow = make('tr');
    for (const name of ['#', t('原始列名', 'Original column'), t('定量类型', 'Quantification'), t('样本原名', 'Original sample name')]) { const th = make('th', name); th.setAttribute('scope', 'col'); hrow.append(th); }
    head.append(hrow); const body = make('tbody');
    for (const c of r.columns) { const row = make('tr'); for (const value of [c.ordinal + 1, c.name_raw, c.quant_family ?? t('不适用', 'Not applicable'), c.sample_label_raw ?? t('不适用', 'Not applicable')]) row.append(make('td', String(value))); body.append(row); }
    table.append(head, body); scroll.append(table); columns.append(summary, scroll);
    const trace = make('details'), traceTitle = make('summary', t('文件与追溯信息', 'File and provenance')); trace.open = traceOpen; const facts = make('dl', '', 'pirv-facts');
    for (const [key, value] of [[t('原文件名', 'Original filename'), v.source_filename], [t('报告版本 ID', 'Report version ID'), v.report_version_id], [t('解析器版本', 'Parser version'), r.parser_contract_version], [t('原始 SHA256', 'Original SHA256'), binding.expected_raw_sha256]]) facts.append(make('dt', key), make('dd', value));
    trace.append(traceTitle, facts); columns.ontoggle = () => { if (!disposed) columnsOpen = columns.open; }; trace.ontoggle = () => { if (!disposed) traceOpen = trace.open; };
    content.append(metrics, counts, columns, trace);
  }
  function invoke(kind) {
    const value = get(); if (!allowed(kind, value)) return;
    pending = kind; paint();
    if (!allowed(kind, get(), true)) { pending = null; paint(); return; }
    try {
      const operation = model[kind]();
      void Promise.resolve(operation).catch(() => {}).finally(() => { pending = null; if (!disposed) paint(); });
    } catch { pending = null; paint(); }
  }
  listen(start, 'click', () => invoke('start')); listen(check, 'click', () => invoke('check'));
  if (binding && model && ['status', 'subscribe', 'start', 'check'].every(k => typeof model[k] === 'function')) {
    try { unsubscribe = model.subscribe(paint); if (typeof unsubscribe !== 'function') { unsubscribe = () => {}; model = null; } } catch { model = null; }
  }
  paint();
  return () => {
    if (disposed) return; disposed = true;
    try { unsubscribe(); } finally {
      stopPreprocessing();
      for (const [node, event, fn] of handlers) node.removeEventListener(event, fn);
      wipe(panel); root.replaceChildren(); model = null; binding = null; pending = null;
    }
  };
}
