// Offline event streams and fake SDK only. No listener, live Auth, DB or R.
import test from 'node:test';import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';import {createHash} from 'node:crypto';
import {createPreprocessBridge,preprocessRoute,preprocessReply,preprocessCursor,preprocessError,ownsPreprocessRoute,PREPROCESS_ARTIFACT_MAX} from '../preprocess-bridge.mjs';
import {requestSpy,args,session,pid,vid,until,tick} from './support/preprocess-bff-setup.mjs';
const root=new URL('./fixtures/preprocess-v2-contract/',import.meta.url);
const fixture=name=>{
 const relative=name==='R1/failed-null-runtime-invalid.json'?'final-failed-state/failed-null-runtime-invalid.json':name.startsWith('R1/')?'R1-addendum/fixtures/'+name:'fixtures/'+name;
 return JSON.parse(readFileSync(new URL(relative,root)));
};
const first=fixture('R1/list-first-page.json'),second=fixture('R1/list-second-page.json'),version=fixture('positive/processed-version.json');
const collection='/api/v1/datasets/'+vid+'/preprocessing-jobs?project_id='+pid;
const versionPath='/api/v1/processed-dataset-versions/'+version.processed_dataset_version_id+'?project_id='+pid;
const artifactPath=name=>versionPath.split('?')[0]+'/artifacts/'+name+'?project_id='+pid;
const sha=raw=>createHash('sha256').update(raw).digest('hex');
const headers=(body,name='working-matrix.tsv')=>['Content-Type','application/octet-stream','Content-Length',String(body.length),'Content-Disposition','attachment; filename="'+name+'"','X-Cyomics-File-SHA256',sha(body),'Cache-Control','no-store','X-Content-Type-Options','nosniff','Referrer-Policy','no-referrer'];

