import {readFileSync} from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {createHash,webcrypto} from 'node:crypto';
import {sampleDesignRoute,strictJSON,validateDesign,validateSampleDesignResponse,designSHA,sampleDesignInput,canonicalJSON,MAX_RESPONSE} from '../sample-design-contract.mjs';
import {createSampleDesignBridge} from '../sample-design-bridge.mjs';
import {createSampleDesignClient,createSampleDesignTransport} from '../sample-design-client.js';
// All fixtures and tokens below are synthetic; no socket or real fetch is used.
const id=n=>'00000000-0000-0000-0000-'+String(n).padStart(12,'0');
const source={dataset_version_id:id(2),sample_columns:[{ordinal:3,name_raw:'Intensity 样本😀',sample_label_raw:'样本😀',quant_family:'Intensity'},{ordinal:5,name_raw:'LFQ intensity 样本😀',sample_label_raw:'样本😀',quant_family:'LFQ intensity'}]};
const clone=v=>structuredClone(v);
const draft=(family='Intensity')=>({schemaVersion:1,dataset_version_id:id(2),quant_family:family,rows:source.sample_columns.filter(c=>c.quant_family===family).map(c=>({column_ordinal:c.ordinal,name_raw:c.name_raw,sample_label_raw:c.sample_label_raw,group:'',condition:'',control:'unknown',replicate:'',replicate_type:'unknown',confirmed:false}))});
const path='/api/v1/datasets/'+id(2)+'/sample-designs?project_id='+id(1)+'&quant_family=intensity';
const route=(method='GET',suffix='')=>sampleDesignRoute(path+suffix,method);
async function record(design=draft(),extra={}){return {sample_design_version_id:id(8),parent_version_id:null,revision:1,request_id:id(9),design_sha256:await designSHA(design,webcrypto),created_at:'2026-11-17T00:00:00.123456Z',design,...extra};}
const envelope=r=>({schemaVersion:1,project_id:id(1),dataset_version_id:id(2),quant_family:r?.design?.quant_family??'Intensity',record:r});
const payload=()=>({schemaVersion:1,request_id:id(9),expected_head_version_id:null,design:draft()});
const bytes=v=>new TextEncoder().encode(JSON.stringify(v));
const response=(v,status=200,headers={})=>new Response(JSON.stringify(v),{status,headers:{'Content-Type':'application/json',...headers}});
const validate=(v,r=route(),opts={})=>validateSampleDesignResponse(v,r,source,{cryptoImpl:webcrypto,...opts});
const client=(transport,options={})=>createSampleDesignClient({projectId:id(1),datasetVersionId:id(2),source,transport,cryptoImpl:webcrypto,isCurrent:()=>true,...options});
const context=(quant_family='Intensity')=>({dataset_version_id:id(2),quant_family});

