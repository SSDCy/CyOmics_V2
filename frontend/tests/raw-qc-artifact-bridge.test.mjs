import test from 'node:test';import assert from 'node:assert/strict';import {EventEmitter} from 'node:events';
import {createArtifactBridge,createArtifactLeasePool,artifactRoute,artifactError,ARTIFACT_CAP} from '../raw-qc-artifact-bridge.mjs';
import {spy,path,headers,bytes,close,sha} from './support/raw-qc-artifact-setup.mjs';
const args=(lease,signal=new AbortController().signal)=>({route:artifactRoute(path('raw-matrix.tsv'),'GET'),accessToken:'SYNTHETIC_ACCESS',signal,lease,checkLive:()=>{},budget:{remaining:()=>9000,deadlineUnixMs:String(Date.now()+9000)}});
test('missing implementation never falls back to native HTTP',async()=>{
 const pool=createArtifactLeasePool(),lease=pool.acquire(),bridge=createArtifactBridge();await assert.rejects(bridge(args(lease)));await lease.finish();
});
test('exact64MiB is verified byte-for-byte; cap+1 is rejected from headers before body allocation',async()=>{
 const body=Buffer.alloc(ARTIFACT_CAP,120),transport=spy(c=>c.reply(body)),pool=createArtifactLeasePool(),lease=pool.acquire();
 const result=await createArtifactBridge({requestImpl:transport.requestImpl})(args(lease));assert.equal(result.body.length,ARTIFACT_CAP);assert.equal(sha(result.body),sha(body));assert.deepEqual(result.body,body);await lease.finish();
 const bad=spy(c=>c.reply(bytes,200,headers().map((v,i)=>i===3?String(ARTIFACT_CAP+1):v))),other=pool.acquire();
 await assert.rejects(createArtifactBridge({requestImpl:bad.requestImpl})(args(other)),error=>error.code==='raw_qc_artifact_integrity_failed');await other.finish();
});
test('unknown closes retain buffers and capacity; destroyed alone is never confirmation; late close cannot automatically unquarantine',async()=>{
 const pool=createArtifactLeasePool(),lease=pool.acquire(),controller=new AbortController();let released=0;
 const resource=new EventEmitter();resource.destroy=()=>{resource.destroyed=true;};lease.own(resource);lease.retain(Buffer.alloc(16));lease.releaseWhenClosed(()=>released++);
 lease.abort();controller.abort();assert.equal(await lease.finish(controller.signal),false);assert.equal(released,0);assert.throws(()=>pool.acquire(),error=>error.code==='raw_qc_unavailable');
 close(resource);assert.equal(released,1);assert.throws(()=>pool.acquire(),error=>error.code==='raw_qc_unavailable');
});
test('late request handle and response after cancellation are owned/destroyed and cannot admit another transfer',async()=>{
 const pool=createArtifactLeasePool(),lease=pool.acquire(),controller=new AbortController();let callback;const out=new EventEmitter();out.destroy=()=>{out.destroyed=true;};out.end=()=>{};
 controller.signal.addEventListener('abort',()=>lease.abort());
 const bridge=createArtifactBridge({requestImpl:(options,cb)=>{callback=cb;controller.abort();return out;}});
 await assert.rejects(bridge(args(lease,controller.signal)));assert.equal(out.destroyed,true);assert.equal(await lease.finish(controller.signal),false);
 const incoming=new EventEmitter();incoming.destroy=()=>{incoming.destroyed=true;};callback(incoming);assert.equal(incoming.destroyed,true);assert.throws(()=>pool.acquire());close(out);close(incoming);assert.throws(()=>pool.acquire());
});
test('error whitelist never accepts missing codes or local409 from upstream',()=>{
 for(const [status,value]of [[400,{error:{code:undefined}}],[418,{error:{code:undefined}}],[409,{error:{code:'project_context_changed'}}],[200,{job:'JSON'}]])assert.throws(()=>artifactError(status,value,{upstream:true}));
 assert.deepEqual(artifactError(409,{error:{code:'project_context_changed'}}),{error:{code:'project_context_changed'}});
});
test('upstream bytes longer than canonical CL and an incomplete stream fail before any result is returned',async()=>{
 for(const complete of [true,false]){
  const transport=spy(c=>{const input=c.response(200,headers().map((v,i)=>i===3?'1':v));input.emit('data',bytes);input.complete=complete;input.emit('end');close(input);close(c.out);});
  const lease=createArtifactLeasePool().acquire();await assert.rejects(createArtifactBridge({requestImpl:transport.requestImpl})(args(lease)));await lease.finish();
 }
});
