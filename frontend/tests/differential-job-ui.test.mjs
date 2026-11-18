// New admission/recovery slice only. Official SDK + original session; synthetic I/O.
import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {harness} from './support/raw-qc-bff-session.mjs';
import {setup,processed} from './support/differential-job-composition.mjs';
import {projectId,first} from './support/contrast-mount-setup.mjs';
import {digest,webcrypto,memory,until,flush} from './support/preprocess-owner-setup.mjs';
import {DIFFERENTIAL_METHOD,differentialJobRoute,differentialJobReply} from '../differential-job-contract.mjs';
const uid=n=>String(n).padStart(8,'0')+'-1111-4111-8111-111111111111',owner='11111111-1111-4111-8111-111111111111';
const path='/api/v1/projects/'+projectId+'/differential-jobs',reviewPath='/api/v1/projects/'+projectId+'/differential-input';
const pins=()=>Object.fromEntries(['matrix','design','preset'].map(k=>[k,{bytes:100,sha256:'a'.repeat(64)}]));
const selection=source=>({processed_dataset_version_id:source.processed_dataset_version_id,sample_design_version_id:uid(2),sample_design_request_id:uid(3),contrast:{schemaVersion:1,dataset_version_id:source.parent_dataset_version_id,sample_design_version_id:uid(2),quant_family:source.quant_family,contrasts:[{contrast_id:uid(4),numerator_group:'Treatment',denominator_group:'Control'}]},replicate_model:'independent_biological'});
function review(s,satisfied=true){return{schemaVersion:1,contract:'differential_input_review_v0.1',project_id:projectId,...Object.fromEntries(['processed_dataset_version_id','sample_design_version_id','sample_design_request_id'].map(k=>[k,s[k]])),model_input_satisfied:satisfied,reasons:satisfied?[]:['insufficient_numerator_biological_replicates'],source_analysis_ready:false,input_summary:satisfied?{row_count:2,sample_count:4,design_rank:2,contrast:structuredClone(s.contrast.contrasts[0]),input_pins:pins()}:null,calculation_started:false};}
function queued(body,source,user=owner){const {request_id,...s}=body;return{schemaVersion:2,contract:'differential_queued_v0.1',job_id:uid(500),project_id:projectId,request_id,
 request_sha256:digest({owner_id:user,project_id:projectId,request:body}),binding_sha256:'b'.repeat(64),selection:s,source:structuredClone(source),method:structuredClone(DIFFERENTIAL_METHOD),input_manifest:pins(),status:'queued',revision:1,observation:'confirmed',created_at:'2026-11-19T01:00:00.000000Z',started_at:null,finished_at:null,attempt_id:null,runtime:null,result:null,error:null};}
