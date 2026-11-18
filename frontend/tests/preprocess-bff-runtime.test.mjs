import test from 'node:test';import assert from 'node:assert/strict';
import {session,harness,job,requestSpy,payload,collection,lookup,detail,pid,vid,jid,rid,until,tick} from './support/preprocess-bff-setup.mjs';
const expected=(r,status,code)=>{assert.equal(r.status,status);if(code)assert.equal(r.body.error.code,code);assert.equal(r.headers['cache-control'],'no-store');assert.equal(r.headers['x-content-type-options'],'nosniff');};

test('default-off/explicit-off/missing request are inert503 in runtime and mock server factory',async t=>{
 for(const useServer of [false,true])for(const opts of [{flag:'default'},{flag:false},{requestConfigured:false}]){
  const h=session(t,{useServer,...opts}),before=h.auth.length;
  for(const [url,method]of [[collection,'POST'],[lookup,'GET'],[detail,'GET']])expected(await h.send(url,{method,value:payload}),503,'preprocessing_unavailable');
  assert.equal(h.calls.length,0);assert.equal(h.auth.length,before);assert.equal(h.bridges.length,0);
 }
});
test('active anonymous401; nonce is not identity; mismatched or foreign context409 without upstream',async t=>{
 const h=session(t);expected(await h.send(detail,{view:'Q'.repeat(22)}),401,'unauthenticated');
 const a=await h.signed(),b=await h.signed('B'),before=h.auth.length;
 expected(await h.send(detail,{cookie:a.cookie,view:'Q'.repeat(22)}),409,'project_context_changed');
 expected(await h.send(detail,{view:a.view}),401,'unauthenticated');
 expected(await h.send(detail,{cookie:b.cookie,view:a.view}),409,'project_context_changed');
 assert.equal(h.calls.length,0);assert.equal(h.auth.length,before);
});
test('three operations use only session bearer and direct queued DTO; server wiring never leaks browser credentials',async t=>{
 for(const useServer of [false,true]){
  const h=session(t,{useServer}),s=await h.signed();
  for(const [url,method]of [[collection,'POST'],[lookup,'GET'],[detail,'GET']]){
   const r=await h.send(url,{...s,method,value:payload,headers:{'x-extra-credential':'PRIVATE_EXTRA'}});
   expected(r,method==='POST'?201:200);assert.deepEqual(r.body,job());assert.equal(Object.keys(r.body).length,16);
   assert.doesNotMatch(r.wire,/ACCESS_A|PRIVATE_EXTRA|cyomics_session|SYNTHETIC_PASSWORD/);assert.equal(r.headers['set-cookie'],undefined);
  }
  assert.equal(h.calls.length,3);assert.equal(h.bridges.length,0);
  for(const c of h.calls){assert.equal(c.options.headers.Authorization,'Bearer ACCESS_A');assert.equal(c.options.hostname,'127.0.0.1');assert.equal(c.options.port,57430);assert.equal(c.options.headers.Cookie,undefined);assert.equal(c.options.headers['X-Cyomics-Account-Context'],undefined);assert.equal(c.options.headers['x-extra-credential'],undefined);assert.ok(Number(c.options.headers['X-Cyomics-Remaining-Ms'])<=12000);assert.ok(Number(c.options.headers['X-Cyomics-Deadline-Unix-Ms'])<=h.now()+12000);}
 }
});
test('forged/duplicate/abnormal browser headers and near routes close before any upstream or broad bridge',async t=>{
 const h=session(t),s=await h.signed();
 for(const headers of [{authorization:'Bearer CLIENT_FORGED'},{'x-owner-id':pid},{'x-cyomics-remaining-ms':'1000'},{'x-cyomics-deadline-unix-ms':'1795000000000'},{origin:'https://evil.invalid'},{host:'evil.invalid'},{'sec-fetch-site':'cross-site'},{'x-extra':'bad\r\nheader'}])expected(await h.send(detail,{...s,headers}),403,'auth_origin_rejected');
 const headers={host:'127.0.0.1:4206',origin:'http://127.0.0.1:4206',cookie:s.cookie,'x-cyomics-account-context':s.view};
 expected(await h.send(detail,{...s,rawHeaders:Object.entries(headers).flat().concat(['X-Test','a','x-test','b'])}),403,'auth_origin_rejected');
 for(const url of [detail+'/artifacts/x',collection.replace('/preprocessing-jobs','/preprocessing-jobs/../sample-designs'),collection.replace('/datasets/','/%64atasets/'),collection.replace('?','/?')]){const r=await h.send(url,{...s});assert.ok([400,404].includes(r.status));}
 assert.equal(h.calls.length,0);assert.equal(h.bridges.length,0);
});
test('GET0 POST4KiB exact framing and numeric spellings are rejected before session bearer is sent',async t=>{
 const h=session(t),s=await h.signed(),text=JSON.stringify(payload);
 for(const opts of [{method:'GET',wire:'x'},{method:'GET',headers:{'content-length':'1'}},{method:'GET',headers:{'transfer-encoding':'chunked'}}])expected(await h.send(detail,{...s,...opts}),400,'invalid_preprocessing_request');
 for(const wire of [text.replace('"pseudocount":0','"pseudocount":0.0'),text.replace('"pseudocount":0','"pseudocount":0e0'),text.replace('"pseudocount":0','"pseudocount":-0'),text.replace('"contaminant":true','"contaminant":"true"'),JSON.stringify({...payload,source:{}})])expected(await h.send(collection,{...s,method:'POST',wire}),400,'invalid_preprocessing_request');
 expected(await h.send(collection,{...s,method:'POST',wire:Buffer.alloc(4097)}),413,'preprocessing_request_too_large');
 expected(await h.send(collection,{...s,method:'POST',value:payload,headers:{'content-length':'2','transfer-encoding':'chunked'}}),400,'invalid_preprocessing_request');
 assert.equal(h.calls.length,0);
});
test('typed conflict/unknown forwarded once; unknown POST recovered only by manual GET same request',async t=>{
 for(const [status,code]of [[409,'preprocessing_request_conflict'],[503,'preprocessing_unavailable'],[504,'preprocessing_submission_unknown']]){
  const h=session(t,{handler:c=>c.reply({error:{code}},status)}),s=await h.signed();expected(await h.send(collection,{...s,method:'POST',value:payload}),status,code);assert.equal(h.calls.length,1);
 }
 const h=session(t,{handler:c=>c.options.method==='POST'?c.reply({error:{code:'preprocessing_submission_unknown'}},504):c.reply(job(),200)}),s=await h.signed();
 expected(await h.send(collection,{...s,method:'POST',value:payload}),504,'preprocessing_submission_unknown');
 expected(await h.send(lookup,{...s}),200);assert.equal(h.calls.length,2);assert.deepEqual(h.calls.map(c=>c.options.method),['POST','GET']);assert.equal(h.calls[1].options.path,lookup);
});
test('wrong source response GET503 and sent POST unknown; foreign session bearer preserves backend404',async t=>{
 for(const method of ['GET','POST']){
  const h=session(t,{handler:c=>{const value=job();value.source.dataset_version_id=jid;c.reply(value,method==='POST'?201:200);}}),s=await h.signed();
  expected(await h.send(method==='POST'?collection:lookup,{...s,method,value:payload}),method==='POST'?504:503,method==='POST'?'preprocessing_submission_unknown':'preprocessing_unavailable');assert.equal(h.calls.length,1);
 }
 const h=session(t,{handler:c=>c.reply({error:{code:'dataset_not_found'}},404)}),s=await h.signed('B');
 expected(await h.send(lookup,{...s}),404,'dataset_not_found');assert.equal(h.calls[0].options.headers.Authorization,'Bearer ACCESS_B');
});
test('logout or same-browser account replacement aborts pending POST; late queued bytes never publish',async t=>{
 for(const mode of ['logout','replace']){
  const h=session(t,{handler:()=>{}}),s=await h.signed(),pending=h.start(collection,{...s,method:'POST',value:payload});await until(()=>h.calls.length===1&&h.calls[0].ended);
  if(mode==='logout')await h.send('/api/auth/logout',{cookie:s.cookie,method:'POST',value:{}});else await h.signed('B',s.cookie);
  const result=await pending.promise;expected(result,504,'preprocessing_submission_unknown');assert.equal(h.calls[0].out.destroyed,true);
  h.calls[0].reply(job(),201);await tick();assert.equal(pending.res.writes.some(v=>v.toString().includes('binding_sha256')),false);assert.equal(h.calls.length,1);
 }
});
test('upstream401 revokes session and shared legacy operations; old project401 aborts preprocessing',async t=>{
 const h=session(t,{handler:()=>{}}),s=await h.signed();h.reply(()=>{});
 const project=h.start('/api/v1/projects',{...s}),one=h.start(detail,{...s}),two=h.start(lookup,{...s});
 await until(()=>h.calls.length===2&&h.bridges.length===1);h.calls[0].reply({error:{code:'unauthenticated'}},401);
 expected(await one.promise,401,'unauthenticated');await two.promise;await project.promise;
 assert.equal(h.calls[1].out.destroyed,true);assert.equal(h.bridges[0].out.destroyed,true);expected(await h.send('/api/v1/me',{cookie:s.cookie}),401,'unauthenticated');
 const other=session(t,{handler:()=>{}}),current=await other.signed();other.reply(()=>{});
 const queued=other.start(detail,{...current});await until(()=>other.calls.length===1);
 const old=other.start('/api/v1/projects',{...current});await until(()=>other.bridges.length===1);
 other.bridges[0].deliver({error:{code:'unauthenticated'}},401);await old.promise;await queued.promise;
 assert.equal(other.calls[0].out.destroyed,true);assert.equal(queued.res.writes.some(v=>v.toString().includes('binding_sha256')),false);
});
test('12s total clock and browser source abort cannot emit late success or retry',async t=>{
 for(const mode of ['elapsed','disconnect']){
  const h=session(t,{handler:()=>{}}),s=await h.signed(),pending=h.start(collection,{...s,method:'POST',value:payload});await until(()=>h.calls.length===1);
  if(mode==='elapsed'){h.advance(12001);h.calls[0].reply(job(),201);}else{pending.req.aborted=true;pending.req.emit('aborted');pending.res.destroy();}
  const r=await pending.promise;if(r.status!==undefined)assert.equal(r.status,504);
  h.calls[0].reply(job(),201);await tick();assert.equal(h.calls[0].out.destroyed,true);assert.equal(h.calls.length,1);assert.equal(pending.res.writes.some(v=>v.toString().includes('binding_sha256')),false);
 }
 const h=session(t),s=await h.signed(),stalled=h.start(collection,{...s,method:'POST',sendBody:false});await until(()=>stalled.req.listenerCount('data')>0);h.advance(12001);h.expire();await stalled.promise;assert.equal(h.calls.length,0);assert.equal(stalled.req.listenerCount('data'),0);
});
test('response backpressure retains session fence through finish; no drain after browser closes',async t=>{
 const h=session(t),s=await h.signed(),pending=h.start(detail,{...s,response:{writeResults:[false],autoFinish:false}});
 await until(()=>pending.res.writes.length===1);pending.res.destroy();await pending.promise;pending.res.emit('drain');
 assert.equal(h.calls.length,1);assert.equal(pending.res.writes.length,1);assert.equal(pending.res.endCalls.length,0);
});
test('session expiry tightens both forwarded remaining and shared deadline without renewal',async t=>{
 let h;const user={id:'11111111-1111-4111-8111-111111111111',email:'a@example.invalid',email_confirmed_at:'2026-11-15T00:00:00Z',aud:'authenticated'};
 h=session(t,{handler:()=>{},fetchImpl:async(_url,opts)=>Response.json(opts.method==='POST'?{access_token:'ACCESS_A',refresh_token:'SYNTHETIC_REFRESH',token_type:'bearer',expires_in:5,expires_at:Math.floor(h.now()/1000)+5,user}:user)});
 const s=await h.signed(),pending=h.start(collection,{...s,method:'POST',value:payload});await until(()=>h.calls.length===1);
 assert.ok(Number(h.calls[0].options.headers['X-Cyomics-Remaining-Ms'])<=5000);assert.ok(Number(h.calls[0].options.headers['X-Cyomics-Deadline-Unix-Ms'])<=h.now()+5000);
 h.advance(5001);h.calls[0].reply(job(),201);await pending.promise;assert.equal(h.calls[0].out.destroyed,true);assert.equal(pending.res.writes.some(v=>v.toString().includes('job_id')),false);
});

