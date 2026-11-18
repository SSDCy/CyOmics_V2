// Independent memory-only probes. No parser, upload, authenticated subject or UI.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createFastaFileLeaseController as create} from '../fasta-file-lease.js';
const A='owner-A/project-A/nonce-A/revision-001',B='owner-B/project-B/nonce-B/revision-002';
const file=name=>new File(['>SYNTHETIC\nAC\n'],name);
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('disabled controller never inspects the injected File prototype or callback',()=>{
  let context=0,prototype=0;
  const FileImpl=new Proxy(File,{get(target,key,receiver){if(key==='prototype'){prototype++;throw Error('forbidden');}return Reflect.get(target,key,receiver);}});
  const c=create({getContextKey(){context++;throw Error('forbidden');},FileImpl});
  assert.equal(c.select(file('synthetic.fa'),A),false);assert.equal(c.acquire(A),null);c.status();c.invalidate();c.clear();c.dispose();
  assert.deepEqual({context,prototype},{context:0,prototype:0});
});

test('native brand-check synchronous reentry preserves a newer selected generation',()=>{
  let key=A,controller,armed=true,inner;
  const fresh=file('new.fasta');
  const FileImpl=new Proxy(File,{get(target,name,receiver){
    if(name==='prototype'&&armed){armed=false;key=B;inner=controller.select(fresh,B);}
    return Reflect.get(target,name,receiver);
  }});
  controller=create({enabled:true,getContextKey:()=>key,FileImpl});
  assert.equal(controller.select(file('old.fasta'),A),false);
  assert.equal(inner,true);assert.equal(controller.acquire(A),null);
  assert.equal(controller.acquire(B).getFile(B),fresh);
});

test('context-triggered replacement microtask cannot accept a borrowed old completion',async()=>{
  let controller,arm=false,key=A;
  const fresh=file('new.faa'),old=file('old.faa');
  controller=create({enabled:true,getContextKey(){if(arm){arm=false;queueMicrotask(()=>{key=B;controller.select(fresh,B);});}return key;}});
  controller.select(old,A);const lease=controller.acquire(A);arm=true;
  const borrowed=lease.getFile(A);assert.equal(borrowed,old);
  await tick();assert.equal(lease.complete(A),false);assert.equal(controller.acquire(B).getFile(B),fresh);
  assert.equal(borrowed,old); // Raw references are intentionally not force-revocable.
});

test('an obsolete completion does not call unavailable context or drop the new File',()=>{
  let broken=false,calls=0;
  const c=create({enabled:true,getContextKey(){calls++;if(broken)throw Error('PRIVATE');return A;}});
  c.select(file('old.fa'),A);const old=c.acquire(A);
  const fresh=file('new.fa');c.select(fresh,A);const current=c.acquire(A);
  broken=true;const before=calls;assert.equal(old.complete(A),false);assert.equal(old.getFile(A),null);assert.equal(calls,before);
  broken=false;assert.equal(current.getFile(A),fresh);assert.equal(current.complete(A),true);
});

test('dispose reentry from status cannot retain a stale selected phase',()=>{
  let controller,dispose=false;
  controller=create({enabled:true,getContextKey(){if(dispose)controller.dispose();return A;}});
  controller.select(file('synthetic.fa'),A);const lease=controller.acquire(A);dispose=true;
  assert.deepEqual(controller.status(),{phase:'disposed',hasSelection:false,code:null});
  assert.equal(lease.complete(A),false);assert.equal(controller.select(file('fresh.fa'),A),false);
});

test('recursive completion during the provider call is bounded and cannot succeed twice',()=>{
  let controller,lease,arm=false,nested;
  controller=create({enabled:true,getContextKey(){if(arm){arm=false;nested=lease.complete(A);}return A;}});
  controller.select(file('synthetic.fa'),A);lease=controller.acquire(A);arm=true;
  assert.equal(lease.complete(A),false);assert.equal(nested,false);assert.equal(lease.complete(A),false);
  assert.equal(controller.status().hasSelection,false);
});

test('unobserved same-key ABA remains the documented host responsibility',()=>{
  let key=A;const c=create({enabled:true,getContextKey:()=>key});
  c.select(file('synthetic.fa'),A);const lease=c.acquire(A);
  key=B;key=A; // No observer call or explicit invalidate occurred between transitions.
  assert.equal(lease.complete(A),true);
  // This is a limitation test, not Owner authentication or an ABA-protection claim.
  c.invalidate();c.select(file('new.fa'),A);assert.equal(lease.getFile(A),null);
});
