// Three new presentation checks only. Derived files/metadata below are explicitly
// hand-authored synthetic fixtures, NOT claims of new R execution or persistence.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash,webcrypto} from 'node:crypto';
import {dom,deferred,flush} from './support/preprocess-parameters-dom.mjs';
import {DIFFERENTIAL_METHOD} from '../differential-job-contract.mjs';
import {DIFFERENTIAL_RESULT_NAMES} from '../differential-result-contract.mjs';
import {canonicalJSON} from '../sample-design-contract.mjs';
import {mountDEClassificationSummary,DE_CLASSIFICATION_ARTIFACTS} from '../de-classification-summary.js';
const enc=new TextEncoder(),dec=new TextDecoder(),id=n=>String(n).padStart(8,'0')+'-1111-4111-8111-111111111111';
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
async function load(t,o=input()){const d=dom(),c=mountDEClassificationSummary(d.root,o);t.after(()=>c.dispose());assert.equal(await c.ready,true);return{d,c,o};}
const field=(d,name)=>d.by('data-de-field',name)?.textContent;
const rowNodes=d=>d.all().filter(n=>Object.hasOwn(n.attrs,'data-de-source-row'));
const cell=(row,name)=>row.children.find(n=>n.attrs['data-de-cell']===name)?.textContent;
function repack(o,name,change){const i=o.artifacts.findIndex(a=>a.name===name);o.artifacts[i]=pack(name,change(dec.decode(o.artifacts[i].body)));o.derivedMetadata.artifacts=o.artifacts.map(pin);o.derivedMetadata.manifest_sha256=hash(o.derivedMetadata.artifacts);}

test('R member preview and original tokens distinguish classified nonempty/0DE from blocked3/4',async t=>{
 const h=await load(t);assert.equal(h.c.view().DE_members_evaluated,true);assert.equal(h.c.view().counts.selected_DE_rows,6);
 assert.match(field(h.d,'provenance'),/工程验证数据.*不能作为科研结论/);assert.match(field(h.d,'direction'),/原分子组 β − 原分母组 α/);assert.match(field(h.d,'parameters'),/1\.0.*0\.000/);
 const rows=rowNodes(h.d);assert.deepEqual(rows.map(r=>r.attrs['data-de-source-row']),['1','2','4','8','9','12']);
 assert.deepEqual(rows.map(r=>cell(r,'protein_ids_raw')),['  SYN_DUP  ','','SYN_UNCHANGED','  SYN_DUP  ','  SYN_MULTI_A;SYN_MULTI_B  ','SYN_PARTIAL']);
 assert.equal(cell(rows[1],'FC'),'7.99999999999999');assert.equal(cell(rows[0],'P.Value'),'0.00463687301429682');assert.equal(cell(rows[2],'log2FC'),'8.88178419700125e-16');
 assert.match(field(h.d,'member-set'),new RegExp(id(22)));assert.equal(h.d.all().filter(n=>Object.hasOwn(n.attrs,'data-de-download')&&n.disabled).length,6);
 const withReference=await load(t,input({withMemberSet:true}));assert.match(field(withReference.d,'member-set'),new RegExp(id(22)));
 for(const mode of ['zero','no-testable','warning']){
  const f=await load(t,input({mode})),v=f.c.view();assert.equal(rowNodes(f.d).length,0);
  if(mode==='zero'){assert.equal(v.DE_members_evaluated,true);assert.equal(v.counts.selected_DE_rows,0);assert.match(field(f.d,'empty'),/分类成功，DE 为 0.*富集暂无成员/);assert.match(field(f.d,'parameters'),/1e-200.*1e\+9/);assert.equal(v.counts.NS,6);}
  else{assert.equal(v.DE_members_evaluated,false);for(const k of ['Up','Down','NS','selected_DE_rows'])assert.equal(v.counts[k],null);assert.match(field(f.d,'count-Up'),/NA（未评估）/);assert.match(field(f.d,'member-set'),/不能用作成功空集/);assert.equal(field(f.d,'empty'),undefined);assert.equal(v.counts.not_testable,mode==='no-testable'?7:1);assert.equal(v.counts.not_evaluated,mode==='warning'?6:0);}
 }
 t.diagnostic('Original R label tokens only; selected stable subsequence preserves duplicate/blank/semicolon IDs and original numeric text.0DE is evaluated success; blocked3/4 retain NA counts and are never enrichment members. No download callback means6 disabled buttons.');
});