// Only related old boundaries, no legacy suite refresh. RawQC/Auth implementations
// are independently byte-checked unchanged by the scope verifier.
test('old RawQC three routes still use old status/envelope through unchanged runtime and server factory',async t=>{
 for(const useServer of [false,true]){
  const v=job(),execution={script:{bytes:1,sha256:'a'.repeat(64)},runner:{bytes:1,sha256:'a'.repeat(64)},output_validator:{bytes:1,sha256:'a'.repeat(64)},executable:{bytes:1,sha256:'a'.repeat(64)},r_version:'R version 4.6.1',entry_kind:'native_R',limits_profile:'raw_qc_limits_v3',limits_sha256:'a'.repeat(64)};
  const old={schemaVersion:1,job_id:jid,request_id:rid,request_sha256:'a'.repeat(64),binding_sha256:'b'.repeat(64),source:v.source,quant_family:'Intensity',params:{},execution,status:'queued',revision:1,created_at:v.created_at,started_at:null,finished_at:null,observation:'confirmed',error:null,result:null};
  const spy=requestSpy(c=>c.reply(old,c.options.method==='POST'?202:200)),h=harness(t,{useServer,rawQcEnabled:true,rawQcRequest:spy.requestImpl}),s=await h.signed();
  const c=collection.replace('preprocessing-jobs','raw-qc-jobs'),l=c+'&request_id='+rid,d=detail.replace('preprocessing-jobs','raw-qc-jobs');
  for(const [url,method]of [[c,'POST'],[l,'GET'],[d,'GET']])expected(await h.send(url,{...s,method,value:{schemaVersion:1,request_id:rid,quant_family:'intensity',params:{}}}),method==='POST'?202:200);
  assert.equal(spy.calls.length,3);
 }
});
