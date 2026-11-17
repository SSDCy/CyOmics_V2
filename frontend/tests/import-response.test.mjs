import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {harness} from './raw-upload-harness.mjs';
import {validateImportSuccess} from '../imports-contract.mjs';

const base=JSON.parse(readFileSync(new URL('./fixtures/import-complete-v2.synthetic.json',import.meta.url),'utf8'));
const pid=base.datasetVersion.project_id,fid=base.datasetVersion.file_id;
const path='/api/v1/uploads/'+fid+'/complete',read=path+'?project_id='+pid;
const tick=()=>new Promise(setImmediate);
function large(){
  const value=JSON.parse(JSON.stringify(base)),label='x'.repeat(70000);
  const header=Buffer.from('\ufeffProtein IDs\tIntensity '+label+'\tLFQ intensity sample A');
  const rows=['P1; P2\t0\t','P3\t1\t0'].map(x=>Buffer.from(x));
  const raw=Buffer.concat([header,Buffer.from('\r\n'),rows[0],Buffer.from('\r\n'),rows[1],Buffer.from('\r\n')]);
  const source=value.datasetVersion.source;source.expected_bytes=raw.length;source.expected_raw_sha256=createHash('sha256').update(raw).digest('hex');source.raw_version_key='raw_bytea_v1:'+fid+':'+source.expected_raw_sha256;
  const doc=value.importReport;doc.report.source=JSON.parse(JSON.stringify(source));doc.report.observed_bytes=raw.length;doc.report.observed_raw_sha256=source.expected_raw_sha256;
  doc.columns[1].name_raw='Intensity '+label;doc.columns[1].sample_label_raw=label;doc.sample_columns=doc.columns.filter(c=>c.quant_family!==null);
  doc.header_span={start_byte:0,end_byte:header.length,start_line:1,end_line:1};
  let offset=header.length+2;doc.row_source_spans=rows.map((row,i)=>{const span={record_number:i+1,start_byte:offset,end_byte:offset+row.length,start_line:i+2,end_line:i+2};offset+=row.length+2;return span;});
  validateImportSuccess(value,{method:'POST',project_id:pid,file_id:fid,version_id:null});return value;
}
function start(h,s,response={}){
  const p=h.start(path,{...s,method:'POST',value:{project_id:pid},response});let headers=0;
  const writeHead=p.res.writeHead;p.res.writeHead=(...args)=>{headers++;return writeHead(...args);};
  const destroy=p.res.destroy;
  // The shared legacy fake parses captured JSON on destroy. A deliberately
  // destroyed partial JSON stream has no JSON body; preserve bytes and close,
  // without altering production code, old harness or any business assertion.
  p.res.destroy=()=>{try{return destroy();}catch(error){if(error instanceof SyntaxError&&p.res.destroyed){p.res.emit('close');return;}throw error;}};
  p.headerCount=()=>headers;return p;
}
const clean=res=>{for(const event of ['drain','finish','error','close'])assert.equal(res.listenerCount(event),0,event+' send listener leaked');};

