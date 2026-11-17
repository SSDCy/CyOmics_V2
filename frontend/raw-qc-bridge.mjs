import {performance} from 'node:perf_hooks';
import {strictProjectJSON} from './projects-bridge.mjs';
import {sendRawUploadResponse} from './raw-upload-response.mjs';
export const RAW_QC_CAP=12000,RAW_QC_SUCCESS=32768,RAW_QC_ERROR=4096;
export class RawQCBridgeFailure extends Error{constructor(status=503,code='raw_qc_unavailable'){super(code);this.status=status;this.code=code;}}
const fault=(status,code)=>{throw new RawQCBridgeFailure(status,code);};
const exact=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
const uuid=v=>typeof v==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v);
const hash=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v),integer=(v,min,max)=>Number.isSafeInteger(v)&&v>=min&&v<=max;
const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
const timestamp=v=>typeof v==='string'&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/.test(v)&&!v.startsWith('0000-')&&Number.isFinite(Date.parse(v))&&new Date(Date.parse(v)).toISOString()===v.slice(0,23)+'Z';
const errors=new Map([[400,['invalid_raw_qc_request']],[401,['unauthenticated']],[403,['auth_origin_rejected']],[404,['not_found','dataset_not_found','raw_qc_job_not_found']],[405,['method_not_allowed']],[409,['raw_qc_request_conflict']],[413,['raw_qc_request_too_large']],[415,['unsupported_media_type']],[422,['raw_qc_family_unavailable']],[429,['raw_qc_capacity']],[503,['raw_qc_unavailable','raw_qc_persisted_invalid']],[504,['raw_qc_timeout','raw_qc_submission_unknown']]]);
export function rawQCError(status,value,{upstream=false}={}){if(!exact(value,['error'])||!exact(value.error,['code'])||!(errors.get(status)?.includes(value.error.code)||!upstream&&status===409&&value.error.code==='project_context_changed'))fault(503,'raw_qc_unavailable');return value;}
function strict(bytes){const v=strictProjectJSON(bytes);function walk(x,d=0){if(d>12)throw Error();if(x&&typeof x==='object')Object.values(x).forEach(c=>walk(c,d+1));}walk(v);return v;}
export function ownsRawQCRoute(raw){
 if(typeof raw!=='string')return false;const paths=[raw.split('?')[0]];
 try{paths.push(decodeURIComponent(raw.split('?')[0]),decodeURIComponent(new URL(raw,'http://127.0.0.1').pathname));}catch{}
 return paths.some(p=>{p=p.replace(/\/{2,}/g,'/');return p.startsWith('/api/v1/raw-qc-jobs')||/^\/api\/v1\/datasets\/[^/]+\/raw-qc-jobs(?:\/|$)/.test(p);});
}
export function rawQCRoute(raw,method){
 if(typeof raw!=='string'||raw.length>320||/[^\x21-\x7e]|[%+#]/.test(raw))fault(404,'not_found');
 const [path,query,...extra]=raw.split('?');if(extra.length)fault(400,'invalid_raw_qc_request');
 const collection=/^\/api\/v1\/datasets\/([0-9a-f-]{36})\/raw-qc-jobs$/.exec(path),detail=/^\/api\/v1\/raw-qc-jobs\/([0-9a-f-]{36})$/.exec(path);
 if(!collection&&!detail||!uuid((collection||detail)[1]))fault(404,'not_found');
 if(!(collection?['GET','POST']:['GET']).includes(method))fault(405,'method_not_allowed');
 if(typeof query!=='string'||query.length>160)fault(400,'invalid_raw_qc_request');const q=new Map();
 for(const pair of query.split('&')){const a=pair.split('=');if(a.length!==2||!['project_id','request_id'].includes(a[0])||q.has(a[0])||!uuid(a[1]))fault(400,'invalid_raw_qc_request');q.set(a[0],a[1]);}
 const lookup=!!collection&&method==='GET';if(q.size!==(lookup?2:1)||!q.has('project_id')||lookup!==q.has('request_id'))fault(400,'invalid_raw_qc_request');
 const canonical=path+'?project_id='+q.get('project_id')+(lookup?'&request_id='+q.get('request_id'):'');if(raw!==canonical)fault(400,'invalid_raw_qc_request');
 return Object.freeze({path:canonical,method,kind:method==='POST'?'submit':lookup?'lookup':'job',project_id:q.get('project_id'),dataset_version_id:collection?.[1]||null,job_id:detail?.[1]||null,request_id:q.get('request_id')||null});
}
export function rawQCInput(bytes){
 try{if(!(bytes instanceof Uint8Array))throw Error();if(bytes.byteLength>1024)fault(413,'raw_qc_request_too_large');const v=strict(bytes);if(!exact(v,['schemaVersion','request_id','quant_family','params'])||v.schemaVersion!==1||!uuid(v.request_id)||!['intensity','lfq'].includes(v.quant_family)||!exact(v.params,[]))throw Error();
  // Only schemaVersion is numeric. Preserve BE's strict integer transport rule
  // instead of silently normalizing a JSON1.0 or1e0 into an integer request.
  const skeleton=new TextDecoder('utf-8',{fatal:true}).decode(bytes).replace(/"(?:\\[\s\S]|[^"\\])*"/g,'""');const numbers=skeleton.match(/\d[\deE.+-]*/g);if(numbers?.length!==1||numbers[0]!=='1')throw Error();return v;
 }catch(error){if(error instanceof RawQCBridgeFailure)throw error;fault(400,'invalid_raw_qc_request');}
}
export function readRawQCBody(req,signal,method){return new Promise((resolve,reject)=>{
 const chunks=[];let size=0,done=false;const cap=method==='POST'?1024:0;
 const cleanup=()=>{req.removeListener('data',data);req.removeListener('end',end);req.removeListener('close',closed);signal.removeEventListener('abort',abort);};
 const finish=error=>{if(done)return;done=true;cleanup();if(error){req.pause?.();reject(error);}else resolve(Buffer.concat(chunks,size));};
 const data=chunk=>{if(!(chunk instanceof Uint8Array))return finish(new RawQCBridgeFailure(400,'invalid_raw_qc_request'));if(size+chunk.byteLength>cap)return finish(new RawQCBridgeFailure(cap?413:400,cap?'raw_qc_request_too_large':'invalid_raw_qc_request'));size+=chunk.byteLength;chunks.push(chunk);};
 const end=()=>finish(),abort=()=>finish(new RawQCBridgeFailure(504,'raw_qc_timeout')),error=()=>finish(new RawQCBridgeFailure(400,'invalid_raw_qc_request')),closed=()=>finish(new RawQCBridgeFailure(400,'invalid_raw_qc_request'));
 req.on('data',data);req.once('end',end);req.on('error',error);req.once('close',closed);req.once('close',()=>req.removeListener('error',error));signal.addEventListener('abort',abort,{once:true});if(signal.aborted)abort();
});}
const publicKeys=['schemaVersion','job_id','request_id','request_sha256','binding_sha256','source','quant_family','params','execution','status','revision','created_at','started_at','finished_at','observation','error','result'];
const names=['R-session-info.txt','blank-mask.tsv','numeric-matrix.tsv','raw-matrix.tsv','sample-qc.tsv','summary.tsv','zero-mask.tsv'];
const failed=['input_integrity_failed','execution_unavailable','runner_failed','resource_limit','output_invalid','worker_interrupted','publish_integrity_failed'];
function execution(v){if(!exact(v,['script','runner','output_validator','executable','r_version','entry_kind','limits_profile','limits_sha256']))throw Error();for(const k of ['script','runner','output_validator','executable'])if(!exact(v[k],['bytes','sha256'])||!integer(v[k].bytes,1,268435456)||!hash(v[k].sha256))throw Error();if(typeof v.r_version!=='string'||!/^[A-Za-z0-9_. -]{1,128}$/.test(v.r_version)||v.entry_kind!=='native_R'||typeof v.limits_profile!=='string'||!/^[a-z][a-z0-9_]{0,79}$/.test(v.limits_profile)||!hash(v.limits_sha256))throw Error();}
// Envelope/route/status/size validation only. API and UI verify scientific pins,
// canonical digests and immutable dataset evidence; BFF selects no R profile.
export function rawQCReply(route,status,v,request=null){
 if(errors.has(status))return rawQCError(status,v,{upstream:true});
 if(!exact(v,publicKeys)||v.schemaVersion!==1||!uuid(v.job_id)||!uuid(v.request_id)||!hash(v.request_sha256)||!hash(v.binding_sha256)||!['Intensity','LFQ intensity'].includes(v.quant_family)||!exact(v.params,[])||!integer(v.revision,1,Number.MAX_SAFE_INTEGER)||!timestamp(v.created_at))throw Error();
 const s=v.source;if(!exact(s,['project_id','dataset_version_id','file_id','source_bytes','source_sha256','parser_contract','config_sha256','report_sha256','row_count','column_count','selected_columns_sha256'])||!['project_id','dataset_version_id','file_id'].every(k=>uuid(s[k]))||!['source_sha256','config_sha256','report_sha256','selected_columns_sha256'].every(k=>hash(s[k]))||!integer(s.source_bytes,1,26214400)||!integer(s.row_count,1,10000)||!integer(s.column_count,2,512)||s.parser_contract!=='protein_groups_txt_v1'||s.project_id!==route.project_id||route.dataset_version_id&&s.dataset_version_id!==route.dataset_version_id||route.job_id&&v.job_id!==route.job_id||route.request_id&&v.request_id!==route.request_id)throw Error();
 if(request&&(v.request_id!==request.request_id||v.quant_family!==({intensity:'Intensity',lfq:'LFQ intensity'})[request.quant_family]))throw Error();execution(v.execution);
 if(!['queued','running','succeeded','failed'].includes(v.status)||!['confirmed','reconciliation_pending'].includes(v.observation)||v.observation==='reconciliation_pending'&&v.status!=='running')throw Error();
 if(status!==(route.method==='POST'&&['queued','running'].includes(v.status)?202:200))throw Error();
 if(v.started_at!==null&&!timestamp(v.started_at)||v.finished_at!==null&&!timestamp(v.finished_at)||v.status==='queued'&&(v.started_at!==null||v.finished_at!==null)||v.status==='running'&&(v.started_at===null||v.finished_at!==null)||['succeeded','failed'].includes(v.status)&&v.finished_at===null||v.status==='succeeded'&&v.started_at===null)throw Error();
 if(v.status==='failed'?!(exact(v.error,['code'])&&failed.includes(v.error.code)):v.error!==null)throw Error();
 if(v.status==='succeeded'){const r=v.result;if(!exact(r,['schemaVersion','job_id','binding_sha256','execution','artifacts','manifest_sha256','verified_at'])||r.schemaVersion!==1||r.job_id!==v.job_id||r.binding_sha256!==v.binding_sha256||!hash(r.manifest_sha256)||!timestamp(r.verified_at)||!Array.isArray(r.artifacts)||r.artifacts.length!==7)throw Error();execution(r.execution);if(canonical(r.execution)!==canonical(v.execution))throw Error();let total=0;for(const [i,a]of r.artifacts.entries()){if(!exact(a,['name','bytes','sha256','media_type'])||a.name!==names[i]||!integer(a.bytes,1,67108864)||!hash(a.sha256)||a.media_type!==(i===0?'text/plain; charset=utf-8':'text/tab-separated-values; charset=utf-8'))throw Error();total+=a.bytes;}if(total>268435456)throw Error();}else if(v.result!==null)throw Error();return v;
}
export function createRawQCBridge({requestImpl=null,setTimer=setTimeout,clearTimer=clearTimeout,now=()=>performance.now()}={}){
 return ({route,body,accessToken,signal,budget,onSent=()=>{}})=>new Promise((resolve,reject)=>{
  let outgoing,incoming,timer,settled=false,sent=false,size=0,ended=false,last,start,left;const chunks=[];
  const expired=()=>{try{return signal?.aborted===true||budget.remaining()<=0||start!==undefined&&now()-start>=left;}catch{return true;}};
  const failure=()=>new RawQCBridgeFailure(sent&&route?.method==='POST'||expired()?504:503,sent&&route?.method==='POST'?'raw_qc_submission_unknown':expired()?'raw_qc_timeout':'raw_qc_unavailable');
  const finish=(error,result)=>{if(settled)return;settled=true;clearTimer(timer);signal?.removeEventListener('abort',abort);if(error){try{outgoing?.destroy();incoming?.destroy();}catch{}reject(error);}else resolve(result);};
  const abort=()=>finish(failure());
  const live=()=>{const mono=now(),remaining=budget.remaining();if(signal.aborted||!Number.isFinite(mono)||mono<last||mono-start>=left||!Number.isFinite(remaining)||remaining<=0)throw Error();last=mono;};
  try{
   route=rawQCRoute(route?.path,route?.method);const request=route.method==='POST'?rawQCInput(body):null;if(!request&&body!==undefined)fault(400,'invalid_raw_qc_request');body=request?Buffer.from(JSON.stringify(request)):undefined;
   left=Math.floor(budget.remaining());start=last=now();if(typeof requestImpl!=='function'||!signal||signal.aborted||!Number.isFinite(left)||left<1||left>RAW_QC_CAP||!Number.isFinite(start)||!/^[1-9]\d{12}$/.test(budget.deadlineUnixMs)||typeof accessToken!=='string'||!/^[A-Za-z0-9._-]{1,8192}$/.test(accessToken))throw Error();
   const headers={Accept:'application/json',Authorization:'Bearer '+accessToken,Origin:'http://127.0.0.1:4206','X-Cyomics-Remaining-Ms':String(left),'X-Cyomics-Deadline-Unix-Ms':budget.deadlineUnixMs};if(request)Object.assign(headers,{'Content-Type':'application/json','Content-Length':String(body.byteLength),'X-Cyomics-Request':'1'});
   timer=setTimer(abort,left);signal.addEventListener('abort',abort,{once:true});live();
   outgoing=requestImpl({hostname:'127.0.0.1',port:57430,path:route.path,method:route.method,headers,agent:false},response=>{
    incoming=response;if(settled){response.on('error',()=>{});response.destroy();return;}const invalid=()=>finish(failure());response.on('error',invalid);response.once('aborted',invalid);
    try{
     live();if(!sent||!Array.isArray(response.rawHeaders)||response.rawHeaders.length%2)throw Error();const h=new Map();
     for(let i=0;i<response.rawHeaders.length;i+=2){const k=response.rawHeaders[i],v=response.rawHeaders[i+1];if(typeof k!=='string'||typeof v!=='string')throw Error();const name=k.toLowerCase();if(['location','refresh','set-cookie'].includes(name))throw Error();if(['content-type','content-length','content-encoding','transfer-encoding'].includes(name)){if(h.has(name))throw Error();h.set(name,v);}}
     if(response.headers?.['set-cookie']!==undefined||!/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(h.get('content-type')||'')||![undefined,'identity'].includes(h.get('content-encoding'))||h.has('transfer-encoding')&&(h.get('transfer-encoding')!=='chunked'||h.has('content-length')))throw Error();
     const status=response.statusCode;if(![200,202].includes(status)&&!errors.has(status))throw Error();const cap=[200,202].includes(status)?RAW_QC_SUCCESS:RAW_QC_ERROR,length=h.get('content-length');if(length!==undefined&&(!/^\d{1,10}$/.test(length)||Number(length)>cap))throw Error();
     response.on('data',chunk=>{if(settled)return;try{live();if(!(chunk instanceof Uint8Array)||size+chunk.byteLength>cap)throw Error();size+=chunk.byteLength;chunks.push(chunk);}catch{invalid();}});
     response.once('end',()=>{if(settled)return;ended=true;try{live();if(length!==undefined&&Number(length)!==size)throw Error();const value=rawQCReply(route,status,strict(Buffer.concat(chunks,size)),request);live();finish(undefined,{status,value});}catch{invalid();}});
     response.once('close',()=>{if(!ended&&!settled)invalid();});
    }catch{invalid();}
   });
   outgoing.on('error',abort);outgoing.once('close',()=>{if(!settled&&!incoming)abort();});if(settled){outgoing.destroy();return;}live();sent=true;onSent();if(settled)return;live();outgoing.end(body);
  }catch(error){finish(error instanceof RawQCBridgeFailure&&!sent?error:failure());}
 });
}
export async function sendRawQCJSONResponse(req,res,{status,value,signal,checkLive,remaining,onController,setTimer=setTimeout,clearTimer=clearTimeout,now=()=>performance.now()}){
 let body;try{body=Buffer.from(JSON.stringify(value));if(body.length>([200,202].includes(status)?RAW_QC_SUCCESS:RAW_QC_ERROR))throw Error();}catch{throw new RawQCBridgeFailure();}
 const local=signal?null:new AbortController(),activeSignal=signal??local.signal;let timer,start,last,left;
 const abort=()=>local?.abort();if(local){start=last=now();left=remaining?Math.min(RAW_QC_CAP,remaining()):RAW_QC_CAP;if(!Number.isFinite(start)||!Number.isFinite(left)||left<=0)throw new RawQCBridgeFailure(504,'raw_qc_timeout');onController?.(local);req.once('aborted',abort);res.once('close',abort);timer=setTimer(abort,Math.max(1,Math.ceil(left)));}
 const guard=()=>{if(local){const current=now();if(!Number.isFinite(current)||current<last||current-start>=left||remaining&&remaining()<=0||req.aborted||req.socket?.destroyed)throw new RawQCBridgeFailure(504,'raw_qc_timeout');last=current;}checkLive?.();};
 try{await sendRawUploadResponse(res,{status,headers:{'Content-Type':'application/json; charset=utf-8','Content-Length':String(body.length),'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','Connection':'close'},body,signal:activeSignal,checkLive:guard});}
 finally{if(local){clearTimer(timer);req.removeListener('aborted',abort);res.removeListener('close',abort);local.abort();}}
}
