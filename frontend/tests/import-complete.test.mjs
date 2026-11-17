import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {harness} from './raw-upload-harness.mjs';
import {importRoute,importInput,validateImportSuccess,validateImportError} from '../imports-contract.mjs';

const fixture=JSON.parse(readFileSync(new URL('./fixtures/import-complete-v2.synthetic.json',import.meta.url),'utf8'));
const pid=fixture.datasetVersion.project_id,fid=fixture.datasetVersion.file_id,vid=fixture.datasetVersion.dataset_version_id;
const path='/api/v1/uploads/'+fid+'/complete',read=path+'?project_id='+pid+'&version_id='+vid;
const payload={project_id:pid},tick=()=>new Promise(setImmediate);
const clone=v=>JSON.parse(JSON.stringify(v));
const issue={code:'TXT_ROW_WIDTH',record_number:1,line:2,column_ordinal:3,limit_key:null};

test('API004 route/body strictness and current exact DTO binds source/config/version independently of key order',()=>{
  const route={...importRoute(path,'POST'),project_id:pid};assert.equal(validateImportSuccess(fixture,route),fixture);
  assert.equal(importRoute(read,'GET').version_id,vid);assert.deepEqual(importInput(Buffer.from(JSON.stringify(payload))),payload);
  for(const [url,method]of [[path+'?x=1','POST'],[path,'GET'],[read+'&project_id='+pid,'GET'],[read.replace('project_id=','project%5fid='),'GET'],[path+'/','POST'],[path.replace('/uploads/','/%75ploads/'),'POST'],['http://evil.invalid'+path,'POST'],[path,'DELETE']])assert.throws(()=>importRoute(url,method));
  for(const raw of [Buffer.from('{"project_id":"'+pid+'","project_id":"'+pid+'"}'),Buffer.from(JSON.stringify({...payload,owner_id:pid})),Buffer.from([255]),Buffer.alloc(1025)])assert.throws(()=>importInput(raw));
  const reordered=clone(fixture);reordered.datasetVersion.source=Object.fromEntries(Object.entries(reordered.datasetVersion.source).reverse());assert.ok(validateImportSuccess(reordered,route));
  for(const mutate of [v=>v.datasetVersion.source.expected_raw_sha256='0'.repeat(64),v=>v.datasetVersion.file_id='0'.repeat(36),v=>v.importReport.report.quant_exact_zero_string_cells=999999,
    v=>v.importReport.columns.reverse(),v=>v.importReport.sample_columns[0].sample_label_raw='normalized',v=>v.importReport.row_source_spans[0].start_byte=0,v=>v.datasetVersion.configuration.publisher_contract='other',v=>v.importReport.raw_content='PRIVATE']){
    const value=clone(fixture);mutate(value);assert.throws(()=>validateImportSuccess(value,route));
  }
});
test('explicit default imports gate performs no Auth or upstream action',async t=>{
  const h=harness(t);const r=await h.send(path,{method:'POST',value:payload});assert.equal(r.status,503);assert.deepEqual(r.body,{error:{code:'import_unavailable'}});assert.equal(h.auth.length,0);assert.equal(h.bridges.length,0);
});
test('POST and GET use only fixed native upstream, private token and bounded server timing; no cookie or nonce forwarding',async t=>{
  const h=harness(t,{importsEnabled:true}),s=await h.signed();h.reply(c=>c.deliver(fixture));
  const post=await h.send(path,{...s,method:'POST',value:payload});assert.equal(post.status,200);assert.deepEqual(post.body,fixture);
  const first=h.bridges[0];assert.equal(first.request.hostname,'127.0.0.1');assert.equal(first.request.port,57430);assert.equal(first.request.headers.Authorization,'Bearer ACCESS_A');assert.equal(first.request.headers.Origin,'http://127.0.0.1:4206');assert.equal(first.request.headers['X-Cyomics-Remaining-Ms'],'55000');assert.deepEqual(JSON.parse(first.body),payload);
  const get=await h.send(read,s);assert.equal(get.status,200);assert.deepEqual(get.body,post.body);assert.equal(h.bridges[1].body,undefined);assert.equal(h.bridges[1].request.headers['X-Cyomics-Remaining-Ms'],'12000');
  for(const call of h.bridges)for(const key of ['Cookie','X-Cyomics-Account-Context','X-User-Id','X-Owner-Id'])assert.equal(call.request.headers[key],undefined);
  for(const secret of ['ACCESS_A','REFRESH_SECRET',s.view,s.cookie])assert.ok(!JSON.stringify([post,get]).includes(secret));
});
test('missing identity/context, browser identity/timing/Origin and request shape fail before upstream',async t=>{
  const h=harness(t,{importsEnabled:true});assert.equal((await h.send(read)).status,401);const s=await h.signed();
  for(const view of [undefined,'bad','Z'.repeat(22)])assert.equal((await h.send(read,{cookie:s.cookie,view})).status,409);
  for(const headers of [{origin:'http://evil.invalid'},{authorization:'Bearer BROWSER'},{'x-user-id':pid},{'x-owner-id':pid},{'x-cyomics-remaining-ms':'55000'},{'sec-fetch-site':'same-site'}])assert.equal((await h.send(read,{...s,headers})).status,403);
  for(const [url,options,status]of [[path,{method:'POST',value:{...payload,source:'caller'}},400],[read,{wire:Buffer.from('x')},400],[path,{method:'POST',wire:Buffer.alloc(1025)},413],[path,{method:'POST',value:payload,headers:{'content-type':'text/plain'}},415],[path,{method:'HEAD'},405]]){
    const value=await h.send(url,{...s,...options});assert.equal(value.status,status);
  }
  assert.equal(h.bridges.length,0);
});
test('fixed 422 issues, authorized unknown version key and 429 pass unchanged; unknown has no automatic retry',async t=>{
  const h=harness(t,{importsEnabled:true}),s=await h.signed();
  for(const [status,value]of [[422,{error:{code:'txt_import_failed',issues:[issue]}}],[429,{error:{code:'import_busy'}}],[504,{error:{code:'import_result_unknown',version_key:vid}}],[404,{error:{code:'file_not_found'}}]]){
    h.reply(c=>c.deliver(value,status));const r=await h.send(path,{...s,method:'POST',value:payload});assert.equal(r.status,status);assert.deepEqual(r.body,value);
  }
  h.reply(c=>c.out.emit('error',Error('DYNAMIC_PRIVATE_TRANSPORT')));const before=h.bridges.length;const r=await h.send(path,{...s,method:'POST',value:payload});assert.equal(r.status,504);assert.equal(r.body.error.code,'import_result_unknown');assert.ok(!JSON.stringify(r).includes('DYNAMIC_PRIVATE'));await tick();assert.equal(h.bridges.length,before+1);
  h.reply(c=>c.deliver(fixture));assert.equal((await h.send(read,s)).status,200);assert.equal(h.bridges.at(-1).request.method,'GET');
});
test('upstream redirects cookies media oversize duplicate JSON or broken source cannot publish a false success',async t=>{
  const h=harness(t,{importsEnabled:true}),s=await h.signed();
  const bad=clone(fixture);bad.importReport.report.observed_raw_sha256='0'.repeat(64);
  const replies=[c=>c.deliver(fixture,302),c=>c.deliver(fixture,200,['Content-Type','application/json','Set-Cookie','SECRET']),
    c=>c.deliver(fixture,200,['Content-Type','text/plain']),c=>c.deliver(fixture,200,['Content-Type','application/json','Content-Type','application/json']),
    c=>c.deliver(fixture,200,['Content-Type','application/json','Content-Length','1114113']),c=>c.deliver(Buffer.from('{"datasetVersion":{},"datasetVersion":{},"importReport":{}}')),
    c=>c.deliver(bad),c=>c.deliver({error:{code:'txt_import_failed',issues:[{...issue,raw_text:'SECRET'}]}},422),
    c=>c.deliver({error:{code:'import_result_unknown',version_key:'11111111-1111-4111-8111-111111111111'}},504)];
  for(const reply of replies){h.reply(reply);const r=await h.send(path,{...s,method:'POST',value:payload});assert.equal(r.status,504);assert.equal(r.body.error.code,'import_result_unknown');assert.equal(r.headers['set-cookie'],undefined);}
  h.reply(c=>c.deliver(bad));const get=await h.send(read,s);assert.equal(get.status,503);assert.notEqual(get.status,200);
});
test('replacement login fences pending import and never clears new account or returns old report',async t=>{
  const h=harness(t,{importsEnabled:true}),a=await h.signed();let late,entered;const started=new Promise(done=>entered=done);h.reply(c=>{late=c;entered();});
  const pending=h.start(path,{...a,method:'POST',value:payload});await started;const b=await h.signed('B',a.cookie);const old=await pending.promise;
  assert.ok([401,409].includes(old.status));assert.equal(old.body.datasetVersion,undefined);assert.equal(old.headers['set-cookie'],undefined);
  late.deliver(fixture);await tick();const me=await h.send('/api/v1/me',{cookie:b.cookie});assert.equal(me.status,200);assert.equal(me.body.user.email,'b@example.invalid');
});
test('global deadline covers input/upstream, GET times out, session expiry wins over a late report',async t=>{
  const h=harness(t,{importsEnabled:true}),s=await h.signed();
  const unsent=h.start(path,{...s,method:'POST',sendBody:false});h.expire();assert.equal((await unsent.promise).body.error.code,'import_timeout');assert.equal(h.bridges.length,0);
  let late,entered;let started=new Promise(done=>entered=done);h.reply(c=>{late=c;entered();});const post=h.start(path,{...s,method:'POST',value:payload});await started;h.expire();assert.equal((await post.promise).body.error.code,'import_result_unknown');late.deliver(fixture);
  started=new Promise(done=>entered=done);const get=h.start(read,s);await started;h.expire();assert.equal((await get.promise).body.error.code,'import_timeout');late.deliver(fixture);
  started=new Promise(done=>entered=done);const expiry=h.start(read,s);await started;h.advance(900001);late.deliver(fixture);const expired=await expiry.promise;assert.equal(expiry.res.destroyed,true);assert.equal(expired.body,undefined);assert.notEqual(expired.status,200);
});
test('subject admission happens before body/upstream and pending disconnect cannot emit late success',async t=>{
  const h=harness(t,{importsEnabled:true}),s=await h.signed();let late,entered;const started=new Promise(done=>entered=done);h.reply(c=>{late=c;entered();});
  const pending=h.start(path,{...s,method:'POST',value:payload});await started;const before=h.bridges.length;
  const blocked=await h.send(read,s);assert.equal(blocked.status,429);assert.equal(h.bridges.length,before);
  pending.req.aborted=true;pending.req.emit('aborted');const result=await pending.promise;assert.equal(result.status,undefined);assert.equal(pending.res.headersSent,false);
  late.deliver(fixture);await tick();assert.equal(pending.res.headersSent,false);
});
test('finite writing rate and safe exact error issue schema reject leaks',async t=>{
  const h=harness(t,{importsEnabled:true}),s=await h.signed();h.reply(c=>c.deliver(fixture));
  for(let i=0;i<10;i++)assert.equal((await h.send(path,{...s,method:'POST',value:payload})).status,200);
  assert.equal((await h.send(path,{...s,method:'POST',value:payload})).body.error.code,'import_rate_limited');assert.equal(h.bridges.length,10);
  assert.ok(validateImportError(422,{error:{code:'txt_import_failed',issues:[issue]}}));
  for(const value of [{error:{code:'txt_import_failed',issues:Array(9).fill(issue)}},{error:{code:'txt_import_failed',issues:[{...issue,record_number:-1}]}},{error:{code:'import_result_unknown',version_key:'bad'}},{error:{code:'import_busy',version_key:vid}},{error:{code:'import_busy',detail:'PRIVATE'}}])assert.throws(()=>validateImportError(value.error.code==='txt_import_failed'?422:value.error.code==='import_result_unknown'?504:429,value));
});