test('V2 exact20 keys all task states/version; failed requires claimed runtime and success receipt',()=>{
 for(const state of ['queued','running','reconciliation-pending','failed','succeeded']){
  const v=fixture('positive/'+state+'.json');assert.equal(Object.keys(preprocessReply(preprocessRoute(collection+'&request_id='+v.request_id,'GET'),200,v)).length,20);
 }
 assert.equal(preprocessReply(preprocessRoute(versionPath,'GET'),200,version),version);
 for(const name of ['schema1','nonqueued-without-runtime','extra-worker-flag','false-permission','wrong-parameters-sha','wrong-source-project'])assert.throws(()=>preprocessReply(preprocessRoute(collection+'&request_id='+first.items[0].request_id,'GET'),200,fixture('negative/'+name+'.json')));
 assert.throws(()=>preprocessReply(preprocessRoute(versionPath,'GET'),200,fixture('negative/premature-analysis-ready.json')));
 assert.throws(()=>preprocessReply(preprocessRoute(collection+'&request_id='+first.items[0].request_id,'GET'),200,fixture('R1/failed-null-runtime-invalid.json')));
});
test('actual 130char cursor fits 512query/1024target and second page roundtrip; keys exclusive',()=>{
 const cursor=first.next_cursor;assert.equal(cursor.length,130);const target=collection+'&limit=2&cursor='+cursor;
 assert.ok(Buffer.byteLength(target.split('?')[1])<512);const route=preprocessRoute(target,'GET');assert.equal(route.cursor,cursor);preprocessCursor(cursor);
 assert.equal(preprocessReply(preprocessRoute(collection,'GET'),200,first),first);assert.equal(preprocessReply(route,200,second),second);
 assert.ok(second.items[0].created_at+second.items[0].job_id<preprocessCursor(cursor).createdAt+preprocessCursor(cursor).job_id);
 for(const url of [target+'&request_id='+first.items[0].request_id,target+'&cursor='+cursor,collection+'&limit=01',collection+'&cursor='+'x'.repeat(193),collection+'&x='+'a'.repeat(513),collection+'a'.repeat(1025)])assert.throws(()=>preprocessRoute(url,'GET'));
 assert.equal(ownsPreprocessRoute(versionPath),true);assert.equal(ownsPreprocessRoute(artifactPath('parameters.json')),true);
});
test('BFF local project_context_changed409 is distinct from upstream API error whitelist',async()=>{
 assert.equal(preprocessError(409,{error:{code:'project_context_changed'}}).error.code,'project_context_changed');
 assert.throws(()=>preprocessError(409,{error:{code:'project_context_changed'}},{upstream:true}));
 const spy=requestSpy(c=>c.reply({error:{code:'project_context_changed'}},409));await assert.rejects(createPreprocessBridge(spy)(args()),e=>e.status===503);
});
test('bounded binary channel exact chunks bytes hash and upstream fixed headers',async()=>{
 const raw=Buffer.alloc(131073,65),spy=requestSpy(c=>{const input=c.response(200,headers(raw));for(let at=0;at<raw.length;at+=65536)input.emit('data',raw.subarray(at,at+65536));input.emit('end');input.emit('close');});
 const out=await createPreprocessBridge(spy)(args('GET',{route:preprocessRoute(artifactPath('working-matrix.tsv'),'GET')}));
 assert.equal(out.status,200);assert.deepEqual(out.artifact.body,raw);assert.equal(out.artifact.sha256,sha(raw));assert.equal(spy.calls[0].options.headers.Accept,'application/octet-stream');assert.equal(out.value,undefined);
});
test('binary rejects overlimit missing length/hash mismatched framing unsafe media redirects before release',async()=>{
 const raw=Buffer.from('PRIVATE_BINARY'),base=headers(raw);
 const cases=[base.filter((_,i)=>![2,3].includes(i)),base.map((v,i)=>i===3?String(PREPROCESS_ARTIFACT_MAX+1):v),base.map((v,i)=>i===7?'0'.repeat(64):v),base.map((v,i)=>i===1?'text/plain':v),[...base,'Location','https://foreign.invalid'],[...base,'Set-Cookie','SECRET=x'],[...base,'Content-Encoding','gzip'],base.map((v,i)=>i===3?String(raw.length-1):v),base.map((v,i)=>i===5?'attachment; filename="../secret"':v)];
 for(const h of cases){const spy=requestSpy(c=>c.reply(raw,200,h));await assert.rejects(createPreprocessBridge(spy)(args('GET',{route:preprocessRoute(artifactPath('working-matrix.tsv'),'GET')})));assert.equal(spy.calls.length,1);assert.equal(spy.calls[0].out.destroyed,true);}
 const bad=requestSpy(c=>c.reply({error:{code:'preprocessing_artifact_integrity_failed'}},503));const out=await createPreprocessBridge(bad)(args('GET',{route:preprocessRoute(artifactPath('working-matrix.tsv'),'GET')}));assert.equal(out.value.error.code,'preprocessing_artifact_integrity_failed');
});
test('new Owner routes recover without session local refs; foreign bearer and nonce remain fenced',async t=>{
 const raw=Buffer.from('x'),h=session(t,{handler:c=>c.options.path===versionPath?c.reply(version):c.options.path.includes('/artifacts/')?c.reply(raw,200,headers(raw)):c.reply(first)}),s=await h.signed();
 for(const path of [collection,versionPath,artifactPath('working-matrix.tsv')]){
  const out=await h.send(path,{...s});assert.equal(out.status,200);assert.equal(out.headers['cache-control'],'no-store');
  if(path.includes('/artifacts/')){assert.deepEqual(out.body,raw);assert.equal(out.headers['content-type'],'application/octet-stream');assert.equal(out.headers['x-cyomics-file-sha256'],sha(raw));assert.equal(out.headers['content-length'],'1');}
 }
 assert.equal(h.calls.length,3);const fresh=await h.signed('A',s.cookie);assert.equal((await h.send(versionPath,{...fresh})).status,200);
 const before=h.calls.length;assert.equal((await h.send(versionPath,{cookie:fresh.cookie,view:s.view})).status,409);assert.equal(h.calls.length,before);
});
test('binary response backpressure retains logout/session fence until finish; no late body after abort',async t=>{
 const raw=Buffer.alloc(65537,65),h=session(t,{handler:c=>c.reply(raw,200,headers(raw))}),s=await h.signed();
 const p=h.start(artifactPath('working-matrix.tsv'),{...s,response:{writeResults:[false],autoFinish:false}});await until(()=>p.res.writes.length===1);
 await h.send('/api/auth/logout',{cookie:s.cookie,method:'POST',value:{}});await p.promise;p.res.emit('drain');assert.equal(p.res.endCalls.length,0);assert.equal(h.calls.length,1);assert.equal(p.res.writes.length,1);
 const other=session(t,{handler:()=>{}}),current=await other.signed(),pending=other.start(artifactPath('working-matrix.tsv'),{...current});await until(()=>other.calls.length===1);
 pending.req.aborted=true;pending.req.emit('aborted');pending.res.destroy();await pending.promise;other.calls[0].reply(raw,200,headers(raw));await tick();assert.equal(pending.res.writes.length,0);assert.equal(other.calls[0].input.destroyed,true);
});
