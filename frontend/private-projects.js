import {createRawTransport, createPrivateTxtController, mountPrivateTxt} from './private-txt-upload.js';
import {clearRawQCRecovery,createQCArtifactScope} from './raw-qc-client.js';
import {isVerifiedSession} from './auth-login.js';

const fields = ['project_id', 'name', 'description', 'species', 'visibility', 'createdAt', 'updatedAt'];
const exact = (value, keys) => value !== null && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const scalar = value => typeof value === 'string' && !/[\uD800-\uDFFF]/u.test(value);
const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
const cursorValid = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,512}$/.test(value);
const utf8 = value => new TextEncoder().encode(value).byteLength;
const control = /[\u0000-\u001f\u007f-\u009f]/u;
const errorIs = (body, code) => exact(body, ['error']) && exact(body.error, ['code']) && body.error.code === code;
function timeValid(value) {
  return typeof value === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(value)
    && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 19) === value.slice(0, 19);
}
export function validateProjectInput(input) {
  const errors = {};
  if (!input || typeof input !== 'object' || Array.isArray(input) || !Object.hasOwn(input, 'name')
    || Object.keys(input).some(key => !['name', 'description', 'species'].includes(key))) return {form: 'invalid_project'};
  for (const key of ['name', 'description', 'species']) {
    const value = Object.hasOwn(input, key) ? input[key] : '';
    if (!scalar(value)) { errors[key] = 'invalid_' + key; continue; }
    const text = value.trim(), size = [...text].length;
    if (key === 'name' && (!size || size > 80 || control.test(text))) errors.name = 'invalid_name';
    if (key === 'species' && (size > 200 || control.test(text))) errors.species = 'invalid_species';
    if (key === 'description' && (size > 1000 || value.includes('\0'))) errors.description = 'invalid_description';
  }
  if (Object.values(input).every(value => typeof value === 'string') && utf8(JSON.stringify(input)) > 8192) errors.form = 'project_body_too_large';
  return errors;
}
export function isProject(value) {
  return exact(value, fields) && uuid(value.project_id) && value.visibility === 'private'
    && !Object.keys(validateProjectInput({name: value.name, description: value.description, species: value.species})).length
    && ['name', 'description', 'species'].every(key => value[key] === value[key].trim())
    && timeValid(value.createdAt) && timeValid(value.updatedAt) && Date.parse(value.updatedAt) >= Date.parse(value.createdAt);
}
export function isProjectList(value) {
  return exact(value, ['items', 'nextCursor']) && Array.isArray(value.items) && value.items.length <= 50
    && value.items.every(isProject) && new Set(value.items.map(item => item.project_id)).size === value.items.length
    && (value.nextCursor === null || cursorValid(value.nextCursor));
}
const sameProject = (left, right) => fields.every(key => left[key] === right[key]);
const projectReply = response => response.status === 200 && exact(response.body, ['project']) && isProject(response.body.project);
export function createPrivateProjectTransport({enabled = false, sampleDesignsEnabled = false, rawQcEnabled = false, rawQcArtifactsEnabled = false, rawQcStorage, preprocessingEnabled = false, preprocessingStorage, fetchImpl = globalThis.fetch} = {}) {
  if (enabled !== true) return null;
  let accountContext = null, revision = 0, lastVerifiedContext = null;
  let preprocessingAuthority = preprocessingEnabled === true ? new AbortController() : null;
  const preprocessingScopes = new Map();
  const preprocessingArtifactScope = preprocessingEnabled === true ? createQCArtifactScope() : null;
  const clearPreprocessingScopes = () => { for (const [scope, recovery] of preprocessingScopes) recovery.clear(scope); preprocessingScopes.clear(); };
  const artifactScope = rawQcEnabled === true && rawQcArtifactsEnabled === true ? createQCArtifactScope() : null;
  const forgetContext = ({confirmed = false} = {}) => {
    if (confirmed && rawQcEnabled === true) clearRawQCRecovery(rawQcStorage);
    accountContext = null; revision++; artifactScope?.cancelAll();
    if (preprocessingAuthority) {
      const old = preprocessingAuthority; preprocessingAuthority = new AbortController(); old.abort(); preprocessingArtifactScope?.cancelAll();
      if (confirmed) clearPreprocessingScopes();
    }
  };
  const options = signal => ({method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal});
  const projectOptions = signal => {
    if (!accountContext) throw Error('project_context_changed');
    return {...options(signal), headers: {'X-Cyomics-Account-Context': accountContext}};
  };
  return {
    forgetContext,
    rawFiles(projectId) {
      const binding = revision;
      return createRawTransport({projectId, fetchImpl, getOptions: projectOptions, sampleDesignsEnabled, rawQcEnabled, rawQcArtifactsEnabled, artifactScope, rawQcStorage,
        preprocessingEnabled, preprocessingStorage, preprocessingArtifactScope, preprocessingContextSignal: preprocessingAuthority?.signal || null,
        onPreprocessingScope(scope, recovery) { if (accountContext !== null && revision === binding) preprocessingScopes.set(scope, recovery); },
        isCurrent: () => accountContext !== null && revision === binding});
    },
    hasContext: () => accountContext !== null,
    async me({signal}) {
      forgetContext(); const current = revision;
      try {
        const response = await fetchImpl('/api/v1/me', options(signal));
        if (current === revision && !signal.aborted) {
          const header = response.status === 200 && !response.redirected ? response.headers.get('X-Cyomics-Account-Context') : null;
          accountContext = typeof header === 'string' && /^[A-Za-z0-9_-]{22}$/.test(header) ? header : null;
          if (accountContext) {
            if (rawQcEnabled === true && lastVerifiedContext && lastVerifiedContext !== accountContext) clearRawQCRecovery(rawQcStorage);
            if (preprocessingEnabled === true && lastVerifiedContext && lastVerifiedContext !== accountContext) preprocessingScopes.clear();
            lastVerifiedContext = accountContext;
          }
        }
        return response;
      } catch (error) { if (current === revision) forgetContext(); throw error; }
    },
    list(cursor, {signal}) {
      if (cursor !== null && !cursorValid(cursor)) throw Error('invalid_project_query');
      return fetchImpl('/api/v1/projects?limit=20' + (cursor === null ? '' : '&cursor=' + encodeURIComponent(cursor)), projectOptions(signal));
    },
    detail(projectId, {signal}) {
      if (!uuid(projectId)) throw Error('invalid_project');
      return fetchImpl('/api/v1/projects/' + projectId, projectOptions(signal));
    },
    create(input, {signal}) {
      if (Object.keys(validateProjectInput(input)).length) throw Error('invalid_project');
      const body = JSON.stringify(Object.fromEntries(['name', 'description', 'species'].map(key => [key, Object.hasOwn(input, key) ? input[key].trim() : ''])));
      if (utf8(body) > 8192) throw Error('project_body_too_large');
      const request = projectOptions(signal);
      return fetchImpl('/api/v1/projects', {...request, method: 'POST', headers: {...request.headers, 'Content-Type': 'application/json', 'X-Cyomics-Request': '1'}, body});
    },
  };
}
const initial = () => ({identity: 'unknown', email: null, phase: 'idle', items: [], nextCursor: null, selected: null,
  detailPhase: 'idle', detailCode: null, dialogOpen: false, draft: {name: '', description: '', species: ''}, errors: {},
  createPhase: 'idle', createCode: null, createBlocked: false, createdProjectId: null, code: null, ready: false});
