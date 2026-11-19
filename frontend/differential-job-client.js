import {sha} from './raw-qc-contract.mjs';
import {exact,uuid,freeze,canonicalJSON} from './sample-design-contract.mjs';
import {parsePreprocessJSON} from './preprocess-owner-wire.mjs';
import {differentialJobInput,differentialJobRoute,differentialJobReply,differentialJobError,DifferentialJobFailure,jobHash,DIFFERENTIAL_HISTORY_MAX} from './differential-job-contract.mjs';
import {classificationParameters,CLASSIFICATION_CORE} from './de-classification-read-contract.mjs';
import {time} from './preprocess-owner-wire.mjs';
const fail=code=>{throw new DifferentialJobFailure(503,code);};
const KEY='cyomics:differential:refs:v1';
const refKeys=['v','project_id','file_id','parent_dataset_version_id','request_id','selection','source_sha256','input_manifest','job_id','task_sha256','disposition'];
function validRef(r){try{
 if(!exact(r,refKeys)||r.v!==1||!['project_id','file_id','parent_dataset_version_id','request_id'].every(k=>uuid(r[k]))||!jobHash(r.source_sha256)
  ||!['unknown','queued','rejected'].includes(r.disposition)||!(r.job_id===null&&r.task_sha256===null||uuid(r.job_id)&&jobHash(r.task_sha256))
  ||r.disposition==='queued'&&r.job_id===null||r.disposition!=='queued'&&r.job_id!==null)return false;
 differentialJobInput(new TextEncoder().encode(canonicalJSON({...r.selection,request_id:r.request_id})));
 if(r.selection.contrast?.dataset_version_id!==r.parent_dataset_version_id||!exact(r.input_manifest,['matrix','design','preset']))return false;
 return Object.values(r.input_manifest).every(p=>exact(p,['bytes','sha256'])&&Number.isSafeInteger(p.bytes)&&p.bytes>=1&&p.bytes<=26214400&&jobHash(p.sha256));
}catch{return false;}}
export function createDifferentialRecovery(storage){
 const store=()=>{const s=storage===undefined?globalThis.sessionStorage:storage;if(!s||!['getItem','setItem','removeItem'].every(k=>typeof s[k]==='function'))fail('differential_recovery_unavailable');return s;};
 const read=()=>{const text=store().getItem(KEY);if(text===null)return null;if(typeof text!=='string'||new TextEncoder().encode(text).length>65536)fail('differential_recovery_unavailable');const v=parsePreprocessJSON(new TextEncoder().encode(text));
  if(!exact(v,['v','scope','items'])||v.v!==1||!jobHash(v.scope)||!Array.isArray(v.items)||v.items.length>16||!v.items.every(validRef)||new Set(v.items.map(r=>r.project_id+':'+r.request_id)).size!==v.items.length)fail('differential_recovery_unavailable');return v;};
 const load=scope=>{if(!jobHash(scope))fail('differential_recovery_unavailable');const v=read();return v?.scope===scope?structuredClone(v.items):[];};
 return Object.freeze({load,put(scope,ref){if(!validRef(ref))fail('differential_recovery_unavailable');const items=load(scope).filter(r=>r.project_id!==ref.project_id||r.request_id!==ref.request_id);items.push(structuredClone(ref));const text=canonicalJSON({v:1,scope,items});if(items.length>16||new TextEncoder().encode(text).length>65536)fail('differential_recovery_unavailable');store().setItem(KEY,text);if(store().getItem(KEY)!==text)fail('differential_recovery_unavailable');},clear(scope){try{if(read()?.scope===scope)store().removeItem(KEY);}catch{}}});
}
// Classification-specific request hints only. They never authorize an HTTP read
// or restore a result/queued state; the Owner must confirm the original request.
const CLASS_REF_KEYS=['v','actor_id','project_id','file_id','source_result_id','source_meta_sha256','source_manifest_sha256','request_id','parameters','parameters_sha256','core','disposition','queued'];
function validClassificationRef(r){try{
 if(!exact(r,CLASS_REF_KEYS)||r.v!==1||!['actor_id','project_id','file_id','source_result_id','request_id'].every(k=>uuid(r[k]))||!['source_meta_sha256','source_manifest_sha256','parameters_sha256'].every(k=>jobHash(r[k]))||canonicalJSON(r.core)!==canonicalJSON(CLASSIFICATION_CORE)||!['unknown','confirmed'].includes(r.disposition))return false;
 classificationParameters(r.parameters);
 if(r.disposition==='unknown')return r.queued===null;
 const q=r.queued;return exact(q,['classification_job_id','created_at','source_binding_sha256','dto_sha256'])&&uuid(q.classification_job_id)&&time(q.created_at)&&jobHash(q.source_binding_sha256)&&jobHash(q.dto_sha256);
}catch{return false;}}
export function createClassificationRecovery(storage){
 const blocked=()=>{throw Error('classification_request_recovery_unavailable');};
 const store=()=>{try{const s=storage===undefined?globalThis.sessionStorage:storage;if(!s||!['getItem','setItem','removeItem'].every(k=>typeof s[k]==='function'))blocked();return s;}catch{blocked();}};
 const key=actor=>'cyomics:classification:refs:v1:'+actor;
 function load(actor,scope){
  if(!uuid(actor)||!jobHash(scope))blocked();let raw;try{raw=store().getItem(key(actor));}catch{blocked();}if(raw===null)return[];
  try{if(typeof raw!=='string'||new TextEncoder().encode(raw).length>65536)blocked();const v=parsePreprocessJSON(new TextEncoder().encode(raw));
   if(!exact(v,['v','actor_id','scope','items'])||v.v!==1||v.actor_id!==actor||v.scope!==scope||!Array.isArray(v.items)||v.items.length>16||!v.items.every(r=>validClassificationRef(r)&&r.actor_id===actor)||new Set(v.items.map(r=>r.project_id+':'+r.request_id)).size!==v.items.length)blocked();
   return structuredClone(v.items);
  }catch{blocked();}
 }
 function put(actor,scope,ref){
  if(!validClassificationRef(ref)||ref.actor_id!==actor)blocked();const items=load(actor,scope),prior=items.find(r=>r.project_id===ref.project_id&&r.request_id===ref.request_id);
  // Never replace an original intent with changed tokens/source/Owner anchors.
  if(prior){const identity=r=>Object.fromEntries(CLASS_REF_KEYS.filter(k=>!['queued','disposition'].includes(k)).map(k=>[k,r[k]]));if(canonicalJSON(identity(prior))!==canonicalJSON(identity(ref))||prior.queued&&ref.queued&&canonicalJSON(prior.queued)!==canonicalJSON(ref.queued))blocked();}
  const next=items.filter(r=>r.project_id!==ref.project_id||r.request_id!==ref.request_id);next.push(structuredClone(ref));const text=canonicalJSON({v:1,actor_id:actor,scope,items:next});
  if(next.length>16||new TextEncoder().encode(text).length>65536)blocked();
  try{const s=store();s.setItem(key(actor),text);if(s.getItem(key(actor))!==text)blocked();}catch{blocked();}
  return next;
 }
 function assertRoom(actor,scope,ref){
  if(!validClassificationRef(ref)||ref.actor_id!==actor)blocked();const next=[...load(actor,scope),ref];
  if(next.length>16||new TextEncoder().encode(canonicalJSON({v:1,actor_id:actor,scope,items:next})).length>65536)blocked();
 }
 return Object.freeze({load,put,assertRoom}); // No implicit cleanup/eviction.
}
// Admission only: this client has no execution/result endpoint or scheduler.
export function createDifferentialJobClient({projectId,fileId,transport,isCurrent,onDenied=()=>{},contextSignal=null,cryptoImpl=globalThis.crypto,setTimer=setTimeout,clearTimer=clearTimeout,now=()=>performance.now()}={}){
 let disposed=false,scope=null,ready=false,op=null,code=null,rejection=null;const entries=new Map(),listeners=new Set();
 let historySource=null,historyKey=null,historyPhase='idle',historyCode=null;const discovered=new Map();
 function bindHistorySource(source){const key=source?canonicalJSON(source):null;if(key===historyKey)return;historyKey=key;historySource=source?freeze(structuredClone(source)):null;historyPhase='idle';historyCode=null;discovered.clear();if(op?.kind==='history'||op?.discoveryKey)cancel();}
 const emit=()=>{if(!disposed)for(const fn of [...listeners])fn();};
 const current=()=>!disposed&&!contextSignal?.aborted&&isCurrent()===true&&transport?.isCurrent()===true;
 const cancel=()=>{if(op)op.controller.abort();};
 const dispose=()=>{if(disposed)return;cancel();bindHistorySource(null);disposed=true;contextSignal?.removeEventListener('abort',dispose);entries.clear();scope=null;ready=false;for(const fn of [...listeners])fn();listeners.clear();};
 contextSignal?.addEventListener('abort',dispose,{once:true});
 const deny=(status,context=false)=>{dispose();onDenied({status,body:context?{error:{code:'project_context_changed'}}:{}});};
 const guard=w=>{const stamp=now();if(!current()||op!==w||w.controller.signal.aborted||!Number.isFinite(stamp)||stamp<w.last||stamp-w.start>=15000||w.currentSelection&&w.currentSelection()!==true){w.controller.abort();fail('differential_unavailable');}w.last=stamp;w.checkContext?.();};
 function begin(kind,currentSelection=null){const w={kind,currentSelection,controller:new AbortController(),start:now(),last:now(),reader:null,sent:false,ref:null};
  w.aborted=new Promise((_,reject)=>w.controller.signal.addEventListener('abort',()=>reject(new DifferentialJobFailure(504,kind==='submit'?'differential_submission_unknown':'differential_timeout')),{once:true}));void w.aborted.catch(()=>{});
  op=w;w.timer=setTimer(()=>w.controller.abort(),15000);code=null;rejection=null;emit();return w;}
 const wait=async(w,p)=>{const result=await Promise.race([p,w.aborted]);guard(w);return result;};
 const remember=(ref,phase,task=null)=>{entries.set(ref.request_id,{ref:freeze(structuredClone(ref)),phase,task});};
 const persist=ref=>{try{transport.recovery.put(scope,ref);}catch{fail('differential_recovery_unavailable');}};
 async function json(w,args){guard(w);if(args.kind==='submit')w.sent=true;const received=await wait(w,transport.send({...args,signal:w.controller.signal}));w.checkContext=received.checkCurrent;guard(w);const response=received.response;
  if([401,403].includes(response.status)){deny(response.status);fail('differential_unavailable');}
  if(response.redirected||!/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(response.headers.get('content-type')||'')||![null,'identity'].includes(response.headers.get('content-encoding'))||['location','refresh'].some(k=>response.headers.has(k)))fail('differential_unavailable');
  const cap=args.kind==='history'&&response.status===200?DIFFERENTIAL_HISTORY_MAX:[200,201].includes(response.status)?32768:response.status===422?16384:4096,length=response.headers.get('content-length');if(length!==null&&(!/^[0-9]{1,10}$/.test(length)||Number(length)>cap))fail('differential_unavailable');
  w.reader=response.body?.getReader();if(!w.reader)fail('differential_unavailable');let size=0;const parts=[];
  for(;;){const part=await wait(w,w.reader.read());if(part.done)break;if(!(part.value instanceof Uint8Array)||size+part.value.length>cap)fail('differential_unavailable');parts.push(part.value);size+=part.value.length;}
  if(length!==null&&Number(length)!==size)fail('differential_unavailable');const bytes=new Uint8Array(size);let at=0;for(const part of parts){bytes.set(part,at);at+=part.length;}
  const value=parsePreprocessJSON(bytes),route=differentialJobRoute('/api/v1/projects/'+projectId+'/differential-jobs'+(args.kind==='submit'?'':args.kind==='history'?'?processed_dataset_version_id='+args.processedVersionId:'?request_id='+args.requestId),args.kind==='submit'?'POST':'GET');
  if(![200,201].includes(response.status)&&!(response.status===422&&value?.queued===false)){const error=differentialJobError(response.status,value);if(response.status===409&&error.error.code==='project_context_changed')deny(409,true);throw new DifferentialJobFailure(response.status,error.error.code);}
  return{status:response.status,value:differentialJobReply(route,response.status,value,args.kind==='submit'?args.body:null)};
 }
 async function accept(w,value){const ref=w.ref;if(value.request_id!==ref.request_id||canonicalJSON(value.selection)!==canonicalJSON(ref.selection)||value.source.file_id!==fileId||value.source.parent_dataset_version_id!==ref.parent_dataset_version_id
  ||canonicalJSON(value.input_manifest)!==canonicalJSON(ref.input_manifest)||ref.job_id!==null&&value.job_id!==ref.job_id)fail('differential_unavailable');
  if(await wait(w,sha(value.source,cryptoImpl))!==ref.source_sha256)fail('differential_unavailable');const fingerprint=await wait(w,sha(value,cryptoImpl));if(ref.task_sha256!==null&&fingerprint!==ref.task_sha256)fail('differential_unavailable');
  const next={...ref,job_id:value.job_id,task_sha256:fingerprint,disposition:'queued'};guard(w);remember(next,'queued',value);try{persist(next);}catch{code='differential_recovery_unavailable';}return true;
 }
 function failed(w,error){if(disposed||op!==w)return;if(error?.message==='project_context_changed'){deny(409,true);return;}
  code=error instanceof DifferentialJobFailure?error.code:'differential_unavailable';if(w.ref&&!w.discoveryOnly){const old=entries.get(w.ref.request_id);remember(old?.ref||w.ref,w.kind==='submit'?(w.sent?'unknown':'not_sent'):'unconfirmed');}emit();
 }
 function finish(w){clearTimer(w.timer);try{void w.reader?.cancel()?.catch(()=>{});w.reader?.releaseLock();}catch{}if(op===w)op=null;w.controller.abort();emit();}
 const loaded=(async()=>{if(!current())return;try{const signal=new AbortController().signal;scope=await transport.scope(signal);if(!current())return;for(const ref of transport.recovery.load(scope).filter(r=>r.project_id===projectId&&r.file_id===fileId))remember(ref,'needs_check');ready=true;emit();}catch{code='differential_recovery_unavailable';emit();}})();
 async function submit({selection,source,inputManifest,currentSelection}){
  if(!current()||!ready||op||typeof currentSelection!=='function'||!currentSelection()||[...entries.values()].some(e=>e.ref.disposition==='unknown'&&canonicalJSON(e.ref.selection)===canonicalJSON(selection)))return false;
  const w=begin('submit',currentSelection);
  try{const sourceSHA=await wait(w,sha(source,cryptoImpl));guard(w);const requestId=cryptoImpl.randomUUID();if(!uuid(requestId)||entries.has(requestId))fail('differential_unavailable');
   const ref={v:1,project_id:projectId,file_id:fileId,parent_dataset_version_id:source.parent_dataset_version_id,request_id:requestId,selection:structuredClone(selection),source_sha256:sourceSHA,input_manifest:structuredClone(inputManifest),job_id:null,task_sha256:null,disposition:'unknown'};
   if(!validRef(ref)||source.project_id!==projectId||source.file_id!==fileId||source.processed_dataset_version_id!==selection.processed_dataset_version_id)fail('differential_unavailable');w.ref=freeze(ref);remember(ref,'submitting');persist(ref);guard(w);emit();
   const body=differentialJobInput(new TextEncoder().encode(canonicalJSON({...selection,request_id:requestId}))),reply=await json(w,{kind:'submit',body});
   if(reply.status===422){const {queued,request_id,...review}=reply.value;const next={...ref,disposition:'rejected'};remember(next,'rejected');persist(next);rejection={request_id,selection:ref.selection,review};return false;}
   return await accept(w,reply.value);
  }catch(error){failed(w,error);return false;}finally{finish(w);}
 }
 async function loadHistory(){if(!current()||!ready||op||!historySource)return false;const source=historySource,key=historyKey,w=begin('history',()=>historyKey===key);historyPhase='loading';historyCode=null;discovered.clear();emit();
  try{const reply=await json(w,{kind:'history',processedVersionId:source.processed_dataset_version_id}),next=new Map();
   for(const task of reply.value.jobs){guard(w);if(task.source.file_id!==fileId||canonicalJSON(task.source)!==key)fail('differential_unavailable');const sourceSHA=await wait(w,sha(task.source,cryptoImpl)),taskSHA=await wait(w,sha(task,cryptoImpl));
    const ref={v:1,project_id:projectId,file_id:fileId,parent_dataset_version_id:source.parent_dataset_version_id,request_id:task.request_id,selection:task.selection,source_sha256:sourceSHA,input_manifest:task.input_manifest,job_id:task.job_id,task_sha256:taskSHA,disposition:'queued'};
    if(!validRef(ref))fail('differential_unavailable');const local=entries.get(ref.request_id)?.ref;if(local&&(local.source_sha256!==ref.source_sha256||canonicalJSON(local.selection)!==canonicalJSON(ref.selection)||canonicalJSON(local.input_manifest)!==canonicalJSON(ref.input_manifest)||local.job_id!==null&&(local.job_id!==ref.job_id||local.task_sha256!==ref.task_sha256)))fail('differential_unavailable');
    next.set(ref.request_id,{ref:freeze(structuredClone(ref)),task});
   }guard(w);for(const [id,item] of next)discovered.set(id,item);historyPhase='ready';emit();return true;
  }catch(error){if(current()&&op===w&&historyKey===key){historyPhase='unavailable';historyCode=error instanceof DifferentialJobFailure?error.code:'differential_unavailable';discovered.clear();emit();}return false;}finally{finish(w);}
 }
 async function check(requestId,{signal=null}={}){if(signal?.aborted||!current()||!ready||op||!entries.has(requestId)&&!discovered.has(requestId))return false;const local=entries.get(requestId),remote=discovered.get(requestId),key=historyKey,w=begin('lookup',remote?()=>historyKey===key:null),abort=()=>w.controller.abort();signal?.addEventListener('abort',abort,{once:true});w.ref=local?.ref||remote.ref;w.discoveryOnly=!local;w.discoveryKey=remote?key:null;if(local)remember(w.ref,'checking');emit();
  try{const reply=await json(w,{kind:'lookup',requestId});return await accept(w,reply.value);}catch(error){failed(w,error);return false;}finally{signal?.removeEventListener('abort',abort);finish(w);}
 }
 return Object.freeze({loaded,submit,check,dispose,bindHistorySource,loadHistory,cancelHistory(){if(op?.kind==='history')cancel();},invalidateSelection(){if(op?.kind==='submit')cancel();},
  view(){const live=current();if(!live)bindHistorySource(null);return freeze({ready:live&&ready,busy:live&&!!op,code:live?code:null,rejection:live?rejection:null,discovery:{phase:live?historyPhase:'idle',code:live?historyCode:null,processed_dataset_version_id:live?historySource?.processed_dataset_version_id||null:null,jobs:live?[...discovered.values()].map(e=>e.task):[]},history:live?[...entries.values()].map(e=>({request_id:e.ref.request_id,job_id:e.ref.job_id,selection:e.ref.selection,disposition:e.ref.disposition,phase:e.phase,task:e.task})):[]});},
  canSubmit(selection){return current()&&ready&&!op&&![...entries.values()].some(e=>e.ref.disposition==='unknown'&&canonicalJSON(e.ref.selection)===canonicalJSON(selection));},
  subscribe(fn){if(disposed)return()=>{};listeners.add(fn);return()=>listeners.delete(fn);}});
}
