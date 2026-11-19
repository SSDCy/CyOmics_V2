// New admission UI slice only. No real backend/Auth/R or durable joblist. Synthetic BFF/native wire + verified-job and original row-map adapters are mocked; no real Auth/backend/R/Owner HTTP. Original suites are not imported or run.
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

async function setupSubmit(t){
 const o=input(),meta=o.parentMetadata,native=wire(),h=harness(t,{useServer:true,differentialResultsEnabled:true,differentialResultsRequest:native.request,deClassificationsEnabled:true,deClassificationsRequest:native.request,classificationJobsEnabled:true,classificationJobsRequest:native.request}),identity=await h.signed();
 const scope=createQCArtifactScope(),context=new AbortController(),save=saver(),timers=clock();let mode='normal',holdHash=false,paused=null,pausedBody=null,savedResult=false,unknownPost=false,uuidCalls=0;const queuedJobs=new Map();
 const rowEntries=o.parentPresentation.rows,quote=v=>'"'+v.replaceAll('"','""')+'"';let work=0;
 const rowMap=enc.encode('"source_record_number"\t"protein_ids_raw"\t"kept"\t"work_row_ordinal"\n'+Array.from({length:12},(_,i)=>{const row=rowEntries.find(r=>r.source_row_ordinal===i+1);return[String(i+1),quote(row?row.protein_ids_raw:'SYN_SKIP_'+i),row?'TRUE':'FALSE',row?String(++work):'NA'].join('\t');}).join('\n')+'\n');
 const job={job_id:meta.job_id,project_id:meta.project_id,request_id:meta.request_id,created_at:'2026-11-18T23:00:00.000000Z',binding_sha256:meta.admission_binding_sha256,selection:meta.selection,method:meta.method,input_manifest:meta.input_manifest,source:{analysis_ready:false,file_id:o.parentPresentation.verifiedOwnerContext.source.file_id,parent_dataset_version_id:meta.selection.contrast.dataset_version_id,created_by:meta.created_by,source:{source_sha256:o.parentPresentation.verifiedOwnerContext.source.source_sha256,row_count:12},artifacts:[pin(pack('row-map.tsv',rowMap))]}};
 const run={schemaVersion:1,contract:'differential_run_v0.1',job_id:job.job_id,attempt_id:meta.attempt_id,registration_id:meta.registration_id,project_id:meta.project_id,status:'succeeded',observation:'confirmed',revision:3,started_at:meta.created_at,finished_at:'2026-11-19T00:00:02.000000Z',result_id:meta.result_id,error_code:null};
 native.reply(c=>{
  const p=c.options.path;
  if(p.includes('/classification-jobs')){
   if(c.options.method==='POST'){
    const body=JSON.parse(c.body);assert.deepEqual(Object.keys(body).sort(),['parameters','request_id','source_result_id']);
    const dto={schemaVersion:1,contract:'de_classification_queued_v0.1',classification_job_id:id(80+queuedJobs.size),project_id:meta.project_id,request_id:body.request_id,created_by:meta.created_by,source_result_id:body.source_result_id,source_manifest_sha256:meta.manifest_sha256,source_binding_sha256:o.derivedMetadata.source_binding_sha256,parameters:structuredClone(body.parameters),parameters_sha256:digest(enc.encode(tsv([['key','value'],...paramsKeys.map(k=>[k,body.parameters[k]])]))),core:structuredClone(o.derivedMetadata.core),created_at:'2026-11-19T00:00:00.500000Z',status:'queued',observation:'confirmed'};
    queuedJobs.set(body.request_id,dto);o.derivedMetadata.request_id=body.request_id;
    return unknownPost?c.deliver({error:{code:'de_classification_submission_unknown'}},504):c.deliver(dto,201);
   }
   assert.equal(c.options.method,'GET');const requestId=new URL(p,'https://synthetic.invalid').searchParams.get('request_id'),dto=queuedJobs.get(requestId);return dto?c.deliver(dto):c.deliver({error:{code:'de_classification_job_not_found'}},404);
  }
  assert.equal(c.options.method,'GET');
  if(p.includes('/differential-runs/'))return c.deliver(run);
  if(p.includes('/differential-results')&&!p.includes('/artifacts/'))return c.deliver(meta);
  if(p.includes('/differential-results/')&&p.includes('/artifacts/')){const a=o.parentPresentation.artifacts.find(a=>p.endsWith('/'+a.name));return c.binary(a.name,a.body);}
  if(p.includes('/differential-classifications')&&!p.includes('/files/')){if(!savedResult)return c.deliver({error:{code:'de_classification_version_not_found'}},404);if(mode==='upstream-unknown')return c.deliver({error:{code:'projects_unavailable'}},503);const value=structuredClone(o.derivedMetadata);if(mode==='wrong-parent'){value.source_result_id=id(90);value.source_binding.source_result_id=id(90);value.source_binding_sha256=hash(value.source_binding);}return c.deliver(value);}
  if(p.includes('/differential-classifications/')&&p.includes('/files/')){const a=o.artifacts.find(a=>p.endsWith('/'+a.name));return c.binary(a.name,a.body);}
  throw Error('Unexpected synthetic readonly route '+p);
 });
 const cryptoImpl={subtle:{digest(algorithm,body){if(holdHash&&!paused&&dec.decode(body).includes('"contract":"de_parent_binding_v0.1"')){paused=deferred();pausedBody=new Uint8Array(body);return paused.promise;}return webcrypto.subtle.digest(algorithm,body);}},randomUUID:()=>id(300+(++uuidCalls))};
 const raw=createRawTransport({projectId:meta.project_id,fetchImpl:browserFetch(h,identity),isCurrent:()=>!context.signal.aborted,preprocessingEnabled:true,preprocessingStorage:memory(),preprocessingArtifactScope:scope,cryptoImpl,getOptions:signal=>({method:'GET',signal,credentials:'same-origin',cache:'no-store',redirect:'error',headers:{'X-Cyomics-Account-Context':identity.view}})});
 const original=raw.differentialResults({projectId:meta.project_id,fileId:job.source.file_id,isCurrent:()=>!context.signal.aborted});let rowMapReads=0,jobChecks=0;
 const tx={...original,async rowMap(readJob,signal){assert.equal(readJob.job_id,job.job_id);rowMapReads++;const pin=job.source.artifacts[0];return{response:new Response(rowMap,{headers:{'content-type':'application/octet-stream','content-length':String(pin.bytes),'content-disposition':'attachment; filename="row-map.tsv"','x-cyomics-file-sha256':pin.sha256,'cache-control':'no-store','x-content-type-options':'nosniff','referrer-policy':'no-referrer'}}),checkCurrent:()=>assert.equal(signal.aborted,false)};}};
 const jobs={async check(id){assert.equal(id,job.request_id);jobChecks++;return true;},view:()=>({history:[{request_id:job.request_id,task:job}],code:null})};
 const source={file:{project_id:meta.project_id,file_id:job.source.file_id,sha256:job.source.source.source_sha256,filename:'synthetic-engineering-only.txt'}};
 const actor=createDifferentialResultOwner({projectId:meta.project_id,fileId:job.source.file_id,jobs,transport:tx,source:()=>source,isCurrent:()=>!context.signal.aborted,contextSignal:context.signal,cryptoImpl,...save,...timers,provenance:'synthetic_engineering_verification'}),d=dom(),stop=mountPrivateDifferentialResult(d.root,{client:actor});
 t.after(()=>{stop();actor.dispose();raw.disposePreprocessing();timers.advance(120000);});
 assert.equal(await actor.select(job.request_id),true);await until(()=>d.all().some(n=>Object.hasOwn(n.attrs,'data-lrt-source-row')));await flush();
 const set=(p='1.0',f='0.000')=>{for(const [control,value] of [['adjusted-p-max',p],['absolute-log2fc-min',f]]){const node=d.by('data-de-submit-control',control);node.value=value;node.fire('input');}};
 return{o,actor,d,scope,native,h,identity,save,timers,context,stop,set,queuedJobs,uuidCalls:()=>uuidCalls,unknown:v=>{unknownPost=v;},saved:v=>{savedResult=v;},async wait(){await until(()=>!actor.view().busy&&['queued','unknown','not_found','conflict','rejected','unavailable'].includes(actor.view().classificationJob.phase)&&!['loading'].includes(actor.view().classification.phase));if(actor.view().classification.phase==='ready')await until(()=>d.by('data-de-field','member-set'));await flush();return actor.view();}};
}

