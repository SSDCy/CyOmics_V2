import { AuthClient } from '@supabase/auth-js';

// Only constant classifications cross the adapter boundary, never SDK exceptions.
export class LoginFailure extends Error {
  constructor(kind = 'unavailable') { super(kind); this.kind = kind; }
}
const fail = kind => { throw new LoginFailure(kind); };
const check = condition => { if (!condition) fail('unavailable'); };
const validKey = value => typeof value === 'string' && /^[\x21-\x7e]{1,4096}$/.test(value);
const validToken = value => typeof value === 'string' && /^[A-Za-z0-9._-]{1,8192}$/.test(value);
const credentialCodes = new Set(['invalid_credentials', 'email_not_confirmed', 'user_not_found', 'user_banned', 'bad_jwt', 'session_not_found', 'session_expired']);
function identity(user) {
  if (!user || typeof user.id !== 'string' || !/^[\x21-\x7e]{1,128}$/.test(user.id)
      || typeof user.email !== 'string' || user.email !== user.email.trim() || Buffer.byteLength(user.email) > 254
      || !user.email.includes('@') || /[\x00-\x20\x7f]/.test(user.email)
      || typeof user.email_confirmed_at !== 'string' || user.email_confirmed_at.length > 64
      || !Number.isFinite(Date.parse(user.email_confirmed_at))) fail('credentials');
  return { userId: user.id, email: user.email };
}

export async function createLoginClient({ signal, deadline, keyProvider, fetchImpl = globalThis.fetch, now = Date.now }) {
  let closed = false, attempted = false, allowedAccess, transportFailed = false;
  const live = () => !closed && !signal.aborted && Number.isFinite(now()) && now() < deadline;
  check(live());
  const anon = await keyProvider(signal); check(live() && validKey(anon));
  const unavailable = () => Response.json({ code: 'unexpected_failure', msg: 'Authentication unavailable' }, { status: 503 });
  const safeFetch = async (input, options = {}) => {
    let response, reader;
    try {
      check(live());
      const url = new URL(typeof input === 'string' ? input : input.url);
      const method = options.method || 'GET';
      const login = method === 'POST' && url.pathname === '/auth/v1/token' && url.search === '?grant_type=password';
      const user = method === 'GET' && url.pathname === '/auth/v1/user' && !url.search;
      check(url.origin === 'http://127.0.0.1:57421' && !url.username && !url.password && !url.hash && (login || user));
      const headers = { Accept: 'application/json', apikey: anon };
      if (login) {
        check(typeof options.body === 'string' && Buffer.byteLength(options.body) <= 8192);
        headers['Content-Type'] = 'application/json';
      } else {
        check(validToken(allowedAccess) && new Headers(options.headers).get('authorization') === 'Bearer ' + allowedAccess);
        headers.Authorization = 'Bearer ' + allowedAccess;
      }
      response = await fetchImpl(url.toString(), { method, headers, ...(login ? { body: options.body } : {}),
        signal, redirect: 'error', credentials: 'omit', cache: 'no-store' });
      check(live() && response.redirected !== true && response.status >= 200 && response.status <= 599
        && (response.status < 300 || response.status >= 400)
        && /^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type') || '') && response.body);
      reader = response.body.getReader(); const chunks = []; let bytes = 0;
      while (true) {
        const { done, value } = await reader.read(); check(live());
        if (done) break;
        bytes += value.byteLength; check(bytes <= 32768); chunks.push(value);
      }
      const data = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks)));
      if (response.status >= 400) {
        const record = data !== null && typeof data === 'object' && !Array.isArray(data);
        const hasCode = record && Object.hasOwn(data, 'code');
        const hasErrorCode = record && Object.hasOwn(data, 'error_code');
        // Accept recognized string codes, matching dual strings, or a legacy
        // numeric HTTP status plus error_code. Conflicts/malformed fields fail closed.
        const credentialShape = record && (
          hasCode && credentialCodes.has(data.code) && (!hasErrorCode || data.error_code === data.code)
          || hasErrorCode && credentialCodes.has(data.error_code)
            && (!hasCode || Number.isInteger(data.code) && data.code === response.status)
        );
        const kind = response.status === 429 ? 'limited'
          : response.status === 401 || response.status === 403 || response.status < 500 && credentialShape ? 'credentials' : 'unavailable';
        // Returning valid, sanitized JSON also prevents the SDK's fetch-error logger seeing raw errors.
        const code = kind === 'credentials' ? 'invalid_credentials' : kind === 'limited' ? 'over_request_rate_limit' : 'unexpected_failure';
        // auth-js 2.86 reads error_code when no upstream API-version header is forwarded.
        return Response.json({ code, error_code: code, msg: 'Authentication unavailable' }, { status: response.status });
      }
      return Response.json(data, { status: response.status });
    } catch {
      transportFailed = true; return unavailable();
    } finally {
      if (reader) void reader.cancel().catch(() => {});
      else if (response?.body) void response.body.cancel().catch(() => {});
    }
  };
  // The SDK is allowed to save into this deliberately forgetful store. No session/refresh token is retained.
  const storage = { getItem: async () => null, setItem: async () => {}, removeItem: async () => {} };
  const client = new AuthClient({ url: 'http://127.0.0.1:57421/auth/v1', headers: { apikey: anon }, fetch: safeFetch,
    storage, storageKey: 'cyomics-login-ephemeral', persistSession: true,
    autoRefreshToken: false, detectSessionInUrl: false, debug: false });
  function result(value) {
    check(live() && !transportFailed);
    if (value?.error !== null) {
      const code = value?.error?.code;
      fail(credentialCodes.has(code) ? 'credentials' : code === 'over_request_rate_limit' ? 'limited' : 'unavailable');
    }
    return value.data;
  }
  const current = async access => {
    check(live() && validToken(access)); allowedAccess = access;
    return identity(result(await client.getUser(access))?.user);
  };
  return {
    async login({ email, password }) {
      check(live() && !attempted); attempted = true;
      const started = now();
      const data = result(await client.signInWithPassword({ email, password }));
      const session = data?.session, signed = data?.user;
      check(session && validToken(session.access_token) && Number.isFinite(session.expires_in) && session.expires_in > 0
        && Number.isFinite(session.expires_at) && session.expires_at * 1000 > now());
      const expiresAt = Math.min(started + 900000, started + session.expires_in * 1000, session.expires_at * 1000);
      const who = await current(session.access_token);
      if (who.userId !== signed?.id || typeof signed?.email !== 'string'
          || who.email.toLowerCase() !== email.toLowerCase() || who.email.toLowerCase() !== signed.email.toLowerCase()) fail('credentials');
      check(live() && Number.isFinite(expiresAt) && expiresAt - now() >= 1000);
      return { ...who, accessToken: session.access_token, expiresAt };
    },
    async current(session) {
      const who = await current(session.accessToken);
      if (who.userId !== session.userId || who.email.toLowerCase() !== session.email.toLowerCase()) fail('credentials');
      return who;
    },
    async dispose() { closed = true; allowedAccess = undefined; await client.stopAutoRefresh(); },
  };
}