test('strict fixed route rejects URLs, encoding, duplicate/missing/extra queries and POST request_id',()=>{
  assert.equal(route().quant_family,'Intensity');assert.equal(sampleDesignRoute(path.replace('intensity','lfq'),'POST').quant_family,'LFQ intensity');
  for(const p of ['https://evil.test'+path,path+'&project_id='+id(1),path+'&x=y',path.replace('intensity','Intensity'),path.replace('project_id','%70roject_id'),path+'#x',path.replace('?','/?')])assert.throws(()=>sampleDesignRoute(p,'GET'));
  assert.throws(()=>sampleDesignRoute(path+'&request_id='+id(9),'POST'));assert.throws(()=>sampleDesignRoute(path,'DELETE'));
});
test('JSON rejects duplicate nested keys, bad UTF8, lone surrogate, nonfinite and excessive nesting',()=>{
  for(const text of ['{"a":1,"a":2}','{"a":{"b":1,"b":2}}','"\\ud800"','1e999','['.repeat(34)+'0'+']'.repeat(34)])assert.throws(()=>strictJSON(new TextEncoder().encode(text)));
  assert.throws(()=>strictJSON(new Uint8Array([0xff])));assert.equal(strictJSON(bytes('😀')),'😀');
});
test('design validates exact raw fields and source order; integers and Unicode scalar boundaries',()=>{
  const d=draft();d.schemaVersion=1.0;d.rows[0].column_ordinal=3.0;d.rows[0].group='😀'.repeat(80);validateDesign(d,source,route());
  for(const mutate of [d=>d.rows[0].column_ordinal=true,d=>d.rows[0].column_ordinal=2**53,d=>d.rows[0].group+='😀',d=>d.rows[0].group='\ud800',d=>d.rows[0].condition='x\u0085',d=>d.rows[0].name_raw='changed',d=>d.rows[0].confirmed=true,d=>d.rows.push(d.rows[0]),d=>d.rows[0].extra=1,d=>d.quant_family='LFQ intensity']){const x=clone(d);mutate(x);assert.throws(()=>validateDesign(x,source,route()));}
  const two=clone(source);two.sample_columns.push({...two.sample_columns[0],ordinal:7});const x=draft();x.rows.push({...x.rows[0],column_ordinal:7});validateDesign(x,two,route());x.rows.reverse();assert.throws(()=>validateDesign(x,two,route()));
});
test('confirmed ambiguous labels rejected; draft conflicts remain saveable without claiming analysis readiness',()=>{
  const d=draft();Object.assign(d.rows[0],{group:'g',condition:'c',control:'yes',replicate:'1',replicate_type:'biological',confirmed:true});validateDesign(d,source,route());
  const s=clone(source);s.sample_columns[0].sample_label_raw=' ';s.sample_columns[0].name_raw='Intensity  ';const x=clone(d);x.rows[0].sample_label_raw=' ';x.rows[0].name_raw='Intensity  ';assert.throws(()=>validateDesign(x,s,route()));x.rows[0].confirmed=false;validateDesign(x,s,route());
});
test('canonical UTF8 SHA and integer -0 normalization match frozen definition',async()=>{
  const d=draft();d.rows[0].condition='中文😀\n'.replace('\n','');const expected=createHash('sha256').update(canonicalJSON(d),'utf8').digest('hex');assert.equal(await designSHA(d,webcrypto),expected);assert.equal(canonicalJSON({schemaVersion:1.0,ordinal:-0}),'\{"ordinal":0,"schemaVersion":1}');
});
test('success envelope validates digest, source, route, exact fields, revision, parent, UUID and timestamps',async()=>{
  const r=await record();await validate(envelope(r));await validate(envelope(null));
  for(const mutate of [v=>v.project_id=id(4),v=>v.dataset_version_id=id(3),v=>v.quant_family='LFQ intensity',v=>v.extra=true,v=>v.record.design_sha256='0'.repeat(64),v=>v.record.revision=true,v=>v.record.revision=101,v=>v.record.parent_version_id=id(5),v=>v.record.created_at='2026-02-30T00:00:00.123456Z',v=>v.record.created_at='2026-11-17T00:00:00.123Z',v=>v.record.created_at='0000-01-01T00:00:00.000000Z',v=>v.record.sample_design_version_id='ABC',v=>v.record.design.rows[0].group='changed']){const v=clone(envelope(r));mutate(v);await assert.rejects(validate(v));}
  await assert.rejects(validate(envelope(null),route('POST')));await assert.rejects(validate(envelope(r),route('GET','&request_id='+id(7))));
});
test('POST response checks exact logical request, parent and cached head revision',async()=>{
  const p=payload(),r=await record();await validate(envelope(r),route('POST'),{request:p,head:null});
  await assert.rejects(validate(envelope({...r,revision:2,parent_version_id:id(6)}),route('POST'),{request:p,head:null}));
  await assert.rejects(validate(envelope({...r,request_id:id(10)}),route('POST'),{request:p,head:null}));
});
test('input bounded bytes and exact wrapper',()=>{assert.equal(sampleDesignInput(bytes(payload()),route('POST'),source).schemaVersion,1);assert.throws(()=>sampleDesignInput(bytes({...payload(),extra:1}),route('POST'),source));assert.throws(()=>sampleDesignInput(new Uint8Array(131073),route('POST'),source));});

