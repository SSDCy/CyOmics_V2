// One new cancellation regression only. Thin trusted-job boundary; no Auth/R/API fixture.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createDifferentialResultOwner} from '../differential-result-owner.js';
import {createQCArtifactScope} from '../raw-qc-client.js';
import {canonicalJSON} from '../sample-design-contract.mjs';
const id=n=>String(n).padStart(8,'0')+'-1111-4111-8111-111111111111';
test('cancelled JSON job digest retains shared lease and clear completion until the real digest settles',async t=>{
 let resolve,started=false,sendCalls=0,seenBytes;const held=new Promise(r=>{resolve=r;}),scope=createQCArtifactScope();
 const job={request_id:id(1),job_id:id(2),project_id:id(3),source:{file_id:id(4),source:{source_sha256:'a'.repeat(64)}},synthetic_note:'α / 原始 "ID"\n'};
 const jobs={check:async()=>true,view:()=>({history:[{request_id:id(1),task:job}],code:null})};
 const owner=createDifferentialResultOwner({projectId:id(3),fileId:id(4),jobs,transport:{isCurrent:()=>true,artifactScope:scope,send:async()=>{sendCalls++;throw Error('must not read metadata while digest pending');}},source:()=>({file:{project_id:id(3),file_id:id(4),sha256:'a'.repeat(64)}}),isCurrent:()=>true,contextSignal:new AbortController().signal,cryptoImpl:{subtle:{digest(algorithm,bytes){assert.equal(algorithm,'SHA-256');seenBytes=new Uint8Array(bytes);started=true;return held;}}}});
 t.after(async()=>{resolve(new Uint8Array(32).buffer);owner.dispose();for(let i=0;i<20;i++)await Promise.resolve();});
 const pending=owner.select(id(1));for(let i=0;i<50&&!started;i++)await Promise.resolve();assert.equal(started,true);assert.equal(scope.busy(),true);
 assert.deepEqual(seenBytes,new TextEncoder().encode(canonicalJSON(job)),'same original canonical JSON UTF8 bytes');
 let clearDone=false;const cleared=owner.clear().then(()=>{clearDone=true;});assert.equal(await pending,false);for(let i=0;i<10;i++)await Promise.resolve();
 assert.equal(sendCalls,0);assert.equal(clearDone,false,'clear completion waits for physical digest settlement');assert.equal(scope.busy(),true,'no early lease release during unabortable JSON hash');assert.equal(scope.acquire(()=>{}),null,'second shared artifact flight remains blocked');
 resolve(new Uint8Array(32).buffer);await cleared;assert.equal(clearDone,true);assert.equal(scope.busy(),false);assert.equal(owner.view().meta,null);assert.equal(owner.view().phase,'idle');assert.equal(sendCalls,0,'late completion publishes no result or request');const next=scope.acquire(()=>{});assert.ok(next);next.release();
});