const errorsByStatus = {400: ['invalid_project', 'invalid_project_query'], 401: ['unauthenticated'], 403: ['auth_origin_rejected'],
  404: ['project_not_found', 'not_found'], 405: ['method_not_allowed'], 409: ['project_context_changed'], 413: ['project_body_too_large'], 415: ['unsupported_media_type'],
  429: ['project_rate_limited'], 503: ['projects_unavailable'], 504: ['project_create_result_unknown']};
function fixedError(response) { return (errorsByStatus[response.status] || []).find(code => errorIs(response.body, code)) || null; }

export function createPrivateProjectController({transport = null, setTimer = setTimeout, clearTimer = clearTimeout} = {}) {
  let state = initial(), main = null, detail = null, started = false, disposed = false;
  let rawFiles = null;
  function stopFiles() { const old = rawFiles; rawFiles = null; old?.dispose(); }
  function bindFiles(project) {
    stopFiles();
    rawFiles = createPrivateTxtController({projectId: project.project_id, transport: transport?.rawFiles?.(project.project_id) || null,
      onDenied(response) { if (!denied(response)) invalidate('unknown', 'projects_unavailable'); }});
  }
  const listeners = new Set(), emit = () => { for (const listener of listeners) listener(); };
  const update = patch => { state = {...state, ...patch}; emit(); };
  const active = op => !disposed && (main === op || detail === op);
  function cancel(op) {
    if (!op) return;
    clearTimer(op.timer); op.controller.abort();
    try { void (op.reader ? op.reader.cancel() : op.response?.body?.cancel())?.catch(() => {}); } catch {}
  }
  function begin(slot) {
    const op = {controller: new AbortController(), timer: null, response: null, reader: null};
    op.cancelled = new Promise((_, reject) => op.controller.signal.addEventListener('abort', () => reject(Error('unavailable')), {once: true}));
    void op.cancelled.catch(() => {});
    if (slot === 'main') main = op; else detail = op;
    return op;
  }
  function stopDetail() { const old = detail; detail = null; cancel(old); }
  function finish(op) {
    if (!active(op)) return;
    if (main === op) main = null; if (detail === op) detail = null; cancel(op); emit();
  }
  function invalidate(identity, code, confirmed = false) {
    stopFiles();
    const old = main; main = null; cancel(old); stopDetail(); transport?.forgetContext?.({confirmed: confirmed || identity === 'anonymous' || ['project_context_changed', 'auth_origin_rejected'].includes(code)}); state = {...initial(), identity, phase: identity === 'anonymous' ? 'anonymous' : 'unavailable', code}; emit();
  }
  function unconfirmedCreate() {
    invalidate('unknown', 'project_create_result_unknown');
    update({createPhase: 'unknown', createCode: 'project_create_result_unknown', createBlocked: true, ready: false});
  }
  function denied(response) {
    if (response.status === 401) { invalidate(errorIs(response.body, 'unauthenticated') ? 'anonymous' : 'unknown', errorIs(response.body, 'unauthenticated') ? 'unauthenticated' : 'session_unavailable', true); return true; }
    if (response.status === 403) { invalidate('unknown', 'auth_origin_rejected'); return true; }
    if (response.status === 409) {
      const changed = errorIs(response.body, 'project_context_changed'); invalidate('unknown', changed ? 'project_context_changed' : 'projects_unavailable');
      if (changed) void refresh(true);
      return true;
    }
    return false;
  }
  async function request(op, kind, input) {
    op.response = null;
    op.timer = setTimer(() => cancel(op), 15000);
    try {
      const pending = kind === 'me' ? transport.me({signal: op.controller.signal}) : transport[kind](input, {signal: op.controller.signal});
      const arrived = Promise.resolve(pending).then(response => {
        op.response = response;
        if (!active(op) || op.controller.signal.aborted) { cancel(op); throw Error('unavailable'); }
        return response;
      });
      const response = await Promise.race([arrived, op.cancelled]);
      if (response.redirected || !/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(response.headers.get('content-type') || '')) throw Error('unavailable');
      const cap = kind === 'me' ? 4096 : kind === 'list' ? 512 * 1024 : 16384, length = response.headers.get('content-length');
      if (length !== null && (!/^\d+$/.test(length) || Number(length) > cap)) throw Error('unavailable');
      const read = async () => {
        const reader = response.body?.getReader(); if (!reader) throw Error('unavailable'); op.reader = reader;
        const chunks = []; let size = 0;
        try {
          while (active(op) && !op.controller.signal.aborted) {
            const part = await reader.read(); if (!active(op) || op.controller.signal.aborted) throw Error('unavailable');
            if (part.done) break;
            size += part.value.byteLength; if (size > cap) throw Error('unavailable'); chunks.push(part.value);
          }
          if (!active(op) || op.controller.signal.aborted) throw Error('unavailable');
          const bytes = new Uint8Array(size); let offset = 0; for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
          return JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes));
        } finally {
          try { void reader.cancel().catch(() => {}); } catch {}
          try { reader.releaseLock(); } catch {}
          if (op.reader === reader) op.reader = null;
        }
      };
      return {status: response.status, body: await Promise.race([read(), op.cancelled])};
    } finally { clearTimer(op.timer); op.timer = null; }
  }
  async function refresh(identityOnly = false) {
    if (disposed || main) return false;
    stopFiles(); started = true; stopDetail(); state = {...initial(), phase: 'loading'};
    if (!transport) { invalidate('unknown', 'projects_unavailable'); return false; }
    const op = begin('main'); emit();
    try {
      const me = await request(op, 'me'); if (!active(op)) return false;
      if (me.status === 401 && errorIs(me.body, 'unauthenticated')) { invalidate('anonymous', 'unauthenticated'); return false; }
      if (me.status !== 200 || !isVerifiedSession(me.body) || transport.hasContext?.() !== true) { invalidate('unknown', 'session_unavailable', [401, 403].includes(me.status)); return false; }
      update({identity: 'authenticated', email: me.body.user.email});
      if (identityOnly) { update({phase: 'identity_changed', code: 'project_context_changed'}); return true; }
      const page = await request(op, 'list', null); if (!active(op)) return false;
      if (denied(page)) return false;
      if (page.status !== 200 || !isProjectList(page.body)) { invalidate('unknown', fixedError(page) || 'projects_unavailable'); return false; }
      update({phase: 'ready', items: page.body.items, nextCursor: page.body.nextCursor, ready: true}); return true;
    } catch { if (active(op)) invalidate('unknown', 'projects_unavailable'); return false; } finally { finish(op); }
  }
  async function more() {
    if (disposed || main || !state.ready || state.identity !== 'authenticated' || !state.nextCursor) return false;
    const cursor = state.nextCursor, op = begin('main'); update({phase: 'loading_more', code: null});
    try {
      const page = await request(op, 'list', cursor); if (!active(op)) return false;
      if (denied(page)) return false;
      if (page.status !== 200 || !isProjectList(page.body) || page.body.nextCursor === cursor) { invalidate('unknown', fixedError(page) || 'projects_unavailable'); return false; }
      const merged = new Map(state.items.map(item => [item.project_id, item])); for (const item of page.body.items) merged.set(item.project_id, item);
      update({phase: 'ready', items: [...merged.values()], nextCursor: page.body.nextCursor}); return true;
    } catch { if (active(op)) invalidate('unknown', 'projects_unavailable'); return false; } finally { finish(op); }
  }
  async function select(projectId) {
    if (disposed || main || state.identity !== 'authenticated' || !state.ready) return false;
    stopFiles(); stopDetail(); update({selected: null, detailPhase: 'loading', detailCode: null});
    if (!uuid(projectId)) { update({detailPhase: 'failed', detailCode: 'project_not_found'}); return false; }
    const op = begin('detail');
    try {
      const response = await request(op, 'detail', projectId); if (!active(op)) return false;
      if (denied(response)) return false;
      if (!projectReply(response) || response.body.project.project_id !== projectId) {
        if (response.status === 404 && fixedError(response)) update({detailPhase: 'failed', detailCode: 'project_not_found'});
        else invalidate('unknown', fixedError(response) || 'projects_unavailable');
        return false;
      }
      bindFiles(response.body.project); update({selected: response.body.project, detailPhase: 'ready'}); return true;
    } catch { if (active(op)) invalidate('unknown', 'projects_unavailable'); return false; } finally { finish(op); }
  }
  async function create(input) {
    if (disposed || main || !transport || !state.ready || state.identity !== 'authenticated' || state.createBlocked) return false;
    const errors = validateProjectInput(input);
    if (Object.keys(errors).length) { update({errors, createPhase: 'invalid', createCode: null}); return false; }
    stopFiles(); stopDetail(); const op = begin('main'); update({createPhase: 'submitting', createCode: null, createdProjectId: null, errors: {}, selected: null, detailPhase: 'idle'});
    try {
      const response = await request(op, 'create', input); if (!active(op)) return false;
      if (denied(response)) return false;
      if (response.status !== 201 || !exact(response.body, ['project']) || !isProject(response.body.project)) {
        if (response.status === 503) { invalidate('unknown', 'projects_unavailable'); return false; }
        const code = fixedError(response), known = [400, 413, 415, 429, 503].includes(response.status) && code;
        if (known) update({createPhase: 'failed', createCode: code, createBlocked: false}); else unconfirmedCreate();
        return false;
      }
      const projectId = response.body.project.project_id;
      update({createPhase: 'confirming', items: [], nextCursor: null});
      const readback = await request(op, 'detail', projectId); if (!active(op)) return false;
      if (denied(readback)) return false;
      if (!projectReply(readback) || readback.body.project.project_id !== projectId) throw Error('unconfirmed');
      const page = await request(op, 'list', null); if (!active(op)) return false;
      if (denied(page)) return false;
      if (page.status !== 200 || !isProjectList(page.body)) throw Error('unconfirmed');
      const found = page.body.items.find(item => item.project_id === projectId);
      update({items: page.body.items, nextCursor: page.body.nextCursor});
      if (!found || !sameProject(found, readback.body.project)) throw Error('unconfirmed');
      bindFiles(readback.body.project); update({phase: 'ready', selected: readback.body.project, detailPhase: 'ready', dialogOpen: false, draft: initial().draft,
        createPhase: 'created', createCode: null, createBlocked: false, createdProjectId: projectId}); return true;
    } catch { if (active(op)) unconfirmedCreate(); return false; } finally { finish(op); }
  }
  return {refresh, more, select, create, files: () => rawFiles,
    start() { if (!started && !disposed) return refresh(); return Promise.resolve(false); },
    openDialog() { if (!disposed && !main && state.ready && state.identity === 'authenticated') update({dialogOpen: true, errors: {}, ...(state.createBlocked ? {} : {createPhase: 'idle', createCode: null})}); },
    closeDialog() { if (!disposed && !main) update({dialogOpen: false, draft: initial().draft, errors: {}}); },
    edit(field, value) {
      if (disposed || main || !state.dialogOpen || !['name', 'description', 'species'].includes(field)) return;
      const draft = {...state.draft, [field]: value}, validation = validateProjectInput(draft), errors = {...state.errors};
      if (errors[field] && !validation[field]) delete errors[field]; if (errors.form && !validation.form) delete errors.form;
      update({draft, errors, ...(state.createPhase === 'invalid' && !Object.keys(errors).length ? {createPhase: 'idle', createCode: null} : {})});
    },
    status() { return {...state, items: state.items.map(item => ({...item})), selected: state.selected ? {...state.selected} : null, draft: {...state.draft}, errors: {...state.errors}, busy: Boolean(main),
      canCreate: Boolean(transport) && state.ready && state.identity === 'authenticated' && !main && !state.createBlocked}; },
    subscribe(listener) { if (disposed) return () => {}; listeners.add(listener); return () => listeners.delete(listener); },
    dispose() { if (disposed) return; disposed = true; stopFiles(); const old = main; main = null; cancel(old); stopDetail(); transport?.forgetContext?.(); state = initial(); listeners.clear(); },
  };
}
export function createPrivateProjectRoutes(options = {}) {
  let route = null, controller = null;
  return {
    enter(nextRoute) { const next = nextRoute === '/workspace/projects' ? nextRoute : null; if (route !== next) { controller?.dispose(); controller = next ? createPrivateProjectController(options) : null; route = next; } return controller; },
    current() { return controller; }, dispose() { controller?.dispose(); controller = null; route = null; },
  };
}