test('import JSON waits through every drain and actual finish; subject slot and original timer stay held',{timeout:3000},async t=>{
  const h=harness(t,{importsEnabled:true}),s=await h.signed(),value=large();h.reply(c=>c.deliver(value));
  const p=start(h,s,{autoFinish:false,writeResults:[false,false,false]});let settled=false;p.promise.then(()=>settled=true);await tick();
  assert.equal(p.res.writes.length,1);assert.equal(p.res.endCalls.length,0);assert.equal(h.timers.size,1);
  const calls=h.bridges.length,blocked=await h.send(read,s);assert.equal(blocked.status,429);assert.equal(h.bridges.length,calls);assert.equal(h.timers.size,1);
  for(let i=0;i<3;i++){p.res.emit('drain');await tick();}
  assert.ok(p.res.writes.every(bytes=>bytes.length<=65536));assert.equal(p.res.endCalls.length,1);assert.equal(p.res.endCalls[0],undefined);
  assert.equal(p.res.writableEnded,true);assert.equal(p.res.writableFinished,false);assert.equal(settled,false);assert.equal(h.timers.size,1);
  p.res.finish();assert.deepEqual((await p.promise).body,value);assert.equal(p.headerCount(),1);assert.equal(h.timers.size,0);clean(p.res);
});
test('deadline during JSON drain or finish destroys once and never appends an error response',{timeout:3000},async t=>{
  for(const phase of ['drain','finish']){
    const h=harness(t,{importsEnabled:true}),s=await h.signed();h.reply(c=>c.deliver(large()));
    const p=start(h,s,{autoFinish:false,writeResults:phase==='drain'?[false]:[]});await tick();h.advance(55001);h.expire();await p.promise;
    assert.equal(p.res.destroyCalls,1);assert.equal(p.headerCount(),1);assert.equal(p.res.endCalls.length,phase==='drain'?0:1);
    assert.ok(p.res.endCalls.every(x=>x===undefined));assert.equal(h.timers.size,0);clean(p.res);
  }
});
test('logout, account replacement and expiry cancel a queued JSON finish under the original session fence',{timeout:3000},async t=>{
  for(const mode of ['logout','replacement','expiry']){
    const h=harness(t,{importsEnabled:true}),s=await h.signed();if(mode==='expiry')h.advance(899000);
    h.reply(c=>c.deliver(large()));const p=start(h,s,{autoFinish:false});await tick();assert.equal(p.res.writableEnded,true);let next;
    if(mode==='logout')assert.equal((await h.send('/api/auth/logout',s)).status,200);
    else if(mode==='replacement')next=await h.signed('B',s.cookie);
    else{assert.ok([...h.timers.values()].every(x=>x.ms<=1000));h.advance(1001);h.expire();}
    const result=await p.promise;assert.equal(p.res.destroyCalls,1);assert.equal(p.headerCount(),1);assert.equal(p.res.endCalls.length,1);assert.equal(p.res.endCalls[0],undefined);assert.equal(result.headers['set-cookie'],undefined);clean(p.res);
    if(next)assert.equal((await h.send('/api/v1/me',{cookie:next.cookie})).body.user.email,'b@example.invalid');
  }
});
test('budget after each JSON write and finish is checked even when its timer has not fired',{timeout:3000},async t=>{
  const h=harness(t,{importsEnabled:true}),s=await h.signed();h.reply(c=>c.deliver(large()));
  const p=start(h,s,{autoFinish:false,onWrite:()=>h.advance(55001,'mono')});await p.promise;
  assert.equal(p.res.writes.length,1);assert.equal(p.res.endCalls.length,0);assert.equal(p.res.destroyCalls,1);assert.equal(p.headerCount(),1);clean(p.res);
  h.reply(c=>c.deliver(base));const q=start(h,s,{autoFinish:false});await tick();h.advance(55001,'mono');q.res.finish();await q.promise;
  assert.equal(q.res.destroyCalls,1);assert.equal(q.headerCount(),1);assert.equal(q.res.endCalls.length,1);clean(q.res);
});
test('known backend 401 immediately revokes its snapshot but public 401 still waits for bounded finish',{timeout:3000},async t=>{
  const h=harness(t,{importsEnabled:true}),s=await h.signed();h.reply(c=>c.deliver({error:{code:'unauthenticated'}},401));
  const p=start(h,s,{autoFinish:false});await tick();assert.equal(p.res.writableEnded,true);assert.equal(p.res.writableFinished,false);assert.equal(h.timers.size,1);
  assert.equal((await h.send('/api/v1/me',{cookie:s.cookie})).status,401);p.res.finish();const result=await p.promise;
  assert.equal(result.status,401);assert.equal(result.body.error.code,'unauthenticated');assert.ok(result.headers['set-cookie'].some(value=>value.includes('Max-Age=0')));assert.equal(p.res.destroyCalls,0);assert.equal(h.timers.size,0);clean(p.res);
});
