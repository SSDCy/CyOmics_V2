const service = 'Cy_Omics_v2.0-backend';
const versionPattern = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/;

export function isBackendHealth(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    && value.status === 'ok' && value.service === service
    && typeof value.version === 'string' && value.version.length <= 64
    && versionPattern.test(value.version)
    && !(value.version.match(versionPattern)[4]?.split('.').some(x => /^0\d+$/.test(x)));
}

export function mountBackendStatus(root, t = zh => zh, {
  fetchImpl = globalThis.fetch, setTimer = setTimeout, clearTimer = clearTimeout,
} = {}) {
  let disposed = false, current = null;
  const doc = root.ownerDocument;
  const line = doc.createElement('div');
  line.className = 'iw-backend';
  const status = doc.createElement('span');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  status.setAttribute('aria-atomic', 'true');
  const button = doc.createElement('button');
  button.type = 'button';
  button.className = 'link';
  button.textContent = t('重新检查', 'Recheck');
  const note = doc.createElement('small');
  note.textContent = t('仅检查连接；认证、存储、计算尚未接入', 'Connection check only; authentication, storage and computation are not connected');
  line.append(status, button, note);
  root.replaceChildren(line);

  function paint(state, version = '') {
    line.dataset.connection = state;
    status.textContent = t('后端：', 'Backend: ') + (state === 'checking'
      ? t('检查中…', 'Checking…') : state === 'connected'
        ? t('已连接', 'Connected') + ' (v' + version + ')' : t('未连接', 'Not connected'));
    button.disabled = state === 'checking';
  }
  function cancel(request) {
    if (!request) return;
    clearTimer(request.timer);
    request.controller.abort();
  }
  async function check() {
    if (disposed) return;
    cancel(current);
    const request = {controller: new AbortController(), timer: null};
    current = request;
    const active = () => !disposed && current === request;
    paint('checking');
    request.timer = setTimer(() => {
      if (!active()) return;
      current = null;
      request.controller.abort();
      paint('disconnected');
    }, 2500);
    try {
      const response = await fetchImpl('/api/health', {
        method: 'GET', credentials: 'omit', cache: 'no-store', redirect: 'error',
        signal: request.controller.signal,
      });
      if (!active()) return;
      if (!response.ok) throw new Error('HEALTH_UNAVAILABLE');
      const data = await response.json();
      if (!active()) return;
      const valid = isBackendHealth(data);
      paint(valid ? 'connected' : 'disconnected', valid ? data.version : '');
    } catch {
      if (active()) paint('disconnected');
    } finally {
      if (active()) {
        clearTimer(request.timer);
        current = null;
      }
    }
  }
  const recheck = () => { void check(); };
  button.addEventListener('click', recheck);
  void check();
  return () => {
    disposed = true;
    cancel(current);
    current = null;
    button.removeEventListener('click', recheck);
    root.replaceChildren();
  };
}
