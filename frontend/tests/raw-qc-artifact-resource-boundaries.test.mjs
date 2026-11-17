import test from 'node:test';import assert from 'node:assert/strict';import {EventEmitter} from 'node:events';
import {createArtifactBridge,createArtifactLeasePool,artifactRoute} from '../raw-qc-artifact-bridge.mjs';
import {path,close} from './support/raw-qc-artifact-setup.mjs';

test('explicit acquisition uncertainty returns immediately but retains buffer until every known close',async()=>{
 const pool=createArtifactLeasePool(),lease=pool.acquire(),a=new EventEmitter(),b=new EventEmitter();let released=0;
 a.destroy=b.destroy=()=>{};lease.own(a);lease.own(b);lease.retain(Buffer.alloc(8));lease.releaseWhenClosed(()=>released++);
 lease.uncertain();assert.equal(await lease.finish(),false);assert.equal(released,0);
 close(a);assert.equal(released,0);assert.throws(()=>pool.acquire(),e=>e.code==='raw_qc_unavailable');
 close(b);assert.equal(released,1);assert.throws(()=>pool.acquire(),e=>e.code==='raw_qc_unavailable');
});

test('invalid token refusal is before acquisition and retains ordinary confirmed reuse',async()=>{
 const pool=createArtifactLeasePool(),lease=pool.acquire();let called=0;
 const bridge=createArtifactBridge({requestImpl:()=>{called++;throw Error('must not acquire');}});
 await assert.rejects(bridge({route:artifactRoute(path('raw-matrix.tsv'),'GET'),accessToken:'not a token',signal:new AbortController().signal,lease,checkLive:()=>{},budget:{remaining:()=>9000,deadlineUnixMs:String(Date.now()+9000)}}));
 assert.equal(called,0);assert.equal(await lease.finish(),true);assert.equal(await pool.acquire().finish(),true);
});

test('throwing ownership getter and destroy both sanitize while permanently withholding capacity',async()=>{
 const pool=createArtifactLeasePool(),lease=pool.acquire(),object=new EventEmitter();
 Object.defineProperty(object,'closed',{get(){throw Error('SYNTHETIC_PRIVATE_GETTER');}});
 object.destroy=()=>{throw Error('SYNTHETIC_PRIVATE_DESTROY');};
 assert.throws(()=>lease.own(object),error=>error.code==='raw_qc_unavailable'&&!String(error).includes('PRIVATE'));
 assert.equal(await lease.finish(),false);assert.throws(()=>pool.acquire(),error=>error.code==='raw_qc_unavailable');
});
