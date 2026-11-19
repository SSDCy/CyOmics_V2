// One new refresh-recovery composition only. Synthetic SDK/Auth/BFF/native data; no actual backend/R or old suites. Synthetic BFF/native wire + verified-job and original row-map adapters are mocked; no real Auth/backend/R/Owner HTTP. Original suites are not imported or run.
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
 const evaluated=!['warning','no-testable'].includes(mode),labels=mode==='members'?['Down','Up','Up','not_testable','Down','Down','Down']:result.slice(1).map(r=>r[3]==='not_testable'?'not_testable':evaluated?'NS':'not_evaluated');
 const classified=result.slice(1).map((r,i)=>[...r,labels[i],labels[i]==='Up'||labels[i]==='Down'?'TRUE':labels[i]==='not_evaluated'?'NOT_EVALUATED':'FALSE']);
 const selected=classified.filter(r=>r[19]==='TRUE'),params={schema_version:'de_classification_v0.1',input_scale:'log2',adjusted_p_max:mode==='zero'?'1e-200':'1.0',absolute_log2fc_min:mode==='zero'?'1e+9':'0.000'};
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

async function recoverySetup(t){
 const o=input(),meta=o.parentMetadata,native=wire(),storage=memory(),queued=new Map();let saved=false,uuidCalls=0;
 const h=harness(t,{useServer:true,differentialResultsEnabled:true,differentialResultsRequest:native.request,deClassificationsEnabled:true,deClassificationsRequest:native.request,classificationJobsEnabled:true,classificationJobsRequest:native.request}),identity=await h.signed();
 const entries=o.parentPresentation.rows,quote=v=>'"'+v.replaceAll('"','""')+'"';let work=0;
 const rowMap=enc.encode('"source_record_number"\t"protein_ids_raw"\t"kept"\t"work_row_ordinal"\n'+Array.from({length:12},(_,i)=>{const r=entries.find(r=>r.source_row_ordinal===i+1);return[String(i+1),quote(r?r.protein_ids_raw:'SKIP_'+i),r?'TRUE':'FALSE',r?String(++work):'NA'].join('\t');}).join('\n')+'\n');
 const job={job_id:meta.job_id,project_id:meta.project_id,request_id:meta.request_id,created_at:'2026-11-18T23:00:00.000000Z',binding_sha256:meta.admission_binding_sha256,selection:meta.selection,method:meta.method,input_manifest:meta.input_manifest,source:{analysis_ready:false,file_id:o.parentPresentation.verifiedOwnerContext.source.file_id,parent_dataset_version_id:meta.selection.contrast.dataset_version_id,created_by:meta.created_by,source:{source_sha256:o.parentPresentation.verifiedOwnerContext.source.source_sha256,row_count:12},artifacts:[pin(pack('row-map.tsv',rowMap))]}};
 const run={schemaVersion:1,contract:'differential_run_v0.1',job_id:job.job_id,attempt_id:meta.attempt_id,registration_id:meta.registration_id,project_id:meta.project_id,status:'succeeded',observation:'confirmed',revision:3,started_at:meta.created_at,finished_at:'2026-11-19T00:00:02.000000Z',result_id:meta.result_id,error_code:null};
 const key='cyomics:classification:refs:v1:'+meta.created_by;
 native.reply(c=>{
  const path=c.options.path;
  if(path.includes('/classification-jobs')){
   if(c.options.method==='POST'){
    const body=JSON.parse(c.body),raw=JSON.parse(storage.getItem(key)),r=raw.items.find(r=>r.request_id===body.request_id);
    assert.ok(r,'Unknown reference must be written before POST');assert.equal(r.disposition,'unknown');assert.equal(r.queued,null);assert.deepEqual(r.parameters,body.parameters);
    const dto={schemaVersion:1,contract:'de_classification_queued_v0.1',classification_job_id:id(80),project_id:meta.project_id,request_id:body.request_id,created_by:meta.created_by,source_result_id:body.source_result_id,source_manifest_sha256:meta.manifest_sha256,source_binding_sha256:o.derivedMetadata.source_binding_sha256,parameters:body.parameters,parameters_sha256:digest(enc.encode(tsv([['key','value'],...paramsKeys.map(k=>[k,body.parameters[k]])]))),core:o.derivedMetadata.core,created_at:'2026-11-19T00:00:00.500000Z',status:'queued',observation:'confirmed'};
    queued.set(body.request_id,dto);o.derivedMetadata.request_id=body.request_id;
    return c.deliver({error:{code:'de_classification_submission_unknown'}},504); // Mock commit with lost acknowledgement.
   }
   const request=new URL(path,'https://synthetic.invalid').searchParams.get('request_id');return queued.has(request)?c.deliver(queued.get(request)):c.deliver({error:{code:'de_classification_job_not_found'}},404);
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
  const jobs={async check(request){assert.equal(request,job.request_id);return true;},view:()=>({history:[{request_id:job.request_id,task:job}],code:null})};
  const actor=createDifferentialResultOwner({projectId:meta.project_id,fileId:job.source.file_id,jobs,transport,source:()=>source,isCurrent:()=>!context.signal.aborted,contextSignal:context.signal,cryptoImpl,...save,...timers,provenance:'synthetic_engineering_verification'}),d=dom(),stop=mountPrivateDifferentialResult(d.root,{client:actor});
  const close=()=>{context.abort();stop();actor.dispose();raw.disposePreprocessing();timers.advance(120000);};t.after(close);
  const ok=await actor.select(job.request_id);if(ok){await until(()=>d.all().some(n=>Object.hasOwn(n.attrs,'data-lrt-source-row')));await flush();}
  return{actor,d,ok,context,close,set(p='1.0',f='0.000'){for(const [name,value] of [['adjusted-p-max',p],['absolute-log2fc-min',f]]){const n=d.by('data-de-submit-control',name);n.value=value;n.fire('input');}},async check(){d.by('data-de-submit-control','check').click();await until(()=>!actor.view().busy&&actor.view().classificationJob.phase!=='recovered'&&actor.view().classification.phase!=='loading');if(actor.view().classification.phase==='ready')await until(()=>d.by('data-de-field','member-set'));return actor.view();}};
 }
 return{o,meta,h,native,storage,key,identity,open,uuidCalls:()=>uuidCalls,saved:v=>{saved=v;},jobCalls:()=>native.calls.filter(c=>c.options.path.includes('/classification-jobs'))};
}

test('one refresh recovery composition keeps original hints/Owner scope, confirms only manual GET, and blocks corrupt/full storage without replacement POST',async t=>{
 const a=await recoverySetup(t),first=await a.open();assert.equal(first.ok,true);first.set();first.d.by('data-de-submit-control','submit').click();await until(()=>first.actor.view().classificationJob.phase==='unknown'&&!first.actor.view().busy);
 assert.equal(a.uuidCalls(),1);assert.equal(a.jobCalls().filter(c=>c.options.method==='POST').length,1);const beforeRefresh=a.storage.getItem(a.key),record=JSON.parse(beforeRefresh).items[0],original=record.request_id;assert.equal(record.disposition,'unknown');assert.deepEqual(record.parameters,{schema_version:'de_classification_v0.1',input_scale:'log2',adjusted_p_max:'1.0',absolute_log2fc_min:'0.000'});
 assert.equal(JSON.stringify(record).includes('ACCESS_A'),false);assert.equal(JSON.stringify(record).includes('protein_ids_raw'),false);first.close();
 const helper=createClassificationRecovery(a.storage),scope=JSON.parse(beforeRefresh).scope,other=structuredClone(record);Object.assign(other,{project_id:id(90),file_id:id(91),source_result_id:id(92),request_id:id(93),source_meta_sha256:'c'.repeat(64),source_manifest_sha256:'d'.repeat(64)});helper.put(a.meta.created_by,scope,other);
 const otherUnknown=canonicalJSON(other),calls=a.jobCalls().length,refresh=await a.open();assert.equal(refresh.actor.view().classificationJob.phase,'recovered');assert.equal(refresh.actor.view().classificationJob.task,null);assert.equal(refresh.actor.view().classificationJob.recovered_hint,true);assert.equal(a.jobCalls().length,calls,'Refresh must not auto-query jobs');
 assert.equal(refresh.d.by('data-de-submit-control','adjusted-p-max').value,'1.0');assert.equal(refresh.d.by('data-de-submit-control','absolute-log2fc-min').value,'0.000');assert.equal(refresh.d.by('data-de-submit-control','submit').disabled,true);assert.match(refresh.d.by('data-de-submit-state','status').textContent,/本地原请求提示.*尚未确认/);refresh.close();
 const ownerB=await a.h.signed('B'),rawA=a.storage.getItem(a.key),b=await a.open(ownerB);assert.equal(b.ok,false);assert.equal(b.actor.view().classificationJob.has_reference,false);assert.equal(b.d.by('data-de-submit-control','adjusted-p-max').value,'');assert.equal(a.storage.getItem(a.key),rawA);b.close();
 // Lost-login simulation: no storage authority bypass, then original Owner
 // reauthenticates with a fresh nonce and still recovers only a hint.
 a.h.advance(3600001);a.h.expire();const signedOut=await a.open();assert.equal(signedOut.ok,false);assert.equal(a.storage.getItem(a.key),rawA);signedOut.close();
 const ownerA=await a.h.signed('A'),back=await a.open(ownerA);assert.equal(back.actor.view().classificationJob.phase,'recovered');assert.equal(a.jobCalls().length,calls);
 // Write failure on confirmation must preserve the valid original unknown.
 const setItem=a.storage.setItem;a.storage.setItem=()=>{throw Error('synthetic quota/write failure');};const value=await back.check();assert.equal(value.classificationJob.phase,'queued');assert.equal(value.classificationJob.recovery_status,'blocked');assert.equal(a.storage.getItem(a.key),rawA);assert.equal(a.uuidCalls(),1);a.storage.setItem=setItem;back.close();
 const confirm=await a.open(ownerA);assert.equal(confirm.actor.view().classificationJob.phase,'recovered');await confirm.check();const confirmedRaw=a.storage.getItem(a.key),confirmed=JSON.parse(confirmedRaw);assert.equal(confirmed.items.find(r=>r.request_id===original).disposition,'confirmed');assert.equal(canonicalJSON(confirmed.items.find(r=>r.request_id===other.request_id)),otherUnknown);confirm.close();
 // A stored confirmed disposition/15-DTO pin still cannot restore queued/result.
 const queuedHint=await a.open(ownerA);assert.equal(queuedHint.actor.view().classificationJob.phase,'recovered');assert.equal(queuedHint.actor.view().classificationJob.task,null);a.saved(true);await queuedHint.check();assert.equal(queuedHint.actor.view().classification.phase,'ready');assert.equal(queuedHint.actor.view().classification.meta.request_id,original);queuedHint.close();a.saved(false);
 for(const corrupt of [doc=>{doc.scope='0'.repeat(64);},doc=>{doc.items.find(r=>r.request_id===original).parameters.adjusted_p_max='0.75';},doc=>{doc.items.find(r=>r.request_id===original).source_meta_sha256='e'.repeat(64);},doc=>{doc.items.find(r=>r.request_id===original).source_result_id=id(77);}]){
  const doc=JSON.parse(confirmedRaw);corrupt(doc);const raw=JSON.stringify(doc);a.storage.setItem(a.key,raw);const count=a.jobCalls().length,scene=await a.open(ownerA);assert.equal(scene.actor.view().classificationJob.recovery_status,'blocked');assert.equal(scene.actor.view().classificationJob.has_reference,false);scene.set('0.04','2');scene.d.by('data-de-submit-control','submit').click();await flush();assert.equal(a.uuidCalls(),1);assert.equal(a.jobCalls().length,count);assert.equal(a.storage.getItem(a.key),raw);scene.close();
 }
 a.storage.setItem(a.key,'x'.repeat(65537));const huge=await a.open(ownerA);assert.equal(huge.actor.view().classificationJob.recovery_status,'blocked');assert.equal(a.storage.getItem(a.key).length,65537);assert.equal(a.uuidCalls(),1);huge.close();
 const tampered=JSON.parse(confirmedRaw);tampered.items.find(r=>r.request_id===original).queued.dto_sha256='f'.repeat(64);const tamperedRaw=JSON.stringify(tampered);a.storage.setItem(a.key,tamperedRaw);const badAnchor=await a.open(ownerA);assert.equal(badAnchor.actor.view().classificationJob.phase,'recovered');await badAnchor.check();assert.equal(badAnchor.actor.view().classificationJob.phase,'unavailable');assert.equal(badAnchor.actor.view().classificationJob.task,null);assert.equal(a.storage.getItem(a.key),tamperedRaw);badAnchor.close();
 a.storage.setItem(a.key,confirmedRaw);for(let i=0;i<14;i++){const r=structuredClone(other);Object.assign(r,{file_id:id(200+i),source_result_id:id(600+i),request_id:id(500+i)});helper.put(a.meta.created_by,scope,r);}
 const full=await a.open(ownerA);assert.equal(full.actor.view().classificationJob.recovery_count,16);await full.check();full.set('0.04','2');assert.equal(full.d.by('data-de-submit-control','submit').disabled,true);const fullRaw=a.storage.getItem(a.key);full.d.by('data-de-submit-control','submit').click();await flush();assert.equal(a.storage.getItem(a.key),fullRaw);assert.equal(JSON.parse(fullRaw).items.length,16);assert.equal(a.uuidCalls(),1);
 const jobCalls=a.jobCalls();assert.equal(jobCalls.filter(c=>c.options.method==='POST').length,1);for(const c of jobCalls.filter(c=>c.options.method==='GET'))assert.equal(c.options.path,'/api/v1/projects/'+a.meta.project_id+'/classification-jobs?request_id='+original);
 assert.equal(a.native.calls.some(c=>c.options.path.includes('/projects/'+other.project_id+'/')),false);assert.equal(canonicalJSON(JSON.parse(fullRaw).items.find(r=>r.request_id===other.request_id)),otherUnknown);
 t.diagnostic('One synthetic combination: full actor replacement/refresh restores unknown and confirmed only as hints with original tokens/request; no automatic GET/POST. Other Owner/lost login show none and preserve A unknown; original Owner new nonce can manually GET then verify saved six. Scope/parameter/source/DTO-anchor tampering, >64KiB, write failure and16-ref capacity fail closed without erasing valid unknown or generating replacement UUID. All job GETs original request, totalPOST1/UUID1, unrelated project never queried. No real Auth/DB/R/service/browser E2E.');
});
