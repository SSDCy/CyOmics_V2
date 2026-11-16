import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {EventEmitter,getEventListeners} from 'node:events';
import {harness} from './raw-upload-harness.mjs';
import {sendRawUploadResponse} from '../raw-upload-response.mjs';
const pid='11111111-1111-4111-8111-111111111111',fid='77777777-7777-4777-8777-777777777777';
const path='/api/v1/projects/'+pid+'/files/'+fid+'/content';
const tick=()=>new Promise(setImmediate);
function download(h,raw=Buffer.alloc(2*65536+17,0x61)){
  const sha=createHash('sha256').update(raw).digest('hex');
  h.reply(c=>c.deliver(raw,200,['Content-Type','application/octet-stream','Content-Length',String(raw.length),'Content-Disposition','attachment; filename="raw.txt"','X-Cyomics-File-Sha256',sha,'Cache-Control','no-store','X-Content-Type-Options','nosniff','Referrer-Policy','no-referrer']));return raw;
}
const clean=res=>{for(const event of ['drain','finish','error','close'])assert.equal(res.listenerCount(event),0,event+' listener leaked');};

test('binary response retains deadline and operation after end until real finish',{timeout:2000},async t=>{
  const h=harness(t),s=await h.signed(),raw=download(h);const pending=h.start(path,{...s,response:{autoFinish:false}});
  let settled=false;pending.promise.then(()=>{settled=true;});await tick();
  assert.equal(pending.res.writableEnded,true);assert.equal(pending.res.writableFinished,false);
  assert.equal(settled,false,'end only queues bytes; finish must retain operation');assert.equal(h.timers.size,1);
  assert.ok(pending.res.writes.length>1);assert.ok(pending.res.writes.every(x=>x.length<=65536));
  pending.res.finish();const result=await pending.promise;assert.deepEqual(result.body,raw);assert.equal(h.timers.size,0);clean(pending.res);
});

test('repeated backpressure waits for each drain and sends at most 64KiB per write',{timeout:2000},async t=>{
  const h=harness(t),s=await h.signed(),raw=download(h,Buffer.alloc(3*65536+13,0x62));
  const p=h.start(path,{...s,response:{autoFinish:false,writeResults:[false,false,false,false]}});
  await tick();assert.equal(p.res.writes.length,1);assert.equal(p.res.writableEnded,undefined);
  for(let count=2;count<=4;count++){
    p.res.emit('drain');await tick();assert.equal(p.res.writes.length,count);assert.equal(p.res.endCalls.length,0);assert.equal(h.timers.size,1);
  }
  p.res.emit('drain');await tick();assert.equal(p.res.endCalls.length,1);assert.equal(p.res.endCalls[0],undefined);
  assert.deepEqual(p.res.writes.map(b=>b.length),[65536,65536,65536,13]);
  p.res.finish();const result=await p.promise;assert.deepEqual(result.body,raw);clean(p.res);assert.equal(p.req.listenerCount('aborted'),0);assert.equal(h.timers.size,0);
});

test('two pending binary finishes retain both long slots; finish then releases a slot',{timeout:2000},async t=>{
  const h=harness(t),s=await h.signed();download(h);
  const first=h.start(path,{...s,response:{autoFinish:false}}),second=h.start(path,{...s,response:{autoFinish:false}});await tick();
  assert.equal(h.timers.size,2);const blocked=await h.send(path,s);
  assert.equal(blocked.status,429);assert.equal(blocked.body.error.code,'upload_busy');assert.equal(h.bridges.length,2);
  first.res.finish();await first.promise;assert.equal((await h.send(path,s)).status,200);assert.equal(h.bridges.length,3);
  second.res.finish();await second.promise;assert.equal(h.timers.size,0);clean(first.res);clean(second.res);
});

test('deadline aborts after end without waiting indefinitely for finish or writing JSON',{timeout:2000},async t=>{
  const h=harness(t),s=await h.signed();download(h);const p=h.start(path,{...s,response:{autoFinish:false}});await tick();
  assert.equal(p.res.writableEnded,true);h.advance(55001);h.expire();const result=await p.promise;
  assert.equal(p.res.destroyCalls,1);assert.equal(p.res.writableFinished,false);assert.equal(p.res.endCalls.length,1);assert.equal(p.res.endCalls[0],undefined);
  assert.equal(result.status,200);assert.ok(Buffer.isBuffer(result.body));assert.equal(h.timers.size,0);clean(p.res);
  assert.equal((await h.send(path,s)).status,200,'failed send releases its long slot');
});

