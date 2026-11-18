// Limited new contract/session/UI wiring cases. Real SDK, synthetic transports only.
import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {harness} from './support/raw-qc-bff-session.mjs';
import {setup} from './support/differential-input-composition.mjs';
import {until,flush,deferred} from './support/preprocess-owner-setup.mjs';
import {projectId,first} from './support/contrast-mount-setup.mjs';
import {differentialInputReply,differentialInputRoute,canonicalJSON} from '../differential-input-contract.mjs';
const uuid=n=>String(n).padStart(8,'0')+'-1111-4111-8111-111111111111';
const path='/api/v1/projects/'+projectId+'/differential-input';
const selected=()=>({processed_dataset_version_id:uuid(1),sample_design_version_id:uuid(2),sample_design_request_id:uuid(3),
 contrast:{schemaVersion:1,dataset_version_id:uuid(4),sample_design_version_id:uuid(2),quant_family:'Intensity',contrasts:[{contrast_id:uuid(5),numerator_group:'Treatment',denominator_group:'Control'}]},replicate_model:'independent_biological'});
function result(selection,satisfied=false){return{schemaVersion:1,contract:'differential_input_review_v0.1',project_id:projectId,
 ...Object.fromEntries(['processed_dataset_version_id','sample_design_version_id','sample_design_request_id'].map(k=>[k,selection[k]])),
 model_input_satisfied:satisfied,reasons:satisfied?[]:['insufficient_numerator_biological_replicates','insufficient_denominator_biological_replicates'],source_analysis_ready:false,
 input_summary:satisfied?{row_count:2,sample_count:4,design_rank:2,contrast:structuredClone(selection.contrast.contrasts[0]),input_pins:Object.fromEntries(['matrix','design','preset'].map(k=>[k,{bytes:100,sha256:'a'.repeat(64)}]))}:null,calculation_started:false};}
function wire(){const calls=[];let reply=call=>call.deliver(result(JSON.parse(call.body)));
 const request=(options,callback)=>{const out=new EventEmitter(),call={options,out};calls.push(call);out.destroy=()=>{out.destroyed=true;};
 call.deliver=(value,status=200,rawHeaders=['Content-Type','application/json'])=>{const r=new EventEmitter();r.statusCode=status;r.rawHeaders=rawHeaders;r.headers={};r.destroy=()=>{r.destroyed=true;};call.response=r;callback(r);r.emit('data',Buffer.isBuffer(value)?value:Buffer.from(JSON.stringify(value)));r.emit('end');};
 out.end=body=>{call.body=body;queueMicrotask(()=>reply(call));};return out;};return{calls,request,reply:fn=>{reply=fn;}};}
function service(t,enabled=true){const native=wire(),h=harness(t,{useServer:true,differentialInputEnabled:enabled,differentialInputRequest:native.request});return{native,h};}
function browserFetch(h,identity){return async(p,o)=>{const active=h.start(p,{method:o.method,cookie:identity.cookie,view:new Headers(o.headers).get('X-Cyomics-Account-Context'),wire:o.body});
 const abort=()=>{active.req.aborted=true;active.req.emit('aborted');};o.signal.addEventListener('abort',abort,{once:true});
 try{const r=await active.promise;return new Response(r.wire||'',{status:r.status||503,headers:r.headers});}finally{o.signal.removeEventListener('abort',abort);}};}
