// New readonly slice only. Synthetic BFF/native wire + verified-job and original row-map adapters are mocked; no real Auth/backend/R/Owner HTTP. Original suites are not imported or run.
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
import {until} from './support/preprocess-owner-setup.mjs';

async function setupRead(t){
 const o=input(),meta=o.parentMetadata,native=wire(),h=harness(t,{useServer:true,differentialResultsEnabled:true,differentialResultsRequest:native.request,deClassificationsEnabled:true,deClassificationsRequest:native.request}),identity=await h.signed();
 const scope=createQCArtifactScope(),context=new AbortController(),save=saver(),timers=clock();let mode='normal',holdHash=false,paused=null,pausedBody=null;
 const rowEntries=o.parentPresentation.rows,quote=v=>'"'+v.replaceAll('"','""')+'"';let work=0;
 const rowMap=enc.encode('"source_record_number"\t"protein_ids_raw"\t"kept"\t"work_row_ordinal"\n'+Array.from({length:12},(_,i)=>{const row=rowEntries.find(r=>r.source_row_ordinal===i+1);return[String(i+1),quote(row?row.protein_ids_raw:'SYN_SKIP_'+i),row?'TRUE':'FALSE',row?String(++work):'NA'].join('\t');}).join('\n')+'\n');
 const job={job_id:meta.job_id,project_id:meta.project_id,request_id:meta.request_id,created_at:'2026-11-18T23:00:00.000000Z',binding_sha256:meta.admission_binding_sha256,selection:meta.selection,method:meta.method,input_manifest:meta.input_manifest,source:{analysis_ready:false,file_id:o.parentPresentation.verifiedOwnerContext.source.file_id,parent_dataset_version_id:meta.selection.contrast.dataset_version_id,created_by:meta.created_by,source:{source_sha256:o.parentPresentation.verifiedOwnerContext.source.source_sha256,row_count:12},artifacts:[pin(pack('row-map.tsv',rowMap))]}};
 const run={schemaVersion:1,contract:'differential_run_v0.1',job_id:job.job_id,attempt_id:meta.attempt_id,registration_id:meta.registration_id,project_id:meta.project_id,status:'succeeded',observation:'confirmed',revision:3,started_at:meta.created_at,finished_at:'2026-11-19T00:00:02.000000Z',result_id:meta.result_id,error_code:null};
 native.reply(c=>{
  const p=c.options.path;assert.equal(c.options.method,'GET');
  if(p.includes('/differential-runs/'))return c.deliver(run);
  if(p.includes('/differential-results')&&!p.includes('/artifacts/'))return c.deliver(meta);
  if(p.includes('/differential-results/')&&p.includes('/artifacts/')){const a=o.parentPresentation.artifacts.find(a=>p.endsWith('/'+a.name));return c.binary(a.name,a.body);}
  if(p.includes('/differential-classifications')&&!p.includes('/files/')){if(mode==='upstream-unknown')return c.deliver({error:{code:'projects_unavailable'}},503);const value=structuredClone(o.derivedMetadata);if(mode==='wrong-parent'){value.source_result_id=id(90);value.source_binding.source_result_id=id(90);value.source_binding_sha256=hash(value.source_binding);}return c.deliver(value);}
  if(p.includes('/differential-classifications/')&&p.includes('/files/')){const a=o.artifacts.find(a=>p.endsWith('/'+a.name));return c.binary(a.name,a.body);}
  throw Error('Unexpected synthetic readonly route '+p);
 });
 const cryptoImpl={subtle:{digest(algorithm,body){if(holdHash&&!paused&&dec.decode(body).includes('"contract":"de_parent_binding_v0.1"')){paused=deferred();pausedBody=new Uint8Array(body);return paused.promise;}return webcrypto.subtle.digest(algorithm,body);}},randomUUID:()=>{throw Error('Readonly flow must never allocate a request ID');}};
 const raw=createRawTransport({projectId:meta.project_id,fetchImpl:browserFetch(h,identity),isCurrent:()=>!context.signal.aborted,preprocessingEnabled:true,preprocessingArtifactScope:scope,cryptoImpl,getOptions:signal=>({method:'GET',signal,credentials:'same-origin',cache:'no-store',redirect:'error',headers:{'X-Cyomics-Account-Context':identity.view}})});
 const original=raw.differentialResults({projectId:meta.project_id,fileId:job.source.file_id,isCurrent:()=>!context.signal.aborted});let rowMapReads=0,jobChecks=0;
 const tx={...original,async rowMap(readJob,signal){assert.equal(readJob.job_id,job.job_id);rowMapReads++;const pin=job.source.artifacts[0];return{response:new Response(rowMap,{headers:{'content-type':'application/octet-stream','content-length':String(pin.bytes),'content-disposition':'attachment; filename="row-map.tsv"','x-cyomics-file-sha256':pin.sha256,'cache-control':'no-store','x-content-type-options':'nosniff','referrer-policy':'no-referrer'}}),checkCurrent:()=>assert.equal(signal.aborted,false)};}};
 const jobs={async check(id){assert.equal(id,job.request_id);jobChecks++;return true;},view:()=>({history:[{request_id:job.request_id,task:job}],code:null})};
 const source={file:{project_id:meta.project_id,file_id:job.source.file_id,sha256:job.source.source.source_sha256,filename:'synthetic-engineering-only.txt'}};
 const actor=createDifferentialResultOwner({projectId:meta.project_id,fileId:job.source.file_id,jobs,transport:tx,source:()=>source,isCurrent:()=>!context.signal.aborted,contextSignal:context.signal,cryptoImpl,...save,...timers,provenance:'synthetic_engineering_verification'}),d=dom(),stop=mountPrivateDifferentialResult(d.root,{client:actor});
 t.after(()=>{stop();actor.dispose();raw.disposePreprocessing();timers.advance(120000);});
 assert.equal(await actor.select(job.request_id),true);await until(()=>d.all().some(n=>Object.hasOwn(n.attrs,'data-lrt-source-row')));await flush();
 const paste=()=>{const n=d.by('data-de-submit-control','adjusted-p-max');n.value='1.0';n.fire('input');};
 async function read(){paste();await actor.readClassification(o.derivedMetadata.request_id);await until(()=>['ready','unavailable','not_found'].includes(actor.view().classification.phase));if(actor.view().classification.phase==='ready')await until(()=>d.by('data-de-field','count-Up'));return actor.view().classification;}
 return{o,actor,d,scope,native,h,identity,save,timers,context,stop,paste,read,rowMapReads:()=>rowMapReads,jobChecks:()=>jobChecks,mode:v=>{mode=v;},hold(){holdHash=true;},paused:()=>paused,async settleHash(){paused.resolve(await webcrypto.subtle.digest('SHA-256',pausedBody));}};
}