test('monotonic deadline while awaiting drain destroys without queueing the next chunk',{timeout:2000},async t=>{
  const h=harness(t),s=await h.signed();download(h);const p=h.start(path,{...s,response:{autoFinish:false,writeResults:[false]}});await tick();
  h.advance(55001,'mono');p.res.emit('drain');await p.promise;
  assert.equal(p.res.writes.length,1);assert.equal(p.res.endCalls.length,0);assert.equal(p.res.destroyCalls,1);assert.equal(h.timers.size,0);clean(p.res);
});

test('budget is checked immediately after each write even with no backpressure',{timeout:2000},async t=>{
  const h=harness(t),s=await h.signed();download(h);
  const p=h.start(path,{...s,response:{autoFinish:false,onWrite:()=>h.advance(55001,'mono')}});await p.promise;
  assert.equal(p.res.writes.length,1);assert.equal(p.res.endCalls.length,0);assert.equal(p.res.destroyCalls,1);clean(p.res);
});

test('finish itself rechecks deadline when the timer has not yet run',{timeout:2000},async t=>{
  const h=harness(t),s=await h.signed();download(h);const p=h.start(path,{...s,response:{autoFinish:false}});await tick();
  h.advance(55001,'mono');p.res.finish();await p.promise;assert.equal(p.res.destroyCalls,1);assert.equal(h.timers.size,0);clean(p.res);
});

test('session expiry clamps send deadline below 55s and aborts a pending finish',{timeout:2000},async t=>{
  const h=harness(t),s=await h.signed();h.advance(899000);download(h);const p=h.start(path,{...s,response:{autoFinish:false}});await tick();
  assert.ok([...h.timers.values()].every(v=>v.ms<=1000));h.advance(1001);h.expire();await p.promise;
  assert.equal(p.res.destroyCalls,1);assert.equal(p.res.endCalls.length,1);assert.equal(h.timers.size,0);clean(p.res);assert.equal((await h.send('/api/v1/me',s)).status,401);
});

for(const phase of ['drain','finish']){
  test('logout aborts binary '+phase+' wait and never appends an error body',{timeout:2000},async t=>{
    const h=harness(t),s=await h.signed();download(h);const p=h.start(path,{...s,response:{autoFinish:false,writeResults:phase==='drain'?[false]:[]}});await tick();
    assert.equal((await h.send('/api/auth/logout',s)).status,200);const result=await p.promise;
    assert.equal(p.res.destroyCalls,1);assert.equal(p.res.endCalls.length,phase==='drain'?0:1);assert.equal(result.headers['set-cookie'],undefined);assert.ok(Buffer.isBuffer(result.body));clean(p.res);assert.equal(h.timers.size,0);
  });
  test('replacement identity aborts binary '+phase+' wait without changing the new account',{timeout:2000},async t=>{
    const h=harness(t),a=await h.signed();download(h);const p=h.start(path,{...a,response:{autoFinish:false,writeResults:phase==='drain'?[false]:[]}});await tick();
    const b=await h.signed('B',a.cookie);const result=await p.promise;
    assert.equal(p.res.destroyCalls,1);assert.equal(result.headers['set-cookie'],undefined);assert.ok(Buffer.isBuffer(result.body));clean(p.res);assert.equal(h.timers.size,0);
    assert.equal((await h.send('/api/v1/me',b)).body.user.email,'b@example.invalid');
  });
}

for(const event of ['close','error','aborted']){
  test('client '+event+' promptly terminates a blocked send and removes all send listeners',{timeout:2000},async t=>{
    const h=harness(t),s=await h.signed();download(h);const p=h.start(path,{...s,response:{autoFinish:false,writeResults:[false]}});await tick();
    if(event==='aborted')p.req.emit(event);else p.res.emit(event,event==='error'?Error('synthetic private detail'):undefined);
    const result=await p.promise;assert.equal(p.res.destroyCalls,1);assert.equal(p.res.writes.length,1);assert.equal(p.res.endCalls.length,0);assert.ok(Buffer.isBuffer(result.body));clean(p.res);assert.equal(h.timers.size,0);assert.equal(p.req.listenerCount('aborted'),0);
  });
}

test('sender abort removes its signal and stream listeners without requiring drain or finish',{timeout:2000},async()=>{
  const res=new EventEmitter(),controller=new AbortController();res.writeHead=()=>{};res.write=()=>false;res.end=()=>assert.fail('must await drain');
  const pending=sendRawUploadResponse(res,{status:200,headers:{},body:Buffer.alloc(70000),signal:controller.signal,checkLive:()=>{}});
  assert.equal(getEventListeners(controller.signal,'abort').length,1);controller.abort();await assert.rejects(pending,{status:503,code:'uploads_unavailable'});
  assert.equal(getEventListeners(controller.signal,'abort').length,0);clean(res);
});
