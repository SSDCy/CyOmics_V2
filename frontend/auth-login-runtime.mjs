import {ownsDEClassificationReadRoute,deClassificationReadRoute,deClassificationReadError,DEClassificationReadFailure} from './de-classification-read-contract.mjs';
import {ownsDifferentialResultsRoute,differentialResultRoute,differentialResultError,DifferentialResultFailure} from './differential-result-contract.mjs';
import {ownsDifferentialJobsRoute,differentialJobRoute,differentialJobInput,differentialJobError,DifferentialJobFailure} from './differential-job-contract.mjs';
import {ownsDifferentialInputRoute,differentialInputRoute,differentialInput,differentialInputError,DifferentialInputFailure,DIFFERENTIAL_INPUT_MAX} from './differential-input-contract.mjs';
import {createPreprocessBridge,ownsPreprocessRoute,preprocessRoute,preprocessInput,preprocessError,readPreprocessBody,sendPreprocessJSONResponse,PreprocessBridgeFailure,PREPROCESS_CAP} from './preprocess-bridge.mjs';
import {createArtifactBridge,createArtifactLeasePool,ownsArtifactRoute,artifactRoute,artifactError,readArtifactRequest,sendArtifactResponse,ArtifactFailure,ARTIFACT_TOTAL_MS,ARTIFACT_UPSTREAM_MS} from './raw-qc-artifact-bridge.mjs';
import {createSampleDesignBridge} from './sample-design-bridge.mjs';
import {createRawQCBridge,ownsRawQCRoute,rawQCRoute,rawQCInput,rawQCError,readRawQCBody,sendRawQCJSONResponse,RawQCBridgeFailure,RAW_QC_CAP} from './raw-qc-bridge.mjs';
import {sampleDesignRoute,sampleDesignInput,validateSource,freeze,validateSampleDesignError,SampleDesignFailure,MAX_REQUEST} from './sample-design-contract.mjs';
import {ownsSampleDesignRoute,readSampleDesignBody} from './sample-design-runtime-helpers.mjs';
import {sendSampleDesignJSONResponse} from './sample-design-response.mjs';
import {createHash} from 'node:crypto';
import {sendRawUploadResponse} from './raw-upload-response.mjs';
import {createImportBridge,ownsImportRoute,importRoute,importInput,readImportBody,ImportFailure} from './imports-bridge.mjs';
import {validateImportError} from './imports-contract.mjs';
import {sendImportJSONResponse} from './imports-response.mjs';
import {createRawUploadBridge, ownsUploadRoute, uploadRoute, uploadHeaders, readUploadBody, UploadFailure, MAX_UPLOAD_BYTES} from './raw-uploads-bridge.mjs';
import { randomBytes } from 'node:crypto';
import { isIP } from 'node:net';
import { domainToASCII } from 'node:url';
import { createAnonKeyProvider } from './auth-register-sdk.mjs';
import { createLoginClient, LoginFailure } from './auth-login-sdk.mjs';
import { performance } from 'node:perf_hooks';
import { createProjectBridge, ownsProjectRoute, projectRoute, projectInput, readProjectBody, ProjectFailure } from './projects-bridge.mjs';

const origin = 'http://127.0.0.1:4206';
const routes = ['/api/auth/login', '/api/v1/me', '/api/auth/logout'];
const browserName = 'cyomics_login_browser', sessionName = 'cyomics_session';
const nonceShape = /^[A-Za-z0-9_-]{43}$/;
const capacity = 256, ttl = 900000, windowMs = 600000;
class Failure extends Error { constructor(code, status) { super(code); this.code = code; this.status = status; } }
const fail = (code, status) => { throw new Failure(code, status); };
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
function jar(req) {
  if (req.headers?.cookie === undefined) return {};
  const raw = single(req, 'cookie');
  if (raw === undefined || raw.length > 8192) fail('auth_origin_rejected', 403);
  const result = Object.create(null), seen = new Set();
  for (const part of raw.split(';')) {
    const pair = part.trim(), at = pair.indexOf('='), name = pair.slice(0, at).trim();
    if (at < 1 || !/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(name) || seen.has(name)) fail('auth_origin_rejected', 403);
    seen.add(name);
    if (name === browserName || name === sessionName) result[name] = pair.slice(at + 1);
  }
  return result;
}
function owned(raw) {
  try {
    const decoded = decodeURIComponent(new URL(raw.startsWith('/') ? origin + raw : raw, origin).pathname).replace(/\/{2,}/g, '/');
    return routes.some(path => decoded === path || decoded.startsWith(path + '/') || raw.startsWith(path));
  } catch { return routes.some(path => raw.startsWith(path)); }
}
function input(bytes, logout) {
  let text, value;
  try { text = new TextDecoder('utf-8', { fatal: true }).decode(bytes); value = JSON.parse(text); }
  catch { fail(logout ? 'invalid_logout' : 'invalid_login', 400); }
  const invalid = () => fail(logout ? 'invalid_logout' : 'invalid_login', 400);
  if (!value || typeof value !== 'object' || Array.isArray(value)) invalid();
  if (logout) { if (!/^\s*\{\s*\}\s*$/.test(text)) invalid(); return {}; }
  if (Object.keys(value).sort().join(',') !== 'email,password' || typeof value.email !== 'string' || typeof value.password !== 'string') invalid();
  const skeleton = text.replace(/"(?:\\[\s\S]|[^"\\])*"/g, '""');
  if (!/^\s*\{\s*""\s*:\s*""\s*,\s*""\s*:\s*""\s*\}\s*$/.test(skeleton)) invalid();
  const email = value.email.trim(), parts = email.split('@'), local = parts[0], domain = parts.length === 2 ? domainToASCII(parts[1]) : '';
  if (Buffer.byteLength(email) > 254 || !local || local.length > 64 || !/^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+$/.test(local)
      || local.startsWith('.') || local.endsWith('.') || local.includes('..') || !domain || domain.length > 253
      || domain.split('.').length < 2 || !domain.split('.').every(label => /^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$/.test(label))) invalid();
  const password = value.password;
  if (!password.length || Buffer.byteLength(password) > 72 || Buffer.from(password).toString('utf8') !== password) invalid();
  return { email, password };
}
function body(req, signal, logout) {
  return new Promise((resolve, reject) => {
    const chunks = []; let bytes = 0, settled = false;
    const cleanup = () => { req.removeListener('data', data); req.removeListener('end', end); signal.removeEventListener('abort', abort); };
    const stop = error => { if (settled) return; settled = true; cleanup(); req.pause?.(); reject(error); };
    const data = chunk => {
      if (!(chunk instanceof Uint8Array)) return stop(new Failure(logout ? 'invalid_logout' : 'invalid_login', 400));
      bytes += chunk.byteLength;
      if (bytes > 4096) return stop(new Failure('auth_body_too_large', 413));
      chunks.push(chunk);
    };
    const end = () => { if (settled) return; settled = true; cleanup(); resolve(Buffer.concat(chunks)); };
    const error = () => stop(new Failure(logout ? 'invalid_logout' : 'invalid_login', 400));
    const abort = () => stop(new Failure(logout ? 'logout_unavailable' : 'login_result_unknown', logout ? 503 : 504));
    req.on('data', data); req.once('end', end); req.on('error', error);
    req.once('close', () => req.removeListener('error', error));
    signal.addEventListener('abort', abort, { once: true });
    if (signal.aborted) abort();
  });
}
const publicUser = email => ({ status: 'authenticated', user: { email, emailVerified: true } });

