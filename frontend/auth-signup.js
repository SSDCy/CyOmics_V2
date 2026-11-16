const bytes = value => new TextEncoder().encode(value).byteLength;
const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value)
  && [Object.prototype, null].includes(Object.getPrototypeOf(value));
const exact = (value, keys) => plain(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const errorStatus = {invalid_signup: 400, signup_origin_rejected: 403, unsupported_media_type: 415,
  signup_body_too_large: 413, signup_unavailable: 503, signup_rate_limited: 429, signup_result_unknown: 504};

export function validateSignup(email, password) {
  const errors = {};
  const address = typeof email === 'string' ? email.trim() : '';
  const parts = address.split('@'), local = parts[0], domain = parts[1];
  if (parts.length !== 2 || !local || bytes(address) > 254 || bytes(local) > 64
    || !/^[A-Za-z0-9.!#$%&'*+\/=?^_`{|}~-]+$/.test(local) || /^\.|\.$|\.\./.test(local)
    || !domain || !domain.includes('.') || !domain.split('.').every(label => /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/.test(label))) errors.email = 'email_format';
  if (typeof password !== 'string' || [...password].length < 12) errors.password = 'password_short';
  else if (bytes(password) > 72) errors.password = 'password_long';
  else if (/[\uD800-\uDFFF]/u.test(password)) errors.password = 'password_format';
  if (typeof email === 'string' && typeof password === 'string' && bytes(JSON.stringify({email: address, password})) > 4096) errors.form = 'signup_body_too_large';
  return errors;
}

export function createSignupTransport({enabled = false, fetchImpl = globalThis.fetch} = {}) {
  if (enabled !== true) return null;
  return (payload, {signal} = {}) => {
    if (!exact(payload, ['email', 'password']) || Object.keys(validateSignup(payload.email, payload.password)).length) throw Error('invalid_signup');
    return fetchImpl('/api/auth/signup', {
      method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal,
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({email: payload.email.trim(), password: payload.password}),
    });
  };
}

export function createSignupController({transport = null, setTimer = setTimeout, clearTimer = clearTimeout} = {}) {
  const listeners = new Set();
  let state = {phase: 'idle', code: null, errors: {}}, current = null, disposed = false;
  const active = request => !disposed && current === request;
  const emit = () => { for (const listener of listeners) listener(); };
  const update = (phase, code = null, errors = {}) => { state = {phase, code, errors}; emit(); };
  function cancel(request) {
    if (!request) return;
    clearTimer(request.timer); request.controller.abort();
    try { void (request.reader ? request.reader.cancel() : request.response?.body?.cancel())?.catch(() => {}); } catch {}
  }
  async function readResult(response, request) {
    if (!/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(response.headers?.get('content-type') || '')) throw Error('invalid_response');
    const length = response.headers.get('content-length');
    if (length !== null && (!/^\d+$/.test(length) || Number(length) > 4096)) throw Error('invalid_response');
    const reader = response.body?.getReader(); if (!reader) throw Error('invalid_response');
    request.reader = reader;
    const chunks = []; let size = 0;
    try {
      while (active(request)) {
        const part = await reader.read(); if (!active(request)) throw Error('cancelled');
        if (part.done) break;
        size += part.value.byteLength; if (size > 4096) throw Error('invalid_response'); chunks.push(part.value);
      }
      if (!active(request)) throw Error('cancelled');
      const data = new Uint8Array(size); let offset = 0;
      for (const chunk of chunks) { data.set(chunk, offset); offset += chunk.byteLength; }
      return JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(data));
    } finally {
      try { void reader.cancel().catch(() => {}); } catch {}
      try { reader.releaseLock(); } catch {}
      if (request.reader === reader) request.reader = null;
    }
  }
  async function submit({email, password, terms = false} = {}) {
    if (disposed || current || state.phase === 'pending') { password = ''; return false; }
    const errors = validateSignup(email, password);
    if (Object.keys(errors).length) { password = ''; update('invalid', null, errors); return false; }
    if (typeof transport !== 'function') { password = ''; update('disabled'); return false; }
    if (terms !== true) { password = ''; update('invalid', null, {terms: 'terms_required'}); return false; }
    const request = {controller: new AbortController(), timer: null, reader: null, response: null}; current = request;
    const cancelled = new Promise((_, reject) => request.controller.signal.addEventListener('abort', () => reject(Error('cancelled')), {once: true}));
    void cancelled.catch(() => {});
    request.timer = setTimer(() => { if (active(request)) { current = null; cancel(request); update('unknown', 'signup_result_unknown'); } }, 16000);
    update('submitting');
    try {
      // The isolated development candidate opts into the fixed same-origin transport.
      let result;
      try { result = transport({email: email.trim(), password}, {signal: request.controller.signal}); }
      finally { password = ''; email = ''; }
      const responseTask = Promise.resolve(result).then(response => {
        request.response = response;
        if (!active(request)) { cancel(request); throw Error('cancelled'); }
        return response;
      });
      const response = await Promise.race([responseTask, cancelled]);
      if (!active(request)) { cancel(request); return false; }
      if (response?.redirected) throw Error('invalid_response');
      if (response?.status === 504) { update('unknown', 'signup_result_unknown'); return false; }
      const body = await Promise.race([readResult(response, request), cancelled]);
      if (!active(request)) return false;
      if (response.status === 202 && exact(body, ['status']) && body.status === 'pending_confirmation') {
        update('pending'); return true;
      }
      if (exact(body, ['error']) && exact(body.error, ['code']) && Object.hasOwn(errorStatus, body.error.code)
        && response.status === errorStatus[body.error.code]) {
        update(body.error.code === 'signup_rate_limited' ? 'limited' : 'failed', body.error.code); return false;
      }
      throw Error('invalid_response');
    } catch {
      if (active(request)) update('failed', 'signup_unavailable'); return false;
    } finally {
      password = ''; email = '';
      if (active(request)) { current = null; cancel(request); emit(); }
    }
  }
  return {submit,
    edit(field, {email, password, terms = false}) {
      if (disposed || current || state.phase === 'pending' || !['email', 'password', 'terms'].includes(field)) return;
      const validation = validateSignup(email, password), errors = {...state.errors};
      const error = field === 'terms' ? (terms ? null : errors.terms) : validation[field];
      if (error) errors[field] = error; else delete errors[field];
      if (errors.form && !validation.form) delete errors.form;
      const local = ['idle', 'invalid', 'disabled'].includes(state.phase);
      update(local ? (Object.keys(errors).length ? 'invalid' : 'idle') : state.phase, local ? null : state.code, errors);
    },
    status() { return {...state, errors: {...state.errors}, busy: Boolean(current)}; },
    subscribe(listener) { if (disposed) return () => {}; listeners.add(listener); return () => listeners.delete(listener); },
    dispose() { if (disposed) return; disposed = true; const request = current; current = null; cancel(request); listeners.clear(); },
  };
}

export function createSignupRoutes(options = {}) {
  let route = null, controller = null;
  return {
    enter(nextRoute) {
      const next = nextRoute === '/register' ? nextRoute : null;
      if (next !== route) { controller?.dispose(); controller = next ? createSignupController(options) : null; route = next; }
      return controller;
    },
    current() { return controller; },
    dispose() { controller?.dispose(); route = null; controller = null; },
  };
}

function message(code, t) {
  const labels = {
    email_format: ['请输入有效邮箱。', 'Enter a valid email address.'],
    password_short: ['密码至少 12 个字符。', 'Use at least 12 characters.'],
    password_long: ['密码过长，请缩短。', 'The password is too long. Please shorten it.'],
    password_format: ['密码包含无法使用的字符，请重新输入。', 'The password contains an unsupported character. Please enter it again.'],
    terms_required: ['提交注册前，请自行确认是否同意服务条款与隐私政策。', 'Before submitting, decide whether to accept the Terms and Privacy Policy.'],
    invalid_signup: ['邮箱或密码不符合注册要求，请检查后重试。', 'Check that your email and password meet the registration requirements.'],
    signup_origin_rejected: ['当前页面无法提交注册，请从平台入口重新打开。', 'Registration cannot be submitted from this page. Reopen the platform entry page.'],
    unsupported_media_type: ['注册请求格式暂不受支持，请稍后重试。', 'The registration request format is unsupported. Please try later.'],
    signup_body_too_large: ['注册内容超出允许大小，请检查邮箱与密码长度。', 'Registration content is too large. Check the email and password lengths.'],
    signup_unavailable: ['暂时无法完成注册请求，请稍后检查邮箱或联系技术支持。', 'The registration request could not be completed. Check your email later or contact support.'],
    signup_rate_limited: ['请求过于频繁，请等待一段时间后再试；不会自动重发。', 'Too many requests. Wait before trying again. No automatic resend will occur.'],
    signup_result_unknown: ['注册请求结果暂时未知，请稍后检查邮箱或联系技术支持，避免重复提交。', 'The registration outcome is unknown. Check your email later or contact support; avoid duplicate submissions.'],
  };
  return t(...(labels[code] || labels.signup_unavailable));
}

export function mountSignup(form, model, t = zh => zh) {
  const email = form.querySelector('[name=email]'), password = form.querySelector('[name=password]'), terms = form.querySelector('[name=terms]');
  const button = form.querySelector('[type=submit]'), status = form.querySelector('[data-signup-state]');
  const toggle = form.querySelector('[data-signup-toggle]'), passwordHelp = form.querySelector('#signup-password-help');
  const fields = {email, password, terms}; let disposed = false, visible = false;
  function visibility(show) {
    visible = show; password.type = show ? 'text' : 'password';
    toggle?.setAttribute('aria-pressed', String(show));
    toggle?.setAttribute('aria-label', show ? t('隐藏密码', 'Hide password') : t('显示密码', 'Show password'));
  }
  visibility(false);
  function paint() {
    if (disposed) return;
    const value = model.status(); form.setAttribute('aria-busy', String(value.busy));
    if (value.busy) visibility(false);
    if (toggle) toggle.disabled = value.busy;
    for (const [key, field] of Object.entries(fields)) {
      field.disabled = value.busy;
      field.setAttribute('aria-invalid', String(Boolean(value.errors[key])));
      form.querySelector('[data-signup-error="' + key + '"]').textContent = value.errors[key] ? message(value.errors[key], t) : '';
    }
    if (passwordHelp) passwordHelp.textContent = value.errors.password ? '' : password.value
      ? (validateSignup(email.value, password.value).password ? '' : t('密码长度符合要求。', 'Password length meets the requirements.'))
      : t('密码至少 12 个字符；过长时请缩短。', 'Use at least 12 characters; shorten the password if it is too long.');
    button.disabled = value.busy || value.phase === 'pending';
    button.textContent = value.busy ? t('提交中…', 'Submitting…') : t('注册并验证邮箱', 'Register & verify email');
    status.textContent = value.phase === 'submitting' ? t('正在提交注册请求…', 'Submitting the registration request…')
      : value.phase === 'pending' ? t('若该邮箱可用于注册，将收到确认邮件，请检查邮箱。', 'If this email can be used to register, you will receive a confirmation email. Please check your inbox.')
      : value.phase === 'disabled' ? t('注册暂未开放。格式检查已通过，但未提交注册请求；密码已清空。', 'Registration is not open. Format checks passed, but no registration request was submitted. The password was cleared.')
      : value.phase === 'invalid' ? (value.errors.form ? message(value.errors.form, t) : t('请检查标出的字段。', 'Please check the marked fields.'))
      : value.code ? message(value.code, t) : '';
  }
  const onSubmit = event => {
    event.preventDefault();
    if (disposed) return;
    visibility(false);
    let secret = password.value; password.value = '';
    void model.submit({email: email.value, password: secret, terms: terms.checked}); secret = '';
  };
  const edit = field => { if (!disposed) model.edit(field, {email: email.value, password: password.value, terms: terms.checked}); };
  const onEmail = () => edit('email'), onPassword = () => edit('password'), onTerms = () => edit('terms');
  const onToggle = () => { if (!disposed && !model.status().busy) visibility(!visible); };
  email.addEventListener('input', onEmail); password.addEventListener('input', onPassword); terms.addEventListener('change', onTerms);
  toggle?.addEventListener('click', onToggle);
  form.addEventListener('submit', onSubmit); const unsubscribe = model.subscribe(paint); paint();
  return () => {
    if (disposed) return; disposed = true; visibility(false); password.value = ''; unsubscribe(); form.removeEventListener('submit', onSubmit);
    email.removeEventListener('input', onEmail); password.removeEventListener('input', onPassword); terms.removeEventListener('change', onTerms);
    toggle?.removeEventListener('click', onToggle);
  };
}
