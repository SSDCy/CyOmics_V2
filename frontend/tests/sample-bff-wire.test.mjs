import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash,webcrypto} from 'node:crypto';
import {harness} from './sample-bff-wire-harness.mjs';
import {validateImportSuccess} from '../imports-contract.mjs';
import {designSHA} from '../sample-design-contract.mjs';
import {createSampleDesignClient} from '../sample-design-client.js';
const base=JSON.parse(readFileSync(new URL('./fixtures/import-complete-v2.synthetic.json',import.meta.url),'utf8'));
const id=n=>'00000000-0000-0000-0000-'+String(n).padStart(12,'0'),clone=v=>structuredClone(v),tick=()=>new Promise(setImmediate);
const pid=base.datasetVersion.project_id,vid=base.datasetVersion.dataset_version_id,fid=base.datasetVersion.file_id;
const importRead=v=>'/api/v1/uploads/'+v.datasetVersion.file_id+'/complete?project_id='+v.datasetVersion.project_id+'&version_id='+v.datasetVersion.dataset_version_id;
const path=(v=base,family='intensity',project=v.datasetVersion.project_id)=>'/api/v1/datasets/'+v.datasetVersion.dataset_version_id+'/sample-designs?project_id='+project+'&quant_family='+family;
const draft=(v=base,family='Intensity',large=false)=>({schemaVersion:1,dataset_version_id:v.datasetVersion.dataset_version_id,quant_family:family,rows:v.importReport.sample_columns.filter(c=>c.quant_family===family).map(c=>({column_ordinal:c.ordinal,name_raw:c.name_raw,sample_label_raw:c.sample_label_raw,group:large?'g'.repeat(80):'',condition:large?'c'.repeat(160):'',control:'unknown',replicate:large?'r'.repeat(40):'',replicate_type:'unknown',confirmed:false}))});
const payload=(v=base)=>({schemaVersion:1,request_id:id(91),expected_head_version_id:null,design:draft(v)});
const envelope=(record=null,v=base,family='Intensity')=>({schemaVersion:1,project_id:v.datasetVersion.project_id,dataset_version_id:v.datasetVersion.dataset_version_id,quant_family:family,record});
async function record(p){return {sample_design_version_id:id(92),parent_version_id:p.expected_head_version_id,revision:1,request_id:p.request_id,design_sha256:await designSHA(p.design,webcrypto),created_at:'2026-11-17T01:02:03.123456Z',design:p.design};}
async function cache(h,s,v=base){h.reply(c=>c.deliver(v));const r=await h.send(importRead(v),s);assert.equal(r.status,200);assert.deepEqual(r.body,v);return r;}
function fixture(n=1,count=1,labelBytes=0){
  const v=clone(base),file=id(200+n),ns=Buffer.from('3bc2494484f25e4380cb9d536a8b9231','hex'),hash=createHash('sha1').update(ns).update(file+':protein_groups_txt_v1:'+v.datasetVersion.config_sha256).digest().subarray(0,16);hash[6]=hash[6]&15|80;hash[8]=hash[8]&63|128;const s=hash.toString('hex'),dataset=s.slice(0,8)+'-'+s.slice(8,12)+'-'+s.slice(12,16)+'-'+s.slice(16,20)+'-'+s.slice(20);
  v.datasetVersion.file_id=file;v.datasetVersion.dataset_version_id=dataset;v.datasetVersion.source.raw_file_id=file;v.importReport.report.dataset_version_id=dataset;
  const cols=[{ordinal:0,name_raw:'Protein IDs',quant_family:null,sample_label_raw:null},...Array.from({length:count},(_,i)=>({ordinal:i+1,name_raw:'Intensity '+('x'.repeat(labelBytes))+i,sample_label_raw:'x'.repeat(labelBytes)+i,quant_family:'Intensity'}))];
  const header=Buffer.from(cols.map(c=>c.name_raw).join('\t')),rows=['P1; P2','P3'].map(p=>Buffer.from(p+'\t'+Array(count).fill('1').join('\t'))),raw=Buffer.concat([header,Buffer.from('\r\n'),rows[0],Buffer.from('\r\n'),rows[1],Buffer.from('\r\n')]);
  const source=v.datasetVersion.source;source.expected_bytes=raw.length;source.expected_raw_sha256=createHash('sha256').update(raw).digest('hex');source.raw_version_key='raw_bytea_v1:'+file+':'+source.expected_raw_sha256;
  const d=v.importReport,r=d.report;r.source=clone(source);r.observed_bytes=raw.length;r.observed_raw_sha256=source.expected_raw_sha256;r.column_count=cols.length;r.intensity_sample_count=count;r.lfq_sample_count=0;r.quant_empty_string_cells=0;r.quant_exact_zero_string_cells=0;d.warnings=[];d.columns=cols;d.sample_columns=cols.slice(1);d.header_span={start_byte:0,end_byte:header.length,start_line:1,end_line:1};let offset=header.length+2;d.row_source_spans=rows.map((row,i)=>{const span={record_number:i+1,start_byte:offset,end_byte:offset+row.length,start_line:i+2,end_line:i+2};offset+=row.length+2;return span;});
  validateImportSuccess(v,{method:'GET',project_id:pid,file_id:file,version_id:dataset});return v;
}

