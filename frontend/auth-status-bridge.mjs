import { constants } from 'node:fs';
import { lstat, open } from 'node:fs/promises';
import { parseEnv } from 'node:util';

const route = '/api/auth/status';
const endpoint = 'http://127.0.0.1:57421/auth/v1/health';
const keyPath = '/home/user01/Cy_Omics_v2.0/auth-dev/.env.keys';
const unavailable = { error: { code: 'auth_unavailable' } };
const healthy = { status: 'ok', service: 'Cy_Omics_v2.0-auth', version: '2.197.0' };
const keyLimit = 16 * 1024;
const validKey = key => typeof key === 'string' && key.length > 0 && key.length <= 4096 && /^[\x21-\x7e]+$/.test(key);
const check = condition => { if (!condition) throw new Error('unavailable'); };

// Construction performs no I/O. Only an enabled, exact GET can call this provider.
function createFileKeyProvider() {
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
          line(pending.slice(0, newline));
          pending = pending.slice(newline + 1);
        }
      }
      pending += decoder.decode();
      line(pending);
      const after = await file.stat();
      check(!signal.aborted && ['dev', 'ino', 'size', 'mtimeMs', 'ctimeMs', 'uid', 'mode', 'nlink'].every(k => before[k] === after[k]));
      check(anonLine !== undefined);
      // Only this single line reaches parseEnv; other keys never enter an object or process.env.
      const parsed = parseEnv(anonLine);
      check(Object.keys(parsed).length === 1 && validKey(parsed.ANON_KEY));
      cached = parsed.ANON_KEY;
      return cached;
    } finally {
      await file.close();
    }
  };
}

function ownsRoute(raw) {
  try {
    const pathname = decodeURIComponent(new URL(raw, 'http://127.0.0.1').pathname).replace(/\/{2,}/g, '/');
    return pathname === route || pathname.startsWith(route + '/') || raw.startsWith(route);
  } catch {
    return raw.startsWith(route);
  }
}

export function createAuthStatusBridge({
  fetchImpl = globalThis.fetch,
  enabled = process.env.CYOMICS_AUTH_STATUS_ENABLED === '1',
  keyProvider = createFileKeyProvider(),
} = {}) {
  let disposed = false;
  const active = new Set();
  const bridge = async (req, res) => {
    if (typeof req.url !== 'string' || !ownsRoute(req.url)) return false;
    const reply = (status, body, extra = {}) => {
      if (res.destroyed || res.writableEnded) return;
      res.writeHead(status, {
        'Content-Type': 'application/json; charset=utf-8',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
        'Referrer-Policy': 'no-referrer', ...extra,
      });
      res.end(req.method === 'HEAD' ? undefined : JSON.stringify(body));
    };
    if (req.url !== route) {
      reply(404, { error: { code: 'not_found' } });
      return true;
    }
    if (req.method !== 'GET') {
      reply(405, { error: { code: 'method_not_allowed' } }, { Allow: 'GET' });
      return true;
    }
    if (enabled !== true || disposed) {
      reply(503, unavailable);
      return true;
    }
    const controller = new AbortController();
    active.add(controller);
    let reader, upstream;
    const abort = () => controller.abort();
    const cancelled = new Promise((_, reject) => {
      controller.signal.addEventListener('abort', () => reject(new Error('unavailable')), { once: true });
    });
    const timer = setTimeout(abort, 2000);
    req.once?.('aborted', abort);
    res.once?.('close', abort);
    const readHealth = async () => {
      const key = await keyProvider(controller.signal);
      check(!controller.signal.aborted && validKey(key));
      upstream = await fetchImpl(endpoint, {
        method: 'GET', redirect: 'error', credentials: 'omit', cache: 'no-store',
        signal: controller.signal, headers: { Accept: 'application/json', apikey: key },
      });
      if (controller.signal.aborted) {
        if (upstream?.body) void upstream.body.cancel().catch(() => {});
        throw new Error('unavailable');
      }
      check(upstream.redirected !== true && upstream.status === 200
        && /^application\/json(?:\s*;|$)/i.test(upstream.headers.get('content-type') || '')
        && upstream.body);
      reader = upstream.body.getReader();
      const chunks = [];
      let bytes = 0;
      while (true) {
        const { done, value } = await reader.read();
        check(!controller.signal.aborted);
        if (done) break;
        bytes += value.byteLength;
        check(bytes <= 4096);
        chunks.push(value);
      }
      const body = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks)));
      check(body && !Array.isArray(body) && body.name === 'GoTrue' && body.version === 'v2.197.0');
      return healthy;
    };
    try {
      reply(200, await Promise.race([readHealth(), cancelled]));
    } catch {
      reply(503, unavailable);
    } finally {
      clearTimeout(timer);
      req.removeListener?.('aborted', abort);
      res.removeListener?.('close', abort);
      controller.abort();
      active.delete(controller);
      if (reader) void reader.cancel().catch(() => {});
      else if (upstream?.body) void upstream.body.cancel().catch(() => {});
    }
    return true;
  };
  bridge.dispose = () => {
    disposed = true;
    for (const controller of active) controller.abort();
  };
  return bridge;
}
