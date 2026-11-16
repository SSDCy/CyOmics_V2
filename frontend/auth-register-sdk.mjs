import { AuthClient } from '@supabase/auth-js';
import { constants } from 'node:fs';
import { lstat, open } from 'node:fs/promises';
import { parseEnv } from 'node:util';
import { registrationDiagnostics, registrationErrorCode, elapsedClass } from './auth-register-diagnostics.mjs';

const authUrl = 'http://127.0.0.1:57421/auth/v1';
const callback = 'http://127.0.0.1:4206/auth/callback';
const keyPath = '/home/user01/Cy_Omics_v2.0/auth-dev/.env.keys';
const keyLimit = 16 * 1024;
const validKey = key => typeof key === 'string' && key.length > 0 && key.length <= 4096 && /^[\x21-\x7e]+$/.test(key);
const check = condition => { if (!condition) throw new Error('registration_unavailable'); };

// Construction does not read a file. Only the explicitly enabled runtime calls this provider.
export function createAnonKeyProvider() {
  let cached;
  return async signal => {
    check(!signal.aborted);
    if (cached !== undefined) return cached;
    for (const directory of ['/home', '/home/user01', '/home/user01/Cy_Omics_v2.0', '/home/user01/Cy_Omics_v2.0/auth-dev']) {
      const info = await lstat(directory);
      check(!signal.aborted && info.isDirectory() && !info.isSymbolicLink());
    }
    const file = await open(keyPath, constants.O_RDONLY | constants.O_NOFOLLOW);
    try {
      const before = await file.stat();
      check(!signal.aborted && before.isFile() && before.nlink === 1
        && typeof process.getuid === 'function' && before.uid === process.getuid()
        && (before.mode & 0o777) === 0o600 && before.size <= keyLimit);
      const decoder = new TextDecoder('utf-8', { fatal: true });
      const buffer = Buffer.alloc(1024);
      let offset = 0, pending = '', anonLine;
      const line = value => {
        value = value.replace(/\r$/, '');
        if (!/^\s*(?:export\s+)?ANON_KEY(?:\s|=|$)/.test(value)) return;
        check(anonLine === undefined && /^\s*(?:export\s+)?ANON_KEY\s*=\s*(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s#'"\r\n]+)\s*(?:#.*)?$/.test(value));
        anonLine = value;
      };
      while (offset < before.size) {
        check(!signal.aborted);
        const { bytesRead } = await file.read(buffer, 0, Math.min(buffer.length, before.size - offset), offset);
        check(!signal.aborted && bytesRead > 0);
        offset += bytesRead;
        pending += decoder.decode(buffer.subarray(0, bytesRead), { stream: true });
        let newline;
        while ((newline = pending.indexOf('\n')) !== -1) {
          line(pending.slice(0, newline)); pending = pending.slice(newline + 1);
        }
      }
      pending += decoder.decode(); line(pending);
      const after = await file.stat();
      check(!signal.aborted && ['dev', 'ino', 'size', 'mtimeMs', 'ctimeMs', 'uid', 'mode', 'nlink'].every(k => before[k] === after[k]));
      check(anonLine !== undefined);
      const parsed = parseEnv(anonLine);
      check(Object.keys(parsed).length === 1 && validKey(parsed.ANON_KEY));
      cached = parsed.ANON_KEY; return cached;
    } finally { await file.close(); }
  };
}

// The SDK creates PKCE values. This adapter stores only that flow's verifier, never its session.
export async function createRegistrationClient({ flow, signal, keyProvider, fetchImpl = globalThis.fetch, now = Date.now, diagnostics }) {
  const diagnostic = registrationDiagnostics(diagnostics);
  check(/^[A-Za-z0-9_-]{22}$/.test(flow.id));
  const live = () => !signal.aborted && !flow.closed && now() < flow.expiresAt;
  check(live());
  const anon = await keyProvider(signal); check(live() && validKey(anon));
  const storageKey = 'cyomics-register-' + flow.id;
  const verifierKey = storageKey + '-code-verifier';
  const redirectTo = callback + '?flow=' + flow.id;
  let transportUnknown = false;
  const diagnosticErrors = { signup: 'none', exchange: 'none', get_user: 'none' };
  const unavailable = () => Response.json({ code: 'unexpected_failure', msg: 'Authentication request unavailable' }, { status: 503 });
  const safeFetch = async (input, options = {}) => {
    let response, reader, operation = 'unknown';
    const started = performance.now();
    const httpClass = () => !response ? 'none' : response.status >= 200 && response.status < 300 ? 'success'
      : response.status >= 400 && response.status < 500 ? 'client_error' : response.status >= 500 ? 'server_error'
      : response.status >= 300 && response.status < 400 ? 'redirect' : 'invalid';
    try {
      check(live());
      const url = new URL(typeof input === 'string' ? input : input.url);
      const method = options.method || 'GET';
      const signup = method === 'POST' && url.pathname === '/auth/v1/signup'
        && [...url.searchParams.keys()].join(',') === 'redirect_to' && url.searchParams.get('redirect_to') === redirectTo;
      const exchange = method === 'POST' && url.pathname === '/auth/v1/token' && url.search === '?grant_type=pkce';
      const user = method === 'GET' && url.pathname === '/auth/v1/user' && !url.search;
      check(url.origin === 'http://127.0.0.1:57421' && !url.username && !url.password && !url.hash && (signup || exchange || user));
      const headers = { Accept: 'application/json', apikey: anon };
      if (method === 'POST') { check(typeof options.body === 'string' && Buffer.byteLength(options.body) <= 8192); headers['Content-Type'] = 'application/json'; }
      if (user) {
        const authorization = new Headers(options.headers).get('authorization');
        check(typeof authorization === 'string' && authorization.length <= 8192 && /^Bearer [A-Za-z0-9._-]+$/.test(authorization));
        headers.Authorization = authorization;
      }
      operation = signup ? 'signup' : exchange ? 'exchange' : 'get_user';
      diagnostic({ stage: 'transport', result: 'attempted', operation });
      response = await fetchImpl(url.toString(), { method, headers, ...(method === 'POST' ? { body: options.body } : {}),
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
      if (response.status >= 400) diagnosticErrors[operation] = registrationErrorCode(data);
      diagnostic({ stage: 'transport', result: 'received', operation, httpClass: httpClass(),
        errorCode: response.status >= 400 ? registrationErrorCode(data) : 'none', elapsed: elapsedClass(started) });
      if (response.status >= 400) {
        const codes = ['user_already_exists', 'weak_password', 'email_address_invalid', 'validation_failed',
          'over_request_rate_limit', 'over_email_send_rate_limit'];
        const code = codes.includes(data?.code) ? data.code : 'unexpected_failure';
        return Response.json({ code, msg: 'Authentication request unavailable' }, { status: response.status });
      }
      return Response.json(data, { status: response.status });
    } catch {
      diagnostic({ stage: 'transport', result: 'failed', operation, httpClass: httpClass(), elapsed: elapsedClass(started) });
      transportUnknown = true; return unavailable();
    } finally {
      if (reader) void reader.cancel().catch(() => {});
      else if (response?.body) void response.body.cancel().catch(() => {});
    }
  };
  const storage = {
    getItem: async key => key === verifierKey && !flow.closed ? flow.verifier ?? null : null,
    setItem: async (key, value) => {
      if (key === verifierKey && live()) { check(typeof value === 'string' && value.length <= 1024); flow.verifier = value; }
    },
    // Retain the verifier on an unknown outcome; the runtime releases it on success/expiry.
    removeItem: async () => {},
  };
  const client = new AuthClient({ url: authUrl, headers: { apikey: anon }, fetch: safeFetch, storage, storageKey,
    flowType: 'pkce', persistSession: true, autoRefreshToken: false, detectSessionInUrl: false, debug: false });
  return {
    async signUp(input) {
      check(live());
      diagnostic({ stage: 'signup', result: 'attempted' });
      try {
        const result = await client.signUp({ email: input.email, password: input.password, options: { emailRedirectTo: redirectTo } });
        if (!live() || transportUnknown) throw new Error('signup_result_unknown');
        diagnostic({ stage: 'signup', result: result.error === null ? 'succeeded' : 'failed',
          errorCode: result.error === null ? 'none' : registrationErrorCode(result.error) });
        return result;
      } catch (error) { diagnostic({ stage: 'signup', result: 'failed' }); throw error; }
    },
    async confirm(code) {
      check(live() && typeof flow.verifier === 'string');
      let exchanged, access;
      const exchangeStarted = performance.now();
      diagnostic({ stage: 'exchange', result: 'attempted' });
      try {
        exchanged = await client.exchangeCodeForSession(code);
        check(live() && !transportUnknown && exchanged.error === null);
        access = exchanged.data?.session?.access_token;
        check(typeof access === 'string');
        diagnostic({ stage: 'exchange', result: 'succeeded', errorCode: 'none', elapsed: elapsedClass(exchangeStarted) });
      } catch (error) {
        diagnostic({ stage: 'exchange', result: signal.aborted ? 'aborted' : 'failed',
          errorCode: diagnosticErrors.exchange === 'none' ? registrationErrorCode(exchanged?.error) : diagnosticErrors.exchange,
          elapsed: elapsedClass(exchangeStarted) }); throw error;
      }
      let current;
      const userStarted = performance.now();
      diagnostic({ stage: 'get_user', result: 'attempted' });
      try {
        current = await client.getUser(access); check(live() && !transportUnknown && current.error === null);
      } catch (error) {
        diagnostic({ stage: 'get_user', result: signal.aborted ? 'aborted' : 'failed',
          errorCode: diagnosticErrors.get_user === 'none' ? registrationErrorCode(current?.error) : diagnosticErrors.get_user,
          elapsed: elapsedClass(userStarted) }); throw error;
      }
      const user = current.data?.user;
      diagnostic({ stage: 'get_user', result: 'succeeded', errorCode: 'none', elapsed: elapsedClass(userStarted),
        emailMatches: !!user && typeof user.email === 'string' && user.email.toLowerCase() === flow.email.toLowerCase(),
        emailConfirmed: !!user && typeof user.email_confirmed_at === 'string' && Number.isFinite(Date.parse(user.email_confirmed_at)) });
      return !!user && typeof user.email === 'string' && user.email.toLowerCase() === flow.email.toLowerCase()
        && typeof user.email_confirmed_at === 'string' && Number.isFinite(Date.parse(user.email_confirmed_at));
    },
    async dispose() { await client.stopAutoRefresh(); },
  };
}
