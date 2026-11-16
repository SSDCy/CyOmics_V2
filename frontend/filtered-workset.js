import {parseProteinGroups} from './protein-groups.js';
import {evaluateProteinFilter, QUALITY_FLAG_NAMES} from './protein-filter-preview.js';

export const WORKSET_LIMITS = Object.freeze({maxBytes: 10 * 1024 * 1024, maxCells: 2000000, pageSize: 50});
const problem = code => Object.assign(new Error(code), {code});
const freezeColumns = cols => Object.freeze(cols.map(c => Object.freeze({...c})));

export function createFilteredWorkset({yieldTask = () => new Promise(resolve => setTimeout(resolve, 0))} = {}) {
  const subscribers = new Set();
  let source = null, selection = null, snapshot = null, request = 0, busy = false, error = '';
  let expanded = false, view = 'filtered', page = 1;
  function cancel(code = 'CANCELLED') { if (busy) { request++; busy = false; error = code; } }
  function reset() {
    request++; busy = false; source = null; selection = null; snapshot = null;
    error = ''; expanded = false; view = 'filtered'; page = 1;
  }
  function acceptSource({text, fileName, byteSize, preview, flags}) {
    reset();
    if (typeof text !== 'string' || !text.length || !Number.isSafeInteger(byteSize) || byteSize < 1
        || byteSize > WORKSET_LIMITS.maxBytes || new TextEncoder().encode(text).byteLength > WORKSET_LIMITS.maxBytes
        || !Number.isSafeInteger(preview.rowCount) || preview.rowCount < 1
        || !Number.isSafeInteger(preview.columnCount) || preview.columnCount < 1
        || preview.headers.length !== preview.columnCount || flags.recordCount !== preview.rowCount) throw problem('WORKSET_SOURCE_INVALID');
    const initial = evaluateProteinFilter(flags, 0);
    if (initial.retained !== preview.rowCount) throw problem('WORKSET_SOURCE_INVALID');
    source = Object.freeze({text, fileName: String(fileName).split(/[\\/]/).pop(),
      rowCount: preview.rowCount, columnCount: preview.columnCount,
      headers: Object.freeze([...preview.headers]), flags});
    selection = initial;
  }
  function updateRules(ruleView) {
    if (!source || ruleView.summary !== source.flags) return false;
    if (selection?.selectedMask === ruleView.selectedMask) return true;
    cancel('RULES_CHANGED'); error = '';
    try { selection = evaluateProteinFilter(source.flags, ruleView.selectedMask); }
    catch { selection = {selectedMask: ruleView.selectedMask, unavailable: true}; }
    return true;
  }
  function blocked() {
    if (!source) return 'NO_SOURCE';
    if (!Number.isSafeInteger(source.rowCount * source.columnCount)
        || source.rowCount * source.columnCount > WORKSET_LIMITS.maxCells) return 'CELL_LIMIT';
    if (!selection || selection.unavailable) return 'RULE_UNAVAILABLE';
    if (selection.pending > 0) return 'PENDING_FLAGS';
    return '';
  }
  async function generate() {
    if (busy) return false;
    const reason = blocked();
    if (reason) { error = reason; return false; }
    const captured = source, appliedMask = selection.selectedMask, expected = selection, token = ++request;
    const current = () => source === captured && request === token;
    busy = true; error = '';
    try {
      await yieldTask();
      if (!current()) return false;
      const parsed = parseProteinGroups(captured.text, captured.rowCount);
      if (parsed.rowCount !== captured.rowCount || parsed.columnCount !== captured.columnCount
          || parsed.previewRows.length !== captured.rowCount
          || parsed.headers.some((h, i) => h !== captured.headers[i])) throw problem('SOURCE_MISMATCH');
      const originalRecords = [], keptRecords = [], reasons = Array(8).fill(0);
      let pending = 0, excluded = 0;
      for (let i = 0; i < parsed.previewRows.length; i++) {
        const cells = Object.freeze(parsed.previewRows[i]);
        const record = Object.freeze({recordNumber: i + 1, cells});
        originalRecords.push(record);
        let hit = 0, unknown = false;
        for (let bit = 0; bit < 3; bit++) if (appliedMask & (1 << bit)) {
          const value = cells[captured.flags.flags[bit].indices[0]];
          if (value === '+') hit |= 1 << bit;
          else if (value !== '') unknown = true;
        }
        if (hit) { excluded++; reasons[hit]++; }
        else if (unknown) pending++;
        else keptRecords.push(record);
        if ((i + 1) % 256 === 0) { await yieldTask(); if (!current()) return false; }
      }
      await yieldTask();
      if (!current()) return false;
      if (pending || pending !== expected.pending || excluded !== expected.excluded || keptRecords.length !== expected.retained
          || reasons.some((n, i) => n !== expected.reasons[i])) throw problem('COUNT_MISMATCH');
      const next = Object.freeze({fileName: captured.fileName, headers: Object.freeze(parsed.headers),
        proteinIdsIndex: parsed.headers.indexOf('Protein IDs'),
        intensityColumns: freezeColumns(parsed.intensityColumns), lfqColumns: freezeColumns(parsed.lfqColumns),
        originalRecords: Object.freeze(originalRecords), keptRecords: Object.freeze(keptRecords),
        appliedMask, appliedRules: Object.freeze(QUALITY_FLAG_NAMES.filter((_, i) => appliedMask & (1 << i))),
        counts: Object.freeze({original: captured.rowCount, retained: keptRecords.length, excluded, pending, reasons: Object.freeze(reasons)})});
      snapshot = next; view = 'filtered'; page = 1;
      return true;
    } catch (failure) {
      if (current()) error = ['SOURCE_MISMATCH', 'COUNT_MISMATCH'].includes(failure?.code) ? failure.code : 'GENERATION_FAILED';
      return false;
    } finally { if (current()) { busy = false; for (const notify of subscribers) notify(); } }
  }
  function status() {
    return {hasSource: Boolean(source), fileName: source?.fileName || '', originalCount: source?.rowCount || 0,
      busy, error, blocked: blocked(), canGenerate: !busy && !blocked(), expanded, view, page,
      stale: Boolean(snapshot && selection?.selectedMask !== snapshot.appliedMask), snapshot};
  }
  function getPage(type = 'intensity') {
    if (!snapshot) return null;
    const records = view === 'original' ? snapshot.originalRecords : snapshot.keptRecords;
    const pages = Math.max(1, Math.ceil(records.length / WORKSET_LIMITS.pageSize));
    page = Math.min(page, pages);
    return Object.freeze({page, pages, total: records.length,
      records: Object.freeze(records.slice((page - 1) * WORKSET_LIMITS.pageSize, page * WORKSET_LIMITS.pageSize)),
      columns: type === 'lfq' ? snapshot.lfqColumns : snapshot.intensityColumns, proteinIdsIndex: snapshot.proteinIdsIndex});
  }
  return {acceptSource, updateRules, generate, cancel, reset, status, getPage,
    subscribe(notify) { subscribers.add(notify); return () => subscribers.delete(notify); },
    setExpanded(open) { expanded = Boolean(open); },
    setView(next) { if (['original', 'filtered'].includes(next) && next !== view) { view = next; page = 1; } },
    setPage(next) {
      if (!snapshot || !Number.isSafeInteger(next) || next < 1) return false;
      const count = view === 'original' ? snapshot.originalRecords.length : snapshot.keptRecords.length;
      if (next > Math.max(1, Math.ceil(count / WORKSET_LIMITS.pageSize))) return false;
      page = next; return true;
    },
  };
}

