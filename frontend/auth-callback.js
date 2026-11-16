(() => {
  'use strict';
  let addressCleared = false;
  try {
    window.history.replaceState(null, '', '/auth/callback');
    addressCleared = true;
  } catch {}

  let disposed = false, initialized = false, request = null, removeLanguage = () => {};
  function cancel(value) {
    if (!value) return;
    clearTimeout(value.timer); value.controller.abort();
    try { void (value.reader ? value.reader.cancel() : value.response?.body?.cancel())?.catch(() => {}); } catch {}
  }
  function dispose() {
    if (disposed) return;
    disposed = true; cancel(request); request = null; removeLanguage();
    document.removeEventListener('DOMContentLoaded', initialize);
  }
  window.addEventListener('pagehide', dispose, {once: true});

  function initialize() {
    if (disposed || initialized) return;
    initialized = true;
    let english = false, state = addressCleared ? 'checking' : 'neutral';
    const title = document.getElementById('callback-title');
    const message = document.getElementById('callback-message');
    const login = document.getElementById('callback-login');
    const home = document.getElementById('callback-home');
    const brand = document.getElementById('callback-brand');
    const language = document.getElementById('callback-language');
    function paint() {
      if (disposed) return;
      document.documentElement.lang = english ? 'en' : 'zh-CN';
      document.title = english ? 'Cy_Omics_v2.0 · Email verification return' : 'Cy_Omics_v2.0 · 邮箱验证返回';
      title.textContent = state === 'verified' ? (english ? 'Email verified' : '邮箱已验证')
        : state === 'checking' ? (english ? 'Confirming email verification' : '正在确认邮箱验证')
        : english ? 'Unable to confirm the verification result' : '暂时无法确认验证结果';
      message.textContent = !addressCleared
        ? english ? 'This link cannot be processed right now. Please return to the sign-in page.' : '暂时无法继续处理此链接。请返回登录页面继续操作。'
        : state === 'verified'
          ? english ? 'Your email has been verified. Return to the sign-in page to continue.' : '邮箱已验证。请返回登录页继续。'
          : state === 'checking'
            ? english ? 'Checking the verification result. Please wait.' : '正在核对验证结果，请稍候。'
            : english ? 'This page cannot confirm email verification yet. Return to the sign-in page to continue.' : '此页面暂时无法确认邮箱验证结果。请返回登录页面继续操作。';
      message.setAttribute('aria-busy', String(state === 'checking'));
      login.textContent = english ? 'Return to sign in' : '返回登录';
      home.textContent = english ? 'Return home' : '返回首页';
      brand.setAttribute('aria-label', english ? 'Cy_Omics_v2.0 home' : 'Cy_Omics_v2.0 首页');
      language.textContent = english ? '中文' : 'EN';
      language.setAttribute('aria-label', english ? '切换为中文' : 'Switch to English');
    }
    const onLanguage = () => { english = !english; paint(); };
    language.addEventListener('click', onLanguage);
    removeLanguage = () => language.removeEventListener('click', onLanguage);
    paint();
    if (addressCleared) void checkConfirmation();

    async function checkConfirmation() {
      const current = {controller: new AbortController(), timer: null, reader: null, response: null}; request = current;
      const active = () => !disposed && request === current;
      const cancelled = new Promise((_, reject) => current.controller.signal.addEventListener('abort', () => reject(Error('unconfirmed')), {once: true}));
      void cancelled.catch(() => {});
      current.timer = setTimeout(() => cancel(current), 16000);
      try {
        const pending = fetch('/api/auth/confirmation', {method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: current.controller.signal});
        const arrived = Promise.resolve(pending).then(response => {
          current.response = response;
          if (!active() || current.controller.signal.aborted) { cancel(current); throw Error('unconfirmed'); }
          return response;
        });
        const response = await Promise.race([arrived, cancelled]);
        if (response.redirected || response.status !== 200 || !/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(response.headers.get('content-type') || '')) throw Error('unconfirmed');
        const length = response.headers.get('content-length');
        if (length !== null && (!/^\d+$/.test(length) || Number(length) > 4096)) throw Error('unconfirmed');
        const read = async () => {
          const reader = response.body?.getReader(); if (!reader) throw Error('unconfirmed'); current.reader = reader;
          const chunks = []; let size = 0;
          try {
            while (active() && !current.controller.signal.aborted) {
              const part = await reader.read(); if (!active() || current.controller.signal.aborted) throw Error('unconfirmed');
              if (part.done) break;
              size += part.value.byteLength; if (size > 4096) throw Error('unconfirmed'); chunks.push(part.value);
            }
            if (!active() || current.controller.signal.aborted) throw Error('unconfirmed');
            const bytes = new Uint8Array(size); let offset = 0;
            for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
            return JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes));
          } finally {
            try { void reader.cancel().catch(() => {}); } catch {}
            try { reader.releaseLock(); } catch {}
            if (current.reader === reader) current.reader = null;
          }
        };
        const result = await Promise.race([read(), cancelled]);
        if (!active() || current.controller.signal.aborted) return;
        if (!result || typeof result !== 'object' || Array.isArray(result) || Object.keys(result).length !== 2
          || !Object.hasOwn(result, 'status') || !Object.hasOwn(result, 'emailVerified')
          || !(result.status === 'verified' && result.emailVerified === true || result.status === 'unconfirmed' && result.emailVerified === false)) throw Error('unconfirmed');
        state = result.status === 'verified' ? 'verified' : 'neutral';
      } catch {
        if (active()) state = 'neutral';
      } finally {
        if (active()) { request = null; cancel(current); paint(); }
      }
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize, {once: true});
  else initialize();
})();
