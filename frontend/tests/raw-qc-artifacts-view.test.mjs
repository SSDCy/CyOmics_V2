import test from 'node:test';
import assert from 'node:assert/strict';
import {client,mounted,job,until,deferred,first,second,digest,memory} from './support/raw-qc-setup.mjs';
const rows=d=>d.all().filter(n=>n.className==='prqc-artifact');
const results=d=>d.all().find(n=>n.className==='prqc-artifacts');
const names=['raw-matrix.tsv','numeric-matrix.tsv','blank-mask.tsv','zero-mask.tsv','sample-qc.tsv','summary.tsv','R-session-info.txt'];
function wire({intensity='succeeded',lfq='queued'}={}){
 const jobs=new Map(),states={intensity,lfq};let denied=false;
 const fetchQC=(path,o)=>{
  if(denied)return new Response('expired',{status:401});
  if(o.method==='POST'){const b=JSON.parse(o.body),j=job({family:b.quant_family,requestId:b.request_id,status:states[b.quant_family],jobId:b.quant_family==='lfq'?'99999999-9999-4999-8999-999999999999':undefined});if(j.result&&b.quant_family==='lfq'){j.result.artifacts.forEach((a,i)=>{a.bytes+=100+i;});j.result.manifest_sha256=digest(j.result.artifacts);}jobs.set(j.job_id,j);return Response.json(j,{status:['queued','running'].includes(j.status)?202:200});}
  return Response.json(jobs.get(/raw-qc-jobs\/([0-9a-f-]{36})/.exec(path)[1]));
 };
 return{fetchQC,jobs,expire(){denied=true;}};
}
async function start(h,family='intensity'){h.host().setExpanded(true);h.host().selectFamily(family);await until(()=>h.host().view().phase==='idle');await h.host().start();}
function successClient(t,extra={}){let h;return client(t,{...extra,fetchImpl:(_,o)=>Response.json(job({status:'succeeded',requestId:o.method==='POST'?JSON.parse(o.body).request_id:h.c.reference().request_id}))}).then(value=>(h=value));}

