import {createPrivateImportController, createImportTransport} from './private-import-controller.js';
import {mountPrivateImportReport} from './private-import-report-view.js';
import {createPrivateSampleDesignHost, mountPrivateSampleDesignHost} from './private-sample-design-host.js';
import {createSampleDesignTransport, createSampleDesignClient} from './sample-design-client.js';
import {sampleDesignRoute} from './sample-design-contract.mjs';
const MAX = 26214400;
const exact = (v, keys) => v !== null && typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === keys.length && keys.every(k => Object.hasOwn(v, k));
const fields = ['file_id', 'project_id', 'request_id', 'filename', 'format', 'bytes', 'sha256', 'status', 'createdAt'];
export const rawUuid = v => typeof v === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v);
export function validTxtName(v) {
  return typeof v === 'string' && !/[\uD800-\uDFFF\u0000-\u001f\u007f-\u009f/\\]/u.test(v)
    && /\.txt$/i.test(v) && new TextEncoder().encode(v).length >= 1 && new TextEncoder().encode(v).length <= 255;
}
const utc = v => typeof v === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(v)
  && Number.isFinite(Date.parse(v)) && new Date(v).toISOString().slice(0, 19) === v.slice(0, 19);
export const isRawFile = (v, projectId) => exact(v, fields) && rawUuid(v.file_id) && v.project_id === projectId && rawUuid(projectId)
  && rawUuid(v.request_id) && validTxtName(v.filename) && v.format === 'proteinGroups_txt' && Number.isSafeInteger(v.bytes)
  && v.bytes >= 1 && v.bytes <= MAX && typeof v.sha256 === 'string' && /^[0-9a-f]{64}$/.test(v.sha256) && v.status === 'stored' && utc(v.createdAt);
