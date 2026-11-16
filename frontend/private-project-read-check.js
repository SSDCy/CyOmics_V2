import {isVerifiedSession} from '/auth-login.js';
import {isProject} from '/private-projects.js';

const accounts = Object.freeze([
  Object.freeze({email: 'owner-a@example.invalid', id: '11111111-1111-4111-8111-111111111111'}),
  Object.freeze({email: 'owner-b@example.invalid', id: '22222222-2222-4222-8222-222222222222'}),
]);
const exact = (value, keys) => value !== null && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const allowedAddress = address => address?.origin === 'http://127.0.0.1:4206'
  && address.pathname === '/__private-test/project-read' && address.search === '' && address.hash === '';
const projectPath = id => '/api/v1/projects/' + id;

export function createProjectReadCheck({fetchImpl = globalThis.fetch, address = globalThis.location,
  setTimer = setTimeout, clearTimer = clearTimeout} = {}) {
  let used = false, disposed = false, controller = null, context = null, reader = null, response = null;
  let timer = null, reason = 'CANCELLED';
  let state = {phase: 'IDLE', verdict: null, email: null, code: 'READY', rows: []};
  const listeners = new Set();
  const status = () => ({...state, rows: state.rows.map(row => ({...row})), canStart: !used && !disposed});
  const emit = () => { if (!disposed) for (const listener of listeners) listener(); };
  const live = () => !disposed && controller !== null && !controller.signal.aborted;
  const guard = () => {
    if (!live()) throw Error(reason);
    if (!allowedAddress(address)) throw Error('ADDRESS_REJECTED');
  };
  function cancelBody(body) { try { void body?.cancel()?.catch(() => {}); } catch {} }
  function abort(code) {
    reason = code; context = null; controller?.abort();
    try { void reader?.cancel()?.catch(() => {}); } catch {}
    if (!reader) cancelBody(response?.body);
  }
  async function run() {
    if (used || disposed) return status();
    used = true;
    if (!allowedAddress(address)) { state = {...state, phase: 'DONE', verdict: 'BLOCKED', code: 'ADDRESS_REJECTED'}; emit(); return status(); }
    controller = new AbortController();
    const signal = controller.signal;
    let rejectCancelled;
    const cancelled = new Promise((_, reject) => { rejectCancelled = reject; });
    void cancelled.catch(() => {});
    const onAbort = () => rejectCancelled(Error(reason));
    signal.addEventListener('abort', onAbort, {once: true});
    const wait = pending => Promise.race([pending, cancelled]);
    timer = setTimer(() => abort('TIMEOUT'), 12000);
    state = {...state, phase: 'RUNNING', code: 'CHECKING'}; emit();
    function mark(code) { if (state.rows.length) state.rows[state.rows.length - 1].code = code; }
    async function get(path, withContext = false) {
      guard(); response = null;
      const options = {method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal};
      if (withContext) {
        if (!context) throw Error('CONTEXT_REJECTED');
        options.headers = {'X-Cyomics-Account-Context': context};
      }
      const pending = Promise.resolve(fetchImpl(path, options)).then(value => {
        if (!live()) { cancelBody(value?.body); throw Error(reason); }
        return value;
      });
      response = await wait(pending); guard();
      const code = response.status;
      if (!Number.isInteger(code) || code < 100 || code > 599 || response.redirected) throw Error('UNEXPECTED_RESPONSE');
      state.rows.push({method: 'GET', path, status: code, code: 'RECEIVED'});
      return response;
    }
    async function json(value, cap) {
      guard();
      if (!/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(value.headers.get('content-type') || '')) throw Error('INVALID_BODY');
      const length = value.headers.get('content-length');
      if (length !== null && (!/^\d+$/.test(length) || Number(length) > cap)) throw Error('INVALID_BODY');
      reader = value.body?.getReader(); if (!reader) throw Error('INVALID_BODY');
      const parts = []; let size = 0;
      try {
        for (;;) {
          const chunk = await wait(reader.read()); guard(); if (chunk.done) break;
          size += chunk.value.byteLength; if (size > cap) throw Error('INVALID_BODY'); parts.push(chunk.value);
        }
        const data = new Uint8Array(size); let offset = 0;
        for (const part of parts) { data.set(part, offset); offset += part.byteLength; }
        return JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(data));
      } finally {
        try { void reader?.cancel()?.catch(() => {}); reader?.releaseLock(); } catch {}
        reader = null;
      }
    }
    try {
      const me = await get('/api/v1/me');
      if (me.status !== 200) throw Error('SESSION_REJECTED');
      const body = await json(me, 4096); guard();
      if (!isVerifiedSession(body)) throw Error('SESSION_REJECTED');
      const index = accounts.findIndex(account => account.email === body.user.email);
      if (index < 0) throw Error('SESSION_REJECTED');
      const header = me.headers.get('X-Cyomics-Account-Context');
      if (typeof header !== 'string' || !/^[A-Za-z0-9_-]{22}$/.test(header)) throw Error('CONTEXT_REJECTED');
      context = header; state.email = accounts[index].email; mark('SESSION_CONFIRMED'); emit();
      const own = await get(projectPath(accounts[index].id), true);
      if (own.status !== 200) throw Error('OWN_REJECTED');
      const ownBody = await json(own, 16384); guard();
      if (!exact(ownBody, ['project']) || !isProject(ownBody.project) || ownBody.project.project_id !== accounts[index].id) throw Error('OWN_REJECTED');
      mark('OWN_CONFIRMED'); emit();
      const other = await get(projectPath(accounts[1 - index].id), true);
      if (other.status === 200) {
        mark('OTHER_VISIBLE'); state = {...state, phase: 'DONE', verdict: 'FAIL', code: 'OTHER_VISIBLE'};
        // Do not inspect an unauthorized success body, even for schema validation.
        cancelBody(other.body);
      } else {
        if (other.status !== 404) throw Error('OTHER_REJECTED');
        const missing = await json(other, 4096); guard();
        if (!exact(missing, ['error']) || !exact(missing.error, ['code']) || missing.error.code !== 'project_not_found') throw Error('OTHER_REJECTED');
        mark('PROJECT_NOT_FOUND'); state = {...state, phase: 'DONE', verdict: 'PASS', code: 'PROJECT_NOT_FOUND'};
      }
    } catch (error) {
      const allowed = ['CANCELLED', 'TIMEOUT', 'ADDRESS_REJECTED', 'SESSION_REJECTED', 'CONTEXT_REJECTED', 'OWN_REJECTED', 'OTHER_REJECTED', 'UNEXPECTED_RESPONSE', 'INVALID_BODY'];
      const code = allowed.includes(error?.message) ? error.message : 'READ_FAILED';
      mark(code); state = {...state, phase: 'DONE', verdict: 'BLOCKED', code};
    } finally {
      context = null; clearTimer(timer); timer = null;
      signal.removeEventListener('abort', onAbort);
      cancelBody(response?.body); response = null; reader = null; controller = null;
      if (disposed) state = {phase: 'DONE', verdict: 'BLOCKED', email: null, code: 'CANCELLED', rows: []};
      emit();
    }
    return status();
  }
  return {run, status,
    subscribe(fn) { if (disposed) return () => {}; listeners.add(fn); return () => listeners.delete(fn); },
    dispose() {
      if (disposed) return; disposed = true; used = true; abort('CANCELLED');
      clearTimer(timer); timer = null;
      state = {phase: 'DONE', verdict: 'BLOCKED', email: null, code: 'CANCELLED', rows: []}; listeners.clear();
    },
  };
}

