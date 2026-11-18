import test from 'node:test';import assert from 'node:assert/strict';
import {createPreprocessBridge,preprocessRoute,ownsPreprocessRoute,preprocessInput,preprocessReply,readPreprocessBody} from '../preprocess-bridge.mjs';
import {EventEmitter,job,requestSpy,args,timerClock,payload,collection,lookup,detail,pid,vid,jid,rid,tick} from './support/preprocess-bff-setup.mjs';

test('only three exact canonical routes; near paths are owned and rejected',()=>{
 assert.equal(preprocessRoute(collection,'POST').kind,'submit');assert.equal(preprocessRoute(lookup,'GET').kind,'lookup');assert.equal(preprocessRoute(detail,'GET').kind,'job');
 for(const [url,method] of [[collection+'&request_id=bad','GET'],[lookup,'POST'],[detail,'POST'],[detail+'&request_id='+rid,'GET'],[lookup+'&project_id='+pid,'GET'],[lookup+'&owner_id='+pid,'GET'],[lookup.replace('project_id','%70roject_id'),'GET'],[collection.replace('/datasets/','//datasets/'),'POST'],[collection.replace('?','/?'),'POST'],['http://evil.invalid'+detail,'GET'],[detail+'#x','GET'],[detail+'/artifacts/x','GET']])assert.throws(()=>preprocessRoute(url,method));
 for(const url of [detail,collection,collection.replace('/datasets/','/%64atasets/'),collection.replace('/preprocessing-jobs','/preprocessing-jobs/../sample-designs')])assert.equal(ownsPreprocessRoute(url),true);
 assert.equal(ownsPreprocessRoute('/api/v1/datasets/'+vid+'/sample-designs'),false);
});
test('3keys/12params/3strict flags and int0 lexical gate preserve backend contract',()=>{
 const text=JSON.stringify(payload),raw=Buffer.from(text);assert.equal(preprocessInput(raw).request_id,rid);
 assert.equal(preprocessInput(Buffer.concat([raw,Buffer.alloc(4096-raw.length,32)])).quant_family,'Intensity');
 for(const wire of [text.replace('"pseudocount":0','"pseudocount":0.0'),text.replace('"pseudocount":0','"pseudocount":0e0'),text.replace('"pseudocount":0','"pseudocount":-0'),text.replace('"contaminant":true','"contaminant":1'),text.replace('"parameters":','"parameters":{},"parameters":'),JSON.stringify({...payload,owner_id:pid}),JSON.stringify({...payload,source:{}}),JSON.stringify({...payload,quant_family:'intensity'}),text.replace('"normalization":"none"','"normalization":"median"')])assert.throws(()=>preprocessInput(Buffer.from(wire)));
 for(const raw of [Buffer.alloc(4097),Buffer.from([255]),Buffer.concat([Buffer.from([239,187,191]),Buffer.from(text)])])assert.throws(()=>preprocessInput(raw));
});
test('stream reader bounds GET0/POST4096 and abort/early close without retaining data listeners',async()=>{
 for(const method of ['GET','POST']){const req=new EventEmitter();req.pause=()=>{};const ctrl=new AbortController(),p=readPreprocessBody(req,ctrl.signal,method);req.emit('data',Buffer.alloc(method==='POST'?4097:1));await assert.rejects(p);assert.equal(req.listenerCount('data'),0);req.emit('close');}
 for(const event of ['abort','close','error']){const req=new EventEmitter();req.pause=()=>{};const ctrl=new AbortController(),p=readPreprocessBody(req,ctrl.signal,'POST');if(event==='abort')ctrl.abort();else req.emit(event,new Error('SYNTHETIC'));await assert.rejects(p);req.emit('close');}
});
test('disabled or invalid bridge never calls upstream; no browser-controlled endpoint or token',async()=>{
 await assert.rejects(createPreprocessBridge()(args()),e=>e.status===503);
 const spy=requestSpy(),bridge=createPreprocessBridge(spy);
 for(const input of [args('GET',{body:Buffer.from('x')}),args('GET',{accessToken:'unsafe\nheader'}),args('GET',{budget:{remaining:()=>12001,deadlineUnixMs:'1795000000000'}}),args('GET',{budget:{remaining:()=>NaN,deadlineUnixMs:'bad'}}),args('POST',{body:Buffer.from('{}')})])await assert.rejects(bridge(input));
 assert.equal(spy.calls.length,0);
});
test('fixed upstream and whitelist headers for create/replay/read; opaque pins stay opaque',async()=>{
 const spy=requestSpy(),bridge=createPreprocessBridge(spy);
 for(const input of [args('POST'),args('GET',{route:preprocessRoute(lookup,'GET')}),args()])assert.ok([200,201].includes((await bridge({...input,endpoint:'https://evil.invalid',cookie:'PRIVATE',owner_id:pid})).status));
 for(const c of spy.calls){assert.equal(c.options.hostname,'127.0.0.1');assert.equal(c.options.port,57430);assert.equal(c.options.agent,false);assert.equal(c.options.headers.Authorization,'Bearer SYNTHETIC_ACCESS_ONLY');assert.equal(c.options.headers.Cookie,undefined);assert.equal(c.options.headers['X-Cyomics-Account-Context'],undefined);assert.ok(Number(c.options.headers['X-Cyomics-Remaining-Ms'])<=12000);}
 assert.deepEqual(JSON.parse(spy.calls[0].body),payload);assert.equal(spy.calls[1].body,undefined);
 const replay=requestSpy(c=>c.reply(job(),200));assert.equal((await createPreprocessBridge(replay)(args('POST'))).status,200);
 const opaque=job();opaque.proposal_sha256='0'.repeat(64);opaque.binding_sha256='1'.repeat(64);assert.equal(preprocessReply(args().route,200,opaque),opaque);assert.equal(opaque.execution,undefined);
});
test('wrong source/request/parameter/digest/status/DTO cannot pass; GET201 and POST202 rejected',()=>{
 const mutations=[v=>v.source.project_id=jid,v=>v.source.dataset_version_id=jid,v=>v.source.source_sha256='bad',v=>v.source.owner_id=pid,v=>v.request_id=pid,v=>v.parameters.filter_flags.reverse=false,v=>v.parameters_sha256='0'.repeat(64),v=>v.request_sha256='0'.repeat(64),v=>v.worker_started=0,v=>v.status='succeeded',v=>v.result={path:'/PRIVATE'},v=>v.execution={path:'/PRIVATE'}];
 for(const mutate of mutations){const value=job();mutate(value);assert.throws(()=>preprocessReply(args().route,200,value));}
 assert.throws(()=>preprocessReply(args().route,201,job()));assert.throws(()=>preprocessReply(args('POST').route,202,job(),payload));
 const v=job();v.job_id=pid;assert.throws(()=>preprocessReply(preprocessRoute(detail,'GET'),200,v));
});
test('unsafe upstream redirects/compression/duplicate or abnormal headers/framing close once',async()=>{
 const cases=[[302,['Content-Type','application/json']],[200,['Content-Type','application/json','Location','https://evil.invalid']],[200,['Content-Type','application/json','Set-Cookie','secret=x']],[200,['Content-Type','application/json','Content-Encoding','gzip']],[200,['Content-Type','application/json','X-Test','a','x-test','b']],[200,['Content-Type','application/json','Bad Header','x']],[200,['Content-Type','application/json','X-Test','line\r\nnext']],[200,['Content-Type','application/json','Content-Length','1','Transfer-Encoding','chunked']]];
 for(const [status,headers]of cases){const spy=requestSpy(c=>c.reply(job(),status,headers));await assert.rejects(createPreprocessBridge(spy)(args()));assert.equal(spy.calls.length,1);assert.equal(spy.calls[0].out.destroyed,true);}
});
test('size/protocol/duplicate/float failures sanitize; sent POST becomes unknown with no retry',async()=>{
 for(const [wire,status,headers] of [[Buffer.alloc(32769),200],[Buffer.alloc(4097),503],[Buffer.from([255]),200],[Buffer.from('{"error":{},"error":{}}'),503],[Buffer.from(JSON.stringify(job()).replace('"schemaVersion":2','"schemaVersion":2.0')),200],[job(),200,['Content-Type','application/json','Content-Length','2']],[{error:{code:'PRIVATE /path'}},503]]){
  const spy=requestSpy(c=>c.reply(wire,status,headers));await assert.rejects(createPreprocessBridge(spy)(args('POST')),e=>e.status===504&&e.code==='preprocessing_submission_unknown');assert.equal(spy.calls.length,1);
 }
 for(const [status,code]of [[409,'preprocessing_request_conflict'],[422,'preprocessing_flags_unavailable'],[503,'preprocessing_unavailable'],[504,'preprocessing_submission_unknown']]){const spy=requestSpy(c=>c.reply({error:{code}},status)),r=await createPreprocessBridge(spy)(args('POST'));assert.equal(r.status,status);assert.equal(r.value.error.code,code);assert.equal(spy.calls.length,1);}
});
test('timer/elapsed/abort fence after possible POST is unknown and never retries or emits late success',async()=>{
 for(const mode of ['timer','elapsed','abort']){const clock=timerClock(),spy=requestSpy(()=>{}),ctrl=new AbortController(),bridge=createPreprocessBridge({...spy,...clock}),pending=bridge(args('POST',{signal:ctrl.signal}));await tick();if(mode==='timer')clock.fire();else if(mode==='elapsed'){clock.advance(11001);spy.calls[0].reply(job(),201);}else ctrl.abort();await assert.rejects(pending,e=>e.code==='preprocessing_submission_unknown');assert.equal(spy.calls.length,1);assert.equal(spy.calls[0].out.destroyed,true);assert.equal(clock.timers.size,0);spy.calls[0].reply(job(),201);assert.equal(spy.calls[0].input.destroyed,true);}
});