const same = (a, b) => fields.every(k => a[k] === b[k]);
const orderTime = v => { const s = v.replace(/(?:Z|\+00:00)$/, '').split('.'); return s[0] + '.' + (s[1] || '').padEnd(6, '0'); };
export function isRawList(v, projectId) {
  if (!exact(v, ['items', 'nextCursor']) || v.nextCursor !== null || !Array.isArray(v.items) || v.items.length > 4 || !v.items.every(x => isRawFile(x, projectId))) return false;
  if (new Set(v.items.map(x => x.file_id)).size !== v.items.length || new Set(v.items.map(x => x.request_id)).size !== v.items.length || v.items.reduce((s, x) => s + x.bytes, 0) > 33554432) return false;
  return v.items.every((x, i) => !i || orderTime(v.items[i - 1].createdAt) > orderTime(x.createdAt)
    || orderTime(v.items[i - 1].createdAt) === orderTime(x.createdAt) && v.items[i - 1].file_id > x.file_id);
}
export const txtFilenameHeader = name => {
  if (!validTxtName(name)) throw Error('invalid_upload');
  return btoa(String.fromCharCode(...new TextEncoder().encode(name))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
};
const hex = bytes => [...new Uint8Array(bytes)].map(x => x.toString(16).padStart(2, '0')).join('');
const bad = code => { throw Error(code); };
const errors = {400: ['invalid_upload', 'upload_checksum_mismatch', 'invalid_upload_query'], 401: ['unauthenticated'], 403: ['auth_origin_rejected'],
  404: ['project_not_found', 'file_not_found'], 405: ['method_not_allowed'], 409: ['project_context_changed', 'upload_request_conflict'],
  413: ['upload_too_large', 'upload_quota_exceeded'], 415: ['unsupported_media_type'], 429: ['upload_rate_limited', 'upload_busy'],
  503: ['uploads_unavailable'], 504: ['upload_result_unknown']};
const errorCode = (status, body) => exact(body, ['error']) && exact(body.error, ['code']) && (errors[status] || []).includes(body.error.code) ? body.error.code : 'uploads_unavailable';

// getOptions/isCurrent close over the existing private transport's nonce and revision.
// The nonce is used only in request options, never returned in public state.
export function createRawTransport({projectId, fetchImpl, getOptions, isCurrent, sampleDesignsEnabled = false, sampleDesignClientFactory = createSampleDesignClient}) {
  if (!rawUuid(projectId)) bad('invalid_upload');
  const base = '/api/v1/projects/' + projectId;
  const send = (suffix, signal, upload) => {
    if (!isCurrent()) bad('project_context_changed');
    const options = getOptions(signal);
    if (upload) return fetchImpl(base + suffix, {...options, method: 'POST', headers: {...options.headers, ...upload.headers}, body: upload.file});
    return fetchImpl(base + suffix, options);
  };
  return {isCurrent,
    sampleDesigns(config) {
      if (sampleDesignsEnabled !== true || typeof sampleDesignClientFactory !== 'function') return null;
      if (config.projectId !== projectId || config.source?.dataset_version_id !== config.datasetVersionId) bad('project_context_changed');
      const fetchSample = (path, options) => {
        const route = sampleDesignRoute(path, options.method);
        if (!isCurrent() || !config.isCurrent({project_id: projectId, dataset_version_id: config.datasetVersionId, quant_family: route.quant_family})
          || route.project_id !== projectId || route.dataset_version_id !== config.datasetVersionId || options.signal.aborted) bad('project_context_changed');
        const baseOptions = getOptions(options.signal), context = new Headers(baseOptions.headers).get('X-Cyomics-Account-Context');
        if (baseOptions.credentials !== 'same-origin' || baseOptions.cache !== 'no-store' || baseOptions.redirect !== 'error'
          || typeof context !== 'string' || !/^[A-Za-z0-9_-]{22}$/.test(context)) bad('project_context_changed');
        // Only the existing account-context header crosses this boundary. Keep
        // the sample transport's JSON/CSRF headers, method and bounded body.
        return fetchImpl(route.path, {...options, headers: {...options.headers, 'X-Cyomics-Account-Context': context}});
      };
      return sampleDesignClientFactory({...config, transport: createSampleDesignTransport({fetchImpl: fetchSample})});
    },
    imports: fileId => createImportTransport({projectId,fileId,fetchImpl,getOptions,isCurrent}),
    list: ({signal}) => send('/files', signal),
    receipt: (id, {signal}) => { if (!rawUuid(id)) bad('invalid_upload'); return send('/raw-uploads/' + id, signal); },
    upload: (request, {signal}) => send('/raw-uploads', signal, request),
    content: (id, {signal}) => { if (!rawUuid(id)) bad('invalid_upload'); return send('/files/' + id + '/content', signal); },
  };
}

export function createPrivateTxtController({projectId, transport = null, onDenied = () => {}, cryptoImpl = globalThis.crypto,
  setTimer = setTimeout, clearTimer = clearTimeout, now = () => performance.now(),
  documentImpl = globalThis.document, urlImpl = globalThis.URL, BlobImpl = globalThis.Blob,
  sampleDesignClientFactory = typeof transport?.sampleDesigns === 'function' ? config => transport.sampleDesigns(config) : null} = {}) {
  if (!rawUuid(projectId)) bad('invalid_upload');
  let disposed = false, started = false, op = null, file = null, attempt = null, generation = 0;
  let state = {phase: 'idle', code: null, items: [], chosen: null, receipt: null, unknown: false};
  let listConfirmed = false, selectionError = null;
  const urls = new Set();
  const importReports = new Map();
  let restoreTail = Promise.resolve();
  function scheduleRestore(run) {
    const next = restoreTail.then(() => disposed ? false : run());
    restoreTail = next.catch(() => false);
    return next;
  }
  function reconcileImports(items) { for (const [id, entry] of importReports) { const next = items.find(x => x.file_id === id); if (!next || !same(next, entry.file)) { entry.design?.dispose(); entry.model.dispose(); importReports.delete(id); } } }
  function importReport(id) {
    if (disposed || !listConfirmed || !transport?.isCurrent()) return null;
    const stored = state.items.find(x => x.file_id === id); if (!stored || !isRawFile(stored, projectId)) return null;
    if (!importReports.has(id)) importReports.set(id, {file: {...stored}, model: createPrivateImportController({file: stored, transport: transport.imports?.(id) || null, restoreScheduler: scheduleRestore, onDenied(response) { dispose(); onDenied(response); }})});
    return importReports.get(id).model;
  }
  function sampleDesign(id) {
    const reportModel = importReport(id); if (!reportModel) return null;
    const entry = importReports.get(id);
    if (!entry.design) entry.design = createPrivateSampleDesignHost({reportModel, projectId, fileId: id, cryptoImpl,
      isCurrent: () => !disposed && importReports.get(id) === entry && transport?.isCurrent() === true,
      clientFactory: sampleDesignClientFactory, loadScheduler: scheduleRestore, onDenied(response) { dispose(); onDenied(response); }});
    return entry.design;
  }
  const idleDownload = () => ({phase: 'idle', file_id: null, code: null});
  let downloadState = idleDownload();
  const listeners = new Set();
  const current = work => !disposed && op === work && generation === work.generation && transport?.isCurrent() === true && !work.controller.signal.aborted;
  const guard = work => {
    if (now() >= work.deadline || work.readDeadline !== null && now() >= work.readDeadline) { cancel(work); bad('cancelled'); }
    if (!current(work)) bad('stale');
  };
  const emit = () => { if (!disposed) for (const fn of listeners) fn(); };
  const update = patch => { state = {...state, ...patch}; emit(); };
  function cancel(work) {
    if (!work) return; work.controller.abort(); clearTimer(work.timer); clearTimer(work.readTimer);
    if (work.kind === 'download') {
      if (work.reader && work.cancelledReader !== work.reader) {
        work.cancelledReader = work.reader; work.cancelledResponse = work.response;
        try { void work.reader.cancel()?.catch(() => {}); } catch {}
      } else if (!work.reader && work.response && work.cancelledResponse !== work.response) {
        work.cancelledResponse = work.response;
        try { void work.response.body?.cancel()?.catch(() => {}); } catch {}
      }
      return;
    }
    try { void (work.reader ? work.reader.cancel() : work.response?.body?.cancel())?.catch(() => {}); } catch {}
  }
  function begin(ms) {
    const work = {controller: new AbortController(), generation, timer: null, readTimer: null, readDeadline: null, reader: null, response: null, deadline: now() + ms};
    work.cancelled = new Promise((_, reject) => work.controller.signal.addEventListener('abort', () => reject(Error('cancelled')), {once: true}));
    void work.cancelled.catch(() => {}); op = work; work.timer = setTimer(() => cancel(work), ms); return work;
  }
  const wait = async (work, pending) => { const result = await Promise.race([pending, work.cancelled]); guard(work); return result; };
  async function request(work, kind, input) {
    guard(work); work.response = null;
    const remaining = Math.floor(work.deadline - now()); if (remaining <= 0) bad('cancelled');
    const ms = Math.min(kind === 'upload' ? 60000 : 15000, remaining);
    work.readDeadline = now() + ms; work.readTimer = setTimer(() => cancel(work), ms);
    try {
      const pending = Promise.resolve(kind === 'list' ? transport.list({signal: work.controller.signal}) : transport[kind](input, {signal: work.controller.signal})).then(response => {
        try { guard(work); } catch (error) { try { void response.body?.cancel()?.catch(() => {}); } catch {} throw error; }
        return response;
      });
      const response = await wait(work, pending); work.response = response;
      if (response.redirected || !/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(response.headers.get('content-type') || '')) bad('uploads_unavailable');
      const cap = kind === 'list' ? 16384 : 4096, length = response.headers.get('content-length');
      if (length !== null && (!/^\d+$/.test(length) || Number(length) > cap)) bad('uploads_unavailable');
      const reader = response.body?.getReader(); if (!reader) bad('uploads_unavailable'); work.reader = reader;
      const chunks = []; let size = 0;
      for (;;) { const part = await wait(work, reader.read()); if (part.done) break; size += part.value.byteLength; if (size > cap) bad('uploads_unavailable'); chunks.push(part.value); }
      const bytes = new Uint8Array(size); let offset = 0; for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
      const body = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes)); guard(work);
      if ([401, 403].includes(response.status) || response.status === 409 && errorCode(response.status, body) !== 'upload_request_conflict' || response.status === 404 && errorCode(response.status, body) === 'project_not_found') {
        const code = errorCode(response.status, body); dispose(); onDenied({status: response.status, body}); bad(code);
      }
      return {status: response.status, body};
    } catch (error) {
      if (!disposed && [401, 403, 409].includes(work.response?.status)) {
        const status = work.response.status; dispose(); onDenied({status, body: {}});
      }
      throw error;
    } finally {
      clearTimer(work.readTimer); work.readTimer = null; work.readDeadline = null;
      try { void work.reader?.cancel()?.catch(() => {}); work.reader?.releaseLock(); } catch {}
      work.reader = null;
    }
  }
  const checkedReceipt = (response, expected) => {
    if (response.status !== 200 || !exact(response.body, ['file']) || !isRawFile(response.body.file, projectId)) bad(errorCode(response.status, response.body));
    const value = response.body.file;
    if (expected && (!['request_id', 'filename', 'bytes', 'sha256', 'format'].every(k => value[k] === expected[k]) || expected.file_id && !same(value, expected))) bad('upload_result_unknown');
    return {...value};
  };
  async function readList(work) {
    const response = await request(work, 'list');
    if (response.status !== 200 || !isRawList(response.body, projectId)) bad(errorCode(response.status, response.body));
    return response.body.items.map(x => ({...x}));
  }
  function finish(work) { if (op === work) { op = null; cancel(work); emit(); } }
  function lostBinding() {
    if (disposed) return true;
    if (transport && !transport.isCurrent()) { dispose(); onDenied({status: 409, body: {}}); return true; }
    return false;
  }
  function revokeURL(lease) {
    urls.delete(lease); clearTimer(lease.timer); lease.timer = null;
    const url = lease.url; lease.url = null;
    if (url !== null) { try { urlImpl.revokeObjectURL(url); } catch {} }
    const anchor = lease.anchor; lease.anchor = null;
    if (anchor) { anchor.removeAttribute?.('href'); anchor.removeAttribute?.('download'); anchor.remove?.(); }
  }
  const revokeURLs = () => { for (const lease of [...urls]) revokeURL(lease); };
  function resetDownload() { revokeURLs(); downloadState = idleDownload(); }
  function canDownload(id) {
    return !disposed && !op && urls.size === 0 && !state.unknown && state.phase !== 'unavailable' && listConfirmed
      && transport?.isCurrent() === true && rawUuid(id) && state.items.some(x => x.file_id === id && isRawFile(x, projectId))
      && typeof documentImpl?.createElement === 'function' && typeof documentImpl?.body?.append === 'function'
      && typeof urlImpl?.createObjectURL === 'function' && typeof urlImpl?.revokeObjectURL === 'function' && typeof BlobImpl === 'function';
  }
  function cancelDownload() {
    if (op?.kind === 'download') cancel(op);
    revokeURLs();
  }
  async function download(id) {
    if (transport && !transport.isCurrent()) { lostBinding(); return false; }
    if (!canDownload(id)) return false;
    const expected = {...state.items.find(x => x.file_id === id)};
    resetDownload();
    const work = begin(60000); work.kind = 'download';
    let buffer = null, lease = null, handedOff = false;
    downloadState = {phase: 'reading', file_id: id, code: null};
    try {
      emit();
      // An emit listener can dispose this project or replace the identity.
      guard(work);
      const pending = Promise.resolve(transport.content(id, {signal: work.controller.signal})).then(response => {
        work.response = response;
        try { guard(work); } catch (error) { cancel(work); throw error; }
        return response;
      });
      const response = await wait(work, pending); guard(work);
      if ([401, 403, 409].includes(response.status)) { dispose(); onDenied({status: response.status, body: {}}); bad('download_unconfirmed'); }
      if (response.status !== 200 || response.redirected) bad('download_unconfirmed');
      const h = response.headers, disposition = h.get('Content-Disposition');
      if (h.get('Content-Type') !== 'application/octet-stream' || h.get('Content-Length') !== String(expected.bytes)
        || h.get('X-Cyomics-File-Sha256') !== expected.sha256 || h.get('Cache-Control') !== 'no-store'
        || h.get('X-Content-Type-Options') !== 'nosniff' || h.get('Referrer-Policy') !== 'no-referrer'
        || typeof disposition !== 'string' || !disposition.startsWith('attachment;') || /[\r\n]/.test(disposition)) bad('download_unconfirmed');
      guard(work); buffer = new Uint8Array(expected.bytes);
      const reader = response.body?.getReader(); if (!reader) bad('download_unconfirmed'); work.reader = reader;
      let size = 0;
      for (;;) {
        guard(work); const part = await wait(work, reader.read()); guard(work); if (part.done) break;
        if (!(part.value instanceof Uint8Array) || size + part.value.byteLength > expected.bytes || size + part.value.byteLength > MAX) bad('download_unconfirmed');
        buffer.set(part.value, size); size += part.value.byteLength;
      }
      if (size !== expected.bytes) bad('download_unconfirmed');
      downloadState = {phase: 'verifying', file_id: id, code: null}; emit(); guard(work);
      if (!await wait(work, verifyRawContent(buffer.buffer, expected, cryptoImpl))) bad('download_unconfirmed');
      guard(work);
      const blob = new BlobImpl([buffer], {type: 'application/octet-stream'}); guard(work);
      lease = {url: null, timer: null, anchor: null}; urls.add(lease);
      guard(work); lease.url = urlImpl.createObjectURL(blob); guard(work);
      lease.anchor = documentImpl.createElement('a'); guard(work);
      lease.anchor.href = lease.url; lease.anchor.download = expected.filename; lease.anchor.style.display = 'none';
      documentImpl.body.append(lease.anchor); guard(work);
      downloadState = {phase: 'handoff', file_id: id, code: null}; emit(); guard(work);
      // This is the final fence before handing verified bytes to the browser.
      const click = lease.anchor.click; guard(work);
      click.call(lease.anchor); handedOff = true; guard(work);
      lease.timer = setTimer(() => {
        const owned = urls.has(lease);
        revokeURL(lease);
        if (owned && !disposed && transport?.isCurrent() === true) emit();
      }, 1000); guard(work);
      downloadState = {phase: 'handed_off', file_id: id, code: null}; emit(); guard(work);
      return true;
    } catch {
      if (!lostBinding() && op === work) { downloadState = {phase: 'unconfirmed', file_id: id, code: 'download_unconfirmed'}; emit(); }
      return false;
    } finally {
      buffer = null;
      try {
        if (work.reader && work.cancelledReader !== work.reader) { work.cancelledReader = work.reader; work.cancelledResponse = work.response; void work.reader.cancel()?.catch(() => {}); }
        work.reader?.releaseLock();
      } catch {}
      work.reader = null;
      if (lease && (!handedOff || !current(work))) revokeURL(lease);
      finish(work);
    }
  }
  async function reload() {
    if (disposed || op || state.unknown) return false;
    if (!transport) { update({phase: 'unavailable', code: 'uploads_unavailable', items: []}); return false; }
    resetDownload(); listConfirmed = false; selectionError = null;
    const work = begin(15000); update({phase: 'loading', code: null, items: []});
    try { const items = await readList(work); guard(work); listConfirmed = true; reconcileImports(items); update({items, phase: 'ready'}); return true; }
    catch { if (!lostBinding() && op === work) { file = null; attempt = null; update({items: [], chosen: null, receipt: null, phase: 'unavailable', code: 'uploads_unavailable'}); } return false; }
    finally { finish(work); }
  }
  function select(selected) {
    if (disposed || op || state.unknown) return false;
    resetDownload();
    generation++; file = null; attempt = null; selectionError = null;
    const actualBytes = Number.isSafeInteger(selected?.size) && selected.size >= 0 ? selected.size : null;
    const code = typeof selected?.name === 'string' && !/\.txt$/i.test(selected.name) ? 'not_txt'
      : !selected || !validTxtName(selected.name) || actualBytes === null || typeof selected.arrayBuffer !== 'function' ? 'invalid_file'
      : actualBytes === 0 ? 'empty_file' : actualBytes > MAX ? 'too_large' : null;
    if (code) {
      selectionError = {code, actualBytes, maxBytes: MAX};
      update({chosen: null, receipt: null, phase: 'invalid', code: 'invalid_upload'}); return false;
    }
    file = selected; update({chosen: {filename: selected.name, bytes: selected.size}, receipt: null, phase: 'chosen', code: null}); return true;
  }
  async function confirm(work) {
    const received = checkedReceipt(await request(work, 'receipt', attempt.request_id), attempt);
    const items = await readList(work); guard(work);
    if (!items.some(x => same(x, received))) bad('upload_result_unknown');
    attempt = null; file = null; listConfirmed = true; reconcileImports(items);
    update({phase: 'saved', code: null, items, receipt: received, chosen: null, unknown: false});
    return true;
  }
  async function save() {
    if (disposed || op || !file || state.unknown || !transport || state.phase === 'unavailable') return false;
    resetDownload(); const work = begin(60000); let postAttempted = false;
    update({phase: 'hashing', code: null});
    try {
      const raw = file; let bytes = await wait(work, raw.arrayBuffer());
      if (!(bytes instanceof ArrayBuffer) || bytes.byteLength !== raw.size || bytes.byteLength > MAX) bad('invalid_upload');
      const sha256 = hex(await wait(work, cryptoImpl.subtle.digest('SHA-256', bytes))); bytes = null;
      const request_id = cryptoImpl.randomUUID(); if (!rawUuid(request_id)) bad('invalid_upload');
      attempt = {request_id, filename: raw.name, bytes: raw.size, sha256, format: 'proteinGroups_txt'};
      const headers = {'Content-Type': 'application/octet-stream', 'Idempotency-Key': request_id, 'X-Cyomics-Filename': txtFilenameHeader(raw.name),
        'X-Cyomics-File-Format': 'proteinGroups_txt', 'X-Cyomics-File-Bytes': String(raw.size), 'X-Cyomics-File-Sha256': sha256, 'X-Cyomics-Request': '1'};
      guard(work); update({phase: 'saving'}); guard(work); postAttempted = true;
      const response = await request(work, 'upload', {file: raw, headers});
      if (![200, 201].includes(response.status)) {
        const code = errorCode(response.status, response.body);
        if ([400, 409, 413, 415, 429].includes(response.status) && code !== 'uploads_unavailable') { attempt = null; update({phase: 'failed', code}); return false; }
        bad('upload_result_unknown');
      }
      if (!exact(response.body, ['file']) || !isRawFile(response.body.file, projectId)) bad('upload_result_unknown');
      const initial = checkedReceipt({...response, status: 200}, attempt); attempt = initial;
      update({phase: 'confirming'}); return await confirm(work);
    } catch {
      if (!lostBinding() && op === work) update({phase: postAttempted ? 'unknown' : 'failed', code: postAttempted ? 'upload_result_unknown' : 'invalid_upload', unknown: postAttempted, receipt: null});
      return false;
    } finally { finish(work); }
  }
  async function check() {
    if (disposed || op || !attempt || !state.unknown || !transport) return false;
    resetDownload(); const work = begin(15000); update({phase: 'confirming', code: null});
    try { return await confirm(work); }
    catch { if (!lostBinding() && op === work) update({phase: 'unknown', code: 'upload_result_unknown', unknown: true, receipt: null}); return false; }
    finally { finish(work); }
  }
  function dispose() {
    if (disposed) return; disposed = true; generation++; const old = op; op = null; cancel(old); file = null; attempt = null;
    listConfirmed = false; selectionError = null; resetDownload();
    reconcileImports([]);
    state = {phase: 'disposed', code: null, items: [], chosen: null, receipt: null, unknown: false};
    for (const fn of listeners) fn(); listeners.clear();
  }
  return {select, save, check, reload, dispose, download, canDownload, cancelDownload, importReport, sampleDesign,
    start() { if (!started) { started = true; return reload(); } return Promise.resolve(false); },
    status() { return {...state, selectionError: selectionError && {...selectionError}, download: {...downloadState}, items: state.items.map(x => ({...x})), chosen: state.chosen && {...state.chosen}, receipt: state.receipt && {...state.receipt}, busy: !!op,
      canChoose: !disposed && !op && !state.unknown && state.phase !== 'unavailable', canSave: !disposed && !!file && !!transport && !op && !state.unknown && state.phase !== 'unavailable'}; },
    subscribe(fn) { if (disposed) return () => {}; listeners.add(fn); return () => listeners.delete(fn); },
  };
}