export function mountProjectReadCheck(root, {host = globalThis, model = createProjectReadCheck()} = {}) {
  const button = root.querySelector('[data-check-start]'), result = root.querySelector('[data-check-result]');
  const email = root.querySelector('[data-check-email]'), rows = root.querySelector('[data-check-rows]');
  let disposed = false;
  function paint() {
    if (disposed) return;
    const value = model.status(); button.disabled = !value.canStart;
    result.textContent = value.verdict ? value.verdict + ' · ' + value.code : value.phase === 'RUNNING' ? '正在只读检查…' : '尚未开始';
    email.textContent = value.email || ''; rows.replaceChildren();
    for (const item of value.rows) {
      const row = root.ownerDocument.createElement('tr');
      for (const key of ['method', 'path', 'status', 'code']) { const cell = root.ownerDocument.createElement('td'); cell.textContent = String(item[key]); row.append(cell); }
      rows.append(row);
    }
  }
  const click = () => { if (!disposed) { button.disabled = true; void model.run(); } };
  const dispose = () => {
    if (disposed) return; disposed = true; model.dispose(); unsubscribe();
    button.removeEventListener('click', click); host.removeEventListener('pagehide', dispose);
    button.disabled = true; email.textContent = ''; result.textContent = ''; rows.replaceChildren();
  };
  const unsubscribe = model.subscribe(paint);
  button.addEventListener('click', click); host.addEventListener('pagehide', dispose); paint();
  return dispose;
}

if (typeof document !== 'undefined') {
  const root = document.getElementById('project-read-check');
  if (root) mountProjectReadCheck(root);
}