test('existing server factory + original SDK login/session forwards exact Selection to only fixed API; unmet stays200',async t=>{
 const {h,native}=service(t),id=await h.signed(),s=selected();const r=await h.send(path,{...id,method:'POST',value:s});assert.equal(r.status,200);assert.deepEqual(r.body,result(s));assert.equal(h.auth.length,3);
 assert.equal(native.calls.length,1);const c=native.calls[0];assert.deepEqual(JSON.parse(c.body),s);assert.equal(c.options.hostname,'127.0.0.1');assert.equal(c.options.port,57430);assert.equal(c.options.path,path);assert.equal(c.options.method,'POST');assert.equal(c.options.agent,false);
 assert.equal(c.options.headers.Authorization,'Bearer ACCESS_A');assert.equal(c.options.headers.Origin,'http://127.0.0.1:4206');assert.equal(c.options.headers['X-Cyomics-Request'],'1');assert.equal(c.options.headers.Cookie,undefined);assert.equal(c.options.headers['X-Cyomics-Account-Context'],undefined);assert.ok(Number(c.options.headers['X-Cyomics-Remaining-Ms'])<=12000);
 assert.equal(r.headers['cache-control'],'no-store');assert.equal(r.headers['x-content-type-options'],'nosniff');assert.doesNotMatch(r.wire,/ACCESS_|REFRESH_|cyomics_session/);
});
test('default off precedes Auth/I/O; enabled missing session/context, malformed route and exact 8KiB DTO fail closed',async t=>{
 const off=service(t,false);const r=await off.h.send(path,{method:'POST',value:selected()});assert.equal(r.status,503);assert.equal(r.body.error.code,'differential_input_unavailable');assert.equal(off.native.calls.length,0);assert.equal(off.h.auth.length,0);
 const {h,native}=service(t),id=await h.signed();assert.equal((await h.send(path,{method:'POST',value:selected()})).status,401);assert.equal((await h.send(path,{cookie:id.cookie,view:'Z'.repeat(22),method:'POST',value:selected()})).status,409);
 for(const p of [path+'?x=1',path+'/',path.replace('differential','%64ifferential')])assert.equal((await h.send(p,{...id,method:'POST',value:selected()})).status,404);
 assert.equal((await h.send(path,{...id,method:'GET'})).status,405);
 for(const value of [{...selected(),owner_id:uuid(9)},{...selected(),request_id:uuid(9)},{...selected(),sample_design_request_id:'bad'}])assert.equal((await h.send(path,{...id,method:'POST',value})).status,400);
 assert.equal((await h.send(path,{...id,method:'POST',wire:'{"processed_dataset_version_id":"'+uuid(1)+'","processed_dataset_version_id":"'+uuid(1)+'"}'})).status,400);
 assert.equal((await h.send(path,{...id,method:'POST',wire:' '.repeat(8193)})).status,413);assert.equal(native.calls.length,0);
});
test('fixed error/DTO response validation preserves404/401, normalizes every503 and rejects mismatched/unknown200',async t=>{
 const {h,native}=service(t),id=await h.signed(),s=selected();
 for(const code of ['quant_diff_input_source_mismatch','quant_diff_input_persisted_invalid','unrecognized_internal_fault']){native.reply(c=>c.deliver({error:{code}},503));const r=await h.send(path,{...id,method:'POST',value:s});assert.equal(r.status,503);assert.equal(r.body.error.code,'differential_input_unavailable');}
 native.reply(c=>c.deliver({error:{code:'sample_design_not_found'}},404));assert.equal((await h.send(path,{...id,method:'POST',value:s})).status,404);
 for(const change of [v=>{v.sample_design_request_id=uuid(8);},v=>{v.reasons=['arbitrary user message'];},v=>{v.calculation_started=true;},v=>{v.extra='matrix';}]){const v=result(s);change(v);native.reply(c=>c.deliver(v));assert.equal((await h.send(path,{...id,method:'POST',value:s})).status,503);}
 native.reply(c=>c.deliver({error:{code:'unauthenticated'}},401));const denied=await h.send(path,{...id,method:'POST',value:s});assert.equal(denied.status,401);const n=native.calls.length;assert.equal((await h.send(path,{...id,method:'POST',value:s})).status,401);assert.equal(native.calls.length,n);
});
test('satisfied schema retains source_analysis_ready:false, single original contrast and three pins; no analysis statistics',()=>{
 const s=selected(),r=result(s,true),route=differentialInputRoute(path,'POST');assert.deepEqual(differentialInputReply(route,200,r,s),r);
 for(const change of [v=>{v.input_summary.contrast.numerator_group='Control';},v=>{v.input_summary.design_rank=1;},v=>{v.input_summary.input_pins.matrix.sha256='bad';},v=>{v.input_summary.contrast=s.contrast;},v=>{v.source_analysis_ready=true;},v=>{v.logFC=2;}]){const v=structuredClone(r);change(v);assert.throws(()=>differentialInputReply(route,200,v,s));}
});
test('read-only POST timeout or lost account after send never becomes submission_unknown or delivers old200',async t=>{
 for(const mode of ['timeout','logout']){const {h,native}=service(t),id=await h.signed();native.reply(()=>{});const active=h.start(path,{...id,method:'POST',value:selected()});await until(()=>native.calls.length===1&&native.calls[0].body);
 if(mode==='timeout'){h.advance(13000);h.expire();}else await h.send('/api/auth/logout',{cookie:id.cookie,value:{}});
 const r=await active.promise;assert.notEqual(r.status,200);assert.ok(active.res.destroyed||[401,409,503].includes(r.status));assert.doesNotMatch(JSON.stringify(r.body??{}),/submission_unknown|result_unknown|preprocessing_/);const writes=active.res.writes.length;native.calls[0].deliver(result(selected()));await flush();assert.equal(active.res.writes.length,writes);
 }
});
test('actual uploader button through original Auth BFF displays unmet reasons; getter has no I/O and downloads stay enabled',async t=>{
 const {h,native}=service(t),identity=await h.signed();const ui=await setup(t,{checkFetch:browserFetch(h,identity),contextNonce:()=>identity.view});ui.prepare();ui.pair();assert.equal(ui.d.by('data-ppo-action','check-diff-input').disabled,true);ui.confirm();const s=ui.selection(),n=ui.b.calls.length;
 for(let i=0;i<5;i++)assert.equal(canonicalJSON(ui.selection()),canonicalJSON(s));assert.equal(ui.b.calls.length,n);ui.d.by('data-ppo-action','check-diff-input').click();await until(()=>ui.prep().view().input_review?.phase==='reviewed');
 assert.equal(native.calls.length,1);assert.deepEqual(JSON.parse(native.calls[0].body),s);assert.equal(ui.prep().view().input_review.result.model_input_satisfied,false);assert.match(ui.d.by('data-ppo-diff-field','input-review').textContent,/模型输入尚不满足，尚未运行差异分析/);assert.match(ui.d.root.textContent,/至少需要两个独立生物学重复/);assert.equal(ui.d.by('data-ppo-artifact','working-matrix.tsv').disabled,false);
 const cid=s.contrast.contrasts[0].contrast_id;ui.contrast().editor().updateContrast(cid,{numerator_group:'Control',denominator_group:'Treatment'});assert.equal(ui.prep().view().input_review.result,null);assert.equal(ui.selection(),null);
});
test('late browser result cannot survive contrast/sample/processed/source/account/unknown-save changes',async t=>{
 for(const mode of ['contrast','sample','processed','source','account','unknown-save']){
  const held=deferred();let signal;const ui=await setup(t,{twoVersions:true,checkFetch:(p,o)=>{signal=o.signal;return held.promise;}});ui.prepare();ui.pair();ui.confirm();const prep=ui.prep(),selection=ui.selection(),pending=prep.checkInput();await until(()=>!!signal);
  if(mode==='contrast')ui.contrast().editor().updateContrast(selection.contrast.contrasts[0].contrast_id,{numerator_group:'Control',denominator_group:'Treatment'});
  if(mode==='sample')ui.host().editor().update(ui.host().editor().view().rows[0].column_ordinal,{condition:'changed while checking'});
  if(mode==='processed')ui.d.by('data-ppo-job',ui.bundles[1].j.job_id).click();
  if(mode==='source'){const report=ui.model.importReport(first.file.file_id),original=report.validatedSource;report.validatedSource=()=>structuredClone(original());ui.selection();}
  if(mode==='account'){ui.b.lose();ui.selection();}
  if(mode==='unknown-save'){ui.b.setIntercept((p,o)=>p.includes('/sample-designs?')&&o.method==='POST'?Response.json({error:{code:'sample_design_result_unknown'}},{status:504}):undefined);await ui.host().save();}
  held.resolve(Response.json(result(selection)));assert.equal(await pending,false);assert.equal(ui.selection(),null);assert.equal(prep.view().input_review?.result??null,null);assert.equal(signal.aborted,true);
 }
});
test('browser404 clears checked selection,503 stays unconfirmed, and401 uses original uploader denial path',async t=>{
 for(const status of [404,503,401]){let denied=0;const ui=await setup(t,{onDenied:()=>{denied++;},checkFetch:()=>Response.json({error:{code:status===404?'sample_design_not_found':status===401?'unauthenticated':'differential_input_unavailable'}},{status})});ui.prepare();ui.pair();ui.confirm();const prep=ui.prep();assert.equal(await prep.checkInput(),false);assert.equal(prep.view().input_review?.result??null,null);
  if(status===404){assert.equal(ui.selection(),null);assert.match(ui.d.root.textContent,/所选输入已不存在/);}
  if(status===503){assert.ok(ui.selection());assert.equal(prep.view().input_review.phase,'unavailable');assert.equal(ui.d.by('data-ppo-artifact','working-matrix.tsv').disabled,false);}
  if(status===401){assert.equal(denied,1);assert.equal(ui.selection(),null);}
 }
});
