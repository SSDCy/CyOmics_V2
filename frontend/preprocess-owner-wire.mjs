import {sha,canonicalJSON,freeze,exact,uuid} from './raw-qc-contract.mjs';
import {strictJSON} from './sample-design-contract.mjs';
export {sha,canonicalJSON,freeze,exact,uuid};
export const FAMILIES=freeze(['Intensity','LFQ intensity']);
export const FLAGS=freeze(['contaminant','reverse','site_only']);
export const ARTIFACTS=freeze(['R-session-info.txt','filter-log.tsv','imputed-mask.tsv','original-blank-mask.tsv','original-zero-mask.tsv','parameters.json','raw-retained.tsv','row-map.tsv','summary.tsv','working-NA-mask.tsv','working-matrix.tsv']);
export const hex=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v);
export const time=v=>typeof v==='string'&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/.test(v)&&!v.startsWith('0000-')&&Number.isFinite(Date.parse(v))&&new Date(Date.parse(v)).toISOString()===v.slice(0,23)+'Z';
export class PreprocessFailure extends Error{constructor(code='invalid_response',status=503){super(code);this.code=code;this.status=status;}}
export const need=(ok,code='invalid_response')=>{if(!ok)throw new PreprocessFailure(code);};
// Enforce raw integer tokens BEFORE JS loses -0/0.0/0e0 distinctions. Strings
// (including escaped quotes and the literal text "-0") are never scanned as numbers.
export function parsePreprocessJSON(bytes){
  need(bytes instanceof Uint8Array&&bytes.length>0);const text=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(bytes);
  for(let i=0;i<text.length;i++){if(text[i]==='"'){for(i++;i<text.length;i++){if(text[i]==='\\'){i++;continue;}if(text[i]==='"')break;}}else if(/[-0-9]/.test(text[i])){const start=i;while(i<text.length&&!/[\s,\]}:]/.test(text[i]))i++;const token=text.slice(start,i);need(/^(?:0|[1-9]\d*)$/.test(token)&&Number.isSafeInteger(Number(token)));i--;}}
  const value=strictJSON(bytes);function walk(v,d=0){need(d<=8);if(v&&typeof v==='object')for(const child of Object.values(v))walk(child,d+1);}walk(value);return value;
}
export function parametersFor(flags){
  need(exact(flags,FLAGS)&&FLAGS.every(k=>typeof flags[k]==='boolean'),'invalid_parameters');
  return freeze({blank_policy:'to_NA',filter_flags:{...flags},imputation:'none',input_scale:'raw_nonnegative',log2:'positive_only',normalization:'none',output_scale:'log2',preset:'positive_log2_zero_na_no_impute_v1',pseudocount:0,seed:null,steps:['flags_filter','zero_and_blank_to_NA','log2_positive','normalization_none','imputation_none'],zero_policy:'to_NA'});
}
export function medianParametersFor(flags,parent){need(exact(parent,['processed_dataset_version_id','manifest_sha256'])&&uuid(parent.processed_dataset_version_id)&&hex(parent.manifest_sha256),'invalid_parameters');return freeze({...parametersFor(flags),normalization:'median',normalization_parent:{...parent},preset:'positive_log2_median_zero_na_no_impute_v1',steps:['flags_filter','zero_and_blank_to_NA','log2_positive','normalization_median','imputation_none']});}
export function preprocessParameters(v){const median=v?.normalization==='median';need(exact(v,['blank_policy','filter_flags','imputation','input_scale','log2','normalization','output_scale','preset','pseudocount','seed','steps','zero_policy',...(median?['normalization_parent']:[])]),'invalid_parameters');const expected=median?medianParametersFor(v.filter_flags,v.normalization_parent):parametersFor(v.filter_flags);need(!Object.is(v.pseudocount,-0)&&canonicalJSON(v)===canonicalJSON(expected),'invalid_parameters');return expected;}
export function encodeCursor(v){need(exact(v,['v','createdAt','job_id'])&&v.v===1&&time(v.createdAt)&&uuid(v.job_id),'invalid_cursor');return btoa(canonicalJSON(v)).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');}
export function decodeCursor(v){need(typeof v==='string'&&v.length<=192&&/^[A-Za-z0-9_-]+$/.test(v),'invalid_cursor');let out;try{const raw=atob(v.replaceAll('-','+').replaceAll('_','/'));out=parsePreprocessJSON(Uint8Array.from(raw,c=>c.charCodeAt(0)));}catch{throw new PreprocessFailure('invalid_cursor');}need(encodeCursor(out)===v,'invalid_cursor');return out;}
export function preprocessingRoute(o){
  const {kind,projectId,datasetVersionId,requestId,jobId,versionId,name}=o;need(uuid(projectId),'invalid_request');let path,method='GET';
  if(['submit','lookup','list'].includes(kind)){need(uuid(datasetVersionId),'invalid_request');path='/api/v1/datasets/'+datasetVersionId+'/preprocessing-jobs?project_id='+projectId;
    if(kind==='submit'){need(o.cursor==null&&o.limit==null,'invalid_request');method='POST';}
    else if(kind==='lookup'){need(uuid(requestId)&&o.cursor==null&&o.limit==null,'invalid_request');path+='&request_id='+requestId;}
    else{need(requestId==null,'invalid_request');const limit=o.limit??10;need(Number.isSafeInteger(limit)&&limit>=1&&limit<=10,'invalid_request');path+='&limit='+limit;if(o.cursor!=null){decodeCursor(o.cursor);path+='&cursor='+o.cursor;}}
  }else if(kind==='job'){need(uuid(jobId),'invalid_request');path='/api/v1/preprocessing-jobs/'+jobId+'?project_id='+projectId;}
  else{need(['version','artifact'].includes(kind)&&uuid(versionId),'invalid_request');path='/api/v1/processed-dataset-versions/'+versionId;if(kind==='artifact'){need(ARTIFACTS.includes(name),'invalid_request');path+='/artifacts/'+name;}path+='?project_id='+projectId;}
  need(new TextEncoder().encode(path).length<=1024&&new TextEncoder().encode(path.split('?')[1]).length<=512,'invalid_request');return freeze({path,method});
}
const TASK=['schemaVersion','job_id','request_id','request_sha256','binding_sha256','source','quant_family','parameters','parameters_sha256','proposal_sha256','status','created_at','result','revision','started_at','finished_at','observation','error','runtime','attempt_id'];
const SOURCE=['project_id','dataset_version_id','file_id','source_bytes','source_sha256','parser_contract','config_sha256','report_sha256','row_count','column_count','selected_columns_sha256'];
const RUNTIME=['registration_id','profile_sha256','runtime_binding_sha256','attestation_sha256','receipt_sha256'];
const FAILURES=['input_integrity_failed','execution_unavailable','resource_limit','runner_failed','output_invalid','publish_integrity_failed','empty_not_analyzable','worker_interrupted'];
const int=(v,min=0,max=Number.MAX_SAFE_INTEGER)=>Number.isSafeInteger(v)&&v>=min&&v<=max;
export function validateSource(v){need(exact(v,SOURCE)&&['project_id','dataset_version_id','file_id'].every(k=>uuid(v[k]))&&['source_sha256','config_sha256','report_sha256','selected_columns_sha256'].every(k=>hex(v[k]))&&v.parser_contract==='protein_groups_txt_v1'&&int(v.source_bytes,1,26214400)&&int(v.row_count,1,10000)&&int(v.column_count,2,512)&&v.row_count*v.column_count<=2000000);return v;}
async function validateRuntime(r,j,cryptoImpl){
  need(exact(r,RUNTIME)&&uuid(r.registration_id)&&['profile_sha256','runtime_binding_sha256','attestation_sha256'].every(k=>hex(r[k]))&&(r.receipt_sha256===null||hex(r.receipt_sha256)));
  need(r.runtime_binding_sha256===await sha({attempt_id:j.attempt_id,binding_sha256:j.binding_sha256,contract:'preprocessing_runtime_binding_v1',job_id:j.job_id,profile_sha256:r.profile_sha256,proposal_sha256:j.proposal_sha256,registration_id:r.registration_id},cryptoImpl));
}
export async function validatePreprocessTask(j,{source,body=null,reference=null,previous=null,method='GET',status=200,cryptoImpl=globalThis.crypto}={}){
  validateSource(source);need(exact(j,TASK)&&j.schemaVersion===2&&uuid(j.job_id)&&uuid(j.request_id)&&FAMILIES.includes(j.quant_family)&&canonicalJSON(j.source)===canonicalJSON(source));
  need(method==='POST'?[200,201].includes(status):method==='GET'&&status===200);const parameters=preprocessParameters(j.parameters),request={request_id:j.request_id,quant_family:j.quant_family,parameters};if(body)need(canonicalJSON(body)===canonicalJSON(request));
  need(['request_sha256','binding_sha256','parameters_sha256','proposal_sha256'].every(k=>hex(j[k]))&&j.parameters_sha256===await sha(parameters,cryptoImpl)&&j.request_sha256===await sha({project_id:source.project_id,dataset_version_id:source.dataset_version_id,request},cryptoImpl));
  need(['queued','running','failed','succeeded'].includes(j.status)&&int(j.revision,1)&&time(j.created_at)&&['confirmed','reconciliation_pending'].includes(j.observation));
  if(j.status==='queued')need(j.revision===1&&j.attempt_id===null&&j.runtime===null&&j.started_at===null&&j.finished_at===null&&j.error===null&&j.result===null&&j.observation==='confirmed');
  else{need(uuid(j.attempt_id)&&time(j.started_at)&&j.started_at>=j.created_at);await validateRuntime(j.runtime,j,cryptoImpl);
    if(j.status==='running')need(j.revision>=2&&j.finished_at===null&&j.error===null&&j.result===null);
    else{need(j.revision>=3&&time(j.finished_at)&&j.finished_at>=j.started_at&&j.observation==='confirmed');
      if(j.status==='failed')need(exact(j.error,['code'])&&FAILURES.includes(j.error.code)&&j.result===null);
      else need(j.error===null&&hex(j.runtime.receipt_sha256)&&exact(j.result,['processed_dataset_version_id','manifest_sha256','artifact_count','analysis_ready'])&&uuid(j.result.processed_dataset_version_id)&&hex(j.result.manifest_sha256)&&j.result.artifact_count===11&&j.result.analysis_ready===false);
    }
  }
  if(reference?.parameters)need(canonicalJSON(parameters)===canonicalJSON(reference.parameters));if(reference)need(j.request_id===reference.request_id&&j.quant_family===reference.quant_family&&canonicalJSON(j.parameters.filter_flags)===canonicalJSON(reference.filter_flags));if(reference?.job_id){if(reference.revision!==undefined){need(j.revision>=reference.revision);if(j.revision===reference.revision||['failed','succeeded'].includes(reference.status))need(await sha(j,cryptoImpl)===reference.task_sha256);if(reference.attestation_sha256!==null)need(j.runtime?.attestation_sha256===reference.attestation_sha256);if(reference.receipt_sha256!==null)need(j.runtime?.receipt_sha256===reference.receipt_sha256);}for(const key of ['job_id','binding_sha256','proposal_sha256','created_at'])need(j[key]===reference[key]);if(reference.attempt_id)need(j.attempt_id===reference.attempt_id&&j.runtime?.runtime_binding_sha256===reference.runtime_binding_sha256);if(reference.processed_dataset_version_id)need(j.status==='succeeded'&&j.result.processed_dataset_version_id===reference.processed_dataset_version_id&&j.result.manifest_sha256===reference.manifest_sha256);}
  if(previous){for(const key of ['schemaVersion','job_id','request_id','request_sha256','binding_sha256','source','quant_family','parameters','parameters_sha256','proposal_sha256','created_at'])need(canonicalJSON(j[key])===canonicalJSON(previous[key]));need(j.revision>=previous.revision);if(j.revision===previous.revision||['failed','succeeded'].includes(previous.status))need(canonicalJSON(j)===canonicalJSON(previous));if(previous.status!=='queued'){need(j.status!=='queued'&&j.attempt_id===previous.attempt_id&&j.started_at===previous.started_at);for(const key of RUNTIME.filter(k=>k!=='receipt_sha256'))need(j.runtime[key]===previous.runtime[key]);if(previous.runtime.receipt_sha256!==null)need(j.runtime.receipt_sha256===previous.runtime.receipt_sha256);}}
  return freeze(structuredClone(j));
}
export const order=(a,b)=>a.created_at===b.created_at?(a.job_id<b.job_id?-1:a.job_id>b.job_id?1:0):(a.created_at<b.created_at?-1:1);
export async function validateTaskPage(v,{sourceFor,previousFor=()=>null,referenceFor=()=>null,cursor=null,cryptoImpl=globalThis.crypto}={}){
  need(exact(v,['items','next_cursor'])&&Array.isArray(v.items)&&v.items.length<=10&&new Set(v.items.map(j=>j.job_id)).size===v.items.length&&new Set(v.items.map(j=>j.request_id)).size===v.items.length);const items=[];let boundary=cursor?decodeCursor(cursor):null;
  for(const raw of v.items){const j=await validatePreprocessTask(raw,{source:sourceFor(raw.quant_family),previous:previousFor(raw.job_id),reference:referenceFor(raw.request_id),cryptoImpl});if(items.length)need(order(items.at(-1),j)>0);if(boundary)need(order({created_at:boundary.createdAt,job_id:boundary.job_id},j)>0);items.push(j);}
  if(v.next_cursor!==null){decodeCursor(v.next_cursor);need(items.length>0&&v.next_cursor===encodeCursor({v:1,createdAt:items.at(-1).created_at,job_id:items.at(-1).job_id}));}
  return freeze({items,next_cursor:v.next_cursor});
}
export async function validateProcessedVersion(v,{task,previous=null,cryptoImpl=globalThis.crypto}={}){
  const keys=['schemaVersion','processed_dataset_version_id','project_id','parent_dataset_version_id','file_id','created_by','created_at','job_id','attempt_id','source','quant_family','steps','parameters','parameters_sha256','proposal_sha256','runtime','tool','manifest_sha256','artifacts','analysis_ready'];
  need(task?.status==='succeeded'&&exact(v,keys)&&v.schemaVersion===2&&v.analysis_ready===false&&v.processed_dataset_version_id===task.result.processed_dataset_version_id&&v.project_id===task.source.project_id&&v.parent_dataset_version_id===task.source.dataset_version_id&&v.file_id===task.source.file_id&&uuid(v.created_by)&&time(v.created_at)&&v.created_at>=task.created_at&&v.job_id===task.job_id&&v.attempt_id===task.attempt_id);
  for(const key of ['source','quant_family','parameters','parameters_sha256','proposal_sha256','runtime'])need(canonicalJSON(v[key])===canonicalJSON(task[key]));need(canonicalJSON(v.steps)===canonicalJSON(task.parameters.steps));
  const tool=['program','r_version','script_sha256','validator_sha256','runner_sha256','native_sha256','process_policy_sha256'];need(exact(v.tool,tool)&&v.tool.program==='R'&&typeof v.tool.r_version==='string'&&/^R version /.test(v.tool.r_version)&&v.tool.r_version.length<=128&&tool.filter(k=>k.endsWith('sha256')).every(k=>hex(v.tool[k])));
  need(Array.isArray(v.artifacts)&&v.artifacts.length===11);let total=0;for(const [i,a]of v.artifacts.entries()){need(exact(a,['name','bytes','sha256','media_type'])&&a.name===ARTIFACTS[i]&&int(a.bytes,1,26214400)&&hex(a.sha256)&&a.media_type===(a.name==='parameters.json'?'application/json':a.name==='R-session-info.txt'?'text/plain; charset=utf-8':'text/tab-separated-values; charset=utf-8'));total+=a.bytes;}
  need(total<=134217728&&hex(v.manifest_sha256)&&v.manifest_sha256===task.result.manifest_sha256&&v.manifest_sha256===await sha(v.artifacts,cryptoImpl));if(previous)need(canonicalJSON(v)===canonicalJSON(previous));return freeze(structuredClone(v));
}
const ERRORS={400:['invalid_preprocessing_request'],401:['unauthenticated'],403:['auth_origin_rejected'],404:['not_found','dataset_not_found','preprocessing_job_not_found','processed_dataset_version_not_found','preprocessing_artifact_not_found'],405:['method_not_allowed'],409:['preprocessing_request_conflict','preprocessing_attempt_conflict','preprocessing_not_ready'],413:['preprocessing_request_too_large'],415:['unsupported_media_type'],422:['preprocessing_family_unavailable','preprocessing_flags_unavailable'],503:['preprocessing_unavailable','preprocessing_persisted_invalid','preprocessing_artifact_integrity_failed','preprocessing_execution_unavailable'],504:['preprocessing_timeout','preprocessing_submission_unknown']};
export function wireError(status,v){need(exact(v,['error'])&&exact(v.error,['code']));if(status===409&&v.error.code==='project_context_changed')return new PreprocessFailure('context_changed',409);need(ERRORS[status]?.includes(v.error.code));return new PreprocessFailure(v.error.code,status);}