test('WIRE default false does no Auth or upstream action',async t=>{const h=harness(t);const r=await h.send(path(),{method:'POST',value:payload()});assert.equal(r.status,503);assert.equal(r.body.error.code,'sample_design_unavailable');assert.equal(h.auth.length,0);assert.equal(h.bridges.length,0);});
test('WIRE missing source waits for report; identity/context/Host/Origin are verified first',async t=>{
  const h=harness(t,{importsEnabled:true,sampleDesignsEnabled:true});assert.equal((await h.send(path())).status,401);const s=await h.signed();
  const miss=await h.send(path(),s);assert.equal(miss.status,503);assert.equal(miss.body.error.code,'sample_design_unavailable');
  for(const view of [undefined,'bad','z'.repeat(22)]){const r=await h.send(path(),{cookie:s.cookie,view});assert.equal(r.status,409);assert.equal(r.body.error.code,'project_context_changed');}
  for(const headers of [{host:'evil.invalid'},{origin:'http://evil.invalid'},{authorization:'Bearer CALLER'},{'x-owner-id':pid},{'x-cyomics-remaining-ms':'10'}])assert.equal((await h.send(path(),{...s,headers})).status,403);
  assert.equal(h.bridges.length,0);
});
test('WIRE only verified successful import GET caches source; POST alone cannot unlock sample writes',async t=>{
  const h=harness(t,{importsEnabled:true,sampleDesignsEnabled:true}),s=await h.signed();h.reply(c=>c.deliver(base));assert.equal((await h.send('/api/v1/uploads/'+fid+'/complete',{...s,method:'POST',value:{project_id:pid}})).status,200);assert.equal((await h.send(path(),s)).status,503);assert.equal(h.bridges.length,1);
  await cache(h,s);h.reply(c=>c.deliver(envelope()));assert.equal((await h.send(path(),s)).status,200);
});
test('WIRE validated GET source then save and reload is source-bound; no cookies/nonce/token leaks',async t=>{
  const h=harness(t,{importsEnabled:true,sampleDesignsEnabled:true}),s=await h.signed();await cache(h,s);let saved;h.reply(async c=>{if(c.request.method==='POST'){saved=await record(JSON.parse(c.body));}c.deliver(envelope(saved));});
  const save=await h.send(path(),{...s,method:'POST',value:payload()});assert.equal(save.status,200);const loaded=await h.send(path(),s);assert.deepEqual(loaded.body,save.body);assert.equal(h.bridges.length,3);
  for(const c of h.bridges.slice(1)){assert.equal(c.request.hostname,'127.0.0.1');assert.equal(c.request.port,57430);assert.equal(c.request.headers.Authorization,'Bearer ACCESS_A');for(const key of ['Cookie','X-Cyomics-Account-Context','X-Owner-Id'])assert.equal(c.request.headers[key],undefined);}
  for(const secret of ['ACCESS_A','REFRESH_SECRET',s.view,s.cookie])assert.ok(!JSON.stringify([save,loaded]).includes(secret));
});
test('WIRE project/dataset/family mismatch and client-reported source cannot reach current operation',async t=>{
  const h=harness(t,{importsEnabled:true,sampleDesignsEnabled:true}),s=await h.signed();await cache(h,s);const n=h.bridges.length;
  assert.equal((await h.send(path(base,'intensity',id(77)),s)).status,503);assert.equal((await h.send(path().replace(vid,id(78)),s)).status,503);
  assert.equal((await h.send(path(base,'lfq'),{...s,method:'POST',value:payload()})).status,422);
  assert.equal((await h.send(path(),{...s,method:'POST',value:{...payload(),source:base.importReport.sample_columns}})).status,400);assert.equal(h.bridges.length,n);
  h.reply(c=>c.deliver(envelope()));const wrongFamily=await h.send(path(base,'lfq'),s);assert.equal(wrongFamily.status,503);assert.equal(wrongFamily.body.error.code,'sample_design_persisted_invalid');
});
test('WIRE sources never cross session even for same account; logout and new identity clear cache',async t=>{
  const h=harness(t,{importsEnabled:true,sampleDesignsEnabled:true}),a=await h.signed();await cache(h,a);const same=await h.signed();assert.equal((await h.send(path(),same)).status,503);
  const b=await h.signed('B',a.cookie);assert.equal((await h.send(path(),b)).status,503);assert.equal((await h.send('/api/auth/logout',{...b,method:'POST',value:{}})).status,200);const fresh=await h.signed();assert.equal((await h.send(path(),fresh)).status,503);assert.equal(h.bridges.length,1);
});
test('WIRE cache max16, single source128KiB, overflow leaves original import GET unchanged',async t=>{
  const h=harness(t,{importsEnabled:true,sampleDesignsEnabled:true}),s=await h.signed(),reports=Array.from({length:17},(_,i)=>fixture(i+1));for(const v of reports)await cache(h,s,v);
  const large=fixture(50,1,70000);await cache(h,s,large);const before=h.bridges.length;assert.equal((await h.send(path(reports[16]),s)).status,503);assert.equal((await h.send(path(large),s)).status,503);assert.equal(h.bridges.length,before);
  h.reply(c=>c.deliver(envelope(null,reports[0])));assert.equal((await h.send(path(reports[0]),s)).status,200);
});
test('WIRE expiry/dispose revoke cached source and abort pending sample requests',async t=>{
  const h=harness(t,{importsEnabled:true,sampleDesignsEnabled:true}),s=await h.signed();await cache(h,s);h.advance(900001);assert.equal((await h.send(path(),s)).status,401);const fresh=await h.signed();assert.equal((await h.send(path(),fresh)).status,503);
  await cache(h,fresh);let entered;const waiting=new Promise(r=>entered=r);h.reply(()=>entered());const pending=h.start(path(),fresh);await waiting;h.runtime.dispose();const result=await pending.promise;assert.notEqual(result.status,200);const n=h.bridges.length;assert.equal((await h.send(path(),fresh)).status,503);assert.equal(h.bridges.length,n);
});
test('WIRE 401 from sample revokes and aborts project/upload/import/sample operations',async t=>{
  const h=harness(t,{importsEnabled:true,sampleDesignsEnabled:true}),s=await h.signed();await cache(h,s);const pending=[];h.reply(()=>{});
  for(const url of ['/api/v1/projects','/api/v1/projects/'+pid+'/files',importRead(base),path()])pending.push(h.start(url,s));await tick();const calls=h.bridges.slice(1);assert.equal(calls.length,4);
  h.reply(c=>c.deliver({error:{code:'unauthenticated'}},401));const revoked=await h.send(path(),s);assert.equal(revoked.status,401);assert.ok(revoked.headers['set-cookie']);await Promise.all(pending.map(p=>p.promise));for(const c of calls)assert.equal(c.out.destroyed,true);assert.equal((await h.send(path(),s)).status,401);
});
test('WIRE replacement before send409 vs after send504 unknown; late private results never publish',async t=>{
  const h=harness(t,{importsEnabled:true,sampleDesignsEnabled:true}),s=await h.signed();await cache(h,s);
  const before=h.start(path(),{...s,method:'POST',sendBody:false});const b=await h.signed('B',s.cookie);assert.equal((await before.promise).body.error.code,'project_context_changed');
  await cache(h,b);let late,entered;const started=new Promise(r=>entered=r);h.reply(c=>{late=c;entered();});const after=h.start(path(),{...b,method:'POST',value:payload()});await started;const a=await h.signed('A',b.cookie);const unknown=await after.promise;assert.equal(unknown.status,504);assert.equal(unknown.body.error.code,'sample_design_result_unknown');late.deliver(envelope(await record(payload())));await tick();assert.notEqual(after.result.status,200);assert.equal((await h.send('/api/v1/me',{cookie:a.cookie})).status,200);
});
test('WIRE timeout after possible POST send is unknown without retries or polling',async t=>{
  const h=harness(t,{importsEnabled:true,sampleDesignsEnabled:true}),s=await h.signed();await cache(h,s);let entered;const started=new Promise(r=>entered=r);h.reply(()=>entered());const pending=h.start(path(),{...s,method:'POST',value:payload()});await started;const count=h.bridges.length;h.expire();const r=await pending.promise;assert.equal(r.status,504);assert.equal(r.body.error.code,'sample_design_result_unknown');await tick();assert.equal(h.bridges.length,count);
});
test('WIRE bounded sample response keeps identity/expiry fence through backpressure and finish',async t=>{
  const h=harness(t,{importsEnabled:true,sampleDesignsEnabled:true}),s=await h.signed(),v=fixture(80,180);await cache(h,s,v);const p={...payload(v),design:draft(v,'Intensity',true)},r=await record(p);assert.ok(Buffer.byteLength(JSON.stringify(envelope(r,v)))>65536);h.reply(c=>c.deliver(envelope(r,v)));
  let wrote;const firstWrite=new Promise(resolve=>wrote=resolve);const pending=h.start(path(v),{...s,response:{writeResults:[false],onWrite:()=>wrote()}});const destroy=pending.res.destroy;pending.res.destroy=()=>{try{return destroy();}catch(error){if(error instanceof SyntaxError&&pending.res.destroyed){pending.res.emit('close');return;}throw error;}};await firstWrite;assert.equal(pending.res.writes.length,1);const b=await h.signed('B',s.cookie);await pending.promise;assert.equal(pending.res.destroyed,true);assert.equal(pending.res.endCalls.length,0);pending.res.emit('drain');assert.equal(pending.res.writes.length,1);assert.equal((await h.send(path(v),b)).status,503);
});
test('R2 client recognizes typed409 context_changed without head-conflict or unknown',async()=>{
  let calls=0;const source={dataset_version_id:vid,sample_columns:base.importReport.sample_columns};const c=createSampleDesignClient({projectId:pid,datasetVersionId:vid,source,isCurrent:()=>true,cryptoImpl:webcrypto,transport:({route})=>{calls++;return Response.json(route.method==='GET'?envelope():{error:{code:'project_context_changed'}},{status:route.method==='GET'?200:409});}});
  await c.loadDraft({dataset_version_id:vid,quant_family:'Intensity'});await assert.rejects(c.saveDraft(draft()),e=>e.status===409&&e.code==='project_context_changed');assert.equal(c.status('Intensity').phase,'context_changed');assert.equal(c.status('Intensity').dirty,true);assert.equal(c.status('Intensity').uncertain_request_id,null);assert.equal(calls,2);
});

