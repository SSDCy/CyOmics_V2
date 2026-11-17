import test from 'node:test';import assert from 'node:assert/strict';import {EventEmitter} from 'node:events';
import {session,path,bytes,headers,close,until,tick} from './support/raw-qc-artifact-setup.mjs';
import {createArtifactBridge,createArtifactLeasePool,artifactRoute} from '../raw-qc-artifact-bridge.mjs';
const args=(lease,controller=new AbortController())=>({route:artifactRoute(path('raw-matrix.tsv'),'GET'),accessToken:'SYNTHETIC_ACCESS',signal:controller.signal,lease,checkLive:()=>{},budget:{remaining:()=>9000,deadlineUnixMs:String(Date.now()+9000)}});

test('unexpected request factory throw after unreturned acquisition cannot prove capacity reusable',async()=>{
 const pool=createArtifactLeasePool(),lease=pool.acquire();let created,destroyed=false;
 const requestImpl=()=>{created=new EventEmitter();created.destroy=()=>{destroyed=true;close(created);};throw Error('SYNTHETIC_PRIVATE_FACTORY_FAILURE');};
 try{
  await assert.rejects(createArtifactBridge({requestImpl})(args(lease)),error=>error.code==='raw_qc_unavailable'&&!String(error).includes('PRIVATE'));
  assert.equal(destroyed,false);assert.ok(created);
  assert.equal(await lease.finish(),false,'no returned handle or teardown receipt proves unreturned acquisition closed');
  assert.throws(()=>pool.acquire(),error=>error.code==='raw_qc_unavailable');
 }finally{created?.destroy();}
});

test('ownership metadata failure before tracking must not silently return capacity',async()=>{
 const pool=createArtifactLeasePool(),lease=pool.acquire(),resource=new EventEmitter();resource.destroy=()=>{};
 Object.defineProperty(resource,'closed',{get(){throw Error('SYNTHETIC_PRIVATE_CLOSED_GETTER');}});
 assert.throws(()=>lease.own(resource));
 assert.equal(await lease.finish(),false,'ownership initialization exception is not closure proof');
 assert.throws(()=>pool.acquire(),error=>error.code==='raw_qc_unavailable');
});

test('normalized-only transfer encoding, redirects or merged SHA cannot bypass raw header checks',async t=>{
 for(const change of [input=>{input.headers['transfer-encoding']='chunked';},input=>{input.headers.refresh='0';},input=>{input.headers['x-cyomics-file-sha256']+=' , duplicate';}]){
  const h=session(t,{transport:{beforeCallback:change}}),s=await h.signed(),result=await h.send(path('raw-matrix.tsv'),s);
  assert.equal(result.status,503);assert.equal(result.body.error.code,'raw_qc_artifact_integrity_failed');assert.notEqual(result.headers['content-type'],'application/octet-stream');
 }
});

test('full correct bytes plus close without EOF never sends200 and a confirmed failure can be reused',async t=>{
 let fail=true;const h=session(t,{handler:call=>{if(!fail){call.reply();return;}const input=call.response();input.emit('data',bytes);input.complete=true;close(input);close(call.out);}}),s=await h.signed();
 const result=await h.send(path('raw-matrix.tsv'),s);assert.equal(result.status,503);assert.notEqual(result.headers['content-type'],'application/octet-stream');
 fail=false;assert.equal((await h.send(path('raw-matrix.tsv'),s)).status,200);assert.equal(h.calls.length,2);
});

test('downstream private error during backpressure latches missing close even after later close',async t=>{
 const data=Buffer.alloc(131072,123),h=session(t,{handler:c=>c.reply(data)}),s=await h.signed();
 const pending=h.start(path('raw-matrix.tsv'),{...s,response:{writeResults:[false],autoFinish:false,ignoreResponseClose:true}});
 await until(()=>pending.res.writes.length===1);pending.res.emit('error',Error('SYNTHETIC_PRIVATE_SEND'));
 const total=[...h.timers.values()].find(x=>x.ms===45000);assert.ok(total);total.fn();await pending.promise;
 assert.equal((await h.send(path('summary.tsv'),s)).status,503);assert.equal(h.calls.length,1);
 pending.res.emit('drain');await tick();assert.equal(pending.res.writes.length,1);close(pending.res);
 assert.equal((await h.send(path('summary.tsv'),s)).status,503);assert.equal(h.calls.length,1);
});

test('backward monotonic observation prevents stale private bytes without depending on timer delivery',async t=>{
 const h=session(t,{handler:()=>{}}),s=await h.signed(),pending=h.start(path('raw-matrix.tsv'),s);
 await until(()=>h.calls.length===1);h.advance(-1,'mono');h.calls[0].reply();await pending.promise;
 assert.equal(pending.res.writes.some(b=>b.equals(bytes)),false);assert.equal(h.calls[0].out.destroyed,true);
});

test('one millisecond session hint cannot publish a later reply even before45s callback',async t=>{
 const h=session(t,{handler:()=>{}}),s=await h.signed();h.advance(899999);const pending=h.start(path('raw-matrix.tsv'),s);
 await until(()=>h.calls.length===1);assert.equal(h.calls[0].options.headers['X-Cyomics-Remaining-Ms'],'1');
 h.advance(2,'wall');h.calls[0].reply();await pending.promise;assert.equal(pending.res.writes.some(b=>b.equals(bytes)),false);
});

test('two-resource unknown finish retains buffer until both close and never clears quarantine',async()=>{
 const pool=createArtifactLeasePool(),lease=pool.acquire(),controller=new AbortController();let release=0;
 const a=new EventEmitter(),b=new EventEmitter();a.destroy=b.destroy=()=>{};lease.own(a);lease.own(b);lease.retain(Buffer.alloc(4));lease.releaseWhenClosed(()=>release++);
 lease.abort();controller.abort();assert.equal(await lease.finish(controller.signal),false);close(a);assert.equal(release,0);close(b);assert.equal(release,1);
 assert.throws(()=>pool.acquire(),error=>error.code==='raw_qc_unavailable');
});

test('synchronous response callback before request send rejects without leaving known handles live',async()=>{
 const pool=createArtifactLeasePool(),lease=pool.acquire();let out,input;
 const requestImpl=(options,callback)=>{input=new EventEmitter();input.destroy=()=>close(input);callback(input);out=new EventEmitter();out.destroy=()=>close(out);out.end=()=>{};return out;};
 await assert.rejects(createArtifactBridge({requestImpl})(args(lease)));
 assert.equal(input.closed,true);assert.equal(out.closed,true);assert.equal(await lease.finish(),true);
});

test('encoded artifact aliases are owned/refused before any JSON or binary upstream',async t=>{
 const h=session(t,{useServer:true}),s=await h.signed(),before=h.auth.length;
 for(const url of [path('raw-matrix.tsv').replace('/api/','//api/'),path('raw-matrix.tsv').replace('/artifacts/','/%2561rtifacts/'),path('raw-matrix.tsv')+'#fragment']){
  const result=await h.send(url,s);assert.ok([400,404].includes(result.status));
 }
 assert.equal(h.calls.length,0);assert.equal(h.bridges.length,0);assert.equal(h.auth.length,before);
});
