import {exact,uuid,freeze,canonicalJSON} from './sample-design-contract.mjs';
import {differentialResultReply,DIFFERENTIAL_RESULT_NAMES} from './differential-result-contract.mjs';

// Internal presentation adapter only. Owner authorization and R validation precede it.
export const DE_CLASSIFICATION_ARTIFACTS=freeze(['classification-audit.tsv','classification-parameters.tsv','classification.tsv','selected-DE.tsv','source-method-audit.tsv','source-test-set.tsv']);
const CONTRACT='de_classification_v0.1',VERSION='de_classification_version_v0.1',MAX_FILE=27262976,MAX_TOTAL=134217728;
const SOURCE_HEADER='source_row_ordinal protein_ids_raw contrast_id test_status reason log2FC FC FC_reason AveExpr moderated_t df_residual df_total P.Value adj.P.Val n_obs_num n_obs_den n_fit_num n_fit_den'.split(' ');
const HEADER=[...SOURCE_HEADER,'classification','selected_DE'];
const PARAM_KEYS=['schema_version','input_scale','adjusted_p_max','absolute_log2fc_min'];
const COUNT_KEYS=['Up','Down','NS','not_testable','not_evaluated','selected_DE_rows'];
const AUDIT_KEYS=('schema_version classification_status DE_members_evaluated source_contract source_output_status source_warning_count source_contrast_id source_numerator source_denominator contrast_direction threshold_scale adjusted_p_max absolute_log2fc_min boundary_rule zero_log2FC input_rows source_testable_rows source_not_testable_rows Up Down NS not_testable not_evaluated selected_DE_rows source_bh_method source_bh_m source_bh_n source_bh_scope FC_column statistical_recomputation row_identity row_order specific_detection').split(' ');
const CORE=freeze({contract:CONTRACT,script_pin:{bytes:10335,sha256:'65e24d287a1546dd7a9fa0480f2a899d4d91eb4b11cda7a4802256fb90f3c421'},validator_pin:{bytes:12074,sha256:'2d37d97fcbbe6bb5c323c887b8c168e68c19945904dd3fe5bc9b247174a5d584'}});
const VERSION_KEYS=['schemaVersion','contract','derived_version_id','request_id','project_id','source_result_id','source_manifest_sha256','source_binding_sha256','source_binding','parameters','parameters_sha256','core','status','DE_members_evaluated','counts','artifacts','manifest_sha256','created_at','created_by','source_analysis_ready','member_set'];
const BINDING_KEYS=('schemaVersion contract owner_id project_id source_result_id source_job_id source_attempt_id source_registration_id source_request_id source_contrast_id admission_binding_sha256 input_snapshot_sha256 source_metadata_sha256 source_receipt_sha256 source_manifest_sha256 source_artifacts source_status source_warning_count source_method_sha256 contrast_direction').split(' ');
const need=ok=>{if(!ok)throw Error('de_classification_presentation_invalid');};
const hash=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v);
const count=v=>Number.isSafeInteger(v)&&v>=0&&v<=10000;
const same=(a,b)=>canonicalJSON(a)===canonicalJSON(b);
const numeric=v=>typeof v==='string'&&/^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$/.test(v)&&Number.isFinite(Number(v));
const text=body=>{need(body.length>0&&body.at(-1)===10&&!body.includes(0)&&!body.includes(13)&&!(body[0]===239&&body[1]===187&&body[2]===191));return new TextDecoder('utf-8',{fatal:true}).decode(body);};
function table(body,header,max=10000){const lines=text(body).split('\n');lines.pop();const rows=lines.map(line=>line.split('\t'));need(rows.length>=1&&rows.length<=max+1&&same(rows[0],header)&&rows.every(r=>r.length===header.length));return rows.slice(1);}
function keyValues(body,keys,cap){need(body.byteLength<=cap);const rows=table(body,['key','value'],keys.length);need(same(rows.map(r=>r[0]),keys));return Object.fromEntries(rows);}
function copies(artifacts,names){
 need(Array.isArray(artifacts)&&artifacts.length===names.length&&new Set(artifacts.map(a=>a.name)).size===names.length);
 let total=0;const files=new Map();
 for(const a of artifacts){need(exact(a,['name','body','bytes','sha256'])&&names.includes(a.name)&&a.body instanceof Uint8Array&&Number.isSafeInteger(a.bytes)&&a.bytes>=1&&a.bytes<=MAX_FILE&&a.body.byteLength===a.bytes&&hash(a.sha256));total+=a.bytes;files.set(a.name,{name:a.name,bytes:a.bytes,sha256:a.sha256,body:new Uint8Array(a.body)});}
 need(total<=MAX_TOTAL);return files;
}
const bytesEqual=(a,b)=>a.byteLength===b.byteLength&&a.every((v,i)=>v===b[i]);
function ownerKey(o){return canonicalJSON({context:o.parentPresentation?.verifiedOwnerContext,parent:o.parentMetadata,derived:o.derivedMetadata,rows:o.parentPresentation?.rows});}
function capture(o){
 const parent=differentialResultReply({kind:'differential-result',project_id:o.parentMetadata?.project_id,result_id:o.parentMetadata?.result_id,job_id:null},200,o.parentMetadata);
 const context=structuredClone(o.parentPresentation?.verifiedOwnerContext),rows=structuredClone(o.parentPresentation?.rows),version=structuredClone(o.derivedMetadata);
 need(exact(context,['project_id','job_id','binding_sha256','selection','source','provenance'])&&context.project_id===parent.project_id&&context.job_id===parent.job_id&&context.binding_sha256===parent.admission_binding_sha256&&same(context.selection,parent.selection));
 need(['research','synthetic_engineering_verification','owner_verified_saved_input'].includes(context.provenance));
 need(exact(context.source,['file_id','parent_dataset_version_id','source_sha256','filename'])&&uuid(context.source.file_id)&&context.source.parent_dataset_version_id===parent.selection.contrast.dataset_version_id&&hash(context.source.source_sha256)&&typeof context.source.filename==='string');
 need(exact(version,VERSION_KEYS)&&version.schemaVersion===1&&version.contract===VERSION&&['derived_version_id','request_id','project_id','source_result_id'].every(k=>uuid(version[k])));
 need(version.project_id===parent.project_id&&version.source_result_id===parent.result_id&&version.source_manifest_sha256===parent.manifest_sha256&&hash(version.source_binding_sha256));
 need(version.created_by===parent.created_by&&version.source_analysis_ready===false);
 const binding=version.source_binding;need(exact(binding,BINDING_KEYS)&&binding.schemaVersion===1&&binding.contract==='de_parent_binding_v0.1'&&hash(binding.input_snapshot_sha256));
 need(typeof version.created_at==='string'&&/Z$/.test(version.created_at)&&Number.isFinite(Date.parse(version.created_at))&&Date.parse(version.created_at)>=Date.parse(parent.created_at));
 need(same(version.core,CORE)&&hash(version.parameters_sha256)&&hash(version.manifest_sha256)&&exact(version.parameters,PARAM_KEYS));
 const p=version.parameters;need(p.schema_version===CONTRACT&&p.input_scale==='log2'&&numeric(p.adjusted_p_max)&&Number(p.adjusted_p_max)>0&&Number(p.adjusted_p_max)<=1&&numeric(p.absolute_log2fc_min)&&Number(p.absolute_log2fc_min)>=0);
 need(exact(version.counts,COUNT_KEYS));
 const evaluated=version.status==='CLASSIFIED';
 need(['CLASSIFIED','BLOCKED_SOURCE_NO_TESTABLE_ROWS','BLOCKED_SOURCE_REVIEW_REQUIRED'].includes(version.status)&&version.DE_members_evaluated===evaluated);
 for(const k of COUNT_KEYS)need(!evaluated&&['Up','Down','NS','selected_DE_rows'].includes(k)?version.counts[k]===null:count(version.counts[k]));
 const status=({COMPUTED_CANDIDATE:'CLASSIFIED',NO_TESTABLE_ROWS:'BLOCKED_SOURCE_NO_TESTABLE_ROWS',REVIEW_REQUIRED_LIMMA_WARNING:'BLOCKED_SOURCE_REVIEW_REQUIRED'})[parent.scientific_status];
 need(version.status===status&&(!evaluated||parent.warning_count===0));
 need(Array.isArray(rows)&&rows.length>=1&&rows.length<=10000);let previous=0;
 for(const r of rows){need(exact(r,['source_row_ordinal','protein_ids_raw'])&&Number.isSafeInteger(r.source_row_ordinal)&&r.source_row_ordinal>previous&&typeof r.protein_ids_raw==='string');previous=r.source_row_ordinal;}
 return{parent,context:freeze(context),rows:freeze(rows),version:freeze(version),parentFiles:copies(o.parentPresentation.artifacts,DIFFERENTIAL_RESULT_NAMES),files:copies(o.artifacts,DE_CLASSIFICATION_ARTIFACTS)};
}
async function validate(w,check){
 const {parent,context,version,rows,parentFiles,files}=w.input,crypto=w.options.cryptoImpl||globalThis.crypto;
 // Digest itself is uncancellable: ready remains pending until its physical
 // settlement even after logical clear. No raced early completion/lease release.
 const sha=async bytes=>{check();w.hashes++;try{const digest=await crypto.subtle.digest('SHA-256',bytes);check();return[...new Uint8Array(digest)].map(v=>v.toString(16).padStart(2,'0')).join('');}finally{w.hashes--;}};
 const shaJSON=value=>sha(new TextEncoder().encode(canonicalJSON(value)));
 const b=version.source_binding,pair=context.selection.contrast.contrasts[0];
 need(b.owner_id===parent.created_by&&b.project_id===parent.project_id&&b.source_result_id===parent.result_id&&b.source_job_id===parent.job_id&&b.source_attempt_id===parent.attempt_id&&b.source_registration_id===parent.registration_id&&b.source_request_id===parent.request_id&&b.source_contrast_id===pair.contrast_id&&b.admission_binding_sha256===parent.admission_binding_sha256&&b.source_receipt_sha256===parent.receipt_sha256&&b.source_manifest_sha256===parent.manifest_sha256&&same(b.source_artifacts,parent.artifacts)&&b.source_status===parent.scientific_status&&b.source_warning_count===parent.warning_count&&b.contrast_direction==='numerator_minus_denominator');
 const methodSHA=await shaJSON(parent.method);need(b.source_method_sha256===methodSHA&&b.source_metadata_sha256===await shaJSON(parent)&&version.source_binding_sha256===await shaJSON(b));
 // Exact differential_jobs.request_sha / binding_sha canonical key names.
 const requestSHA=await shaJSON({owner_id:parent.created_by,project_id:parent.project_id,request:{...parent.selection,request_id:parent.request_id}});
 const admissionSHA=await shaJSON({input_manifest_sha256:await shaJSON(parent.input_manifest),input_snapshot_sha256:b.input_snapshot_sha256,method_sha256:methodSHA,request_sha256:requestSHA});
 need(admissionSHA===parent.admission_binding_sha256);
 const manifest=(pins,names)=>{need(Array.isArray(pins)&&pins.length===names.length);for(const [i,p] of pins.entries())need(exact(p,['name','bytes','sha256'])&&p.name===names[i]&&Number.isSafeInteger(p.bytes)&&p.bytes>=1&&p.bytes<=MAX_FILE&&hash(p.sha256));return pins;};
 for(const [meta,artifacts,names] of [[parent,parentFiles,DIFFERENTIAL_RESULT_NAMES],[version,files,DE_CLASSIFICATION_ARTIFACTS]]){
  manifest(meta.artifacts,names);need(await sha(new TextEncoder().encode(canonicalJSON(meta.artifacts)))===meta.manifest_sha256);
  for(const pin of meta.artifacts){const file=artifacts.get(pin.name);need(same(pin,{name:file.name,bytes:file.bytes,sha256:file.sha256})&&await sha(file.body)===pin.sha256);}
 }
 need(bytesEqual(files.get('source-method-audit.tsv').body,parentFiles.get('method-audit.tsv').body)&&bytesEqual(files.get('source-test-set.tsv').body,parentFiles.get('test-set.tsv').body));
 const p=keyValues(files.get('classification-parameters.tsv').body,PARAM_KEYS,4096);need(same(p,version.parameters)&&await sha(files.get('classification-parameters.tsv').body)===version.parameters_sha256);
 const source=table(parentFiles.get('results.tsv').body,SOURCE_HEADER),actual=table(files.get('classification.tsv').body,HEADER),selected=table(files.get('selected-DE.tsv').body,HEADER);
 need(source.length===rows.length&&actual.length===source.length);
 const evaluated=version.DE_members_evaluated,labels={Up:0,Down:0,NS:0,not_testable:0,not_evaluated:0},members=[];
 for(const [i,row] of actual.entries()){
  check();const original=source[i],lineage=rows[i];need(same(row.slice(0,18),original)&&row[0]===String(lineage.source_row_ordinal)&&row[1]===lineage.protein_ids_raw&&row[2]===pair.contrast_id&&['testable','not_testable'].includes(row[3]));
  const label=row[18],flag=row[19];
  if(row[3]==='not_testable')need(label==='not_testable'&&flag==='FALSE');
  else if(evaluated)need(['Up','Down','NS'].includes(label)&&flag===(label==='Up'||label==='Down'?'TRUE':'FALSE'));
  else need(label==='not_evaluated'&&flag==='NOT_EVALUATED');
  labels[label]++;if(flag==='TRUE')members.push(row);
 }
 need(same(selected,members));
 const c=version.counts;
 for(const k of ['not_testable','not_evaluated'])need(c[k]===labels[k]);
 if(evaluated)need(['Up','Down','NS'].every(k=>c[k]===labels[k])&&c.selected_DE_rows===members.length);
 else need(selected.length===0&&version.member_set===null);
 const originalAudit=Object.fromEntries(table(parentFiles.get('method-audit.tsv').body,['key','value'],100));
 need(originalAudit.output_status===parent.scientific_status&&originalAudit.warning_count===String(parent.warning_count)&&originalAudit.input_rows===String(rows.length)&&originalAudit.contrast_id===pair.contrast_id&&originalAudit.numerator==='g1'&&originalAudit.denominator==='g2');
 const tested=source.filter(r=>r[3]==='testable').length;need(originalAudit.bh_m===String(tested));
 const expected={schema_version:CONTRACT,classification_status:version.status,DE_members_evaluated:evaluated?'TRUE':'FALSE',source_contract:originalAudit.schema_version,source_output_status:originalAudit.output_status,source_warning_count:originalAudit.warning_count,source_contrast_id:pair.contrast_id,source_numerator:originalAudit.numerator,source_denominator:originalAudit.denominator,contrast_direction:'numerator_minus_denominator',threshold_scale:'log2FC',adjusted_p_max:p.adjusted_p_max,absolute_log2fc_min:p.absolute_log2fc_min,boundary_rule:'adj.P.Val_le_max_and_abs_log2FC_ge_min_and_strict_direction_sign',zero_log2FC:'NS_if_evaluated',input_rows:String(rows.length),source_testable_rows:originalAudit.bh_m,source_not_testable_rows:String(labels.not_testable),Up:evaluated?String(labels.Up):'NA',Down:evaluated?String(labels.Down):'NA',NS:evaluated?String(labels.NS):'NA',not_testable:String(labels.not_testable),not_evaluated:String(labels.not_evaluated),selected_DE_rows:evaluated?String(selected.length):'NA',source_bh_method:originalAudit.bh_method,source_bh_m:originalAudit.bh_m,source_bh_n:originalAudit.bh_n,source_bh_scope:originalAudit.bh_scope,FC_column:'copied_original_not_used_for_threshold',statistical_recomputation:'none',row_identity:'source_row_ordinal_no_ProteinID_deduplication',row_order:'original',specific_detection:'not_implemented_D04_not_frozen'};
 need(same(keyValues(files.get('classification-audit.tsv').body,AUDIT_KEYS,32768),expected));
 need(!evaluated||version.member_set!==null);
 if(version.member_set!==null){const set=version.member_set;need(evaluated&&exact(set,['member_set_id','derived_version_id','selected_artifact_sha256','member_count'])&&uuid(set.member_set_id)&&set.derived_version_id===version.derived_version_id&&set.selected_artifact_sha256===files.get('selected-DE.tsv').sha256&&set.member_count===selected.length);}
 check();return freeze({members:selected,counts:c,evaluated,pins:version.artifacts});
}

