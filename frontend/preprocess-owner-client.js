import {qcSource,sha,canonicalJSON,freeze,exact,uuid} from './raw-qc-contract.mjs';
import {strictJSON} from './sample-design-contract.mjs';
export const PREPROCESS_FAMILIES=freeze(['Intensity','LFQ intensity']);
const FLAG_KEYS=['contaminant','reverse','site_only'];
const DTO_KEYS=['schemaVersion','job_id','request_id','request_sha256','binding_sha256','source','quant_family','parameters','parameters_sha256','execution_sha256','status','created_at','result','computation_performed','worker_started','execution_registered'];
const REF_KEYS=['v','project_id','dataset_version_id','file_id','quant_family','filter_flags','request_id','job_id','source_binding_sha256','binding_sha256','execution_sha256','created_at'];
const STORE_KEY='cyomics:preprocessing:refs:v1';
const hex=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v);
const timestamp=v=>typeof v==='string'&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/.test(v)&&!v.startsWith('0000-')&&Number.isFinite(Date.parse(v))&&new Date(Date.parse(v)).toISOString()===v.slice(0,23)+'Z';
export class PreprocessFailure extends Error{constructor(code='invalid_response',status=503){super(code);this.code=code;this.status=status;}}
const need=(ok,code='invalid_response')=>{if(!ok)throw new PreprocessFailure(code);};
export function preprocessParameters(value){
  need(exact(value,['blank_policy','filter_flags','imputation','input_scale','log2','normalization','output_scale','preset','pseudocount','seed','steps','zero_policy']),'invalid_parameters');
  need(exact(value.filter_flags,FLAG_KEYS)&&FLAG_KEYS.every(k=>typeof value.filter_flags[k]==='boolean'),'invalid_parameters');
  const expected={blank_policy:'to_NA',filter_flags:{...value.filter_flags},imputation:'none',input_scale:'raw_nonnegative',log2:'positive_only',normalization:'none',output_scale:'log2',preset:'positive_log2_zero_na_no_impute_v1',pseudocount:0,seed:null,steps:['flags_filter','zero_and_blank_to_NA','log2_positive','normalization_none','imputation_none'],zero_policy:'to_NA'};
  need(canonicalJSON(value)===canonicalJSON(expected),'invalid_parameters');return freeze(expected);
}
const parametersFor=flags=>preprocessParameters({blank_policy:'to_NA',filter_flags:flags,imputation:'none',input_scale:'raw_nonnegative',log2:'positive_only',normalization:'none',output_scale:'log2',preset:'positive_log2_zero_na_no_impute_v1',pseudocount:0,seed:null,steps:['flags_filter','zero_and_blank_to_NA','log2_positive','normalization_none','imputation_none'],zero_policy:'to_NA'});
export function preprocessingRoute({kind,projectId,datasetVersionId,requestId,jobId}){
  need(uuid(projectId),'invalid_request');let path,method='GET';
  if(kind==='submit'||kind==='lookup'){need(uuid(datasetVersionId),'invalid_request');path='/api/v1/datasets/'+datasetVersionId+'/preprocessing-jobs?project_id='+projectId;if(kind==='submit')method='POST';else{need(uuid(requestId),'invalid_request');path+='&request_id='+requestId;}}
  else{need(kind==='job'&&uuid(jobId),'invalid_request');path='/api/v1/preprocessing-jobs/'+jobId+'?project_id='+projectId;}
  return freeze({path,method});
}
function validRef(ref){
  if(!exact(ref,REF_KEYS)||ref.v!==1||!['project_id','dataset_version_id','file_id','request_id'].every(k=>uuid(ref[k]))||!PREPROCESS_FAMILIES.includes(ref.quant_family)||!hex(ref.source_binding_sha256)||!exact(ref.filter_flags,FLAG_KEYS)||!FLAG_KEYS.every(k=>typeof ref.filter_flags[k]==='boolean'))return false;
  return ref.job_id===null?ref.binding_sha256===null&&ref.execution_sha256===null&&ref.created_at===null:uuid(ref.job_id)&&hex(ref.binding_sha256)&&hex(ref.execution_sha256)&&timestamp(ref.created_at);
}
const refKey=r=>canonicalJSON([r.project_id,r.dataset_version_id,r.file_id,r.source_binding_sha256,r.quant_family,r.filter_flags]);
export function createPreprocessRecovery(storage){
  const store=()=>{try{const value=storage===undefined?globalThis.sessionStorage:storage;need(value&&['getItem','setItem','removeItem'].every(k=>typeof value[k]==='function'),'recovery_unavailable');return value;}catch{throw new PreprocessFailure('recovery_unavailable');}};
  function envelope(){const text=store().getItem(STORE_KEY);if(text===null)return null;need(typeof text==='string'&&new TextEncoder().encode(text).length<=32768,'recovery_unavailable');let v;try{v=strictJSON(new TextEncoder().encode(text));}catch{throw new PreprocessFailure('recovery_unavailable');}need(exact(v,['v','scope','items'])&&v.v===1&&hex(v.scope)&&Array.isArray(v.items)&&v.items.length<=64&&v.items.every(validRef)&&new Set(v.items.map(refKey)).size===v.items.length,'recovery_unavailable');return v;}
  const load=scope=>{try{need(hex(scope),'recovery_unavailable');const v=envelope();return v?.scope===scope?structuredClone(v.items):[];}catch{throw new PreprocessFailure('recovery_unavailable');}};
  const put=(scope,ref)=>{try{need(validRef(ref),'recovery_unavailable');const items=load(scope).filter(v=>refKey(v)!==refKey(ref));items.push(structuredClone(ref));const text=canonicalJSON({v:1,scope,items});need(items.length<=64&&new TextEncoder().encode(text).length<=32768,'recovery_unavailable');const s=store();s.setItem(STORE_KEY,text);need(s.getItem(STORE_KEY)===text,'recovery_unavailable');}catch{throw new PreprocessFailure('recovery_unavailable');}};
  const clear=scope=>{try{const v=envelope();if(v?.scope===scope)store().removeItem(STORE_KEY);}catch{}};
  return Object.freeze({load,put,clear});
}
// getOptions is supplied by the existing same-origin authenticated host. This
// adapter never reads JWTs/cookies or exposes/stores its account-context header.
export function createPreprocessOwnerTransport({featureEnabled=false,projectId,datasetVersionId,fetchImpl,getOptions,isCurrent,storage,cryptoImpl=globalThis.crypto}={}){
  if(featureEnabled!==true||![fetchImpl,getOptions,isCurrent].every(fn=>typeof fn==='function'))return null;
  need(uuid(projectId)&&uuid(datasetVersionId),'invalid_request');let firstNonce=null;
  function context(signal){need(isCurrent()&&!signal.aborted,'context_changed');const o=getOptions(signal),nonce=new Headers(o?.headers).get('X-Cyomics-Account-Context');need(o?.credentials==='same-origin'&&o.cache==='no-store'&&o.redirect==='error'&&typeof nonce==='string'&&/^[A-Za-z0-9_-]{22}$/.test(nonce),'context_changed');if(firstNonce===null)firstNonce=nonce;need(firstNonce===nonce,'context_changed');return{nonce,options:o};}
  return Object.freeze({isCurrent,recovery:createPreprocessRecovery(storage),async scope(){const signal=new AbortController().signal,{nonce}=context(signal),scope=await sha({domain:'cyomics/preprocessing/account/v1',context:nonce},cryptoImpl);need(context(signal).nonce===nonce,'context_changed');return scope;},
    send({kind,requestId,jobId,body,signal}){const r=preprocessingRoute({kind,projectId,datasetVersionId,requestId,jobId}),{nonce}=context(signal),headers={'X-Cyomics-Account-Context':nonce};if(r.method==='POST'){need(typeof body==='string'&&new TextEncoder().encode(body).length<=4096,'invalid_request');const parsed=strictJSON(new TextEncoder().encode(body));need(exact(parsed,['request_id','quant_family','parameters'])&&parsed.request_id===requestId&&PREPROCESS_FAMILIES.includes(parsed.quant_family),'invalid_request');preprocessParameters(parsed.parameters);headers['Content-Type']='application/json';headers['X-Cyomics-Request']='1';}return fetchImpl(r.path,{credentials:'same-origin',cache:'no-store',redirect:'error',signal,method:r.method,headers,...(r.method==='POST'?{body}:{})});}});
}
export async function validatePreprocessQueued(value,{source,body,reference=null,previous=null,method='GET',status=200,cryptoImpl=globalThis.crypto}){
  need(exact(value,DTO_KEYS)&&value.schemaVersion===1&&uuid(value.job_id)&&value.request_id===body.request_id&&uuid(body.request_id));
  need((method==='POST'&&[200,201].includes(status))||(method==='GET'&&status===200));
  need(value.status==='queued'&&value.result===null&&value.computation_performed===false&&value.worker_started===false&&value.execution_registered===false&&timestamp(value.created_at));
  need(value.quant_family===body.quant_family&&canonicalJSON(value.parameters)===canonicalJSON(preprocessParameters(body.parameters))&&canonicalJSON(value.source)===canonicalJSON(source));
  need(hex(value.request_sha256)&&hex(value.parameters_sha256)&&hex(value.binding_sha256)&&hex(value.execution_sha256));
  need(value.parameters_sha256===await sha(body.parameters,cryptoImpl));
  need(value.request_sha256===await sha({project_id:source.project_id,dataset_version_id:source.dataset_version_id,request:body},cryptoImpl));
  if(reference?.job_id)for(const key of ['job_id','binding_sha256','execution_sha256','created_at'])need(value[key]===reference[key]);
  if(previous)need(canonicalJSON(value)===canonicalJSON(previous));
  // These two opaque server fingerprints are format-/immutability-checked,
  // not independently recomputed execution/Owner attestations.
  return freeze(structuredClone(value));
}

