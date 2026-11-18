import {differentialResultRoute,differentialResultReply,DifferentialResultFailure,DIFFERENTIAL_RESULT_NAMES} from './differential-result-contract.mjs';
import {differentialJobRoute,differentialJobInput,differentialJobReply,DifferentialJobFailure} from './differential-job-contract.mjs';
import {differentialInputRoute,differentialInput,differentialInputReply,DifferentialInputFailure,DIFFERENTIAL_INPUT_MAX,DIFFERENTIAL_REPLY_MAX} from './differential-input-contract.mjs';
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
const errors=new Map([[400,['invalid_preprocessing_request']],[401,['unauthenticated']],[403,['auth_origin_rejected']],[404,['not_found','dataset_not_found','preprocessing_job_not_found','processed_dataset_version_not_found','preprocessing_artifact_not_found']],[405,['method_not_allowed']],[409,['preprocessing_request_conflict','preprocessing_attempt_conflict','preprocessing_not_ready']],[413,['preprocessing_request_too_large']],[415,['unsupported_media_type']],[422,['preprocessing_family_unavailable','preprocessing_flags_unavailable']],[503,['preprocessing_unavailable','preprocessing_persisted_invalid','preprocessing_execution_unavailable','preprocessing_artifact_integrity_failed']],[504,['preprocessing_timeout','preprocessing_submission_unknown']]]);
export function preprocessError(status,value,{upstream=false}={}){if(!exact(value,['error'])||!exact(value.error,['code'])||!(errors.get(status)?.includes(value.error.code)||!upstream&&status===409&&value.error.code==='project_context_changed'))fault(503,'preprocessing_unavailable');return value;}
function strict(bytes){
 const v=strictProjectJSON(bytes);
 function walk(x,d=0){if(d>12)throw Error();if(x&&typeof x==='object')Object.values(x).forEach(c=>walk(c,d+1));}walk(v);
 // These envelopes contain only nonnegative integer numbers. Reject JSON float,
 // exponent and negative-zero spellings before JSON.parse can erase their types.
 const skeleton=new TextDecoder('utf-8',{fatal:true}).decode(bytes).replace(/"(?:\\[\s\S]|[^"\\])*"/g,'""');
 for(const n of skeleton.match(/-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/g)||[])if(!/^(?:0|[1-9]\d*)$/.test(n))throw Error();
 return v;
}
export function ownsPreprocessRoute(raw){
 if(typeof raw!=='string')return false;const paths=[raw.split('?')[0]];
 try{paths.push(decodeURIComponent(raw.split('?')[0]),decodeURIComponent(new URL(raw,'http://127.0.0.1').pathname));}catch{}
 return paths.some(p=>{p=p.replace(/\/{2,}/g,'/');return p.startsWith('/api/v1/preprocessing-jobs')||p.startsWith('/api/v1/processed-dataset-versions')||/^\/api\/v1\/datasets\/[^/]+\/preprocessing-jobs(?:\/|$)/.test(p);});
}
export const PREPROCESS_QUERY_MAX=512,PREPROCESS_TARGET_MAX=1024,PREPROCESS_CURSOR_MAX=192,PREPROCESS_ARTIFACT_MAX=26214400;
export const preprocessArtifactNames=Object.freeze(['filter-log.tsv','row-map.tsv','summary.tsv','raw-retained.tsv','working-matrix.tsv','original-blank-mask.tsv','original-zero-mask.tsv','working-NA-mask.tsv','imputed-mask.tsv','R-session-info.txt','parameters.json'].sort());
const artifactMedia=name=>name.endsWith('.json')?'application/json':name.endsWith('.txt')?'text/plain; charset=utf-8':'text/tab-separated-values; charset=utf-8';
export function preprocessCursor(value){
 if(typeof value!=='string'||!(/^[A-Za-z0-9_-]{1,192}$/).test(value))throw Error();
 const raw=Buffer.from(value,'base64url');if(raw.length>384||raw.toString('base64url')!==value)throw Error();
 const item=strict(raw);if(!exact(item,['v','createdAt','job_id'])||item.v!==1||!timestamp(item.createdAt)||!uuid(item.job_id)||Buffer.from(canonical(item)).toString('base64url')!==value)throw Error();return item;
}
export function preprocessRoute(raw,method){
 if(typeof raw!=='string'||Buffer.byteLength(raw)>PREPROCESS_TARGET_MAX||/[^\x21-\x7e]|[%+#]/.test(raw))fault(404,'not_found');
 const [path,query,...extra]=raw.split('?');if(extra.length)fault(400,'invalid_preprocessing_request');
 const collection=/^\/api\/v1\/datasets\/([0-9a-f-]{36})\/preprocessing-jobs$/.exec(path),detail=/^\/api\/v1\/preprocessing-jobs\/([0-9a-f-]{36})$/.exec(path),version=/^\/api\/v1\/processed-dataset-versions\/([0-9a-f-]{36})$/.exec(path),artifact=/^\/api\/v1\/processed-dataset-versions\/([0-9a-f-]{36})\/artifacts\/([A-Za-z0-9_.-]{1,64})$/.exec(path);
 const match=collection||detail||version||artifact;if(!match||!uuid(match[1]))fault(404,'not_found');
 if(!(collection?['GET','POST']:['GET']).includes(method))fault(405,'method_not_allowed');
 if(typeof query!=='string'||Buffer.byteLength(query)>PREPROCESS_QUERY_MAX)fault(400,'invalid_preprocessing_request');const q=new Map();
 for(const pair of query.split('&')){const a=pair.split('=');if(a.length!==2||!['project_id','request_id','limit','cursor'].includes(a[0])||q.has(a[0]))fault(400,'invalid_preprocessing_request');q.set(a[0],a[1]);}
 if(!uuid(q.get('project_id')))fault(400,'invalid_preprocessing_request');
 const kind=method==='POST'?'submit':collection?(q.has('request_id')?'lookup':'list'):detail?'job':version?'version':'artifact';
 if(kind==='lookup'?(q.size!==2||!uuid(q.get('request_id'))):kind==='list'?[...q.keys()].some(k=>!['project_id','limit','cursor'].includes(k)):q.size!==1)fault(400,'invalid_preprocessing_request');
 let limit=10;if(q.has('limit')){if(!/^(?:[1-9]|10)$/.test(q.get('limit')))fault(400,'invalid_preprocessing_request');limit=Number(q.get('limit'));}
 if(q.has('cursor'))try{preprocessCursor(q.get('cursor'));}catch{fault(400,'invalid_preprocessing_request');}
 const target=path+'?project_id='+q.get('project_id')+['request_id','limit','cursor'].filter(k=>q.has(k)).map(k=>'&'+k+'='+q.get(k)).join('');if(target!==raw)fault(400,'invalid_preprocessing_request');
 if(artifact&&!preprocessArtifactNames.includes(artifact[2]))fault(404,'preprocessing_artifact_not_found');
 return Object.freeze({path:target,method,kind,project_id:q.get('project_id'),dataset_version_id:collection?.[1]||null,job_id:detail?.[1]||null,request_id:q.get('request_id')||null,version_id:(version||artifact)?.[1]||null,name:artifact?.[2]||null,limit,cursor:q.get('cursor')||null});
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
export function readPreprocessBody(req,signal,method,differentialInputReview=false,differentialJobs=false,differentialResults=false){return new Promise((resolve,reject)=>{
 const chunks=[];let size=0,done=false;const cap=method==='POST'?(differentialJobs?8192:differentialInputReview?DIFFERENTIAL_INPUT_MAX:4096):0;
 const invalid=differentialResults?'invalid_differential_result_request':differentialJobs?'invalid_differential_request':differentialInputReview?'invalid_differential_input':'invalid_preprocessing_request',tooLarge=differentialJobs?'differential_request_too_large':differentialInputReview?'differential_input_too_large':'preprocessing_request_too_large';
 const cleanup=()=>{req.removeListener('data',data);req.removeListener('end',end);req.removeListener('close',closed);signal.removeEventListener('abort',abort);};
 const finish=error=>{if(done)return;done=true;cleanup();if(error){req.pause?.();reject(error);}else resolve(Buffer.concat(chunks,size));};
 const data=chunk=>{if(!(chunk instanceof Uint8Array))return finish(new PreprocessBridgeFailure(400,invalid));if(size+chunk.byteLength>cap)return finish(new PreprocessBridgeFailure(cap?413:400,cap?tooLarge:invalid));size+=chunk.byteLength;chunks.push(chunk);};
 const end=()=>finish(),abort=()=>finish(new PreprocessBridgeFailure(differentialInputReview?503:504,differentialInputReview?'differential_input_unavailable':'preprocessing_timeout')),error=()=>finish(new PreprocessBridgeFailure(400,invalid)),closed=()=>finish(new PreprocessBridgeFailure(400,invalid));
 req.on('data',data);req.once('end',end);req.on('error',error);req.once('close',closed);req.once('close',()=>req.removeListener('error',error));signal.addEventListener('abort',abort,{once:true});if(signal.aborted)abort();
});}
const publicKeys='schemaVersion job_id request_id request_sha256 binding_sha256 source quant_family parameters parameters_sha256 proposal_sha256 status created_at result revision started_at finished_at observation error runtime attempt_id'.split(' ');
const sourceKeys='project_id dataset_version_id file_id source_bytes source_sha256 parser_contract config_sha256 report_sha256 row_count column_count selected_columns_sha256'.split(' ');
const runtimeKeys='registration_id profile_sha256 runtime_binding_sha256 attestation_sha256 receipt_sha256'.split(' ');
const versionKeys='schemaVersion processed_dataset_version_id project_id parent_dataset_version_id file_id created_by created_at job_id attempt_id source quant_family steps parameters parameters_sha256 proposal_sha256 runtime tool manifest_sha256 artifacts analysis_ready'.split(' ');
const digest=v=>createHash('sha256').update(canonical(v)).digest('hex');
function sourceValue(s,route){
 if(!exact(s,sourceKeys)||!['project_id','dataset_version_id','file_id'].every(k=>uuid(s[k]))||!['source_sha256','config_sha256','report_sha256','selected_columns_sha256'].every(k=>hash(s[k]))||!integer(s.source_bytes,1,26214400)||!integer(s.row_count,1,10000)||!integer(s.column_count,2,512)||s.row_count*s.column_count>2000000||s.parser_contract!=='protein_groups_txt_v1'||s.project_id!==route.project_id||route.dataset_version_id&&s.dataset_version_id!==route.dataset_version_id)throw Error();
}
function runtimeValue(r,task=null){
 if(!exact(r,runtimeKeys)||!uuid(r.registration_id)||!['profile_sha256','runtime_binding_sha256','attestation_sha256'].every(k=>hash(r[k]))||r.receipt_sha256!==null&&!hash(r.receipt_sha256))throw Error();
 if(task&&r.runtime_binding_sha256!==digest({contract:'preprocessing_runtime_binding_v1',job_id:task.job_id,binding_sha256:task.binding_sha256,proposal_sha256:task.proposal_sha256,registration_id:r.registration_id,profile_sha256:r.profile_sha256,attempt_id:task.attempt_id}))throw Error();
}
function taskValue(v,route,request){
 if(!exact(v,publicKeys)||v.schemaVersion!==2||!uuid(v.job_id)||!uuid(v.request_id)||!['request_sha256','binding_sha256','parameters_sha256','proposal_sha256'].every(k=>hash(v[k]))||!['Intensity','LFQ intensity'].includes(v.quant_family)||!timestamp(v.created_at)||!integer(v.revision,1,Number.MAX_SAFE_INTEGER)||!['confirmed','reconciliation_pending'].includes(v.observation))throw Error();
 sourceValue(v.source,route);if(route.job_id&&v.job_id!==route.job_id||route.request_id&&v.request_id!==route.request_id)throw Error();
 parameters(v.parameters);const normalized={request_id:v.request_id,quant_family:v.quant_family,parameters:v.parameters};
 if(request&&canonical(normalized)!==canonical(request)||v.parameters_sha256!==digest(v.parameters)||v.request_sha256!==digest({project_id:v.source.project_id,dataset_version_id:v.source.dataset_version_id,request:normalized}))throw Error();
 if(v.status==='queued'){
  if(v.revision!==1||v.observation!=='confirmed'||!['started_at','finished_at','runtime','attempt_id','error','result'].every(k=>v[k]===null))throw Error();
 }else{
  if(!['running','succeeded','failed'].includes(v.status)||v.revision<2||!uuid(v.attempt_id)||!timestamp(v.started_at)||v.started_at<v.created_at)throw Error();runtimeValue(v.runtime,v);
  if(v.status==='running'){if(v.finished_at!==null||v.error!==null||v.result!==null)throw Error();}
  else{
   if(v.revision<3||v.observation!=='confirmed'||!timestamp(v.finished_at)||v.finished_at<v.started_at)throw Error();
   if(v.status==='failed'){if(!exact(v.error,['code'])||!['input_integrity_failed','execution_unavailable','resource_limit','runner_failed','output_invalid','publish_integrity_failed','empty_not_analyzable','worker_interrupted'].includes(v.error.code)||v.result!==null)throw Error();}
   else if(v.error!==null||!hash(v.runtime.receipt_sha256)||!exact(v.result,['processed_dataset_version_id','manifest_sha256','artifact_count','analysis_ready'])||!uuid(v.result.processed_dataset_version_id)||!hash(v.result.manifest_sha256)||v.result.artifact_count!==11||v.result.analysis_ready!==false)throw Error();
  }
 }
 return v;
}
function versionValue(v,route){
 if(!exact(v,versionKeys)||v.schemaVersion!==2||!['processed_dataset_version_id','project_id','parent_dataset_version_id','file_id','created_by','job_id','attempt_id'].every(k=>uuid(v[k]))||v.project_id!==route.project_id||route.version_id&&v.processed_dataset_version_id!==route.version_id||!timestamp(v.created_at)||!['Intensity','LFQ intensity'].includes(v.quant_family)||v.analysis_ready!==false||!hash(v.proposal_sha256)||v.parameters_sha256!==digest(v.parameters)||canonical(v.steps)!==canonical(steps))throw Error();
 parameters(v.parameters);sourceValue(v.source,route);if(v.source.dataset_version_id!==v.parent_dataset_version_id||v.source.file_id!==v.file_id)throw Error();runtimeValue(v.runtime);if(!hash(v.runtime.receipt_sha256))throw Error();
 const toolKeys=['program','r_version','script_sha256','validator_sha256','runner_sha256','native_sha256','process_policy_sha256'];
 if(!exact(v.tool,toolKeys)||v.tool.program!=='R'||v.tool.r_version!=='R version 4.6.1'||!toolKeys.slice(2).every(k=>hash(v.tool[k])))throw Error();
 if(!Array.isArray(v.artifacts)||v.artifacts.length!==11)throw Error();let total=0;
 v.artifacts.forEach((x,i)=>{if(!exact(x,['name','bytes','sha256','media_type'])||x.name!==preprocessArtifactNames[i]||!integer(x.bytes,1,PREPROCESS_ARTIFACT_MAX)||!hash(x.sha256)||x.media_type!==artifactMedia(x.name))throw Error();total+=x.bytes;});
 if(total>134217728||v.manifest_sha256!==digest(v.artifacts))throw Error();return v;
}
export function preprocessReply(route,status,v,request=null){
 if(errors.has(status))return preprocessError(status,v,{upstream:true});
 if(![200,201].includes(status)||route.method!=='POST'&&status!==200)throw Error();
 if(route.kind==='version')return versionValue(v,route);
 if(route.kind==='list'){
  if(!exact(v,['items','next_cursor'])||!Array.isArray(v.items)||v.items.length>route.limit)throw Error();const seen=new Set();let previous=null;
  for(const item of v.items){taskValue(item,route,null);const key=item.created_at+item.job_id;if(seen.has(item.job_id)||previous!==null&&key>=previous)throw Error();seen.add(item.job_id);previous=key;}
  if(v.next_cursor!==null){const last=v.items.at(-1),cursor=preprocessCursor(v.next_cursor);if(!last||cursor.createdAt!==last.created_at||cursor.job_id!==last.job_id)throw Error();}return v;
 }
 if(route.kind==='artifact')throw Error();return taskValue(v,route,request);
}
const ARTIFACT=Symbol('verified-preprocessing-artifact');
export function createPreprocessBridge({requestImpl=null,setTimer=setTimeout,clearTimer=clearTimeout,now=()=>performance.now(),differentialInputReview=false,differentialJobs=false,differentialResults=false}={}){
 return ({route,body,accessToken,ownerId,signal,budget,onSent=()=>{}})=>new Promise((resolve,reject)=>{
  let outgoing,incoming,timer,settled=false,sent=false,size=0,ended=false,last,start,left;const chunks=[];
  const expired=()=>{try{return signal?.aborted===true||budget.remaining()<=0||start!==undefined&&now()-start>=left;}catch{return true;}};
  const failure=()=>differentialResults?new DifferentialResultFailure(expired()?504:503,expired()?'differential_results_timeout':'differential_results_unavailable'):differentialJobs?new DifferentialJobFailure(sent&&route?.method==='POST'||expired()?504:503,sent&&route?.method==='POST'?'differential_submission_unknown':expired()?'differential_timeout':'differential_unavailable'):differentialInputReview?new PreprocessBridgeFailure(503,'differential_input_unavailable'):new PreprocessBridgeFailure(sent&&route?.method==='POST'||expired()?504:503,sent&&route?.method==='POST'?'preprocessing_submission_unknown':expired()?'preprocessing_timeout':'preprocessing_unavailable');
  const finish=(error,result)=>{if(settled)return;settled=true;clearTimer(timer);signal?.removeEventListener('abort',abort);if(error){try{outgoing?.destroy();incoming?.destroy();}catch{}reject(error);}else resolve(result);};
  const abort=()=>finish(failure());
  const live=()=>{const mono=now(),remaining=budget.remaining();if(signal.aborted||!Number.isFinite(mono)||mono<last||mono-start>=left||!Number.isFinite(remaining)||remaining<=0)throw Error();last=mono;};
  try{
   route=differentialResults?differentialResultRoute(route?.path,route?.method):differentialJobs?differentialJobRoute(route?.path,route?.method):differentialInputReview?differentialInputRoute(route?.path,route?.method):preprocessRoute(route?.path,route?.method);const request=route.method==='POST'?(differentialJobs?differentialJobInput(body):differentialInputReview?differentialInput(body):preprocessInput(body)):null;if(!request&&body!==undefined)fault(400,'invalid_preprocessing_request');body=request?Buffer.from(JSON.stringify(request)):undefined;
   left=Math.floor(budget.remaining());start=last=now();if(typeof requestImpl!=='function'||!signal||signal.aborted||!Number.isFinite(left)||left<1||left>PREPROCESS_CAP||!Number.isFinite(start)||!/^[1-9]\d{12}$/.test(budget.deadlineUnixMs)||typeof accessToken!=='string'||!/^[A-Za-z0-9._-]{1,8192}$/.test(accessToken))throw Error();
   const headers={Accept:route.kind==='artifact'?'application/octet-stream':'application/json',Authorization:'Bearer '+accessToken,Origin:'http://127.0.0.1:4206','X-Cyomics-Remaining-Ms':String(left),'X-Cyomics-Deadline-Unix-Ms':budget.deadlineUnixMs};if(request)Object.assign(headers,{'Content-Type':'application/json','Content-Length':String(body.byteLength),'X-Cyomics-Request':'1'});
   timer=setTimer(abort,left);signal.addEventListener('abort',abort,{once:true});live();
   outgoing=requestImpl({hostname:'127.0.0.1',port:57430,path:route.path,method:route.method,headers,agent:false},response=>{
    incoming=response;if(settled){response.on('error',()=>{});response.destroy();return;}const invalid=()=>finish(failure());response.on('error',invalid);response.once('aborted',invalid);
    try{
     live();if(!sent||!Array.isArray(response.rawHeaders)||response.rawHeaders.length%2)throw Error();const h=new Map();
     for(let i=0;i<response.rawHeaders.length;i+=2){const k=response.rawHeaders[i],v=response.rawHeaders[i+1];if(typeof k!=='string'||typeof v!=='string'||! /^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(k)||/[\x00-\x1f\x7f]/.test(v))throw Error();const name=k.toLowerCase();if(['location','refresh','set-cookie'].includes(name)||h.has(name))throw Error();h.set(name,v);}
     const binary=route.kind==='artifact'&&response.statusCode===200;
     if(response.headers?.['set-cookie']!==undefined||(!binary&&!/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(h.get('content-type')||'')||binary&&(h.get('content-type')!=='application/octet-stream'||h.get('content-disposition')!=='attachment; filename=\"'+route.name+'\"'||!hash(h.get('x-cyomics-file-sha256'))||h.get('cache-control')!=='no-store'||h.get('x-content-type-options')!=='nosniff'||h.get('referrer-policy')!=='no-referrer'))||![undefined,'identity'].includes(h.get('content-encoding'))||h.has('transfer-encoding')&&(h.get('transfer-encoding')!=='chunked'||h.has('content-length')))throw Error();
     const status=response.statusCode;if(![200,201].includes(status)&&!errors.has(status))throw Error();const cap=binary?PREPROCESS_ARTIFACT_MAX:[200,201].includes(status)?(differentialInputReview?DIFFERENTIAL_REPLY_MAX:PREPROCESS_SUCCESS):differentialJobs&&status===422?DIFFERENTIAL_REPLY_MAX:PREPROCESS_ERROR,length=h.get('content-length');if(binary&&length===undefined||length!==undefined&&(!/^\d{1,10}$/.test(length)||Number(length)>cap))throw Error();
     response.on('data',chunk=>{if(settled)return;try{live();if(!(chunk instanceof Uint8Array)||size+chunk.byteLength>cap)throw Error();size+=chunk.byteLength;chunks.push(chunk);}catch{invalid();}});
     response.once('end',()=>{if(settled)return;ended=true;try{live();if(length!==undefined&&Number(length)!==size)throw Error();const raw=Buffer.concat(chunks,size);if(binary){if(!size||createHash('sha256').update(raw).digest('hex')!==h.get('x-cyomics-file-sha256'))throw Error();live();finish(undefined,{status,artifact:{[ARTIFACT]:true,name:route.name,body:raw,mediaType:artifactMedia(route.name),sha256:h.get('x-cyomics-file-sha256')}});}else{const value=differentialResults?differentialResultReply(route,status,strict(raw),ownerId):differentialJobs?differentialJobReply(route,status,strict(raw),request):differentialInputReview?differentialInputReply(route,status,strict(raw),request):preprocessReply(route,status,strict(raw),request);if(differentialResults&&status===200&&route.kind!=='differential-run'&&digest(value.artifacts)!==value.manifest_sha256)throw Error();if(differentialJobs&&[200,201].includes(status)){preprocessReply({kind:'version',project_id:route.project_id,version_id:value.selection.processed_dataset_version_id},200,value.source);if(!uuid(ownerId)||value.request_sha256!==digest({owner_id:ownerId,project_id:route.project_id,request:{...value.selection,request_id:value.request_id}}))throw Error();}live();finish(undefined,{status,value});}}catch{invalid();}});
     response.once('close',()=>{if(!ended&&!settled)invalid();});
    }catch{invalid();}
   });
   outgoing.on('error',abort);outgoing.once('close',()=>{if(!settled&&!incoming)abort();});if(settled){outgoing.destroy();return;}live();sent=true;onSent();if(settled)return;live();outgoing.end(body);
  }catch(error){finish((error instanceof PreprocessBridgeFailure||error instanceof DifferentialInputFailure||error instanceof DifferentialJobFailure||error instanceof DifferentialResultFailure)&&!sent?error:failure());}
 });
}
export async function sendPreprocessJSONResponse(req,res,{status,value,signal,checkLive,remaining,onController,setTimer=setTimeout,clearTimer=clearTimeout,now=()=>performance.now(),artifact=null,differentialInputReview=false,differentialJobs=false,differentialResults=false}){
 let body;try{if((differentialInputReview||differentialJobs)&&artifact!==null)throw Error();if(artifact!==null){if(artifact[ARTIFACT]!==true||status!==200||!(differentialResults?DIFFERENTIAL_RESULT_NAMES:preprocessArtifactNames).includes(artifact.name)||!Buffer.isBuffer(artifact.body)||!artifact.body.length||artifact.body.length>PREPROCESS_ARTIFACT_MAX||artifact.mediaType!==artifactMedia(artifact.name)||createHash('sha256').update(artifact.body).digest('hex')!==artifact.sha256)throw Error();body=artifact.body;}else body=Buffer.from(JSON.stringify(value));if(artifact===null&&body.length>([200,201].includes(status)?(differentialInputReview?DIFFERENTIAL_REPLY_MAX:PREPROCESS_SUCCESS):differentialJobs&&status===422?DIFFERENTIAL_REPLY_MAX:PREPROCESS_ERROR))throw Error();}catch{throw new PreprocessBridgeFailure();}
 const local=signal?null:new AbortController(),activeSignal=signal??local.signal;let timer,start,last,left;
 const abort=()=>local?.abort();if(local){start=last=now();left=remaining?Math.min(PREPROCESS_CAP,remaining()):PREPROCESS_CAP;if(!Number.isFinite(start)||!Number.isFinite(left)||left<=0)throw new PreprocessBridgeFailure(504,'preprocessing_timeout');onController?.(local);req.once('aborted',abort);res.once('close',abort);timer=setTimer(abort,Math.max(1,Math.ceil(left)));}
 const guard=()=>{if(local){const current=now();if(!Number.isFinite(current)||current<last||current-start>=left||remaining&&remaining()<=0||req.aborted||req.socket?.destroyed)throw new PreprocessBridgeFailure(504,'preprocessing_timeout');last=current;}checkLive?.();};
 try{await sendRawUploadResponse(res,{status,headers:{'Content-Type':artifact?'application/octet-stream':'application/json; charset=utf-8',...(artifact?{'Content-Disposition':'attachment; filename=\"'+artifact.name+'\"','X-Cyomics-File-SHA256':artifact.sha256}:{}),'Content-Length':String(body.length),'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','Connection':'close'},body,signal:activeSignal,checkLive:guard});}
 finally{if(local){clearTimer(timer);req.removeListener('aborted',abort);res.removeListener('close',abort);local.abort();}}
}