export function mountDEClassificationSummary(root,initial={}){
 const doc=root.ownerDocument;let active=null,disposed=false,phase='waiting',ready=Promise.resolve(false);
 const make=(tag,text='',cls='')=>{const n=doc.createElement(tag);n.textContent=text;n.className=cls;return n;};
 function clear(){const old=active;active=null;if(!old)return;old.controller.abort();for(const signal of old.signals)signal.removeEventListener('abort',dispose);old.stops.splice(0).forEach(fn=>fn());old.panel.textContent='';old.panel.remove();old.input=null;old.data=null;}
 function dispose(){if(disposed)return;disposed=true;clear();phase='disposed';}
 function current(w){try{return!disposed&&active===w&&!w.controller.signal.aborted&&w.signals.every(s=>!s.aborted)&&w.options.isCurrent(w.context)===true&&w.options.parentPresentation.isCurrent(w.context)===true&&ownerKey(w.options)===w.key;}catch{return false;}}
 const guard=w=>{if(!current(w)){if(active===w)dispose();throw Error('de_classification_context_changed');}};
 const bind=(w,n,type,fn)=>{n.addEventListener(type,fn);w.stops.push(()=>n.removeEventListener(type,fn));};
 function draw(w){
  guard(w);const {t,data,input}=w,{context,version}=input,pair=context.selection.contrast.contrasts[0],direction=pair.numerator_group+' − '+pair.denominator_group;
  w.panel.textContent='';w.panel.append(make('h3',t('R 差异蛋白分类','R differential protein classification')));
  const add=(field,value,cls='lrt-source')=>{const n=make('p',value,cls);n.setAttribute('data-de-field',field);w.panel.append(n);return n;};
  const provenance=add('provenance',context.provenance==='synthetic_engineering_verification'?t('工程验证数据；不能作为科研结论。','Engineering verification data; not a research conclusion.'):context.provenance==='owner_verified_saved_input'?t('基于已保存输入；请核对实验分组与比较方向。','Based on saved inputs; review study groups and contrast direction.'):t('请结合实验设计、原结果与方法审计解释分类。','Interpret classification with the study design, original results and method audit.'),'lrt-note');provenance.setAttribute('data-lrt-provenance',context.provenance);
  add('parent',t('原结果：','Source result: ')+version.source_result_id+' · '+t('完整清单 SHA256：','Full manifest SHA256: ')+version.source_manifest_sha256);
  add('derived',t('派生版本／请求：','Derived version / request: ')+version.derived_version_id+' / '+version.request_id);
  add('direction',t('原比较方向（log2FC）：','Original direction (log2FC): ')+direction+' · '+pair.contrast_id);
  add('parameters','adjusted_p_max = '+version.parameters.adjusted_p_max+' · absolute_log2fc_min = '+version.parameters.absolute_log2fc_min+' · input_scale = '+version.parameters.input_scale);
  add('parameters-sha','classification-parameters.tsv · SHA256 '+version.parameters_sha256);
  add('status',data.evaluated?t('R 分类已完成（CLASSIFIED）。','R classification completed (CLASSIFIED).'):version.status==='BLOCKED_SOURCE_NO_TESTABLE_ROWS'?t('分类被阻断：原结果没有可检验行；未评估 DE 成员。','Classification blocked: no testable source rows; DE members were not evaluated.'):t('分类被阻断：原结果存在方法警告，需要审查；未评估 DE 成员。','Classification blocked: source method warnings require review; DE members were not evaluated.'));
  const names={Up:t('上调','Up'),Down:t('下调','Down'),NS:t('未显著','NS'),not_testable:t('不可检验','Not testable'),not_evaluated:t('未评估','Not evaluated'),selected_DE_rows:t('DE 成员行','DE member rows')};
  for(const key of COUNT_KEYS)add('count-'+key,names[key]+' ('+key+'): '+(data.counts[key]===null?t('NA（未评估）','NA (not evaluated)'):String(data.counts[key])));
  add('meaning',t('展示 R 已输出的分类与原始统计 token。不可检验不等同于未显著；Protein IDs 保留空白、重复和分号，不拆分或去重。','Displays R output labels and original statistical tokens. Not testable differs from NS; Protein IDs retain whitespace, duplicates and semicolons without splitting or deduplication.'),'lrt-note');
  if(data.evaluated){
   const set=version.member_set;add('member-set',t('DE 成员集引用：','DE member-set reference: ')+set.member_set_id+' · '+set.member_count+t(' 行',' rows')+' · SHA256 '+set.selected_artifact_sha256);
   if(data.members.length===0)add('empty',t('本次分类成功，DE 为 0；功能富集暂无成员。','Classification succeeded with 0 DE rows; no members are available for enrichment.'));
   else{
    const scroll=make('div','','lrt-scroll'),tableNode=make('table'),caption=make('caption',t('按原始行序预览前 ','First ')+Math.min(25,data.members.length)+' / '+data.members.length+t(' 个成员行；完整成员见 selected-DE.tsv。',' member rows in original order; see selected-DE.tsv for the full set.')),head=make('thead'),tr=make('tr'),body=make('tbody');
    scroll.tabIndex=0;scroll.setAttribute('role','region');scroll.setAttribute('aria-label',t('可横向滚动的 DE 成员预览','Horizontally scrollable DE member preview'));
    const columns=[[0,'原始行号','Source row'],[1,'原始 Protein IDs','Original Protein IDs'],[18,'R 分类','R classification'],[5,'log2FC','log2FC'],[6,'FC','FC'],[12,'P.Value','P.Value'],[13,'adj.P.Val','adj.P.Val']];
    for(const [,zh,en] of columns){const th=make('th',t(zh,en));th.setAttribute('scope','col');tr.append(th);}head.append(tr);
    for(const row of data.members.slice(0,25)){const node=make('tr');node.setAttribute('data-de-source-row',row[0]);for(const [i] of columns){const cell=make('td',row[i],i===1?'lrt-protein':'');cell.setAttribute('data-de-cell',HEADER[i]);node.append(cell);}body.append(node);}
    tableNode.append(caption,head,body);scroll.append(tableNode);w.panel.append(scroll);
   }
  }else add('member-set',t('没有有效 DE 成员集；空 selected-DE.tsv 不能用作成功空集或富集输入。','No valid DE member set exists; the empty selected-DE.tsv is not a successful empty set or an enrichment input.'));
  const downloads=make('div','','lrt-downloads'),note=make('p',typeof w.download==='function'?t('下载由项目授权通道重新读取并核对。','Downloads are re-read and checked by the project authorization channel.'):t('下载尚未连接。','Downloads are not connected.'),'lrt-note'),buttons=[];note.setAttribute('role','status');note.setAttribute('aria-live','polite');note.setAttribute('data-de-download-state','status');
  for(const pin of data.pins){const button=make('button',pin.name+' · '+pin.bytes+' B');button.type='button';button.disabled=typeof w.download!=='function';button.setAttribute('data-de-download',pin.name);buttons.push(button);downloads.append(button);
   bind(w,button,'click',async()=>{
    if(!current(w)){dispose();return;}if(w.downloading||typeof w.download!=='function')return;w.downloading=true;buttons.forEach(b=>{b.disabled=true;});note.textContent=t('正在请求授权下载…','Requesting authorized download…');
    try{const result=await w.download({name:pin.name,pin:{bytes:pin.bytes,sha256:pin.sha256},derived_version_id:version.derived_version_id,request_id:version.request_id,source_result_id:version.source_result_id,verifiedOwnerContext:context,signal:w.controller.signal});guard(w);note.textContent=exact(result,['status'])&&result.status==='handed_off'?t('已由授权通道交给浏览器保存；请查看浏览器下载状态。','The authorized channel handed the file to the browser; check the browser download status.'):t('下载尚未确认。','Download is unconfirmed.');}
    catch{if(current(w))note.textContent=t('下载未确认，请稍后手动重试。','Download is unconfirmed; retry manually later.');else if(active===w)dispose();}
    finally{if(current(w)){w.downloading=false;buttons.forEach(b=>{b.disabled=false;});}}
   });
  }w.panel.append(downloads,note);
 }
 function update(options={}){
  if(disposed)return Promise.resolve(false);clear();phase='waiting';const t=options.t||((zh)=>zh),panel=make('section','','lrt'),state=make('p',t('正在核对 R 分类结果及原结果关联…','Checking R classification and source-result linkage…'),'lrt-note');panel.setAttribute('role','region');panel.setAttribute('aria-label',t('R 差异蛋白分类','R differential protein classification'));state.setAttribute('role','status');panel.append(state);root.append(panel);
  let w;
  try{
   const signals=[...new Set([options.contextSignal,options.parentPresentation?.contextSignal])];need(signals.every(s=>s&&typeof s.aborted==='boolean'&&!s.aborted&&typeof s.addEventListener==='function'&&typeof s.removeEventListener==='function'));
   need(typeof options.isCurrent==='function'&&typeof options.parentPresentation?.isCurrent==='function');
   // Capture all metadata, lineage and eleven byte buffers before the first await.
   const input=capture(options);w={options,panel,t,signals,controller:new AbortController(),stops:[],input,context:input.context,key:ownerKey(options),download:options.onDownload,data:null,downloading:false,hashes:0};active=w;for(const signal of signals)signal.addEventListener('abort',dispose,{once:true});guard(w);phase='verifying';
  }catch{if(active===w&&w)clear();else{panel.textContent='';panel.remove();}phase='invalid';return ready=Promise.resolve(false);}
  ready=(async()=>{try{w.data=await validate(w,()=>guard(w));guard(w);draw(w);phase='ready';return true;}catch{if(active===w&&current(w)){w.input=null;w.data=null;w.stops.splice(0).forEach(fn=>fn());w.panel.textContent='';w.panel.append(make('p',t('分类文件或原结果关联校验失败；旧展示已清除。','Classification artifacts or source linkage failed validation; previous content was cleared.'),'lrt-note'));phase='invalid';}return false;}})();return ready;
 }
 update(initial);return Object.freeze({update,dispose,get ready(){return ready;},view(){if(active&&!current(active))dispose();return freeze({phase,derived_version_id:phase==='ready'?active.input.version.derived_version_id:null,source_result_id:phase==='ready'?active.input.version.source_result_id:null,DE_members_evaluated:phase==='ready'?active.data.evaluated:null,counts:phase==='ready'?active.data.counts:null,preview_rows:phase==='ready'?Math.min(25,active.data.members.length):0});}});
}