function nodeTransport(handler){return (options,callback)=>{const out=new EventEmitter();out.destroy=()=>{};out.end=body=>queueMicrotask(()=>handler({options,body,out,reply:(v,status=200,rawHeaders=['Content-Type','application/json'])=>{const incoming=new EventEmitter();incoming.rawHeaders=rawHeaders;incoming.headers={};incoming.statusCode=status;incoming.destroy=()=>{};callback(incoming);if(!incoming.destroyed){incoming.emit('data',Buffer.from(JSON.stringify(v)));incoming.complete=true;incoming.emit('end');incoming.emit('close');}return incoming;}}));return out;};}
const nodeArgs=(method='GET')=>({route:route(method),body:method==='POST'?bytes(payload()):undefined,source,accessToken:'synthetic.token.only',signal:new AbortController().signal,budget:{remaining:()=>1000,deadlineUnixMs:'1794873600000'}});
test('Node transport defaults disabled and pins destination, Origin, budget, and allowlisted headers',async()=>{
  await assert.rejects(createSampleDesignBridge()(nodeArgs()));
  const bridge=createSampleDesignBridge({cryptoImpl:webcrypto,requestImpl:nodeTransport(({options,reply})=>{assert.equal(options.hostname,'127.0.0.1');assert.equal(options.port,57430);assert.equal(options.path,path);assert.equal(options.headers.Origin,'http://127.0.0.1:4206');assert.equal(options.headers['X-Cyomics-Remaining-Ms'],'1000');assert.equal(options.headers.Cookie,undefined);reply(envelope(null));})});assert.equal((await bridge(nodeArgs())).status,200);
});
test('Node rejects redirects, cookies, encodings, duplicate headers and oversized bodies',async()=>{
  for(const h of [['Content-Type','application/json','Location','https://evil.test'],['Content-Type','application/json','Set-Cookie','x=y'],['Content-Type','application/json','Content-Encoding','gzip'],['Content-Type','application/json','Content-Type','application/json'],['Content-Type','application/json','Content-Length',String(MAX_RESPONSE+1)]]){
    const bridge=createSampleDesignBridge({requestImpl:nodeTransport(({reply})=>reply(envelope(null),200,h))});await assert.rejects(bridge(nodeArgs()));}
});
test('Node POST malformed/failed transport is unknown, never retried; explicit 409 remains conflict',async()=>{
  let count=0;const bridge=createSampleDesignBridge({requestImpl:nodeTransport(({out})=>{count++;out.emit('error',Error());})});await assert.rejects(bridge(nodeArgs('POST')),e=>e.code==='sample_design_result_unknown');assert.equal(count,1);
  const conflict=createSampleDesignBridge({requestImpl:nodeTransport(({reply})=>reply({error:{code:'sample_design_head_conflict'}},409))});assert.equal((await conflict(nodeArgs('POST'))).status,409);
});
test('Node invalid token/budget/route never invokes injected request',async()=>{
  let count=0;const bridge=createSampleDesignBridge({requestImpl:()=>{count++;throw Error();}});for(const change of [a=>a.accessToken='x\r\ny',a=>a.budget.remaining=()=>55001,a=>a.route={...a.route,path:'https://evil.test'},a=>a.signal=new AbortController().signal]){const a=nodeArgs();change(a);if(a.accessToken==='synthetic.token.only'&&a.budget.remaining()===1000&&a.route.path===path)continue;await assert.rejects(bridge(a));}assert.equal(count,0);
});

