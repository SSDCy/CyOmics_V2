import {classificationParameters,classificationParametersBytes,classificationJobInput,classificationJobRoute,classificationJobReply,classificationJobError,ClassificationJobFailure,CLASSIFICATION_CORE} from './de-classification-read-contract.mjs';
import {deClassificationReadRoute,deClassificationReadReply,deClassificationReadError,DEClassificationReadFailure,DE_TOTAL_MAX} from './de-classification-read-contract.mjs';
import {verifyDEClassificationPresentation} from './de-classification-summary.js';
import {canonicalJSON,freeze,uuid} from './sample-design-contract.mjs';
import {parsePreprocessJSON,wireError} from './preprocess-owner-wire.mjs';
import {differentialResultRoute,differentialResultReply,differentialResultError,DifferentialResultFailure,joinDifferentialResult,DIFFERENTIAL_RESULT_NAMES} from './differential-result-contract.mjs';
import {parseDifferentialRowMap} from './differential-row-map.mjs';
const fail=(code='differential_results_unavailable',status=503)=>{throw new DifferentialResultFailure(status,code);};
const need=ok=>{if(!ok)fail();};
function auditMatchesMetadata(artifacts,meta,job){
 const text=new TextDecoder('utf-8',{fatal:true}).decode(artifacts.find(a=>a.name==='method-audit.tsv').body),lines=text.split('\n');need(lines.shift()==='key\tvalue'&&lines.pop()==='');const audit=new Map();
 for(const line of lines){const pair=line.split('\t');need(pair.length===2&&!audit.has(pair[0]));audit.set(pair[0],pair[1]);}
 need(audit.get('output_status')===meta.scientific_status&&audit.get('warning_count')===String(meta.warning_count)&&audit.get('warning_codes')===(meta.warning_count?'limma_warning_review_required':'')&&audit.get('contrast_id')===job.selection.contrast.contrasts[0].contrast_id);
}
// Explicit reads only. The queued record remains immutable admission history.
export function createDifferentialResultOwner({projectId,fileId,jobs,transport,source,isCurrent,subscribeSource=()=>()=>{},onDenied=()=>{},contextSignal,
 provenance='owner_verified_saved_input',cryptoImpl=globalThis.crypto,setTimer=setTimeout,clearTimer=clearTimeout,now=()=>performance.now(),documentImpl=globalThis.document,urlImpl=globalThis.URL,BlobImpl=globalThis.Blob}={}){
 let disposed=false,generation=0,op=null,handoff=null,snapshot=null,phase='idle',stage=null,code=null,requestId=null,run=null;const listeners=new Set();
 let classificationSnapshot=null,classificationPhase='idle',classificationRequestId=null,classificationGeneration=0;
 const classificationIntents=new Map();let classificationJobNotice='idle';
 let classificationRecoveryStatus='waiting',classificationRecoveryScope=null,classificationRecoveryCount=0;
 const emit=()=>{if(!disposed)for(const fn of [...listeners])try{fn();}catch{}};
 const base=()=>{try{if(disposed||contextSignal?.aborted||!isCurrent()||!transport?.isCurrent())return null;const value=source();return value?.file?.project_id===projectId&&value.file.file_id===fileId?value:null;}catch{return null;}};
 const baseKey=()=>{const value=base();return value?canonicalJSON(value):null;};let observed=baseKey();
 function revoke(w){if(!w)return;clearTimer(w.urlTimer);w.urlTimer=null;if(w.url!==null){try{urlImpl.revokeObjectURL(w.url);}catch{}w.url=null;}if(w.anchor){try{w.anchor.removeAttribute?.('href');w.anchor.removeAttribute?.('download');w.anchor.remove?.();}catch{}w.anchor=null;}}
 function release(w){if(w?.asyncDone&&w.hashes===0&&w.url===null){w.lease?.release();w.lease=null;if(handoff===w)handoff=null;w.released?.();}}
 function cancel(w){if(!w)return;if(w.kind==='classification-submit'&&w.intent?.phase==='submitting')w.intent.phase='unknown';clearTimer(w.timer);clearTimer(w.idleTimer);w.controller.abort();try{void w.reader?.cancel()?.catch(()=>{});}catch{}revoke(w);release(w);}
 function clear(next='idle'){
  if(disposed)return Promise.resolve();generation++;classificationJobNotice='idle';classificationRecoveryStatus='waiting';classificationRecoveryScope=null;classificationRecoveryCount=0;const old=op;op=null;clearClassification('idle',false);cancel(old);if(handoff&&handoff!==old)cancel(handoff);snapshot?.controller.abort();snapshot=null;phase=next;stage=null;code=null;run=null;requestId=null;emit();return old?.done||Promise.resolve();
 }
 function dispose(){if(disposed)return;clear('disposed');disposed=true;classificationIntents.clear();contextSignal?.removeEventListener('abort',dispose);stopSource();for(const fn of [...listeners])try{fn();}catch{}listeners.clear();}
 const stopSource=subscribeSource(()=>{const next=baseKey();if(next!==observed){observed=next;clear();}})||(()=>{});
 contextSignal?.addEventListener('abort',dispose,{once:true});
 function guard(w){const stamp=now();if(disposed||op!==w||w.generation!==generation||!base()||baseKey()!==w.baseKey||w.controller.signal.aborted||!Number.isFinite(stamp)||stamp<w.last||stamp-w.start>=60000){cancel(w);fail('differential_results_unavailable');}w.last=stamp;w.checkContext?.();}
 function begin(kind,id){const w={kind,requestId:id,generation,baseKey:baseKey(),controller:new AbortController(),start:now(),last:now(),reader:null,hashes:0,asyncDone:false,url:null,urlTimer:null,anchor:null};
  w.done=new Promise(resolve=>{w.released=resolve;});w.aborted=new Promise((_,reject)=>w.controller.signal.addEventListener('abort',()=>reject(new DifferentialResultFailure(504,'differential_results_timeout')),{once:true}));void w.aborted.catch(()=>{});op=w;
  w.lease=transport.artifactScope?.acquire(()=>cancel(w));if(!w.lease){op=null;fail('differential_artifact_busy');}w.timer=setTimer(()=>cancel(w),60000);return w;
 }
 async function wait(w,p){const value=await Promise.race([p,w.aborted]);guard(w);return value;}
 async function hashBytes(w,body){guard(w);w.hashes++;let pending;try{pending=Promise.resolve(cryptoImpl.subtle.digest('SHA-256',body));}catch(error){w.hashes--;throw error;}
  void pending.then(()=>{w.hashes--;release(w);},()=>{w.hashes--;release(w);});const digest=await wait(w,pending);return [...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2,'0')).join('');}
 const hashJSON=(w,value)=>hashBytes(w,new TextEncoder().encode(canonicalJSON(value)));
 const setStage=(w,value)=>{guard(w);stage=value;emit();};
 function deny(status,context=false){dispose();onDenied({status,body:context?{error:{code:'project_context_changed'}}:{}});}
 async function readBody(w,response,cap,expected=null){
  guard(w);if([401,403].includes(response.status)){deny(response.status);fail();}
  const h=response.headers;need(!response.redirected&&[null,'identity'].includes(h.get('content-encoding'))&&!h.has('location')&&!h.has('refresh'));
  if(expected){need(response.status===200&&h.get('content-type')==='application/octet-stream'&&h.get('content-length')===String(expected.bytes)&&h.get('content-disposition')==='attachment; filename="'+expected.name+'"'&&h.get('x-cyomics-file-sha256')===expected.sha256&&h.get('cache-control')==='no-store'&&h.get('x-content-type-options')==='nosniff'&&h.get('referrer-policy')==='no-referrer');}
  else need(/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(h.get('content-type')||''));
  const length=h.get('content-length');need(length===null||/^[0-9]{1,10}$/.test(length)&&Number(length)<=cap);w.reader=response.body?.getReader();need(w.reader);const parts=[];let size=0;
  try{for(;;){w.idleTimer=setTimer(()=>cancel(w),Math.min(15000,Math.max(1,60000-(now()-w.start))));let part;try{part=await wait(w,w.reader.read());}finally{clearTimer(w.idleTimer);w.idleTimer=null;}if(part.done)break;need(part.value instanceof Uint8Array&&size+part.value.length<=cap);parts.push(part.value);size+=part.value.length;}
   need(length===null||Number(length)===size);const bytes=new Uint8Array(size);let at=0;for(const part of parts){bytes.set(part,at);at+=part.length;}if(expected)need(size===expected.bytes&&await hashBytes(w,bytes)===expected.sha256);guard(w);return bytes;
  }finally{try{void w.reader?.cancel()?.catch(()=>{});w.reader?.releaseLock();}catch{}w.reader=null;}
 }
 async function send(w,args){const received=await wait(w,transport.send({...args,signal:w.controller.signal}));w.checkContext=received.checkCurrent;guard(w);return received.response;}
 async function json(w,args){const response=await send(w,args),bytes=await readBody(w,response,response.status===200?32768:4096),value=parsePreprocessJSON(bytes);
  if(response.status!==200){const error=differentialResultError(response.status,value);if(response.status===409&&error.error.code==='project_context_changed')deny(409,true);throw new DifferentialResultFailure(response.status,error.error.code);}
  const path='/api/v1/projects/'+projectId+(args.kind==='run'?'/differential-runs/'+args.jobId:args.kind==='lookup'?'/differential-results?job_id='+args.jobId:'/differential-results/'+args.resultId);
  const validated=differentialResultReply(differentialResultRoute(path,'GET'),200,value);if(args.kind!=='run')need(await hashJSON(w,validated.artifacts)===validated.manifest_sha256);return validated;
 }
 async function freshJob(w){setStage(w,'job');const ok=await wait(w,jobs.check(w.requestId,{signal:w.controller.signal}));
  if(!ok){const c=jobs.view().code;fail(c==='differential_job_not_found'?c:'differential_results_unavailable',c==='differential_job_not_found'?404:503);}
  const job=jobs.view().history.find(e=>e.request_id===w.requestId)?.task,src=base();need(job&&job.request_id===w.requestId&&job.project_id===projectId&&job.source.file_id===fileId&&job.source.source.source_sha256===src.file.sha256);
  const key=await hashJSON(w,job);if(w.expected)need(key===w.expected.jobSHA);return{job,key};
 }
 async function metadata(w,job){setStage(w,'run');const execution=await json(w,{kind:'run',jobId:job.job_id});guard(w);run=execution;
  if(execution.observation!=='confirmed')return{phase:'reconciliation',execution};if(execution.status!=='succeeded')return{phase:execution.status,execution};
  setStage(w,'metadata');const meta=await json(w,w.expected?{kind:'result',resultId:w.expected.meta.result_id}:{kind:'lookup',jobId:job.job_id});joinDifferentialResult(job,execution,meta);const metaSHA=await hashJSON(w,meta);if(w.expected)need(metaSHA===w.expected.metaSHA);return{execution,meta,metaSHA};
 }
 async function artifact(w,meta,pin){setStage(w,'artifacts');const response=await send(w,{kind:'artifact',resultId:meta.result_id,name:pin.name});if(response.status!==200){const value=parsePreprocessJSON(await readBody(w,response,4096));const error=differentialResultError(response.status,value);if(response.status===409)deny(409,true);throw new DifferentialResultFailure(response.status,error.error.code);}return readBody(w,response,pin.bytes,pin);}
 async function rowMap(w,job){setStage(w,'rowmap');const pin=job.source.artifacts.find(a=>a.name==='row-map.tsv');need(pin);const received=await wait(w,transport.rowMap(job,w.controller.signal));w.checkContext=received.checkCurrent;guard(w);const response=received.response;
  if(response.status!==200){const value=parsePreprocessJSON(await readBody(w,response,4096)),error=wireError(response.status,value);if(error.code==='context_changed')deny(409,true);throw new DifferentialResultFailure(response.status,error.code);}
  const bytes=await readBody(w,response,pin.bytes,pin);return parseDifferentialRowMap(bytes,job.source);
 }
 function errorState(w,error){if(disposed||op!==w||w.generation!==generation)return;if(error?.message==='project_context_changed'){deny(409,true);return;}
  snapshot?.controller.abort();snapshot=null;code=error instanceof DifferentialResultFailure?error.code:'differential_results_unavailable';phase=error?.status===404?(stage==='run'?'not_started':stage==='metadata'?'no_result':'unavailable'):'unavailable';emit();
 }
 function finish(w){clearTimer(w.timer);clearTimer(w.idleTimer);try{void w.reader?.cancel()?.catch(()=>{});}catch{}w.reader=null;w.asyncDone=true;if(op===w)op=null;if(!['download','classification-download'].includes(w.kind)||w.url===null)w.controller.abort();release(w);emit();}
 async function select(id){if(disposed||!uuid(id)||!base()||phase==='loading'&&requestId===id)return false;const token=generation+1,previous=clear();phase='loading';requestId=id;emit();await previous;if(disposed||generation!==token||!base())return false;let w;
  try{w=begin('view',id);const {job,key}=await freshJob(w),result=await metadata(w,job);if(result.phase){phase=result.phase;emit();return false;}
   const lineage=await rowMap(w,job),artifacts=[];for(const pin of result.meta.artifacts){const body=await artifact(w,result.meta,pin);artifacts.push({...pin,body});}guard(w);auditMatchesMetadata(artifacts,result.meta,job);
   need(['owner_verified_saved_input','synthetic_engineering_verification','research'].includes(provenance));const original=base().file,ctx=freeze({project_id:projectId,job_id:job.job_id,binding_sha256:job.binding_sha256,selection:job.selection,
    source:{file_id:fileId,parent_dataset_version_id:job.source.parent_dataset_version_id,source_sha256:job.source.source.source_sha256,filename:original.filename},provenance});
   snapshot={generation,baseKey:w.baseKey,controller:new AbortController(),context:ctx,job,jobSHA:key,run:result.execution,meta:result.meta,metaSHA:result.metaSHA,rows:lineage,artifacts};await restoreClassificationRecovery(w,snapshot);guard(w);phase='ready';stage=null;code=null;emit();return true;
  }catch(error){if(w)errorState(w,error);else{phase='unavailable';code='differential_artifact_busy';emit();}return false;}finally{if(w)finish(w);}
 }
 function clearClassification(next='idle',notify=true){
  classificationGeneration++;const old=op?.kind?.startsWith('classification-')?op:null;if(old){op=null;cancel(old);}if(handoff?.kind==='classification-download')cancel(handoff);
  classificationSnapshot?.controller.abort();classificationSnapshot=null;classificationPhase=next;classificationRequestId=null;if(notify)emit();return old?.done||Promise.resolve();
 }
 function classificationCurrent(s){return !!s&&classificationSnapshot===s&&snapshotCurrent(s.parent)&&!s.controller.signal.aborted;}
 const classificationPath=(kind,id,name)=>'/api/v1/projects/'+projectId+'/differential-classifications'+(kind==='classification-lookup'?'?request_id='+id:'/'+id+(kind==='classification-artifact'?'/files/'+name:''));
 async function classificationJSON(w,kind,id){
  const response=await send(w,{kind,classificationRequestId:kind==='classification-lookup'?id:null,derivedVersionId:kind==='classification-version'?id:null});
  const value=parsePreprocessJSON(await readBody(w,response,response.status===200?32768:4096));
  if(response.status!==200){const error=deClassificationReadError(response.status,value,{upstream:true});if(response.status===409)deny(409,true);throw new DEClassificationReadFailure(response.status,error.error.code);}
  const meta=deClassificationReadReply(deClassificationReadRoute(classificationPath(kind,id),'GET'),200,value);
  need(await hashJSON(w,meta.artifacts)===meta.manifest_sha256&&await hashJSON(w,meta.source_binding)===meta.source_binding_sha256);return meta;
 }
 async function classificationArtifact(w,meta,pin){
  const response=await send(w,{kind:'classification-artifact',derivedVersionId:meta.derived_version_id,name:pin.name});
  if(response.status!==200){const value=parsePreprocessJSON(await readBody(w,response,4096)),error=deClassificationReadError(response.status,value,{upstream:true});if(response.status===409)deny(409,true);throw new DEClassificationReadFailure(response.status,error.error.code);}
  return readBody(w,response,pin.bytes,pin);
 }
 async function classificationBundle(w,kind,id,expected=null){
  const {job}=await freshJob(w),parent=await metadata(w,job);need(!parent.phase&&snapshotCurrent(w.expected));
  const rows=await rowMap(w,job),parentArtifacts=[];for(const pin of parent.meta.artifacts)parentArtifacts.push({...pin,body:await artifact(w,parent.meta,pin)});auditMatchesMetadata(parentArtifacts,parent.meta,job);
  const meta=await classificationJSON(w,kind,id);need(meta.source_result_id===parent.meta.result_id&&meta.source_manifest_sha256===parent.meta.manifest_sha256&&meta.created_by===parent.meta.created_by);
  const metaSHA=await hashJSON(w,meta);if(expected)need(metaSHA===expected.metaSHA);let total=0;const artifacts=[];for(const pin of meta.artifacts){total+=pin.bytes;need(total<=DE_TOTAL_MAX);artifacts.push({...pin,body:await classificationArtifact(w,meta,pin)});}
  const check=()=>{guard(w);need(snapshotCurrent(w.expected));};
  // Reuse the reviewed display validator in this original lease. Its WebCrypto
  // adapter delegates every digest to the existing tracked hashBytes lifetime.
  const trackedCrypto={subtle:{async digest(algorithm,body){need(algorithm==='SHA-256');const hex=await hashBytes(w,body);return Uint8Array.from(hex.match(/../g),pair=>parseInt(pair,16)).buffer;}}};
  const parentPresentation={verifiedOwnerContext:w.expected.context,rows,artifacts:parentArtifacts,contextSignal:w.controller.signal,isCurrent:()=>snapshotCurrent(w.expected)};
  await wait(w,verifyDEClassificationPresentation({parentMetadata:parent.meta,parentPresentation,derivedMetadata:meta,artifacts,contextSignal:w.controller.signal,isCurrent:()=>snapshotCurrent(w.expected),cryptoImpl:trackedCrypto},check));check();
  return{meta,metaSHA,rows,parentArtifacts,parentMetadata:parent.meta,artifacts};
 }
 function classificationError(w,error){
  if(disposed||op!==w)return;if(error?.message==='project_context_changed'){deny(409,true);return;}
  classificationSnapshot?.controller.abort();classificationSnapshot=null;classificationPhase=error?.status===404?'not_found':'unavailable';emit();
 }
 async function readClassification(id,admission=null){
  const wanted=snapshot;if(!uuid(id)||!wanted||!snapshotCurrent(wanted)||op&&!op.kind.startsWith('classification-'))return false;
  const previous=clearClassification(),token=classificationGeneration;classificationPhase='loading';classificationRequestId=id;emit();await previous;
  if(disposed||token!==classificationGeneration||!snapshotCurrent(wanted))return false;let w;
  try{w=begin('classification-view',wanted.job.request_id);w.expected=wanted;const value=await classificationBundle(w,'classification-lookup',id);guard(w);if(admission)need(value.meta.request_id===admission.request_id&&value.meta.source_result_id===admission.source_result_id&&value.meta.source_manifest_sha256===admission.source_manifest_sha256&&value.meta.source_binding_sha256===admission.source_binding_sha256&&value.meta.parameters_sha256===admission.parameters_sha256&&canonicalJSON(value.meta.parameters)===canonicalJSON(admission.parameters)&&canonicalJSON(value.meta.core)===canonicalJSON(admission.core));
   const intent=classificationIntent();if(admission&&intent?.task===admission)intent.expectedBindingSHA=value.meta.source_binding_sha256;
   classificationSnapshot={...value,parent:wanted,controller:new AbortController(),epoch:token};classificationPhase='ready';emit();return true;
  }catch(error){if(w)classificationError(w,error);else{classificationPhase='unavailable';emit();}return false;}finally{if(w)finish(w);}
 }
 async function downloadClassification({name,pin,derived_version_id,request_id,source_result_id,verifiedOwnerContext,signal}){
  const wanted=classificationSnapshot;if(!classificationCurrent(wanted)||op||signal.aborted||derived_version_id!==wanted.meta.derived_version_id||request_id!==wanted.meta.request_id||source_result_id!==wanted.meta.source_result_id||canonicalJSON(verifiedOwnerContext)!==canonicalJSON(wanted.parent.context))fail();
  const expected=wanted.meta.artifacts.find(a=>a.name===name);need(expected&&canonicalJSON(pin)===canonicalJSON({bytes:expected.bytes,sha256:expected.sha256}));let w,handed=false,abort;
  try{w=begin('classification-download',wanted.parent.job.request_id);handoff=w;w.expected=wanted.parent;abort=()=>cancel(w);signal.addEventListener('abort',abort,{once:true});
   const fresh=await classificationBundle(w,'classification-version',wanted.meta.derived_version_id,wanted);guard(w);need(classificationCurrent(wanted));const body=fresh.artifacts.find(a=>a.name===name).body;
   need(typeof documentImpl?.createElement==='function'&&typeof documentImpl?.body?.append==='function'&&typeof urlImpl?.createObjectURL==='function'&&typeof urlImpl?.revokeObjectURL==='function'&&typeof BlobImpl==='function');
   const blob=new BlobImpl([body],{type:'application/octet-stream'});guard(w);w.url=urlImpl.createObjectURL(blob);guard(w);w.anchor=documentImpl.createElement('a');guard(w);w.anchor.href=w.url;w.anchor.download='classification_'+derived_version_id+'_'+name;w.anchor.style.display='none';documentImpl.body.append(w.anchor);guard(w);w.anchor.click();guard(w);handed=true;w.urlTimer=setTimer(()=>{revoke(w);w.controller.abort();release(w);},1000);return{status:'handed_off'};
  }catch(error){if(w)classificationError(w,error);throw new DEClassificationReadFailure();}finally{if(abort)signal.removeEventListener('abort',abort);if(w){if(!handed||!classificationCurrent(wanted))revoke(w);finish(w);}}
 }
 // Live authority remains in the private Owner context. Scoped sessionStorage
 // contains only untrusted source-bound request hints; a restored hint requires
 // manual Owner GET, not local queued/result/auth or server-list authority.
 async function restoreClassificationRecovery(w,wanted){
  guard(w);const actor=wanted.meta.created_by,scope=await hashJSON(w,{domain:'cyomics/classification/request-recovery/v1',actor_id:actor});guard(w);need(snapshotCurrent(wanted));
  classificationRecoveryScope={actor,scope};
  try{
   const items=transport.classificationRecovery.load(actor,scope);
   for(const r of items){need(await hashBytes(w,classificationParametersBytes(r.parameters))===r.parameters_sha256);}
   guard(w);need(snapshotCurrent(wanted));
   for(const r of items)if(r.source_result_id===wanted.meta.result_id||r.source_meta_sha256===wanted.metaSHA||r.file_id===fileId&&r.project_id!==projectId){
    need(r.actor_id===actor&&r.project_id===projectId&&r.file_id===fileId&&r.source_result_id===wanted.meta.result_id&&r.source_meta_sha256===wanted.metaSHA&&r.source_manifest_sha256===wanted.meta.manifest_sha256);
   }
   const matches=items.filter(r=>r.project_id===projectId&&r.file_id===fileId&&r.source_result_id===wanted.meta.result_id),memory=classificationIntent();
   if(memory){
    const stored=matches.find(r=>r.request_id===memory.body.request_id);need(stored&&stored.parameters_sha256===memory.parametersSHA&&canonicalJSON(stored.parameters)===canonicalJSON(memory.body.parameters));
    if(memory.task&&stored.queued)need(await hashJSON(w,memory.task)===stored.queued.dto_sha256);
    if(!memory.task&&stored.queued){memory.recoveryAnchor=stored.queued;memory.needsConfirmation=true;memory.retryAllowed=false;memory.retryBlocked=true;memory.phase='recovered';}
   }else if(matches.length){
    // Ordering only chooses a local hint, never a server result or authority.
    const r=matches.at(-1),body=classificationJobInput(new TextEncoder().encode(canonicalJSON({request_id:r.request_id,source_result_id:r.source_result_id,parameters:r.parameters})));
    classificationIntents.set(wanted.meta.result_id,{body,parentMetaSHA:wanted.metaSHA,baseKey:wanted.baseKey,sourceManifestSHA:wanted.meta.manifest_sha256,parametersSHA:r.parameters_sha256,expectedBindingSHA:null,phase:'recovered',task:null,needsConfirmation:true,recoveryAnchor:r.queued,retryAllowed:false,retryBlocked:!!r.queued});
   }
   guard(w);classificationRecoveryCount=items.length;classificationRecoveryStatus='ready';
  }catch{guard(w);classificationRecoveryStatus='blocked';}
 }
 async function persistClassificationRecovery(w,ref,task=null){
  guard(w);try{
   const bound=classificationRecoveryScope;need(bound&&bound.actor===snapshot.meta.created_by);
   const queued=task?{classification_job_id:task.classification_job_id,created_at:task.created_at,source_binding_sha256:task.source_binding_sha256,dto_sha256:await hashJSON(w,task)}:null;
   guard(w);const record={v:1,actor_id:bound.actor,project_id:projectId,file_id:fileId,source_result_id:ref.body.source_result_id,source_meta_sha256:ref.parentMetaSHA,source_manifest_sha256:ref.sourceManifestSHA,request_id:ref.body.request_id,parameters:ref.body.parameters,parameters_sha256:ref.parametersSHA,core:CLASSIFICATION_CORE,disposition:task?'confirmed':'unknown',queued};
   const items=transport.classificationRecovery.put(bound.actor,bound.scope,record);guard(w);classificationRecoveryCount=items.length;classificationRecoveryStatus='ready';return true;
  }catch{guard(w);classificationRecoveryStatus='blocked';return false;}
 }
 function classificationIntent(){const s=snapshot;if(!s||!snapshotCurrent(s))return null;const ref=classificationIntents.get(s.meta.result_id);return ref&&ref.parentMetaSHA===s.metaSHA&&ref.baseKey===s.baseKey?ref:null;}
 function canSubmitClassification(parameters){try{classificationParameters(parameters);const ref=classificationIntent();return !!snapshot&&snapshotCurrent(snapshot)&&classificationRecoveryStatus==='ready'&&classificationRecoveryCount<16&&!op&&classificationJobNotice!=='submitting'&&transport.artifactScope?.busy?.()!==true&&(!ref||['queued','rejected'].includes(ref.phase)&&canonicalJSON(ref.body.parameters)!==canonicalJSON(parameters));}catch{return false;}}
 async function classificationJobJSON(w,kind,ref){
  const post=kind==='classification-submit';if(post)w.sent=true;
  const response=await send(w,{kind,body:post?ref.body:undefined,classificationRequestId:ref.body.request_id});
  const value=parsePreprocessJSON(await readBody(w,response,[200,201].includes(response.status)?32768:4096));
  if(![200,201].includes(response.status)){const error=classificationJobError(response.status,value,{upstream:true});if(response.status===409&&error.error.code==='project_context_changed')deny(409,true);throw new ClassificationJobFailure(response.status,error.error.code);}
  const route=classificationJobRoute('/api/v1/projects/'+projectId+'/classification-jobs'+(post?'':'?request_id='+ref.body.request_id),post?'POST':'GET');
  const task=classificationJobReply(route,response.status,value,ref.body,snapshot.meta.created_by);
  need(task.source_manifest_sha256===ref.sourceManifestSHA&&task.parameters_sha256===ref.parametersSHA);
  if(ref.expectedBindingSHA)need(task.source_binding_sha256===ref.expectedBindingSHA);
  if(ref.task)need(canonicalJSON(task)===canonicalJSON(ref.task));if(ref.recoveryAnchor){const a=ref.recoveryAnchor;need(task.classification_job_id===a.classification_job_id&&task.created_at===a.created_at&&task.source_binding_sha256===a.source_binding_sha256&&await hashJSON(w,task)===a.dto_sha256);}return task;
 }
 function jobFailure(w,ref,error,post){
  if(error?.message==='project_context_changed'){deny(409,true);return;}
  if(disposed||op!==w||!snapshotCurrent(snapshot))return;
  if(ref){
   if(error?.status===409&&error.code==='de_classification_request_conflict')ref.retryBlocked=true;
   const withoutQueuedEvidence=!ref.task&&!ref.recoveryAnchor&&!ref.retryBlocked;
   ref.retryAllowed=withoutQueuedEvidence&&(post&&error?.status===409&&error.code==='de_classification_capacity_busy'||!post&&error?.status===404&&error.code==='de_classification_job_not_found');
   ref.phase=post&&ref.retryAllowed?'capacity_busy':error?.status===409?'conflict':error?.status===404&&!post?'not_found':[400,404].includes(error?.status)&&post?'rejected':post||ref.phase==='unknown'?'unknown':'unavailable';
  }
  else classificationJobNotice='unavailable';emit();
 }
 async function submitClassification(parameters){
  if(!canSubmitClassification(parameters))return false;const wanted=snapshot,p=classificationParameters(parameters);
  const checked=classificationCurrent(classificationSnapshot)&&classificationSnapshot.parent===wanted?classificationSnapshot.meta.source_binding_sha256:classificationIntent()?.expectedBindingSHA||null;
  const previous=clearClassification('idle',false);classificationJobNotice='submitting';emit();await previous;if(!snapshotCurrent(wanted)){classificationJobNotice='idle';return false;}
  let w,ref,accepted=false;
  try{
   w=begin('classification-submit',wanted.job.request_id);await restoreClassificationRecovery(w,wanted);need(classificationRecoveryStatus==='ready'&&classificationRecoveryCount<16);const parametersSHA=await hashBytes(w,classificationParametersBytes(p));guard(w);need(snapshotCurrent(wanted));
   const old=classificationIntent();need(!old||['queued','rejected'].includes(old.phase));need(classificationIntents.has(wanted.meta.result_id)||classificationIntents.size<16);
   const bound=classificationRecoveryScope;transport.classificationRecovery.assertRoom(bound.actor,bound.scope,{v:1,actor_id:bound.actor,project_id:projectId,file_id:fileId,source_result_id:wanted.meta.result_id,source_meta_sha256:wanted.metaSHA,source_manifest_sha256:wanted.meta.manifest_sha256,request_id:'00000000-0000-4000-8000-000000000000',parameters:p,parameters_sha256:parametersSHA,core:CLASSIFICATION_CORE,disposition:'unknown',queued:null});
   const request=cryptoImpl.randomUUID();need(uuid(request));const body=classificationJobInput(new TextEncoder().encode(canonicalJSON({request_id:request,source_result_id:wanted.meta.result_id,parameters:p})));
   ref={body,parentMetaSHA:wanted.metaSHA,baseKey:wanted.baseKey,sourceManifestSHA:wanted.meta.manifest_sha256,parametersSHA,expectedBindingSHA:checked,phase:'submitting',task:null};
   classificationIntents.set(wanted.meta.result_id,ref);w.intent=ref;emit();need(await persistClassificationRecovery(w,ref));
   const task=await classificationJobJSON(w,'classification-submit',ref);guard(w);need(snapshotCurrent(wanted));ref.task=task;ref.phase='queued';ref.needsConfirmation=false;ref.retryAllowed=false;ref.retryBlocked=true;await persistClassificationRecovery(w,ref,task);accepted=true;emit();
  }catch(error){if(w){if(error?.message==='classification_request_recovery_unavailable')classificationRecoveryStatus='blocked';jobFailure(w,ref,error,true);}else classificationJobNotice='unavailable';}
  finally{classificationJobNotice=classificationIntent()?'idle':classificationJobNotice==='submitting'?'unavailable':classificationJobNotice;if(w)finish(w);else emit();}
  if(accepted&&classificationIntent()===ref)await readClassification(ref.body.request_id,ref.task);return accepted;
 }
 async function checkClassificationJob(){
  const ref=classificationIntent(),wanted=snapshot;if(!ref||op||['submitting','retrying'].includes(classificationJobNotice))return false;ref.retryAllowed=false;classificationJobNotice='idle';clearClassification('idle',false);let w,accepted=false;
  try{w=begin('classification-job-check',wanted.job.request_id);await restoreClassificationRecovery(w,wanted);const task=await classificationJobJSON(w,'classification-job-lookup',ref);guard(w);need(snapshotCurrent(wanted));ref.task=task;ref.phase='queued';ref.needsConfirmation=false;ref.retryAllowed=false;ref.retryBlocked=true;await persistClassificationRecovery(w,ref,task);accepted=true;emit();}
  catch(error){if(w)jobFailure(w,ref,error,false);else{ref.phase='unavailable';emit();}}
  finally{if(w)finish(w);}
  if(accepted&&classificationIntent()===ref)await readClassification(ref.body.request_id,ref.task);return accepted;
 }
 function retryEligible(ref){return !!ref&&ref.retryAllowed===true&&!ref.retryBlocked&&!ref.task&&!ref.recoveryAnchor;}
 function canRetryClassification(){const ref=classificationIntent();return retryEligible(ref)&&classificationRecoveryStatus==='ready'&&!op&&!['submitting','retrying'].includes(classificationJobNotice)&&transport.artifactScope?.busy?.()!==true;}
 async function retryClassification(){
  const ref=classificationIntent(),wanted=snapshot;if(!canRetryClassification())return false;
  // Eligibility is volatile evidence from this Owner's explicit response/GET,
  // never restored from unknown/confirmed storage hints. Consume before awaits.
  ref.retryAllowed=false;ref.phase='submitting';classificationJobNotice='retrying';const previous=clearClassification('idle',false);emit();await previous;
  if(!snapshotCurrent(wanted)||classificationIntent()!==ref){ref.phase='unknown';classificationJobNotice='idle';return false;}
  let w,accepted=false;
  try{
   w=begin('classification-submit',wanted.job.request_id);w.intent=ref;w.expected=wanted;
   await restoreClassificationRecovery(w,wanted);need(classificationRecoveryStatus==='ready'&&classificationIntent()===ref&&!ref.task&&!ref.recoveryAnchor&&!ref.retryBlocked);
   const {job}=await freshJob(w),parent=await metadata(w,job);need(!parent.phase&&snapshotCurrent(wanted)&&parent.metaSHA===ref.parentMetaSHA&&parent.meta.result_id===ref.body.source_result_id&&parent.meta.manifest_sha256===ref.sourceManifestSHA);
   need(await hashBytes(w,classificationParametersBytes(ref.body.parameters))===ref.parametersSHA);
   // Upsert/readback the SAME unknown ref, even at16 refs. No append/new UUID.
   need(await persistClassificationRecovery(w,ref));
   const task=await classificationJobJSON(w,'classification-submit',ref);guard(w);need(snapshotCurrent(wanted));ref.task=task;ref.phase='queued';ref.needsConfirmation=false;ref.retryAllowed=false;ref.retryBlocked=true;await persistClassificationRecovery(w,ref,task);accepted=true;emit();
  }catch(error){if(w)jobFailure(w,ref,error,true);else{ref.phase='unknown';emit();}}
  finally{classificationJobNotice='idle';if(w)finish(w);else emit();}
  if(accepted&&classificationIntent()===ref)await readClassification(ref.body.request_id,ref.task);return accepted;
 }
 function classificationJobView(){const ref=classificationIntent();return freeze({retry_eligible:retryEligible(ref),can_retry:canRetryClassification(),recovery_status:classificationRecoveryStatus,recovery_count:classificationRecoveryCount,recovered_hint:ref?.needsConfirmation===true,phase:classificationRecoveryStatus==='blocked'&&!ref?'recovery_unavailable':classificationJobNotice!=='idle'?classificationJobNotice:ref?.phase||'idle',has_reference:!!ref,parameters:ref?.body.parameters||null,source_result_id:ref?.body.source_result_id||null,task:ref?.task||null,binding_verification:ref?.expectedBindingSHA?'checked_parent_proof':ref?.task?'service_summary_only':null});}
 function snapshotCurrent(s){return !disposed&&snapshot===s&&s.generation===generation&&!s.controller.signal.aborted&&baseKey()===s.baseKey;}
 async function download({name,pin,verifiedOwnerContext,signal}){const wanted=snapshot;if(!wanted||!snapshotCurrent(wanted)||op||signal.aborted||!DIFFERENTIAL_RESULT_NAMES.includes(name)||canonicalJSON(verifiedOwnerContext)!==canonicalJSON(wanted.context))fail();const expected=wanted.meta.artifacts.find(a=>a.name===name);need(expected&&canonicalJSON(pin)===canonicalJSON({bytes:expected.bytes,sha256:expected.sha256}));let w,handed=false,abort;
  try{w=begin('download',wanted.job.request_id);handoff=w;w.expected=wanted;abort=()=>cancel(w);signal.addEventListener('abort',abort,{once:true});const {job}=await freshJob(w),check=await metadata(w,job);need(!check.phase&&snapshotCurrent(wanted));const bytes=await artifact(w,check.meta,expected);guard(w);need(snapshotCurrent(wanted)&&typeof documentImpl?.createElement==='function'&&typeof documentImpl?.body?.append==='function'&&typeof urlImpl?.createObjectURL==='function'&&typeof urlImpl?.revokeObjectURL==='function'&&typeof BlobImpl==='function');
   const blob=new BlobImpl([bytes],{type:'application/octet-stream'});guard(w);w.url=urlImpl.createObjectURL(blob);guard(w);w.anchor=documentImpl.createElement('a');guard(w);w.anchor.href=w.url;w.anchor.download='differential_'+wanted.meta.result_id+'_'+name;w.anchor.style.display='none';documentImpl.body.append(w.anchor);guard(w);w.anchor.click();guard(w);handed=true;w.urlTimer=setTimer(()=>{revoke(w);w.controller.abort();release(w);},1000);return{status:'handed_off'};
  }catch(error){if(w)errorState(w,error);throw new DifferentialResultFailure();}finally{if(abort)signal.removeEventListener('abort',abort);if(w){if(!handed||!snapshotCurrent(wanted))revoke(w);finish(w);}}
 }
 return Object.freeze({select,download,clear,dispose,readClassification,clearClassification,submitClassification,checkClassificationJob,canSubmitClassification,retryClassification,canRetryClassification,
  rejectClassificationPresentation(){clearClassification('unavailable');},
  classificationPresentation(){const s=classificationSnapshot;if(!classificationCurrent(s))return null;return{parentMetadata:s.parentMetadata,parentPresentation:{verifiedOwnerContext:s.parent.context,rows:s.rows,artifacts:s.parentArtifacts,contextSignal:s.controller.signal,isCurrent:()=>classificationCurrent(s)},derivedMetadata:s.meta,artifacts:s.artifacts,contextSignal:s.controller.signal,isCurrent:()=>classificationCurrent(s),onDownload:downloadClassification,cryptoImpl};},rejectPresentation(){clear('unavailable');code='differential_output_invalid';emit();},
  view(){if(snapshot&&!snapshotCurrent(snapshot))clear();if(classificationSnapshot&&!classificationCurrent(classificationSnapshot))clearClassification();return Object.freeze({classificationJob:classificationJobView(),classification:Object.freeze({phase:classificationPhase,request_id:classificationRequestId,epoch:classificationGeneration,meta:classificationSnapshot?.meta||null}),phase,stage,code,request_id:requestId,run,meta:snapshot?.meta||null,busy:!!op,epoch:generation});},
  presentation(){const s=snapshot;if(!s||!snapshotCurrent(s))return null;return{verifiedOwnerContext:s.context,rows:s.rows,artifacts:s.artifacts,contextSignal:s.controller.signal,isCurrent:()=>snapshotCurrent(s),onDownload:download,cryptoImpl};},
  subscribe(fn){if(disposed)return()=>{};listeners.add(fn);return()=>listeners.delete(fn);}});
}
