import http from 'node:http';

export class ProjectFailure extends Error {
  constructor(status = 503, code = 'projects_unavailable') { super(code); this.status = status; this.code = code; }
}
const bad = (status, code) => { throw new ProjectFailure(status, code); };
const prefix = '/api/v1/projects';
const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const exact = (value, keys) => record(value) && Object.keys(value).sort().join(',') === [...keys].sort().join(',');
const scalar = value => typeof value === 'string' && Buffer.from(value).toString('utf8') === value;
// Python str.strip's Unicode whitespace set, matching the frozen API input policy.
const whitespace = '[\\u0009-\\u000d\\u001c-\\u0020\\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]';
const trim = value => value.replace(new RegExp('^' + whitespace + '+|' + whitespace + '+$', 'gu'), '');
function textField(value, key) {
  if (!scalar(value)) throw Error();
  value = trim(value);
  if ([...value].length > ({ name: 80, description: 1000, species: 200 })[key]
      || key === 'name' && !value || value.includes('\0')
      || key !== 'description' && /[\u0000-\u001f\u007f-\u009f]/u.test(value)) throw Error();
  return value;
}

// A bounded JSON parser retains duplicate-key rejection at every object depth.
// JSON.parse alone would erase ambiguous keys before forwarding/validation.
export function strictProjectJSON(bytes) {
  const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes);
  let at = 0;
  const space = () => { while (/[\x20\t\n\r]/.test(text[at] || '\x01')) at++; };
  const string = () => {
    const start = at++;
    while (at < text.length) {
      const c = text[at++];
      if (c === '\\') { at++; continue; }
      if (c === '"') {
        const result = JSON.parse(text.slice(start, at));
        if (!scalar(result)) throw Error();
        return result;
      }
    }
    throw Error();
  };
  function value(depth) {
    if (depth > 32) throw Error();
    space();
    if (text[at] === '"') return string();
    if (text[at] === '{') {
      at++; space(); const result = Object.create(null), seen = new Set();
      if (text[at] === '}') { at++; return result; }
      while (true) {
        space(); if (text[at] !== '"') throw Error();
        const key = string(); space();
        if (seen.has(key) || text[at++] !== ':') throw Error();
        seen.add(key); result[key] = value(depth + 1); space();
        const separator = text[at++]; if (separator === '}') return result;
        if (separator !== ',') throw Error();
      }
    }
    if (text[at] === '[') {
      at++; space(); const result = [];
      if (text[at] === ']') { at++; return result; }
      while (true) {
        result.push(value(depth + 1)); space();
        const separator = text[at++]; if (separator === ']') return result;
        if (separator !== ',') throw Error();
      }
    }
    for (const [literal, result] of [['true', true], ['false', false], ['null', null]]) {
      if (text.startsWith(literal, at)) { at += literal.length; return result; }
    }
    const number = /-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/y; number.lastIndex = at;
    const match = number.exec(text); if (!match || !Number.isFinite(Number(match[0]))) throw Error();
    at = number.lastIndex; return Number(match[0]);
  }
  const result = value(0); space(); if (at !== text.length) throw Error(); return result;
}
function timestamp(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/.test(value) || value.startsWith('0000-')) return false;
  const ms = Date.parse(value);
  return Number.isFinite(ms) && new Date(ms).toISOString() === value.slice(0, 23) + 'Z';
}
function cursor(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{1,512}$/.test(value)) throw Error();
  const bytes = Buffer.from(value, 'base64url');
  if (bytes.length > 384 || bytes.toString('base64url') !== value) throw Error();
  const obj = strictProjectJSON(bytes);
  if (!exact(obj, ['v', 'createdAt', 'project_id']) || obj.v !== 1 || !uuid(obj.project_id) || !timestamp(obj.createdAt)) throw Error();
  return value;
}
export function ownsProjectRoute(raw) {
  if (typeof raw !== 'string') return false;
  try { return decodeURIComponent(new URL(raw.startsWith('/') ? 'http://127.0.0.1' + raw : raw, 'http://127.0.0.1').pathname).replace(/\/{2,}/g, '/').startsWith(prefix); }
  catch { return raw.startsWith(prefix); }
}
export function projectRoute(raw, method) {
  if (typeof raw !== 'string' || !/^[\x21-\x7e]+$/.test(raw) || !raw.startsWith(prefix) || raw.includes('#')) bad(404, 'not_found');
  const question = raw.indexOf('?'), path = question < 0 ? raw : raw.slice(0, question), query = question < 0 ? '' : raw.slice(question + 1);
  const detail = path !== prefix;
  if (detail && (!path.startsWith(prefix + '/') || !path.slice(prefix.length + 1) || path.slice(prefix.length + 1).includes('/'))) bad(404, 'not_found');
  if (detail && !uuid(path.slice(prefix.length + 1))) bad(404, 'project_not_found');
  if (!(detail ? ['GET'] : ['GET', 'POST']).includes(method)) bad(405, 'method_not_allowed');
  if ((detail || method === 'POST') && query) bad(400, 'invalid_project_query');
  let limit = 20;
  try {
    if (query.length > 4096 || /%(?![0-9a-fA-F]{2})/.test(query)) throw Error();
    const values = new Map();
    for (const part of query ? query.split('&') : []) {
      const at = part.indexOf('='); if (at < 1) throw Error();
      const decode = v => decodeURIComponent(v.replace(/\+/g, ' '));
      const key = decode(part.slice(0, at)), val = decode(part.slice(at + 1));
      if (!['limit', 'cursor'].includes(key) || values.has(key)) throw Error(); values.set(key, val);
    }
    if (values.has('limit')) {
      const value = values.get('limit'); if (!/^\d+$/.test(value) || !Number.isInteger(Number(value)) || Number(value) < 1 || Number(value) > 50) throw Error();
      limit = Number(value);
    }
    if (values.has('cursor')) cursor(values.get('cursor'));
  } catch { bad(400, 'invalid_project_query'); }
  return { path: raw, method, kind: method === 'POST' ? 'create' : detail ? 'detail' : 'list', limit };
}
export function projectInput(bytes) {
  try {
    const value = strictProjectJSON(bytes);
    if (!record(value) || !Object.hasOwn(value, 'name') || Object.keys(value).some(k => !['name', 'description', 'species'].includes(k))) throw Error();
    return Object.fromEntries(['name', 'description', 'species'].map(key => [key, textField(Object.hasOwn(value, key) ? value[key] : '', key)]));
  } catch { bad(400, 'invalid_project'); }
}
export function readProjectBody(req, signal) {
  return new Promise((resolve, reject) => {
    let count = 0, settled = false; const chunks = [];
    const cleanup = () => { req.removeListener('data', data); req.removeListener('end', end); signal.removeEventListener('abort', abort); };
    const finish = error => { if (settled) return; settled = true; cleanup(); if (error) { req.pause?.(); reject(error); } else resolve(Buffer.concat(chunks)); };
    const data = chunk => {
      if (!(chunk instanceof Uint8Array)) return finish(new ProjectFailure(400, 'invalid_project'));
      count += chunk.byteLength;
      if (count > 8192) return finish(new ProjectFailure(413, 'project_body_too_large'));
      chunks.push(chunk);
    };
    const end = () => finish();
    const error = () => finish(new ProjectFailure(400, 'invalid_project'));
    const abort = () => finish(new ProjectFailure());
    req.on('data', data); req.once('end', end); req.on('error', error);
    req.once('close', () => req.removeListener('error', error));
    signal.addEventListener('abort', abort, { once: true }); if (signal.aborted) abort();
  });
}
const codes = new Map([
  [400, ['invalid_project', 'invalid_project_query']], [401, ['unauthenticated']], [403, ['auth_origin_rejected']],
  [404, ['project_not_found', 'not_found']], [405, ['method_not_allowed']], [409, ['project_context_changed']],
  [413, ['project_body_too_large']], [415, ['unsupported_media_type']], [429, ['project_rate_limited']],
  [503, ['projects_unavailable']], [504, ['project_create_result_unknown']],
]);
function project(value) {
  if (!exact(value, ['project_id', 'name', 'description', 'species', 'visibility', 'createdAt', 'updatedAt'])
      || !uuid(value.project_id) || value.visibility !== 'private' || !timestamp(value.createdAt) || !timestamp(value.updatedAt)) throw Error();
  for (const key of ['name', 'description', 'species']) if (textField(value[key], key) !== value[key]) throw Error();
}
function publicValue(route, status, value) {
  if (codes.has(status)) {
    if (!exact(value, ['error']) || !exact(value.error, ['code']) || !codes.get(status).includes(value.error.code)) throw Error();
    return value;
  }
  if (status !== (route.kind === 'create' ? 201 : 200)) throw Error();
  if (route.kind === 'list') {
    if (!exact(value, ['items', 'nextCursor']) || !Array.isArray(value.items) || value.items.length > route.limit) throw Error();
    value.items.forEach(project); if (value.nextCursor !== null) cursor(value.nextCursor);
  } else { if (!exact(value, ['project'])) throw Error(); project(value.project); }
  return value;
}
export function createProjectBridge({ requestImpl = http.request } = {}) {
  if (typeof requestImpl !== 'function') throw new TypeError('Invalid project transport');
  return ({ route, body, accessToken, signal, budget, onSent = () => {} }) => new Promise((resolve, reject) => {
    let outgoing, incoming, settled = false, sent = false;
    const chunks = []; let size = 0;
    const failure = () => new ProjectFailure(route.kind === 'create' && sent ? 504 : 503,
      route.kind === 'create' && sent ? 'project_create_result_unknown' : 'projects_unavailable');
    const finish = (error, result) => {
      if (settled) return; settled = true; signal.removeEventListener('abort', abort);
      if (error) { outgoing?.destroy(); incoming?.destroy(); reject(error); } else resolve(result);
    };
    const abort = () => finish(failure());
    try {
      const remaining = Math.floor(budget.remaining());
      if (signal.aborted || !Number.isFinite(remaining) || remaining <= 0 || remaining > 12000 || !/^[1-9]\d{12}$/.test(budget.deadlineUnixMs)
          || typeof accessToken !== 'string' || !/^[A-Za-z0-9._-]{1,8192}$/.test(accessToken)) throw Error();
      // Revalidate route even for internal callers. Endpoint/Host cannot be injected.
      route = projectRoute(route.path, route.method);
      const headers = { Accept: 'application/json', Authorization: 'Bearer ' + accessToken,
        'X-Cyomics-Remaining-Ms': String(remaining), 'X-Cyomics-Deadline-Unix-Ms': budget.deadlineUnixMs };
      if (route.kind === 'create') { headers['Content-Type'] = 'application/json'; headers['Content-Length'] = String(body.byteLength); }
      outgoing = requestImpl({ hostname: '127.0.0.1', port: 57430, method: route.method, path: route.path, headers, agent: false }, response => {
        incoming = response;
        if (settled) { response.on('error', () => {}); response.destroy(); return; }
        const invalid = () => finish(failure());
        response.on('error', invalid); response.once('aborted', invalid);
        try {
          const raw = response.rawHeaders; let media;
          if (!Array.isArray(raw) || raw.length % 2) throw Error();
          for (let i = 0; i < raw.length; i += 2) {
            if (typeof raw[i] !== 'string' || typeof raw[i + 1] !== 'string') throw Error();
            if (raw[i].toLowerCase() === 'set-cookie') throw Error();
            if (raw[i].toLowerCase() === 'content-type') { if (media !== undefined) throw Error(); media = raw[i + 1]; }
          }
          if (!/^application\/json(?:\s*;\s*charset\s*=\s*(?:utf-8|"utf-8"))?\s*$/i.test(media || '')) throw Error();
          const status = response.statusCode;
          if (!codes.has(status) && status !== (route.kind === 'create' ? 201 : 200)) throw Error();
          const maximum = codes.has(status) ? 4096 : route.kind === 'list' ? 524288 : 16384;
          response.on('data', chunk => {
            if (settled) return;
            if (!(chunk instanceof Uint8Array) || (size += chunk.byteLength) > maximum) return invalid();
            chunks.push(chunk);
          });
          response.once('end', () => {
            if (settled) return;
            try {
              if (signal.aborted || budget.remaining() <= 0) throw Error();
              const value = publicValue(route, status, strictProjectJSON(Buffer.concat(chunks)));
              finish(undefined, { status, value });
            } catch { invalid(); }
          });
          response.once('close', () => { if (!settled) invalid(); });
        } catch { invalid(); }
      });
      outgoing.on('error', abort);
      outgoing.once('close', () => { if (!settled && !incoming) abort(); });
      if (settled) { outgoing.destroy(); return; }
      signal.addEventListener('abort', abort, { once: true });
      if (signal.aborted || budget.remaining() <= 0) return abort();
      sent = true; onSent(); outgoing.end(body);
    } catch { finish(failure()); }
  });
}