test('blank explicit thresholds submit once with original strings; queued admission is not a computed result',async t=>{
 const a=await setupSubmit(t),p=a.d.by('data-de-submit-control','adjusted-p-max'),f=a.d.by('data-de-submit-control','absolute-log2fc-min'),submit=a.d.by('data-de-submit-control','submit');
 assert.equal(p.value,'');assert.equal(f.value,'');assert.equal(submit.disabled,true);assert.equal(a.d.by('data-de-read-control','request-id'),undefined);assert.equal(a.uuidCalls(),0);
 const before=a.native.calls.length;a.set('NaN','1');assert.equal(submit.disabled,true);a.set('5e-2','1.00');assert.equal(submit.disabled,false);assert.equal(a.native.calls.length,before);assert.equal(a.uuidCalls(),0);
 assert.match(a.d.by('data-de-submit-field','source').textContent,/原分子组 β − 原分母组 α/);assert.ok(a.d.by('data-de-submit-field','source').textContent.includes(a.o.parentMetadata.result_id));
 const parentBefore=structuredClone(a.o.parentPresentation.artifacts);submit.click();const value=await a.wait();
 assert.equal(value.classificationJob.phase,'queued');assert.equal(value.classification.phase,'not_found');assert.equal(value.classificationJob.binding_verification,'service_summary_only');
 const posts=a.native.calls.filter(c=>c.options.method==='POST');assert.equal(posts.length,1);assert.equal(a.uuidCalls(),1);const body=JSON.parse(posts[0].body);
 assert.deepEqual(Object.keys(body).sort(),['parameters','request_id','source_result_id']);assert.deepEqual(body.parameters,{schema_version:'de_classification_v0.1',input_scale:'log2',adjusted_p_max:'5e-2',absolute_log2fc_min:'1.00'});assert.equal(body.source_result_id,a.o.parentMetadata.result_id);
 assert.equal(value.classificationJob.task.request_id,body.request_id);assert.equal(Object.keys(value.classificationJob.task).length,15);assert.equal(value.classificationJob.task.status,'queued');assert.equal(value.classificationJob.task.observation,'confirmed');assert.equal(Object.hasOwn(value.classificationJob.task,'result'),false);
 assert.match(a.d.by('data-de-submit-state','status').textContent,/排队不代表计算完成/);assert.match(a.d.by('data-de-submit-field','binding-verification').textContent,/摘要由服务返回.*完整证明待保存结果读取时校验/);assert.equal(a.d.by('data-de-field','member-set'),undefined);
 assert.deepEqual(a.o.parentPresentation.artifacts,parentBefore);assert.equal(a.save.clicks.length,0);assert.equal(submit.disabled,true);submit.click();await flush();assert.equal(a.uuidCalls(),1);assert.equal(a.native.calls.filter(c=>c.options.method==='POST').length,1);
 t.diagnostic('Synthetic BFF only: inputs empty/no execution until explicit click, exact3-key request and original numeric strings/TSV SHA preserved, exactly one UUID/POST. Queued15-key history has no result/runtime; missing saved result stays pending and opaque binding is honestly marked. Parent P/BH bytes untouched.');
});

