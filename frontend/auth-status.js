const endpoint = '/api/auth/status';
const maxBytes = 4096;
export function isAuthStatus(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    && [Object.prototype, null].includes(Object.getPrototypeOf(value))
    && Object.keys(value).length === 3 && ['status', 'service', 'version'].every(key => Object.hasOwn(value, key))
    && value.status === 'ok' && value.service === 'Cy_Omics_v2.0-auth' && value.version === '2.197.0';
}

export function createAuthStatusController({fetchImpl = globalThis.fetch, setTimer = setTimeout, clearTimer = clearTimeout} = {}) {
  const listeners = new Set();
  let state = 'checking', started = false, disposed = false, current = null;
  const emit = () => { for (const listener of listeners) listener(); };
  const active = request => !disposed && current === request;
  function cancel(request) {
    if (!request) return;
    clearTimer(request.timer); request.controller.abort();
    try { void (request.reader ? request.reader.cancel() : request.response?.body?.cancel())?.catch(() => {}); } catch {}
  }
  async function readJSON(response, request) {
    const mime = response.headers.get('content-type') || '';
    if (!/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(mime)) throw Error('unavailable');
    const length = response.headers.get('content-length');
    if (length !== null && (!/^\d+$/.test(length) || Number(length) > maxBytes)) throw Error('unavailable');
    const reader = response.body?.getReader();
    if (!reader) throw Error('unavailable');
    request.reader = reader;
    const chunks = []; let count = 0;
    try {
      while (active(request)) {
        const part = await reader.read();
        if (!active(request)) throw Error('unavailable');
        if (part.done) break;
        count += part.value.byteLength;
        if (count > maxBytes) throw Error('unavailable');
        chunks.push(part.value);
      }
      if (!active(request)) throw Error('unavailable');
      const bytes = new Uint8Array(count); let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
      return JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes));
    } finally {
      try { void reader.cancel().catch(() => {}); } catch {}
      try { reader.releaseLock(); } catch {}
      if (request.reader === reader) request.reader = null;
    }
  }
  async function check() {
    if (disposed || current) return false;
    started = true;
    const request = {controller: new AbortController(), timer: null, reader: null, response: null}; current = request; state = 'checking'; emit();
    request.timer = setTimer(() => {
      if (!active(request)) return;
      current = null; cancel(request); state = 'unavailable'; emit();
    }, 2500);
    try {
      const response = await fetchImpl(endpoint, {method: 'GET', credentials: 'omit', cache: 'no-store', redirect: 'error', signal: request.controller.signal});
      request.response = response;
      if (!active(request)) { cancel(request); return false; }
      if (response.status !== 200 || response.redirected) throw Error('unavailable');
      const value = await readJSON(response, request);
      if (!active(request)) return false;
      state = isAuthStatus(value) ? 'available' : 'unavailable'; return state === 'available';
    } catch {
      if (active(request)) state = 'unavailable'; return false;
    } finally {
      if (active(request)) { cancel(request); current = null; emit(); }
    }
  }
  return {check,
    start() { if (!started && !disposed) return check(); return Promise.resolve(false); },
    status() { return {state, busy: Boolean(current), started}; },
    subscribe(listener) { if (disposed) return () => {}; listeners.add(listener); return () => listeners.delete(listener); },
    dispose() { if (disposed) return; disposed = true; cancel(current); current = null; listeners.clear(); },
  };
}

export function createAuthStatusRoutes(options = {}) {
  let route = null, controller = null;
  return {
    enter(nextRoute) {
      const next = ['/login', '/register'].includes(nextRoute) ? nextRoute : null;
      if (route !== next) { controller?.dispose(); controller = next ? createAuthStatusController(options) : null; route = next; }
      return controller;
    },
    current() { return controller; },
    dispose() { controller?.dispose(); controller = null; route = null; },
  };
}

export function mountAuthStatus(root, model, t = zh => zh) {
  if (!model) { root.replaceChildren(); return () => {}; }
  const doc = root.ownerDocument, line = doc.createElement('div'), status = doc.createElement('span'), button = doc.createElement('button'), note = doc.createElement('small');
  let disposed = false;
  line.className = 'auth-service-status';
  status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite'); status.setAttribute('aria-atomic', 'true');
  button.type = 'button'; button.className = 'link'; button.textContent = t('重新检查', 'Recheck');
  note.textContent = t('仅表示服务连通', 'Service connectivity only');
  line.append(status, button, note); root.replaceChildren(line);
  function paint() {
    if (disposed) return;
    const value = model.status(); line.dataset.state = value.state;
    status.textContent = t('认证服务：', 'Authentication service: ') + (value.state === 'checking' ? t('检查中…', 'Checking…') : value.state === 'available' ? t('可用', 'Available') : t('不可用', 'Unavailable'));
    button.disabled = value.busy || value.state === 'checking';
  }
  const recheck = () => { if (!disposed) void model.check(); };
  button.addEventListener('click', recheck); const unsubscribe = model.subscribe(paint); paint(); void model.start();
  return () => { if (disposed) return; disposed = true; unsubscribe(); button.removeEventListener('click', recheck); root.replaceChildren(); };
}
