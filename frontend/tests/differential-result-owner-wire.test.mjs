// New read/mount/download slice only; all transports/DTOs are explicit synthetic effects.
import test from 'node:test';
import assert from 'node:assert/strict';
import {setup,path,uid} from './support/differential-result-composition.mjs';
import {until,flush} from './support/preprocess-owner-setup.mjs';
import {parseDifferentialRowMap} from '../differential-row-map.mjs';
import {DIFFERENTIAL_RESULT_NAMES,differentialResultRoute} from '../differential-result-contract.mjs';
const tableRows=d=>d.all().filter(n=>Object.hasOwn(n.attrs,'data-lrt-source-row'));
const row=(d,n)=>tableRows(d).find(r=>r.attrs['data-lrt-source-row']===String(n));
const cell=(d,n,k)=>row(d,n)?.children.find(c=>c.attrs['data-lrt-cell']===k)?.textContent;
const resultCalls=h=>h.native.calls.filter(c=>/differential-(?:runs|results)/.test(c.options.path));
test('original SDK/Auth BFF + uploader history reads exact frozen job/run/meta/rowmap/five artifacts and mounts original table',async t=>{
 const h=await setup(t),before=h.native.calls.length;assert.equal(h.save.clicks.length,0);const state=await h.view();assert.equal(state.phase,'ready');await until(()=>tableRows(h.d).length===7);
 const item=h.api.saved.get(h.requestId);assert.equal(item.job.status,'queued');assert.match(h.d.root.textContent,/任务请求已保存，执行状态请查看下方记录/);assert.ok(!h.d.root.textContent.includes('已排队，尚未计算'));
 assert.equal(cell(h.d,2,'protein_ids_raw'),'  SYN_"DOWN"; B  ');assert.equal(cell(h.d,1,'log2FC'),'-3');assert.equal(cell(h.d,5,'P.Value'),'不可检验（NA）');assert.equal(cell(h.d,9,'protein_ids_raw'),'SYN_MULTI_A;SYN_MULTI_B');assert.equal(tableRows(h.d).filter(r=>r.textContent.includes('SYN_DUP')).length,2);
 assert.equal(h.d.by('data-lrt-provenance','synthetic_engineering_verification')?.textContent.includes('不是用户研究结果'),true);assert.match(h.d.by('data-lrt-field','direction').textContent,/Numerator original β − Denominator original α/);
 const calls=h.native.calls.slice(before);assert.deepEqual(calls.map(c=>c.options.path),[path+'/differential-jobs?request_id='+h.requestId,path+'/differential-runs/'+item.job.job_id,path+'/differential-results?job_id='+item.job.job_id,'/api/v1/processed-dataset-versions/'+item.job.selection.processed_dataset_version_id+'/artifacts/row-map.tsv?project_id='+item.job.project_id,...DIFFERENTIAL_RESULT_NAMES.map(n=>path+'/differential-results/'+item.meta.result_id+'/artifacts/'+n)]);
 for(const c of calls){assert.equal(c.options.method,'GET');assert.equal(c.options.hostname,'127.0.0.1');assert.equal(c.options.port,57430);assert.equal(c.options.agent,false);assert.equal(c.options.headers.Authorization,'Bearer ACCESS_A');assert.equal(c.options.headers.Cookie,undefined);assert.equal(c.options.headers['X-Cyomics-Account-Context'],undefined);}
 assert.equal(h.save.clicks.length,0,'authorized rowmap/table reads never save a file');assert.equal(h.h.auth.length,3);const dataCalls=h.backend.calls.filter(c=>/differential-results|row-map.tsv/.test(c.path));assert.ok(dataCalls.every(c=>new Headers(c.options.headers).get('Authorization')===null));
});
test('five table buttons fresh-read original job/run/result ID and authorized exact bytes before genuine browser handoff',async t=>{
 const h=await setup(t);await h.view();await until(()=>tableRows(h.d).length===7);const item=h.api.saved.get(h.requestId);
 for(const name of DIFFERENTIAL_RESULT_NAMES){const before=h.native.calls.length;h.d.by('data-lrt-download',name).click();await until(()=>h.save.clicks.length>0&&h.save.clicks.at(-1).name.endsWith('_'+name));const clicked=h.save.clicks.at(-1),blob=h.save.active.get(clicked.url);assert.ok(blob);assert.deepEqual(new Uint8Array(await blob.arrayBuffer()),item.artifacts.get(name));assert.deepEqual(h.native.calls.slice(before).map(c=>c.options.path),[path+'/differential-jobs?request_id='+h.requestId,path+'/differential-runs/'+item.job.job_id,path+'/differential-results/'+item.meta.result_id,path+'/differential-results/'+item.meta.result_id+'/artifacts/'+name]);h.timers.advance(1001);await flush();assert.equal(h.scope.busy(),false);}
 assert.equal(h.save.clicks.length,5);assert.match(h.d.root.textContent,/请查看浏览器的下载状态/);assert.equal(h.save.active.size,0);assert.ok(h.save.anchors.every(a=>a.href===''));assert.equal(h.native.calls.filter(c=>c.options.method==='POST'&&c.options.path.includes('/differential-jobs')).length,1,'view/download never re-submit');
});
test('three scientific states remain distinct, conservative saved-input mode never defaults to research',async t=>{
 for(const scientific of ['COMPUTED_CANDIDATE','NO_TESTABLE_ROWS','REVIEW_REQUIRED_LIMMA_WARNING']){const h=await setup(t,{scientific});await h.view();await until(()=>tableRows(h.d).length===7);assert.equal(h.owner().view().meta.scientific_status,scientific);const text=h.d.by('data-ppo-diff-result-state','status').textContent;
  if(scientific==='NO_TESTABLE_ROWS'){assert.match(text,/没有可检验行/);assert.ok(tableRows(h.d).every(r=>r.textContent.includes('not_testable')));assert.equal(cell(h.d,1,'P.Value'),'不可检验（NA）');}
  else if(scientific==='REVIEW_REQUIRED_LIMMA_WARNING')assert.match(text,/警告/);else assert.match(text,/已核对保存的计算结果/);
 }
 const conservative=await setup(t,{provenance:'owner_verified_saved_input'});await conservative.view();await until(()=>tableRows(conservative.d).length===7);assert.ok(conservative.d.by('data-lrt-provenance','owner_verified_saved_input'));assert.match(conservative.d.root.textContent,/基于已保存输入的计算结果/);assert.equal(conservative.d.by('data-lrt-provenance','research'),undefined);
});
test('disabled, absent run/result, running, failed and unconfirmed records never become empty/successful result tables or poll',async t=>{
 for(const mode of ['no-run','no-result','running','failed','reconcile','off']){const h=await setup(t,{mode}),before=h.native.calls.length;const view=await h.view();assert.equal(view.phase,({ 'no-run':'not_started','no-result':'no_result',running:'running',failed:'failed',reconcile:'reconciliation',off:'unavailable'})[mode]);assert.equal(tableRows(h.d).length,0);assert.equal(h.save.clicks.length,0);const count=h.native.calls.length;await flush();assert.equal(h.native.calls.length,count);assert.ok(count-before<=3);}
 const h=await setup(t,{resultsEnabled:false});const before=h.native.calls.length;assert.equal((await h.view()).phase,'unavailable');assert.equal(resultCalls(h).length,0,'default-off BFF never reaches read backend');assert.equal(h.native.calls.length-before,1,'only existing queued check occurs');
});
test('Owner/session and source/selection/attempt/result/manifest bindings fail closed; no results-derived identity fallback',async t=>{
 for(const mutate of [(kind,v)=>{if(kind==='run')v.attempt_id=uid(950);},(kind,v)=>{if(kind==='meta')v.admission_binding_sha256='d'.repeat(64);},(kind,v)=>{if(kind==='meta')v.selection.sample_design_request_id=uid(951);},(kind,v)=>{if(kind==='meta')v.result_id=uid(952);},(kind,v)=>{if(kind==='meta')v.manifest_sha256='d'.repeat(64);},(kind,v)=>{if(kind==='meta'){v.exit_code=3;v.scientific_status='NO_TESTABLE_ROWS';v.review_required=false;}}]){
  const h=await setup(t);h.api.mutate(mutate);assert.equal((await h.view()).phase,'unavailable');assert.equal(tableRows(h.d).length,0);assert.equal(h.save.clicks.length,0);
 }
 const lineage=await setup(t,{wrongLineage:true});await lineage.view();await until(()=>lineage.owner().view().phase==='unavailable');assert.equal(tableRows(lineage.d).length,0,'results.tsv cannot certify its own raw IDs when original rowmap disagrees');
 const denied=await setup(t);denied.api.mutate((kind,v)=>{if(kind==='meta')v.created_by=uid(953);});assert.equal((await denied.view()).phase,'unavailable');assert.equal(tableRows(denied.d).length,0);
});
test('corrupt/oversize artifact bytes are rejected before mount/handoff; changing original draft clears old table but historical frozen selection can be viewed',async t=>{
 for(const mode of ['corrupt','oversize']){const corrupt=await setup(t,{mode});assert.equal((await corrupt.view()).phase,'unavailable');assert.equal(tableRows(corrupt.d).length,0);assert.equal(corrupt.save.clicks.length,0);}
 const h=await setup(t);await h.view();await until(()=>tableRows(h.d).length===7);const old=h.api.saved.get(h.requestId).job.selection;h.model.comparisonDesign(h.f.file.file_id).editor().updateContrast(old.contrast.contrasts[0].contrast_id,{numerator_group:'Denominator original α',denominator_group:'Numerator original β'});assert.equal(tableRows(h.d).length,0);assert.equal(h.owner().view().meta,null);
 await h.owner().select(h.requestId);await until(()=>tableRows(h.d).length===7);assert.match(h.d.by('data-lrt-field','direction').textContent,/Numerator original β − Denominator original α/);assert.equal(h.api.saved.get(h.requestId).job.status,'queued');
});
test('context loss/unmount aborts late result reads and downloads and cannot restore an old table or Blob',async t=>{
 const h=await setup(t,{mode:'hold'}),pending=h.owner().select(h.requestId);await until(()=>!!h.api.held());const old=h.api.held();h.owner().clear();assert.equal(await pending,false);old.deliver(h.api.saved.get(h.requestId).run);await flush();assert.equal(tableRows(h.d).length,0);assert.equal(h.owner().view().meta,null);
 h.api.mode('normal');await h.owner().select(h.requestId);await until(()=>tableRows(h.d).length===7);h.api.mode('hold');h.d.by('data-lrt-download','results.tsv').click();await until(()=>h.api.held()!==old);const second=h.api.held();h.stop();h.model.dispose();second.deliver(h.api.saved.get(h.requestId).run);await flush();assert.equal(h.save.clicks.length,0);assert.equal(h.save.active.size,0);assert.equal(tableRows(h.d).length,0);
});
test('read routes are GET-only/exact/five-name bounded; quoted and plain rowmap numerals preserve raw IDs/ordering',async t=>{
 const h=await setup(t),j=h.api.saved.get(h.requestId).job,r=h.api.saved.get(h.requestId).meta;const run=path+'/differential-runs/'+j.job_id;
 assert.throws(()=>differentialResultRoute(run,'POST'));assert.throws(()=>differentialResultRoute(run+'?anything=1','GET'));assert.throws(()=>differentialResultRoute(path+'/differential-results/'+r.result_id+'/artifacts/receipt.json','GET'));assert.throws(()=>differentialResultRoute(path+'/differential-results?job_id='+j.job_id+'&x=1','GET'));
 const read=await h.h.send(run,{cookie:h.identity.cookie,view:'Z'.repeat(22)});assert.equal(read.status,409);const no=await h.h.send(run);assert.equal(no.status,401);
 const rows=parseDifferentialRowMap(h.b.rowMap,h.b.v);assert.equal(rows.length,7);assert.equal(rows[1].protein_ids_raw,'  SYN_"DOWN"; B  ');assert.deepEqual(rows.map(r=>r.source_row_ordinal),[1,2,4,5,8,9,12]);const bad=new TextEncoder().encode(new TextDecoder().decode(h.b.rowMap).replace('TRUE\t1','TRUE\t99'));assert.throws(()=>parseDifferentialRowMap(bad,h.b.v));
});
test('switching saved tasks and original-session logout fence an in-flight run read without restoring stale data',async t=>{
 const h=await setup(t,{mode:'hold'}),a=h.requestId,pending=h.owner().select(a);await until(()=>!!h.api.held());const held=h.api.held();
 assert.equal(await h.prep().checkInput(),true);assert.equal(await h.prep().submitJob(),true);const b=h.prep().view().differential_jobs.history.find(e=>e.request_id!==a).request_id;
 h.api.mode('normal');const next=h.owner().select(b);assert.equal(await pending,false);assert.equal(await next,true);await until(()=>tableRows(h.d).length===7);const saved=h.api.saved.get(b);assert.equal(h.owner().view().meta.result_id,saved.meta.result_id);assert.ok(h.d.by('data-lrt-field','job').textContent.includes(saved.job.job_id));
 held.deliver(h.api.saved.get(a).run);await flush();assert.equal(h.owner().view().meta.result_id,saved.meta.result_id);assert.equal(h.save.clicks.length,0);
 const lost=await setup(t,{mode:'hold'}),read=lost.owner().select(lost.requestId);await until(()=>!!lost.api.held());const late=lost.api.held(),owner=lost.owner();await lost.h.send('/api/auth/logout',{cookie:lost.identity.cookie,value:{}});assert.equal(await read,false);assert.equal(tableRows(lost.d).length,0);late.deliver(lost.api.saved.get(lost.requestId).run);await flush();assert.equal(tableRows(lost.d).length,0);assert.equal(owner.view().meta,null);assert.equal(lost.save.clicks.length,0);
});