test('unknown POST retains one original intent; manual checks only GET that request and never recreate or resubmit',async t=>{
 const a=await setupSubmit(t);a.unknown(true);a.set('5e-2','1.0');a.d.by('data-de-submit-control','submit').click();let value=await a.wait();
 assert.equal(value.classificationJob.phase,'unknown');assert.equal(value.classificationJob.has_reference,true);assert.equal(a.uuidCalls(),1);const post=a.native.calls.find(c=>c.options.method==='POST'),body=JSON.parse(post.body);
 assert.equal(a.d.by('data-de-submit-control','adjusted-p-max').disabled,true);assert.equal(a.d.by('data-de-submit-control','submit').disabled,true);assert.match(a.d.by('data-de-submit-state','status').textContent,/原请求.*暂勿重新提交/);
 for(let i=0;i<2;i++){
  a.d.by('data-de-submit-control','check').click();value=await a.wait();assert.equal(value.classificationJob.phase,'queued');assert.equal(value.classificationJob.task.request_id,body.request_id);assert.equal(value.classification.phase,'not_found');
 }
 const jobCalls=a.native.calls.filter(c=>c.options.path.includes('/classification-jobs'));assert.equal(jobCalls.length,3);assert.equal(jobCalls[0].options.method,'POST');for(const call of jobCalls.slice(1)){assert.equal(call.options.method,'GET');assert.equal(call.options.path,'/api/v1/projects/'+a.o.parentMetadata.project_id+'/classification-jobs?request_id='+body.request_id);assert.equal(call.body,undefined);}
 assert.equal(a.uuidCalls(),1);assert.equal(a.native.calls.filter(c=>c.options.method==='POST').length,1);const count=a.native.calls.length;a.timers.advance(10000);await flush();assert.equal(a.native.calls.length,count,'No polling or automatic retry');
 a.context.abort();assert.equal(a.actor.view().classificationJob.has_reference,false);assert.equal(a.actor.view().classificationJob.parameters,null);
 t.diagnostic('Mock backend commits admission then returns unknown504. Original request/parameters survive in private actor context; repeated explicit checks use identical GET only, no second POST/new UUID/poll/R. Session abort clears private visible references.');
});

