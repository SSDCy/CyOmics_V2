import {createPrivateSampleDesign, mountPrivateSampleDesign} from './private-sample-design.js';

const freeze = value => { if (value && typeof value === 'object' && !Object.isFrozen(value)) { Object.values(value).forEach(freeze); Object.freeze(value); } return value; };
const failures = new Set(['sample_design_head_conflict', 'sample_design_request_conflict', 'sample_design_result_unknown', 'sample_design_timeout',
  'sample_design_head_required', 'sample_design_persisted_invalid', 'invalid_sample_design', 'sample_design_version_limit', 'sample_design_unavailable',
  'sample_design_not_found', 'dataset_not_found', 'not_found', 'unauthenticated', 'auth_origin_rejected', 'project_context_changed']);

// Owned by the private uploader, not by a DOM mount. No browser persistence.
export function createPrivateSampleDesignHost({reportModel, projectId, fileId, isCurrent = () => false, clientFactory = null,
  onDenied = () => {}, cryptoImpl = globalThis.crypto, loadScheduler = run => run()} = {}) {
  let disposed = false, entry = null, serial = 0, blockedSource = null, expanded = false;
  const listeners = new Set(), emit = () => { if (!disposed) for (const fn of listeners) fn(); };
  const contextCurrent = () => { try { return !disposed && isCurrent() === true; } catch { return false; } };
  const sourceNow = () => { try { return contextCurrent() ? reportModel?.validatedSource?.() || null : null; } catch { return null; } };
  const current = (e, binding = null) => entry === e && e.alive && contextCurrent() && sourceNow()?.importDTO === e.source.importDTO
    && (!binding || binding.project_id === projectId && binding.dataset_version_id === e.version && binding.quant_family === e.model.view().quant_family);
  function discardEntry() {
    const old = entry; entry = null; if (!old) return;
    old.alive = false; old.query?.abort(); old.unsubscribe?.(); old.client?.dispose(); old.model.dispose();
  }
  function sourceLost(e, response) {
    if (entry !== e || !e.alive) return;
    blockedSource = e.source.importDTO; discardEntry(); emit();
    if (response) onDenied(response);
  }
  function failure(e, family, error) {
    if (entry !== e || !e.alive) return;
    // Cancellation of the previous family is not loss of this private project.
    // The client retains that family's uncertain-write state independently.
    if (e.model.view().quant_family !== family) return;
    const code = failures.has(error?.code) ? error.code : 'sample_design_unavailable'; e.errors.set(family, code);
    if (error?.status === 401 || error?.status === 403 || error?.status === 409 && code === 'project_context_changed') {
      sourceLost(e, {status: error.status, body: {error: {code}}});
    } else if (error?.status === 404 && ['dataset_not_found', 'not_found'].includes(code)) sourceLost(e);
  }
  function clientState(e, family) {
    try { return e.client?.status(family) || {phase: 'not_connected', head_known: false, uncertain_request_id: null}; }
    catch { return {phase: 'not_connected', head_known: false, uncertain_request_id: null}; }
  }
  function clientCall(e, family, kind, value, signal) {
    // A family switch may abort the core before its callback has settled. Keep
    // callback completion serialized so the next family cannot race that client.
    const run = e.tail.then(async () => {
      const binding = {project_id: projectId, dataset_version_id: e.version, quant_family: family};
      if (!current(e, binding) || signal?.aborted || !e.client) throw Error('context_changed');
      e.errors.delete(family);
      try { return await e.client[kind](value, {signal}); }
      catch (error) { failure(e, family, error); throw error; }
    });
    e.tail = run.catch(() => {}); return run;
  }
  function scheduleLoad(e) {
    if (!current(e) || !e.ready) return;
    const family = e.model.view().quant_family;
    if (e.initialLoads.has(family)) return;
    e.initialLoads.add(family);
    // Never replace a dirty draft, including a programmatic edit during bind.
    if (e.model.view().dirty || clientState(e, family).phase === 'not_connected') return;
    const epoch = e.model.view().source_epoch, ticket = {}; e.queued.set(family, ticket); emit();
    const run = () => {
      if (e.queued.get(family) !== ticket) return false;
      e.queued.delete(family);
      if (!current(e) || e.model.view().source_epoch !== epoch || e.model.view().quant_family !== family
        || e.model.view().dirty || e.model.view().busy || e.query) return false;
      // The client (and its 15s budget) starts here, never while queued.
      return e.model.load();
    };
    try { void Promise.resolve(loadScheduler(run)).catch(() => {
      if (current(e)) e.errors.set(family, 'sample_design_unavailable');
    }).finally(() => { if (e.queued.get(family) === ticket) e.queued.delete(family); if (current(e)) emit(); }); }
    catch { e.queued.delete(family); if (current(e)) { e.errors.set(family, 'sample_design_unavailable'); emit(); } }
  }
  function facade(e) {
    const editable = fn => (...args) => current(e) && !e.query ? fn(...args) : false;
    return {view: () => {
      const v = e.model.view(), s = clientState(e, v.quant_family), connected = current(e) && s.phase !== 'not_connected';
      return freeze({...v, busy: v.busy || !!e.query,
        can_save: v.can_save && connected && s.head_known && !s.uncertain_request_id && !e.query,
        can_load: v.can_load && connected && !s.uncertain_request_id && !e.query && !e.queued.has(v.quant_family),
        can_confirm_all: v.can_confirm_all && !e.query});
    }, subscribe(fn) { if (disposed) return () => {}; listeners.add(fn); return () => listeners.delete(fn); },
      update: editable(e.model.update), batchUpdate: editable(e.model.batchUpdate),
      setConfirmed: editable(e.model.setConfirmed), confirmAll: editable(e.model.confirmAll),
      setQuantFamily(next) { if (!current(e)) return false; e.query?.abort(); return e.model.setQuantFamily(next); },
      exportDraft: () => current(e) ? e.model.exportDraft() : null,
      save: () => save(), load: options => load(options)};
  }
  function sync() {
    if (disposed) return;
    const source = sourceNow();
    if (!source || source.importDTO === blockedSource || source.file?.project_id !== projectId || source.file?.file_id !== fileId) {
      if (entry) { discardEntry(); emit(); } return;
    }
    if (entry?.source.importDTO === source.importDTO) return;
    discardEntry(); blockedSource = null;
    const e = {alive: true, source, version: source.importDTO.datasetVersion.dataset_version_id, key: ++serial,
      ready: false, client: null, model: null, editor: null, tail: Promise.resolve(), initialLoads: new Set(), queued: new Map(), errors: new Map(), query: null, queried: new Set()};
    entry = e;
    const callbacks = typeof clientFactory === 'function' ? {
      saveDraft: (design, {signal}) => clientCall(e, design.quant_family, 'saveDraft', design, signal),
      loadDraft: (selector, {signal}) => clientCall(e, selector.quant_family, 'loadDraft', selector, signal),
    } : {};
    e.model = createPrivateSampleDesign({...callbacks, cryptoImpl});
    try {
      e.client = typeof clientFactory === 'function' ? clientFactory({projectId, datasetVersionId: e.version,
        source: freeze({dataset_version_id: e.version, sample_columns: source.importDTO.importReport.sample_columns}),
        isCurrent: binding => current(e, binding), cryptoImpl}) : null;
      if (e.client && !['saveDraft', 'loadDraft', 'resolveRequest', 'status', 'dispose'].every(k => typeof e.client[k] === 'function')) { e.client?.dispose?.(); e.client = null; }
    } catch { e.client = null; }
    e.editor = facade(e);
    e.unsubscribe = e.model.subscribe(() => { if (current(e)) { emit(); scheduleLoad(e); } });
    emit();
    void e.model.bindSource(source).then(valid => {
      if (!current(e)) return;
      e.ready = valid; emit(); if (valid) scheduleLoad(e);
    });
  }
  async function save() {
    sync(); const e = entry;
    if (!e || !current(e) || !e.editor.view().can_save) return false;
    return e.model.save();
  }
  async function load({discardDirty = false} = {}) {
    sync(); const e = entry;
    if (!e || !current(e) || !e.editor.view().can_load) return false;
    if (e.model.view().dirty && discardDirty !== true) return false;
    e.initialLoads.add(e.model.view().quant_family); return e.model.load({discardDirty});
  }
  async function querySave() {
    sync(); const e = entry; if (!e || !current(e) || e.query || e.model.view().busy) return false;
    const family = e.model.view().quant_family;
    if (!clientState(e, family).uncertain_request_id) return false;
    const query = new AbortController(); e.query = query; emit();
    try {
      await clientCall(e, family, 'resolveRequest', family, query.signal);
      if (!current(e) || e.model.view().quant_family !== family || query.signal.aborted) return false;
      // A request lookup confirms a historical write, never the latest head.
      // Do not apply its design or mark this editable draft saved.
      e.queried.add(family); e.errors.delete(family); return true;
    } catch { return false; }
    finally { if (e.query === query) { e.query = null; if (current(e)) emit(); } }
  }
  function view() {
    sync(); const e = entry;
    if (!e || !current(e)) return freeze({key: null, phase: disposed ? 'disposed' : blockedSource ? 'source_unavailable' : 'waiting', expanded, connected: false, design: null, can_query: false});
    const d = e.editor.view(), s = clientState(e, d.quant_family), connected = s.phase !== 'not_connected';
    const phase = !e.ready ? d.source_phase === 'invalid' ? 'source_unavailable' : 'validating'
      : e.query ? 'querying' : s.uncertain_request_id ? 'unknown' : s.phase === 'conflict' ? 'conflict'
      : d.busy ? d.save_state === 'saving' ? 'saving' : 'loading' : e.queried.has(d.quant_family) && !s.head_known ? 'queried'
      : e.errors.has(d.quant_family) ? 'failed' : !connected ? 'not_connected' : d.dirty ? 'dirty' : e.queued.has(d.quant_family) ? 'queued'
      : d.save_state === 'acknowledged' && s.phase === 'saved' ? 'saved' : d.load_state === 'loaded' && s.phase === 'loaded' ? 'restored'
      : s.phase === 'not_found' ? 'empty' : 'ready';
    return freeze({key: e.key, phase, expanded, connected, design: d, error: e.errors.get(d.quant_family) || null,
      can_query: !!s.uncertain_request_id && !d.busy && !e.query,
      head_known: s.head_known, head_version_id: s.head_version_id || null, persistence_verified: phase === 'saved' && !d.dirty && s.persistence_verified === true, analysis_ready: false});
  }
  const unsubscribeReport = reportModel?.subscribe?.(sync) || (() => {}); sync();
  return {view, save, load, querySave,
    editor() { sync(); return entry && current(entry) ? entry.editor : null; },
    setExpanded(value) { if (!disposed && typeof value === 'boolean') expanded = value; },
    subscribe(fn) { if (disposed) return () => {}; listeners.add(fn); return () => listeners.delete(fn); },
    dispose() { if (disposed) return; disposed = true; unsubscribeReport(); discardEntry(); blockedSource = null; expanded = false; for (const fn of listeners) fn(); listeners.clear(); },
  };
}