test('WIRE sample401 also cancels a pending me operation under the same private session',async t=>{
  let hold=false,entered,release;const blocked=new Promise(r=>entered=r),late=new Promise(r=>release=r);
  const h=harness(t,{importsEnabled:true,sampleDesignsEnabled:true,fixtureCurrent:async()=>{if(hold){entered();await late;}}}),s=await h.signed();await cache(h,s);hold=true;
  const pending=h.start('/api/v1/me',{cookie:s.cookie});await blocked;h.reply(c=>c.deliver({error:{code:'unauthenticated'}},401));assert.equal((await h.send(path(),s)).status,401);const cancelled=await pending.promise;assert.equal(cancelled.status,401);release();await tick();assert.notEqual(cancelled.body?.status,'authenticated');
});
test('WIRE sample response retains original deadline through end until actual finish',async t=>{
  const h=harness(t,{importsEnabled:true,sampleDesignsEnabled:true}),s=await h.signed();await cache(h,s);h.reply(c=>c.deliver(envelope()));let ended;const end=new Promise(r=>ended=r);
  const pending=h.start(path(),{...s,response:{autoFinish:false,onEnd:()=>ended()}});await end;assert.equal(pending.res.writableFinished,false);assert.ok(h.timers.size>0);h.expire();await pending.promise;assert.equal(pending.res.destroyed,true);assert.equal(pending.res.endCalls.length,1);assert.equal(h.timers.size,0);
});
test('R2 local context lost before POST stays409 context_changed; after possible send becomes504 unknown',async()=>{
  const source={dataset_version_id:vid,sample_columns:base.importReport.sample_columns},context={dataset_version_id:vid,quant_family:'Intensity'};
  let active=true,calls=0;const early=createSampleDesignClient({projectId:pid,datasetVersionId:vid,source,isCurrent:()=>active,cryptoImpl:webcrypto,transport:()=>{calls++;return Response.json(envelope());}});
  await early.loadDraft(context);active=false;await assert.rejects(early.saveDraft(draft()),e=>e.status===409&&e.code==='project_context_changed');assert.equal(early.status('Intensity').phase,'context_changed');assert.equal(early.status('Intensity').dirty,true);assert.equal(early.status('Intensity').uncertain_request_id,null);assert.equal(calls,1);
  active=true;let release;const late=createSampleDesignClient({projectId:pid,datasetVersionId:vid,source,isCurrent:()=>active,cryptoImpl:webcrypto,transport:({route})=>route.method==='GET'?Response.json(envelope()):new Promise(r=>release=r)});
  await late.loadDraft(context);const saving=late.saveDraft(draft());active=false;release(Response.json(envelope(await record(payload()))));await assert.rejects(saving,e=>e.status===504&&e.code==='sample_design_result_unknown');assert.equal(late.status('Intensity').phase,'unknown');assert.equal(late.status('Intensity').dirty,true);
});