function wire(){const calls=[];let respond=()=>{throw Error('Synthetic response not set');};
 const request=(options,callback)=>{const out=new EventEmitter(),call={options,out};calls.push(call);out.destroy=()=>{out.destroyed=true;};call.deliver=(value,status=200,headers=['Content-Type','application/json'])=>{const r=new EventEmitter();r.statusCode=status;r.rawHeaders=headers;r.headers={};r.destroy=()=>{r.destroyed=true;};call.response=r;callback(r);r.emit('data',Buffer.isBuffer(value)?value:Buffer.from(JSON.stringify(value)));r.emit('end');};out.end=body=>{call.body=body;queueMicrotask(()=>respond(call));};return out;};
 return{calls,request,reply:fn=>{respond=fn;}};
}
function service(t,enabled=true){const native=wire(),h=harness(t,{useServer:true,differentialInputEnabled:true,differentialInputRequest:native.request,differentialJobsEnabled:enabled,differentialJobsRequest:native.request});return{native,h};}
function api(native,source){const saved=new Map();let mode='normal',held=null;
 native.reply(c=>{const p=c.options.path;if(p===reviewPath)return c.deliver(review(JSON.parse(c.body)));const user=c.options.headers.Authorization==='Bearer ACCESS_B'?uid(222):owner;
  if(c.options.method==='POST'){
   const body=JSON.parse(c.body),old=saved.get(body.request_id);if(old&&JSON.stringify(old.body)!==JSON.stringify(body))return c.deliver({error:{code:'differential_request_conflict'}},409);
   if(mode==='reject')return c.deliver({...review(body,false),queued:false,request_id:body.request_id},422);
   const value=old?.value||queued(body,source,user);saved.set(body.request_id,{body,value,user});
   if(mode==='hold'){held=c;return;}if(mode==='unknown')return c.deliver({error:{code:'differential_submission_unknown'}},504);return c.deliver(value,old?200:201);
  }
  if(mode==='offline')return c.deliver({error:{code:'differential_unavailable'}},503);
  const requestId=new URL(p,'http://synthetic.invalid').searchParams.get('request_id'),record=requestId?saved.get(requestId):[...saved.values()].find(x=>p.endsWith('/'+x.value.job_id));
  return record&&record.user===user?c.deliver(record.value):c.deliver({error:{code:'differential_job_not_found'}},404);
 });return{saved,mode:value=>{mode=value;},held:()=>held};}
function browserFetch(h,id){return async(p,o)=>{const call=h.start(p,{method:o.method,cookie:id.cookie,view:new Headers(o.headers).get('X-Cyomics-Account-Context'),wire:o.body});
 const abort=()=>{call.req.aborted=true;call.req.emit('aborted');};o.signal.addEventListener('abort',abort,{once:true});
 try{const r=await call.promise;return new Response(r.wire||JSON.stringify({error:{code:'differential_unavailable'}}),{status:r.status||503,headers:r.headers['content-type']?r.headers:{'content-type':'application/json'}});}finally{o.signal.removeEventListener('abort',abort);}};}
async function uiReady(t,{mode='normal',storage=memory(),...options}={}){const svc=service(t),id=await svc.h.signed(),calls=[];const f=browserFetch(svc.h,id),fetch=(p,o)=>{calls.push({p,o});return f(p,o);};
 const ui=await setup(t,{storage,checkFetch:fetch,jobFetch:fetch,contextNonce:()=>id.view,...options});const server=api(svc.native,ui.bundles[0].v);server.mode(mode);await until(()=>ui.prep().view().differential_jobs.ready);
 ui.prepare();ui.pair();ui.confirm();return{...svc,id,ui,server,calls,storage,fetch};}
async function checkReady(ui){assert.equal(await ui.prep().checkInput(),true);assert.equal(ui.prep().view().can_submit_job,true);}
const admissions=native=>native.calls.filter(c=>c.options.path.includes('/differential-jobs'));