export function createAuthLoginRuntime({ enabled = false, keyProvider = createAnonKeyProvider(), createClient = createLoginClient,
  fetchImpl = globalThis.fetch, now = Date.now, setTimer = setTimeout, clearTimer = clearTimeout,
  expectedOrigin = origin, projectsEnabled = false, projectRequest, uploadsEnabled = false, uploadRequest,
  importsEnabled = false, importRequest, sampleDesignsEnabled = false, sampleDesignRequest, rawQcEnabled = false, rawQcRequest, rawQcArtifactsEnabled = false, rawQcArtifactRequest = null, preprocessingEnabled = false, preprocessingRequest = null, differentialInputEnabled = false, differentialInputRequest = null, differentialJobsEnabled = false, differentialJobsRequest = null, differentialResultsEnabled = false, differentialResultsRequest = null, deClassificationsEnabled = false, deClassificationsRequest = null, projectNow = () => performance.now() } = {}) {
  const port = typeof expectedOrigin === 'string' && /^http:\/\/127\.0\.0\.1:([1-9]\d{0,4})$/.exec(expectedOrigin);
  if (!port || Number(port[1]) > 65535) throw new TypeError('Invalid private login origin');
  const expected = new URL(expectedOrigin), expectedHost = expected.host;
  let disposed = false, lastTime = -Infinity;
  const contexts = new Map(), sessions = new Map(), sources = new Map(), active = new Set();
  const projectBridge = createProjectBridge({ requestImpl: projectRequest });
  const uploadBridge = createRawUploadBridge({ requestImpl: uploadRequest ?? projectRequest });
  const importBridge = createImportBridge({requestImpl:importRequest??projectRequest});
  const sampleDesignBridge=createSampleDesignBridge({requestImpl:sampleDesignRequest,setTimer,clearTimer});
  const sampleDesignErrorBridge=createSampleDesignBridge({requestImpl:sampleDesignRequest,setTimer,clearTimer,allowSourceFreeGetErrors:true});
  const rawQCBridge=createRawQCBridge({requestImpl:rawQcRequest,setTimer,clearTimer,now:projectNow});
  const preprocessBridge=createPreprocessBridge({requestImpl:preprocessingRequest,setTimer,clearTimer,now:projectNow});
  const deClassificationsBridge=createPreprocessBridge({requestImpl:deClassificationsRequest,setTimer,clearTimer,now:projectNow,deClassifications:true});
  const differentialResultsBridge=createPreprocessBridge({requestImpl:differentialResultsRequest,setTimer,clearTimer,now:projectNow,differentialResults:true});
  const differentialJobsBridge=createPreprocessBridge({requestImpl:differentialJobsRequest,setTimer,clearTimer,now:projectNow,differentialJobs:true});
  const differentialInputBridge=createPreprocessBridge({requestImpl:differentialInputRequest,setTimer,clearTimer,now:projectNow,differentialInputReview:true});
  const artifactBridge=createArtifactBridge({requestImpl:rawQcArtifactRequest,setTimer,clearTimer,now:projectNow});
  const artifactLeases=createArtifactLeasePool();
  const importSubjects=new Set(),importAttempts=new Map();let importActive=0;
  const uploadSubjects = new Set(), uploadAttempts = new Map();
  let uploadActive = 0;
  const clock = () => {
    const time = now();
    if (!Number.isFinite(time) || time < lastTime) throw new LoginFailure();
    lastTime = time; return time;
  };
  const removeSession = (id, expectedSession) => {
    const session = sessions.get(id);
    if (!session || expectedSession && session !== expectedSession) return;
    session.sampleDesignSources.clear();session.sampleDesignSourceBytes=0;
    sessions.delete(id);
    for (const op of session.context.operations)
      if (op.session === session && (op.kind === 'project' || op.kind === 'me' || op.kind === 'upload' || op.kind === 'import' || op.kind === 'sample-design' || op.kind === 'raw-qc' || op.kind === 'raw-qc-artifact')) op.controller.abort();
  };
  function close(context) {
    context.closed = true; context.epoch++;
    for (const op of context.operations) op.controller.abort();
    for (const [id, session] of sessions) if (session.context === context) removeSession(id);
  }
  function sweep(time) {
    for (const [id, session] of sessions) if (session.expiresAt <= time) removeSession(id);
    for (const [nonce, context] of contexts) if (context.expiresAt <= time || context.closed) { close(context); contexts.delete(nonce); }
    for (const [ip, entry] of sources) if (entry.until <= time) sources.delete(ip);
  }
  const interval = enabled === true ? setInterval(() => {
    try { sweep(clock()); } catch { for (const context of contexts.values()) close(context); contexts.clear(); sessions.clear(); }
  }, 1000) : undefined;
  interval?.unref();
  function cookie(res, name, value, path, age) {
    const old = res.getHeader('Set-Cookie');
    res.setHeader('Set-Cookie', [...(Array.isArray(old) ? old : old ? [old] : []),
      `${name}=${value}; Path=${path}; HttpOnly; SameSite=Lax; Max-Age=${age}`]);
  }
  const clearSession = res => cookie(res, sessionName, '', '/api', 0);
  const reply = (req, res, status, value, extra = {}) => {
    if (req.aborted || res.destroyed || res.writableEnded) return;
    res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store',
      'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff', 'Connection': 'close', ...extra });
    res.end(req.method === 'HEAD' ? undefined : JSON.stringify(value));
  };
  const runtime = async (req, res) => {
    if (ownsArtifactRoute(req.url)) return handleRawQCArtifact(req,res);
    if (ownsRawQCRoute(req.url)) return handleRawQC(req,res);
    if (ownsDEClassificationReadRoute(req.url)) return handlePreprocessing(req,res,false,false,false,true);
    if (ownsDifferentialResultsRoute(req.url)) return handlePreprocessing(req,res,false,false,true);
    if (ownsDifferentialJobsRoute(req.url)) return handlePreprocessing(req,res,false,true);
    if (ownsDifferentialInputRoute(req.url)) return handlePreprocessing(req,res,true);
    if (ownsPreprocessRoute(req.url)) return handlePreprocessing(req,res);
    if (ownsSampleDesignRoute(req.url)) return handleSampleDesign(req,res);
    if (ownsImportRoute(req.url)) return handleImport(req,res);
    if (ownsUploadRoute(req.url)) return handleUpload(req, res);
    if (ownsProjectRoute(req.url)) return handleProject(req, res);
    if (typeof req.url !== 'string' || !owned(req.url)) return false;
    const me = req.url === routes[1], logout = req.url === routes[2];
    const unavailable = me ? 'session_unavailable' : logout ? 'logout_unavailable' : 'login_unavailable';
    const respond = (status, value, extra) => reply(req, res, status, value, extra);
    const reject = (status, code, extra) => respond(status, { error: { code } }, extra);
    let op, client, timer, context, nonce, session, sessionId, commit;
    try {
      if (!routes.includes(req.url)) fail('not_found', 404);
      if (req.method !== (me ? 'GET' : 'POST')) fail('method_not_allowed', 405);
      for (const name of ['host', 'origin', 'x-cyomics-request', 'content-type', 'cookie', 'sec-fetch-site', 'content-length'])
        if (req.headers?.[name] !== undefined && single(req, name) === undefined) fail('auth_origin_rejected', 403);
      if (single(req, 'host') !== expectedHost || (me ? req.headers?.origin !== undefined && single(req, 'origin') !== expected.origin
          : single(req, 'origin') !== expected.origin || single(req, 'x-cyomics-request') !== '1')
          || req.headers?.['sec-fetch-site'] !== undefined && single(req, 'sec-fetch-site') !== 'same-origin') fail('auth_origin_rejected', 403);
      if (!me && !/^application\/json(?:\s*;\s*charset\s*=\s*(?:utf-8|"utf-8"))?\s*$/i.test(single(req, 'content-type') || '')) fail('unsupported_media_type', 415);
      if (!me && req.headers?.['content-length'] !== undefined) {
        const length = single(req, 'content-length');
        if (!/^\d+$/.test(length)) fail(logout ? 'invalid_logout' : 'invalid_login', 400);
        if (Number(length) > 4096) fail('auth_body_too_large', 413);
      }
      const cookies = jar(req);
      if (enabled !== true || disposed) fail(unavailable, 503);
      if (req.aborted || res.destroyed || res.writableEnded) return true;
      let time = clock(); sweep(time);
      nonce = cookies[browserName];
      context = nonceShape.test(nonce || '') ? contexts.get(nonce) : undefined;
      sessionId = cookies[sessionName];
      session = nonceShape.test(sessionId || '') ? sessions.get(sessionId) : undefined;
      if (session?.context !== context || !context) session = undefined;
      if (me && !context) {
        if (contexts.size >= capacity) fail('session_unavailable', 503);
        nonce = randomBytes(32).toString('base64url');
        context = { expiresAt: time + ttl, epoch: 0, closed: false, operations: new Set(), attempts: 0, until: 0 };
        contexts.set(nonce, context); cookie(res, browserName, nonce, '/', 900);
      }
      if (me && !session) { reject(401, 'unauthenticated'); return true; }
      // Auth work cannot consume the local logout path's ability to revoke its own browser.
      if (!logout && active.size >= capacity) fail(unavailable, 503);
      const controller = new AbortController();
      op = { controller, deadline: time + 15000, epoch: context?.epoch, session: me ? session : undefined, kind: me ? 'me' : logout ? 'logout' : 'login' };
      active.add(op);
      if (!logout) context?.operations.add(op);
      const abort = () => controller.abort();
      req.once('aborted', abort); res.once('close', abort);
      op.cleanup = () => { req.removeListener('aborted', abort); res.removeListener('close', abort); };
      const cancelled = new Promise((_, rejectPromise) => controller.signal.addEventListener('abort', () =>
        rejectPromise(me && (sessions.get(sessionId) !== session || context?.closed || contexts.get(nonce) !== context)
          ? new Failure('unauthenticated', 401)
          : new Failure(me ? 'session_unavailable' : logout ? 'logout_unavailable' : 'login_result_unknown', me || logout ? 503 : 504)), { once: true }));
      timer = setTimer(abort, 15000);
      const live = () => {
        const current = clock();
        if (me && (sessions.get(sessionId) !== session || session.expiresAt <= current
            || contexts.get(nonce) !== context || context.closed || context.expiresAt <= current)) fail('unauthenticated', 401);
        if (controller.signal.aborted || current >= op.deadline)
          fail(me ? 'session_unavailable' : logout ? 'logout_unavailable' : 'login_result_unknown', me || logout ? 503 : 504);
        if (disposed || req.aborted || res.destroyed || res.writableEnded) throw new LoginFailure();
        if (!logout && context && (contexts.get(nonce) !== context || context.closed || context.expiresAt <= current || context.epoch !== op.epoch)) throw new LoginFailure();
        return current;
      };
      const run = async () => {
        if (!me) {
          const value = input(await body(req, controller.signal, logout), logout);
          if (!me && !logout && context) op.epoch = context.epoch;
          live();
          if (logout) {
            // This synchronous commit precedes cancellation, so no late operation can resurrect the context.
            commit = () => {
              if (context && contexts.get(nonce) === context) { close(context); contexts.delete(nonce); }
              clearSession(res); cookie(res, browserName, '', '/', 0); respond(200, { status: 'signed_out' });
            };
            return;
          }
          if (!context) fail('login_context_required', 409);
          time = live(); sweep(time);
          const ip = req.socket?.remoteAddress;
          if (typeof ip !== 'string' || !isIP(ip)) throw new LoginFailure();
          const source = sources.get(ip);
          if (context.until <= time) { context.until = time + windowMs; context.attempts = 0; }
          if (context.attempts >= 5 || source?.count >= 10) fail('login_rate_limited', 429);
          if (!source && sources.size >= capacity) throw new LoginFailure();
          context.attempts++;
          sources.set(ip, { count: (source?.count || 0) + 1, until: source?.until ?? time + windowMs });
          // One owned login at a time. A replacement invalidates the old generation before contacting Auth.
          context.epoch++; op.epoch = context.epoch;
          for(const old of sessions.values())if(old.context===context){old.sampleDesignSources.clear();old.sampleDesignSourceBytes=0;}
          for (const previous of context.operations) if (previous !== op) previous.controller.abort();
          context.operations.add(op);
          client = await createClient({ signal: controller.signal, deadline: op.deadline, keyProvider, fetchImpl, now });
          if (controller.signal.aborted) { try { void Promise.resolve(client?.dispose?.()).catch(() => {}); } catch {} throw new LoginFailure(); }
          live(); const result = await client.login(value); time = live();
          if (!result || typeof result.email !== 'string' || typeof result.userId !== 'string'
              || typeof result.accessToken !== 'string' || !Number.isFinite(result.expiresAt)) throw new LoginFailure();
          const expiresAt = Math.min(result.expiresAt, time + ttl);
          const id = randomBytes(32).toString('base64url');
          commit = () => {
            time = live();
            const age = Math.floor((expiresAt - time) / 1000);
            if (age < 1 || sessions.size >= capacity && ![...sessions.values()].some(s => s.context === context)) throw new LoginFailure();
            const viewNonce = randomBytes(16).toString('base64url');
            for (const [oldId, old] of sessions) if (old.context === context) removeSession(oldId);
            sessions.set(id, { context, accessToken: result.accessToken, userId: result.userId, email: result.email, expiresAt, viewNonce, sampleDesignSources:new Map(), sampleDesignSourceBytes:0 });
            context.expiresAt = time + ttl;
            cookie(res, browserName, nonce, '/', 900); cookie(res, sessionName, id, '/api', age);
            respond(200, publicUser(result.email));
          };
        } else {
          context.operations.add(op);
          client = await createClient({ signal: controller.signal, deadline: op.deadline, keyProvider, fetchImpl, now });
          if (controller.signal.aborted) { try { void Promise.resolve(client?.dispose?.()).catch(() => {}); } catch {} throw new LoginFailure(); }
          live(); const who = await client.current(session); live();
          if (who?.userId !== session.userId || typeof who.email !== 'string' || who.email.toLowerCase() !== session.email.toLowerCase()) throw new LoginFailure('credentials');
          commit = () => { live(); respond(200, publicUser(who.email), { 'X-Cyomics-Account-Context': session.viewNonce }); };
        }
      };
      await Promise.race([run(), cancelled]); live();
      commit();
    } catch (error) {
      if (error instanceof Failure) {
        if (me && error.code === 'unauthenticated') removeSession(sessionId);
        reject(error.status, error.code, error.status === 405 ? { Allow: me ? 'GET' : 'POST' } : error.status === 429 ? { 'Retry-After': '60' } : {});
      } else if (error instanceof LoginFailure && error.kind === 'credentials') {
        if (me) removeSession(sessionId);
        reject(401, me ? 'unauthenticated' : 'invalid_credentials');
      } else if (!me && !logout && error instanceof LoginFailure && error.kind === 'limited') reject(429, 'login_rate_limited', { 'Retry-After': '60' });
      else reject(503, unavailable);
    } finally {
      clearTimer(timer);
      if (op) { active.delete(op); context?.operations.delete(op); op.cleanup?.(); op.controller.abort(); }
      if (client) { try { void Promise.resolve(client.dispose?.()).catch(() => {}); } catch {} }
    }
    return true;
  };
  async function handleProject(req, res) {
    const respond = (status, value) => reply(req, res, status, value);
    const reject = (status, code) => respond(status, { error: { code } });
    if (projectsEnabled !== true || enabled !== true || disposed) { reject(503, 'projects_unavailable'); return true; }
    let op, timer, context, session, sessionId, browser, route, checkLive;
    try {
      const started = projectNow(), wall = clock();
      if (!Number.isFinite(started)) throw new ProjectFailure();
      const deadline = wall + 12000, deadlineUnixMs = String(deadline);
      if (!/^[1-9]\d{12}$/.test(deadlineUnixMs)) throw new ProjectFailure();
      let lastMono = started;
      const remaining = () => {
        const mono = projectNow(), current = clock();
        if (!Number.isFinite(mono) || mono < lastMono) throw new ProjectFailure();
        lastMono = mono;
        return Math.min(deadline - current, 12000 - (mono - started));
      };
      route = projectRoute(req.url, req.method);
      for (const name of ['host', 'origin', 'x-cyomics-request', 'content-type', 'cookie', 'sec-fetch-site', 'content-length', 'transfer-encoding'])
        if (req.headers?.[name] !== undefined && single(req, name) === undefined) fail('auth_origin_rejected', 403);
      if (single(req, 'host') !== expectedHost || req.headers?.origin !== undefined && single(req, 'origin') !== expected.origin
          || req.headers?.['sec-fetch-site'] !== undefined && single(req, 'sec-fetch-site') !== 'same-origin'
          || route.kind === 'create' && (single(req, 'origin') !== expected.origin || single(req, 'x-cyomics-request') !== '1')) fail('auth_origin_rejected', 403);
      const cookies = jar(req); sweep(clock());
      browser = cookies[browserName]; sessionId = cookies[sessionName];
      context = nonceShape.test(browser || '') ? contexts.get(browser) : undefined;
      session = nonceShape.test(sessionId || '') ? sessions.get(sessionId) : undefined;
      if (!context || !session || session.context !== context || context.closed || session.expiresAt <= clock() || context.expiresAt <= clock()) fail('unauthenticated', 401);
      const view = single(req, 'x-cyomics-account-context');
      if (typeof view !== 'string' || !/^[A-Za-z0-9_-]{22}$/.test(view) || view !== session.viewNonce) fail('project_context_changed', 409);
      if (active.size >= capacity || remaining() <= 0) throw new ProjectFailure();
      const controller = new AbortController();
      op = { controller, epoch: context.epoch, session, kind: 'project', sent: false };
      active.add(op); context.operations.add(op);
      const abort = () => controller.abort();
      req.once('aborted', abort); res.once('close', abort);
      op.cleanup = () => { req.removeListener('aborted', abort); res.removeListener('close', abort); };
      const availability = () => new ProjectFailure(route.kind === 'create' && op.sent ? 504 : 503,
        route.kind === 'create' && op.sent ? 'project_create_result_unknown' : 'projects_unavailable');
      checkLive = () => {
        const current = clock();
        if (sessions.get(sessionId) !== session || contexts.get(browser) !== context || session.context !== context
            || context.closed || session.expiresAt <= current || context.expiresAt <= current) fail('unauthenticated', 401);
        if (context.epoch !== op.epoch) fail('project_context_changed', 409);
        if (controller.signal.aborted || disposed || remaining() <= 0) throw availability();
        if (req.aborted || req.socket?.destroyed || res.socket?.destroyed || res.destroyed || res.writableEnded) throw availability();
      };
      let cancelListener;
      const cancelled = new Promise((_, rejectPromise) => {
        cancelListener = () => rejectPromise(availability());
        controller.signal.addEventListener('abort', cancelListener, { once: true });
      });
      op.cancelCleanup = () => controller.signal.removeEventListener('abort', cancelListener);
      timer = setTimer(abort, Math.max(1, Math.ceil(Math.min(remaining(), session.expiresAt - wall, context.expiresAt - wall))));
      const run = async () => {
        checkLive();
        if (route.kind === 'create' && !/^application\/json(?:\s*;\s*charset\s*=\s*(?:utf-8|"utf-8"))?\s*$/i.test(single(req, 'content-type') || '')) fail('unsupported_media_type', 415);
        if (req.headers?.['content-length'] !== undefined) {
          const length = single(req, 'content-length');
          if (!/^\d+$/.test(length) || req.headers?.['transfer-encoding'] !== undefined) fail('invalid_project', 400);
          if (Number(length) > 8192) fail('project_body_too_large', 413);
          if (route.kind !== 'create' && Number(length) !== 0) fail('invalid_project_query', 400);
        }
        const raw = await readProjectBody(req, controller.signal); checkLive();
        if (route.kind !== 'create' && raw.byteLength) fail('invalid_project_query', 400);
        const body = route.kind === 'create' ? Buffer.from(JSON.stringify(projectInput(raw))) : undefined;
        checkLive();
        return projectBridge({ route, body, accessToken: session.accessToken, signal: controller.signal,
          budget: { remaining, deadlineUnixMs }, onSent: () => { op.sent = true; } });
      };
      const result = await Promise.race([run(), cancelled]); checkLive();
      if (result.status === 401) {
        // The final fence above guarantees this is still the exact captured session.
        removeSession(sessionId, session); clearSession(res);
      }
      respond(result.status, result.value);
    } catch (error) {
      if (op) { try { checkLive(); } catch (fenced) { error = fenced; } }
      if (error instanceof Failure || error instanceof ProjectFailure) reject(error.status, error.code);
      else reject(route?.kind === 'create' && op?.sent ? 504 : 503, route?.kind === 'create' && op?.sent ? 'project_create_result_unknown' : 'projects_unavailable');
    } finally {
      clearTimer(timer);
      if (op) { active.delete(op); context?.operations.delete(op); op.cleanup?.(); op.cancelCleanup?.(); op.controller.abort(); }
    }
    return true;
  }
  async function handleUpload(req,res) {
    const respond=(status,value)=>reply(req,res,status,value);
    if(uploadsEnabled!==true||projectsEnabled!==true||enabled!==true||disposed){respond(503,{error:{code:'uploads_unavailable'}});return true;}
    let route,op,context,session,sessionId,browser,timer,admitted=false,longAdmitted=false,checkLive;
    const availability=()=>new UploadFailure(route?.kind==='save'&&op?.sent?504:503,route?.kind==='save'&&op?.sent?'upload_result_unknown':'uploads_unavailable');
    try {
      const started=projectNow(),wall=clock();
      if(!Number.isFinite(started))throw availability();
      route=uploadRoute(req.url,req.method);
      const cap=route.long?55000:12000,deadline=wall+cap;
      let last=started;
      const remaining=()=>{const mono=projectNow(),current=clock();if(!Number.isFinite(mono)||mono<last)throw availability();last=mono;return Math.min(deadline-current,cap-(mono-started),session?session.expiresAt-current:cap,context?context.expiresAt-current:cap);};
      const names=['host','origin','x-cyomics-request','content-type','cookie','sec-fetch-site','content-length','transfer-encoding','content-encoding','idempotency-key','x-cyomics-filename','x-cyomics-file-format','x-cyomics-file-bytes','x-cyomics-file-sha256'];
      for(const name of names)if(req.headers?.[name]!==undefined&&single(req,name)===undefined)fail('auth_origin_rejected',403);
      for(const name of ['authorization','x-user-id','x-owner-id','x-cyomics-remaining-ms','x-cyomics-deadline-unix-ms'])if(req.headers?.[name]!==undefined)fail('auth_origin_rejected',403);
      if(single(req,'host')!==expectedHost||req.headers?.origin!==undefined&&single(req,'origin')!==expected.origin||req.headers?.['sec-fetch-site']!==undefined&&single(req,'sec-fetch-site')!=='same-origin'||route.kind==='save'&&(single(req,'origin')!==expected.origin||single(req,'x-cyomics-request')!=='1'))fail('auth_origin_rejected',403);
      const cookies=jar(req);sweep(clock());browser=cookies[browserName];sessionId=cookies[sessionName];context=contexts.get(browser);session=sessions.get(sessionId);
      if(!context||!session||session.context!==context||context.closed||context.expiresAt<=clock()||session.expiresAt<=clock())fail('unauthenticated',401);
      if(typeof single(req,'x-cyomics-account-context')!=='string'||!(/^[A-Za-z0-9_-]{22}$/).test(single(req,'x-cyomics-account-context'))||single(req,'x-cyomics-account-context')!==session.viewNonce)fail('project_context_changed',409);
      if(active.size>=capacity)throw availability();
      if(route.long){if(uploadActive>=2)throw new UploadFailure(429,'upload_busy');uploadActive++;longAdmitted=true;}
      if(route.kind==='save'){
        const time=clock();for(const [id,v]of uploadAttempts)if(v.until<=time)uploadAttempts.delete(id);
        if(uploadSubjects.has(session.userId))throw new UploadFailure(429,'upload_busy');
        const entry=uploadAttempts.get(session.userId)??{until:time+600000,count:0};
        if(entry.count>=10)throw new UploadFailure(429,'upload_rate_limited');
        if(!uploadAttempts.has(session.userId)&&uploadAttempts.size>=capacity)throw new UploadFailure(429,'upload_busy');
        entry.count++;uploadAttempts.set(session.userId,entry);uploadSubjects.add(session.userId);admitted=true;
      }
      const controller=new AbortController();op={controller,epoch:context.epoch,session,kind:'upload',sent:false};active.add(op);context.operations.add(op);
      const abort=()=>controller.abort();req.once('aborted',abort);res.once('close',abort);op.cleanup=()=>{req.removeListener('aborted',abort);res.removeListener('close',abort);};
      checkLive=(allowEnded=false)=>{const time=clock();if(sessions.get(sessionId)!==session||contexts.get(browser)!==context||session.context!==context||context.closed||session.expiresAt<=time||context.expiresAt<=time)fail('unauthenticated',401);if(context.epoch!==op.epoch)fail('project_context_changed',409);if(controller.signal.aborted||disposed||remaining()<=0||req.aborted||req.socket?.destroyed||res.socket?.destroyed||res.destroyed||res.writableEnded&&!allowEnded)throw availability();};
      let cancel;const cancelled=new Promise((_,reject)=>{cancel=()=>reject(availability());controller.signal.addEventListener('abort',cancel,{once:true});});op.cancelCleanup=()=>controller.signal.removeEventListener('abort',cancel);
      timer=setTimer(abort,Math.max(1,Math.ceil(remaining())));
      const run=async()=>{
        checkLive();const metadata=route.kind==='save'?uploadHeaders(name=>single(req,name)):undefined;
        const length=single(req,'content-length');
        if(length!==undefined&&(!(/^\d+$/).test(length)||single(req,'transfer-encoding')!==undefined||Number(length)>(route.kind==='save'?MAX_UPLOAD_BYTES:0)))throw new UploadFailure(route.kind==='save'?413:400,route.kind==='save'?'upload_too_large':'invalid_upload_query');
        const raw=await readUploadBody(req,controller.signal,route.kind==='save'?MAX_UPLOAD_BYTES:0);checkLive();
        if(metadata&&(raw.length!==metadata.bytes||length!==undefined&&Number(length)!==raw.length))throw new UploadFailure(400,'invalid_upload');
        if(metadata&&createHash('sha256').update(raw).digest('hex')!==metadata.sha256)throw new UploadFailure(400,'upload_checksum_mismatch');
        return uploadBridge({route,body:route.kind==='save'?raw:undefined,metadata,accessToken:session.accessToken,signal:controller.signal,budget:{remaining,deadlineUnixMs:String(deadline)},onSent:()=>{op.sent=true;}});
      };
      const result=await Promise.race([run(),cancelled]);checkLive();
      if(result.status===401){removeSession(sessionId,session);clearSession(res);}
      if(result.body){await sendRawUploadResponse(res,{status:result.status,headers:{'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','Connection':'close',...result.headers},body:result.body,signal:op.controller.signal,checkLive:()=>checkLive(true)});}else respond(result.status,result.value);
    } catch(error) {
      if(op){try{checkLive();}catch(fenced){error=fenced;}}
      if(res.headersSent){res.destroy();}else{const value=error instanceof Failure||error instanceof UploadFailure?error:availability();respond(value.status,{error:{code:value.code}});}
    } finally {
      clearTimer(timer);if(op){active.delete(op);context?.operations.delete(op);op.cleanup?.();op.cancelCleanup?.();op.controller.abort();}
      if(admitted)uploadSubjects.delete(session.userId);if(longAdmitted)uploadActive--;
    }
    return true;
  }
  function cacheSampleDesignSource(session,value){
    let key;try{
      const v=value.datasetVersion;key=v.project_id+'|'+v.dataset_version_id;
      const source=freeze({project_id:v.project_id,dataset_version_id:v.dataset_version_id,sample_columns:value.importReport.sample_columns.map(c=>({...c}))});
      validateSource({dataset_version_id:source.dataset_version_id,sample_columns:source.sample_columns});
      const bytes=Buffer.byteLength(JSON.stringify(source)),old=session.sampleDesignSources.get(key);
      if(old){session.sampleDesignSources.delete(key);session.sampleDesignSourceBytes-=old.bytes;}
      if(bytes>131072||session.sampleDesignSources.size>=16||session.sampleDesignSourceBytes+bytes>2097152)return;
      session.sampleDesignSources.set(key,{source,bytes});session.sampleDesignSourceBytes+=bytes;
    }catch{if(key){const old=session.sampleDesignSources.get(key);if(old){session.sampleDesignSources.delete(key);session.sampleDesignSourceBytes-=old.bytes;}}}
  }
  async function handleImport(req,res) {
    let route,op,context,session,sessionId,browser,timer,admitted=false,total=false,checkLive,sendRemaining;
    const respond=async(status,value,publicFailure=false)=>{
      if(req.aborted||res.destroyed||res.writableEnded)return;
      await sendImportJSONResponse(req,res,{status,value,signal:op&&!publicFailure?op.controller.signal:undefined,
        checkLive:op&&!publicFailure?()=>checkLive(true):undefined,remaining:sendRemaining,
        onController:controller=>{if(op)op.controller=controller;},setTimer,clearTimer,now:projectNow});
    };
    if(importsEnabled!==true||projectsEnabled!==true||enabled!==true||disposed){try{await respond(503,{error:{code:'import_unavailable'}});}catch{try{res.destroy();}catch{}}return true;}
    const availability=()=>new ImportFailure(route?.method==='POST'&&op?.sent?504:op?.controller.signal.aborted?504:503,route?.method==='POST'&&op?.sent?'import_result_unknown':op?.controller.signal.aborted?'import_timeout':'import_unavailable');
    try {
      const started=projectNow(),wall=clock();if(!Number.isFinite(started))throw availability();
      route=importRoute(req.url,req.method);const cap=route.method==='POST'?55000:12000,deadline=wall+cap;
      let last=started;
      const remaining=()=>{const mono=projectNow(),current=clock();if(!Number.isFinite(mono)||mono<last)throw availability();last=mono;return Math.min(deadline-current,cap-(mono-started),session?session.expiresAt-current:cap,context?context.expiresAt-current:cap);};
      sendRemaining=remaining;
      if(!/^[1-9]\d{12}$/.test(String(deadline)))throw availability();
      for(const name of ['host','origin','x-cyomics-request','content-type','cookie','sec-fetch-site','content-length','transfer-encoding','content-encoding'])
        if(req.headers?.[name]!==undefined&&single(req,name)===undefined)fail('auth_origin_rejected',403);
      for(const name of ['authorization','x-user-id','x-owner-id','x-cyomics-remaining-ms','x-cyomics-deadline-unix-ms'])
        if(req.headers?.[name]!==undefined)fail('auth_origin_rejected',403);
      if(single(req,'host')!==expectedHost||req.headers?.origin!==undefined&&single(req,'origin')!==expected.origin
          ||req.headers?.['sec-fetch-site']!==undefined&&single(req,'sec-fetch-site')!=='same-origin'
          ||route.method==='POST'&&(single(req,'origin')!==expected.origin||single(req,'x-cyomics-request')!=='1'))fail('auth_origin_rejected',403);
      const cookies=jar(req);sweep(clock());browser=cookies[browserName];sessionId=cookies[sessionName];context=contexts.get(browser);session=sessions.get(sessionId);
      if(!context||!session||session.context!==context||context.closed||context.expiresAt<=clock()||session.expiresAt<=clock())fail('unauthenticated',401);
      const view=single(req,'x-cyomics-account-context');
      if(typeof view!=='string'||!(/^[A-Za-z0-9_-]{22}$/).test(view)||view!==session.viewNonce)fail('project_context_changed',409);
      if(active.size>=capacity||importActive>=2||importSubjects.has(session.userId))throw new ImportFailure(429,'import_busy');
      const time=clock();for(const [id,value]of importAttempts)if(value.until<=time)importAttempts.delete(id);
      const key=session.userId+':'+route.method,entry=importAttempts.get(key)??{until:time+(route.method==='POST'?600000:60000),count:0};
      if(entry.count>=(route.method==='POST'?10:60))throw new ImportFailure(429,'import_rate_limited');
      if(!importAttempts.has(key)&&importAttempts.size>=capacity)throw new ImportFailure(429,'import_busy');
      entry.count++;importAttempts.set(key,entry);importSubjects.add(session.userId);admitted=true;importActive++;total=true;
      const controller=new AbortController();op={controller,epoch:context.epoch,session,kind:'import',sent:false};active.add(op);context.operations.add(op);
      const abort=()=>controller.abort();req.once('aborted',abort);res.once('close',abort);op.cleanup=()=>{req.removeListener('aborted',abort);res.removeListener('close',abort);};
      checkLive=(allowEnded=false)=>{const current=clock();if((op.public401?sessions.has(sessionId):sessions.get(sessionId)!==session)||contexts.get(browser)!==context||session.context!==context||context.closed||session.expiresAt<=current||context.expiresAt<=current)fail('unauthenticated',401);
        if(context.epoch!==op.epoch)fail('project_context_changed',409);
        if(remaining()<=0){controller.abort();throw availability();}
        if(controller.signal.aborted||disposed||req.aborted||req.socket?.destroyed||res.socket?.destroyed||res.destroyed||res.writableEnded&&!allowEnded)throw availability();};
      let cancel;const cancelled=new Promise((_,reject)=>{cancel=()=>reject(availability());controller.signal.addEventListener('abort',cancel,{once:true});});op.cancelCleanup=()=>controller.signal.removeEventListener('abort',cancel);
      timer=setTimer(abort,Math.max(1,Math.ceil(remaining())));
      const run=async()=>{
        checkLive();
        if(route.method==='POST'&&!(/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i).test(single(req,'content-type')||''))throw new ImportFailure(415,'unsupported_media_type');
        if(![undefined,'identity'].includes(single(req,'content-encoding')))throw new ImportFailure(415,'unsupported_media_type');
        const length=single(req,'content-length'),transfer=single(req,'transfer-encoding');
        if(transfer!==undefined&&(transfer!=='chunked'||length!==undefined||route.method==='GET'))throw new ImportFailure(400,'invalid_import_request');
        if(length!==undefined){if(!(/^[0-9]{1,10}$/).test(length))throw new ImportFailure(400,'invalid_import_request');if(Number(length)>(route.method==='POST'?1024:0))throw new ImportFailure(route.method==='POST'?413:400,route.method==='POST'?'import_request_too_large':'invalid_import_request');}
        const raw=await readImportBody(req,controller.signal,route.method);checkLive();if(length!==undefined&&Number(length)!==raw.byteLength)throw new ImportFailure(400,'invalid_import_request');
        let body;
        if(route.method==='POST'){const input=importInput(raw);route.project_id=input.project_id;body=Buffer.from(JSON.stringify(input));}
        checkLive();return importBridge({route,body,accessToken:session.accessToken,signal:controller.signal,budget:{remaining,deadlineUnixMs:String(deadline)},onSent:()=>{op.sent=true;}});
      };
      const result=await Promise.race([run(),cancelled]);checkLive();
      if(sampleDesignsEnabled===true&&route.method==='GET'&&result.status===200)cacheSampleDesignSource(session,result.value);
      if(result.status===401){
        // Revoke synchronously, including every other operation, before sending
        // the public 401. Only this response drains under its original context,
        // epoch, expiry, controller and deadline; it cannot authorize new work.
        session.sampleDesignSources.clear();session.sampleDesignSourceBytes=0;sessions.delete(sessionId);op.public401=true;
        for(const previous of context.operations)if(previous!==op&&previous.session===session&&['project','me','upload','import','sample-design','raw-qc','raw-qc-artifact'].includes(previous.kind))previous.controller.abort();
        clearSession(res);
      }
      await respond(result.status,result.value);
    } catch(error) {
      if(op){try{checkLive(true);}catch(fenced){error=fenced;}}
      if(res.headersSent){try{res.destroy();}catch{}return true;}
      const fault=error instanceof Failure||error instanceof ImportFailure?error:availability();
      let value={error:{code:fault.code}},status=fault.status;
      try{validateImportError(status,value);}catch{status=503;value={error:{code:'import_unavailable'}};}
      try{await respond(status,value,true);}catch{try{res.destroy();}catch{}}
    } finally {
      clearTimer(timer);if(op){active.delete(op);context?.operations.delete(op);op.cleanup?.();op.cancelCleanup?.();op.controller.abort();}
      if(admitted)importSubjects.delete(session.userId);if(total)importActive--;
    }
    return true;
  }
  async function handleSampleDesign(req,res) {
    let route,op,context,session,sessionId,browser,timer,checkLive,sendRemaining;
    const respond=async(status,value,publicFailure=false)=>{
      if(req.aborted||res.destroyed||res.writableEnded)return;
      await sendSampleDesignJSONResponse(req,res,{status,value,signal:op&&!publicFailure?op.controller.signal:undefined,
        checkLive:op&&!publicFailure?()=>checkLive(true):undefined,remaining:sendRemaining,
        onController:controller=>{if(op)op.controller=controller;},setTimer,clearTimer,now:projectNow});
    };
    if(sampleDesignsEnabled!==true||projectsEnabled!==true||enabled!==true||disposed){try{await respond(503,{error:{code:'sample_design_unavailable'}});}catch{try{res.destroy();}catch{}}return true;}
    const availability=()=>new SampleDesignFailure(route?.method==='POST'&&op?.sent?504:op?.controller.signal.aborted?504:503,route?.method==='POST'&&op?.sent?'sample_design_result_unknown':op?.controller.signal.aborted?'sample_design_timeout':'sample_design_unavailable');
    try {
      const started=projectNow(),wall=clock();if(!Number.isFinite(started))throw availability();
      route=sampleDesignRoute(req.url,req.method);const cap=route.method==='POST'?55000:12000,deadline=wall+cap;
      let last=started;
      const remaining=()=>{const mono=projectNow(),current=clock();if(!Number.isFinite(mono)||mono<last)throw availability();last=mono;return Math.min(deadline-current,cap-(mono-started),session?session.expiresAt-current:cap,context?context.expiresAt-current:cap);};
      sendRemaining=remaining;
      if(!/^[1-9]\d{12}$/.test(String(deadline)))throw availability();
      for(const name of ['host','origin','x-cyomics-request','content-type','cookie','sec-fetch-site','content-length','transfer-encoding','content-encoding'])
        if(req.headers?.[name]!==undefined&&single(req,name)===undefined)fail('auth_origin_rejected',403);
      for(const name of ['authorization','x-user-id','x-owner-id','x-cyomics-remaining-ms','x-cyomics-deadline-unix-ms'])
        if(req.headers?.[name]!==undefined)fail('auth_origin_rejected',403);
      if(single(req,'host')!==expectedHost||req.headers?.origin!==undefined&&single(req,'origin')!==expected.origin
          ||req.headers?.['sec-fetch-site']!==undefined&&single(req,'sec-fetch-site')!=='same-origin'
          ||route.method==='POST'&&(single(req,'origin')!==expected.origin||single(req,'x-cyomics-request')!=='1'))fail('auth_origin_rejected',403);
      const cookies=jar(req);sweep(clock());browser=cookies[browserName];sessionId=cookies[sessionName];context=contexts.get(browser);session=sessions.get(sessionId);
      if(!context||!session||session.context!==context||context.closed||context.expiresAt<=clock()||session.expiresAt<=clock())fail('unauthenticated',401);
      const view=single(req,'x-cyomics-account-context');
      if(typeof view!=='string'||!(/^[A-Za-z0-9_-]{22}$/).test(view)||view!==session.viewNonce)fail('project_context_changed',409);
      if(active.size>=capacity)throw availability();
      const controller=new AbortController();op={controller,epoch:context.epoch,session,kind:'sample-design',sent:false};active.add(op);context.operations.add(op);
      const abort=()=>controller.abort();req.once('aborted',abort);res.once('close',abort);op.cleanup=()=>{req.removeListener('aborted',abort);res.removeListener('close',abort);};
      checkLive=(allowEnded=false)=>{const current=clock();if((op.public401?sessions.has(sessionId):sessions.get(sessionId)!==session)||contexts.get(browser)!==context||session.context!==context||context.closed||session.expiresAt<=current||context.expiresAt<=current){if(route.method==='POST'&&op.sent)throw availability();fail('unauthenticated',401);}
        if(context.epoch!==op.epoch){if(route.method==='POST'&&op.sent)throw availability();fail('project_context_changed',409);}
        if(remaining()<=0){controller.abort();throw availability();}
        if(controller.signal.aborted||disposed||req.aborted||req.socket?.destroyed||res.socket?.destroyed||res.destroyed||res.writableEnded&&!allowEnded)throw availability();};
      let cancel;const cancelled=new Promise((_,reject)=>{cancel=()=>reject(availability());controller.signal.addEventListener('abort',cancel,{once:true});});op.cancelCleanup=()=>controller.signal.removeEventListener('abort',cancel);
      timer=setTimer(abort,Math.max(1,Math.ceil(remaining())));
      const run=async()=>{
        checkLive();
        if(route.method==='POST'&&!(/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i).test(single(req,'content-type')||''))throw new SampleDesignFailure(415,'unsupported_media_type');
        if(![undefined,'identity'].includes(single(req,'content-encoding')))throw new SampleDesignFailure(415,'unsupported_media_type');
        const length=single(req,'content-length'),transfer=single(req,'transfer-encoding');
        if(transfer!==undefined&&(transfer!=='chunked'||length!==undefined||route.method==='GET'))throw new SampleDesignFailure(400,'invalid_sample_design_request');
        if(length!==undefined){if(!(/^[0-9]{1,10}$/).test(length))throw new SampleDesignFailure(400,'invalid_sample_design_request');if(Number(length)>(route.method==='POST'?MAX_REQUEST:0))throw new SampleDesignFailure(route.method==='POST'?413:400,route.method==='POST'?'sample_design_request_too_large':'invalid_sample_design_request');}
        const raw=await readSampleDesignBody(req,controller.signal,route.method);checkLive();if(length!==undefined&&Number(length)!==raw.byteLength)throw new SampleDesignFailure(400,'invalid_sample_design_request');
        const cached=session.sampleDesignSources.get(route.project_id+'|'+route.dataset_version_id);
        if(!cached){
          if(route.method!=='GET')throw new SampleDesignFailure(503,'sample_design_unavailable');
          checkLive();return sampleDesignErrorBridge({route,accessToken:session.accessToken,signal:controller.signal,budget:{remaining,deadlineUnixMs:String(deadline)},onSent:()=>{op.sent=true;}});
        }
        if(cached.source.project_id!==route.project_id)throw new SampleDesignFailure(503,'sample_design_unavailable');
        const source={dataset_version_id:cached.source.dataset_version_id,sample_columns:cached.source.sample_columns};
        let body;if(route.method==='POST'){const input=sampleDesignInput(raw,route,source);body=Buffer.from(JSON.stringify(input));}
        checkLive();return sampleDesignBridge({route,body,source,accessToken:session.accessToken,signal:controller.signal,budget:{remaining,deadlineUnixMs:String(deadline)},onSent:()=>{op.sent=true;}});
      };
      const result=await Promise.race([run(),cancelled]);checkLive();
      if(result.status===401){
        // Revoke synchronously, including every other operation, before sending
        // the public 401. Only this response drains under its original context,
        // epoch, expiry, controller and deadline; it cannot authorize new work.
        session.sampleDesignSources.clear();session.sampleDesignSourceBytes=0;sessions.delete(sessionId);op.public401=true;
        for(const previous of context.operations)if(previous!==op&&previous.session===session&&['project','me','upload','import','sample-design','raw-qc','raw-qc-artifact'].includes(previous.kind))previous.controller.abort();
        clearSession(res);
      }
      await respond(result.status,result.value);
    } catch(error) {
      if(op){try{checkLive(true);}catch(fenced){error=fenced;}}
      if(res.headersSent){try{res.destroy();}catch{}return true;}
      const fault=error instanceof Failure||error instanceof SampleDesignFailure?error:availability();
      let value={error:{code:fault.code}},status=fault.status;
      try{validateSampleDesignError(status,value);}catch{status=503;value={error:{code:'sample_design_unavailable'}};}
      try{await respond(status,value,true);}catch{try{res.destroy();}catch{}}
    } finally {
      clearTimer(timer);if(op){active.delete(op);context?.operations.delete(op);op.cleanup?.();op.cancelCleanup?.();op.controller.abort();}
    }
    return true;
  }
  async function handleRawQC(req,res) {
    let route,op,context,session,sessionId,browser,timer,checkLive,sendRemaining;
    const respond=async(status,value,publicFailure=false)=>{
      if(req.aborted||res.destroyed||res.writableEnded)return;
      await sendRawQCJSONResponse(req,res,{status,value,signal:op&&!publicFailure?op.controller.signal:undefined,
        checkLive:op&&!publicFailure?()=>checkLive(true):undefined,remaining:sendRemaining,
        onController:controller=>{if(op)op.controller=controller;},setTimer,clearTimer,now:projectNow});
    };
    if(rawQcEnabled!==true||typeof rawQcRequest!=='function'||projectsEnabled!==true||enabled!==true||disposed){try{await respond(503,{error:{code:'raw_qc_unavailable'}});}catch{try{res.destroy();}catch{}}return true;}
    const availability=()=>new RawQCBridgeFailure(route?.method==='POST'&&op?.sent?504:op?.controller.signal.aborted?504:503,route?.method==='POST'&&op?.sent?'raw_qc_submission_unknown':op?.controller.signal.aborted?'raw_qc_timeout':'raw_qc_unavailable');
    try {
      const started=projectNow(),wall=clock();if(!Number.isFinite(started))throw availability();
      route=rawQCRoute(req.url,req.method);const cap=RAW_QC_CAP,deadline=wall+cap;let last=started;
      const remaining=()=>{const mono=projectNow(),current=clock();if(!Number.isFinite(mono)||mono<last)throw availability();last=mono;return Math.min(deadline-current,cap-(mono-started),session?session.expiresAt-current:cap,context?context.expiresAt-current:cap);};
      sendRemaining=remaining;if(!/^[1-9]\d{12}$/.test(String(deadline)))throw availability();
      for(const name of ['host','origin','x-cyomics-request','content-type','cookie','sec-fetch-site','content-length','transfer-encoding','content-encoding','x-cyomics-account-context'])
        if(req.headers?.[name]!==undefined&&single(req,name)===undefined)fail('auth_origin_rejected',403);
      for(const name of ['authorization','x-user-id','x-owner-id','x-cyomics-remaining-ms','x-cyomics-deadline-unix-ms'])
        if(req.headers?.[name]!==undefined)fail('auth_origin_rejected',403);
      if(single(req,'host')!==expectedHost||req.headers?.origin!==undefined&&single(req,'origin')!==expected.origin
          ||req.headers?.['sec-fetch-site']!==undefined&&single(req,'sec-fetch-site')!=='same-origin'
          ||route.method==='POST'&&(single(req,'origin')!==expected.origin||single(req,'x-cyomics-request')!=='1'))fail('auth_origin_rejected',403);
      const cookies=jar(req);sweep(clock());browser=cookies[browserName];sessionId=cookies[sessionName];context=contexts.get(browser);session=sessions.get(sessionId);
      if(!context||!session||session.context!==context||context.closed||context.expiresAt<=clock()||session.expiresAt<=clock())fail('unauthenticated',401);
      const view=single(req,'x-cyomics-account-context');
      if(typeof view!=='string'||!(/^[A-Za-z0-9_-]{22}$/).test(view)||view!==session.viewNonce)fail('project_context_changed',409);
      if(active.size>=capacity)throw availability();
      const controller=new AbortController();op={controller,epoch:context.epoch,session,kind:'raw-qc',sent:false};active.add(op);context.operations.add(op);
      const abort=()=>controller.abort();req.once('aborted',abort);res.once('close',abort);op.cleanup=()=>{req.removeListener('aborted',abort);res.removeListener('close',abort);};
      checkLive=(allowEnded=false)=>{const current=clock();if((op.public401?sessions.has(sessionId):sessions.get(sessionId)!==session)||contexts.get(browser)!==context||session.context!==context||context.closed||session.expiresAt<=current||context.expiresAt<=current){if(route.method==='POST'&&op.sent)throw availability();fail('unauthenticated',401);}
        if(context.epoch!==op.epoch){if(route.method==='POST'&&op.sent)throw availability();fail('project_context_changed',409);}
        if(remaining()<=0){controller.abort();throw availability();}
        if(controller.signal.aborted||disposed||req.aborted||req.socket?.destroyed||res.socket?.destroyed||res.destroyed||res.writableEnded&&!allowEnded)throw availability();};
      let cancel;const cancelled=new Promise((_,reject)=>{cancel=()=>reject(availability());controller.signal.addEventListener('abort',cancel,{once:true});});op.cancelCleanup=()=>controller.signal.removeEventListener('abort',cancel);
      timer=setTimer(abort,Math.max(1,Math.ceil(remaining())));
      const run=async()=>{
        checkLive();if(route.method==='POST'&&!/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(single(req,'content-type')||''))throw new RawQCBridgeFailure(415,'unsupported_media_type');
        if(![undefined,'identity'].includes(single(req,'content-encoding')))throw new RawQCBridgeFailure(415,'unsupported_media_type');
        const length=single(req,'content-length'),transfer=single(req,'transfer-encoding');
        if(transfer!==undefined&&(transfer!=='chunked'||length!==undefined||route.method==='GET'))throw new RawQCBridgeFailure(400,'invalid_raw_qc_request');
        if(length!==undefined){if(!/^[0-9]{1,10}$/.test(length))throw new RawQCBridgeFailure(400,'invalid_raw_qc_request');if(Number(length)>(route.method==='POST'?1024:0))throw new RawQCBridgeFailure(route.method==='POST'?413:400,route.method==='POST'?'raw_qc_request_too_large':'invalid_raw_qc_request');}
        const raw=await readRawQCBody(req,controller.signal,route.method);checkLive();if(length!==undefined&&Number(length)!==raw.byteLength)throw new RawQCBridgeFailure(400,'invalid_raw_qc_request');
        const body=route.method==='POST'?Buffer.from(JSON.stringify(rawQCInput(raw))):undefined;
        checkLive();return rawQCBridge({route,body,accessToken:session.accessToken,signal:controller.signal,budget:{remaining,deadlineUnixMs:String(deadline)},onSent:()=>{op.sent=true;}});
      };
      const result=await Promise.race([run(),cancelled]);checkLive();
      if(result.status===401){
        session.sampleDesignSources.clear();session.sampleDesignSourceBytes=0;sessions.delete(sessionId);op.public401=true;
        for(const previous of context.operations)if(previous!==op&&previous.session===session&&['project','me','upload','import','sample-design','raw-qc','raw-qc-artifact'].includes(previous.kind))previous.controller.abort();
        clearSession(res);
      }
      await respond(result.status,result.value);
    } catch(error) {
      if(op){try{checkLive(true);}catch(fenced){error=fenced;}}
      if(res.headersSent){try{res.destroy();}catch{}return true;}
      const fault=error instanceof Failure||error instanceof RawQCBridgeFailure?error:availability();let status=fault.status,value={error:{code:fault.code}};
      try{rawQCError(status,value);}catch{status=503;value={error:{code:'raw_qc_unavailable'}};}
      try{await respond(status,value,true);}catch{try{res.destroy();}catch{}}
    } finally {clearTimer(timer);if(op){active.delete(op);context?.operations.delete(op);op.cleanup?.();op.cancelCleanup?.();op.controller.abort();}}
    return true;
  }
  // Reuse the existing raw-qc private-operation fence so unchanged logout/401
  // paths abort this operation too; purpose does not grant execution authority.
  async function handlePreprocessing(req,res,differentialInputReview=false,differentialJobs=false,differentialResults=false,deClassifications=false) {
    let route,op,context,session,sessionId,browser,timer,checkLive,sendRemaining;
    const unavailable=deClassifications?'de_classification_results_unavailable':differentialResults?'differential_results_unavailable':differentialJobs?'differential_unavailable':differentialInputReview?'differential_input_unavailable':'preprocessing_unavailable',invalid=deClassifications?'invalid_de_classification_request':differentialResults?'invalid_differential_result_request':differentialJobs?'invalid_differential_request':differentialInputReview?'invalid_differential_input':'invalid_preprocessing_request',tooLarge=differentialJobs?'differential_request_too_large':differentialInputReview?'differential_input_too_large':'preprocessing_request_too_large';
    const bodyCap=differentialJobs?8192:differentialInputReview?DIFFERENTIAL_INPUT_MAX:4096;
    const respond=async(status,value,publicFailure=false,artifact=null)=>{
      if(req.aborted||res.destroyed||res.writableEnded)return;
      await sendPreprocessJSONResponse(req,res,{status,value,artifact,signal:op&&!publicFailure?op.controller.signal:undefined,
        checkLive:op&&!publicFailure?()=>checkLive(true):undefined,remaining:sendRemaining,
        onController:controller=>{if(op)op.controller=controller;},setTimer,clearTimer,now:projectNow,differentialInputReview,differentialJobs,differentialResults,deClassifications});
    };
    if((deClassifications?(deClassificationsEnabled!==true||typeof deClassificationsRequest!=='function'):differentialResults?(differentialResultsEnabled!==true||typeof differentialResultsRequest!=='function'):differentialJobs?(differentialJobsEnabled!==true||typeof differentialJobsRequest!=='function'):differentialInputReview?(differentialInputEnabled!==true||typeof differentialInputRequest!=='function'):(preprocessingEnabled!==true||typeof preprocessingRequest!=='function'))||projectsEnabled!==true||enabled!==true||disposed){try{await respond(503,{error:{code:unavailable}});}catch{try{res.destroy();}catch{}}return true;}
    const availability=()=>deClassifications?new DEClassificationReadFailure(op?.controller.signal.aborted?504:503,op?.controller.signal.aborted?'de_classification_read_timeout':'de_classification_results_unavailable'):differentialResults?new DifferentialResultFailure(op?.controller.signal.aborted?504:503,op?.controller.signal.aborted?'differential_results_timeout':'differential_results_unavailable'):differentialJobs?new DifferentialJobFailure(route?.method==='POST'&&op?.sent||op?.controller.signal.aborted?504:503,route?.method==='POST'&&op?.sent?'differential_submission_unknown':op?.controller.signal.aborted?'differential_timeout':'differential_unavailable'):differentialInputReview?new DifferentialInputFailure():new PreprocessBridgeFailure(route?.method==='POST'&&op?.sent?504:op?.controller.signal.aborted?504:503,route?.method==='POST'&&op?.sent?'preprocessing_submission_unknown':op?.controller.signal.aborted?'preprocessing_timeout':'preprocessing_unavailable');
    try {
      const started=projectNow(),wall=clock();if(!Number.isFinite(started))throw availability();
      route=deClassifications?deClassificationReadRoute(req.url,req.method):differentialResults?differentialResultRoute(req.url,req.method):differentialJobs?differentialJobRoute(req.url,req.method):differentialInputReview?differentialInputRoute(req.url,req.method):preprocessRoute(req.url,req.method);const cap=PREPROCESS_CAP,deadline=wall+cap;let last=started;
      const remaining=()=>{const mono=projectNow(),current=clock();if(!Number.isFinite(mono)||mono<last)throw availability();last=mono;return Math.min(deadline-current,cap-(mono-started),session?session.expiresAt-current:cap,context?context.expiresAt-current:cap);};
      sendRemaining=remaining;if(!/^[1-9]\d{12}$/.test(String(deadline)))throw availability();
      if(!Array.isArray(req.rawHeaders)||req.rawHeaders.length%2)fail('auth_origin_rejected',403);
      const seenHeaders=new Set();
      for(let i=0;i<req.rawHeaders.length;i+=2){const key=req.rawHeaders[i],value=req.rawHeaders[i+1];
        if(typeof key!=='string'||typeof value!=='string'||! /^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(key)||/[\x00-\x1f\x7f]/.test(value))fail('auth_origin_rejected',403);
        const name=key.toLowerCase();if(seenHeaders.has(name)||req.headers?.[name]!==value)fail('auth_origin_rejected',403);seenHeaders.add(name);}
      for(const name of ['host','origin','x-cyomics-request','content-type','cookie','sec-fetch-site','content-length','transfer-encoding','content-encoding','x-cyomics-account-context'])
        if(req.headers?.[name]!==undefined&&single(req,name)===undefined)fail('auth_origin_rejected',403);
      for(const name of ['authorization','x-user-id','x-owner-id','x-cyomics-remaining-ms','x-cyomics-deadline-unix-ms'])
        if(req.headers?.[name]!==undefined)fail('auth_origin_rejected',403);
      if(single(req,'host')!==expectedHost||req.headers?.origin!==undefined&&single(req,'origin')!==expected.origin
          ||req.headers?.['sec-fetch-site']!==undefined&&single(req,'sec-fetch-site')!=='same-origin'
          ||route.method==='POST'&&(single(req,'origin')!==expected.origin||single(req,'x-cyomics-request')!=='1'))fail('auth_origin_rejected',403);
      const cookies=jar(req);sweep(clock());browser=cookies[browserName];sessionId=cookies[sessionName];context=contexts.get(browser);session=sessions.get(sessionId);
      if(!context||!session||session.context!==context||context.closed||context.expiresAt<=clock()||session.expiresAt<=clock())fail('unauthenticated',401);
      const view=single(req,'x-cyomics-account-context');
      if(typeof view!=='string'||!(/^[A-Za-z0-9_-]{22}$/).test(view)||view!==session.viewNonce)fail('project_context_changed',409);
      if(active.size>=capacity)throw availability();
      const controller=new AbortController();op={controller,epoch:context.epoch,session,kind:'raw-qc',purpose:deClassifications?'de-classifications':differentialResults?'differential-results':differentialJobs?'differential-jobs':differentialInputReview?'differential-input':'preprocessing',sent:false};active.add(op);context.operations.add(op);
      const abort=()=>controller.abort();req.once('aborted',abort);res.once('close',abort);op.cleanup=()=>{req.removeListener('aborted',abort);res.removeListener('close',abort);};
      checkLive=(allowEnded=false)=>{const current=clock();if((op.public401?sessions.has(sessionId):sessions.get(sessionId)!==session)||contexts.get(browser)!==context||session.context!==context||context.closed||session.expiresAt<=current||context.expiresAt<=current){if(!differentialInputReview&&route.method==='POST'&&op.sent)throw availability();fail('unauthenticated',401);}
        if(context.epoch!==op.epoch){if(!differentialInputReview&&route.method==='POST'&&op.sent)throw availability();fail('project_context_changed',409);}
        if(remaining()<=0){controller.abort();throw availability();}
        if(controller.signal.aborted||disposed||req.aborted||req.socket?.destroyed||res.socket?.destroyed||res.destroyed||res.writableEnded&&!allowEnded)throw availability();};
      let cancel;const cancelled=new Promise((_,reject)=>{cancel=()=>reject(availability());controller.signal.addEventListener('abort',cancel,{once:true});});op.cancelCleanup=()=>controller.signal.removeEventListener('abort',cancel);
      timer=setTimer(abort,Math.max(1,Math.ceil(remaining())));
      const run=async()=>{
        checkLive();if(route.method==='POST'&&!/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(single(req,'content-type')||''))throw new PreprocessBridgeFailure(415,'unsupported_media_type');
        if(![undefined,'identity'].includes(single(req,'content-encoding')))throw new PreprocessBridgeFailure(415,'unsupported_media_type');
        const length=single(req,'content-length'),transfer=single(req,'transfer-encoding');
        if(transfer!==undefined&&(transfer!=='chunked'||length!==undefined||route.method==='GET'))throw new PreprocessBridgeFailure(400,invalid);
        if(length!==undefined){if(!/^[0-9]{1,10}$/.test(length))throw new PreprocessBridgeFailure(400,invalid);if(Number(length)>(route.method==='POST'?bodyCap:0))throw new PreprocessBridgeFailure(route.method==='POST'?413:400,route.method==='POST'?tooLarge:invalid);}
        const raw=await readPreprocessBody(req,controller.signal,route.method,differentialInputReview,differentialJobs,differentialResults,deClassifications);checkLive();if(length!==undefined&&Number(length)!==raw.byteLength)throw new PreprocessBridgeFailure(400,invalid);
        const body=route.method==='POST'?Buffer.from(JSON.stringify(differentialJobs?differentialJobInput(raw):differentialInputReview?differentialInput(raw):preprocessInput(raw))):undefined;
        checkLive();return (deClassifications?deClassificationsBridge:differentialResults?differentialResultsBridge:differentialJobs?differentialJobsBridge:differentialInputReview?differentialInputBridge:preprocessBridge)({route,body,accessToken:session.accessToken,ownerId:session.userId,signal:controller.signal,budget:{remaining,deadlineUnixMs:String(Math.floor(Math.min(deadline,session.expiresAt,context.expiresAt)))},onSent:()=>{op.sent=true;}});
      };
      const result=await Promise.race([run(),cancelled]);checkLive();
      if(result.status===401){
        session.sampleDesignSources.clear();session.sampleDesignSourceBytes=0;sessions.delete(sessionId);op.public401=true;
        for(const previous of context.operations)if(previous!==op&&previous.session===session&&['project','me','upload','import','sample-design','raw-qc','raw-qc-artifact'].includes(previous.kind))previous.controller.abort();
        clearSession(res);
      }
      await respond(result.status,result.value,false,result.artifact??null);
    } catch(error) {
      if(op){try{checkLive(true);}catch(fenced){error=fenced;}}
      if(res.headersSent){try{res.destroy();}catch{}return true;}
      const fault=error instanceof Failure||error instanceof PreprocessBridgeFailure||error instanceof DifferentialInputFailure||error instanceof DifferentialJobFailure||error instanceof DifferentialResultFailure?error:availability();let status=fault.status,value={error:{code:fault.code}};
      try{if(deClassifications)deClassificationReadError(status,value);else if(differentialResults)differentialResultError(status,value);else if(differentialJobs)differentialJobError(status,value);else if(differentialInputReview)differentialInputError(status,value);else preprocessError(status,value);}catch{status=503;value={error:{code:unavailable}};}
      try{await respond(status,value,true);}catch{try{res.destroy();}catch{}}
    } finally {clearTimer(timer);if(op){active.delete(op);context?.operations.delete(op);op.cleanup?.();op.cancelCleanup?.();op.controller.abort();}}
    return true;
  }
  async function handleRawQCArtifact(req,res) {
    // Keep default-off requests inert, and retain the old job route's 404
    // behavior when the separate artifact feature is not enabled.
    if(rawQcArtifactsEnabled!==true){reply(req,res,404,{error:{code:'not_found'}});return true;}
    if(rawQcEnabled!==true||projectsEnabled!==true||enabled!==true||disposed||typeof rawQcArtifactRequest!=='function'){
      reply(req,res,503,{error:{code:'raw_qc_unavailable'}});return true;
    }
    let lease,controller,timer,op,context,session,sessionId,browser,route,checkLive,remaining,baseLive,detach;
    const unavailable=()=>new ArtifactFailure(503,'raw_qc_unavailable');
    try{
      const started=projectNow(),wall=clock(),deadline=wall+ARTIFACT_TOTAL_MS;let last=started;
      if(!Number.isFinite(started)||!/^[1-9]\d{12}$/.test(String(deadline)))throw unavailable();
      remaining=()=>{const mono=projectNow(),current=clock();if(!Number.isFinite(mono)||mono<last)throw unavailable();last=mono;
        return Math.min(deadline-current,ARTIFACT_TOTAL_MS-(mono-started),session?session.expiresAt-current:ARTIFACT_TOTAL_MS,context?context.expiresAt-current:ARTIFACT_TOTAL_MS);};
      route=artifactRoute(req.url,req.method);
      // Inspect both views before cookie/session access. Never forward any
      // caller identity, condition, body, budget or extra request header.
      if(!Array.isArray(req.rawHeaders)||req.rawHeaders.length%2)fail('auth_origin_rejected',403);
      const seen=new Set();
      for(let i=0;i<req.rawHeaders.length;i+=2){
        const raw=req.rawHeaders[i],value=req.rawHeaders[i+1];
        if(typeof raw!=='string'||typeof value!=='string'||/[\r\n\0]/.test(raw+value))fail('auth_origin_rejected',403);
        const name=raw.toLowerCase();if(seen.has(name)||req.headers?.[name]!==value)fail('auth_origin_rejected',403);seen.add(name);
      }
      for(const name of Object.keys(req.headers??{})){
        if(['authorization','proxy-authorization','apikey','x-user-id','x-owner-id','x-auth-token','x-access-token','x-cyomics-user-id','x-cyomics-owner-id','x-cyomics-remaining-ms','x-cyomics-deadline-unix-ms'].includes(name))fail('auth_origin_rejected',403);
        if(name==='range'||name.startsWith('if-'))throw new ArtifactFailure(400,'invalid_raw_qc_request');
      }
      for(const name of ['host','origin','cookie','sec-fetch-site','content-length','transfer-encoding','content-encoding','x-cyomics-account-context'])
        if(req.headers?.[name]!==undefined&&single(req,name)===undefined)fail('auth_origin_rejected',403);
      if(single(req,'host')!==expectedHost||req.headers?.origin!==undefined&&single(req,'origin')!==expected.origin
        ||req.headers?.['sec-fetch-site']!==undefined&&single(req,'sec-fetch-site')!=='same-origin')fail('auth_origin_rejected',403);
      if(req.headers?.['transfer-encoding']!==undefined||![undefined,'0'].includes(single(req,'content-length')))
        throw new ArtifactFailure(400,'invalid_raw_qc_request');
      if(![undefined,'identity'].includes(single(req,'content-encoding')))throw new ArtifactFailure(415,'unsupported_media_type');
      lease=artifactLeases.acquire();controller=new AbortController();lease.own(req);lease.own(res);
      const abort=()=>controller.abort(),closed=()=>{if(!res.writableFinished)abort();};
      req.once('aborted',abort);res.once('close',closed);controller.signal.addEventListener('abort',()=>lease.abort(),{once:true});
      detach=()=>{req.removeListener('aborted',abort);res.removeListener('close',closed);};
      baseLive=(allowEnded=false)=>{
        if(remaining()<=0){controller.abort();throw new ArtifactFailure(504,'raw_qc_timeout');}
        const finished=allowEnded&&res.writableFinished===true;
        if(controller.signal.aborted||disposed||!finished&&(req.aborted||req.socket?.destroyed||res.socket?.destroyed||res.destroyed||res.writableEnded&&!allowEnded))throw unavailable();
      };
      checkLive=baseLive;
      timer=setTimer(abort,Math.max(1,Math.ceil(remaining())));
      if(active.size>=capacity)throw unavailable();
      op={controller,session:undefined,kind:'raw-qc-artifact'};active.add(op);
      const cookies=jar(req);sweep(clock());browser=cookies[browserName];sessionId=cookies[sessionName];context=contexts.get(browser);session=sessions.get(sessionId);
      if(!context||!session||session.context!==context||context.closed||context.expiresAt<=clock()||session.expiresAt<=clock())fail('unauthenticated',401);
      const view=single(req,'x-cyomics-account-context');
      if(typeof view!=='string'||!(/^[A-Za-z0-9_-]{22}$/).test(view)||view!==session.viewNonce)fail('project_context_changed',409);
      op.epoch=context.epoch;op.session=session;op.viewNonce=view;context.operations.add(op);
      checkLive=(allowEnded=false)=>{
        const current=clock();
        if((op.public401?sessions.has(sessionId):sessions.get(sessionId)!==session)||contexts.get(browser)!==context
          ||session.context!==context||context.closed||session.expiresAt<=current||context.expiresAt<=current)fail('unauthenticated',401);
        if(context.epoch!==op.epoch||session.viewNonce!==op.viewNonce)fail('project_context_changed',409);
        baseLive(allowEnded);
      };
      clearTimer(timer);timer=setTimer(abort,Math.max(1,Math.ceil(remaining())));
      checkLive();await readArtifactRequest(req,{signal:controller.signal,checkLive});checkLive();
      const hint=Math.floor(Math.min(ARTIFACT_UPSTREAM_MS,remaining()));
      const upstreamDeadline=Math.floor(Math.min(clock()+hint,deadline,session.expiresAt,context.expiresAt));
      const result=await artifactBridge({route,accessToken:session.accessToken,signal:controller.signal,
        budget:{remaining:()=>Math.min(hint,remaining(),upstreamDeadline-clock()),deadlineUnixMs:String(upstreamDeadline)},checkLive,lease});
      checkLive();
      if(result.status===401){
        session.sampleDesignSources.clear();session.sampleDesignSourceBytes=0;sessions.delete(sessionId);op.public401=true;
        for(const previous of context.operations)if(previous!==op&&previous.session===session&&['project','me','upload','import','sample-design','raw-qc','raw-qc-artifact'].includes(previous.kind))previous.controller.abort();
        clearSession(res);
      }
      await sendArtifactResponse(res,{result,signal:controller.signal,checkLive,lease});
    }catch(error){
      if(lease&&!baseLive)controller?.abort();
      if(op){try{checkLive(true);}catch(fenced){error=fenced;}}
      if(res.headersSent){try{lease?.abort();res.destroy();}catch{}return true;}
      let status=error instanceof Failure||error instanceof RawQCBridgeFailure?error.status:503;
      let value={error:{code:error instanceof Failure||error instanceof RawQCBridgeFailure?error.code:'raw_qc_unavailable'}};
      try{artifactError(status,value);}catch{status=503;value={error:{code:'raw_qc_unavailable'}};}
      if(lease&&controller){
        try{await sendArtifactResponse(res,{result:{status,value},signal:controller.signal,checkLive:baseLive??(()=>{}),lease});}
        catch{try{lease.abort();res.destroy();}catch{}}
      }else reply(req,res,status,value);
    }finally{
      if(lease){lease.abort();await lease.finish(controller?.signal);}
      clearTimer(timer);detach?.();
      if(op){active.delete(op);context?.operations.delete(op);op.controller.abort();}
      else controller?.abort();
    }
    return true;
  }
  runtime.dispose = () => {
    disposed = true; clearInterval(interval);
    for (const op of active) op.controller.abort();
    for (const context of contexts.values()) close(context);
    contexts.clear(); sessions.clear(); sources.clear(); uploadAttempts.clear(); importAttempts.clear();
  };
  return runtime;
}
