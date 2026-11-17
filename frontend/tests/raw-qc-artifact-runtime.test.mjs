import test from 'node:test';import assert from 'node:assert/strict';
import {session,path,bytes,headers,close,tick,until,sha,pid} from './support/raw-qc-artifact-setup.mjs';
import {ARTIFACT_NAMES} from '../raw-qc-artifact-bridge.mjs';
import {requestSpy} from './support/raw-qc-bff-setup.mjs';
const expect=(r,status,code)=>{assert.equal(r.status,status);if(code)assert.equal(r.body.error.code,code);assert.equal(r.headers['cache-control'],'no-store');};
test('default-off and every gate or missing request implementation performs no Auth or upstream binary request',async t=>{
 for(const useServer of [false,true])for(const flags of [{rawQcArtifactsEnabled:false},{rawQcArtifactsEnabled:undefined},{rawQcEnabled:false},{projectsEnabled:false},{enabled:false}]){
  const h=session(t,{useServer,flags}),before=h.auth.length;
  const r=await h.send(path('raw-matrix.tsv'));expect(r,flags.rawQcArtifactsEnabled!==true&&Object.hasOwn(flags,'rawQcArtifactsEnabled')?404:503);
  assert.equal(h.auth.length,before);assert.equal(h.calls.length,0);
 }
 const h=session(t,{requestConfigured:false});expect(await h.send(path('raw-matrix.tsv')),503,'raw_qc_unavailable');assert.equal(h.auth.length,0);assert.equal(h.calls.length,0);
});
test('seven fixed names use captured real harness session, fixed request and verified binary response (including server factory)',async t=>{
 for(const useServer of [false,true]){
  const h=session(t,{useServer,handler:call=>call.reply(bytes,200,headers(bytes,call.options.path.split('/artifacts/')[1].split('?')[0]))}),s=await h.signed();
  for(const name of ARTIFACT_NAMES){const r=await h.send(path(name),s);expect(r,200);assert.deepEqual(r.body,bytes);assert.equal(r.headers['content-type'],'application/octet-stream');assert.equal(r.headers['content-disposition'],'attachment; filename="'+name+'"');assert.equal(r.headers['x-cyomics-file-sha256'],sha(bytes));assert.equal(r.headers['x-content-type-options'],'nosniff');assert.equal(r.headers['referrer-policy'],'no-referrer');}
  assert.equal(h.bridges.length,0);for(const call of h.calls){assert.equal(call.options.hostname,'127.0.0.1');assert.equal(call.options.port,57430);assert.equal(call.options.agent,false);assert.equal(call.options.headers.Authorization,'Bearer ACCESS_A');assert.equal(call.options.headers.Cookie,undefined);assert.equal(call.options.headers['X-Owner-Id'],undefined);assert.ok(Number(call.options.headers['X-Cyomics-Remaining-Ms'])<=9000);assert.ok(Number(call.options.headers['X-Cyomics-Deadline-Unix-Ms'])<=h.now()+9000);}
 }
});
test('session and account nonce are both required; forged identity/ranges/conditions/framing never reach upstream',async t=>{
 const h=session(t),s=await h.signed();expect(await h.send(path('raw-matrix.tsv'),{view:s.view}),401,'unauthenticated');
 expect(await h.send(path('raw-matrix.tsv'),{...s,view:'Q'.repeat(22)}),409,'project_context_changed');
 for(const [header,value,status]of [['authorization','Bearer FORGED',403],['x-owner-id',pid,403],['x-cyomics-remaining-ms','9000',403],['range','bytes=0-9',400],['if-none-match','x',400],['if-range','x',400],['content-length','1',400],['content-length','00',400],['transfer-encoding','chunked',400]])expect(await h.send(path('raw-matrix.tsv'),{...s,headers:{[header]:value}}),status);
 const r=await h.send(path('raw-matrix.tsv'),{...s,wire:'BODY'});expect(r,400);assert.equal(h.calls.length,0);
});
test('artifact ownership precedes JSON job bridge; traversal/encoding/query/method variants never reach a bridge',async t=>{
 const h=session(t),s=await h.signed();
 for(const url of [path('raw-matrix.tsv').replace('artifacts','%61rtifacts'),path('raw-matrix.tsv').replace('/api/','/%2561pi/'),path('raw-matrix.tsv')+'&x=1',path('raw-matrix.tsv')+'&project_id='+pid,path('../raw-matrix.tsv'),path('raw-matrix.tsv').replace('?','/?'),path('unknown.tsv'),path('raw-matrix.tsv').replace('project_id=','project_id=%')]){const r=await h.send(url,s);assert.ok([400,404].includes(r.status));}
 expect(await h.send(path('raw-matrix.tsv'),{...s,method:'HEAD'}),405);assert.equal(h.calls.length,0);assert.equal(h.bridges.length,0);
});
test('each strict raw-header/hash/size/EOF attack produces no browser binary200',async t=>{
 const attacks=[
  raw=>raw.concat(['Content-Length',String(bytes.length)]),raw=>raw.concat(['X-Cyomics-File-Sha256',sha(bytes)]),
  raw=>raw.concat(['Location','http://evil.invalid']),raw=>raw.concat(['Refresh','0']),raw=>raw.concat(['Set-Cookie','secret=x']),
  raw=>raw.concat(['Transfer-Encoding','chunked']),raw=>raw.concat(['Content-Encoding','gzip']),
  raw=>raw.map((v,i)=>i===1?'application/octet-stream; charset=utf-8':v),
  raw=>raw.map((v,i)=>i===3?'0'+bytes.length:v),raw=>raw.map((v,i)=>i===3?'67108865':v),
  raw=>raw.map((v,i)=>i===5?'attachment; filename="summary.tsv"':v),raw=>raw.map((v,i)=>i===7?'a'.repeat(64):v),
  raw=>raw.map((v,i)=>i===7?sha(bytes).toUpperCase():v),raw=>raw.map((v,i)=>i===3?String(bytes.length+1):v),
  raw=>raw.map((v,i)=>i===7?sha(bytes)+', '+sha(bytes):v),
 ];
 for(const mutate of attacks){const h=session(t,{handler:c=>c.reply(bytes,200,mutate(headers()))}),s=await h.signed();const r=await h.send(path('raw-matrix.tsv'),s);assert.notEqual(r.status,200);assert.notEqual(r.headers['content-type'],'application/octet-stream');assert.equal(h.calls.length,1);}
 const h=session(t,{handler:c=>{const input=c.response();input.emit('data',bytes);input.emit('end');close(input);close(c.out);}}),s=await h.signed();assert.notEqual((await h.send(path('raw-matrix.tsv'),s)).status,200);
});
test('error allowlist accepts typed new errors and rejects extra/duplicate/oversize/local-context upstream errors',async t=>{
 for(const [status,code]of [[404,'raw_qc_artifact_not_found'],[409,'raw_qc_result_not_ready'],[503,'raw_qc_artifact_integrity_failed'],[429,'raw_qc_capacity']]){
  const h=session(t,{handler:c=>c.reply({error:{code}},status)}),s=await h.signed();expect(await h.send(path('raw-matrix.tsv'),s),status,code);
 }
 for(const data of [Buffer.from('{"error":{"code":"raw_qc_result_not_ready"},"extra":1}'),Buffer.from('{"error":{"code":"raw_qc_result_not_ready","code":"raw_qc_result_not_ready"}}'),Buffer.alloc(4097),Buffer.from('{"error":{"code":"project_context_changed"}}')]){
  const h=session(t,{handler:c=>c.reply(data,409,['Content-Type','application/json','Content-Length',String(data.length)])}),s=await h.signed();expect(await h.send(path('raw-matrix.tsv'),s),503,'raw_qc_artifact_integrity_failed');
 }
});
test('single runtime lease is busy without queue during fetch and send; successful confirmed close permits next download',async t=>{
 const h=session(t,{handler:()=>{}}),s=await h.signed(),pending=h.start(path('raw-matrix.tsv'),s);await until(()=>h.calls.length===1);
 expect(await h.send(path('summary.tsv'),s),429,'raw_qc_capacity');assert.equal(h.calls.length,1);
 h.calls[0].reply();expect(await pending.promise,200);
 const next=h.start(path('raw-matrix.tsv'),{...s,response:{writeResults:[false],autoFinish:false}});await until(()=>h.calls.length===2);h.calls[1].reply();await until(()=>next.res.writes.length===1);
 expect(await h.send(path('summary.tsv'),s),429,'raw_qc_capacity');next.res.emit('drain');next.res.finish();expect(await next.promise,200);
});
test('logout cancels owned streams; late incoming cannot publish or reopen unknown capacity',async t=>{
 const h=session(t,{handler:()=>{},transport:{ignoreOutDestroy:true,ignoreIncomingDestroy:true}}),s=await h.signed(),pending=h.start(path('raw-matrix.tsv'),s);await until(()=>h.calls.length===1);
 await h.send('/api/auth/logout',{cookie:s.cookie,method:'POST',value:{}});await pending.promise;assert.equal(h.calls[0].out.destroyed,true);assert.equal(pending.res.destroyed,true);
 const fresh=await h.signed('B');expect(await h.send(path('summary.tsv'),fresh),503,'raw_qc_unavailable');assert.equal(h.calls.length,1);
 h.calls[0].reply();await tick();assert.equal(pending.res.writes.length,0);assert.equal(h.calls[0].input.destroyed,true);close(h.calls[0].input);close(h.calls[0].out);
 expect(await h.send(path('summary.tsv'),fresh),503,'raw_qc_unavailable');
});
test('legacy project401 and QC401 cancel artifact; artifact401 revokes session and pending project before drain',async t=>{
 for(const route of ['/api/v1/projects','/api/v1/raw-qc-jobs/33333333-3333-4333-8333-333333333333?project_id='+pid]){
  const json=requestSpy(()=>{}),h=session(t,{handler:()=>{},flags:{rawQcRequest:json.requestImpl}}),s=await h.signed();h.reply(()=>{});const pending=h.start(path('raw-matrix.tsv'),s);await until(()=>h.calls.length===1);
  const other=h.start(route,s);if(route.startsWith('/api/v1/projects')){await until(()=>h.bridges.length===1);h.bridges[0].deliver({error:{code:'unauthenticated'}},401);}else{await until(()=>json.calls.length===1);json.calls[0].reply({error:{code:'unauthenticated'}},401);}await other.promise;await pending.promise;assert.equal(h.calls[0].out.destroyed,true);
 }
 const h=session(t,{handler:c=>c.reply({error:{code:'unauthenticated'}},401)}),s=await h.signed();h.reply(()=>{});const project=h.start('/api/v1/projects',s);await until(()=>h.bridges.length===1);
 const r=await h.send(path('raw-matrix.tsv'),s);expect(r,401,'unauthenticated');await project.promise;assert.equal(h.bridges[0].out.destroyed,true);expect(await h.send('/api/v1/me',{cookie:s.cookie}),401,'unauthenticated');
});
test('wall/monotonic deadline, session expiry and replacement block late bytes without relying on timer callback',async t=>{
 for(const which of ['wall','mono','both']){const h=session(t,{handler:()=>{}}),s=await h.signed(),pending=h.start(path('raw-matrix.tsv'),s);await until(()=>h.calls.length===1);h.advance(45001,which);h.calls[0].reply();await pending.promise;assert.equal(pending.res.writes.some(x=>x.equals(bytes)),false);}
 const h=session(t,{handler:()=>{}}),s=await h.signed(),pending=h.start(path('raw-matrix.tsv'),s);await until(()=>h.calls.length===1);h.advance(900001);h.calls[0].reply();await pending.promise;assert.equal(pending.res.writes.length,0);
 const next=session(t,{handler:()=>{}}),a=await next.signed(),old=next.start(path('raw-matrix.tsv'),a);await until(()=>next.calls.length===1);await next.signed('B',a.cookie);await old.promise;assert.equal(next.calls[0].out.destroyed,true);
});
test('send ignoring destroy/abort cannot release capacity or write later private chunks',async t=>{
 const data=Buffer.alloc(131072,120),h=session(t,{handler:c=>c.reply(data)}),s=await h.signed();
 const pending=h.start(path('raw-matrix.tsv'),{...s,response:{writeResults:[false],autoFinish:false,ignoreResponseClose:true}});await until(()=>pending.res.writes.length===1);
 h.runtime.dispose();await pending.promise;assert.equal(pending.res.writes.length,1);assert.ok(pending.res.destroyCalls>=1);pending.res.emit('drain');await tick();assert.equal(pending.res.writes.length,1);
 // Disabled/disposed runtime also refuses; retained lease cannot be replaced.
 expect(await h.send(path('raw-matrix.tsv'),s),503,'raw_qc_unavailable');close(pending.res);
});
test('partial upstream and EOF/hash verification precede any200 headers; 9s upstream timeout is not the old JSON12s',async t=>{
 const h=session(t,{handler:()=>{}}),s=await h.signed(),pending=h.start(path('raw-matrix.tsv'),s);await until(()=>h.calls.length===1);
 const input=h.calls[0].response();input.emit('data',bytes);assert.equal(pending.res.headersSent,false);await tick();assert.equal(pending.res.headersSent,false);
 input.complete=true;input.emit('end');close(input);close(h.calls[0].out);expect(await pending.promise,200);
 const second=h.start(path('raw-matrix.tsv'),s);await until(()=>h.calls.length===2);
 const deadline=[...h.timers.values()].find(timer=>timer.ms<=9000);assert.ok(deadline);deadline.fn();expect(await second.promise,504,'raw_qc_timeout');assert.equal(h.calls[1].out.destroyed,true);
});
test('normalized/raw disagreement, duplicate browser nonce and JSON success cannot bypass binary validation',async t=>{
 for(const beforeCallback of [input=>{input.headers['content-length']='1';},input=>{input.headers['set-cookie']=['SECRET=x'];}]){
  const h=session(t,{transport:{beforeCallback}}),s=await h.signed();expect(await h.send(path('raw-matrix.tsv'),s),503,'raw_qc_artifact_integrity_failed');
 }
 const h=session(t),s=await h.signed();
 const reqHeaders={host:'127.0.0.1:4206',origin:'http://127.0.0.1:4206',cookie:s.cookie,'x-cyomics-account-context':s.view};
 expect(await h.send(path('raw-matrix.tsv'),{...s,rawHeaders:Object.entries(reqHeaders).flat().concat(['X-Cyomics-Account-Context',s.view])}),403);assert.equal(h.calls.length,0);
 const j=session(t,{handler:c=>c.reply({schemaVersion:1,job_id:'SECRET_JOB'},200,['Content-Type','application/json','Content-Length','45'])}),a=await j.signed();assert.notEqual((await j.send(path('raw-matrix.tsv'),a)).status,200);
});
test('45s stalled sender ignoring destroy keeps capacity quarantined even when runtime and session remain live',async t=>{
 const data=Buffer.alloc(131072,121),h=session(t,{handler:c=>c.reply(data)}),s=await h.signed();
 const pending=h.start(path('raw-matrix.tsv'),{...s,response:{writeResults:[false],autoFinish:false,ignoreResponseClose:true}});await until(()=>pending.res.writes.length===1);
 const deadline=[...h.timers.values()].find(timer=>timer.ms===45000);assert.ok(deadline);deadline.fn();await pending.promise;
 expect(await h.send(path('summary.tsv'),s),503,'raw_qc_unavailable');assert.equal(h.calls.length,1);
 pending.res.emit('drain');await tick();assert.equal(pending.res.writes.length,1);close(pending.res);
 expect(await h.send(path('summary.tsv'),s),503,'raw_qc_unavailable');assert.equal(h.calls.length,1);
});
test('me401 revokes artifact through the existing session-removal cancellation set',async t=>{
 const h=session(t,{handler:()=>{}}),s=await h.signed(),pending=h.start(path('raw-matrix.tsv'),s);await until(()=>h.calls.length===1);
 h.mode('bad');expect(await h.send('/api/v1/me',{cookie:s.cookie}),401,'unauthenticated');await pending.promise;assert.equal(h.calls[0].out.destroyed,true);assert.equal(pending.res.writes.length,0);
});
test('body-stage lease and45s deadline precede upstream; confirmed cancellation permits reuse; expiry shortens hints',async t=>{
 const h=session(t),s=await h.signed(),pending=h.start(path('raw-matrix.tsv'),{...s,sendBody:false});await until(()=>pending.req.listenerCount('data')>0);
 expect(await h.send(path('summary.tsv'),s),429,'raw_qc_capacity');assert.equal(h.calls.length,0);
 [...h.timers.values()].find(timer=>timer.ms===45000).fn();await pending.promise;assert.equal(h.calls.length,0);assert.equal(pending.req.destroyed,true);
 expect(await h.send(path('raw-matrix.tsv'),s),200);assert.equal(h.calls.length,1);
 const short=session(t),a=await short.signed();short.advance(899999);expect(await short.send(path('raw-matrix.tsv'),a),200);
 assert.equal(short.calls[0].options.headers['X-Cyomics-Remaining-Ms'],'1');assert.equal(Number(short.calls[0].options.headers['X-Cyomics-Deadline-Unix-Ms']),short.now()+1);
});
test('9s upstream absolute wall deadline also fences a late reply while45s BFF budget and monotonic clock remain live',async t=>{
 const h=session(t,{handler:()=>{}}),s=await h.signed(),pending=h.start(path('raw-matrix.tsv'),s);await until(()=>h.calls.length===1);
 h.advance(9001,'wall');h.calls[0].reply();const r=await pending.promise;assert.notEqual(r.status,200);assert.equal(pending.res.writes.some(chunk=>chunk.equals(bytes)),false);
 assert.equal(h.calls[0].out.destroyed,true);assert.equal(h.calls[0].input.destroyed,true);
});
