import test from 'node:test';import assert from 'node:assert/strict';
import {createFastaFileLeaseController} from '../fasta-file-lease.js';
const A='ctx:synthetic-owner-A:project-A:nonce-A:revision-1',B='ctx:synthetic-owner-A:project-B:nonce-A:revision-2',C='ctx:synthetic-owner-B:project-B:nonce-B:revision-3';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function file(name='synthetic.fasta',data='>synthetic\nAC\n'){
 const f=new File([data],name);let content=0,metadata=0;
 for(const key of ['stream','text','arrayBuffer','slice','bytes'])Object.defineProperty(f,key,{get(){content++;throw Error('Content access forbidden');}});
 for(const key of ['name','size','type','lastModified'])Object.defineProperty(f,key,{get(){metadata++;throw Error('Own metadata getter forbidden');}});
 return{f,reads:()=>({content,metadata})};
}
function setup(){let key=A,calls=0;const c=createFastaFileLeaseController({enabled:true,getContextKey(){calls++;return key;}});return{c,set(v){key=v;},calls:()=>calls};}

test('default off and truthy nonboolean options admit nothing and invoke no context/File traps',()=>{
 for(const enabled of [undefined,false,1,'true',new Boolean(true)]){let calls=0,properties=0;const c=createFastaFileLeaseController({enabled,getContextKey(){calls++;throw Error('Must not run');}}),fake=new Proxy({}, {get(){properties++;throw Error('Must not inspect');}});assert.equal(c.select(fake,A),false);assert.equal(c.acquire(A),null);assert.equal(c.status().phase,'disabled');c.clear();c.invalidate();c.dispose();assert.equal(calls,0);assert.equal(properties,0);}
});
test('two independent leases reuse the exact native File reference, no content or own metadata reads',()=>{
 const {c}=setup(),f=file();assert.equal(c.select(f.f,A),true);const preview=c.acquire(A),save=c.acquire(A);assert.notEqual(preview,save);assert.equal(preview.getFile(A),f.f);assert.equal(save.getFile(A),f.f);assert.equal(preview.complete(A),true);assert.equal(preview.getFile(A),null);assert.equal(preview.complete(A),false);assert.equal(save.getFile(A),f.f);assert.equal(save.complete(A),true);assert.deepEqual(f.reads(),{content:0,metadata:0});assert.equal(c.status().phase,'selected');
});
test('status and tokens expose no File, filename, key, nonce or private identity values',()=>{
 const {c}=setup(),f=file('PRIVATE_SEQUENCE_FILENAME.fasta');c.select(f.f,A);const lease=c.acquire(A),s=c.status();assert.ok(Object.isFrozen(c));assert.ok(Object.isFrozen(lease));assert.ok(Object.isFrozen(s));assert.deepEqual(Object.keys(s).sort(),['code','hasSelection','phase']);assert.doesNotMatch(JSON.stringify(c)+JSON.stringify(lease)+JSON.stringify(s),/PRIVATE|nonce|owner|project|synthetic|File|ctx:/);assert.equal(s.hasSelection,true);assert.throws(()=>{s.phase='saved';},TypeError);
});
for(const key of [null,undefined,'',' ','a b','a\n','a\r','a\t','a\0','a\x7f','α','x'.repeat(513),true,7,{},new String(A)])test('strict provided key rejects '+String(key).slice(0,12),()=>{
 const h=setup(),f=file();assert.equal(h.c.select(f.f,key),false);assert.equal(h.c.acquire(key),null);assert.equal(h.c.status().hasSelection,false);assert.equal(h.calls(),0);assert.deepEqual(f.reads(),{content:0,metadata:0});
});
for(const key of [null,undefined,'',' ','a\n',true,7,{},new String(A)])test('invalid authority key fails closed without file access '+String(key).slice(0,12),()=>{
 const h=setup(),f=file();h.set(key);assert.equal(h.c.select(f.f,A),false);assert.equal(h.c.acquire(A),null);assert.equal(h.c.status().hasSelection,false);assert.deepEqual(f.reads(),{content:0,metadata:0});
});
test('wrong consumer key denies access/completion without erasing a newer valid selection',()=>{
 const {c}=setup(),f=file();c.select(f.f,A);assert.equal(c.acquire(B),null);const a=c.acquire(A),b=c.acquire(A);assert.equal(a.getFile(B),null);assert.equal(a.isCurrent(B),false);assert.equal(a.complete(B),false);assert.equal(a.complete(A),false);assert.equal(b.getFile(A),f.f);assert.equal(c.status().hasSelection,true);
});
test('replacement invalidates every previous lease; old completion cannot clear or republish the new File',async()=>{
 const h=setup(),a=file(),b=file('replacement.faa');h.c.select(a.f,A);const old=h.c.acquire(A),delayed=tick().then(()=>old.complete(A));h.c.select(b.f,A);const current=h.c.acquire(A);assert.equal(await delayed,false);assert.equal(old.getFile(A),null);assert.equal(old.isCurrent(A),false);assert.equal(current.getFile(A),b.f);assert.equal(current.complete(A),true);assert.deepEqual(a.reads(),{content:0,metadata:0});assert.deepEqual(b.reads(),{content:0,metadata:0});
});
test('reselecting even the same File starts a new ownership generation',()=>{
 const {c}=setup(),f=file();c.select(f.f,A);const old=c.acquire(A);c.select(f.f,A);assert.equal(old.getFile(A),null);assert.equal(old.complete(A),false);assert.equal(c.acquire(A).getFile(A),f.f);
});
for(const action of ['clear','invalidate','dispose'])test(action+' revokes old leases immediately and late acceptance stays false',async()=>{
 const {c}=setup(),f=file();c.select(f.f,A);const a=c.acquire(A),b=c.acquire(A);assert.equal(c[action](),true);assert.equal(a.getFile(A),null);assert.equal(b.isCurrent(A),false);await tick();assert.equal(a.complete(A),false);assert.equal(b.complete(A),false);assert.equal(c.acquire(A),null);assert.equal(c.status().hasSelection,false);assert.equal(c[action](),false);assert.deepEqual(f.reads(),{content:0,metadata:0});
});
for(const key of [B,C,null,'ctx:synthetic-owner-A:project-A:nonce-rotated:revision-4'])test('project/Owner/nonce/expiry authority transition revokes active cell '+String(key),()=>{
 const h=setup(),f=file();h.c.select(f.f,A);const old=h.c.acquire(A);h.set(key);assert.equal(old.getFile(A),null);assert.equal(old.complete(A),false);assert.equal(h.c.status().hasSelection,false);h.set(A);assert.equal(old.isCurrent(A),false);assert.equal(h.c.acquire(A),null);assert.equal(h.c.select(f.f,A),true);assert.equal(old.getFile(A),null);
});
test('explicit context invalidation prevents ABA resurrection even when host reuses a key later',()=>{
 const h=setup(),f=file();h.c.select(f.f,A);const old=h.c.acquire(A);h.c.invalidate();h.set(B);h.c.invalidate();h.set(A);h.c.select(f.f,A);assert.equal(old.complete(A),false);assert.equal(h.c.acquire(A).getFile(A),f.f);
});
test('cancel only closes its own operation, so a later explicit operation reuses the selected File',()=>{
 const {c}=setup(),f=file();c.select(f.f,A);const a=c.acquire(A),b=c.acquire(A);assert.equal(a.cancel(),true);assert.equal(a.cancel(),false);assert.equal(a.getFile(A),null);assert.equal(a.complete(A),false);assert.equal(b.getFile(A),f.f);assert.equal(c.acquire(A).getFile(A),f.f);c.clear();assert.equal(b.getFile(A),null);
});
test('native brand check rejects fake tag/prototype and invalid replacement clears previous choice',()=>{
 const {c}=setup(),f=file();for(const fake of [null,{}, {name:'x.fasta',size:1,[Symbol.toStringTag]:'File'},Object.create(File.prototype)]){c.select(f.f,A);const old=c.acquire(A);assert.equal(c.select(fake,A),false);assert.equal(old.getFile(A),null);assert.equal(c.status().hasSelection,false);}assert.deepEqual(f.reads(),{content:0,metadata:0});
});
test('runtime brand check cannot promise proxy rejection; accepted references retain their original identity',()=>{
 const {c}=setup(),f=file(),proxy=new Proxy(f.f,{});let runtimeAccepts=false;
 try{runtimeAccepts=typeof Object.getOwnPropertyDescriptor(File.prototype,'name').get.call(proxy)==='string';}catch{}
 assert.equal(c.select(proxy,A),runtimeAccepts);
 if(runtimeAccepts)assert.equal(c.acquire(A).getFile(A),proxy);
 else assert.equal(c.acquire(A),null);
 assert.deepEqual(f.reads(),{content:0,metadata:0});
});
test('ownership does not pretend to validate size/extension/FASTA content or canonical sequence',()=>{
 const {c}=setup();for(const f of [new File([],'empty.exe'),new File(['not FASTA'],'synthetic.faa'),new File(['x'],'synthetic.fa')]){assert.equal(c.select(f,A),true);assert.equal(c.acquire(A).getFile(A),f);}assert.equal(c.status().phase,'selected');
});
test('throwing authority is sanitized and permanently revokes current leases',()=>{
 let fail=false;const c=createFastaFileLeaseController({enabled:true,getContextKey(){if(fail)throw Error('PRIVATE_NONCE_DETAIL');return A;}}),f=file();c.select(f.f,A);const lease=c.acquire(A);fail=true;assert.equal(lease.complete(A),false);assert.equal(c.status().hasSelection,false);assert.doesNotMatch(JSON.stringify(c.status()),/PRIVATE|NONCE/);assert.deepEqual(f.reads(),{content:0,metadata:0});
});
test('context changes during select cannot admit a File against an outdated context snapshot',()=>{
 let calls=0;const c=createFastaFileLeaseController({enabled:true,getContextKey:()=>++calls===1?A:B}),f=file();assert.equal(c.select(f.f,A),false);assert.equal(c.status().hasSelection,false);assert.equal(c.acquire(B),null);
});
test('synchronous reentrant clear cannot publish selection and recursive context observation is bounded',()=>{
 let c,clear=false;c=createFastaFileLeaseController({enabled:true,getContextKey(){if(clear)c.clear();return A;}});const f=file();c.select(f.f,A);const old=c.acquire(A);clear=true;assert.equal(old.getFile(A),null);assert.equal(c.select(f.f,A),false);assert.equal(c.status().hasSelection,false);
 let nested=false,d;d=createFastaFileLeaseController({enabled:true,getContextKey(){if(nested)d.status();return A;}});d.select(f.f,A);nested=true;assert.equal(d.acquire(A),null);assert.equal(d.status().hasSelection,false);
});
test('accidental native Promise authority is never awaited or accepted and rejected Promise is handled',async()=>{
 for(const getContextKey of [()=>Promise.resolve(A),()=>Promise.reject(Error('PRIVATE_ASYNC_CONTEXT'))]){const c=createFastaFileLeaseController({enabled:true,getContextKey}),f=file();assert.equal(c.select(f.f,A),false);await tick();assert.equal(c.status().hasSelection,false);assert.equal(c.acquire(A),null);assert.deepEqual(f.reads(),{content:0,metadata:0});}
});
test('arbitrary thenable authority is rejected without invoking its properties',()=>{
 let reads=0;const object={get then(){reads++;throw Error('No thenable access');}},c=createFastaFileLeaseController({enabled:true,getContextKey:()=>object}),f=file();assert.equal(c.select(f.f,A),false);assert.equal(reads,0);
});
test('an already borrowed raw File is not revocable; all later mediated access/completion is revoked',()=>{
 const {c}=setup(),f=file();c.select(f.f,A);const lease=c.acquire(A),borrowed=lease.getFile(A);c.clear();assert.equal(borrowed,f.f);assert.equal(lease.getFile(A),null);assert.equal(lease.complete(A),false);assert.deepEqual(f.reads(),{content:0,metadata:0});
});
test('completion is only one-shot ownership acceptance, never a publish callback or a save result',()=>{
 const {c}=setup(),f=file();c.select(f.f,A);const lease=c.acquire(A);let invoked=0;assert.equal(lease.complete(()=>{invoked++;}),false);assert.equal(invoked,0);assert.equal(lease.complete(A),false);assert.deepEqual(c.status(),{phase:'selected',hasSelection:true,code:null});
});