function message(code, t) {
  const labels = {
    invalid_name: ['请填写项目名称，最多 80 个字符，不能包含控制字符。', 'Enter a project name of up to 80 characters, without control characters.'],
    invalid_description: ['描述最多 1000 个字符，不能包含无效字符。', 'Use up to 1,000 characters without invalid characters for the description.'],
    invalid_species: ['物种最多 200 个字符，不能包含控制字符。', 'Use up to 200 characters without control characters for the species.'],
    invalid_project: ['请检查项目名称、描述和物种。', 'Check the project name, description and species.'],
    project_body_too_large: ['输入内容过长，请缩短后再试。', 'The input is too long. Shorten it before trying again.'],
    unauthenticated: ['请先登录并验证邮箱后访问私人项目。', 'Sign in with a verified email to access private projects.'],
    session_unavailable: ['暂时无法确认登录身份，请重新载入。', 'Your identity could not be confirmed. Please reload.'],
    auth_origin_rejected: ['当前页面无权访问私人项目，请从平台入口重新打开。', 'This page cannot access private projects. Reopen the platform entry page.'],
    project_not_found: ['无法访问此项目，项目不存在或不在你的授权范围。', 'This project is unavailable or outside your authorized access.'],
    project_rate_limited: ['请求过于频繁，请等待后手动重试。', 'Too many requests. Wait before trying again manually.'],
    project_context_changed: ['登录身份已变化，旧项目与表单已清除。请重新载入当前账号的项目；不会自动重发创建请求。', 'The signed-in account changed. Old projects and form content were cleared. Reload projects for the current account. Creation will not be retried automatically.'],
    project_create_result_unknown: ['创建结果尚未完全确认，项目可能已保存。请重新载入列表核对，不要重复提交。', 'The creation outcome is not fully confirmed; the project may already be saved. Reload the list to check and do not repeat the submission.'],
    projects_unavailable: ['私人项目服务暂不可用或结果未通过校验，请重新载入。', 'Private project services are unavailable or returned an unconfirmed result. Please reload.'],
  };
  return t(...(labels[code] || labels.projects_unavailable));
}
export function mountPrivateProjects(root, model, t = zh => zh) {
  const doc = root.ownerDocument; let disposed = false;
  const make = (tag, content = '', className = '') => { const node = doc.createElement(tag); if (content) node.textContent = content; if (className) node.className = className; return node; };
  const bindings = [], bind = (node, name, fn) => { node.addEventListener(name, fn); bindings.push([node, name, fn]); };
  const button = (label, callback, className = '') => { const node = make('button', label, className); node.type = 'button'; bind(node, 'click', callback); return node; };
  const title = make('h1', t('我的私人项目', 'My private projects')), note = make('p', t('仅显示服务端授权的项目；与独立演示空间分开。', 'Only server-authorized projects are shown, separately from the demo workspace.'), 'muted');
  const heading = make('div', '', 'page-title'), actions = make('div', '', 'row');
  const createButton = button(t('新建私人项目', 'New private project'), () => model.openDialog(), 'primary'); createButton.setAttribute('data-private-create', '');
  const reload = button(t('重新载入', 'Reload'), () => void model.refresh()); reload.setAttribute('data-private-reload', '');
  heading.append(title, note); actions.append(createButton, reload);
  const status = make('p', '', 'signup-state'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite'); status.setAttribute('aria-atomic', 'true'); status.setAttribute('data-private-state', '');
  const identity = make('p', '', 'note'); identity.setAttribute('data-private-identity', '');
  const login = make('a', t('前往登录', 'Go to sign in'), 'link'); login.href = '#/login';
  const list = make('section', '', 'panel'); list.setAttribute('aria-label', t('私人项目列表', 'Private project list')); list.setAttribute('data-private-list', '');
  const more = button(t('加载下一页', 'Load next page'), () => void model.more()); more.setAttribute('data-private-more', '');
  const detail = make('section', '', 'panel panel-pad'); detail.setAttribute('aria-label', t('项目基本信息', 'Project basic information')); detail.setAttribute('data-private-detail', '');
  const footer = make('p', t('文件保存、导入报告和分析任务分别记录。开发登录会话最长 15 分钟，服务重启后需重新登录。', 'File storage, import reports and analysis tasks are recorded separately. Development sign-in lasts at most 15 minutes and ends on a server restart.'), 'note');
  const dialog = make('dialog'), dialogTitle = make('h2', t('创建私人项目', 'Create private project')); dialogTitle.id = 'private-create-title'; dialog.setAttribute('aria-labelledby', dialogTitle.id);
  const form = make('form'); form.noValidate = true; form.setAttribute('data-private-form', '');
  const inputs = {}, errors = {};
  for (const [key, label, tag] of [['name', t('项目名称', 'Project name'), 'input'], ['description', t('描述（可选）', 'Description (optional)'), 'textarea'], ['species', t('物种（可选）', 'Species (optional)'), 'input']]) {
    const wrapper = make('label', label, 'field'), input = make(tag), error = make('span', '', 'signup-field-error');
    input.name = key; input.id = 'private-project-' + key; input.required = key === 'name'; input.autocomplete = 'off'; error.id = input.id + '-error'; input.setAttribute('aria-describedby', error.id);
    wrapper.append(input, error); form.append(wrapper); inputs[key] = input; errors[key] = error;
    bind(input, 'input', () => { if (!disposed) model.edit(key, input.value); });
  }
  const formStatus = make('p', '', 'signup-state'); formStatus.setAttribute('role', 'status'); formStatus.setAttribute('aria-live', 'polite'); formStatus.setAttribute('data-private-create-state', '');
  const cancelButton = button(t('取消', 'Cancel'), () => model.closeDialog()), submit = make('button', t('创建项目', 'Create project'), 'primary'); submit.type = 'submit';
  const formActions = make('div', '', 'actions'); formActions.append(cancelButton, submit); form.append(formStatus, formActions); dialog.append(dialogTitle, form);
  bind(form, 'submit', event => { event.preventDefault(); if (!disposed) void model.create(Object.fromEntries(Object.entries(inputs).map(([key, input]) => [key, input.value]))); });
  bind(dialog, 'cancel', event => { event.preventDefault(); if (!disposed) model.closeDialog(); });
  root.replaceChildren(heading, actions, identity, status, login, list, more, detail, footer, dialog);
  let rowBindings = [], disposeFilesView = () => {};
  const clearRows = () => { for (const [node, fn] of rowBindings) node.removeEventListener('click', fn); rowBindings = []; };
  function paint() {
    if (disposed) return;
    const value = model.status(); root.setAttribute('aria-busy', String(value.busy)); createButton.disabled = !value.canCreate; reload.disabled = value.busy;
    identity.textContent = value.identity === 'authenticated' && value.email ? t('当前已验证邮箱：', 'Current verified email: ') + value.email : '';
    login.hidden = value.identity !== 'anonymous';
    status.textContent = value.phase === 'loading' ? t('正在确认身份并读取私人项目…', 'Confirming your identity and loading private projects…')
      : value.phase === 'loading_more' ? t('正在加载下一页…', 'Loading the next page…')
      : value.code ? message(value.code, t) : value.createPhase === 'created' ? t('项目已创建，并已通过详情与列表回读确认。', 'Project created and confirmed by detail and list readback.')
      : value.createPhase === 'unknown' ? message('project_create_result_unknown', t)
      : value.createPhase === 'submitting' || value.createPhase === 'confirming' ? t('正在创建并回读确认项目…', 'Creating and reading back the project…')
      : value.phase === 'ready' ? (value.items.length ? t('已读取当前授权范围内的私人项目。', 'Private projects in your authorized access have been loaded.') : t('尚无私人项目，可创建第一个项目。', 'No private projects yet. You can create your first project.')) : '';
    clearRows(); list.replaceChildren();
    if (value.phase === 'ready' || value.phase === 'loading_more') for (const item of value.items) {
      const row = make('div', '', 'list-row'), content = make('div'), select = make('button', item.name, 'link'); select.type = 'button'; select.dataset.privateProject = item.project_id; select.disabled = value.busy;
      const handler = () => { if (!disposed) void model.select(item.project_id); }; select.addEventListener('click', handler); rowBindings.push([select, handler]);
      content.append(select, make('p', (item.species || t('物种未提供', 'Species not provided')) + ' · ' + item.createdAt, 'note'));
      row.append(content, make('span', t('私有', 'Private'), 'badge')); list.append(row);
    }
    more.hidden = !value.nextCursor || !value.ready; more.disabled = value.busy;
    disposeFilesView(); disposeFilesView = () => {}; detail.replaceChildren();
    if (value.detailPhase === 'loading') detail.append(make('p', t('正在读取项目基本信息…', 'Loading project details…')));
    else if (value.detailCode) detail.append(make('p', message(value.detailCode, t)));
    else if (value.selected) {
      detail.append(make('h2', value.selected.name));
      const facts = make('dl', '', 'facts');
      for (const [label, text] of [[t('项目 ID', 'Project ID'), value.selected.project_id], [t('描述', 'Description'), value.selected.description || t('未填写', 'Not provided')], [t('物种', 'Species'), value.selected.species || t('未提供', 'Not provided')], [t('可见性', 'Visibility'), t('私有', 'Private')], [t('创建时间', 'Created'), value.selected.createdAt], [t('更新时间', 'Updated'), value.selected.updatedAt]]) facts.append(make('dt', label), make('dd', text));
      detail.append(facts);
      const filesModel = model.files?.();
      if (filesModel) { const filesRoot = make('section', '', 'panel panel-pad'); filesRoot.setAttribute('data-private-txt', ''); detail.append(filesRoot); disposeFilesView = mountPrivateTxt(filesRoot, filesModel, t); }
    } else detail.append(make('p', t('选择一个项目查看基本信息。', 'Select a project to view its basic information.'), 'note'));
    for (const [key, input] of Object.entries(inputs)) {
      if (input.value !== value.draft[key]) input.value = value.draft[key]; input.disabled = value.busy;
      input.setAttribute('aria-invalid', String(Boolean(value.errors[key]))); errors[key].textContent = value.errors[key] ? message(value.errors[key], t) : '';
    }
    cancelButton.disabled = value.busy; submit.disabled = !value.canCreate;
    submit.textContent = value.createPhase === 'submitting' || value.createPhase === 'confirming' ? t('创建并确认中…', 'Creating and confirming…') : t('创建项目', 'Create project');
    formStatus.textContent = value.createPhase === 'submitting' ? t('正在提交创建请求…', 'Submitting the creation request…') : value.createPhase === 'confirming' ? t('正在回读确认项目…', 'Reading back the project…') : value.errors.form ? message(value.errors.form, t) : value.createPhase === 'invalid' ? t('请检查标出的字段。', 'Please check the marked fields.') : value.createCode ? message(value.createCode, t) : '';
    if (value.dialogOpen && !dialog.open) dialog.showModal(); else if (!value.dialogOpen && dialog.open) dialog.close();
  }
  const unsubscribe = model.subscribe(paint); paint(); void model.start();
  return () => { if (disposed) return; disposed = true; disposeFilesView(); unsubscribe(); clearRows(); for (const [node, name, fn] of bindings) node.removeEventListener(name, fn); if (dialog.open) dialog.close(); root.replaceChildren(); };
}