export function createPreprocessOwnerClient({featureEnabled=false,fullSource=null,transport=null,isCurrent=()=>true,onDenied=()=>{},contextSignal=null,cryptoImpl=globalThis.crypto,setTimer=setTimeout,clearTimer=clearTimeout,now=()=>performance.now()}={}){
  const enabled=featureEnabled===true;let disposed=false,generation=0,scope=null,sources=new Map(),sourceHashes=new Map(),ids=null,attempt=null,job=null,op=null;
  let phase=!enabled?'disabled':!transport?'not_connected':'preparing',code=null;const listeners=new Set();
  const emit=()=>{for(const fn of [...listeners])try{fn();}catch{}};
  function cancel(work,reason='timeout'){if(!work)return;work.reason=reason;clearTimer(work.timer);work.controller.abort();try{void(work.reader?.cancel()||work.response?.body?.cancel())?.catch(()=>{});}catch{}}
  function dispose(reason='disposed'){if(disposed)return;disposed=true;generation++;cancel(op,reason);op=null;sources.clear();sourceHashes.clear();ids=null;attempt=null;job=null;scope=null;fullSource=null;phase=reason==='unauthenticated'?'unauthenticated':'disposed';code=reason;if(enabled&&transport)contextSignal?.removeEventListener('abort',lost);emit();listeners.clear();}
  function lost(){dispose('context_changed');}
  function live(){if(disposed||!enabled||!transport)return false;let ok=false;try{ok=isCurrent()===true&&transport.isCurrent()===true&&!contextSignal?.aborted;}catch{}if(!ok)dispose('context_changed');return ok;}
  const set=(next,error=null)=>{phase=next;code=error;emit();};
  const key=()=>scope?'preprocessing:'+scope:null;
  function deny(status){if(scope)transport.recovery.clear(scope);dispose('unauthenticated');try{onDenied({status,body:{}});}catch{}}
  function guard(work){if(!live()||op!==work||work.generation!==generation||work.controller.signal.aborted||now()>=work.deadline){cancel(work,work.reason||(now()>=work.deadline?'timeout':'context_changed'));throw new PreprocessFailure(work.reason);}}
  const wait=async(work,promise)=>{const value=await Promise.race([promise,work.aborted]);guard(work);return value;};
  function begin(kind){const work={kind,generation,controller:new AbortController(),deadline:now()+(kind==='submit'?30000:15000),reader:null,response:null,sent:false,reason:null,timer:null};work.aborted=new Promise((_,reject)=>work.controller.signal.addEventListener('abort',()=>reject(new PreprocessFailure(work.reason||'timeout')),{once:true}));void work.aborted.catch(()=>{});op=work;work.timer=setTimer(()=>cancel(work,'timeout'),kind==='submit'?30000:15000);return work;}
  function finish(work){cancel(work,'finished');if(op===work)op=null;if(!disposed)emit();}
  const bodyFor=ref=>freeze({request_id:ref.request_id,quant_family:ref.quant_family,parameters:parametersFor(ref.filter_flags)});
  async function request(work,kind){
    const body=bodyFor(attempt);guard(work);let response;
    try{
      if(kind==='submit')work.sent=true;
      const pending=Promise.resolve(transport.send({kind,requestId:attempt.request_id,jobId:attempt.job_id,body:kind==='submit'?canonicalJSON(body):undefined,signal:work.controller.signal})).then(r=>{try{guard(work);return r;}catch(e){try{void r.body?.cancel()?.catch(()=>{});}catch{}throw e;}});
      response=await wait(work,pending);work.response=response;
      if([401,403].includes(response.status)){deny(response.status);throw new PreprocessFailure('unauthenticated',response.status);}
      need(!response.redirected&&/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(response.headers.get('content-type')||'')&&[null,'identity'].includes(response.headers.get('content-encoding')));
      const cap=[200,201].includes(response.status)?32768:4096,length=response.headers.get('content-length');need(length===null||/^\d{1,6}$/.test(length)&&Number(length)<=cap);
      work.reader=response.body?.getReader();need(work.reader);const chunks=[];let size=0;
      for(;;){const piece=await wait(work,work.reader.read());if(piece.done)break;need(piece.value instanceof Uint8Array&&size+piece.value.length<=cap);size+=piece.value.length;chunks.push(piece.value);}
      need(length===null||Number(length)===size);const bytes=new Uint8Array(size);let offset=0;for(const part of chunks){bytes.set(part,offset);offset+=part.length;}let value;try{value=strictJSON(bytes);}catch{throw new PreprocessFailure('invalid_response');}guard(work);
      if(![200,201].includes(response.status)){need(exact(value,['error'])&&exact(value.error,['code'])&&typeof value.error.code==='string'&&value.error.code.length<=128);throw new PreprocessFailure(response.status===404?'not_found':response.status===409?'conflict':response.status===413?'rejected':'unavailable',response.status);}
      return await wait(work,validatePreprocessQueued(value,{source:sources.get(attempt.quant_family),body,reference:attempt,previous:job,method:kind==='submit'?'POST':'GET',status:response.status,cryptoImpl}));
    }finally{try{void work.reader?.cancel()?.catch(()=>{});work.reader?.releaseLock();}catch{}work.reader=null;}
  }
  function accept(value,work){const next=freeze({...attempt,job_id:value.job_id,binding_sha256:value.binding_sha256,execution_sha256:value.execution_sha256,created_at:value.created_at});transport.recovery.put(scope,next);guard(work);attempt=next;job=value;set('queued');}
  function failure(work,error){if(disposed)return;if(!live())return;const safe=error instanceof PreprocessFailure?error.code:'unavailable';job=null;set(work.kind==='submit'&&work.sent?(error.status===409?'conflict':error.status===413?'rejected':'unknown'):safe==='not_found'?'not_found':safe==='conflict'?'conflict':'unavailable',safe);}
  async function check(){if(!live()||op||!attempt||!scope)return false;const work=begin('lookup');set('checking');try{const value=await request(work,'lookup');accept(value,work);return true;}catch(error){failure(work,error);return false;}finally{finish(work);}}
  const ready=(async()=>{
    if(!enabled||!transport){fullSource=null;return;}
    try{need(live(),'context_changed');const saved=freeze(structuredClone(fullSource));fullSource=null;const available=PREPROCESS_FAMILIES.filter(f=>saved.importDTO?.importReport?.sample_columns?.some(c=>c.quant_family===f));need(available.length>0,'source_unavailable');
      for(const family of available){const ref=await qcSource(saved,family==='Intensity'?'intensity':'lfq',cryptoImpl);need(live(),'context_changed');const fingerprint=await sha(ref,cryptoImpl);need(live(),'context_changed');sources.set(family,ref);sourceHashes.set(family,fingerprint);}
      const first=sources.values().next().value;ids=freeze({project_id:first.project_id,dataset_version_id:first.dataset_version_id,file_id:first.file_id});scope=await transport.scope();need(live()&&hex(scope),'context_changed');const refs=transport.recovery.load(scope).filter(r=>r.project_id===ids.project_id&&r.dataset_version_id===ids.dataset_version_id&&r.file_id===ids.file_id);need(live(),'context_changed');
      if(refs.length){const ref=refs.at(-1);need(sourceHashes.get(ref.quant_family)===ref.source_binding_sha256,'source_mismatch');attempt=freeze(ref);set('unknown');await check();}else set('idle');
    }catch(error){fullSource=null;if(!disposed)set('unavailable',error instanceof PreprocessFailure?error.code:'source_unavailable');}
  })();
  async function submit(input){
    if(!live()||op||!ids||!scope||!['idle','queued','unknown','unavailable','not_found','conflict','rejected'].includes(phase))return false;
    try{need(exact(input,['contextKey','source','quant_family','parameters'])&&input.contextKey===key()&&canonicalJSON(input.source)===canonicalJSON(ids)&&sources.has(input.quant_family),'context_changed');const params=preprocessParameters(input.parameters);
      const existing=transport.recovery.load(scope).find(r=>r.project_id===ids.project_id&&r.dataset_version_id===ids.dataset_version_id&&r.file_id===ids.file_id&&r.quant_family===input.quant_family&&r.source_binding_sha256===sourceHashes.get(input.quant_family)&&canonicalJSON(r.filter_flags)===canonicalJSON(params.filter_flags));need(live(),'context_changed');
      if(existing){if(attempt?.request_id!==existing.request_id)job=null;attempt=freeze(existing);return check();}
      const ref=freeze({v:1,...ids,quant_family:input.quant_family,filter_flags:{...params.filter_flags},request_id:cryptoImpl.randomUUID(),job_id:null,source_binding_sha256:sourceHashes.get(input.quant_family),binding_sha256:null,execution_sha256:null,created_at:null});need(validRef(ref),'invalid_request');transport.recovery.put(scope,ref);need(live(),'context_changed');attempt=ref;job=null;
    }catch(error){if(!disposed)set('unavailable',error instanceof PreprocessFailure?error.code:'invalid_request');return false;}
    const work=begin('submit');set('submitting');try{const value=await request(work,'submit');accept(value,work);return true;}catch(error){failure(work,error);return false;}finally{finish(work);}
  }
  if(enabled&&transport&&contextSignal&&!disposed){if(contextSignal.aborted)lost();else contextSignal.addEventListener('abort',lost,{once:true});}
  return Object.freeze({ready,submit,check,dispose,
    parameterSource(){return live()&&ids&&scope?freeze({contextKey:key(),datasetVersion:{...ids},availableQuantFamilies:[...sources.keys()]}):null;},
    reference(){if(enabled&&transport)live();return attempt?freeze(structuredClone(attempt)):null;},
    view(){if(enabled&&transport)live();return freeze({phase,code,quant_family:attempt?.quant_family||null,filter_flags:attempt?{...attempt.filter_flags}:null,request_id:attempt?.request_id||null,job_id:job?.job_id||null,can_check:!disposed&&!!attempt&&!op,analysis_ready:false,computation_performed:false,worker_started:false,execution_registered:false});},
    subscribe(fn){if(disposed||!enabled)return()=>{};listeners.add(fn);return()=>listeners.delete(fn);}
  });
}