test('successful projection is a fresh deeply frozen name/bytes copy with no wire metadata or reverse mutation',async t=>{
 const h=await successClient(t);await h.c.start();const a=h.c.view().artifacts,b=h.c.view().artifacts;
 assert.equal(a.length,7);assert.notEqual(a,b);assert.notEqual(a[0],b[0]);assert.ok(Object.isFrozen(a));
 for(const item of a){assert.ok(Object.isFrozen(item));assert.deepEqual(Object.keys(item).sort(),['bytes','name']);}
 assert.throws(()=>a.reverse(),TypeError);assert.throws(()=>{a[0].name='changed';},TypeError);
 assert.equal(h.c.view().artifacts[0].name,'R-session-info.txt');assert.doesNotMatch(JSON.stringify(h.c.view()),/sha256|binding|execution|token|limits_profile/);
});
test('queued, running, failed, unknown and not-connected expose no artifact list',async t=>{
 for(const status of ['queued','running','failed']){let h;h=await client(t,{fetchImpl:(_,o)=>Response.json(job({status,requestId:o.method==='POST'?JSON.parse(o.body).request_id:h.c.reference().request_id}),{status:o.method==='POST'&&status!=='failed'?202:200})});await h.c.start();assert.equal(h.c.view().phase,status);assert.deepEqual(h.c.view().artifacts,[]);}
 const unknown=await client(t,{fetchImpl:()=>Response.json({error:{code:'raw_qc_submission_unknown'}},{status:504})});await unknown.c.start();assert.equal(unknown.c.view().phase,'unknown');assert.deepEqual(unknown.c.view().artifacts,[]);
 const {createRawQCClient}=await import('../raw-qc-client.js');const off=createRawQCClient();await off.ready;assert.deepEqual(off.view().artifacts,[]);off.dispose();
});
test('invalid success artifacts and foreign source never enter the projection',async t=>{
 for(const foreign of [false,true]){const h=await client(t,{fetchImpl:(_,o)=>{const j=job({f:foreign?second:first,status:'succeeded',requestId:JSON.parse(o.body).request_id});if(!foreign)j.result.artifacts[0].bytes=1;return Response.json(j);}});await h.c.start();assert.equal(h.c.view().phase,'unknown');assert.deepEqual(h.c.view().artifacts,[]);}
});
test('checking immediately withdraws a previous successful projection and late response cannot restore lost context',async t=>{
 const held=deferred();let h,holding=false;h=await client(t,{fetchImpl:(_,o)=>holding?held.promise:Response.json(job({status:'succeeded',requestId:o.method==='POST'?JSON.parse(o.body).request_id:h.c.reference().request_id}))});await h.c.start();assert.equal(h.c.view().artifacts.length,7);const requestId=h.c.reference().request_id;holding=true;const checking=h.c.check();assert.equal(h.c.view().phase,'checking');assert.deepEqual(h.c.view().artifacts,[]);h.lose();assert.equal(h.c.view().disposed,true);assert.deepEqual(h.c.view().artifacts,[]);held.resolve(Response.json(job({status:'succeeded',requestId})));await checking;assert.deepEqual(h.c.view().artifacts,[]);
});
test('real mounted card has seven scientific labels, exact file names/bytes and no download links/buttons',async t=>{
 const w=wire(),h=await mounted(t,w);await start(h);assert.equal(results(h.d).hidden,false);assert.equal(results(h.d).open,false);assert.deepEqual(rows(h.d).map(n=>n.children[2].textContent),names);
 for(const text of ['原始矩阵','数值矩阵','原始空值掩码','原始零值掩码','样本统计','总体汇总','R 环境信息'])assert.match(h.d.root.textContent,new RegExp(text));
 assert.ok(rows(h.d).every(n=>n.children[1].textContent==='42 B'));assert.ok(rows(h.d).every(n=>n.children.every(c=>!['a','button','input'].includes(c.tag))));assert.equal(h.d.all().filter(n=>n.tag==='input'&&n.type==='file').length,1);
 const bytes=JSON.stringify([...w.jobs.values()]);results(h.d).open=true;h.host().setExpanded(false);h.host().setExpanded(true);assert.equal(results(h.d).open,true);assert.equal(JSON.stringify([...w.jobs.values()]),bytes);
});
test('display order is derived only and original manifest/SHA/binding remain untouched across locale and remount',async t=>{
 const w=wire(),h=await mounted(t,w);await start(h);const before=JSON.stringify([...w.jobs.values()]),calls=h.qcCalls().length,request=JSON.parse(h.qcCalls()[0].options.body).request_id,retained=rows(h.d)[0];
 h.remount(true);assert.equal(retained.textContent,'');assert.match(h.d.root.textContent,/Raw blank mask/);assert.match(h.d.root.textContent,/R session information/);assert.equal(rows(h.d).length,7);h.remount();assert.equal(rows(h.d).length,7);assert.equal(h.qcCalls().length,calls);assert.equal(JSON.parse(h.qcCalls()[0].options.body).request_id,request);assert.equal(JSON.stringify([...w.jobs.values()]),before);
});
test('family switching never leaks a finished Intensity list into queued/failed LFQ and restores its own success',async t=>{
 const w=wire(),h=await mounted(t,w);await start(h);const retained=rows(h.d)[0];h.host().selectFamily('lfq');await until(()=>h.host().view().phase==='idle');assert.equal(retained.textContent,'');assert.equal(rows(h.d).length,0);assert.equal(results(h.d).hidden,true);await h.host().start();assert.equal(h.host().view().phase,'queued');assert.equal(rows(h.d).length,0);
 const j=[...w.jobs.values()].find(j=>j.quant_family==='LFQ intensity');Object.assign(j,job({family:'lfq',jobId:j.job_id,requestId:j.request_id,status:'failed'}));await h.host().check();assert.equal(h.host().view().phase,'failed');assert.equal(rows(h.d).length,0);h.host().selectFamily('intensity');assert.equal(rows(h.d).length,7);assert.equal(h.qcCalls().filter(c=>c.options.method==='POST').length,2);
});
test('both families independently render their own validated sizes',async t=>{
 const w=wire({lfq:'succeeded'}),h=await mounted(t,w);await start(h);assert.equal(rows(h.d).length,7);assert.equal(rows(h.d)[0].children[1].textContent,'42 B');await start(h,'lfq');assert.equal(rows(h.d).length,7);assert.equal(rows(h.d)[0].children[1].textContent,'145 B');assert.equal(h.host().view().family,'lfq');assert.equal(h.qcCalls().filter(c=>c.options.method==='POST').length,2);h.host().selectFamily('intensity');assert.equal(rows(h.d)[0].children[1].textContent,'42 B');
});
test('new account partition and a different source restore neither prior account success nor artifact metadata',async t=>{
 const storage=memory(),a=await successClient(t,{storage});await a.c.start();assert.equal(a.c.view().artifacts.length,7);a.lose();assert.deepEqual(a.c.view().artifacts,[]);const b=await client(t,{storage,nonce:'R'.repeat(22)});assert.equal(b.c.view().phase,'idle');assert.deepEqual(b.c.view().artifacts,[]);assert.equal(b.calls.length,0);const c=await client(t,{storage,f:second,nonce:'R'.repeat(22)});assert.equal(c.c.view().phase,'idle');assert.deepEqual(c.c.view().artifacts,[]);assert.equal(c.calls.length,0);
});
test('source removal wipes success and retained row nodes immediately',async t=>{
 const h=await mounted(t,wire());await start(h);const host=h.host(),retained=rows(h.d);h.backend.setItems([]);await h.model.reload();assert.equal(host.view().disposed,true);assert.deepEqual(host.view().task,null);assert.ok(retained.every(n=>n.textContent===''));assert.equal(rows(h.d).length,0);assert.doesNotMatch(h.d.root.textContent,/raw-matrix.tsv|R-session-info.txt/);
});
test('expired response removes success, private projection and retained row nodes',async t=>{
 const w=wire(),h=await mounted(t,w);await start(h);const host=h.host(),retained=rows(h.d);w.expire();await host.check();assert.equal(h.model.status().phase,'disposed');assert.equal(host.view().disposed,true);assert.ok(retained.every(n=>n.textContent===''));assert.equal(rows(h.d).length,0);assert.equal(h.storage.values.size,0);
});
test('context invalidation and explicit unmount wipe success without extra request',async t=>{
 const h=await mounted(t,wire());await start(h);const host=h.host(),retained=rows(h.d),cells=retained.flatMap(n=>n.children),calls=h.qcCalls().length;h.backend.lose();assert.equal(h.host(),null);assert.equal(host.view().phase,'waiting');assert.equal(host.view().task,null);assert.ok(retained.every(n=>n.textContent===''));assert.ok(cells.every(n=>n.textContent===''));assert.equal(rows(h.d).length,0);h.cleanup();assert.equal(h.d.root.textContent,'');assert.equal(h.qcCalls().length,calls);
});
