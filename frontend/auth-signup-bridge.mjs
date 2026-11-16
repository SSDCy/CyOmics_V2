import { createHash } from 'node:crypto';
import { isIP } from 'node:net';
import { domainToASCII } from 'node:url';

const route = '/api/auth/signup';
const origin = 'http://127.0.0.1:4206';
const host = '127.0.0.1:4206';
const callback = origin + '/auth/callback';
// All errors are local constants; upstream messages and account objects are never serialized.
const errors = Object.freeze({
  not_found: [404, 'not_found'],
  method: [405, 'method_not_allowed'],
  origin: [403, 'signup_origin_rejected'],
  media: [415, 'unsupported_media_type'],
  large: [413, 'signup_body_too_large'],
  invalid: [400, 'invalid_signup'],
  unavailable: [503, 'signup_unavailable'],
  limited: [429, 'signup_rate_limited'],
  unknown: [504, 'signup_result_unknown'],
});
const duplicateCodes = new Set(['user_already_exists']);
const invalidCodes = new Set(['weak_password', 'email_address_invalid', 'validation_failed']);
const limitedCodes = new Set(['over_request_rate_limit', 'over_email_send_rate_limit']);
const bodyLimit = 4096, deadlineMs = 15000, emailCooldownMs = 60000, sourceWindowMs = 600000;
const capacity = 256; // Each of the two TTL maps has this hard cap.
class Failure extends Error {
  constructor(kind) { super(kind); this.kind = kind; }
}
const fail = kind => { throw new Failure(kind); };
const plain = value => value !== null && typeof value === 'object'
  && [Object.prototype, null].includes(Object.getPrototypeOf(value));

function ownsRoute(raw) {
  try {
    const path = decodeURIComponent(new URL(raw, origin).pathname).replace(/\/{2,}/g, '/');
    return path === route || path.startsWith(route + '/') || raw.startsWith(route);
  } catch { return raw.startsWith(route); }
}
function singleHeader(req, name) {
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
function parseInput(bytes) {
  let text, value;
  try {
    text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    value = JSON.parse(text);
  } catch { fail('invalid'); }
  if (!plain(value) || Object.keys(value).sort().join(',') !== 'email,password'
      || typeof value.email !== 'string' || typeof value.password !== 'string') fail('invalid');
  // Count the original members too: JSON.parse alone collapses duplicate keys.
  const skeleton = text.replace(/"(?:\\[\s\S]|[^"\\])*"/g, '""');
  if (!/^\s*\{\s*""\s*:\s*""\s*,\s*""\s*:\s*""\s*\}\s*$/.test(skeleton)) fail('invalid');
  const email = value.email.trim();
  const parts = email.split('@'), local = parts[0], domain = parts.length === 2 ? domainToASCII(parts[1]) : '';
  if (Buffer.byteLength(email) > 254 || !local || local.length > 64
      || !/^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+$/.test(local)
      || local.startsWith('.') || local.endsWith('.') || local.includes('..')
      || !domain || domain.length > 253 || domain.split('.').length < 2
      || !domain.split('.').every(label => /^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$/.test(label))) fail('invalid');
  const password = value.password;
  if (Buffer.from(password).toString('utf8') !== password || [...password].length < 12
      || Buffer.byteLength(password, 'utf8') > 72) fail('invalid');
  return { email, password };
}
function readBody(req, signal) {
  return new Promise((resolve, reject) => {
    const chunks = []; let bytes = 0, settled = false;
    const cleanup = () => {
      req.removeListener('data', data); req.removeListener('end', end);
      signal.removeEventListener('abort', aborted);
    };
    const stop = failure => {
      if (settled) return;
      settled = true; cleanup(); req.pause?.(); reject(failure);
    };
    const data = chunk => {
      if (!(chunk instanceof Uint8Array)) return stop(new Failure('invalid'));
      bytes += chunk.byteLength;
      if (bytes > bodyLimit) return stop(new Failure('large'));
      chunks.push(chunk);
    };
    const end = () => { settled = true; cleanup(); resolve(Buffer.concat(chunks)); };
    const error = () => stop(new Failure('invalid'));
    const aborted = () => stop(new Failure('unknown'));
    if (signal.aborted) return aborted();
    // A later socket error after cancellation is ignored until the request closes.
    req.on('data', data); req.once('end', end); req.on('error', error);
    req.once('close', () => req.removeListener('error', error));
    signal.addEventListener('abort', aborted, { once: true });
  });
}
function projectResult(result, email) {
  if (!plain(result) || !Object.hasOwn(result, 'error') || !plain(result.data)
      || result.data.session !== null) fail('unavailable');
  const user = result.data.user;
  for (const object of [result, result.data, user]) {
    if (object && ['session', 'access_token', 'refresh_token', 'token', 'id_token'].some(key => object[key] != null)) fail('unavailable');
  }
  if (user !== null) {
    if (!plain(user) || typeof user.id !== 'string' || !user.id || user.id.length > 128
        || typeof user.email !== 'string' || user.email.toLowerCase() !== email.toLowerCase()
        || user.email_confirmed_at != null || user.confirmed_at != null || user.phone_confirmed_at != null) fail('unavailable');
  }
  if (result.error !== null) {
    const code = result.error?.code;
    if (typeof code === 'string' && duplicateCodes.has(code)) return;
    if (typeof code === 'string' && invalidCodes.has(code)) fail('invalid');
    if (typeof code === 'string' && limitedCodes.has(code)) fail('limited');
    fail('unavailable');
  }
  if (user === null || user === undefined) fail('unavailable');
}