export function mountFilteredWorkset(root, model, type, t = zh => zh) {
  if (!model.status().hasSource) { root.replaceChildren(); return {refresh() {}, dispose() {}}; }
  const doc = root.ownerDocument, listeners = [];
  let disposed = false;
  function make(tag, text, cls) {
    const node = doc.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    if (cls) node.className = cls;
    return node;
  }
  function listen(node, event, handler) { node.addEventListener(event, handler); listeners.push(() => node.removeEventListener(event, handler)); }
  function button(label, action) {
    const node = make('button', label); node.type = 'button'; node.dataset.worksetAction = action; return node;
  }
  const details = make('details', undefined, 'iw-filtered-workset'); details.open = model.status().expanded;
  const source = make('p', '', 'note');
  const note = make('p', t('只生成当前页内存中的独立工作集，原始记录始终保留。不覆盖文件、不上传或保存服务器；现有QC、组成图、分组与关联不使用此工作集。',
    'Creates an independent workset in page memory. Original records remain available. No file overwrite, upload or server save; existing QC, composition, groups and association do not use this workset.'), 'note');
  const actions = make('div', undefined, 'row iw-workset-actions');
  const generate = button(t('按当前规则生成工作集', 'Generate workset from current rules'), 'generate');
  const cancel = button(t('取消生成', 'Cancel generation'), 'cancel'); actions.append(generate, cancel);
  const state = make('p', '', 'iw-workset-state'); state.setAttribute('role', 'status'); state.setAttribute('aria-live', 'polite');
  const applied = make('p', '', 'iw-workset-applied'); applied.dataset.worksetApplied = '';
  const controls = make('div', undefined, 'row iw-workset-controls');
  const select = make('select'); select.setAttribute('aria-label', t('工作集视图', 'Workset view'));
  const filtered = make('option', t('过滤后工作集', 'Filtered workset')); filtered.value = 'filtered';
  const original = make('option', t('原始记录', 'Original records')); original.value = 'original'; select.append(filtered, original);
  const prev = button('‹', 'prev'), next = button('›', 'next');
  prev.setAttribute('aria-label', t('上一页工作集记录', 'Previous workset page')); next.setAttribute('aria-label', t('下一页工作集记录', 'Next workset page'));
  const pageText = make('span'), jump = make('input'); jump.type = 'number'; jump.min = '1'; jump.setAttribute('aria-label', t('工作集页码', 'Workset page'));
  const go = button(t('跳转', 'Go'), 'page'); controls.append(select, prev, pageText, next, jump, go);
  const range = make('p', '', 'note'); range.dataset.worksetRange = '';
  const area = make('div');
  details.append(make('summary', t('过滤后工作集', 'Filtered workset')), source, note, actions, state, applied, controls, range, area);
  root.replaceChildren(details);
  const messages = {
    NO_SOURCE: t('等待真实定量表完成校验。', 'Waiting for a validated real table.'),
    CELL_LIMIT: t('超过200万单元格上限，不能生成；原预览仍可使用。', 'Exceeds the 2-million-cell generation limit; the original preview remains available.'),
    RULE_UNAVAILABLE: t('所选规则不可用，不能生成。', 'A selected rule is unavailable; generation is blocked.'),
    PENDING_FLAGS: t('当前规则含待确认记录，不能生成。请先调整规则。', 'Current rules have pending entries; adjust the rules before generating.'),
    CANCELLED: t('已取消；未提交新结果，已有有效集保持不变。', 'Cancelled; no new result committed. Any existing valid workset is unchanged.'),
    SOURCE_MISMATCH: t('源数据校验不一致，未提交新工作集。', 'Source validation mismatch; no new workset committed.'),
    COUNT_MISMATCH: t('工作集计数与规则预览不一致，未提交。', 'Workset counts do not match the preview; nothing committed.'),
    GENERATION_FAILED: t('生成失败；已有有效工作集保持不变。', 'Generation failed; any existing valid workset is unchanged.'),
  };
  function refresh() {
    if (disposed) return;
    const current = model.status(), snapshot = current.snapshot;
    source.textContent = current.fileName + ' · ' + current.originalCount + t(' 条原始记录', ' original records');
    generate.disabled = !current.canGenerate; cancel.hidden = !current.busy;
    state.textContent = current.busy ? t('正在本页生成…可取消。', 'Generating in page memory… You can cancel.')
      : messages[current.error] || messages[current.blocked] || (current.stale
        ? t('当前预览规则已改动：仍显示上次已应用规则与记录，需重新生成。', 'Preview rules changed. Showing the last applied rules and records; regenerate to update.')
        : snapshot ? t('本页工作集已生成，未应用到其他模块。', 'Page workset generated; not applied to other modules.')
          : t('尚未生成。请选择预览规则后显式点击生成。', 'Not generated. Choose preview rules, then click Generate.'));
    details.dataset.worksetStale = String(current.stale);
    applied.hidden = !snapshot; controls.hidden = !snapshot; range.hidden = !snapshot;
    if (!snapshot) { applied.textContent = ''; range.textContent = ''; area.replaceChildren(); return; }
    applied.textContent = t('原始 ', 'Original ') + snapshot.counts.original + t(' → 排除 ', ' → exclude ') + snapshot.counts.excluded
      + t(' → 工作集 ', ' → workset ') + snapshot.counts.retained + t('；已应用规则：', '; applied rules: ')
      + (snapshot.appliedRules.length ? snapshot.appliedRules.join(' + ') : t('无（全部保留）', 'none (retain all)'));
    const data = model.getPage(type);
    select.value = current.view; pageText.textContent = data.page + ' / ' + data.pages;
    jump.value = String(data.page); jump.max = String(data.pages); prev.disabled = data.page === 1; next.disabled = data.page === data.pages;
    range.textContent = (type === 'lfq' ? 'LFQ intensity' : 'Intensity') + ' · ' + data.columns.length + t(' 个样本列；', ' sample columns; ')
      + data.total + t(' 条记录，本页 ', ' records, this page ') + data.records.length + t(' 条（每页50条）。原记录号不重编号。', ' (50 per page). Original record numbers are preserved.');
    const wrapper = make('div', undefined, 'iw-scroll'), table = make('table'); table.setAttribute('aria-label', t('工作集原始定量值', 'Workset original quantification values'));
    const head = make('thead'), headRow = make('tr');
    for (const label of [t('原记录号', 'Original record number'), snapshot.headers[data.proteinIdsIndex], ...data.columns.map(c => c.columnName)]) {
      const th = make('th', label); th.setAttribute('scope', 'col'); headRow.append(th);
    }
    head.append(headRow); const body = make('tbody');
    for (const record of data.records) {
      const tr = make('tr'); tr.dataset.worksetRecord = String(record.recordNumber);
      tr.append(make('td', record.recordNumber), make('td', record.cells[data.proteinIdsIndex], 'iw-workset-ids'));
      for (const col of data.columns) tr.append(make('td', record.cells[col.index]));
      body.append(tr);
    }
    table.append(head, body); wrapper.append(table);
    area.replaceChildren(...(data.total ? [wrapper] : [make('p', t('本工作集无保留记录；仍可切换查看原始记录。', 'No retained records in this workset; original records remain available.'), 'note')]));
  }
  listen(generate, 'click', () => { const operation = model.generate(); refresh(); void operation.then(refresh); });
  listen(cancel, 'click', () => { model.cancel(); refresh(); });
  listen(select, 'change', () => { model.setView(select.value); refresh(); });
  listen(prev, 'click', () => { model.setPage(model.status().page - 1); refresh(); });
  listen(next, 'click', () => { model.setPage(model.status().page + 1); refresh(); });
  listen(go, 'click', () => { model.setPage(Number(jump.value)); refresh(); });
  listen(details, 'toggle', event => { if (!disposed && event.target === details) model.setExpanded(details.open); });
  listeners.push(model.subscribe(refresh));
  refresh();
  return {refresh, dispose() { model.setExpanded(details.open); disposed = true; for (const remove of listeners) remove(); root.replaceChildren(); }};
}
