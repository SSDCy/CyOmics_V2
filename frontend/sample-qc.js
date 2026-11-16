// Local aggregate counts only. No raw rows, network, storage or quality verdicts.
const decimal = /^[+-]?(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:[eE][+-]?\d+)?$/;
export function classifySampleValue(raw) {
  if (raw === '') return 'emptyString';
  const value = raw.trim();
  if (!value) return 'whitespace';
  if (/^(?:NaN|[+-]?Infinity)$/.test(value)) return 'nonFinite';
  const match = value.match(decimal);
  if (!match) return 'format';
  // Decide true zero from its coefficient, before Number rounding or range loss.
  if (!/[1-9]/.test((match[1] || '') + (match[2] || '') + (match[3] || ''))) return 'zero';
  const number = Number(value);
  if (!Number.isFinite(number)) return 'nonFinite';
  if (number === 0) return 'format'; // Nonzero coefficient underflow: range anomaly.
  return number > 0 ? 'positive' : 'negative';
}

export function createSampleQCCollector() {
  const counts = new Map();
  let records = 0, active = true;
  const fail = () => { abort(); throw Object.assign(new Error('SAMPLE_QC_INVALID_STREAM'), {code: 'SAMPLE_QC_INVALID_STREAM'}); };
  function abort() { active = false; counts.clear(); records = 0; }
  function onQuantificationRow({recordNumber, cells}) {
    if (!active || recordNumber !== records + 1 || !Array.isArray(cells)) fail();
    const seen = new Set();
    for (const {columnIndex, value} of cells) {
      if (!Number.isSafeInteger(columnIndex) || columnIndex < 0 || seen.has(columnIndex) || typeof value !== 'string') fail();
      seen.add(columnIndex);
      if (!records) counts.set(columnIndex, {positive: 0, zero: 0, emptyString: 0, whitespace: 0, negative: 0, format: 0, nonFinite: 0});
      if (!counts.has(columnIndex)) fail();
      counts.get(columnIndex)[classifySampleValue(value)]++;
    }
    if (seen.size !== counts.size) fail();
    records++;
  }
  function finish(preview) {
    try {
      if (!active || !records || preview.rowCount !== records) fail();
      const columns = [...preview.intensityColumns, ...preview.lfqColumns];
      if (columns.length !== counts.size || new Set(columns.map(x => x.index)).size !== counts.size) fail();
      const summarize = cols => Object.freeze(cols.map(({index, columnName, sampleName}) => {
        const c = counts.get(index);
        if (!c) fail();
        const empty = c.emptyString + c.whitespace, abnormal = c.negative + c.format + c.nonFinite;
        if (c.positive + c.zero + empty + abnormal !== records) fail();
        return Object.freeze({index, columnName, sampleName, ...c, empty, abnormal});
      }));
      return Object.freeze({recordCount: records,
        intensity: summarize(preview.intensityColumns), lfq: summarize(preview.lfqColumns)});
    } finally { abort(); }
  }
  return {onQuantificationRow, finish, abort};
}

