import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { createProjectBridge, projectRoute, projectInput, strictProjectJSON } from '../projects-bridge.mjs';

const id = '11111111-1111-4111-8111-111111111111';
const project = { project_id: id, name: 'Project', description: '', species: '', visibility: 'private',
  createdAt: '2026-11-16T00:00:00.000000Z', updatedAt: '2026-11-16T00:00:00.123456Z' };
function native(reply) {
  const calls = [];
  const requestImpl = (options, callback) => {
    const out = new EventEmitter(); out.destroy = () => { out.destroyed = true; };
    const call = { options, out }; calls.push(call);
    out.end = body => { call.body = body; queueMicrotask(() => reply({ ...call, send(value, status = 200, rawHeaders = ['Content-Type', 'application/json']) {
      const response = new EventEmitter(); response.statusCode = status; response.rawHeaders = rawHeaders;
      response.destroy = () => { response.destroyed = true; }; call.response = response; callback(response);
      response.emit('data', Buffer.isBuffer(value) ? value : Buffer.from(JSON.stringify(value))); response.emit('end');
      return response;
    } })); };
    return out;
  };
  return { requestImpl, calls };
}
function invoke(driver, { method = 'GET', path = '/api/v1/projects', remaining = () => 11999, signal = new AbortController().signal } = {}) {
  return createProjectBridge(driver)({ route: projectRoute(path, method), body: method === 'POST' ? Buffer.from('{"name":"Project"}') : undefined,
    accessToken: 'SYNTHETIC_ACCESS', signal, budget: { remaining, deadlineUnixMs: '1790000012000' } });
}
const plain = value => JSON.parse(JSON.stringify(value));

