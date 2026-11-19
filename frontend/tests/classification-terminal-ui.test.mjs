// Test-only fixture/helper prefix copied without old case registration. Synthetic data, not actual R or research results.
// One new capacity-retry composition only. Synthetic SDK/Auth/BFF/native data; no actual backend/R or old suites. Synthetic BFF/native wire + verified-job and original row-map adapters are mocked; no real Auth/backend/R/Owner HTTP. Original suites are not imported or run.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash,webcrypto} from 'node:crypto';
import {dom,deferred,flush} from './support/preprocess-parameters-dom.mjs';
import {DIFFERENTIAL_METHOD} from '../differential-job-contract.mjs';
import {DIFFERENTIAL_RESULT_NAMES} from '../differential-result-contract.mjs';
import {canonicalJSON} from '../sample-design-contract.mjs';
import {mountDEClassificationSummary,DE_CLASSIFICATION_ARTIFACTS} from '../de-classification-summary.js';
const enc=new TextEncoder(),dec=new TextDecoder(),id=n=>[1,11].includes(n)?'11111111-1111-4111-8111-111111111111':String(n).padStart(8,'0')+'-1111-4111-8111-111111111111';
const digest=bytes=>createHash('sha256').update(bytes).digest('hex'),hash=v=>digest(enc.encode(canonicalJSON(v))),pin=a=>({name:a.name,bytes:a.bytes,sha256:a.sha256});
const pack=(name,value)=>{const body=typeof value==='string'?enc.encode(value):new Uint8Array(value);return{name,body,bytes:body.byteLength,sha256:digest(body)};};
const fixture=new URL('./fixtures/limma-results-native-synthetic/',import.meta.url);
const original=await Promise.all(DIFFERENTIAL_RESULT_NAMES.map(async name=>pack(name,await readFile(new URL(name,fixture)))));
const matrix=body=>dec.decode(body).split('\n').slice(0,-1).map(s=>s.split('\t'));
const tsv=rows=>rows.map(r=>r.join('\t')).join('\n')+'\n';
const paramsKeys=['schema_version','input_scale','adjusted_p_max','absolute_log2fc_min'];
function input({mode='members',onDownload,withMemberSet=true}={}){
 const parentFiles=structuredClone(original),get=name=>parentFiles.find(a=>a.name===name),replace=(name,value)=>{parentFiles[parentFiles.findIndex(a=>a.name===name)]=pack(name,value);};
 const result=matrix(get('results.tsv').body),method=matrix(get('method-audit.tsv').body),audit=Object.fromEntries(method.slice(1));
 // Literal IDs deliberately retain duplicates, leading/trailing spaces and blank IDs.
 result[1][1]=result[5][1]='  SYN_DUP  ';result[2][1]='';result[6][1]='  SYN_MULTI_A;SYN_MULTI_B  ';
 if(mode==='no-testable')for(const row of result.slice(1)){row[3]='not_testable';row[4]='insufficient_numerator_observations';for(const i of [5,6,8,9,10,11,12,13])row[i]='NA';row[7]='not_testable';row[16]=row[17]='0';}
 audit.output_status=mode==='warning'?'REVIEW_REQUIRED_LIMMA_WARNING':mode==='no-testable'?'NO_TESTABLE_ROWS':'COMPUTED_CANDIDATE';
 audit.warning_count=mode==='warning'?'1':'0';audit.warning_codes=mode==='warning'?'limma_warning':'';
 if(mode==='no-testable')audit.bh_m=audit.bh_n=audit.eligible_before_fit='0';
 replace('results.tsv',tsv(result));replace('method-audit.tsv',tsv(method.map((r,i)=>i?[r[0],audit[r[0]]]:r)));
 replace('test-set.tsv',tsv([['source_row_ordinal','included_in_BH','P.Value','test_status','reason'],...result.slice(1).map(r=>[r[0],r[3]==='testable'?'TRUE':'FALSE',r[12],r[3],r[4]])]));
 const context={project_id:id(1),job_id:id(2),binding_sha256:'b'.repeat(64),provenance:'synthetic_engineering_verification',source:{file_id:id(3),parent_dataset_version_id:id(4),source_sha256:'a'.repeat(64),filename:'synthetic-engineering-only.txt'},selection:{processed_dataset_version_id:id(5),sample_design_version_id:id(6),sample_design_request_id:id(7),replicate_model:'independent_biological',contrast:{schemaVersion:1,dataset_version_id:id(4),sample_design_version_id:id(6),quant_family:'LFQ intensity',contrasts:[{contrast_id:'c_reverse',numerator_group:'原分子组 β',denominator_group:'原分母组 α'}]}}};
 const parent={schemaVersion:1,contract:'differential_result_v0.1',result_id:id(8),job_id:id(2),attempt_id:id(9),registration_id:id(10),project_id:id(1),created_by:id(11),created_at:'2026-11-19T00:00:00.000000Z',request_id:id(12),admission_binding_sha256:context.binding_sha256,selection:structuredClone(context.selection),method:structuredClone(DIFFERENTIAL_METHOD),input_manifest:Object.fromEntries(['matrix','design','preset'].map(k=>[k,{bytes:1,sha256:'c'.repeat(64)}])),source_analysis_ready:false,exit_code:mode==='no-testable'?3:mode==='warning'?4:0,scientific_status:audit.output_status,warning_count:Number(audit.warning_count),review_required:mode==='warning',artifacts:parentFiles.map(pin),manifest_sha256:hash(parentFiles.map(pin)),receipt_sha256:'d'.repeat(64),stored:true};
 const inputSnapshotSHA='f'.repeat(64),requestSHA=hash({owner_id:parent.created_by,project_id:parent.project_id,request:{...parent.selection,request_id:parent.request_id}});
 parent.admission_binding_sha256=hash({input_manifest_sha256:hash(parent.input_manifest),input_snapshot_sha256:inputSnapshotSHA,method_sha256:hash(parent.method),request_sha256:requestSHA});context.binding_sha256=parent.admission_binding_sha256;
 const binding={schemaVersion:1,contract:'de_parent_binding_v0.1',owner_id:parent.created_by,project_id:parent.project_id,source_result_id:parent.result_id,source_job_id:parent.job_id,source_attempt_id:parent.attempt_id,source_registration_id:parent.registration_id,source_request_id:parent.request_id,source_contrast_id:'c_reverse',admission_binding_sha256:parent.admission_binding_sha256,input_snapshot_sha256:inputSnapshotSHA,source_metadata_sha256:hash(parent),source_receipt_sha256:parent.receipt_sha256,source_manifest_sha256:parent.manifest_sha256,source_artifacts:structuredClone(parent.artifacts),source_status:parent.scientific_status,source_warning_count:parent.warning_count,source_method_sha256:hash(parent.method),contrast_direction:'numerator_minus_denominator'};
 const evaluated=!['warning','no-testable'].includes(mode),labels=mode==='members'?['Down','Up','NS','not_testable','NS','NS','NS']:result.slice(1).map(r=>r[3]==='not_testable'?'not_testable':evaluated?'NS':'not_evaluated');
 const classified=result.slice(1).map((r,i)=>[...r,labels[i],labels[i]==='Up'||labels[i]==='Down'?'TRUE':labels[i]==='not_evaluated'?'NOT_EVALUATED':'FALSE']);
 const selected=classified.filter(r=>r[19]==='TRUE'),params={schema_version:'de_classification_v0.1',input_scale:'log2',adjusted_p_max:mode==='zero'?'1e-200':'.05',absolute_log2fc_min:mode==='zero'?'1e+9':'1'};
 const status=evaluated?'CLASSIFIED':mode==='no-testable'?'BLOCKED_SOURCE_NO_TESTABLE_ROWS':'BLOCKED_SOURCE_REVIEW_REQUIRED';
 const labelsCount=Object.fromEntries(['Up','Down','NS','not_testable','not_evaluated'].map(k=>[k,labels.filter(v=>v===k).length]));
 const a={schema_version:'de_classification_v0.1',classification_status:status,DE_members_evaluated:evaluated?'TRUE':'FALSE',source_contract:audit.schema_version,source_output_status:audit.output_status,source_warning_count:audit.warning_count,source_contrast_id:audit.contrast_id,source_numerator:audit.numerator,source_denominator:audit.denominator,contrast_direction:'numerator_minus_denominator',threshold_scale:'log2FC',adjusted_p_max:params.adjusted_p_max,absolute_log2fc_min:params.absolute_log2fc_min,boundary_rule:'adj.P.Val_le_max_and_abs_log2FC_ge_min_and_strict_direction_sign',zero_log2FC:'NS_if_evaluated',input_rows:'7',source_testable_rows:audit.bh_m,source_not_testable_rows:String(labelsCount.not_testable),Up:evaluated?String(labelsCount.Up):'NA',Down:evaluated?String(labelsCount.Down):'NA',NS:evaluated?String(labelsCount.NS):'NA',not_testable:String(labelsCount.not_testable),not_evaluated:String(labelsCount.not_evaluated),selected_DE_rows:evaluated?String(selected.length):'NA',source_bh_method:audit.bh_method,source_bh_m:audit.bh_m,source_bh_n:audit.bh_n,source_bh_scope:audit.bh_scope,FC_column:'copied_original_not_used_for_threshold',statistical_recomputation:'none',row_identity:'source_row_ordinal_no_ProteinID_deduplication',row_order:'original',specific_detection:'not_implemented_D04_not_frozen'};
 const values={'classification.tsv':tsv([[...result[0],'classification','selected_DE'],...classified]),'selected-DE.tsv':tsv([[...result[0],'classification','selected_DE'],...selected]),'classification-parameters.tsv':tsv([['key','value'],...paramsKeys.map(k=>[k,params[k]])]),'classification-audit.tsv':tsv([['key','value'],...Object.entries(a)]),'source-method-audit.tsv':get('method-audit.tsv').body,'source-test-set.tsv':get('test-set.tsv').body};
 const artifacts=DE_CLASSIFICATION_ARTIFACTS.map(name=>pack(name,values[name]));
 const derived={schemaVersion:1,contract:'de_classification_version_v0.1',derived_version_id:id(20),request_id:id(21),project_id:id(1),source_result_id:parent.result_id,source_manifest_sha256:parent.manifest_sha256,source_binding_sha256:'e'.repeat(64),parameters:params,parameters_sha256:artifacts.find(a=>a.name==='classification-parameters.tsv').sha256,core:{contract:'de_classification_v0.1',script_pin:{bytes:10335,sha256:'65e24d287a1546dd7a9fa0480f2a899d4d91eb4b11cda7a4802256fb90f3c421'},validator_pin:{bytes:12074,sha256:'2d37d97fcbbe6bb5c323c887b8c168e68c19945904dd3fe5bc9b247174a5d584'}},status,DE_members_evaluated:evaluated,counts:{...labelsCount,Up:evaluated?labelsCount.Up:null,Down:evaluated?labelsCount.Down:null,NS:evaluated?labelsCount.NS:null,selected_DE_rows:evaluated?selected.length:null},artifacts:artifacts.map(pin),manifest_sha256:hash(artifacts.map(pin)),created_at:'2026-11-19T00:00:01.000000Z',created_by:parent.created_by,source_analysis_ready:false,member_set:evaluated&&withMemberSet?{member_set_id:id(22),derived_version_id:id(20),selected_artifact_sha256:artifacts.find(a=>a.name==='selected-DE.tsv').sha256,member_count:selected.length}:null};
 derived.source_binding=binding;derived.source_binding_sha256=hash(binding);
 return{parentMetadata:parent,parentPresentation:{verifiedOwnerContext:context,rows:result.slice(1).map(r=>({source_row_ordinal:Number(r[0]),protein_ids_raw:r[1]})),artifacts:parentFiles,isCurrent:()=>true,contextSignal:new AbortController().signal},derivedMetadata:derived,artifacts,isCurrent:()=>true,contextSignal:new AbortController().signal,onDownload,cryptoImpl:webcrypto};
}
import {createDifferentialResultOwner} from '../differential-result-owner.js';
import {mountPrivateDifferentialResult} from '../private-differential-result.js';
import {createRawTransport} from '../private-txt-upload.js';
import {createQCArtifactScope} from '../raw-qc-client.js';
import {harness} from './support/raw-qc-bff-session.mjs';
import {wire,browserFetch} from './support/differential-result-composition.mjs';
import {saver,clock} from './support/raw-qc-download-setup.mjs';
import {until,memory} from './support/preprocess-owner-setup.mjs';

