const bytes = value => new TextEncoder().encode(value).byteLength;
const exact = (value, keys) => value !== null && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
function emailValid(value) {
  if (typeof value !== 'string') return false;
  const address = value.trim(), parts = address.split('@'), local = parts[0], domain = parts[1];
  return parts.length === 2 && Boolean(local) && bytes(address) <= 254 && bytes(local) <= 64
    && /^[A-Za-z0-9.!#$%&'*+\/=?^_`{|}~-]+$/.test(local) && !/^\.|\.$|\.\./.test(local)
    && Boolean(domain) && domain.includes('.') && domain.split('.').every(label => /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/.test(label));
}
export function validateLogin(email, password) {
  const errors = {};
  if (!emailValid(email)) errors.email = 'email_format';
  if (typeof password !== 'string' || !password.length) errors.password = 'password_required';
  else if (bytes(password) > 72) errors.password = 'password_long';
  else if (/[\uD800-\uDFFF]/u.test(password)) errors.password = 'password_format';
  if (typeof email === 'string' && typeof password === 'string' && bytes(JSON.stringify({email: email.trim(), password})) > 4096) errors.form = 'auth_body_too_large';
  return errors;
}
export function isVerifiedSession(body) {
  return exact(body, ['status', 'user']) && body.status === 'authenticated'
    && exact(body.user, ['email', 'emailVerified']) && body.user.emailVerified === true
    && emailValid(body.user.email) && body.user.email === body.user.email.trim();
}
const errorIs = (body, code) => exact(body, ['error']) && exact(body.error, ['code']) && body.error.code === code;
const errorCodes = {
  login: {invalid_login: 400, invalid_credentials: 401, login_context_required: 409, login_rate_limited: 429, login_unavailable: 503, login_result_unknown: 504},
  me: {unauthenticated: 401, session_unavailable: 503},
  logout: {invalid_logout: 400, logout_unavailable: 503},
};
const commonCodes = {not_found: 404, method_not_allowed: 405, auth_origin_rejected: 403, unsupported_media_type: 415, auth_body_too_large: 413};
export function createLoginTransport({enabled = false, fetchImpl = globalThis.fetch} = {}) {
  if (enabled !== true) return null;
  const get = signal => ({method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal});
  const post = (body, signal) => ({...get(signal), method: 'POST', headers: {'Content-Type': 'application/json', 'X-Cyomics-Request': '1'}, body});
  return {
    me: ({signal}) => fetchImpl('/api/v1/me', get(signal)),
    login(payload, {signal}) {
      if (!exact(payload, ['email', 'password']) || Object.keys(validateLogin(payload.email, payload.password)).length) throw Error('invalid_login');
      return fetchImpl('/api/auth/login', post(JSON.stringify({email: payload.email.trim(), password: payload.password}), signal));
    },
    logout: ({signal}) => fetchImpl('/api/auth/logout', post('{}', signal)),
  };
}

export function createLoginController({transport = null, setTimer = setTimeout, clearTimer = clearTimeout} = {}) {
  let state = {phase: 'idle', identity: 'unknown', email: null, ready: false, code: null, errors: {}}, started = false, disposed = false, current = null;
  const listeners = new Set(), emit = () => { for (const listener of listeners) listener(); };
  const active = op => !disposed && current === op;
  const update = patch => { state = {...state, ...patch}; emit(); };
  const unknown = code => update({phase: 'unknown', identity: 'unknown', email: null, ready: false, errors: {}, code});
  function cancel(op) {
    if (!op) return;
    clearTimer(op.timer); op.controller.abort();
    try { void (op.reader ? op.reader.cancel() : op.response?.body?.cancel())?.catch(() => {}); } catch {}
  }
  function begin(phase) {
    const op = {controller: new AbortController(), timer: null, reader: null, response: null};
    op.cancelled = new Promise((_, reject) => op.controller.signal.addEventListener('abort', () => reject(Error('unconfirmed')), {once: true}));
    void op.cancelled.catch(() => {}); current = op;
    update({phase, identity: 'unknown', email: null, ready: false, code: null, errors: {}}); return op;
  }
  function finish(op) { if (active(op)) { current = null; cancel(op); emit(); } }
  async function request(op, kind, payload) {
    op.response = null; op.timer = setTimer(() => cancel(op), 16000);
    try {
      let pending;
      if (kind === 'login') pending = transport.login(payload, {signal: op.controller.signal});
      else pending = transport[kind]({signal: op.controller.signal});
      payload = null;
      const arrived = Promise.resolve(pending).then(response => {
        op.response = response;
        if (!active(op) || op.controller.signal.aborted) { cancel(op); throw Error('unconfirmed'); }
        return response;
      });
      const response = await Promise.race([arrived, op.cancelled]);
      if (response.redirected || !/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(response.headers.get('content-type') || '')) throw Error('unconfirmed');
      const length = response.headers.get('content-length');
      if (length !== null && (!/^\d+$/.test(length) || Number(length) > 4096)) throw Error('unconfirmed');
      const read = async () => {
        const reader = response.body?.getReader(); if (!reader) throw Error('unconfirmed'); op.reader = reader;
        const chunks = []; let size = 0;
        try {
          while (active(op) && !op.controller.signal.aborted) {
            const part = await reader.read(); if (!active(op) || op.controller.signal.aborted) throw Error('unconfirmed');
            if (part.done) break;
            size += part.value.byteLength; if (size > 4096) throw Error('unconfirmed'); chunks.push(part.value);
          }
          if (!active(op) || op.controller.signal.aborted) throw Error('unconfirmed');
          const data = new Uint8Array(size); let offset = 0; for (const chunk of chunks) { data.set(chunk, offset); offset += chunk.byteLength; }
          return JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(data));
        } finally {
          try { void reader.cancel().catch(() => {}); } catch {}
          try { reader.releaseLock(); } catch {}
          if (op.reader === reader) op.reader = null;
        }
      };
      return {status: response.status, body: await Promise.race([read(), op.cancelled])};
    } finally { payload = null; clearTimer(op.timer); op.timer = null; }
  }
  function fixedError(kind, response) {
    for (const [code, status] of Object.entries({...commonCodes, ...errorCodes[kind]})) if (response.status === status && errorIs(response.body, code)) return code;
    return null;
  }
  async function confirmSession(op, reason, expectedEmail = null) {
    const response = await request(op, 'me'); if (!active(op)) return false;
    if (response.status === 200 && isVerifiedSession(response.body)) {
      if (reason === 'logout' || expectedEmail !== null && response.body.user.email !== expectedEmail) {
        unknown(reason === 'logout' ? 'logout_result_unknown' : 'login_result_unknown'); return false;
      }
      update({phase: 'authenticated', identity: 'authenticated', email: response.body.user.email, ready: true, code: null, errors: {}}); return true;
    }
    if (response.status === 401 && errorIs(response.body, 'unauthenticated')) {
      update({phase: reason === 'logout' ? 'signed_out' : reason === 'login' ? 'unknown' : 'idle', identity: 'anonymous', email: null,
        ready: true, code: reason === 'login' ? 'login_result_unknown' : null, errors: {}}); return reason !== 'login';
    }
    unknown(reason === 'logout' ? 'logout_result_unknown' : reason === 'login' ? 'login_result_unknown' : 'session_unavailable'); return false;
  }
  async function checkSession() {
    if (disposed || current) return false;
    started = true;
    if (!transport) { update({phase: 'disabled', identity: 'unknown', email: null, ready: false, code: null, errors: {}}); return false; }
    const op = begin('checking');
    try { return await confirmSession(op, 'check'); } catch { if (active(op)) unknown('session_unavailable'); return false; } finally { finish(op); }
  }
  async function login({email, password} = {}) {
    if (disposed || current || state.identity === 'authenticated') { password = ''; return false; }
    const errors = validateLogin(email, password);
    if (Object.keys(errors).length) { password = ''; update({phase: 'invalid', code: null, errors}); return false; }
    if (!transport) { password = ''; update({phase: 'disabled', code: null}); return false; }
    if (!started || !state.ready) { password = ''; unknown('login_context_required'); return false; }
    const op = begin('login');
    try {
      let pending;
      try { pending = request(op, 'login', {email: email.trim(), password}); } finally { password = ''; email = ''; }
      const response = await pending; if (!active(op)) return false;
      if (response.status === 200 && isVerifiedSession(response.body)) return await confirmSession(op, 'login', response.body.user.email);
      const code = fixedError('login', response);
      if (['invalid_credentials', 'invalid_login', 'login_rate_limited'].includes(code)) {
        update({phase: code === 'login_rate_limited' ? 'limited' : 'failed', identity: 'unknown', email: null, ready: true, code, errors: {}});
      } else unknown(code || 'login_result_unknown');
      return false;
    } catch { if (active(op)) unknown('login_result_unknown'); return false; } finally { password = ''; email = ''; finish(op); }
  }
  async function logout() {
    if (disposed || current && state.phase === 'logout') return false;
    const old = current; current = null; cancel(old); started = true;
    if (!transport) { update({phase: 'disabled', identity: 'unknown', email: null, ready: false, code: null, errors: {}}); return false; }
    const op = begin('logout');
    try {
      const response = await request(op, 'logout'); if (!active(op)) return false;
      if (response.status === 200 && exact(response.body, ['status']) && response.body.status === 'signed_out') return await confirmSession(op, 'logout');
      unknown(fixedError('logout', response) || 'logout_result_unknown'); return false;
    } catch { if (active(op)) unknown('logout_result_unknown'); return false; } finally { finish(op); }
  }
  return {login, logout, checkSession,
    start() { if (!started && !disposed) return checkSession(); return Promise.resolve(false); },
    edit(field, {email, password}) {
      if (disposed || current || !['email', 'password'].includes(field) || state.identity === 'authenticated') return;
      const validation = validateLogin(email, password), errors = {...state.errors};
      if (validation[field]) errors[field] = validation[field]; else delete errors[field];
      if (errors.form && !validation.form) delete errors.form;
      update({errors, ...(state.phase === 'invalid' ? {phase: Object.keys(errors).length ? 'invalid' : 'idle', code: null} : {})});
    },
    status() { return {...state, errors: {...state.errors}, busy: Boolean(current), canLogin: Boolean(transport) && started && state.ready && !current && state.identity !== 'authenticated'}; },
    subscribe(listener) { if (disposed) return () => {}; listeners.add(listener); return () => listeners.delete(listener); },
    dispose() { if (disposed) return; disposed = true; const op = current; current = null; cancel(op); state = {...state, email: null, identity: 'unknown', ready: false}; listeners.clear(); },
  };
}