test('wrong parent/binding/hash, altered source prefix and inconsistent R counts reject and clear old content',async t=>{
 const h=await load(t);
 const mutations=[
  o=>{o.derivedMetadata.source_result_id=id(90);},
  o=>{o.derivedMetadata.source_manifest_sha256='9'.repeat(64);},
  o=>{o.derivedMetadata.source_binding.input_snapshot_sha256='9'.repeat(64);o.derivedMetadata.source_binding_sha256=hash(o.derivedMetadata.source_binding);},
  o=>{o.artifacts[0].body[0]^=1;},
  o=>{repack(o,'classification.tsv',s=>s.replace('1\t  SYN_DUP  ','1\tCHANGED_ID'));},
  o=>{o.derivedMetadata.counts.Up=0;},
 ];
 for(const mutate of mutations){const o=input();mutate(o);assert.equal(await h.c.update(o),false);assert.equal(h.c.view().phase,'invalid');assert.equal(h.c.view().counts,null);assert.equal(rowNodes(h.d).length,0);assert.equal(h.d.all().filter(n=>Object.hasOwn(n.attrs,'data-de-download')).length,0);}
 t.diagnostic('Wrong result/full parent manifest/rehashed wrong input snapshot in full source binding, corrupted byte SHA, rehashed altered original prefix and audit/count inconsistency all fail closed. Backend request/admission SHA relationship is rejoined; no expected-SHA self-comparison or JS threshold classifier.');
});

test('context replacement/abort and late validation/download completions cannot restore old result or success',async t=>{
 const d=dom(),held=deferred();let firstDigest=true;
 const slow=input();slow.cryptoImpl={subtle:{digest(algorithm,bytes){if(firstDigest){firstDigest=false;return held.promise;}return webcrypto.subtle.digest(algorithm,bytes);}}};
 const c=mountDEClassificationSummary(d.root,slow),oldReady=c.ready;t.after(()=>c.dispose());
 const fresh=input();fresh.derivedMetadata.derived_version_id=id(91);fresh.derivedMetadata.member_set.derived_version_id=id(91);
 let oldSettled=false;void oldReady.then(()=>{oldSettled=true;});
 const nextReady=c.update(fresh);assert.equal(rowNodes(d).length,0);assert.equal(c.view().phase,'verifying');assert.equal(await nextReady,true);assert.equal(oldSettled,false,'Uncancellable digest must physically settle before old ready completes');
 held.resolve(await webcrypto.subtle.digest('SHA-256',enc.encode(canonicalJSON(slow.parentMetadata.method))));assert.equal(await oldReady,false);assert.equal(c.view().derived_version_id,id(91));
 const completion=deferred(),calls=[],a=new AbortController(),o=input({onDownload:info=>{calls.push(info);return completion.promise;}});o.contextSignal=a.signal;
 const h=await load(t,o);h.d.by('data-de-download','selected-DE.tsv').click();assert.equal(calls.length,1);assert.equal(calls[0].derived_version_id,id(20));assert.equal(calls[0].source_result_id,id(8));assert.equal(Object.hasOwn(calls[0],'body'),false);
 assert.deepEqual(calls[0].pin,{bytes:o.artifacts.find(a=>a.name==='selected-DE.tsv').bytes,sha256:o.artifacts.find(a=>a.name==='selected-DE.tsv').sha256});
 a.abort();assert.equal(calls[0].signal.aborted,true);assert.equal(h.c.view().phase,'disposed');assert.equal(h.d.root.textContent,'');assert.equal(h.d.listenerCount(),0);
 completion.resolve({status:'handed_off'});await flush();assert.equal(h.d.root.textContent,'');assert.equal(h.d.listenerCount(),0);
 const mutated=await load(t);mutated.o.parentPresentation.verifiedOwnerContext.source.source_sha256='9'.repeat(64);assert.equal(mutated.c.view().phase,'disposed');assert.equal(mutated.d.root.textContent,'');
 const delivered=await load(t,input({onDownload:async()=>({status:'handed_off'})}));delivered.d.by('data-de-download','classification.tsv').click();await flush();assert.match(delivered.d.root.textContent,/交给浏览器保存.*浏览器下载状态/);assert.doesNotMatch(delivered.d.root.textContent,/磁盘已保存/);
 t.diagnostic('Pending old digest cannot publish over a new derived version; Owner abort clears DOM/listeners and aborts download signal; late callback cannot republish. In-place Owner context changes dispose. Downloads delegate only exact refs/pins to supplied Owner callback and make no disk-save claim.');
});