test('browser adapter is disabled by default, relative-only with same-origin credentials and no redirect',async()=>{
  assert.equal(createSampleDesignTransport(),null);let seen;const t=createSampleDesignTransport({fetchImpl:(p,o)=>{seen={p,o};return response(envelope(null));}});await t({route:route(),signal:new AbortController().signal});assert.equal(seen.p,path);assert.equal(seen.o.credentials,'same-origin');assert.equal(seen.o.redirect,'error');assert.equal(seen.o.headers.Authorization,undefined);assert.throws(()=>t({route:{path:'https://evil.test',method:'GET'},signal:new AbortController().signal}));
});
test('client unconnected does not claim saved; saves require a GET-known head',async()=>{
  const c=client(null);assert.equal(c.status('Intensity').phase,'not_connected');await assert.rejects(c.saveDraft(draft()),e=>e.code==='sample_design_head_required');assert.equal(c.status('Intensity').persistence_verified,false);
  let posts=0;const connected=client(()=>{posts++;throw Error();});await assert.rejects(connected.saveDraft(draft()));assert.equal(posts,0);
});
test('client GET then CAS POST verifies and returns exact draft; families hold independent heads',async()=>{
  const calls=[];const c=client(async({route,body,onSent})=>{calls.push(route);if(route.method==='GET')return response(envelope(null));onSent();const p=JSON.parse(body);return response(envelope(await record(p.design,{request_id:p.request_id})));});
  assert.equal(await c.loadDraft(context()),null);const d=draft();assert.equal(canonicalJSON(await c.saveDraft(d)),canonicalJSON(d));assert.equal(c.status('Intensity').dirty,false);assert.equal(c.status('Intensity').persistence_verified,true);assert.equal(c.status('LFQ intensity').head_known,false);await assert.rejects(c.saveDraft(draft('LFQ intensity')));assert.equal(calls.length,2);
});
test('client CAS409 retains dirty and requires explicit head refresh',async()=>{
  let calls=0;const c=client(({route,onSent})=>{calls++;if(route.method==='GET')return response(envelope(null));onSent();return response({error:{code:'sample_design_head_conflict'}},409);});await c.loadDraft(context());await assert.rejects(c.saveDraft(draft()));assert.equal(c.status('Intensity').phase,'conflict');assert.equal(c.status('Intensity').dirty,true);await assert.rejects(c.saveDraft(draft()));assert.equal(calls,2);
});
test('client uncertain POST never retries or polls; explicit request lookup cannot become head',async()=>{
  let count=0,pending;const c=client(async({route,body,onSent})=>{count++;if(route.method==='POST'){onSent();pending=JSON.parse(body);throw Error('synthetic lost response');}if(route.request_id)return response(envelope(await record(pending.design,{request_id:pending.request_id})));return response(envelope(null));});
  await c.loadDraft(context());await assert.rejects(c.saveDraft(draft()));assert.equal(c.status('Intensity').phase,'unknown');await assert.rejects(c.saveDraft(draft()));await assert.rejects(c.loadDraft(context()));assert.equal(count,2);assert.equal(canonicalJSON(await c.resolveRequest('Intensity')),canonicalJSON(draft()));assert.equal(c.status('Intensity').head_known,false);assert.equal(c.status('Intensity').dirty,true);assert.equal(count,3);await assert.rejects(c.saveDraft(draft()));assert.equal(count,3);
});
test('client rejects late family/project/dataset responses and does not update head',async()=>{
  let active=true,release;const c=client(()=>new Promise(r=>release=r),{isCurrent:()=>active});const pending=c.loadDraft(context());active=false;release(response(envelope(null)));await assert.rejects(pending);assert.equal(c.status('Intensity').head_known,false);
  const wrong=client(()=>response({...envelope(null),project_id:id(7)}));await assert.rejects(wrong.loadDraft(context()));assert.equal(wrong.status('Intensity').head_known,false);
});
test('client abort after POST may be sent remains unknown with no hidden network',async()=>{
  let timer,posts=0;const c=client(({route,onSent})=>{if(route.method==='GET')return response(envelope(null));posts++;onSent();return new Promise(()=>{});},{setTimer:fn=>{timer=fn;return 1;},clearTimer:()=>{}});await c.loadDraft(context());const p=c.saveDraft(draft());timer();await assert.rejects(p);assert.equal(c.status('Intensity').phase,'unknown');assert.equal(posts,1);
});