test('explicit original UUID reads fresh parent+six via private BFF; all six downloads re-read fixed proof and use original lease',async t=>{
 const a=await setupRead(t),start=a.native.calls.length,rows=a.rowMapReads();a.paste();assert.equal(a.native.calls.length,start,'Editing threshold draft does not issue requests');
 assert.equal((await a.read()).phase,'ready');assert.equal(a.rowMapReads(),rows+1);assert.ok(a.jobChecks()>=2);assert.equal(a.scope.busy(),false);
 const calls=a.native.calls.slice(start),classCalls=calls.filter(c=>c.options.path.includes('/differential-classifications'));
 assert.equal(classCalls[0].options.path,'/api/v1/projects/'+a.o.parentMetadata.project_id+'/differential-classifications?request_id='+a.o.derivedMetadata.request_id);
 assert.deepEqual(classCalls.slice(1).map(c=>c.options.path.split('/files/')[1]),[...DE_CLASSIFICATION_ARTIFACTS]);assert.match(a.d.by('data-de-field','provenance').textContent,/工程验证数据/);
 for(const name of DE_CLASSIFICATION_ARTIFACTS){
  const before=a.native.calls.length,rm=a.rowMapReads(),clicked=a.save.clicks.length;a.d.by('data-de-download',name).click();await until(()=>a.save.active.size>0&&a.save.clicks.length>clicked&&a.native.calls.length>=before+14&&!a.actor.view().busy);await flush();
  const fresh=a.native.calls.slice(before);assert.equal(a.rowMapReads(),rm+1);assert.equal(fresh.filter(c=>c.options.path.includes('/differential-classifications/')&&!c.options.path.includes('/files/'))[0].options.path,'/api/v1/projects/'+a.o.parentMetadata.project_id+'/differential-classifications/'+a.o.derivedMetadata.derived_version_id);
  assert.equal(fresh.filter(c=>c.options.path.includes('/files/')).length,6);assert.equal(fresh.some(c=>c.options.path.includes('?request_id=')),false);
  assert.deepEqual(new Uint8Array(await [...a.save.active.values()][0].arrayBuffer()),a.o.artifacts.find(a=>a.name===name).body);
  assert.equal(a.scope.busy(),true,'Browser handoff retains the existing lease until URL cleanup');a.timers.advance(1001);assert.equal(a.scope.busy(),false);
 }
 for(const call of a.native.calls){assert.equal(call.options.method,'GET');assert.equal(call.options.headers.Authorization,'Bearer ACCESS_A');assert.equal(call.options.headers.Cookie,undefined);assert.equal(call.options.hostname,'127.0.0.1');assert.equal(call.options.port,57430);}
 assert.match(a.d.root.textContent,/浏览器下载状态/);assert.doesNotMatch(a.d.root.textContent,/磁盘已保存/);
 t.diagnostic('Synthetic authenticated BFF only; verified-job and original rowmap adapters mocked. Explicit UUID only, no writes/IDs/poll/member endpoint. Fresh parent job/meta/rowmap/five precede six reads; six fixed-version downloads each revalidate complete proof/files before browser handoff under shared lease.');
});