import {createClassificationRecovery} from '../differential-job-client.js';

async function capacitySetup(t,initialMode='busy'){
 const o=input(),meta=o.parentMetadata,native=wire(),storage=memory(),queued=new Map();let saved=false,uuidCalls=0,mode=initialMode,missing=false,spawned=0,idempotent=0,lastBody=null,held=null,runMode='failure',runHeld=null; let runMutate=null;
 const h=harness(t,{useServer:true,differentialResultsEnabled:true,differentialResultsRequest:native.request,deClassificationsEnabled:true,deClassificationsRequest:native.request,classificationJobsEnabled:true,classificationJobsRequest:native.request}),identity=await h.signed();
 const entries=o.parentPresentation.rows,quote=v=>'"'+v.replaceAll('"','""')+'"';let work=0;
 const rowMap=enc.encode('"source_record_number"\t"protein_ids_raw"\t"kept"\t"work_row_ordinal"\n'+Array.from({length:12},(_,i)=>{const r=entries.find(r=>r.source_row_ordinal===i+1);return[String(i+1),quote(r?r.protein_ids_raw:'SKIP_'+i),r?'TRUE':'FALSE',r?String(++work):'NA'].join('\t');}).join('\n')+'\n');
 const job={job_id:meta.job_id,project_id:meta.project_id,request_id:meta.request_id,created_at:'2026-11-18T23:00:00.000000Z',binding_sha256:meta.admission_binding_sha256,selection:meta.selection,method:meta.method,input_manifest:meta.input_manifest,source:{analysis_ready:false,file_id:o.parentPresentation.verifiedOwnerContext.source.file_id,parent_dataset_version_id:meta.selection.contrast.dataset_version_id,created_by:meta.created_by,source:{source_sha256:o.parentPresentation.verifiedOwnerContext.source.source_sha256,row_count:12},artifacts:[pin(pack('row-map.tsv',rowMap))]}};
 const run={schemaVersion:1,contract:'differential_run_v0.1',job_id:job.job_id,attempt_id:meta.attempt_id,registration_id:meta.registration_id,project_id:meta.project_id,status:'succeeded',observation:'confirmed',revision:3,started_at:meta.created_at,finished_at:'2026-11-19T00:00:02.000000Z',result_id:meta.result_id,error_code:null};
 const key='cyomics:classification:refs:v1:'+meta.created_by;
 const makeDTO=body=>({schemaVersion:1,contract:'de_classification_queued_v0.1',classification_job_id:id(80+queued.size),project_id:meta.project_id,request_id:body.request_id,created_by:meta.created_by,source_result_id:body.source_result_id,source_manifest_sha256:meta.manifest_sha256,source_binding_sha256:o.derivedMetadata.source_binding_sha256,parameters:body.parameters,parameters_sha256:digest(enc.encode(tsv([['key','value'],...paramsKeys.map(k=>[k,body.parameters[k]])]))),core:o.derivedMetadata.core,created_at:'2026-11-19T00:00:00.500000Z',status:'queued',observation:'confirmed'});
 native.reply(c=>{
  const path=c.options.path;
  if(path.includes('/classification-jobs')&&path.endsWith('&view=run')){
   const request=new URL(path,'https://synthetic.invalid').searchParams.get('request_id'),task=queued.get(request);if(!task)return c.deliver({error:{code:'de_classification_job_not_found'}},404);
   if(runMode==='unavailable')return c.deliver({error:{code:'de_classification_jobs_unavailable'}},503);
   const r={schemaVersion:1,contract:'de_classification_run_v0.1',classification_job_id:task.classification_job_id,project_id:task.project_id,request_id:task.request_id,created_by:task.created_by,source_result_id:task.source_result_id,source_manifest_sha256:task.source_manifest_sha256,source_binding_sha256:task.source_binding_sha256,parameters_sha256:task.parameters_sha256,created_at:task.created_at,state:'finished',observation:'confirmed',attempt_id:id(900),claimed_at:'2026-11-19T00:00:00.600000Z',finished_at:'2026-11-19T00:00:02.000000Z',error_code:'runner_failed',derived_version_id:null,scientific_status:null};
   if(runMode==='queued')Object.assign(r,{state:'queued',attempt_id:null,claimed_at:null,finished_at:null,error_code:null});
   if(runMode==='claimed')Object.assign(r,{state:'claimed',finished_at:null,error_code:null});
   if(['success','existing','pending'].includes(runMode))Object.assign(r,{error_code:null,derived_version_id:o.derivedMetadata.derived_version_id,scientific_status:o.derivedMetadata.status});
   if(runMode==='existing')Object.assign(r,{attempt_id:null,claimed_at:null,finished_at:o.derivedMetadata.created_at});
   if(runMode==='pending')Object.assign(r,{state:'reconciliation_pending',finished_at:null});
   runMutate?.(r);
   if(runMode==='hold'){runHeld={call:c,value:r};return;}
   return c.deliver(r);
  }
  if(path.includes('/classification-jobs')){
   if(c.options.method==='POST'){
    const body=JSON.parse(c.body),raw=JSON.parse(storage.getItem(key)),r=raw.items.find(r=>r.request_id===body.request_id);
    assert.ok(r,'Unknown reference must be written before POST');assert.equal(r.disposition,'unknown');assert.equal(r.queued,null);assert.deepEqual(r.parameters,body.parameters);lastBody=body;
    if(mode==='busy')return c.deliver({error:{code:'de_classification_capacity_busy'}},409);
    if(mode==='conflict')return c.deliver({error:{code:'de_classification_request_conflict'}},409);
    if(mode==='unknown-missing')return c.deliver({error:{code:'de_classification_submission_unknown'}},504);
    if(queued.has(body.request_id)){idempotent++;return c.deliver(queued.get(body.request_id),200);}
    const dto=makeDTO(body);
    queued.set(body.request_id,dto);spawned++;o.derivedMetadata.request_id=body.request_id;
    if(mode==='hold-queued'){held=c;return;}
    return mode==='unknown-queued'?c.deliver({error:{code:'de_classification_submission_unknown'}},504):c.deliver(dto,201);
   }
   const request=new URL(path,'https://synthetic.invalid').searchParams.get('request_id');return !missing&&queued.has(request)?c.deliver(queued.get(request)):c.deliver({error:{code:'de_classification_job_not_found'}},404);
  }
  assert.equal(c.options.method,'GET');
  if(path.includes('/differential-runs/'))return c.deliver(run);
  if(path.includes('/differential-results')&&!path.includes('/artifacts/'))return c.deliver(meta);
  if(path.includes('/differential-results/')&&path.includes('/artifacts/')){const a=o.parentPresentation.artifacts.find(a=>path.endsWith('/'+a.name));return c.binary(a.name,a.body);}
  if(path.includes('/differential-classifications')&&!path.includes('/files/'))return saved?c.deliver(o.derivedMetadata):c.deliver({error:{code:'de_classification_version_not_found'}},404);
  if(path.includes('/files/')){const a=o.artifacts.find(a=>path.endsWith('/'+a.name));return c.binary(a.name,a.body);}
  throw Error('Unexpected synthetic recovery route');
 });
 const source={file:{project_id:meta.project_id,file_id:job.source.file_id,sha256:job.source.source.source_sha256,filename:'synthetic-engineering-only.txt'}};
 async function open(who=identity){
  const context=new AbortController(),scope=createQCArtifactScope(),save=saver(),timers=clock(),cryptoImpl={subtle:webcrypto.subtle,randomUUID:()=>id(300+(++uuidCalls))};
  const raw=createRawTransport({projectId:meta.project_id,fetchImpl:browserFetch(h,who),isCurrent:()=>!context.signal.aborted,preprocessingEnabled:true,preprocessingStorage:storage,preprocessingArtifactScope:scope,cryptoImpl,getOptions:signal=>({signal,credentials:'same-origin',cache:'no-store',redirect:'error',headers:{'X-Cyomics-Account-Context':who.view}})}),base=raw.differentialResults({projectId:meta.project_id,fileId:job.source.file_id,isCurrent:()=>!context.signal.aborted});
  const transport={...base,async rowMap(_,signal){const p=job.source.artifacts[0];return{response:new Response(rowMap,{headers:{'content-type':'application/octet-stream','content-length':String(p.bytes),'content-disposition':'attachment; filename="row-map.tsv"','x-cyomics-file-sha256':p.sha256,'cache-control':'no-store','x-content-type-options':'nosniff','referrer-policy':'no-referrer'}}),checkCurrent:()=>assert.equal(signal.aborted,false)};}};
  let jobChecks=0;const jobs={async check(request){assert.equal(request,job.request_id);jobChecks++;return true;},view:()=>({history:[{request_id:job.request_id,task:job}],code:null})};
  const actor=createDifferentialResultOwner({projectId:meta.project_id,fileId:job.source.file_id,jobs,transport,source:()=>source,isCurrent:()=>!context.signal.aborted,contextSignal:context.signal,cryptoImpl,...save,...timers,provenance:'synthetic_engineering_verification'}),d=dom(),stop=mountPrivateDifferentialResult(d.root,{client:actor});
  const close=()=>{context.abort();stop();actor.dispose();raw.disposePreprocessing();timers.advance(120000);};t.after(close);
  const ok=await actor.select(job.request_id);if(ok){await until(()=>d.all().some(n=>Object.hasOwn(n.attrs,'data-lrt-source-row')));await flush();}
  return{actor,d,ok,context,close,jobChecks:()=>jobChecks,scope,set(p='.05',f='1'){for(const [name,value] of [['adjusted-p-max',p],['absolute-log2fc-min',f]]){const n=d.by('data-de-submit-control',name);n.value=value;n.fire('input');}},async check(){d.by('data-de-submit-control','check').click();await until(()=>!actor.view().busy&&actor.view().classificationJob.phase!=='recovered'&&actor.view().classification.phase!=='loading');if(actor.view().classification.phase==='ready')await until(()=>d.by('data-de-field','member-set'));return actor.view();}};
 }
 return{o,meta,h,native,storage,key,identity,open,runMode:v=>{runMode=v;runHeld=null;},runHeld:()=>runHeld,runMutate:fn=>{runMutate=fn;},saved:v=>{saved=v;},uuidCalls:()=>uuidCalls,mode:v=>{mode=v;},missing:v=>{missing=v;},spawned:()=>spawned,idempotent:()=>idempotent,held:()=>held,admitExisting(){assert.ok(lastBody);queued.set(lastBody.request_id,makeDTO(lastBody));spawned++;o.derivedMetadata.request_id=lastBody.request_id;},jobCalls:()=>native.calls.filter(c=>c.options.path.includes('/classification-jobs'))};
}

