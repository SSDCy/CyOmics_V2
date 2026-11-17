import {FAMILIES,OUTPUTS,RawQCFailure,requireQC,exact,uuid,freeze,canonicalJSON,sha,qcSource,qcRoute,qcRequest,parseQC,qcError,validateQCJob,MAX_RESPONSE,MAX_ERROR} from './raw-qc-contract.mjs';
// One artifact flight per private transport, including non-cancellable hashing.
// The scope owns no credentials, bytes, manifest, URL, or recovery state.
export function createQCArtifactScope(){
  let owner=null;const listeners=new Set();
  const emit=()=>{for(const fn of [...listeners]){try{fn();}catch{}}};
  return Object.freeze({busy:()=>owner!==null,subscribe(fn){listeners.add(fn);return()=>listeners.delete(fn);},
    acquire(cancel){if(owner)return null;const token={cancel};owner=token;emit();return Object.freeze({release(){if(owner!==token)return;owner=null;emit();}});},
    cancelAll(){try{owner?.cancel();}catch{}}
  });
}
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
export function createQCTransport({projectId,datasetVersionId,fetchImpl,getOptions,isCurrent,cryptoImpl=globalThis.crypto,storage,rawQcArtifactsEnabled=false,artifactScope=null}){
  const context=signal=>{if(!isCurrent())throw new RawQCFailure('project_context_changed',409);const o=getOptions(signal),nonce=new Headers(o.headers).get('X-Cyomics-Account-Context');if(o.credentials!=='same-origin'||o.cache!=='no-store'||o.redirect!=='error'||typeof nonce!=='string'||!/^[A-Za-z0-9_-]{22}$/.test(nonce))throw new RawQCFailure('project_context_changed',409);return{nonce,options:o};};
  const artifactDownload=rawQcArtifactsEnabled===true&&artifactScope?Object.freeze({scope:artifactScope,request({jobId,name,signal}){
    requireQC(uuid(projectId)&&uuid(jobId)&&OUTPUTS.includes(name));const {nonce,options:o}=context(signal);
    if(!isCurrent()||signal.aborted)throw new RawQCFailure('project_context_changed',409);
    return fetchImpl('/api/v1/raw-qc-jobs/'+jobId+'/artifacts/'+name+'?project_id='+projectId,{...o,method:'GET',signal,headers:{'X-Cyomics-Account-Context':nonce}});
  }}):null;
  return{isCurrent,artifactDownload,recovery:createQCRecovery(storage),async scope(){const signal=new AbortController().signal,{nonce}=context(signal),value=await sha({domain:'cyomics/raw-qc/session/v1',context:nonce},cryptoImpl);if(context(signal).nonce!==nonce)throw new RawQCFailure('project_context_changed',409);return value;},
    send({kind,requestId,jobId,body,signal}){const r=qcRoute({kind,projectId,datasetVersionId,requestId,jobId}),{nonce,options:o}=context(signal),headers={'X-Cyomics-Account-Context':nonce};if(r.method==='POST'){headers['Content-Type']='application/json';headers['X-Cyomics-Request']='1';requireQC(typeof body==='string'&&new TextEncoder().encode(body).length<=1024);}return fetchImpl(r.path,{...o,method:r.method,headers,...(r.method==='POST'?{body}:{})});}};
}
export function createRawQCClient({fullSource,family,transport=null,cryptoImpl=globalThis.crypto,onDenied=()=>{},isCurrent=()=>true,setTimer=setTimeout,clearTimer=clearTimeout,now=()=>performance.now(),documentImpl=globalThis.document,urlImpl=globalThis.URL,BlobImpl=globalThis.Blob}={}){
  let disposed=false,generation=0,source=null,scope=null,attempt=null,job=null,lastJob=null,op=null,poll=null,pollIndex=0,views=0,paused=false,starting=false;
  let phase=transport?'preparing':'not_connected',code=null;const listeners=new Set();
  const live=()=>!disposed&&isCurrent()===true&&transport?.isCurrent()===true;
  const emit=()=>{if(!disposed)for(const fn of listeners)fn();};
  const artifactCapability=transport?.artifactDownload||null;
  let downloadOp=null,downloadGeneration=0,artifactNeedsCheck=false,downloadState={phase:'idle',name:null,code:null};
  const stopArtifactScope=artifactCapability?.scope.subscribe(emit)||(()=>{});
  function revokeArtifact(work){
    clearTimer(work.urlTimer);work.urlTimer=null;const url=work.url;work.url=null;if(url!==null){try{urlImpl.revokeObjectURL(url);}catch{}}
    const anchor=work.anchor;work.anchor=null;if(anchor){try{anchor.removeAttribute?.('href');anchor.removeAttribute?.('download');anchor.remove?.();}catch{}}
  }
  function releaseArtifact(work){if(!work.asyncDone||work.hashPending||work.url!==null)return;if(downloadOp===work)downloadOp=null;work.lease?.release();work.lease=null;}
  function cancelArtifactWork(work,reason='cancelled'){if(!work)return;work.reason=work.reason||reason;work.cancelled=true;clearTimer(work.timer);clearTimer(work.idleTimer);work.controller.abort();
    if(work.reader&&work.cancelledReader!==work.reader){work.cancelledReader=work.reader;try{void work.reader.cancel()?.catch(()=>{});}catch{}}
    else if(!work.reader&&work.response){try{void work.response.body?.cancel()?.catch(()=>{});}catch{}}
    revokeArtifact(work);releaseArtifact(work);
  }
  function cancelArtifactDownload(){downloadGeneration++;downloadState=artifactNeedsCheck&&!disposed&&live()?{phase:'unconfirmed',name:null,code:'raw_qc_result_not_ready'}:{phase:'idle',name:null,code:null};cancelArtifactWork(downloadOp);}
  const artifactReady=()=>live()&&views>0&&!op&&phase==='succeeded'&&job?.status==='succeeded'&&job.observation==='confirmed';
  function canDownloadArtifact(name){if(!disposed&&transport&&!live())detach();return !!artifactCapability&&artifactReady()&&!artifactNeedsCheck&&!downloadOp&&!artifactCapability.scope.busy()&&OUTPUTS.includes(name)&&job.result.artifacts.some(a=>a.name===name)
    &&typeof cryptoImpl?.subtle?.digest==='function'&&typeof documentImpl?.createElement==='function'&&typeof documentImpl?.body?.append==='function'&&typeof urlImpl?.createObjectURL==='function'&&typeof urlImpl?.revokeObjectURL==='function'&&typeof BlobImpl==='function';}
  function guardArtifact(work){if(!artifactReady()||downloadOp!==work||work.generation!==downloadGeneration||work.controller.signal.aborted||work.cancelled||job!==work.job||now()>=work.deadline){cancelArtifactWork(work,now()>=work.deadline?'raw_qc_timeout':'cancelled');throw new RawQCFailure('raw_qc_timeout',504);}}
  async function artifactWait(work,promise,idle=false){let timer=null;try{guardArtifact(work);if(idle){timer=setTimer(()=>cancelArtifactWork(work,'raw_qc_timeout'),Math.min(15000,Math.max(0,work.deadline-now())));work.idleTimer=timer;}const value=await Promise.race([promise,work.aborted]);guardArtifact(work);return value;}finally{clearTimer(timer);if(work.idleTimer===timer)work.idleTimer=null;}}
  function downloadUpdate(work,phase,code=null){guardArtifact(work);downloadState={phase,name:work.name,code};emit();guardArtifact(work);}
  async function readArtifactError(work,response){
    requireQC(/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(response.headers.get('content-type')||'')&&[null,'identity'].includes(response.headers.get('content-encoding')));
    const length=response.headers.get('content-length');requireQC(length===null||/^(0|[1-9]\d{0,3})$/.test(length)&&Number(length)<=MAX_ERROR);
    const reader=response.body?.getReader();requireQC(reader);work.reader=reader;const bytes=new Uint8Array(MAX_ERROR);let size=0;
    for(;;){const part=await artifactWait(work,reader.read(),true);if(part.done)break;requireQC(part.value instanceof Uint8Array&&size+part.value.byteLength<=MAX_ERROR);bytes.set(part.value,size);size+=part.value.byteLength;}
    requireQC(length===null||Number(length)===size);return qcError(response.status,parseQC(bytes.slice(0,size)));
  }
  async function downloadArtifact(name){
    if(!canDownloadArtifact(name))return false;
    const expected=job.result.artifacts.find(a=>a.name===name),work={job,name,generation:++downloadGeneration,controller:new AbortController(),deadline:now()+60000,timer:null,idleTimer:null,reader:null,response:null,cancelledReader:null,cancelled:false,asyncDone:false,hashPending:false,lease:null,url:null,urlTimer:null,anchor:null};
    work.aborted=new Promise((_,reject)=>work.controller.signal.addEventListener('abort',()=>reject(new RawQCFailure('raw_qc_timeout',504)),{once:true}));void work.aborted.catch(()=>{});
    downloadOp=work;let buffer=null,handedOff=false;
    try{
      work.lease=artifactCapability.scope.acquire(()=>{cancelArtifactDownload();emit();});if(!work.lease)return false;
      work.timer=setTimer(()=>cancelArtifactWork(work,'raw_qc_timeout'),60000);downloadUpdate(work,'reading');
      const pending=Promise.resolve(artifactCapability.request({jobId:work.job.job_id,name,signal:work.controller.signal})).then(response=>{work.response=response;try{guardArtifact(work);}catch(error){try{void response.body?.cancel()?.catch(()=>{});}catch{}throw error;}return response;});
      const response=await artifactWait(work,pending,true);guardArtifact(work);
      if([401,403].includes(response.status)){detach(true,{status:response.status,body:{}});throw new RawQCFailure('unauthenticated',response.status);}
      requireQC(!response.redirected);
      if(response.status!==200){if(response.status===409)artifactNeedsCheck=true;const error=await readArtifactError(work,response);if(error.code==='project_context_changed')detach(true,{status:409,body:{error:{code:error.code}}});throw error;}
      const h=response.headers;requireQC(expected.bytes>=1&&expected.bytes<=67108864&&h.get('content-type')==='application/octet-stream'&&h.get('content-length')===String(expected.bytes)&&[null,'identity'].includes(h.get('content-encoding'))
        &&h.get('content-disposition')==='attachment; filename="'+name+'"'&&h.get('x-cyomics-file-sha256')===expected.sha256&&h.get('cache-control')==='no-store'&&h.get('x-content-type-options')==='nosniff'&&h.get('referrer-policy')==='no-referrer');
      guardArtifact(work);buffer=new Uint8Array(expected.bytes);guardArtifact(work);const reader=response.body?.getReader();requireQC(reader);work.reader=reader;let size=0;
      for(;;){const part=await artifactWait(work,reader.read(),true);if(part.done)break;requireQC(part.value instanceof Uint8Array&&size+part.value.byteLength<=expected.bytes&&size+part.value.byteLength<=67108864);buffer.set(part.value,size);size+=part.value.byteLength;}
      requireQC(size===expected.bytes);downloadUpdate(work,'verifying');work.hashPending=true;
      let hash;try{const digestFunction=cryptoImpl.subtle.digest;guardArtifact(work);hash=Promise.resolve(digestFunction.call(cryptoImpl.subtle,'SHA-256',buffer));}catch(error){work.hashPending=false;throw error;}
      void hash.then(()=>{work.hashPending=false;releaseArtifact(work);},()=>{work.hashPending=false;releaseArtifact(work);});
      const digest=await artifactWait(work,hash);requireQC([...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2,'0')).join('')===expected.sha256);guardArtifact(work);
      const blob=new BlobImpl([buffer],{type:'application/octet-stream'});guardArtifact(work);const makeURL=urlImpl.createObjectURL;guardArtifact(work);work.url=makeURL.call(urlImpl,blob);guardArtifact(work);
      const makeAnchor=documentImpl.createElement;guardArtifact(work);work.anchor=makeAnchor.call(documentImpl,'a');guardArtifact(work);work.anchor.href=work.url;guardArtifact(work);work.anchor.download='raw-qc_'+family+'_'+work.job.job_id+'_'+name;guardArtifact(work);work.anchor.style.display='none';guardArtifact(work);const append=documentImpl.body.append;guardArtifact(work);append.call(documentImpl.body,work.anchor);guardArtifact(work);
      downloadUpdate(work,'handing_off');const click=work.anchor.click;guardArtifact(work);click.call(work.anchor);handedOff=true;guardArtifact(work);
      work.urlTimer=setTimer(()=>{revokeArtifact(work);releaseArtifact(work);if(live())emit();},Math.min(1000,Math.max(0,work.deadline-now())));guardArtifact(work);downloadUpdate(work,'handed_off');return true;
    }catch(error){
      if(!disposed&&transport&&!live())detach();
      if(!disposed&&downloadOp===work&&work.reason!=='cancelled'){const code=error instanceof RawQCFailure&&['raw_qc_result_not_ready','raw_qc_artifact_not_found','raw_qc_artifact_integrity_failed','raw_qc_persisted_invalid','raw_qc_unavailable','raw_qc_timeout','raw_qc_capacity'].includes(error.code)?error.code:'raw_qc_unavailable';downloadState={phase:'unconfirmed',name,code};try{emit();}catch{}}
      return false;
    }finally{
      buffer=null;clearTimer(work.timer);clearTimer(work.idleTimer);try{if(work.reader&&work.cancelledReader!==work.reader){work.cancelledReader=work.reader;void work.reader.cancel()?.catch(()=>{});}work.reader?.releaseLock();}catch{}work.reader=null;
      work.asyncDone=true;if(!handedOff||work.cancelled||!live())revokeArtifact(work);releaseArtifact(work);
    }
  }
  const stopPoll=()=>{clearTimer(poll);poll=null;};
  function cancel(work){if(!work)return;clearTimer(work.timer);clearTimer(work.requestTimer);work.controller.abort();try{void(work.reader?work.reader.cancel():work.response?.body?.cancel())?.catch(()=>{});}catch{}}
  function detach(confirmed=false,response=null){if(disposed)return;disposed=true;generation++;stopArtifactScope();cancelArtifactDownload();stopPoll();cancel(op);op=null;if(confirmed&&scope)transport?.recovery.clear(scope);source=null;fullSource=null;attempt=null;job=null;lastJob=null;scope=null;phase='unavailable';code=null;for(const fn of listeners)fn();listeners.clear();if(response)onDenied(response);}
  function guard(work){if(!live()||op!==work||generation!==work.generation||work.controller.signal.aborted||now()>=work.deadline||work.readDeadline!==null&&now()>=work.readDeadline){cancel(work);throw new RawQCFailure('raw_qc_timeout',504);}}
  function begin(ms,kind){const work={kind,generation,controller:new AbortController(),deadline:now()+ms,readDeadline:null,timer:null,requestTimer:null,reader:null,response:null,sent:false,viewDetached:false};work.cancelled=new Promise((_,reject)=>work.controller.signal.addEventListener('abort',()=>reject(new RawQCFailure('raw_qc_timeout',504)),{once:true}));void work.cancelled.catch(()=>{});op=work;work.timer=setTimer(()=>cancel(work),ms);return work;}
  const wait=async(work,promise)=>{const value=await Promise.race([promise,work.cancelled]);guard(work);return value;};
  function update(next,error=null){phase=next;code=error;emit();}
  function schedule(){stopPoll();if(live()&&views>0&&!paused&&!op&&job&&['queued','running'].includes(job.status)){const delay=[2000,4000,10000][Math.min(pollIndex++,2)];poll=setTimer(()=>{poll=null;void check({explicit:false});},delay);}}
  function finish(work){cancel(work);if(op===work){op=null;if(work.viewDetached&&views>0&&!paused)queueMicrotask(()=>void check({explicit:false}));else schedule();emit();}}
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
  const ready=(async()=>{if(!transport){fullSource=null;return;}const epoch=generation;try{const s=await qcSource(fullSource,family,cryptoImpl);fullSource=null;if(!live()||generation!==epoch)return;const ns=await transport.scope();if(!live()||generation!==epoch)return;source=s;scope=ns;const refs=transport.recovery.load(scope);if(!live()||generation!==epoch)return;attempt=refs.find(r=>r.project_id===s.project_id&&r.file_id===s.file_id&&r.dataset_version_id===s.dataset_version_id&&r.quant_family===family)||null;update(attempt?'unknown':'idle');if(views&&attempt)void check({explicit:false});}catch(error){fullSource=null;if(!disposed&&generation===epoch){if(!live()){detach();return;}update('unavailable',error instanceof RawQCFailure?error.code:'raw_qc_persisted_invalid');}}})();
  async function start(){
    if(!live()||op||starting||phase!=='idle'||attempt||!source||!scope)return false;let ref;starting=true;
    try{ref=freeze({v:1,project_id:source.project_id,file_id:source.file_id,dataset_version_id:source.dataset_version_id,quant_family:family,request_id:cryptoImpl.randomUUID(),job_id:null});if(!live())return false;transport.recovery.put(scope,ref);if(!live())return false;attempt=ref;}catch{if(!disposed)update('unavailable','raw_qc_recovery_unavailable');return false;}finally{starting=false;}
    const work=begin(30000,'submit');stopPoll();update('submitting');
    try{const posted=await request(work,'submit');guard(work);lastJob=posted;transport.recovery.put(scope,{...attempt,job_id:posted.job_id});guard(work);attempt=freeze({...attempt,job_id:posted.job_id});update('checking');const found=await request(work,'job',posted);guard(work);accept(found,work);return true;}
    catch(error){failure(work,error,true);return false;}finally{finish(work);}
  }
  async function check({explicit=true}={}){
    if(!live()||op||!source||!scope||!attempt)return false;cancelArtifactDownload();stopPoll();const work=begin(15000,'check');update('checking');
    try{const value=await request(work,attempt.job_id?'job':'lookup',lastJob);guard(work);if(explicit&&value.status==='succeeded'){artifactNeedsCheck=false;downloadState={phase:'idle',name:null,code:null};}accept(value,work);return true;}
    catch(error){failure(work,error);return false;}finally{finish(work);}
  }
  return{ready,start,check,downloadArtifact,canDownloadArtifact,cancelArtifactDownload,
    attach(){if(disposed)return()=>{};views++;if(views===1){if(attempt&&!op&&!paused&&(!lastJob||['queued','running'].includes(lastJob.status)))void check({explicit:false});else schedule();}let stopped=false;return()=>{if(stopped)return;stopped=true;views=Math.max(0,views-1);if(!views){cancelArtifactDownload();stopPoll();if(op?.kind==='check'){op.viewDetached=true;cancel(op);}}};},
    reference(){if(!disposed&&transport&&!live())detach();return attempt?freeze({...attempt}):null;},
    view(){if(!disposed&&transport&&!live())detach();return freeze({disposed,phase,code,family,status:job?.status||null,observation:job?.observation||null,task:job?.job_id.slice(0,8)||null,created_at:job?.created_at||null,finished_at:job?.finished_at||null,error:job?.error?.code||null,can_start:live()&&phase==='idle'&&!op&&!attempt,can_check:live()&&!!attempt&&!op,artifacts:phase==='succeeded'&&job?.status==='succeeded'?job.result.artifacts.map(({name,bytes})=>({name,bytes})):[],artifact_downloads:!!artifactCapability,can_download_artifact:!!artifactCapability&&OUTPUTS.some(canDownloadArtifact),download:{...downloadState},analysis_ready:false});},
    subscribe(fn){if(disposed)return()=>{};listeners.add(fn);return()=>listeners.delete(fn);},dispose:()=>detach()};
}
