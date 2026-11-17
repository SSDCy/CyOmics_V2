import test from 'node:test';import assert from 'node:assert/strict';import {EventEmitter} from 'node:events';
import {createSampleDesignBridge} from '../sample-design-bridge.mjs';import {sampleDesignRoute} from '../sample-design-contract.mjs';
const path='/api/v1/datasets/22222222-2222-4222-8222-222222222222/sample-designs?project_id=11111111-1111-4111-8111-111111111111&quant_family=intensity';
const args=(method='GET')=>({route:sampleDesignRoute(path,method),accessToken:'SYNTHETIC_TOKEN',signal:new AbortController().signal,budget:{remaining:()=>1000,deadlineUnixMs:'1794873600000'}});
function spy({status=404,value={error:{code:'dataset_not_found'}},rawHeaders=['Content-Type','application/json'],hold=false}={}){const calls=[];return {calls,requestImpl:(options,callback)=>{const out=new EventEmitter();out.destroy=()=>{out.destroyed=true;};const call={options,out};calls.push(call);out.end=()=>queueMicrotask(()=>{const response=new EventEmitter();Object.assign(response,{statusCode:status,rawHeaders,headers:{},destroy(){this.destroyed=true;}});call.response=response;callback(response);if(!response.destroyed&&!hold){response.emit('data',Buffer.from(JSON.stringify(value)));response.complete=true;response.emit('end');}});return out;}};}
test('default missing-source bridge and enabled source-free POST remain fail-closed before request',async()=>{
 for(const enabled of [false,true])for(const method of ['GET','POST']){if(enabled&&method==='GET')continue;const s=spy();await assert.rejects(createSampleDesignBridge({...s,allowSourceFreeGetErrors:enabled})(args(method)),e=>e.code==='sample_design_persisted_invalid');assert.equal(s.calls.length,0);}
});
test('source-free GET passes only strict typed error envelopes',async()=>{
 for(const [status,code]of [[401,'unauthenticated'],[403,'auth_origin_rejected'],[404,'dataset_not_found']]){const s=spy({status,value:{error:{code}}});const r=await createSampleDesignBridge({...s,allowSourceFreeGetErrors:true})(args());assert.equal(r.status,status);assert.equal(r.value.error.code,code);assert.equal(s.calls.length,1);}
});
test('source-free errors reject redirects,cookies,duplicate headers,length mismatch and bad typed codes',async()=>{
 const cases=[{rawHeaders:['Content-Type','application/json','Location','http://evil.invalid']},{rawHeaders:['Content-Type','application/json','Set-Cookie','x=y']},{rawHeaders:['Content-Type','application/json','Content-Type','application/json']},{rawHeaders:['Content-Type','application/json','Content-Length','1']},{status:404,value:{error:{code:'unauthenticated'}}}];
 for(const c of cases){const s=spy(c);await assert.rejects(createSampleDesignBridge({...s,allowSourceFreeGetErrors:true})(args()),e=>e.status===503);assert.equal(s.calls[0].response.destroyed,true);}
});
test('source-free success is destroyed at headers without consuming any body',async()=>{
 const s=spy({status:200,value:{secret:'SYNTHETIC_PRIVATE_RESULT'}});await assert.rejects(createSampleDesignBridge({...s,allowSourceFreeGetErrors:true})(args()),e=>e.code==='sample_design_unavailable');assert.equal(s.calls[0].response.listenerCount('data'),0);assert.equal(s.calls[0].response.destroyed,true);
});
test('abort after source-free send uses normal timeout fencing and no retry',async()=>{
 const controller=new AbortController(),s=spy({hold:true}),a=args();a.signal=controller.signal;const p=createSampleDesignBridge({...s,allowSourceFreeGetErrors:true})(a);await new Promise(r=>queueMicrotask(r));controller.abort();await assert.rejects(p,e=>e.code==='sample_design_timeout');assert.equal(s.calls.length,1);assert.equal(s.calls[0].out.destroyed,true);
});