export function createAuthSignupBridge({
  enabled = false, createAuthClient, now = Date.now, setTimer = setTimeout, clearTimer = clearTimeout,
} = {}) {
  let disposed = false, lastTime = -Infinity;
  const active = new Set(), emails = new Map(), sources = new Map();
  function reserve(email, address) {
    if (typeof address !== 'string' || !isIP(address)) fail('unavailable');
    const time = now();
    if (!Number.isFinite(time) || time < lastTime) fail('unavailable');
    lastTime = time;
    for (const [key, expiry] of emails) if (expiry <= time) emails.delete(key);
    for (const [key, entry] of sources) if (entry.until <= time) sources.delete(key);
    // Hash only the email limiter key, never a password. No identity lookup is performed.
    const emailKey = createHash('sha256').update(email.toLowerCase()).digest('hex');
    const entry = sources.get(address);
    if (emails.has(emailKey) || (entry && entry.count >= 5)) fail('limited');
    if ((!emails.has(emailKey) && emails.size >= capacity) || (!entry && sources.size >= capacity)) fail('unavailable');
    emails.set(emailKey, time + emailCooldownMs);
    sources.set(address, { count: (entry?.count || 0) + 1, until: entry?.until ?? time + sourceWindowMs });
  }
  const bridge = async (req, res) => {
    if (typeof req.url !== 'string' || !ownsRoute(req.url)) return false;
    const reply = (status, body, extra = {}) => {
      if (res.destroyed || res.writableEnded) return;
      res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
        'Connection': 'close', ...extra }); // Also closes incomplete/oversized request bodies after the fixed reply.
      res.end(req.method === 'HEAD' ? undefined : JSON.stringify(body));
    };
    const reject = kind => {
      const [status, code] = errors[kind] || errors.unavailable;
      reply(status, { error: { code } }, kind === 'method' ? { Allow: 'POST' } : kind === 'limited' ? { 'Retry-After': '60' } : {});
    };
    if (req.url !== route) { reject('not_found'); return true; }
    if (req.method !== 'POST') { reject('method'); return true; }
    if (singleHeader(req, 'origin') !== origin || singleHeader(req, 'host') !== host) { reject('origin'); return true; }
    if (!/^application\/json(?:\s*;\s*charset\s*=\s*(?:utf-8|"utf-8"))?\s*$/i.test(singleHeader(req, 'content-type') || '')) {
      reject('media'); return true;
    }
    if (enabled !== true || typeof createAuthClient !== 'function' || disposed) { reject('unavailable'); return true; }
    if (req.aborted || res.destroyed || res.writableEnded) return true;
    const controller = new AbortController(); active.add(controller);
    let attempted = false, timer;
    const abort = () => controller.abort();
    const cancelled = new Promise((_, rejectPromise) => {
      controller.signal.addEventListener('abort', () => rejectPromise(new Failure('unknown')), { once: true });
    });
    req.once('aborted', abort); res.once('close', abort);
    const live = () => { if (controller.signal.aborted || disposed) fail('unknown'); };
    const run = async () => {
      const input = parseInput(await readBody(req, controller.signal)); live();
      reserve(input.email, req.socket?.remoteAddress); live();
      const client = await createAuthClient({ signal: controller.signal }); live();
      if (!client || typeof client.signUp !== 'function') fail('unavailable');
      attempted = true;
      const result = await client.signUp({ email: input.email, password: input.password,
        options: { emailRedirectTo: callback } });
      live(); projectResult(result, input.email);
    };
    try {
      timer = setTimer(abort, deadlineMs);
      await Promise.race([run(), cancelled]);
      reply(202, { status: 'pending_confirmation' });
    } catch (error) {
      reject(error instanceof Failure ? error.kind : attempted ? 'unknown' : 'unavailable');
    } finally {
      clearTimer(timer); controller.abort(); active.delete(controller);
      req.removeListener('aborted', abort); res.removeListener('close', abort);
    }
    return true;
  };
  bridge.dispose = () => {
    disposed = true;
    for (const controller of active) controller.abort();
    emails.clear(); sources.clear();
  };
  return bridge;
}