// The download consumer bounds the stream before calling this checksum helper.
export async function verifyRawContent(buffer, receipt, cryptoImpl = globalThis.crypto) {
  return buffer instanceof ArrayBuffer && isRawFile(receipt, receipt?.project_id) && buffer.byteLength === receipt.bytes
    && hex(await cryptoImpl.subtle.digest('SHA-256', buffer)) === receipt.sha256;
}

export function mountPrivateTxt(root, model, t = zh => zh) {
  const doc = root.ownerDocument, bindings = [];
  const make = (tag, text = '', cls = '') => { const n = doc.createElement(tag); n.textContent = text; n.className = cls; return n; };
  const bind = (n, event, fn) => { n.addEventListener(event, fn); bindings.push([n, event, fn]); };
  const heading = make('h3', t('项目原始文件', 'Project original files'));
  const picker = make('label', t('添加定量表 TXT', 'Add quantification TXT'), 'field'), input = make('input'); input.type = 'file'; input.accept = '.txt'; input.setAttribute('aria-label', t('选择一个定量表 TXT', 'Choose one quantification TXT')); picker.append(input);
  picker.append(make('small', t('单文件：1 字节至 25 MiB（26214400 字节）', 'Single file: 1 byte to 25 MiB (26214400 bytes)')));
  const chosen = make('p', '', 'note'), result = make('p', '', 'signup-state'); result.setAttribute('role', 'status'); result.setAttribute('aria-live', 'polite');
  const actions = make('div', '', 'row'), save = make('button', t('保存到此项目', 'Save to this project'), 'primary'), check = make('button', t('只读查询保存结果', 'Check save result (read-only)')), reload = make('button', t('刷新文件列表', 'Reload files'));
  for (const button of [save, check, reload]) button.type = 'button'; actions.append(save, check, reload);
  const files = make('div'), note = make('p', t('文件保存与导入报告分别确认；未关联 FASTA、未做科研计算。', 'File storage and import reports are confirmed separately. No FASTA association or scientific computation.'), 'note');
  root.replaceChildren(heading, picker, chosen, actions, result, files, note); let disposed = false, rowBindings = [], reportViews = [];
  const clearRows = () => { for (const stop of reportViews) stop(); reportViews = []; for (const [n, fn] of rowBindings) n.removeEventListener('click', fn); rowBindings = []; };
  function paint() {
    if (disposed) return; const v = model.status(); input.disabled = !v.canChoose; save.disabled = !v.canSave; check.hidden = !v.unknown; check.disabled = v.busy; reload.disabled = v.busy || v.unknown;
    chosen.textContent = v.chosen ? v.chosen.filename + ' · ' + v.chosen.bytes + ' bytes · ' + (v.unknown ? t('保存状态待确认', 'Save status unconfirmed') : t('当前选择', 'Current selection')) : '';
    const messages = {loading: ['正在读取服务端文件列表…', 'Reading server files…'], unavailable: ['暂时无法确认服务器文件状态，请重新检查。', 'Server file status cannot be confirmed right now. Please check again.'], invalid: ['请选择一个有效 TXT（1 字节至 25 MiB）。', 'Choose one valid TXT (1 byte to 25 MiB).'], hashing: ['正在校验原始字节…', 'Checking original bytes…'], saving: ['正在保存原始文件…', 'Saving original file…'], confirming: ['正在回读确认保存结果…', 'Confirming the server receipt…'], saved: ['原始文件已保存并回读确认，导入状态见文件报告。', 'Original file stored and confirmed by readback. See its import report for parsing status.'], unknown: ['保存结果未知，请只读查询；暂勿重复保存。', 'Save outcome unknown. Use read-only lookup; do not resubmit.'], failed: ['本次保存未确认，请检查错误后手动操作。', 'Save not confirmed. Review the error before another manual action.']};
    result.textContent = messages[v.phase] ? t(...messages[v.phase]) + (v.code ? ' (' + v.code + ')' : '') : '';
    if (v.phase === 'invalid' && v.selectionError) {
      const reasons = {empty_file: ['文件为空。', 'The file is empty.'], not_txt: ['仅支持 TXT 文件。', 'Only TXT files are supported.'],
        too_large: ['文件超过单文件大小上限。', 'The file exceeds the single-file size limit.'], invalid_file: ['文件无法读取，或文件名/大小无效。', 'The file cannot be read, or its name or size is invalid.']};
      const bytes = v.selectionError.actualBytes, actual = Number.isSafeInteger(bytes) && bytes >= 0 ? bytes + ' bytes (' + (bytes / 1048576).toFixed(2) + ' MiB)' : t('未知', 'Unknown');
      result.textContent = t(...(reasons[v.selectionError.code] || reasons.invalid_file)) + ' ' + t('实际大小', 'Actual size') + ': ' + actual
        + ' · ' + t('允许范围', 'Allowed range') + ': 1–' + MAX + ' bytes (25 MiB)';
    }
    const downloadMessages = {reading: ['正在读取原始 TXT…', 'Reading original TXT…'], verifying: ['正在校验下载字节…', 'Verifying download bytes…'], handoff: ['正在交给浏览器保存…', 'Handing off to the browser…'], handed_off: ['已校验并交给浏览器保存。', 'Verified and handed to the browser for saving.'], unconfirmed: ['未能确认下载，请手动重试。', 'Download could not be confirmed. Retry manually.']};
    if (downloadMessages[v.download.phase]) result.textContent = t(...downloadMessages[v.download.phase]);
    clearRows();
    files.replaceChildren();
    if (!v.items.length) files.append(make('p', v.phase === 'ready' ? t('服务端暂无已保存文件。', 'No stored server files.') : '', 'note'));
    for (const item of v.items) {
      const row = make('div', '', 'list-row'), info = make('div');
      info.append(make('strong', item.filename), make('p', item.bytes + ' bytes · ' + item.createdAt, 'note'));
      const details = make('details'), summary = make('summary', t('文件详情', 'File details')); details.append(summary, make('p', 'file_id: ' + item.file_id, 'note'), make('p', 'SHA256: ' + item.sha256, 'sequence')); info.append(details); row.append(info);
      const download = make('button', t('下载原始 TXT', 'Download original TXT')); download.type = 'button'; download.disabled = !model.canDownload(item.file_id);
      const click = () => { if (!disposed) void model.download(item.file_id); }; download.addEventListener('click', click); rowBindings.push([download, click]); row.append(download);
      const reportModel = model.importReport(item.file_id);
      if (reportModel) {
        const reportRoot = make('div'); info.append(reportRoot);
        reportViews.push(mountPrivateImportReport(reportRoot, reportModel, {binding: {project_id: item.project_id, raw_file_id: item.file_id, expected_raw_sha256: item.sha256}, t}));
        const design = model.sampleDesign?.(item.file_id);
        if (design) { const designRoot = make('div'); info.append(designRoot); reportViews.push(mountPrivateSampleDesignHost(designRoot, design, {t})); }
        void reportModel.restore();
      }
      files.append(row);
    }
  }
  bind(input, 'change', () => { if (!disposed) { model.select(input.files?.[0]); input.value = ''; } });
  bind(save, 'click', () => { if (!disposed) void model.save(); }); bind(check, 'click', () => { if (!disposed) void model.check(); }); bind(reload, 'click', () => { if (!disposed) void model.reload(); });
  const unsubscribe = model.subscribe(paint); paint(); void model.start();
  return () => { if (disposed) return; disposed = true; model.cancelDownload(); unsubscribe(); clearRows(); for (const [n, event, fn] of bindings) n.removeEventListener(event, fn); input.value = ''; root.replaceChildren(); };
}