// The reviewed editor retains all model behavior. Translate its provisional
// adapter language at this integration boundary, using actual host state above it.
function editorText(t) {
  const copy = new Map([
    ['未接入正式保存服务', ['样本信息 · 不改变原始定量值', 'Sample information · original values preserved']],
    ['正在向适配器保存草稿…', ['正在保存样本信息…', 'Saving sample information…']],
    ['正在从适配器载入草稿…', ['正在读取已保存的样本信息…', 'Reading saved sample information…']],
    ['本次操作未确认，请检查草稿或适配器。', ['本次操作未确认，当前草稿保留。', 'This operation is unconfirmed. Your draft is retained.']],
    ['适配器已确认草稿；尚未验证服务端持久化。', ['已收到样本信息的保存确认。', 'Sample information save acknowledged.']],
    ['已载入适配器草稿；尚未验证服务端持久化。', ['已恢复此版本的样本信息。', 'Sample information restored for this version.']],
    ['适配器未返回草稿，当前内容未改变。', ['此量化类型尚无已保存信息，当前草稿未改变。', 'No saved information for this quantification. Your draft is unchanged.']],
    ['载入并替换当前草稿', ['丢弃当前修改并重新载入', 'Discard current changes and reload']],
    ['草稿校验或适配器确认不代表服务端持久化完成，也不代表可运行差异分析；比较、统计设计和重复策略仍需后续确认。',
      ['保存样本信息不改变原始定量值，也不执行科研计算；比较、统计设计和重复策略仍需后续确认。', 'Saving sample information does not alter original values or run analyses. Contrasts, statistical design and replication policy still require review.']],
  ]);
  return (zh, english) => copy.has(zh) ? t(...copy.get(zh)) : t(zh, english);
}

