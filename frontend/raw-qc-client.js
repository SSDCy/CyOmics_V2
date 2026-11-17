import {FAMILIES,RawQCFailure,requireQC,exact,uuid,freeze,canonicalJSON,sha,qcSource,qcRoute,qcRequest,parseQC,qcError,validateQCJob,MAX_RESPONSE,MAX_ERROR} from './raw-qc-contract.mjs';
const STORAGE_KEY='cyomics:raw-qc:refs:v1';
const browserStorage=()=>{try{return globalThis.sessionStorage;}catch{return null;}};
const storageFault=()=>{throw new RawQCFailure('raw_qc_recovery_unavailable');};
const refKeys=['v','project_id','file_id','dataset_version_id','quant_family','request_id','job_id'];
function reference(v){return exact(v,refKeys)&&v.v===1&&['project_id','file_id','dataset_version_id','request_id'].every(k=>uuid(v[k]))&&(v.job_id===null||uuid(v.job_id))&&Object.hasOwn(FAMILIES,v.quant_family);}
const refKey=v=>[v.project_id,v.file_id,v.dataset_version_id,v.quant_family].join(':');
export function clearRawQCRecovery(storage=browserStorage(),scope=null){try{if(!storage)return;const text=storage.getItem(STORAGE_KEY);if(scope&&text){const v=JSON.parse(text);if(v.scope!==scope)return;}storage.removeItem(STORAGE_KEY);}catch{}}
export function createQCRecovery(storage=browserStorage()){
  function load(scope){
    try{requireQC(typeof scope==='string'&&/^[0-9a-f]{64}$/.test(scope));if(!storage)storageFault();const text=storage.getItem(STORAGE_KEY);if(text===null)return[];const bytes=new TextEncoder().encode(text);if(bytes.length>16384)storageFault();const v=parseQC(bytes);
      if(!exact(v,['v','scope','items'])||v.v!==1||typeof v.scope!=='string'||!/^[0-9a-f]{64}$/.test(v.scope)||!Array.isArray(v.items)||v.items.length>64||!v.items.every(reference)||new Set(v.items.map(refKey)).size!==v.items.length)storageFault();
      if(v.scope!==scope){storage.removeItem(STORAGE_KEY);return[];}return structuredClone(v.items);
    }catch{storageFault();}
  }
  function put(scope,ref){try{if(!reference(ref))storageFault();const items=load(scope).filter(v=>refKey(v)!==refKey(ref));items.push(structuredClone(ref));const text=canonicalJSON({v:1,scope,items});if(items.length>64||new TextEncoder().encode(text).length>16384)storageFault();storage.setItem(STORAGE_KEY,text);if(storage.getItem(STORAGE_KEY)!==text)storageFault();}catch{storageFault();}}
  return{load,put,clear:scope=>clearRawQCRecovery(storage,scope)};
}
// Opaque account context never leaves this closure; scope is only a storage partition.
export function createQCTransport({projectId,datasetVersionId,fetchImpl,getOptions,isCurrent,cryptoImpl=globalThis.crypto,storage}){
  const context=signal=>{if(!isCurrent())throw new RawQCFailure('project_context_changed',409);const o=getOptions(signal),nonce=new Headers(o.headers).get('X-Cyomics-Account-Context');if(o.credentials!=='same-origin'||o.cache!=='no-store'||o.redirect!=='error'||typeof nonce!=='string'||!/^[A-Za-z0-9_-]{22}$/.test(nonce))throw new RawQCFailure('project_context_changed',409);return{nonce,options:o};};
  return{isCurrent,recovery:createQCRecovery(storage),async scope(){const signal=new AbortController().signal,{nonce}=context(signal),value=await sha({domain:'cyomics/raw-qc/session/v1',context:nonce},cryptoImpl);if(context(signal).nonce!==nonce)throw new RawQCFailure('project_context_changed',409);return value;},
    send({kind,requestId,jobId,body,signal}){const r=qcRoute({kind,projectId,datasetVersionId,requestId,jobId}),{nonce,options:o}=context(signal),headers={'X-Cyomics-Account-Context':nonce};if(r.method==='POST'){headers['Content-Type']='application/json';headers['X-Cyomics-Request']='1';requireQC(typeof body==='string'&&new TextEncoder().encode(body).length<=1024);}return fetchImpl(r.path,{...o,method:r.method,headers,...(r.method==='POST'?{body}:{})});}};
}
export function createRawQCClient({fullSource,family,transport=null,cryptoImpl=globalThis.crypto,onDenied=()=>{},isCurrent=()=>true,setTimer=setTimeout,clearTimer=clearTimeout,now=()=>performance.now()}={}){
  let disposed=false,generation=0,source=null,scope=null,attempt=null,job=null,lastJob=null,op=null,poll=null,pollIndex=0,views=0,paused=false,starting=false;
  let phase=transport?'preparing':'not_connected',code=null;const listeners=new Set();
  const live=()=>!disposed&&isCurrent()===true&&transport?.isCurrent()===true;
  const emit=()=>{if(!disposed)for(const fn of listeners)fn();};
  const stopPoll=()=>{clearTimer(poll);poll=null;};
  function cancel(work){if(!work)return;clearTimer(work.timer);clearTimer(work.requestTimer);work.controller.abort();try{void(work.reader?work.reader.cancel():work.response?.body?.cancel())?.catch(()=>{});}catch{}}
  function detach(confirmed=false,response=null){if(disposed)return;disposed=true;generation++;stopPoll();cancel(op);op=null;if(confirmed&&scope)transport?.recovery.clear(scope);source=null;fullSource=null;attempt=null;job=null;lastJob=null;scope=null;phase='unavailable';code=null;for(const fn of listeners)fn();listeners.clear();if(response)onDenied(response);}
  function guard(work){if(!live()||op!==work||generation!==work.generation||work.controller.signal.aborted||now()>=work.deadline||work.readDeadline!==null&&now()>=work.readDeadline){cancel(work);throw new RawQCFailure('raw_qc_timeout',504);}}
  function begin(ms,kind){const work={kind,generation,controller:new AbortController(),deadline:now()+ms,readDeadline:null,timer:null,requestTimer:null,reader:null,response:null,sent:false,viewDetached:false};work.cancelled=new Promise((_,reject)=>work.controller.signal.addEventListener('abort',()=>reject(new RawQCFailure('raw_qc_timeout',504)),{once:true}));void work.cancelled.catch(()=>{});op=work;work.timer=setTimer(()=>cancel(work),ms);return work;}
  const wait=async(work,promise)=>{const value=await Promise.race([promise,work.cancelled]);guard(work);return value;};
  function update(next,error=null){phase=next;code=error;emit();}
  function schedule(){stopPoll();if(live()&&views>0&&!paused&&!op&&job&&['queued','running'].includes(job.status)){const delay=[2000,4000,10000][Math.min(pollIndex++,2)];poll=setTimer(()=>{poll=null;void check();},delay);}}
  function finish(work){cancel(work);if(op===work){op=null;if(work.viewDetached&&views>0&&!paused)queueMicrotask(()=>void check());else schedule();emit();}}
  async function request(work,kind,previous=null){
    guard(work);work.readDeadline=now()+15000;work.requestTimer=setTimer(()=>cancel(work),15000);work.response=null;
    try{
      const body=kind==='submit'?JSON.stringify(qcRequest(attempt.request_id,family)):undefined;
      if(kind==='submit')work.sent=true;
      const pending=Promise.resolve(transport.send({kind,requestId:attempt.request_id,jobId:attempt.job_id,body,signal:work.controller.signal})).then(r=>{try{guard(work);}catch(e){try{void r.body?.cancel()?.catch(()=>{});}catch{}throw e;}return r;});
      const response=await wait(work,pending);work.response=response;
      if([401,403].includes(response.status)){const status=response.status;detach(true,{status,body:{}});throw new RawQCFailure('unauthenticated',status);}
      requireQC(!response.redirected&&/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(response.headers.get('content-type')||'')&&[null,'identity'].includes(response.headers.get('content-encoding')));
      const cap=[200,202].includes(response.status)?MAX_RESPONSE:MAX_ERROR,length=response.headers.get('content-length');requireQC(length===null||/^\d{1,6}$/.test(length)&&Number(length)<=cap);
      const reader=response.body?.getReader();requireQC(reader);work.reader=reader;let size=0;const chunks=[];
      for(;;){const p=await wait(work,reader.read());if(p.done)break;requireQC(p.value instanceof Uint8Array&&size+p.value.length<=cap);size+=p.value.length;chunks.push(p.value);}
      requireQC(length===null||Number(length)===size);const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}const value=parseQC(bytes);guard(work);
      if(![200,202].includes(response.status)){const e=qcError(response.status,value);if(e.code==='project_context_changed'){detach(true,{status:409,body:value});}throw e;}
      return await wait(work,validateQCJob(value,{source,family,requestId:attempt.request_id,jobId:attempt.job_id,previous,method:kind==='submit'?'POST':'GET',status:response.status,cryptoImpl}));
    }finally{clearTimer(work.requestTimer);work.requestTimer=null;work.readDeadline=null;try{void work.reader?.cancel()?.catch(()=>{});work.reader?.releaseLock();}catch{}work.reader=null;}
  }
  function accept(value,work){transport.recovery.put(scope,{...attempt,job_id:value.job_id});guard(work);attempt=freeze({...attempt,job_id:value.job_id});job=value;lastJob=value;paused=false;update(value.status);guard(work);}
  function failure(work,error,submission=false){
    if(disposed)return;if(!live()){detach();return;}if(work.viewDetached){phase=job?.status||(attempt?'unknown':'idle');return;}
    paused=true;const safe=error instanceof RawQCFailure?error.code:'raw_qc_unavailable';
    if(safe==='raw_qc_request_conflict'){job=null;update('conflict',safe);return;}
    if(submission&&[400,405,413,415,422,429].includes(error.status)){job=null;update('rejected',safe);return;}
    if(submission&&work.sent){update('unknown',safe);return;}
    if(attempt&&!attempt.job_id){update('unknown',safe);return;}
    job=null;update('unavailable',safe);
  }
  const ready=(async()=>{if(!transport){fullSource=null;return;}const epoch=generation;try{const s=await qcSource(fullSource,family,cryptoImpl);fullSource=null;if(!live()||generation!==epoch)return;const ns=await transport.scope();if(!live()||generation!==epoch)return;source=s;scope=ns;const refs=transport.recovery.load(scope);if(!live()||generation!==epoch)return;attempt=refs.find(r=>r.project_id===s.project_id&&r.file_id===s.file_id&&r.dataset_version_id===s.dataset_version_id&&r.quant_family===family)||null;update(attempt?'unknown':'idle');if(views&&attempt)void check();}catch(error){fullSource=null;if(!disposed&&generation===epoch){if(!live()){detach();return;}update('unavailable',error instanceof RawQCFailure?error.code:'raw_qc_persisted_invalid');}}})();
  async function start(){
    if(!live()||op||starting||phase!=='idle'||attempt||!source||!scope)return false;let ref;starting=true;
    try{ref=freeze({v:1,project_id:source.project_id,file_id:source.file_id,dataset_version_id:source.dataset_version_id,quant_family:family,request_id:cryptoImpl.randomUUID(),job_id:null});if(!live())return false;transport.recovery.put(scope,ref);if(!live())return false;attempt=ref;}catch{if(!disposed)update('unavailable','raw_qc_recovery_unavailable');return false;}finally{starting=false;}
    const work=begin(30000,'submit');stopPoll();update('submitting');
    try{const posted=await request(work,'submit');guard(work);lastJob=posted;transport.recovery.put(scope,{...attempt,job_id:posted.job_id});guard(work);attempt=freeze({...attempt,job_id:posted.job_id});update('checking');const found=await request(work,'job',posted);guard(work);accept(found,work);return true;}
    catch(error){failure(work,error,true);return false;}finally{finish(work);}
  }
  async function check(){
    if(!live()||op||!source||!scope||!attempt)return false;stopPoll();const work=begin(15000,'check');update('checking');
    try{const value=await request(work,attempt.job_id?'job':'lookup',lastJob);guard(work);accept(value,work);return true;}
    catch(error){failure(work,error);return false;}finally{finish(work);}
  }
  return{ready,start,check,
    attach(){if(disposed)return()=>{};views++;if(views===1){if(attempt&&!op&&!paused&&(!lastJob||['queued','running'].includes(lastJob.status)))void check();else schedule();}let stopped=false;return()=>{if(stopped)return;stopped=true;views=Math.max(0,views-1);if(!views){stopPoll();if(op?.kind==='check'){op.viewDetached=true;cancel(op);}}};},
    reference(){if(!disposed&&transport&&!live())detach();return attempt?freeze({...attempt}):null;},
    view(){if(!disposed&&transport&&!live())detach();return freeze({disposed,phase,code,family,status:job?.status||null,observation:job?.observation||null,task:job?.job_id.slice(0,8)||null,created_at:job?.created_at||null,finished_at:job?.finished_at||null,error:job?.error?.code||null,can_start:live()&&phase==='idle'&&!op&&!attempt,can_check:live()&&!!attempt&&!op,artifacts:phase==='succeeded'&&job?.status==='succeeded'?job.result.artifacts.map(({name,bytes})=>({name,bytes})):[],analysis_ready:false});},
    subscribe(fn){if(disposed)return()=>{};listeners.add(fn);return()=>listeners.delete(fn);},dispose:()=>detach()};
}
