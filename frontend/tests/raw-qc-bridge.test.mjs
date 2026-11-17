import test from 'node:test';import assert from 'node:assert/strict';
import {createRawQCBridge,rawQCRoute,ownsRawQCRoute,rawQCInput,rawQCReply,readRawQCBody} from '../raw-qc-bridge.mjs';
import {EventEmitter,job,rid,jid,clone,requestSpy,timerClock,args,collection,lookup,detail,payload,pid,vid,tick} from './support/raw-qc-bff-setup.mjs';
test('exact three canonical routes; owns aliases before broad fallback but never accepts them',()=>{
 assert.equal(rawQCRoute(collection,'POST').kind,'submit');assert.equal(rawQCRoute(lookup,'GET').kind,'lookup');assert.equal(rawQCRoute(detail,'GET').kind,'job');
 for(const [url,method]of [[collection,'GET'],[collection+'&request_id='+rid,'POST'],[detail+'&request_id='+rid,'GET'],[detail+'&owner_id='+pid,'GET'],[detail+'&project_id='+pid,'GET'],[collection.replace('project_id','%70roject_id'),'POST'],[collection+'#x','POST'],[collection.replace('/datasets/','//datasets/'),'POST'],['http://evil.invalid'+detail,'GET'],[detail.replace(jid,'ABCDEFAB-CDEF-4ABC-8DEF-ABCDEFABCDEF'),'GET'],[detail+'/artifacts/raw-matrix.tsv','GET'],[collection.replace('?','/?'),'POST'],[detail,'POST'],[detail,'DELETE'],[collection.replace('?project_id='+pid,'?request_id='+rid+'&project_id='+pid),'GET']])assert.throws(()=>rawQCRoute(url,method),url);
 for(const url of [detail,collection,collection.replace('/datasets/','/%64atasets/'),collection.replace('/raw-qc-jobs','/raw-qc-jobs/../sample-designs'),'/api/v1/raw-qc-jobs/'+jid+'/artifacts/x'])assert.equal(ownsRawQCRoute(url),true);
 assert.equal(ownsRawQCRoute('/api/v1/datasets/'+vid+'/sample-designs'),false);
});
test('bounded exact POST input rejects duplicate/escaped duplicate/schemafloat/owner/path/exec/matrix/unknown params',()=>{
 assert.equal(rawQCInput(Buffer.from(JSON.stringify(payload))).request_id,rid);
 for(const wire of ['{"schemaVersion":1,"request_id":"'+rid+'","quant_family":"intensity","params":{},"owner":"x"}',JSON.stringify({...payload,params:{matrix:[]}}),JSON.stringify({...payload,execution:{}}),JSON.stringify({...payload,path:'/private'}),JSON.stringify({...payload,matrix:[]}),JSON.stringify(payload).replace('"schemaVersion":1','"schemaVersion":1.0'),JSON.stringify(payload).replace('"schemaVersion":1','"schemaVersion":1e0'),JSON.stringify(payload).replace('"params":{}','"params":{},"par\\u0061ms":{}')])assert.throws(()=>rawQCInput(Buffer.from(wire)));
 for(const bytes of [Buffer.alloc(1025),Buffer.from([255]),Buffer.concat([Buffer.from([239,187,191]),Buffer.from(JSON.stringify(payload))])])assert.throws(()=>rawQCInput(bytes));
});
test('request body reader enforces GET0/POST1024, declared stream content, abort and early close',async()=>{
 for(const method of ['GET','POST']){const req=new EventEmitter();req.pause=()=>{};const c=new AbortController(),p=readRawQCBody(req,c.signal,method);req.emit('data',Buffer.alloc(method==='POST'?1025:1));await assert.rejects(p);assert.equal(req.listenerCount('data'),0);req.emit('close');}
 for(const event of ['abort','close','error']){const req=new EventEmitter();req.pause=()=>{};const c=new AbortController(),p=readRawQCBody(req,c.signal,'POST');if(event==='abort')c.abort();else req.emit(event,event==='error'?new Error('synthetic'):undefined);await assert.rejects(p);req.emit('close');}
});
test('disabled bridge calls no native implementation; invalid route/body/token/budgets never reach upstream',async()=>{
 await assert.rejects(createRawQCBridge()(args()),e=>e.status===503);
 const spy=requestSpy(),bridge=createRawQCBridge(spy);
 for(const input of [args('GET',{body:Buffer.from('private')}),args('GET',{accessToken:'unsafe\nheader'}),args('GET',{budget:{remaining:()=>13000,deadlineUnixMs:'1795000000000'}}),args('GET',{budget:{remaining:()=>NaN,deadlineUnixMs:'1795000000000'}}),args('GET',{budget:{remaining:()=>1000,deadlineUnixMs:'bad'}}),args('POST',{body:Buffer.from('{"owner":1}')})])await assert.rejects(bridge(input));assert.equal(spy.calls.length,0);
});
test('three operations pin upstream and headers, normalize only safe validated body and never accept URL override',async()=>{
 const spy=requestSpy(),bridge=createRawQCBridge(spy);
 for(const input of [args(),args('POST'),args('GET',{route:rawQCRoute(lookup,'GET')})])assert.ok([200,202].includes((await bridge({...input,endpoint:'https://evil.invalid',cookie:'do-not-forward',owner_id:pid})).status));
 for(const c of spy.calls){assert.equal(c.options.hostname,'127.0.0.1');assert.equal(c.options.port,57430);assert.equal(c.options.agent,false);assert.equal(c.options.headers.Origin,'http://127.0.0.1:4206');assert.equal(c.options.headers.Authorization,'Bearer SYNTHETIC_ACCESS_ONLY');assert.equal(c.options.headers.Cookie,undefined);assert.equal(c.options.headers['X-Cyomics-Account-Context'],undefined);assert.equal(c.options.headers['X-Owner-Id'],undefined);assert.ok(Number(c.options.headers['X-Cyomics-Remaining-Ms'])<=12000);}
 assert.deepEqual(JSON.parse(spy.calls[1].body),payload);assert.equal(spy.calls[0].body,undefined);
});
test('POST202 only nonterminal, terminal200; GET200 only; no envelope/list/private unknown fields',async()=>{
 for(const status of ['queued','running','succeeded','failed']){const j=job({status}),r=rawQCRoute(collection,'POST');assert.equal(rawQCReply(r,['queued','running'].includes(status)?202:200,j,payload),j);}
 for(const [route,status,value]of [[rawQCRoute(detail,'GET'),202,job()],[rawQCRoute(collection,'POST'),200,job()],[rawQCRoute(collection,'POST'),202,job({status:'failed'})],[rawQCRoute(detail,'GET'),200,{job:job()}],[rawQCRoute(detail,'GET'),200,{...job(),owner_id:pid}],[rawQCRoute(detail,'GET'),200,[]]])assert.throws(()=>rawQCReply(route,status,value,payload));
 const reordered=job({status:'succeeded'});reordered.result.execution=Object.fromEntries(Object.entries(reordered.execution).reverse());assert.equal(rawQCReply(rawQCRoute(detail,'GET'),200,reordered),reordered);
 const differentProfile=job();differentProfile.execution.limits_profile='raw_qc_limits_v99';differentProfile.execution.limits_sha256='f'.repeat(64);assert.equal(rawQCReply(rawQCRoute(detail,'GET'),200,differentProfile),differentProfile,'BFF does not choose scientific runtime profile');
});
for(const [name,status,headers]of [['redirect302',302,['Content-Type','application/json']],['location',200,['Content-Type','application/json','Location','https://evil.invalid']],['cookie',200,['Content-Type','application/json','Set-Cookie','private=secret']],['refresh',200,['Content-Type','application/json','Refresh','0;url=evil']],['gzip',200,['Content-Type','application/json','Content-Encoding','gzip']],['duplicate-media',200,['Content-Type','application/json','Content-Type','application/json']],['oversize-length',200,['Content-Type','application/json','Content-Length','32769']],['ambiguous-framing',200,['Content-Type','application/json','Content-Length','2','Transfer-Encoding','chunked']]])test('GET rejects unsafe upstream '+name,async()=>{const spy=requestSpy(c=>c.reply(job(),status,headers));await assert.rejects(createRawQCBridge(spy)(args()));assert.equal(spy.calls.length,1);assert.equal(spy.calls[0].out.destroyed,true);});
test('success/error byte limits and corrupt/duplicate/deep/length mismatch responses close without passthrough',async()=>{
 const cases=[{v:Buffer.alloc(32769),s:200},{v:Buffer.alloc(4097),s:503},{v:Buffer.from([255]),s:200},{v:Buffer.from('{"schemaVersion":1,"schemaVersion":1}'),s:200},{v:Buffer.from('['.repeat(14)+'0'+']'.repeat(14)),s:200},{v:job(),s:200,h:['Content-Type','application/json','Content-Length','2']},{v:{error:{code:'private path /secrets'}},s:503}];
 for(const p of cases){const spy=requestSpy(c=>c.reply(p.v,p.s,p.h));await assert.rejects(createRawQCBridge(spy)(args()));assert.equal(spy.calls.length,1);}
});
test('valid explicit rejection and submission-unknown are forwarded once; invalid POST wire becomes unknown',async()=>{
 for(const [status,code]of [[409,'raw_qc_request_conflict'],[422,'raw_qc_family_unavailable'],[429,'raw_qc_capacity'],[503,'raw_qc_unavailable'],[504,'raw_qc_submission_unknown']]){const spy=requestSpy(c=>c.reply({error:{code}},status)),r=await createRawQCBridge(spy)(args('POST'));assert.equal(r.status,status);assert.equal(r.value.error.code,code);assert.equal(spy.calls.length,1);}
 const spy=requestSpy(c=>c.reply(Buffer.from('bad'),202));await assert.rejects(createRawQCBridge(spy)(args('POST')),e=>e.code==='raw_qc_submission_unknown'&&e.status===504);assert.equal(spy.calls.length,1);
});
test('partial close, transport error and abort after possible POST are unknown; no retry',async()=>{
 for(const kind of ['partial','error','aborted']){const spy=requestSpy(c=>{const r=c.response(202);if(kind==='partial'){r.emit('data',Buffer.from('{'));r.emit('close');}else r.emit(kind,kind==='error'?new Error('synthetic'):undefined);});await assert.rejects(createRawQCBridge(spy)(args('POST')),e=>e.code==='raw_qc_submission_unknown');assert.equal(spy.calls.length,1);}
});
test('finite local timer and monotonic guard reject late end even if timer callback is delayed',async()=>{
 for(const mode of ['timer','elapsed']){const clock=timerClock(),spy=requestSpy(()=>{}),bridge=createRawQCBridge({...spy,...clock}),p=bridge(args('POST'));await tick();if(mode==='timer')clock.fire();else{clock.advance(11001);spy.calls[0].reply(job(),202);}await assert.rejects(p,e=>e.code==='raw_qc_submission_unknown');assert.equal(spy.calls[0].out.destroyed,true);assert.equal(clock.timers.size,0);spy.calls[0].reply(job(),202);assert.equal(spy.calls[0].input.destroyed,true);}
});
test('onSent cancellation cannot end a request; factory failure before send is unavailable',async()=>{
 const spy=requestSpy(),controller=new AbortController();await assert.rejects(createRawQCBridge(spy)(args('POST',{signal:controller.signal,onSent:()=>controller.abort()})));assert.equal(spy.calls[0].ended,undefined);assert.equal(spy.calls[0].out.destroyed,true);
 await assert.rejects(createRawQCBridge({requestImpl:()=>{throw Error('synthetic');}})(args('POST')),e=>e.code==='raw_qc_unavailable'&&e.status===503);
});