test('context abort clears saved classification and pending uncancellable proof hash holds original shared lease until settlement',async t=>{
 const a=await setupRead(t);assert.equal((await a.read()).phase,'ready');a.hold();a.paste();const pending=a.actor.readClassification(a.o.derivedMetadata.request_id);await until(()=>a.paused());assert.equal(a.scope.busy(),true);
 a.context.abort();assert.equal(a.actor.view().phase,'disposed');assert.equal(await pending,false);assert.equal(a.scope.busy(),true);assert.equal(a.scope.acquire(()=>{}),null);
 assert.equal(a.d.by('data-de-field','member-set'),undefined);assert.equal(a.d.by('data-de-submit-control','adjusted-p-max').value,'');
 await a.settleHash();await flush();assert.equal(a.scope.busy(),false);assert.equal(a.actor.view().classification.meta,null);a.stop();assert.equal(a.d.listenerCount(),0);
 t.diagnostic('No late republish after Owner context abort; old class/UUID cleared immediately. Logical operation ends false while shared lease remains busy through physical SHA settlement; original lifecycle retained.');
});

test('validly hashed wrong-parent saved version is rejected before six downloads and clears previous classification',async t=>{
 const a=await setupRead(t);assert.equal((await a.read()).phase,'ready');const start=a.native.calls.length;a.mode('wrong-parent');assert.equal((await a.read()).phase,'unavailable');
 assert.equal(a.actor.view().classification.meta,null);assert.equal(a.d.by('data-de-field','member-set'),undefined);assert.equal(a.d.all().filter(n=>Object.hasOwn(n.attrs,'data-de-download')).length,0);
 assert.equal(a.native.calls.slice(start).filter(c=>c.options.path.includes('/files/')).length,0);assert.equal(a.scope.busy(),false);
 a.mode('upstream-unknown');const failed=await a.h.start('/api/v1/projects/'+a.o.parentMetadata.project_id+'/differential-classifications?request_id='+a.o.derivedMetadata.request_id,{cookie:a.identity.cookie,view:a.identity.view}).promise;
 assert.equal(failed.status,503);assert.deepEqual(JSON.parse(failed.wire),{error:{code:'de_classification_results_unavailable'}});
 t.diagnostic('The wrong source_result and binding are consistently rehashed; current verified parent still differs, so no six-file fetch or stale class display. Parent result itself remains verified. Unknown upstream503 projects_unavailable is safely mapped to the frozen generic classification error.');
});
