import {createContrastDesignDraft} from './private-contrast-design.js';
import {mountContrastDesignEditor} from './private-contrast-design-editor.js';
import {canonicalJSON, freeze} from './sample-design-contract.mjs';

// Owned by one private uploader file entry; DOM mounts never own this model.
export function createPrivateContrastHost({sampleHost, cryptoImpl = globalThis.crypto} = {}) {
  const model = createContrastDesignDraft({cryptoImpl}), listeners = new Set();
  let disposed = false, fingerprint = null, expanded = false;
  const emit = () => { if (!disposed) for (const fn of listeners) fn(); };
  function sourceNow() { try { return sampleHost?.comparisonSource?.() || null; } catch { return null; } }
  function sync() {
    if (disposed) return;
    let source = sourceNow(), next = null;
    try { if (source) next = canonicalJSON(source); } catch { source = null; }
    // The getter returns a fresh frozen clone. Compare full canonical contents,
    // never object identity, only version IDs, or just a claimed ready flag.
    if (next === fingerprint) return;
    fingerprint = next;
    void model.bindSource(source);
  }
  const stopModel = model.subscribe(emit), stopSource = sampleHost?.subscribe?.(sync) || (() => {});
  const editable = fn => (...args) => { sync(); return !disposed && fingerprint !== null && model.view().can_edit ? fn(...args) : false; };
  const editor = {view() { sync(); return model.view(); }, subscribe(fn) { if (disposed) return () => {}; listeners.add(fn); return () => listeners.delete(fn); },
    addContrast: editable(model.addContrast), updateContrast: editable(model.updateContrast), removeContrast: editable(model.removeContrast),
    exportDraft() { sync(); return !disposed && fingerprint !== null ? model.exportDraft() : null; }};
  sync();
  return {editor: () => editor,
    view() {
      sync(); const v = model.view();
      return freeze({expanded, disposed, available: !disposed && fingerprint !== null && v.can_edit, design: v, analysis_ready: false});
    },
    exportDraft: editor.exportDraft,
    setExpanded(value) { if (!disposed && typeof value === 'boolean' && expanded !== value) { expanded = value; emit(); } },
    subscribe(fn) { if (disposed) return () => {}; listeners.add(fn); return () => listeners.delete(fn); },
    dispose() { if (disposed) return; disposed = true; stopSource(); stopModel(); fingerprint = null; expanded = false; model.dispose(); for (const fn of listeners) fn(); listeners.clear(); },
  };
}

export function mountPrivateContrastHost(root, host, {t = zh => zh} = {}) {
  const doc = root.ownerDocument, make = (tag, text = '', cls = '') => { const n = doc.createElement(tag); n.textContent = text; n.className = cls; return n; };
  const box = make('details', '', 'pcdh'), summary = make('summary', t('比较设计（草稿）', 'Comparison design (draft)')),
    state = make('p', '', 'pcdh-state'), content = make('div');
  state.setAttribute('role', 'status'); state.setAttribute('aria-live', 'polite'); box.append(summary, state, content); root.replaceChildren(box);
  let disposed = false, editorMounted = false, stopEditor = () => {}, unsubscribe = () => {};
  const toggle = () => { if (!disposed) host.setExpanded(box.open); };
  function stop() {
    if (disposed) return; disposed = true; unsubscribe(); stopEditor(); box.removeEventListener('toggle', toggle);
    state.textContent = ''; box.textContent = ''; root.replaceChildren();
  }
  function paint() {
    if (disposed) return; const value = host.view(); if (value.disposed) { stop(); return; }
    box.open = value.expanded;
    if (value.available && !editorMounted) { editorMounted = true; stopEditor = mountContrastDesignEditor(content, host.editor(), {t}); }
    else if (!value.available && editorMounted) { editorMounted = false; stopEditor(); stopEditor = () => {}; }
    state.hidden = value.available;
    state.textContent = value.available ? '' : value.design.source_checking ? t('正在核对已保存的样本来源…', 'Checking the saved sample source…')
      : value.design.state === 'invalid' ? t('来源未通过校验，请重新确认已保存的样本信息。', 'Source validation failed. Confirm the saved sample information again.')
      : t('请先保存样本信息，保存确认后可填写比较草稿。', 'Save sample information first, then edit a comparison draft after confirmation.');
  }
  box.addEventListener('toggle', toggle); unsubscribe = host.subscribe(paint); paint(); return stop;
}