test('original SDK/session BFF forwards6-key POST and original-request/job GET;201 and idempotent200 expose queued only',async t=>{
 const {h,native}=service(t),id=await h.signed(),source=processed().v,server=api(native,source),body={...selection(source),request_id:uid(10)};
 const first=await h.send(path,{...id,method:'POST',value:body});assert.equal(first.status,201);assert.equal(first.body.status,'queued');assert.equal(first.body.revision,1);assert.equal(first.body.method.runtime_registered,false);assert.equal(first.body.method.publication_registered,false);assert.equal(first.body.result,null);
 const repeat=await h.send(path,{...id,method:'POST',value:body});assert.equal(repeat.status,200);assert.deepEqual(repeat.body,first.body);assert.equal(server.saved.size,1);
 const get=await h.send(path+'?request_id='+body.request_id,id),job=await h.send(path+'/'+first.body.job_id,id);assert.deepEqual(get.body,first.body);assert.deepEqual(job.body,first.body);
 for(const call of admissions(native)){assert.equal(call.options.hostname,'127.0.0.1');assert.equal(call.options.port,57430);assert.equal(call.options.agent,false);assert.equal(call.options.headers.Authorization,'Bearer ACCESS_A');assert.equal(call.options.headers.Cookie,undefined);assert.equal(call.options.headers['X-Cyomics-Account-Context'],undefined);}
 assert.deepEqual(JSON.parse(admissions(native)[0].body),body);assert.equal(h.auth.length,3);assert.equal(first.headers['cache-control'],'no-store');assert.doesNotMatch(first.wire,/ACCESS_|REFRESH_|cyomics_session/);
});
test('admission defaultoff, exact route/body limits,409 conflict,422 unmet and404/503 reads never claim queued or empty',async t=>{
 const off=service(t,false);assert.equal((await off.h.send(path,{method:'POST',value:{}})).status,503);assert.equal(off.h.auth.length,0);assert.equal(off.native.calls.length,0);
 const {h,native}=service(t),id=await h.signed(),source=processed().v,server=api(native,source),body={...selection(source),request_id:uid(11)};
 const bad=[{...body,owner_id:owner},{...body,request_id:'BAD'}];for(const value of bad)assert.equal((await h.send(path,{...id,method:'POST',value})).status,400);
 assert.equal((await h.send(path,{...id,method:'POST',wire:' '.repeat(8193)})).status,413);assert.equal((await h.send(path+'?request_id='+uid(1),{...id,method:'POST',value:body})).status,400);
 assert.equal((await h.send(path,id)).status,400);assert.equal((await h.send(path+'?request_id='+uid(1)+'&extra=1',id)).status,404);assert.equal(native.calls.length,0);
 assert.equal((await h.send(path,{...id,method:'POST',value:body})).status,201);assert.equal((await h.send(path,{...id,method:'POST',value:{...body,replicate_model:'paired'}})).status,409);
 server.mode('reject');const rejected=await h.send(path,{...id,method:'POST',value:{...body,request_id:uid(12)}});assert.equal(rejected.status,422);assert.equal(rejected.body.queued,false);assert.equal(rejected.body.model_input_satisfied,false);assert.equal(rejected.body.request_id,uid(12));assert.deepEqual(rejected.body.reasons,['insufficient_numerator_biological_replicates']);
 assert.equal((await h.send(path+'?request_id='+uid(999),id)).status,404);server.mode('offline');const offline=await h.send(path+'?request_id='+body.request_id,id);assert.equal(offline.status,503);assert.equal(offline.body.error.code,'differential_unavailable');assert.equal(offline.body.items,undefined);
});
test('malformed queued DTO, altered full selection/source/Owner digest or runtime registration cannot pass BFF',async t=>{
 const {h,native}=service(t),id=await h.signed(),source=processed().v;
 for(const mutate of [v=>{v.selection.contrast.contrasts[0].numerator_group='Other';},v=>{v.method.runtime_registered=true;},v=>{v.source.source.source_sha256='bad';},v=>{v.request_sha256='c'.repeat(64);},v=>{v.extra='statistics';},v=>{v.result={};},v=>{v.status='succeeded';}]){
  const body={...selection(source),request_id:uid(13)},value=queued(body,source);mutate(value);native.reply(c=>c.deliver(value,201));const r=await h.send(path,{...id,method:'POST',value:body});assert.equal(r.status,504);assert.equal(r.body.error.code,'differential_submission_unknown');
 }
 const good=queued({...selection(source),request_id:uid(13)},source);assert.deepEqual(differentialJobReply(differentialJobRoute(path,'POST'),201,good,{...selection(source),request_id:uid(13)}),good);
});
test('explicit checked input permits one UUID/POST; actual uploader shows queued and original GET recovers same task',async t=>{
 let generated=0;const cryptoImpl={subtle:webcrypto.subtle,randomUUID(){generated++;return webcrypto.randomUUID();}},h=await uiReady(t,{cryptoImpl});const {ui,native}=h;
 assert.equal(ui.d.by('data-ppo-action','submit-diff-job').disabled,true);await checkReady(ui);const before=generated;const submit=ui.d.by('data-ppo-action','submit-diff-job');submit.click();submit.click();await until(()=>ui.prep().view().differential_jobs.history.some(e=>e.phase==='queued'));
 const record=ui.prep().view().differential_jobs.history[0];assert.equal(generated,before+1);assert.equal(admissions(native).filter(c=>c.options.method==='POST').length,1);assert.match(ui.d.by('data-ppo-diff-job-state',record.request_id).textContent,/已排队，尚未计算/);assert.equal(ui.d.by('data-ppo-action','submit-diff-job').disabled,true);assert.equal(ui.d.by('data-ppo-artifact','working-matrix.tsv').disabled,false);
 assert.equal(await ui.prep().submitJob(),false);assert.equal(await ui.prep().checkJob(record.request_id),true);assert.equal(generated,before+1);assert.equal(admissions(native).at(-1).options.path,path+'?request_id='+record.request_id);assert.equal(ui.prep().view().differential_jobs.history[0].job_id,record.job_id);
 const browser=h.calls.find(c=>c.p===path);assert.equal(browser.o.credentials,'same-origin');assert.equal(new Headers(browser.o.headers).get('Authorization'),null);assert.equal(Object.keys(JSON.parse(browser.o.body)).length,6);
});
test('unknown submission retains original ID across remount; only explicit GET restores, no UUID rotation/rePOST/poll',async t=>{
 let generated=0;const cryptoImpl={subtle:webcrypto.subtle,randomUUID(){generated++;return webcrypto.randomUUID();}},h=await uiReady(t,{mode:'unknown',cryptoImpl});await checkReady(h.ui);const before=generated;assert.equal(await h.ui.prep().submitJob(),false);const ref=h.ui.prep().view().differential_jobs.history[0];assert.equal(ref.phase,'unknown');assert.equal(generated,before+1);assert.equal(await h.ui.prep().submitJob(),false);const postCount=admissions(h.native).length;
 h.ui.stop();h.ui.model.dispose();const restored=await setup(t,{storage:h.storage,cryptoImpl,jobFetch:h.fetch,checkFetch:h.fetch,contextNonce:()=>h.id.view});await until(()=>restored.prep().view().differential_jobs.ready);const stored=restored.prep().view().differential_jobs.history[0];assert.equal(stored.request_id,ref.request_id);assert.equal(stored.phase,'needs_check');assert.equal(admissions(h.native).length,postCount);
 h.server.mode('normal');assert.equal(await restored.prep().checkJob(ref.request_id),true);const recovered=restored.prep().view().differential_jobs.history[0];assert.equal(recovered.job_id,h.server.saved.get(ref.request_id).value.job_id);assert.equal(admissions(h.native).filter(c=>c.options.method==='POST').length,1);assert.equal(admissions(h.native).filter(c=>c.options.method==='GET').length,1);assert.equal(generated,before+1);assert.doesNotMatch(JSON.stringify([...h.storage.values.values()]),/ACCESS_|REFRESH_|cyomics_session/);
});
test('422 replaces the checked-ready conclusion with unmet reasons and preserves a nonqueued request record',async t=>{
 const h=await uiReady(t,{mode:'reject'});await checkReady(h.ui);assert.equal(await h.ui.prep().submitJob(),false);const state=h.ui.prep().view(),r=state.differential_jobs.history[0];assert.equal(r.disposition,'rejected');assert.equal(r.job_id,null);assert.equal(state.input_review.result.model_input_satisfied,false);assert.equal(state.can_submit_job,false);assert.equal(h.server.saved.size,0);assert.match(h.ui.d.by('data-ppo-diff-job-state',r.request_id).textContent,/未排队/);assert.equal(h.ui.d.by('data-ppo-artifact','working-matrix.tsv').disabled,false);
});
test('selection change during admission aborts old usable result but retains original-ID history for an explicit read',async t=>{
 const h=await uiReady(t,{mode:'hold'});await checkReady(h.ui);const chosen=h.ui.selection(),pending=h.ui.prep().submitJob();await until(()=>!!h.server.held());h.ui.contrast().editor().updateContrast(chosen.contrast.contrasts[0].contrast_id,{numerator_group:'Control',denominator_group:'Treatment'});
 assert.equal(await pending,false);assert.equal(h.ui.selection(),null);const old=h.ui.prep().view().differential_jobs.history[0];assert.equal(old.phase,'unknown');assert.equal(old.selection.contrast.contrasts[0].numerator_group,'Treatment');assert.equal(h.ui.prep().view().can_submit_job,false);
 h.server.held().deliver(h.server.saved.get(old.request_id).value,201);await flush();assert.equal(h.ui.prep().view().differential_jobs.history[0].phase,'unknown');h.server.mode('normal');assert.equal(await h.ui.prep().checkJob(old.request_id),true);assert.equal(h.ui.prep().view().differential_jobs.history[0].phase,'queued');assert.equal(h.ui.prep().view().can_submit_job,false);assert.equal(admissions(h.native).filter(c=>c.options.method==='POST').length,1);
});
test('503 or404 original-request check keeps the request and blocks rePOST; session loss fences late queued responses',async t=>{
 const h=await uiReady(t,{mode:'unknown'});await checkReady(h.ui);await h.ui.prep().submitJob();const id=h.ui.prep().view().differential_jobs.history[0].request_id;h.server.mode('offline');assert.equal(await h.ui.prep().checkJob(id),false);assert.equal(h.ui.prep().view().differential_jobs.history.length,1);h.server.mode('normal');h.server.saved.clear();assert.equal(await h.ui.prep().checkJob(id),false);assert.equal(h.ui.prep().view().differential_jobs.history[0].request_id,id);assert.equal(await h.ui.prep().submitJob(),false);assert.equal(admissions(h.native).filter(c=>c.options.method==='POST').length,1);assert.match(h.ui.d.by('data-ppo-diff-job-state',id).textContent,/仍保留原 ID/);
 const direct=service(t),who=await direct.h.signed(),source=processed().v;let held;direct.native.reply(c=>{held=c;});const active=direct.h.start(path,{...who,method:'POST',value:{...selection(source),request_id:uid(14)}});await until(()=>held?.body);await direct.h.send('/api/auth/logout',{cookie:who.cookie,value:{}});const denied=await active.promise;assert.notEqual(denied.status,201);assert.notEqual(denied.status,200);const writes=active.res.writes.length;held.deliver(queued({...selection(source),request_id:uid(14)},source),201);await flush();assert.equal(active.res.writes.length,writes);
});
test('local recovery failure sends no POST and a different original account nonce cannot display the saved request',async t=>{
 const broken={getItem(){return null;},setItem(){throw Error('synthetic storage unavailable');},removeItem(){}};const h=await uiReady(t,{storage:broken});await checkReady(h.ui);assert.equal(await h.ui.prep().submitJob(),false);assert.equal(admissions(h.native).length,0);assert.equal(h.ui.prep().view().differential_jobs.code,'differential_recovery_unavailable');
 const a=await uiReady(t,{mode:'unknown'});await checkReady(a.ui);await a.ui.prep().submitJob();assert.equal(a.ui.prep().view().differential_jobs.history.length,1);a.ui.stop();a.ui.model.dispose();
 const other=await a.h.signed('B'),otherFetch=browserFetch(a.h,other),b=await setup(t,{storage:a.storage,jobFetch:otherFetch,checkFetch:otherFetch,contextNonce:()=>other.view});await until(()=>b.prep().view().differential_jobs.ready);assert.deepEqual(b.prep().view().differential_jobs.history,[]);assert.equal(admissions(a.native).length,1);
});
