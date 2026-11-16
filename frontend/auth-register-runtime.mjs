import { AsyncLocalStorage } from 'node:async_hooks';
import { createHash, randomBytes } from 'node:crypto';
import { createAuthSignupBridge } from './auth-signup-bridge.mjs';
import { createAnonKeyProvider, createRegistrationClient } from './auth-register-sdk.mjs';
import { createRegistrationDiagnostics, flowAgeClass } from './auth-register-diagnostics.mjs';

const origin = 'http://127.0.0.1:4206', host = '127.0.0.1:4206';
const signupPath = '/api/auth/signup', callbackPath = '/auth/callback', confirmationPath = '/api/auth/confirmation';
const browserCookie = 'cyomics_register_browser', receiptCookie = 'cyomics_register_receipt';
const lifetime = 900000, capacity = 256;
const opaque = value => typeof value === 'string' && /^[A-Za-z0-9_-]{43}$/.test(value);
const hash = value => createHash('sha256').update(value).digest('hex');
const headers = { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer' };
function single(req, name) {
  const value = req.headers?.[name];
  if (typeof value !== 'string') return undefined;
  if (req.rawHeaders !== undefined) {
    if (!Array.isArray(req.rawHeaders) || req.rawHeaders.length % 2) return undefined;
    let count = 0;
    for (let i = 0; i < req.rawHeaders.length; i += 2) {
      if (typeof req.rawHeaders[i] !== 'string') return undefined;
      if (req.rawHeaders[i].toLowerCase() === name) {
        if (req.rawHeaders[i + 1] !== value) return undefined;
        count++;
      }
    }
    if (count !== 1) return undefined;
  }
  return value;
}
function cookies(req) {
  if (req.headers?.cookie === undefined) return {};
  const wire = single(req, 'cookie');
  if (!wire || wire.length > 4096) return {};
  const values = {};
  for (const field of wire.split(';')) {
    const at = field.indexOf('='), name = field.slice(0, at).trim();
    if (![browserCookie, receiptCookie].includes(name)) continue;
    const value = field.slice(at + 1).trim();
    if (at < 1 || Object.hasOwn(values, name) || !opaque(value)) return {};
    values[name] = value;
  }
  return values;
}
function cookieObservation(req) {
  if (req.headers?.cookie === undefined) return { cookieHeaderValid: true, browserPresent: false,
    browserShapeValid: false, receiptPresent: false, receiptShapeValid: false };
  const wire = single(req, 'cookie');
  if (!wire || wire.length > 4096) return { cookieHeaderValid: false };
  const found = { [browserCookie]: [], [receiptCookie]: [] };
  for (const field of wire.split(';')) {
    const at = field.indexOf('='), name = (at < 0 ? field : field.slice(0, at)).trim();
    if (Object.hasOwn(found, name)) found[name].push(at < 0 ? undefined : field.slice(at + 1).trim());
  }
  const browser = found[browserCookie], receipt = found[receiptCookie];
  const valid = values => values.length === 1 && opaque(values[0]);
  return { cookieHeaderValid: [browser, receipt].every(values => !values.length || valid(values)),
    browserPresent: browser.length > 0, browserShapeValid: valid(browser),
    receiptPresent: receipt.length > 0, receiptShapeValid: valid(receipt) };
}
function callbackShape(raw) {
  if (raw === callbackPath) return 'clean';
  if (raw.length > 4096) return 'oversized';
  try {
    const url = new URL(raw, origin), keys = [...url.searchParams.keys()];
    if (url.hash) return 'invalid';
    if (new Set(keys).size !== keys.length) return 'duplicate';
    if (url.searchParams.has('error') || url.searchParams.has('error_code')) return 'error_query';
    if (keys.sort().join(',') === 'code,flow') return 'code_flow';
    return keys.some(key => key !== 'code' && key !== 'flow') ? 'unexpected' : 'missing';
  } catch { return 'invalid'; }
}
function observedFlowState(flow, observedTime) {
  return !flow ? 'absent' : flow.expiresAt <= observedTime ? 'expired' : flow.used ? 'used' : 'active';
}
function owns(raw, route) {
  try {
    const path = decodeURIComponent(new URL(raw, origin).pathname).replace(/\/{2,}/g, '/');
    return path === route || path.startsWith(route + '/') || raw.startsWith(route);
  } catch { return raw.startsWith(route); }
}
function setCookie(res, name, value, path, age = lifetime / 1000) {
  // This candidate is HTTP loopback only. No Domain, JS-readable session or product login cookie.
  const cookie = `${name}=${value}; Path=${path}; HttpOnly; SameSite=Lax; Max-Age=${age}`;
  const previous = res.getHeader('Set-Cookie');
  res.setHeader('Set-Cookie', [...(Array.isArray(previous) ? previous : previous ? [previous] : []), cookie]);
}
function reply(req, res, status, body, extra = {}) {
  if (res.destroyed || res.writableEnded) return;
  res.writeHead(status, { ...headers, 'Content-Type': 'application/json; charset=utf-8', ...extra });
  res.end(req.method === 'HEAD' || status === 303 ? undefined : JSON.stringify(body));
}

export function createAuthRegisterRuntime({ enabled = false, keyProvider = createAnonKeyProvider(),
  fetchImpl = globalThis.fetch, createClient = createRegistrationClient, now = Date.now,
  setTimer = setTimeout, clearTimer = clearTimeout,
  diagnosticsEnabled = false, diagnosticsSink,
} = {}) {
  const diagnostic = createRegistrationDiagnostics({ enabled: diagnosticsEnabled, sink: diagnosticsSink });
  const context = new AsyncLocalStorage(), flows = new Map(), receipts = new Map(), active = new Set();
  let disposed = false, lastTime = -Infinity;
  const closeFlow = flow => { flow.closed = true; delete flow.verifier; };
  const sweep = () => {
    const time = now();
    if (!Number.isFinite(time) || time < lastTime) {
      diagnostic({ stage: 'flow', result: 'clock_invalid', flowAge: 'unknown' });
      for (const flow of flows.values()) closeFlow(flow);
      flows.clear(); receipts.clear(); return false;
    }
    lastTime = time;
    for (const [id, flow] of flows) if (flow.expiresAt <= time) {
      diagnostic({ stage: 'flow', result: 'expired', flowAge: flowAgeClass(flow.expiresAt, time) });
      closeFlow(flow); flows.delete(id);
    }
    for (const [id, receipt] of receipts) if (receipt.expiresAt <= time) receipts.delete(id);
    return true;
  };
  const cleanup = enabled === true ? setInterval(sweep, 60000) : undefined;
  cleanup?.unref();
  const bridge = createAuthSignupBridge({ enabled: enabled === true, now, setTimer, clearTimer,
    createAuthClient: async ({ signal }) => {
      const request = context.getStore();
      if (!request || signal.aborted || disposed || !sweep() || flows.size >= capacity) throw Error('registration_unavailable');
      const flow = { id: randomBytes(16).toString('base64url'), browserHash: request.browserHash,
        expiresAt: now() + lifetime, closed: false, used: false };
      flows.set(flow.id, flow);
      diagnostic({ stage: 'flow', result: 'created', flowAge: 'under_1m' });
      let client;
      try {
        client = await createClient({ flow, signal, keyProvider, fetchImpl, now, diagnostics: diagnostic });
        if (signal.aborted || disposed) throw Error('registration_unavailable');
      } catch {
        diagnostic({ stage: 'flow', result: 'factory_failed' });
        closeFlow(flow); flows.delete(flow.id); await client?.dispose?.(); throw Error('registration_unavailable');
      }
      // The reviewed bridge owns request validation, rate limits and its complete 15s deadline.
      return { signUp: async input => {
        flow.email = input.email;
        try { return await client.signUp(input); }
        finally { await client.dispose?.(); }
      } };
    },
  });
  const runtime = async (req, res) => {
    if (typeof req.url !== 'string') return false;
    const isSignup = owns(req.url, signupPath), isCallback = owns(req.url, callbackPath);
    const isConfirmation = owns(req.url, confirmationPath);
    if (isCallback && (req.aborted || res.destroyed || res.writableEnded)) {
      diagnostic({ stage: 'callback', result: 'rejected', reason: 'disconnected' }); return true;
    }
    const sameHost = single(req, 'host') === host;
    const sameOrigin = req.headers?.origin === undefined || single(req, 'origin') === origin;
    // Observe only a bounded known parameter shape. No identifier enters a diagnostic record.
    const shape = diagnostic.enabled && isCallback ? callbackShape(req.url) : undefined;
    const beforeSweep = shape === 'code_flow' ? flows.get(new URL(req.url, origin).searchParams.get('flow')) : undefined;
    const valid = enabled === true && !disposed && sweep();
    const jar = cookies(req), browser = jar[browserCookie];
    const observedCookies = diagnostic.enabled ? cookieObservation(req) : {};
    const callbackDiagnostic = (result, extra = {}) => diagnostic({ stage: 'callback', result, shape,
      hostMatches: sameHost, originMatches: sameOrigin, ...observedCookies,
      queryErrorUntrusted: shape === 'error_query', ...extra });

    // A browser receives its opaque binding while loading the ordinary root document.
    // The POST never invents a new binding after a possibly delivered signup request.
    if (req.url === '/' && req.method === 'GET' && sameHost && sameOrigin && valid) {
      setCookie(res, browserCookie, browser || randomBytes(32).toString('base64url'), '/');
      diagnostic({ stage: 'bootstrap', result: browser ? 'reused' : 'issued', ...observedCookies });
      return false;
    }
    if (isSignup) {
      if (req.url === signupPath && req.method === 'POST' && sameHost && single(req, 'origin') === origin && valid) {
        if (!browser) { reply(req, res, 409, { error: { code: 'registration_context_required' } }); return true; }
        setCookie(res, browserCookie, browser, '/');
        return context.run({ browserHash: hash(browser) }, () => bridge(req, res));
      }
      return bridge(req, res);
    }
    if (!isCallback && !isConfirmation) return false;
    if (isConfirmation) {
      const observed = { stage: 'confirmation', hostMatches: sameHost, originMatches: sameOrigin, ...observedCookies };
      if (req.url !== confirmationPath) {
        reply(req, res, 404, { error: { code: 'not_found' } }); diagnostic({ ...observed, result: 'not_found' });
      } else if (req.method !== 'GET') {
        reply(req, res, 405, { error: { code: 'method_not_allowed' } }, { Allow: 'GET' });
        diagnostic({ ...observed, result: 'method_rejected' });
      } else if (!sameHost || !sameOrigin) {
        reply(req, res, 403, { error: { code: 'confirmation_origin_rejected' } });
        diagnostic({ ...observed, result: 'origin_rejected' });
      } else if (!valid) {
        reply(req, res, 503, { error: { code: 'confirmation_unavailable' } }); diagnostic({ ...observed, result: 'unavailable' });
      }
      else {
        const receipt = receipts.get(jar[receiptCookie]);
        let checkedAt;
        const verified = !!browser && !!receipt && receipt.browserHash === hash(browser) && receipt.expiresAt > (checkedAt = now());
        reply(req, res, 200, { status: verified ? 'verified' : 'unconfirmed', emailVerified: verified });
        const receiptState = !jar[receiptCookie] ? observedCookies.receiptPresent ? 'invalid' : 'absent'
          : !receipt ? 'absent' : receipt.expiresAt <= (checkedAt ?? lastTime) ? 'expired' : !browser || receipt.browserHash !== hash(browser) ? 'mismatch' : 'matched';
        diagnostic({ ...observed, result: verified ? 'verified' : 'unconfirmed', receiptState, receiptMatches: verified });
      }
      return true;
    }
    // The clean callback document is served normally and makes one GET confirmation request.
    if (req.url === callbackPath && req.method === 'GET') return false;
    if (!req.url.startsWith(callbackPath + '?')) {
      reply(req, res, 404, { error: { code: 'not_found' } }); callbackDiagnostic('rejected', { reason: 'path' }); return true;
    }
    if (req.method !== 'GET') {
      reply(req, res, 405, { error: { code: 'method_not_allowed' } }, { Allow: 'GET' });
      callbackDiagnostic('rejected', { reason: 'method' }); return true;
    }
    const redirect = receipt => {
      if (res.destroyed || res.writableEnded) return;
      setCookie(res, receiptCookie, receipt || '', confirmationPath, receipt ? lifetime / 1000 : 0);
      reply(req, res, 303, undefined, { Location: callbackPath });
      diagnostic({ stage: 'receipt', result: receipt ? 'issued' : 'cleared', receiptIssued: !!receipt });
    };
    if (!valid || !sameHost || !sameOrigin || !browser || req.url.length > 4096) {
      callbackDiagnostic('rejected', { reason: !valid ? 'disabled' : !sameHost || !sameOrigin ? 'origin' : !browser ? 'cookie' : 'parameters' });
      redirect(); return true;
    }
    let flow, code, checkedAt, lookupAttempted = false;
    try {
      const url = new URL(req.url, origin);
      if ([...url.searchParams.keys()].sort().join(',') !== 'code,flow' || url.hash) throw Error();
      const id = url.searchParams.get('flow'); code = url.searchParams.get('code');
      if (!/^[A-Za-z0-9_-]{22}$/.test(id) || !/^[A-Za-z0-9._~-]{1,512}$/.test(code)) throw Error();
      lookupAttempted = true; flow = flows.get(id);
      if (!flow || flow.browserHash !== hash(browser) || flow.expiresAt <= (checkedAt = now())) throw Error();
    } catch {
      const observed = flow || beforeSweep, observedAt = checkedAt ?? lastTime;
      callbackDiagnostic('rejected', { reason: lookupAttempted && observed?.expiresAt <= observedAt ? 'expired_flow'
        : flow && flow.browserHash !== hash(browser) ? 'binding' : lookupAttempted && !flow && !beforeSweep ? 'missing_flow' : 'parameters',
        flowState: lookupAttempted ? observedFlowState(observed, observedAt) : 'unknown',
        flowAge: lookupAttempted ? flowAgeClass(observed?.expiresAt, observedAt) : 'unknown',
        ...(lookupAttempted && observed ? { browserMatches: observed.browserHash === hash(browser) } : {}) });
      redirect(); return true;
    }
    if (flow.used) {
      callbackDiagnostic('replay', { flowState: 'used', flowAge: flowAgeClass(flow.expiresAt, lastTime), browserMatches: true });
      redirect(receipts.has(flow.receiptId) ? flow.receiptId : undefined); return true;
    }
    callbackDiagnostic('eligible', { flowState: 'active', flowAge: flowAgeClass(flow.expiresAt, lastTime),
      browserMatches: true, verifierPresent: typeof flow.verifier === 'string' });
    flow.used = true;
    const controller = new AbortController(); active.add(controller);
    const abort = () => {
      if (!controller.signal.aborted) callbackDiagnostic('cancelled', { reason: 'deadline_or_disconnect' });
      controller.abort();
    };
    req.once('aborted', abort); res.once('close', abort);
    let client, timer, receiptId;
    const live = () => !disposed && !controller.signal.aborted && !flow.closed && now() < flow.expiresAt;
    const cancelled = new Promise(resolve => controller.signal.addEventListener('abort', () => resolve(false), { once: true }));
    const operation = async () => {
      try {
        const created = await createClient({ flow, signal: controller.signal, keyProvider, fetchImpl, now, diagnostics: diagnostic });
        if (!live()) { await created?.dispose?.(); return false; }
        client = created;
        const verified = await client.confirm(code);
        return live() && verified === true;
      } catch { callbackDiagnostic('client_failed', { reason: client ? 'not_verified' : 'factory' }); return false; }
    };
    try {
      timer = setTimer(abort, 15000);
      const verified = await Promise.race([operation(), cancelled]);
      if (verified && live() && sweep() && receipts.size < capacity) {
        receiptId = randomBytes(32).toString('base64url');
        receipts.set(receiptId, { browserHash: flow.browserHash, expiresAt: now() + lifetime });
        flow.receiptId = receiptId;
      }
    } finally {
      clearTimer(timer); controller.abort(); active.delete(controller); closeFlow(flow);
      req.removeListener('aborted', abort); res.removeListener('close', abort);
      // Cleanup cannot delay the clean redirect or expose a late exchange result.
      void Promise.resolve(client?.dispose?.()).catch(() => {});
    }
    callbackDiagnostic(receiptId ? 'verified' : 'not_verified', { receiptIssued: !!receiptId });
    redirect(receiptId); return true;
  };
  runtime.dispose = () => {
    diagnostic.dispose();
    disposed = true; bridge.dispose(); clearInterval(cleanup);
    for (const controller of active) controller.abort();
    for (const flow of flows.values()) closeFlow(flow);
    flows.clear(); receipts.clear();
  };
  return runtime;
}