export function createLoginRoutes(options = {}) {
  let route = null, controller = null;
  return {
    enter(nextRoute) {
      const next = nextRoute === '/login' ? nextRoute : null;
      if (route !== next) { controller?.dispose(); controller = next ? createLoginController(options) : null; route = next; }
      return controller;
    },
    current() { return controller; }, dispose() { controller?.dispose(); controller = null; route = null; },
  };
}

function text(code, t) {
  const labels = {
    email_format: ['请输入有效邮箱。', 'Enter a valid email address.'], password_required: ['请输入注册时设置的密码。', 'Enter the password you set when registering.'],
    password_long: ['密码过长，请检查输入。', 'The password is too long. Check your input.'], password_format: ['密码包含无法使用的字符，请重新输入。', 'The password contains an unsupported character. Enter it again.'],
    invalid_credentials: ['登录未完成，请检查邮箱和密码，并确认邮箱已验证。', 'Sign-in was not completed. Check your email and password, and ensure your email is verified.'],
    invalid_login: ['请检查邮箱和密码后再试。', 'Check your email and password before trying again.'],
    login_context_required: ['登录上下文已失效，请刷新登录页后再试。', 'The sign-in context is unavailable. Refresh the sign-in page before trying again.'],
    login_rate_limited: ['请求过于频繁，请等待后再试；不会自动重发。', 'Too many requests. Wait before trying again. No automatic retry will occur.'],
    login_result_unknown: ['登录结果暂时无法确认，请重新检查登录状态；不会自动重试。', 'The sign-in outcome is unconfirmed. Recheck your sign-in status. No automatic retry will occur.'],
    login_unavailable: ['登录服务暂不可用，当前会话结果未确认，请重新检查登录状态。', 'Sign-in is unavailable. The session outcome is unconfirmed. Recheck your sign-in status.'],
    session_unavailable: ['暂时无法确认登录状态，请重新检查。', 'The sign-in status could not be confirmed. Please recheck.'],
    logout_unavailable: ['退出服务暂不可用，不能确认已退出，请重新检查登录状态。', 'Sign-out is unavailable. Sign-out is not confirmed. Recheck your sign-in status.'],
    logout_result_unknown: ['退出结果暂时无法确认，请重新检查登录状态。', 'The sign-out outcome is unconfirmed. Recheck your sign-in status.'],
    auth_origin_rejected: ['当前页面无法完成此请求，请从平台入口重新打开。', 'This request cannot be completed from this page. Reopen the platform entry page.'],
    auth_body_too_large: ['输入内容过长，请检查邮箱和密码。', 'The input is too long. Check your email and password.'],
  };
  return t(...(labels[code] || labels.session_unavailable));
}
export function mountLogin(form, model, t = zh => zh) {
  const email = form.querySelector('[name=email]'), password = form.querySelector('[name=password]');
  const submit = form.querySelector('[data-login-submit]'), toggle = form.querySelector('[data-login-toggle]');
  const recheck = form.querySelector('[data-login-recheck]'), logout = form.querySelector('[data-login-logout]'), status = form.querySelector('[data-login-state]');
  const projects = form.querySelector('[data-login-projects]');
  let disposed = false, visible = false, serverEmail = null;
  function conceal(show = false) {
    visible = show; password.type = show ? 'text' : 'password'; toggle.setAttribute('aria-pressed', String(show));
    toggle.setAttribute('aria-label', show ? t('隐藏密码', 'Hide password') : t('显示密码', 'Show password'));
  }
  function paint() {
    if (disposed) return;
    const value = model.status(); form.setAttribute('aria-busy', String(value.busy));
    if (value.identity === 'authenticated' && emailValid(value.email)) {
      email.value = value.email; serverEmail = value.email; password.value = ''; conceal();
    } else if (serverEmail !== null) {
      if (email.value === serverEmail) email.value = '';
      serverEmail = null;
    }
    if (value.busy) conceal();
    for (const [key, field] of Object.entries({email, password})) {
      field.disabled = value.busy || value.identity === 'authenticated'; field.setAttribute('aria-invalid', String(Boolean(value.errors[key])));
      form.querySelector('[data-login-error="' + key + '"]').textContent = value.errors[key] ? text(value.errors[key], t) : '';
    }
    toggle.disabled = password.disabled; submit.disabled = !value.canLogin; submit.hidden = value.identity === 'authenticated';
    submit.textContent = value.phase === 'login' ? t('登录中…', 'Signing in…') : t('邮箱密码登录', 'Sign in with email');
    projects.hidden = !(value.phase === 'authenticated' && value.identity === 'authenticated' && value.ready === true && !value.busy && emailValid(value.email));
    projects.textContent = t('进入私人项目', 'Enter private projects');
    recheck.disabled = value.busy; logout.disabled = value.phase === 'logout';
    logout.hidden = !(value.identity === 'authenticated' || value.phase === 'login' || value.phase === 'logout');
    status.textContent = value.phase === 'checking' ? t('正在检查登录状态…', 'Checking sign-in status…')
      : value.phase === 'login' ? t('正在登录并确认本浏览器会话…', 'Signing in and confirming this browser session…')
      : value.phase === 'logout' ? t('正在退出并确认本浏览器会话状态…', 'Signing out and confirming this browser session…')
      : value.phase === 'authenticated' ? t('已登录，邮箱已验证。可进入私人项目，分析功能按模块逐步开放。开发会话最多保留 15 分钟，服务重启后失效。', 'Signed in with a verified email. Private projects can now be accessed; analysis features are enabled module by module. This development session lasts at most 15 minutes and ends on a server restart.')
      : value.phase === 'signed_out' ? t('已退出本浏览器的平台会话，其他设备不受影响。', 'Signed out of this browser’s platform session. Other devices are unaffected.')
      : value.phase === 'disabled' ? t('邮箱密码登录尚未启用，此页面不会提交或保存密码。', 'Password sign-in is not enabled. This page does not submit or save passwords.')
      : value.phase === 'invalid' ? (value.errors.form ? text(value.errors.form, t) : t('请检查标出的字段。', 'Please check the marked fields.'))
      : value.code ? text(value.code, t) : value.identity === 'anonymous'
        ? t('当前未登录。请输入邮箱和注册时设置的密码。', 'You are not signed in. Enter your email and the password you set when registering.')
        : t('登录状态尚未确认。', 'Sign-in status has not been confirmed.');
  }
  const clear = () => { conceal(); password.value = ''; };
  const onSubmit = event => { event.preventDefault(); if (disposed) return; let secret = password.value; clear(); void model.login({email: email.value, password: secret}); secret = ''; };
  const onToggle = () => { if (!disposed && !password.disabled) conceal(!visible); };
  const onEmail = () => { if (!disposed) model.edit('email', {email: email.value, password: password.value}); };
  const onPassword = () => { if (!disposed) model.edit('password', {email: email.value, password: password.value}); };
  const onRecheck = () => { if (!disposed) { clear(); void model.checkSession(); } };
  const onLogout = () => { if (!disposed) { clear(); void model.logout(); } };
  const bindings = [[form, 'submit', onSubmit], [toggle, 'click', onToggle], [email, 'input', onEmail], [password, 'input', onPassword], [recheck, 'click', onRecheck], [logout, 'click', onLogout]];
  for (const [node, event, handler] of bindings) node.addEventListener(event, handler);
  conceal(); const unsubscribe = model.subscribe(paint); paint(); void model.start();
  return () => { if (disposed) return; disposed = true; projects.hidden = true; clear(); unsubscribe(); for (const [node, event, handler] of bindings) node.removeEventListener(event, handler); };
}