export function mountSampleQC(root, summary, type, expanded, t = zh => zh) {
  if (!summary) { root.replaceChildren(); return () => {}; }
  const doc = root.ownerDocument;
  function make(tag, text, cls) {
    const el = doc.createElement(tag);
    if (text !== undefined) el.textContent = String(text);
    if (cls) el.className = cls;
    return el;
  }
  const rows = summary[type], details = make('details', undefined, 'iw-sample-qc');
  details.open = expanded.has(type);
  const title = make('summary', t('每个样本的数据概览', 'Per-sample data overview'));
  title.append(make('span', ' · ' + summary.recordCount + t(' 条完整记录 / ', ' complete records / ') + rows.length + t(' 个样本列', ' sample columns'), 'iw-qc-meta'));
  const note = make('p', t('按原列索引分别计数，仅显示当前量化类型；不作合格判断、不改变原值、不执行过滤或标准化。仅本页汇总，未上传、未保存服务器。',
    'Counts use original column indices for the current quantification type only. No quality verdict, value changes, filtering or normalization. Page-memory summary only; no upload or server save.'), 'note');
  const wrapper = make('div', undefined, 'iw-scroll'), table = make('table');
  table.setAttribute('aria-label', t('每个样本的数据计数', 'Per-sample value counts'));
  const head = make('thead'), header = make('tr');
  for (const name of [t('原始列名', 'Original column'), t('原始样本名', 'Original sample'), t('正数', 'Positive'), t('零值', 'Zero'), t('空值', 'Empty'), t('异常', 'Anomalies')]) {
    const th = make('th', name); th.setAttribute('scope', 'col'); header.append(th);
  }
  head.append(header);
  const body = make('tbody');
  function breakdown(total, parts) {
    const cell = make('td'), sub = make('details');
    sub.append(make('summary', total));
    for (const [label, count] of parts) sub.append(make('div', label + ': ' + count));
    cell.append(sub); return cell;
  }
  for (const row of rows) {
    const tr = make('tr'); tr.dataset.qcColumn = String(row.index);
    tr.append(make('td', row.columnName), make('td', row.sampleName));
    for (const key of ['positive', 'zero']) {
      const cell = make('td', row[key]); cell.dataset.qcCount = key; tr.append(cell);
    }
    const empty = breakdown(row.empty, [[t('空字符串', 'Empty string'), row.emptyString], [t('仅空白', 'Whitespace only'), row.whitespace]]);
    empty.dataset.qcCount = 'empty';
    const abnormal = breakdown(row.abnormal, [[t('负数', 'Negative'), row.negative], [t('格式/范围', 'Format/range'), row.format], [t('非有限', 'Non-finite'), row.nonFinite]]);
    abnormal.dataset.qcCount = 'abnormal';
    tr.append(empty, abnormal); body.append(tr);
  }
  table.append(head, body); wrapper.append(table);
  const rules = make('details', undefined, 'iw-qc-rules');
  rules.append(make('summary', t('计数规则', 'Counting rules')), make('p', t(
    '空值＝空字符串＋仅空白，分类时仅对临时字符串 trim，不写回原值。仅接受完整十进制/科学计数语法；有限正数、零值、空值、异常之和等于每列完整记录数。0、-0、零系数（如0e999）计零，不自动当缺失；有限负数计异常。NaN、±Infinity和溢出计非有限；非法格式（含十六进制）或非零数下溢为0计格式/范围异常。此处不按样本名合并列，也不依赖分组确认。',
    'Empty = empty string + whitespace only. Classification trims a temporary string without changing source values. Only complete decimal/scientific notation is accepted. Positive + zero + empty + anomalies equals the full record count per column. 0, -0 and zero coefficients (e.g. 0e999) are zero, not inferred missing values. Finite negatives are anomalies. NaN, ±Infinity and overflow are non-finite; invalid syntax (including hex) or nonzero underflow to 0 is a format/range anomaly. Columns are never merged by sample name; grouping confirmation is not required.')));
  details.append(title, note, wrapper, rules);
  const compositionKey = 'composition:' + type;
  const composition = make('details', undefined, 'iw-sample-composition');
  composition.open = expanded.has(compositionKey);
  const compositionTitle = make('summary', t('样本数据组成图', 'Sample data composition'));
  compositionTitle.append(make('span', ' · ' + summary.recordCount + t(' 条完整记录 / ', ' complete records / ') + rows.length + t(' 个样本列', ' sample columns'), 'iw-qc-meta'));
  const categories = [
    ['positive', t('正数', 'Positive')], ['zero', t('零值', 'Zero')],
    ['empty', t('空值', 'Empty')], ['abnormal', t('异常', 'Anomalies')],
  ];
  function percentage(count) {
    if (count === 0) return '0%';
    const value = count / summary.recordCount * 100;
    return value < 0.01 ? '<0.01%' : value.toFixed(2) + '%';
  }
  const compositionNote = make('p', t(
    '仅复用本页计数摘要，按原列顺序显示数值类别组成，不代表缺失率或合格判断。柱宽使用未舍入比例；文字百分比经舍入，显示合计可能不等于100%。',
    'Reuses page-memory counts in original column order. Value composition is not an inferred missingness rate or quality verdict. Bar widths use unrounded proportions; displayed percentages are rounded and may not sum to 100%.'), 'note');
  const legend = make('div', undefined, 'iw-composition-legend');
  for (const [key, label] of categories) {
    const entry = make('span');
    const swatch = make('span', undefined, 'iw-composition-swatch iw-composition-' + key);
    swatch.setAttribute('aria-hidden', 'true'); entry.append(swatch, make('span', label)); legend.append(entry);
  }
  const chart = make('div', undefined, 'iw-composition-chart');
  for (const row of rows) {
    const item = make('div', undefined, 'iw-composition-row');
    item.dataset.compositionColumn = String(row.index);
    item.setAttribute('role', 'group'); item.setAttribute('tabindex', '0');
    const label = make('div', undefined, 'iw-composition-label');
    label.append(make('strong', row.sampleName), make('small', '#' + (row.index + 1) + ' · ' + row.columnName));
    const bar = make('div', undefined, 'iw-composition-bar'); bar.setAttribute('aria-hidden', 'true');
    const values = make('div', undefined, 'iw-composition-values'), descriptions = [];
    for (const [key, name] of categories) {
      const count = row[key], segment = make('span', undefined, 'iw-composition-segment iw-composition-' + key);
      segment.dataset.compositionCategory = key; segment.dataset.compositionCount = String(count);
      segment.setAttribute('style', 'flex:0 0 ' + (count / summary.recordCount * 100) + '%');
      bar.append(segment);
      const text = name + ': ' + count + ' (' + percentage(count) + ')';
      descriptions.push(text); values.append(make('span', text));
    }
    const accessible = '#' + (row.index + 1) + '; ' + row.sampleName + '; ' + row.columnName + '; ' + descriptions.join('; ');
    item.setAttribute('aria-label', accessible); item.setAttribute('title', accessible);
    item.append(label, bar, values); chart.append(item);
  }
  composition.append(compositionTitle, compositionNote, legend, chart);
  root.replaceChildren(details, composition);
  const toggle = event => { if (event.target === details) { if (details.open) expanded.add(type); else expanded.delete(type); } };
  details.addEventListener('toggle', toggle);
  const compositionToggle = event => {
    if (event.target === composition) { if (composition.open) expanded.add(compositionKey); else expanded.delete(compositionKey); }
  };
  composition.addEventListener('toggle', compositionToggle);
  return () => {
    if (details.open) expanded.add(type); else expanded.delete(type);
    if (composition.open) expanded.add(compositionKey); else expanded.delete(compositionKey);
    composition.removeEventListener('toggle', compositionToggle);
    details.removeEventListener('toggle', toggle); root.replaceChildren();
  };
}