test('native bridge fixes endpoint, exact generated budget/credential headers, path and validated public list/detail/create', async () => {
  for (const [method, path, value, status] of [['GET', '/api/v1/projects?limit=1', {items:[project],nextCursor:null}, 200],
    ['GET', '/api/v1/projects/'+id, {project}, 200], ['POST', '/api/v1/projects', {project}, 201]]) {
    const h = native(({send}) => send(value, status));
    assert.deepEqual(plain(await invoke(h, {method,path})), {status,value});
    const {options} = h.calls[0];
    assert.equal(options.hostname,'127.0.0.1'); assert.equal(options.port,57430); assert.equal(options.agent,false); assert.equal(options.path,path);
    assert.deepEqual(Object.keys(options.headers).sort(), ['Accept','Authorization','X-Cyomics-Deadline-Unix-Ms','X-Cyomics-Remaining-Ms',...(method==='POST'?['Content-Length','Content-Type']:[])].sort());
    assert.equal(options.headers.Authorization,'Bearer SYNTHETIC_ACCESS'); assert.equal(options.headers['X-Cyomics-Remaining-Ms'],'11999');
    assert.equal(options.headers['X-Cyomics-Deadline-Unix-Ms'],'1790000012000'); assert.equal(h.calls.length,1);
  }
});
test('strict JSON rejects duplicate keys at all depths, unpaired surrogates, invalid UTF8 and optional input defaults are exact', () => {
  assert.deepEqual(projectInput(Buffer.from('{"name":"  Project  "}')), {name:'Project',description:'',species:''});
  for (const raw of ['{"name":"a","n\\u0061me":"b"}','{"name":"a","owner_id":"x"}','{"name":"\\ud800"}',
    '{"name":"a","species":null}','{"name":"a","description":"\\u0000"}', '{"name":"x","species":"x\\u0080"}',
    '{"name":"'+'x'.repeat(81)+'"}', '[]', '{"name":1}']) assert.throws(()=>projectInput(Buffer.from(raw)),e=>e.status===400);
  for (const raw of ['{"a":{"x":1,"x":2}}','{"a":1,}', '[1,]', '01', 'true false', '1e999', ' '.repeat(2)]) assert.throws(()=>strictProjectJSON(Buffer.from(raw)));
  assert.throws(()=>projectInput(Buffer.from([255])),e=>e.status===400);
});
test('raw route and cursor checks reject encoded aliases, foreign URLs, ambiguous queries and noncanonical identifiers', () => {
  for (const path of ['/api/v1/projects/','/api/v1/projects/a/b','/api/v1/projects/%31'+id.slice(1),'/api/v1/projects/'+id.toUpperCase().replace('1','A'),
    'http://evil.test/api/v1/projects','/api/v1/projects?limit=0','/api/v1/projects?limit=1&limit=2','/api/v1/projects?target=x',
    '/api/v1/projects?cursor=abc','/api/v1/projects?limit=%ff','/api/v1/projects?limit=%x1','/api/v1/projects/'+id+'?x=1']) assert.throws(()=>projectRoute(path,'GET'));
});
test('every allowed fixed error pair retains status; missing/extra/duplicate errors reject whole response', async () => {
  const pairs = [[400,'invalid_project'],[401,'unauthenticated'],[403,'auth_origin_rejected'],[404,'project_not_found'],[405,'method_not_allowed'],
    [409,'project_context_changed'],[413,'project_body_too_large'],[415,'unsupported_media_type'],[429,'project_rate_limited'],[503,'projects_unavailable'],[504,'project_create_result_unknown']];
  for (const [status,code] of pairs) {
    const h=native(({send})=>send({error:{code}},status));
    assert.deepEqual(plain(await invoke(h,{method:'POST'})),{status,value:{error:{code}}});
  }
  for (const value of [{error:{code:'SECRET'}},{error:{code:'projects_unavailable',internal:'SECRET'}},{error:{code:'projects_unavailable'},extra:1},
    Buffer.from('{"error":{"code":"projects_unavailable","code":"projects_unavailable"}}')]) {
    await assert.rejects(invoke(native(({send})=>send(value,503))),e=>e.status===503&&e.code==='projects_unavailable');
  }
});
test('malformed or extra public fields, private columns, invalid scalar/times and wrong wrappers never become sanitized success', async () => {
  const values=[{project:{...project,owner_id:id}},{project:{...project,visibility:'public'}},{project:{...project,name:'x'.repeat(81)}},
    {project:{...project,createdAt:'2026-02-30T00:00:00.000000Z'}},{project:{...project,createdAt:'0000-01-01T00:00:00.000000Z'}},{project:{...project,species:1}},{project,extra:true},
    {project:{...project,project_id:id.toUpperCase().replace('1','A')}}, {project:{...project,description:'\ud800'}},
    Buffer.from('{"project":'+JSON.stringify(project)+',"project":'+JSON.stringify(project)+'}')];
  for (const value of values) for (const method of ['GET','POST']) {
    await assert.rejects(invoke(native(({send})=>send(value,method==='POST'?201:200)),{method,path:method==='POST'?'/api/v1/projects':'/api/v1/projects/'+id}),
      e=>e.status===(method==='POST'?504:503)&&e.code===(method==='POST'?'project_create_result_unknown':'projects_unavailable'));
  }
});
test('bounded bodies, MIME, redirects, cookies and invalid UTF8 reject; sent POST is uncertain, unsent is unavailable', async () => {
  for (const method of ['GET','POST']) for (const mode of ['oversize','mime','redirect','cookie','duplicate-mime','utf8','transport']) {
    const h=native(({send,out})=>{
      if(mode==='transport')return out.emit('error',Error('SECRET'));
      const raw=mode==='oversize'?Buffer.alloc(method==='POST'?16385:524289,32):mode==='utf8'?Buffer.from([255]):{items:[],nextCursor:null};
      const headers=mode==='mime'?['Content-Type','text/plain']:mode==='cookie'?['Content-Type','application/json','Set-Cookie','SECRET=1']:
        mode==='duplicate-mime'?['Content-Type','application/json','content-type','application/json']:['Content-Type','application/json'];
      send(raw,mode==='redirect'?302:method==='POST'?201:200,headers);
    });
    await assert.rejects(invoke(h,{method}),e=>e.status===(method==='POST'?504:503)); assert.equal(h.calls.length,1); assert.equal(h.calls[0].out.destroyed,true);
  }
  for (const method of ['GET','POST']) {
    await assert.rejects(invoke({requestImpl(){throw Error('not sent');}},{method}),e=>e.status===503);
    const h=native(()=>assert.fail('not sent')); await assert.rejects(invoke(h,{method,remaining:()=>0}),e=>e.status===503);assert.equal(h.calls.length,0);
    await assert.rejects(invoke(h,{method,remaining:()=>NaN}),e=>e.status===503);assert.equal(h.calls.length,0);
  }
});
test('abort destroys a pending native request and slow body budget never resets at headers', async () => {
  const controller=new AbortController(), h=native(()=>{}), p=invoke(h,{method:'POST',signal:controller.signal});
  await new Promise(setImmediate);controller.abort();await assert.rejects(p,e=>e.status===504);assert.equal(h.calls[0].out.destroyed,true);
  let left=100;
  const slow=native(({send})=>{left=0;send({project},201);});
  await assert.rejects(invoke(slow,{method:'POST',remaining:()=>left}),e=>e.status===504);
  const errorBig=native(({send})=>send(Buffer.alloc(4097,32),503));await assert.rejects(invoke(errorBig),e=>e.status===503);
});
