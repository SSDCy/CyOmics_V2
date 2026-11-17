import test from 'node:test';import assert from 'node:assert/strict';
import {session,job,tick,until,collection,lookup,detail,payload,pid,vid,jid} from './support/raw-qc-bff-setup.mjs';
const expected=(r,status,code)=>{assert.equal(r.status,status);if(code)assert.equal(r.body.error.code,code);assert.equal(r.headers['cache-control'],'no-store');assert.equal(r.headers['x-content-type-options'],'nosniff');};
test('default-off/explicit-off/missing implementation respond503 before Auth and upstream including actual server factory',async t=>{
 for(const useServer of [false,true])for(const opts of [{flag:'default'},{flag:false},{requestConfigured:false}]){const h=session(t,{useServer,...opts}),before=h.auth.length;for(const [url,method]of [[collection,'POST'],[lookup,'GET'],[detail,'GET']])expected(await h.send(url,{method,value:payload}),503,'raw_qc_unavailable');assert.equal(h.calls.length,0);assert.equal(h.auth.length,before);}
});
test('nonce alone is not authentication; no session401 and wrong current nonce409 have no upstream',async t=>{
 const h=session(t);expected(await h.send(detail,{view:'Q'.repeat(22)}),401,'unauthenticated');const s=await h.signed();const auth=h.auth.length;expected(await h.send(detail,{cookie:s.cookie,view:'Q'.repeat(22)}),409,'project_context_changed');expected(await h.send(detail,{view:s.view}),401,'unauthenticated');assert.equal(h.calls.length,0);assert.equal(h.auth.length,auth);
});
test('browser forged bearer/Owner/budget/origin/host/duplicate context and foreign session context are denied',async t=>{
 const h=session(t),a=await h.signed(),b=await h.signed('B');
 for(const headers of [{authorization:'Bearer CLIENT_FORGED'},{'x-user-id':pid},{'x-owner-id':pid},{'x-cyomics-remaining-ms':'1000'},{'x-cyomics-deadline-unix-ms':'1795000000000'},{origin:'https://evil.invalid'},{host:'evil.invalid'},{'sec-fetch-site':'cross-site'}])expected(await h.send(detail,{cookie:a.cookie,view:a.view,headers}),403,'auth_origin_rejected');
 const headers={host:'127.0.0.1:4206',origin:'http://127.0.0.1:4206',cookie:a.cookie,'x-cyomics-account-context':a.view};const rawHeaders=Object.entries(headers).flat().concat(['X-Cyomics-Account-Context',a.view]);expected(await h.send(detail,{cookie:a.cookie,view:a.view,rawHeaders}),403,'auth_origin_rejected');expected(await h.send(detail,{cookie:b.cookie,view:a.view}),409,'project_context_changed');assert.equal(h.calls.length,0);
});
test('trusted session forwards exactly three operations with server bearer; response has no browser credentials',async t=>{
 for(const useServer of [false,true]){const h=session(t,{useServer}),s=await h.signed();for(const [url,method]of [[collection,'POST'],[lookup,'GET'],[detail,'GET']]){const r=await h.send(url,{...s,method,value:payload,headers:{'x-extra-credential':'PRIVATE_EXTRA'}});expected(r,method==='POST'?202:200);assert.equal(r.body.job_id,jid);assert.doesNotMatch(r.wire,/ACCESS_A|PRIVATE_EXTRA|cyomics_session|SYNTHETIC_PASSWORD/);assert.equal(r.headers['set-cookie'],undefined);}assert.equal(h.calls.length,3);for(const c of h.calls){assert.equal(c.options.hostname,'127.0.0.1');assert.equal(c.options.port,57430);assert.equal(c.options.headers.Authorization,'Bearer ACCESS_A');assert.equal(c.options.headers.Cookie,undefined);assert.equal(c.options.headers['X-Cyomics-Account-Context'],undefined);assert.equal(c.options.headers['x-extra-credential'],undefined);assert.equal(c.options.headers['X-Owner-Id'],undefined);assert.ok(Number(c.options.headers['X-Cyomics-Remaining-Ms'])<=12000);}assert.equal(h.bridges.length,0);}
});
test('encoded/traversal/artifact/list routes are owned early and rejected before any legacy bridge',async t=>{
 const h=session(t),s=await h.signed();for(const url of [collection,collection.replace('/raw-qc-jobs','/raw-qc-jobs/../sample-designs'),collection.replace('/datasets/','/%64atasets/'),detail+'/artifacts/raw-matrix.tsv?project_id='+pid]){const r=await h.send(url,{...s});assert.ok([400,404].includes(r.status));}assert.equal(h.calls.length,0);assert.equal(h.bridges.length,0);
});
test('GET body, POST oversized/duplicate/matrix/float/incorrect content framing never reach upstream',async t=>{
 const h=session(t),s=await h.signed();for(const opts of [{method:'GET',wire:'x'},{method:'GET',headers:{'content-length':'1'}},{method:'GET',headers:{'transfer-encoding':'chunked'}}]){const r=await h.send(detail,{...s,...opts});expected(r,400,'invalid_raw_qc_request');}
 for(const [wire,status]of [[Buffer.alloc(1025),413],[JSON.stringify(payload).replace('"schemaVersion":1','"schemaVersion":1.0'),400],[JSON.stringify({...payload,matrix:[]}),400],[JSON.stringify(payload).replace('"params":{}','"params":{},"params":{}'),400]])expected(await h.send(collection,{...s,method:'POST',wire}),status,status===413?'raw_qc_request_too_large':'invalid_raw_qc_request');
 expected(await h.send(collection,{...s,method:'POST',value:payload,headers:{'content-length':'2','transfer-encoding':'chunked'}}),400,'invalid_raw_qc_request');assert.equal(h.calls.length,0);
});
test('valid typed rejection409 remains business error; unknown504 is not retried or mapped to failed',async t=>{
 for(const [status,code]of [[409,'raw_qc_request_conflict'],[429,'raw_qc_capacity'],[503,'raw_qc_unavailable'],[504,'raw_qc_submission_unknown']]){const h=session(t,{handler:c=>c.reply({error:{code}},status)}),s=await h.signed();expected(await h.send(collection,{...s,method:'POST',value:payload}),status,code);assert.equal(h.calls.length,1);assert.equal((await h.send('/api/v1/me',{cookie:s.cookie})).status,200);}
});
test('logout during pending POST aborts transport and late result is not published; backend job is never retried',async t=>{
 const h=session(t,{handler:()=>{}}),s=await h.signed(),pending=h.start(collection,{...s,method:'POST',value:payload});await until(()=>h.calls.length===1&&h.calls[0].ended);await h.send('/api/auth/logout',{cookie:s.cookie,method:'POST',value:{}});const r=await pending.promise;expected(r,504,'raw_qc_submission_unknown');assert.equal(h.calls[0].out.destroyed,true);h.calls[0].reply(job(),202);await tick();assert.equal(pending.res.writes.some(v=>v.toString().includes('binding_sha256')),false);assert.equal(h.calls.length,1);
});
test('upstream401 revokes existing session and every pending QC operation before public401 drains',async t=>{
 const h=session(t,{handler:()=>{}}),s=await h.signed(),one=h.start(detail,{...s}),two=h.start(lookup,{...s});await until(()=>h.calls.length===2);h.calls[0].reply({error:{code:'unauthenticated'}},401);const first=await one.promise;expected(first,401,'unauthenticated');assert.ok(first.headers['set-cookie'].some(x=>x.startsWith('cyomics_session=;')));await two.promise;assert.equal(h.calls[1].out.destroyed,true);expected(await h.send('/api/v1/me',{cookie:s.cookie}),401,'unauthenticated');
});
test('12s clock budget covers pending body and upstream; elapsed success cannot escape without timer callback',async t=>{
 const h=session(t,{handler:()=>{}}),s=await h.signed(),pending=h.start(collection,{...s,method:'POST',value:payload});await until(()=>h.calls.length===1);h.advance(12001);h.calls[0].reply(job(),202);const r=await pending.promise;assert.ok(r.status===undefined||r.status===504);assert.equal(pending.res.writes.some(v=>v.toString().includes('binding_sha256')),false);assert.equal(h.calls[0].out.destroyed,true);assert.equal(h.calls.length,1);
});
test('backpressure retains session fence until finish and cannot release further bytes after disconnect',async t=>{
 const h=session(t),s=await h.signed(),pending=h.start(detail,{...s,response:{writeResults:[false],autoFinish:false}});await until(()=>pending.res.writes.length===1);assert.equal(pending.res.writableEnded,undefined);pending.res.destroy();await pending.promise;assert.equal(h.calls.length,1);assert.equal(pending.res.endCalls.length,0);pending.res.emit('drain');assert.equal(pending.res.writes.length,1);
});
test('legacy project401 aborts a pending QC read under the same captured session',async t=>{
 const h=session(t,{handler:()=>{}}),s=await h.signed();h.reply(()=>{});const qc=h.start(detail,{...s});await until(()=>h.calls.length===1);const project=h.start('/api/v1/projects',{...s});await until(()=>h.bridges.length===1);h.bridges[0].deliver({error:{code:'unauthenticated'}},401);await project.promise;await qc.promise;assert.equal(h.calls[0].out.destroyed,true);assert.equal(qc.res.writes.some(v=>v.toString().includes('binding_sha256')),false);
});
test('QC401 aborts a pending legacy project read before any private project data can drain',async t=>{
 const h=session(t,{handler:()=>{}}),s=await h.signed();h.reply(()=>{});const project=h.start('/api/v1/projects',{...s});await until(()=>h.bridges.length===1);const qc=h.start(detail,{...s});await until(()=>h.calls.length===1);h.calls[0].reply({error:{code:'unauthenticated'}},401);await qc.promise;await project.promise;assert.equal(h.bridges[0].out.destroyed,true);assert.equal(project.res.writes.some(v=>v.toString().includes('Private A')),false);
});
test('stalled browser POST body expires before upstream without inventing a submitted job',async t=>{
 const h=session(t),s=await h.signed(),pending=h.start(collection,{...s,method:'POST',sendBody:false});await until(()=>pending.req.listenerCount('data')>0);h.advance(12001);h.expire();await pending.promise;assert.equal(h.calls.length,0);assert.equal(pending.req.listenerCount('data'),0);assert.equal(pending.res.writes.some(v=>v.toString().includes('job_id')),false);
});