test('own original request automatically joins saved derived result; current-context remount restores check without manual UUID',async t=>{
 const a=await setupSubmit(t);a.saved(true);a.set('1.0','0.000');a.d.by('data-de-submit-control','submit').click();let value=await a.wait();
 assert.equal(value.classificationJob.phase,'queued');assert.equal(value.classification.phase,'ready');assert.equal(value.classificationJob.binding_verification,'checked_parent_proof');
 const body=JSON.parse(a.native.calls.find(c=>c.options.method==='POST').body);assert.equal(value.classification.meta.request_id,body.request_id);assert.equal(value.classification.meta.parameters_sha256,value.classificationJob.task.parameters_sha256);assert.equal(value.classification.meta.source_binding_sha256,value.classificationJob.task.source_binding_sha256);
 const lookup=a.native.calls.find(c=>c.options.path.includes('/differential-classifications?'));assert.equal(lookup.options.path,'/api/v1/projects/'+a.o.parentMetadata.project_id+'/differential-classifications?request_id='+body.request_id);
 assert.match(a.d.by('data-de-submit-state','status').textContent,/对应分类结果已核对/);assert.doesNotMatch(a.d.by('data-de-submit-state','status').textContent,/尚无|排队不代表/);assert.match(a.d.by('data-de-field','provenance').textContent,/工程验证数据/);assert.ok(a.d.by('data-de-field','member-set'));
 a.d.by('data-de-submit-control','check').click();assert.equal(a.d.by('data-de-field','member-set'),undefined,'New check clears old child before the queued GET can finish');await until(()=>a.actor.view().classification.phase==='ready'&&!a.actor.view().busy&&a.d.by('data-de-field','member-set'));
 a.stop();const d=dom(),stop=mountPrivateDifferentialResult(d.root,{client:a.actor});t.after(stop);assert.equal(await a.actor.select(a.o.parentMetadata.request_id),true);await until(()=>d.all().some(n=>Object.hasOwn(n.attrs,'data-lrt-source-row')));await flush();
 assert.equal(d.by('data-de-submit-control','adjusted-p-max').value,'1.0');assert.equal(d.by('data-de-submit-control','absolute-log2fc-min').value,'0.000');assert.equal(d.by('data-de-read-control','request-id'),undefined);
 d.by('data-de-submit-control','check').click();await until(()=>a.actor.view().classification.phase==='ready'&&!a.actor.view().busy&&d.by('data-de-field','member-set'));assert.equal(a.uuidCalls(),1);assert.equal(a.native.calls.filter(c=>c.options.method==='POST').length,1);
 t.diagnostic('Synthetic saved derivedDTO+six are automatically read by own admission request, joined to queue parameters/core/parent binding and verified by existing read chain. No manual UUID input/default research claim. Same private actor context remount/reselect restores reference+original strings; no cross-page-refresh/service-restart joblist claim.');
});