test('late POST success after family switch is unknown and cannot acknowledge a current draft',async()=>{
  let active=true,release,payload;
  const c=client(({route,body})=>{if(route.method==='GET')return response(envelope(null));payload=JSON.parse(body);return new Promise(r=>release=r);},{isCurrent:()=>active});
  await c.loadDraft(context());const p=c.saveDraft(draft());active=false;release(response(envelope(await record(payload.design,{request_id:payload.request_id}))));await assert.rejects(p);
  assert.equal(c.status('Intensity').phase,'unknown');assert.equal(c.status('Intensity').dirty,true);assert.equal(c.status('Intensity').persistence_verified,false);
});
test('Node abort after write and response/body integrity failures remain unknown',async()=>{
  for(const bad of ['abort','digest','length','size']){
    const a=nodeArgs('POST'),controller=new AbortController();a.signal=controller.signal;
    const bridge=createSampleDesignBridge({requestImpl:nodeTransport(({reply})=>{
      if(bad==='abort')return controller.abort();if(bad==='size')return reply('x'.repeat(MAX_RESPONSE));
      reply(envelope({}),200,bad==='length'?['Content-Type','application/json','Content-Length','1']:['Content-Type','application/json']);
    })});await assert.rejects(bridge(a),e=>e.code==='sample_design_result_unknown');
  }
});
test('explicit request lookup rejects absent, mismatched request or changed design and keeps uncertainty',async()=>{
  for(const mode of ['absent','mismatch','changed']){
    let p;const c=client(async({route,body})=>{if(route.method==='POST'){p=JSON.parse(body);throw Error();}if(!route.request_id)return response(envelope(null));if(mode==='absent')return response({error:{code:'sample_design_not_found'}},404);
      const d=clone(p.design);if(mode==='changed')d.rows[0].group='changed';return response(envelope(await record(d,{request_id:mode==='mismatch'?id(44):p.request_id})));
    });await c.loadDraft(context());await assert.rejects(c.saveDraft(draft()));await assert.rejects(c.resolveRequest('Intensity'));assert.equal(c.status('Intensity').phase,'unknown');assert.ok(c.status('Intensity').uncertain_request_id);
  }
});

test('client rejects oversized or truncated response without accepting a head',async()=>{
  for(const headers of [{'Content-Length':String(MAX_RESPONSE+1)},{'Content-Length':'999'}]){
    const c=client(()=>response(envelope(null),200,headers));await assert.rejects(c.loadDraft(context()));assert.equal(c.status('Intensity').head_known,false);
  }
});
test('Node preserves valid source snapshot across caller mutation and rejects wrong dataset before send',async()=>{
  let reply;const local=clone(source);const bridge=createSampleDesignBridge({requestImpl:nodeTransport(args=>{reply=args.reply;})});const args=nodeArgs();args.source=local;
  const p=bridge(args);local.dataset_version_id=id(90);await new Promise(resolve=>queueMicrotask(resolve));reply(envelope(null));assert.equal((await p).status,200);
  let count=0;const deny=createSampleDesignBridge({requestImpl:()=>{count++;}});await assert.rejects(deny({...nodeArgs(),source:{...source,dataset_version_id:id(90)}}));assert.equal(count,0);
});

test('independent Python canonical hash vector matches Node and browser contract',async()=>{
  const vector=JSON.parse(readFileSync(new URL('./fixtures/sample-design-canonical.synthetic.json',import.meta.url),'utf8'));
  assert.equal(canonicalJSON(vector.design),vector.canonical);assert.equal(await designSHA(vector.design,webcrypto),vector.sha256);
});
test('Node valid POST verifies returned record and preserves caller request id',async()=>{
  const value=envelope(await record());let posted;
  const bridge=createSampleDesignBridge({cryptoImpl:webcrypto,requestImpl:nodeTransport(({body,reply})=>{posted=JSON.parse(Buffer.from(body).toString('utf8'));reply(value);})});
  const result=await bridge(nodeArgs('POST'));assert.equal(result.value.record.request_id,posted.request_id);assert.equal(result.status,200);
});
