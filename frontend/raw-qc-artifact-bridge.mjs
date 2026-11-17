import {createHash} from 'node:crypto';
import {performance} from 'node:perf_hooks';
import {strictProjectJSON} from './projects-bridge.mjs';
import {rawQCError,RawQCBridgeFailure} from './raw-qc-bridge.mjs';

export const ARTIFACT_CAP=67108864,ARTIFACT_TOTAL_MS=45000,ARTIFACT_UPSTREAM_MS=9000;
export const ARTIFACT_NAMES=Object.freeze(['R-session-info.txt','blank-mask.tsv','numeric-matrix.tsv','raw-matrix.tsv','sample-qc.tsv','summary.tsv','zero-mask.tsv']);
export class ArtifactFailure extends RawQCBridgeFailure {}
const failure=(status=503,code='raw_qc_unavailable')=>new ArtifactFailure(status,code);
const integrity=()=>failure(503,'raw_qc_artifact_integrity_failed');
const require=(ok)=>{if(!ok)throw integrity();};
const uuid=value=>typeof value==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
const noop=()=>{};

export function ownsArtifactRoute(raw){
 if(typeof raw!=='string')return false;const paths=[raw.split('?')[0]];
 try{paths.push(new URL(raw,'http://127.0.0.1').pathname);}catch{}
 for(const initial of [...paths]){let path=initial;for(let depth=0;depth<8;depth++){
  try{const decoded=decodeURIComponent(path);if(decoded===path)break;paths.push(decoded);path=decoded;}catch{break;}
 }}
 return paths.some(path=>/^\/api\/v1\/raw-qc-jobs\/[^/]+\/artifacts(?:\/|$)/.test(path.replace(/\/{2,}/g,'/')));
}
export function artifactRoute(raw,method){
 if(typeof raw!=='string'||raw.length>320||/[^\x21-\x7e]|[%+#]/.test(raw))throw failure(404,'not_found');
 const match=/^\/api\/v1\/raw-qc-jobs\/([0-9a-f-]{36})\/artifacts\/([^/?]+)\?project_id=([0-9a-f-]{36})$/.exec(raw);
 if(!match||!uuid(match[1])||!uuid(match[3])||!ARTIFACT_NAMES.includes(match[2]))throw failure(404,'not_found');
 if(method!=='GET')throw failure(405,'method_not_allowed');
 return Object.freeze({path:raw,method:'GET',job_id:match[1],name:match[2],project_id:match[3]});
}
export function artifactError(status,value,{upstream=false}={}){
 const extra=new Map([[404,'raw_qc_artifact_not_found'],[409,'raw_qc_result_not_ready'],[503,'raw_qc_artifact_integrity_failed']]);
 if(value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).length===1&&Object.hasOwn(value,'error')
   &&value.error&&typeof value.error==='object'&&!Array.isArray(value.error)&&Object.keys(value.error).length===1
   &&Object.hasOwn(value.error,'code')&&extra.has(status)&&typeof value.error.code==='string'&&extra.get(status)===value.error.code)return value;
 try{return rawQCError(status,value,{upstream});}catch{throw integrity();}
}

// One long-lived pool per auth runtime. No queue or automatic replacement.
// Retain buffers until every owned stream has confirmed close, even after the
// handler returned on cancellation. destroyed alone is never close evidence.
export function createArtifactLeasePool(){
 let active=null,quarantined=false;
 return Object.freeze({acquire(){
  if(quarantined)throw failure();
  if(active)throw failure(429,'raw_qc_capacity');
  const resources=new Map(),retained=new Set(),releaseCallbacks=new Set(),waiters=new Set();
  let complete=false,aborted=false,unknown=false;
  const allClosed=()=>[...resources.values()].every(item=>item.closed);
  const update=()=>{
   if(!complete||!allClosed())return;
   retained.clear();for(const release of releaseCallbacks)release();releaseCallbacks.clear();resources.clear();
   if(!unknown&&active===lease)active=null;
   for(const wake of waiters)wake();waiters.clear();
  };
  const poison=()=>{unknown=true;quarantined=true;};
  const destroy=resource=>{
   const item=resources.get(resource);if(!item||item.closed||item.destroyAttempted)return;
   item.destroyAttempted=true;
   try{resource.destroy();}catch{poison();}
  };
  const lease=Object.freeze({
   own(resource){
    if(resources.has(resource))return resource;
    const item={closed:false,destroyAttempted:false};resources.set(resource,item);
    try{
     item.closed=resource?.closed===true;
     if(!resource||typeof resource.once!=='function'||typeof resource.on!=='function'||typeof resource.destroy!=='function')throw Error();
     resource.on('error',noop);
     resource.once('close',()=>{item.closed=true;update();});
    }catch{poison();destroy(resource);throw failure();}
    if(complete)poison();
    if(aborted||complete)destroy(resource);
    return resource;
   },
   uncertain(){poison();},
   retain(buffer){retained.add(buffer);},
   releaseWhenClosed(release){releaseCallbacks.add(release);},
   destroy(resource){destroy(resource);},
   abort(){aborted=true;for(const resource of resources.keys())destroy(resource);},
   async finish(signal){
    complete=true;update();if(unknown)return false;if(allClosed())return true;
    if(signal?.aborted){poison();return false;}
    return new Promise(resolve=>{
     const done=()=>{signal?.removeEventListener('abort',cancel);resolve(!unknown);};
     const cancel=()=>{poison();waiters.delete(done);signal?.removeEventListener('abort',cancel);resolve(false);update();};
     waiters.add(done);signal?.addEventListener('abort',cancel,{once:true});update();
     if(signal?.aborted)cancel();
    });
   },
  });
  active=lease;return lease;
 }});
}

export function readArtifactRequest(req,{signal,checkLive}){
 return new Promise((resolve,reject)=>{
  let settled=false;
  const cleanup=()=>{req.removeListener('data',data);req.removeListener('end',end);req.removeListener('error',bad);req.removeListener('aborted',bad);req.removeListener('close',closed);signal.removeEventListener('abort',abort);};
  const finish=error=>{if(settled)return;settled=true;cleanup();error?reject(error):resolve();};
  const bad=()=>finish(failure(400,'invalid_raw_qc_request'));
  const data=chunk=>{try{checkLive();if(!(chunk instanceof Uint8Array)||chunk.byteLength)bad();}catch(error){finish(error);}};
  const end=()=>{try{checkLive();finish();}catch(error){finish(error);}};
  const closed=()=>{if(!req.complete)bad();};
  const abort=()=>finish(failure(504,'raw_qc_timeout'));
  req.on('data',data);req.once('end',end);req.once('error',bad);req.once('aborted',bad);req.once('close',closed);signal.addEventListener('abort',abort,{once:true});
  if(signal.aborted)abort();
 });
}

function headersOf(response){
 require(Array.isArray(response.rawHeaders)&&response.rawHeaders.length%2===0);
 const headers=new Map();
 for(let i=0;i<response.rawHeaders.length;i+=2){
  const key=response.rawHeaders[i],value=response.rawHeaders[i+1];
  require(typeof key==='string'&&/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(key)&&typeof value==='string'&&!/[\r\n\0]/.test(value));
  const name=key.toLowerCase();require(!headers.has(name)&&!['location','refresh','set-cookie'].includes(name));headers.set(name,value);
 }
 // Raw fields are authoritative. A conflicting normalized/merged view or an
 // extra forbidden field cannot be used to bypass raw-header validation.
 for(const [key,value] of Object.entries(response.headers??{})){
  const name=key.toLowerCase();require(!['location','refresh','set-cookie'].includes(name));
  if(['content-type','content-length','content-disposition','x-cyomics-file-sha256','content-encoding','transfer-encoding','cache-control','x-content-type-options','referrer-policy'].includes(name))require(headers.get(name)===value);
 }
 require(!headers.has('transfer-encoding')||!headers.has('content-length'));
 require([undefined,'identity'].includes(headers.get('content-encoding')));
 return headers;
}

export function createArtifactBridge({requestImpl=null,setTimer=setTimeout,clearTimer=clearTimeout,now=()=>performance.now()}={}){
 return ({route,accessToken,signal,budget,checkLive,lease})=>new Promise((resolve,reject)=>{
  let outgoing,incoming,timer,settled=false,sent=false,ended=false,start,last,left,buffer,size=0,acquisitionPending=false;
  const live=()=>{
   checkLive();const current=now();require(Number.isFinite(current)&&current>=last);last=current;
   if(signal.aborted||current-start>=left||budget.remaining()<=0)throw failure(504,'raw_qc_timeout');
  };
  const finish=(error,result)=>{
   if(settled)return;settled=true;clearTimer(timer);signal?.removeEventListener('abort',abort);
   if(error){lease?.destroy(outgoing);lease?.destroy(incoming);reject(error);}else resolve(result);
  };
  const abort=()=>finish(failure(504,'raw_qc_timeout'));
  try{
   route=artifactRoute(route?.path,route?.method);
   require(typeof requestImpl==='function'&&signal&&lease&&typeof checkLive==='function'
    &&typeof accessToken==='string'&&/^[A-Za-z0-9._-]{1,8192}$/.test(accessToken));
   start=last=now();left=Math.floor(Math.min(ARTIFACT_UPSTREAM_MS,budget.remaining()));
   require(Number.isFinite(start)&&Number.isFinite(left)&&left>=1&&/^[1-9]\d{12}$/.test(budget.deadlineUnixMs));
   timer=setTimer(abort,left);signal.addEventListener('abort',abort,{once:true});live();
   acquisitionPending=true;
   outgoing=requestImpl({hostname:'127.0.0.1',port:57430,path:route.path,method:'GET',agent:false,
    headers:{Accept:'application/octet-stream',Authorization:'Bearer '+accessToken,Origin:'http://127.0.0.1:4206',
     'Accept-Encoding':'identity','X-Cyomics-Remaining-Ms':String(left),'X-Cyomics-Deadline-Unix-Ms':budget.deadlineUnixMs}},response=>{
    incoming=response;
    try{lease.own(response);}catch(error){finish(error);return;}
    if(settled||signal.aborted){lease.destroy(response);return;}
    const bad=error=>finish(error instanceof RawQCBridgeFailure?error:integrity());
    response.on('error',()=>bad(failure()));response.once('aborted',()=>bad(integrity()));
    try{
     live();require(sent);const h=headersOf(response),status=response.statusCode,binary=status===200;
     require(binary||[400,401,403,404,405,409,413,415,422,429,503,504].includes(status));
     const length=h.get('content-length'),cap=binary?ARTIFACT_CAP:4096;
     if(length!==undefined)require(/^[1-9]\d{0,7}$/.test(length)&&Number(length)<=cap);
     require(!h.has('transfer-encoding')||!binary&&h.get('transfer-encoding')==='chunked');
     if(binary){
      require(h.get('content-type')==='application/octet-stream'&&length!==undefined
       &&h.get('content-disposition')==='attachment; filename="'+route.name+'"'
       &&/^[0-9a-f]{64}$/.test(h.get('x-cyomics-file-sha256')||'')
       &&h.get('cache-control')==='no-store'&&h.get('x-content-type-options')==='nosniff'&&h.get('referrer-policy')==='no-referrer');
     }else require(/^application\/json(?:; charset=utf-8)?$/.test(h.get('content-type')||''));
     buffer=Buffer.allocUnsafe(length===undefined?cap:Number(length));lease.retain(buffer);lease.releaseWhenClosed(()=>{buffer=null;});
     response.on('data',chunk=>{
      if(settled)return;try{live();require(chunk instanceof Uint8Array&&size+chunk.byteLength<=buffer.length);
       buffer.set(chunk,size);size+=chunk.byteLength;}catch(error){bad(error);}
     });
     response.once('end',()=>{
      if(settled)return;ended=true;
      try{
       live();require(response.complete===true&&size>=1&&(length===undefined||size===Number(length)));
       const body=buffer.subarray(0,size);
       if(binary){
        // Synchronous native digest is not forcibly cancellable. Both clocks
        // and captured identity are fenced again after it returns.
        require(createHash('sha256').update(body).digest('hex')===h.get('x-cyomics-file-sha256'));live();
        finish(undefined,{status:200,body,name:route.name,sha256:h.get('x-cyomics-file-sha256')});
       }else{const value=artifactError(status,strictProjectJSON(body),{upstream:true});live();finish(undefined,{status,value});}
      }catch(error){bad(error);}
     });
     response.once('close',()=>{if(!ended&&!settled)bad(integrity());});
    }catch(error){bad(error);}
   });
   acquisitionPending=false;
   lease.own(outgoing);outgoing.on('error',()=>finish(failure()));outgoing.once('close',()=>{if(!settled&&!incoming)finish(failure());});
   if(settled){lease.destroy(outgoing);return;}live();sent=true;outgoing.end();
  }catch(error){if(acquisitionPending)lease.uncertain();finish(error instanceof RawQCBridgeFailure?error:failure());}
 });
}

export function sendArtifactResponse(res,{result,signal,checkLive,lease}){
 return new Promise((resolve,reject)=>{
  let body,offset=0,settled=false,ending=false,finished=false,waiting=false;
  const cleanup=()=>{res.removeListener('drain',drain);res.removeListener('finish',finish);res.removeListener('close',closed);res.removeListener('error',failed);signal.removeEventListener('abort',abort);};
  const settle=error=>{if(settled)return;settled=true;cleanup();if(error){lease.abort();reject(error);}else resolve();};
  const guard=()=>{checkLive(true);if(signal.aborted)throw failure(504,'raw_qc_timeout');};
  const failed=()=>settle(failure()),abort=()=>settle(failure(504,'raw_qc_timeout'));
  const closed=()=>{try{guard();require(finished&&res.writableFinished===true);settle();}catch(error){settle(error);}};
  const finish=()=>{try{guard();require(ending&&res.writableFinished===true);finished=true;if(res.closed===true)closed();}catch(error){settle(error);}};
  const pump=()=>{
   if(settled)return;
   try{
    guard();while(offset<body.length){guard();const end=Math.min(offset+65536,body.length);const accepted=res.write(body.subarray(offset,end));offset=end;guard();if(!accepted){waiting=true;return;}}
    guard();ending=true;res.end();if(res.writableFinished===true)finish();
   }catch(error){settle(error);}
  };
  const drain=()=>{if(waiting&&!settled){waiting=false;pump();}};
  try{
   let headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','Connection':'close'};
   if(result.status===200){
    require(Buffer.isBuffer(result.body)&&result.body.length>=1&&result.body.length<=ARTIFACT_CAP&&ARTIFACT_NAMES.includes(result.name));body=result.body;
    headers={...headers,'Content-Type':'application/octet-stream','Content-Disposition':'attachment; filename="'+result.name+'"','X-Cyomics-File-Sha256':result.sha256};
   }else{artifactError(result.status,result.value);body=Buffer.from(JSON.stringify(result.value));require(body.length<=4096);headers['Content-Type']='application/json; charset=utf-8';}
   lease.retain(body);lease.releaseWhenClosed(()=>{body=null;});headers['Content-Length']=String(body.length);
   res.on('drain',drain);res.once('finish',finish);res.once('close',closed);res.once('error',failed);signal.addEventListener('abort',abort,{once:true});
   guard();res.writeHead(result.status,headers);pump();
  }catch(error){settle(error);}
 });
}