export function mountPrivateSampleDesignHost(root, host, {t = zh => zh} = {}) {
  const doc = root.ownerDocument, make = (tag, value = '', cls = '') => { const n = doc.createElement(tag); n.textContent = value; n.className = cls; return n; };
  const box = make('details', '', 'psdh'), summary = make('summary', t('样本分组与实验信息', 'Sample groups & study information'));
  const state = make('p', '', 'psdh-state'), query = make('button', t('查询保存状态（只读）', 'Check save status (read-only)')), content = make('div');
  state.setAttribute('role', 'status'); state.setAttribute('aria-live', 'polite'); query.type = 'button'; query.setAttribute('data-psdh-action', 'query');
  box.append(summary, state, query, content); root.replaceChildren(box);
  let disposed = false, editor = null, stopEditor = () => {};
  const messages = {
    waiting: ['确认导入报告后可填写样本信息。', 'Confirm the import report before editing sample information.'],
    validating: ['正在核对样本来源…', 'Checking sample source…'],
    source_unavailable: ['此输入版本暂不可用，私人草稿已清除，请重新确认导入报告。', 'This input version is unavailable. The private draft was cleared. Confirm the import report again.'],
    not_connected: ['保存服务尚未启用；可在当前页面填写，保存与载入暂不可用。', 'Saving is not enabled. You can edit on this page; save and reload are unavailable.'],
    ready: ['填写后请明确保存；不自动推断实验分组。', 'Save explicitly after editing. Study groups are not inferred.'],
    dirty: ['当前页面有未保存修改。', 'There are unsaved changes on this page.'],
    loading: ['正在读取此量化类型的样本信息…', 'Reading sample information for this quantification…'],
    queued: ['等候读取此量化类型的样本信息；可先填写，修改后不会自动覆盖。', 'Waiting to read this quantification. You can edit first; changes will not be overwritten automatically.'],
    saving: ['正在保存样本信息，请等待确认…', 'Saving sample information. Waiting for confirmation…'],
    restored: ['已恢复此输入版本和量化类型的样本信息。', 'Sample information restored for this input version and quantification.'],
    saved: ['样本信息已收到保存确认。', 'Sample information save confirmed.'],
    empty: ['此量化类型尚无已保存记录，请填写并保存。', 'No saved record for this quantification. Edit and save explicitly.'],
    conflict: ['已有更新版本，当前修改保留。请先选择丢弃修改并重新载入，再核对保存。', 'A newer version exists. Your changes are retained. Discard and reload explicitly before reviewing another save.'],
    unknown: ['保存结果尚未确认，当前修改保留。请只读查询，暂勿重复保存。', 'The save outcome is unknown. Your changes are retained. Check read-only; do not resubmit.'],
    querying: ['正在只读查询该次保存…', 'Checking that save read-only…'],
    queried: ['已找到该次保存记录，当前草稿未改变。请明确重新载入最新版本后再保存。', 'That historical save was found. Your draft is unchanged. Explicitly reload the latest version before saving.'],
    failed: ['未能确认本次操作，当前草稿保留，请手动重新载入或检查连接。', 'The operation could not be confirmed. Your draft is retained. Reload manually or check the connection.'],
    disposed: ['', ''],
  };
  function paint() {
    if (disposed) return; const value = host.view(), next = host.editor();
    if (next !== editor) { stopEditor(); editor = next; stopEditor = next ? mountPrivateSampleDesign(content, next, {t: editorText(t)}) : () => {}; }
    box.open = value.expanded; state.textContent = t(...(messages[value.phase] || messages.failed));
    query.hidden = !value.can_query && value.phase !== 'unknown' && value.phase !== 'querying'; query.disabled = !value.can_query;
    if (value.phase === 'disposed') { stopEditor(); stopEditor = () => {}; editor = null; content.replaceChildren(); box.textContent = ''; root.replaceChildren(); }
  }
  const toggle = () => { if (!disposed) host.setExpanded(box.open); };
  const queryClick = () => { if (!disposed) void host.querySave(); };
  box.addEventListener('toggle', toggle); query.addEventListener('click', queryClick);
  const unsubscribe = host.subscribe(paint); paint();
  return () => { if (disposed) return; disposed = true; unsubscribe(); stopEditor(); box.removeEventListener('toggle', toggle); query.removeEventListener('click', queryClick); state.textContent = ''; box.textContent = ''; root.replaceChildren(); };
}
