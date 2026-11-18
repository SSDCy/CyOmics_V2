import {createHash} from 'node:crypto';
import {performance} from 'node:perf_hooks';
import {strictProjectJSON} from './projects-bridge.mjs';
import {sendRawUploadResponse} from './raw-upload-response.mjs';
export const PREPROCESS_CAP=12000,PREPROCESS_SUCCESS=32768,PREPROCESS_ERROR=4096;
export class PreprocessBridgeFailure extends Error{constructor(status=503,code='preprocessing_unavailable'){super(code);this.status=status;this.code=code;}}
const fault=(status,code)=>{throw new PreprocessBridgeFailure(status,code);};
const exact=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
const uuid=v=>typeof v==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v);
const hash=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v),integer=(v,min,max)=>Number.isSafeInteger(v)&&v>=min&&v<=max;
const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
const timestamp=v=>typeof v==='string'&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/.test(v)&&!v.startsWith('0000-')&&Number.isFinite(Date.parse(v))&&new Date(Date.parse(v)).toISOString()===v.slice(0,23)+'Z';
const errors=new Map([[400,['invalid_preprocessing_request']],[401,['unauthenticated']],[403,['auth_origin_rejected']],[404,['not_found','dataset_not_found','preprocessing_job_not_found']],[405,['method_not_allowed']],[409,['preprocessing_request_conflict']],[413,['preprocessing_request_too_large']],[415,['unsupported_media_type']],[422,['preprocessing_family_unavailable','preprocessing_flags_unavailable']],[503,['preprocessing_unavailable','preprocessing_persisted_invalid']],[504,['preprocessing_timeout','preprocessing_submission_unknown']]]);
export function preprocessError(status,value,{upstream=false}={}){if(!exact(value,['error'])||!exact(value.error,['code'])||!(errors.get(status)?.includes(value.error.code)||!upstream&&status===409&&value.error.code==='project_context_changed'))fault(503,'preprocessing_unavailable');return value;}
function strict(bytes){
 const v=strictProjectJSON(bytes);
 function walk(x,d=0){if(d>8)throw Error();if(x&&typeof x==='object')Object.values(x).forEach(c=>walk(c,d+1));}walk(v);
 // These envelopes contain only nonnegative integer numbers. Reject JSON float,
 // exponent and negative-zero spellings before JSON.parse can erase their types.
 const skeleton=new TextDecoder('utf-8',{fatal:true}).decode(bytes).replace(/"(?:\\[\s\S]|[^"\\])*"/g,'""');
 for(const n of skeleton.match(/-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/g)||[])if(!/^(?:0|[1-9]\d*)$/.test(n))throw Error();
 return v;
}
export function ownsPreprocessRoute(raw){
 if(typeof raw!=='string')return false;const paths=[raw.split('?')[0]];
 try{paths.push(decodeURIComponent(raw.split('?')[0]),decodeURIComponent(new URL(raw,'http://127.0.0.1').pathname));}catch{}
 return paths.some(p=>{p=p.replace(/\/{2,}/g,'/');return p.startsWith('/api/v1/preprocessing-jobs')||/^\/api\/v1\/datasets\/[^/]+\/preprocessing-jobs(?:\/|$)/.test(p);});
}
export function preprocessRoute(raw,method){
 if(typeof raw!=='string'||raw.length>320||/[^\x21-\x7e]|[%+#]/.test(raw))fault(404,'not_found');
 const [path,query,...extra]=raw.split('?');if(extra.length)fault(400,'invalid_preprocessing_request');
 const collection=/^\/api\/v1\/datasets\/([0-9a-f-]{36})\/preprocessing-jobs$/.exec(path),detail=/^\/api\/v1\/preprocessing-jobs\/([0-9a-f-]{36})$/.exec(path);
 if(!collection&&!detail||!uuid((collection||detail)[1]))fault(404,'not_found');
 if(!(collection?['GET','POST']:['GET']).includes(method))fault(405,'method_not_allowed');
 if(typeof query!=='string'||query.length>160)fault(400,'invalid_preprocessing_request');const q=new Map();
 for(const pair of query.split('&')){const a=pair.split('=');if(a.length!==2||!['project_id','request_id'].includes(a[0])||q.has(a[0])||!uuid(a[1]))fault(400,'invalid_preprocessing_request');q.set(a[0],a[1]);}
 const lookup=!!collection&&method==='GET';if(q.size!==(lookup?2:1)||!q.has('project_id')||lookup!==q.has('request_id'))fault(400,'invalid_preprocessing_request');
 const canonical=path+'?project_id='+q.get('project_id')+(lookup?'&request_id='+q.get('request_id'):'');if(raw!==canonical)fault(400,'invalid_preprocessing_request');
 return Object.freeze({path:canonical,method,kind:method==='POST'?'submit':lookup?'lookup':'job',project_id:q.get('project_id'),dataset_version_id:collection?.[1]||null,job_id:detail?.[1]||null,request_id:q.get('request_id')||null});
}
const parameterKeys=['blank_policy','filter_flags','imputation','input_scale','log2','normalization','output_scale','preset','pseudocount','seed','steps','zero_policy'];
const steps=['flags_filter','zero_and_blank_to_NA','log2_positive','normalization_none','imputation_none'];
function parameters(v){
 if(!exact(v,parameterKeys)||!exact(v.filter_flags,['contaminant','reverse','site_only'])||!Object.values(v.filter_flags).every(x=>typeof x==='boolean'))throw Error();
 const fixed={blank_policy:'to_NA',imputation:'none',input_scale:'raw_nonnegative',log2:'positive_only',normalization:'none',output_scale:'log2',preset:'positive_log2_zero_na_no_impute_v1',pseudocount:0,seed:null,steps,zero_policy:'to_NA'};
 for(const [k,want] of Object.entries(fixed))if(canonical(v[k])!==canonical(want))throw Error();
 return v;
}
export function preprocessInput(bytes){
 try{if(!(bytes instanceof Uint8Array))throw Error();if(bytes.byteLength>4096)fault(413,'preprocessing_request_too_large');
  const v=strict(bytes);if(!exact(v,['request_id','quant_family','parameters'])||!uuid(v.request_id)||!['Intensity','LFQ intensity'].includes(v.quant_family))throw Error();parameters(v.parameters);return v;
 }catch(error){if(error instanceof PreprocessBridgeFailure)throw error;fault(400,'invalid_preprocessing_request');}
}
export function readPreprocessBody(req,signal,method){return new Promise((resolve,reject)=>{
 const chunks=[];let size=0,done=false;const cap=method==='POST'?4096:0;
 const cleanup=()=>{req.removeListener('data',data);req.removeListener('end',end);req.removeListener('close',closed);signal.removeEventListener('abort',abort);};
 const finish=error=>{if(done)return;done=true;cleanup();if(error){req.pause?.();reject(error);}else resolve(Buffer.concat(chunks,size));};
 const data=chunk=>{if(!(chunk instanceof Uint8Array))return finish(new PreprocessBridgeFailure(400,'invalid_preprocessing_request'));if(size+chunk.byteLength>cap)return finish(new PreprocessBridgeFailure(cap?413:400,cap?'preprocessing_request_too_large':'invalid_preprocessing_request'));size+=chunk.byteLength;chunks.push(chunk);};
 const end=()=>finish(),abort=()=>finish(new PreprocessBridgeFailure(504,'preprocessing_timeout')),error=()=>finish(new PreprocessBridgeFailure(400,'invalid_preprocessing_request')),closed=()=>finish(new PreprocessBridgeFailure(400,'invalid_preprocessing_request'));
 req.on('data',data);req.once('end',end);req.on('error',error);req.once('close',closed);req.once('close',()=>req.removeListener('error',error));signal.addEventListener('abort',abort,{once:true});if(signal.aborted)abort();
});}
const publicKeys=['schemaVersion','job_id','request_id','request_sha256','binding_sha256','source','quant_family','parameters','parameters_sha256','execution_sha256','status','created_at','result','computation_performed','worker_started','execution_registered'];
const digest=v=>createHash('sha256').update(canonical(v)).digest('hex');
// Owner/current source/full binding checks remain in the authenticated repository.
// binding/execution hashes are opaque; this bridge constructs no execution plan.
export function preprocessReply(route,status,v,request=null){
 if(errors.has(status))return preprocessError(status,v,{upstream:true});
 if(![200,201].includes(status)||route.method!=='POST'&&status!==200||!exact(v,publicKeys)||v.schemaVersion!==1||!uuid(v.job_id)||!uuid(v.request_id)||!['request_sha256','binding_sha256','parameters_sha256','execution_sha256'].every(k=>hash(v[k]))||!['Intensity','LFQ intensity'].includes(v.quant_family)||!timestamp(v.created_at))throw Error();
 const s=v.source;
 if(!exact(s,['project_id','dataset_version_id','file_id','source_bytes','source_sha256','parser_contract','config_sha256','report_sha256','row_count','column_count','selected_columns_sha256'])||!['project_id','dataset_version_id','file_id'].every(k=>uuid(s[k]))||!['source_sha256','config_sha256','report_sha256','selected_columns_sha256'].every(k=>hash(s[k]))||!integer(s.source_bytes,1,26214400)||!integer(s.row_count,1,10000)||!integer(s.column_count,2,512)||s.row_count*s.column_count>2000000||s.parser_contract!=='protein_groups_txt_v1'||s.project_id!==route.project_id||route.dataset_version_id&&s.dataset_version_id!==route.dataset_version_id||route.job_id&&v.job_id!==route.job_id||route.request_id&&v.request_id!==route.request_id)throw Error();
 parameters(v.parameters);const normalized={request_id:v.request_id,quant_family:v.quant_family,parameters:v.parameters};
 if(request&&canonical(normalized)!==canonical(request)||v.parameters_sha256!==digest(v.parameters)||v.request_sha256!==digest({project_id:s.project_id,dataset_version_id:s.dataset_version_id,request:normalized}))throw Error();
 if(v.status!=='queued'||v.result!==null||v.computation_performed!==false||v.worker_started!==false||v.execution_registered!==false)throw Error();
 return v;
}
export function createPreprocessBridge({requestImpl=null,setTimer=setTimeout,clearTimer=clearTimeout,now=()=>performance.now()}={}){
 return ({route,body,accessToken,signal,budget,onSent=()=>{}})=>new Promise((resolve,reject)=>{
  let outgoing,incoming,timer,settled=false,sent=false,size=0,ended=false,last,start,left;const chunks=[];
  const expired=()=>{try{return signal?.aborted===true||budget.remaining()<=0||start!==undefined&&now()-start>=left;}catch{return true;}};
  const failure=()=>new PreprocessBridgeFailure(sent&&route?.method==='POST'||expired()?504:503,sent&&route?.method==='POST'?'preprocessing_submission_unknown':expired()?'preprocessing_timeout':'preprocessing_unavailable');
  const finish=(error,result)=>{if(settled)return;settled=true;clearTimer(timer);signal?.removeEventListener('abort',abort);if(error){try{outgoing?.destroy();incoming?.destroy();}catch{}reject(error);}else resolve(result);};
  const abort=()=>finish(failure());
  const live=()=>{const mono=now(),remaining=budget.remaining();if(signal.aborted||!Number.isFinite(mono)||mono<last||mono-start>=left||!Number.isFinite(remaining)||remaining<=0)throw Error();last=mono;};
  try{
   route=preprocessRoute(route?.path,route?.method);const request=route.method==='POST'?preprocessInput(body):null;if(!request&&body!==undefined)fault(400,'invalid_preprocessing_request');body=request?Buffer.from(JSON.stringify(request)):undefined;
   left=Math.floor(budget.remaining());start=last=now();if(typeof requestImpl!=='function'||!signal||signal.aborted||!Number.isFinite(left)||left<1||left>PREPROCESS_CAP||!Number.isFinite(start)||!/^[1-9]\d{12}$/.test(budget.deadlineUnixMs)||typeof accessToken!=='string'||!/^[A-Za-z0-9._-]{1,8192}$/.test(accessToken))throw Error();
   const headers={Accept:'application/json',Authorization:'Bearer '+accessToken,Origin:'http://127.0.0.1:4206','X-Cyomics-Remaining-Ms':String(left),'X-Cyomics-Deadline-Unix-Ms':budget.deadlineUnixMs};if(request)Object.assign(headers,{'Content-Type':'application/json','Content-Length':String(body.byteLength),'X-Cyomics-Request':'1'});
   timer=setTimer(abort,left);signal.addEventListener('abort',abort,{once:true});live();
   outgoing=requestImpl({hostname:'127.0.0.1',port:57430,path:route.path,method:route.method,headers,agent:false},response=>{
    incoming=response;if(settled){response.on('error',()=>{});response.destroy();return;}const invalid=()=>finish(failure());response.on('error',invalid);response.once('aborted',invalid);
    try{
     live();if(!sent||!Array.isArray(response.rawHeaders)||response.rawHeaders.length%2)throw Error();const h=new Map();
     for(let i=0;i<response.rawHeaders.length;i+=2){const k=response.rawHeaders[i],v=response.rawHeaders[i+1];if(typeof k!=='string'||typeof v!=='string'||! /^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(k)||/[\x00-\x1f\x7f]/.test(v))throw Error();const name=k.toLowerCase();if(['location','refresh','set-cookie'].includes(name)||h.has(name))throw Error();h.set(name,v);}
     if(response.headers?.['set-cookie']!==undefined||!/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(h.get('content-type')||'')||![undefined,'identity'].includes(h.get('content-encoding'))||h.has('transfer-encoding')&&(h.get('transfer-encoding')!=='chunked'||h.has('content-length')))throw Error();
     const status=response.statusCode;if(![200,201].includes(status)&&!errors.has(status))throw Error();const cap=[200,201].includes(status)?PREPROCESS_SUCCESS:PREPROCESS_ERROR,length=h.get('content-length');if(length!==undefined&&(!/^\d{1,10}$/.test(length)||Number(length)>cap))throw Error();
     response.on('data',chunk=>{if(settled)return;try{live();if(!(chunk instanceof Uint8Array)||size+chunk.byteLength>cap)throw Error();size+=chunk.byteLength;chunks.push(chunk);}catch{invalid();}});
     response.once('end',()=>{if(settled)return;ended=true;try{live();if(length!==undefined&&Number(length)!==size)throw Error();const value=preprocessReply(route,status,strict(Buffer.concat(chunks,size)),request);live();finish(undefined,{status,value});}catch{invalid();}});
     response.once('close',()=>{if(!ended&&!settled)invalid();});
    }catch{invalid();}
   });
   outgoing.on('error',abort);outgoing.once('close',()=>{if(!settled&&!incoming)abort();});if(settled){outgoing.destroy();return;}live();sent=true;onSent();if(settled)return;live();outgoing.end(body);
  }catch(error){finish(error instanceof PreprocessBridgeFailure&&!sent?error:failure());}
 });
}
export async function sendPreprocessJSONResponse(req,res,{status,value,signal,checkLive,remaining,onController,setTimer=setTimeout,clearTimer=clearTimeout,now=()=>performance.now()}){
 let body;try{body=Buffer.from(JSON.stringify(value));if(body.length>([200,201].includes(status)?PREPROCESS_SUCCESS:PREPROCESS_ERROR))throw Error();}catch{throw new PreprocessBridgeFailure();}
 const local=signal?null:new AbortController(),activeSignal=signal??local.signal;let timer,start,last,left;
 const abort=()=>local?.abort();if(local){start=last=now();left=remaining?Math.min(PREPROCESS_CAP,remaining()):PREPROCESS_CAP;if(!Number.isFinite(start)||!Number.isFinite(left)||left<=0)throw new PreprocessBridgeFailure(504,'preprocessing_timeout');onController?.(local);req.once('aborted',abort);res.once('close',abort);timer=setTimer(abort,Math.max(1,Math.ceil(left)));}
 const guard=()=>{if(local){const current=now();if(!Number.isFinite(current)||current<last||current-start>=left||remaining&&remaining()<=0||req.aborted||req.socket?.destroyed)throw new PreprocessBridgeFailure(504,'preprocessing_timeout');last=current;}checkLive?.();};
 try{await sendRawUploadResponse(res,{status,headers:{'Content-Type':'application/json; charset=utf-8','Content-Length':String(body.length),'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','Connection':'close'},body,signal:activeSignal,checkLive:guard});}
 finally{if(local){clearTimer(timer);req.removeListener('aborted',abort);res.removeListener('close',abort);local.abort();}}
}