async function waitPhase(scene,phase){await until(()=>scene.actor.view().classificationJob.phase===phase&&!scene.actor.view().busy&&scene.actor.view().classification.phase!=='loading');await flush();return scene.actor.view();}
async function initial(t,mode){const a=await capacitySetup(t,mode),scene=await a.open();assert.equal(scene.ok,true);scene.set();scene.d.by('data-de-submit-control','submit').click();return{a,scene};}
const posts=a=>a.jobCalls().filter(c=>c.options.method==='POST');
const retry=scene=>scene.d.by('data-de-submit-control','retry');

import {classificationJobRoute,classificationJobReply} from '../de-classification-read-contract.mjs';
test('confirmed terminal failure alone enables explicit same-threshold new request; immutable history, run isolation and full saved-result proof remain enforced',async t=>{
 const {a,scene}=await initial(t,'queued');await waitPhase(scene,'queued');
 const first=JSON.parse(posts(a)[0].body),task=structuredClone(scene.actor.view().classificationJob.task),parentTable=scene.d.all().filter(n=>Object.hasOwn(n.attrs,'data-lrt-source-row')).map(n=>n.textContent),before=a.native.calls.length;
 assert.equal(a.uuidCalls(),1);assert.equal(scene.actor.view().classificationJob.execution.phase,'idle');assert.equal(scene.d.by('data-de-submit-control','submit').disabled,true);
 await scene.check();let v=scene.actor.view();assert.equal(v.classificationJob.execution.phase,'verified');assert.equal(v.classificationJob.execution.value.state,'finished');assert.equal(v.classificationJob.execution.value.error_code,'runner_failed');assert.equal(v.classificationJob.execution.value.derived_version_id,null);
 assert.deepEqual(v.classificationJob.task,task);assert.equal(v.classificationJob.task.status,'queued');assert.equal(v.classificationJob.new_same_parameters,true);assert.equal(v.phase,'ready');assert.equal(v.meta.result_id,a.meta.result_id);
 assert.match(scene.d.by('data-de-submit-state','status').textContent,/执行失败.*runner_failed.*原请求已保留/);assert.doesNotMatch(scene.d.by('data-de-submit-state','status').textContent,/R已运行|R未启动|请求已入队/);
 const ordered=a.native.calls.slice(before);assert.ok(ordered[0].options.path.endsWith('?request_id='+first.request_id));assert.ok(ordered[1].options.path.endsWith('?request_id='+first.request_id+'&view=run'));assert.equal(ordered.length,2);assert.deepEqual(scene.d.all().filter(n=>Object.hasOwn(n.attrs,'data-lrt-source-row')).map(n=>n.textContent),parentTable);
 const oldStored=JSON.parse(a.storage.getItem(a.key)).items.find(r=>r.request_id===first.request_id);assert.equal(oldStored.disposition,'confirmed');assert.equal(a.storage.getItem(a.key).includes('runner_failed'),false);
 const button=scene.d.by('data-de-submit-control','submit');assert.equal(button.textContent,'用相同阈值新建任务');assert.equal(button.disabled,false);button.click();button.click();await waitPhase(scene,'queued');
 assert.equal(posts(a).length,2);assert.equal(a.uuidCalls(),2);const second=JSON.parse(posts(a)[1].body);assert.notEqual(second.request_id,first.request_id);assert.deepEqual(second.parameters,first.parameters);assert.equal(second.source_result_id,first.source_result_id);assert.equal(scene.actor.view().classificationJob.execution.phase,'idle');
 const both=JSON.parse(a.storage.getItem(a.key)).items;assert.equal(both.length,2);assert.deepEqual(both.find(r=>r.request_id===first.request_id),oldStored);assert.equal(scene.d.by('data-de-submit-control','submit').disabled,true);assert.equal(retry(scene).hidden,true);
 const params=second.parameters;
 for(const mode of ['queued','claimed','pending','unavailable']){a.runMode(mode);await scene.check();v=scene.actor.view();assert.equal(v.classificationJob.new_same_parameters,false);assert.equal(scene.actor.canSubmitClassification(params),false);assert.equal(scene.d.by('data-de-submit-control','submit').disabled,true);assert.equal(await scene.actor.submitClassification(params),false);assert.equal(posts(a).length,2);if(mode==='unavailable'){assert.equal(v.classificationJob.execution.phase,'unavailable');assert.match(scene.d.by('data-de-submit-state','status').textContent,/未确认/);assert.doesNotMatch(scene.d.by('data-de-submit-state','status').textContent,/仍在排队|执行失败|请求已入队/);}}
 // Matching successful run is insufficient until the original six files and
 // parent proof are read and verified, including the existing member set.
 a.runMode('success');a.saved(true);const filesBefore=a.native.calls.filter(c=>c.options.path.includes('/files/')).length;await scene.check();v=scene.actor.view();assert.equal(v.classification.phase,'ready');assert.equal(v.classification.meta.status,'CLASSIFIED');assert.equal(a.native.calls.filter(c=>c.options.path.includes('/files/')).length-filesBefore,6);assert.equal(scene.actor.canSubmitClassification(params),false);assert.match(scene.d.by('data-de-submit-state','status').textContent,/执行已结束.*结果已核对/);
 a.runMode('existing');await scene.check();assert.equal(scene.actor.view().classificationJob.execution.value.attempt_id,null);assert.equal(scene.actor.view().classification.phase,'ready');
 a.runMutate(r=>{r.finished_at='2026-11-19T00:00:03.000000Z';});await scene.check();assert.equal(scene.actor.view().classification.phase,'unavailable');assert.equal(scene.actor.view().classificationJob.new_same_parameters,false);a.runMutate(null);
 // Owner and source/hash/identity contradictions fail closed before a failed
 // status or new-request permission is published. Invalid nullability is not
 // allowed to forge terminal authority either.
 a.saved(false);a.runMode('failure');
 for(const mutate of [r=>{r.created_by=id(999);},r=>{r.source_manifest_sha256='9'.repeat(64);},r=>{r.classification_job_id=id(998);},r=>{r.error_code='other';},r=>{r.attempt_id=null;},r=>{r.finished_at='2026-02-30T00:00:02.000000Z';},r=>{r.extra=true;}]){a.runMutate(mutate);await scene.check();assert.equal(scene.actor.view().classificationJob.execution.phase,'unavailable');assert.equal(scene.actor.view().classificationJob.new_same_parameters,false);assert.equal(posts(a).length,2);}
 a.runMutate(null);a.runMode('hold');const pending=scene.actor.checkClassificationJob();await until(()=>a.runHeld());const held=a.runHeld();await scene.actor.clearClassification();assert.equal(await pending,false);held.call.deliver(held.value);await flush();assert.notEqual(scene.actor.view().classificationJob.execution.phase,'verified');assert.equal(scene.actor.view().classificationJob.new_same_parameters,false);assert.equal(scene.actor.view().phase,'ready');assert.equal(posts(a).length,2);
 // A fresh Owner scope may restore the original hint, never volatile terminal
 // permission. Changing Owner while a held failure arrives cannot revive it.
 a.runMode('failure');await scene.check();assert.equal(scene.actor.view().classificationJob.new_same_parameters,true);scene.close();const reopened=await a.open();assert.equal(reopened.actor.view().classificationJob.execution.phase,'idle');assert.equal(reopened.actor.view().classificationJob.new_same_parameters,false);
 a.runMode('hold');const late=reopened.actor.checkClassificationJob();await until(()=>a.runHeld());const old=a.runHeld();reopened.context.abort();assert.equal(await late,false);old.call.deliver(old.value);await flush();assert.equal(reopened.actor.view().phase,'disposed');assert.equal(reopened.actor.view().classificationJob.new_same_parameters,false);
 const who=await a.h.signed('B'),other=await a.open(who);assert.equal(other.ok,false);assert.equal(other.actor.view().classificationJob.new_same_parameters,false);assert.equal(posts(a).length,2);
 const unknown=await initial(t,'unknown-missing');await waitPhase(unknown.scene,'unknown');assert.equal(unknown.scene.actor.canSubmitClassification(params),false);assert.equal(await unknown.scene.actor.submitClassification(params),false);assert.equal(posts(unknown.a).length,1);assert.equal(unknown.a.uuidCalls(),1);
 const route=classificationJobRoute('/api/v1/projects/'+a.meta.project_id+'/classification-jobs?request_id='+first.request_id+'&view=run','GET');assert.equal(route.kind,'classification-run-lookup');assert.throws(()=>classificationJobRoute(route.path,'POST'));assert.throws(()=>classificationJobRoute(route.path+'&view=run','GET'));
 const run=structuredClone(v.classificationJob.execution.value);run.request_id=first.request_id;run.classification_job_id=task.classification_job_id;run.created_at=task.created_at;assert.equal(classificationJobReply(route,200,run,null,a.meta.created_by).contract,'de_classification_run_v0.1');
 t.diagnostic('Synthetic composition only: actual-failure DTO shape and parent-bound run, two explicit distinct UUIDs with same raw thresholds, old confirmed recovery retained, six-artifact success proof, unconfirmed/queued/claimed/reconciliation block same-threshold new intent, Owner/cancel/late guards. No actual API/DB/R/browser execution; no old suites.');
});
