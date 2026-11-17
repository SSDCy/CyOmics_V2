import {canonicalJSON,exact,uuid,freeze,strictJSON} from './sample-design-contract.mjs';
import {validateImportDTO} from './private-import-dto.js';
export {canonicalJSON,exact,uuid,freeze};
export const CONTRACT='raw_qc_job_v0.1', MAX_RESPONSE=32768, MAX_ERROR=4096;
export const FAMILIES=freeze({intensity:'Intensity',lfq:'LFQ intensity'});
// Single reviewed identity location. New runtime/profile requires another Root review.
export const EXECUTION_IDENTITY=freeze({script:{bytes:4949,sha256:'2072a20e9dca53ab9ff03ef5a0a5d6aa9556dcee8004b5f28ce2fa4345624d64'},r_version:'R version 4.6.1',entry_kind:'native_R',limits_profile:'raw_qc_limits_v3',limits_sha256:'fe1e05ac949ed027969d4ae4475e0e66bbc44cdc1d9310486d33aa334f40aad0'});
export const OUTPUTS=freeze(['R-session-info.txt','blank-mask.tsv','numeric-matrix.tsv','raw-matrix.tsv','sample-qc.tsv','summary.tsv','zero-mask.tsv']);
const PUBLIC=['schemaVersion','job_id','request_id','request_sha256','binding_sha256','source','quant_family','params','execution','status','revision','created_at','started_at','finished_at','observation','error','result'];
const SOURCE=['project_id','dataset_version_id','file_id','source_bytes','source_sha256','parser_contract','config_sha256','report_sha256','row_count','column_count','selected_columns_sha256'];
const ERRORS={400:['invalid_raw_qc_request'],401:['unauthenticated'],403:['auth_origin_rejected'],404:['not_found','dataset_not_found','raw_qc_job_not_found','raw_qc_artifact_not_found'],405:['method_not_allowed'],409:['project_context_changed','raw_qc_request_conflict','raw_qc_result_not_ready'],413:['raw_qc_request_too_large'],415:['unsupported_media_type'],422:['raw_qc_family_unavailable','raw_qc_unsupported_source'],429:['raw_qc_capacity'],503:['raw_qc_unavailable','raw_qc_persisted_invalid','raw_qc_artifact_integrity_failed'],504:['raw_qc_timeout','raw_qc_submission_unknown']};
const FAILED=['input_integrity_failed','execution_unavailable','runner_failed','resource_limit','output_invalid','worker_interrupted','publish_integrity_failed'];
export class RawQCFailure extends Error{constructor(code='raw_qc_persisted_invalid',status=503){super(code);this.code=code;this.status=status;}}
export const requireQC=condition=>{if(!condition)throw new RawQCFailure();};
const hash=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v),integer=(v,min=0,max=Number.MAX_SAFE_INTEGER)=>Number.isSafeInteger(v)&&v>=min&&v<=max;
export async function sha(value,cryptoImpl=globalThis.crypto){return [...new Uint8Array(await cryptoImpl.subtle.digest('SHA-256',new TextEncoder().encode(canonicalJSON(value))))].map(x=>x.toString(16).padStart(2,'0')).join('');}
export function parseQC(bytes){const v=strictJSON(bytes);function walk(x,d=0){requireQC(d<=12);if(x&&typeof x==='object')for(const c of Object.values(x))walk(c,d+1);}walk(v);return v;}
export function qcError(status,v){requireQC(exact(v,['error'])&&exact(v.error,['code'])&&ERRORS[status]?.includes(v.error.code));return new RawQCFailure(v.error.code,status);}
export function qcRoute({kind,projectId,datasetVersionId,requestId,jobId}){
  requireQC(uuid(projectId));let path,method='GET';
  if(kind==='submit'||kind==='lookup'){requireQC(uuid(datasetVersionId));path='/api/v1/datasets/'+datasetVersionId+'/raw-qc-jobs?project_id='+projectId;if(kind==='lookup'){requireQC(uuid(requestId));path+='&request_id='+requestId;}else method='POST';}
  else{requireQC(kind==='job'&&uuid(jobId));path='/api/v1/raw-qc-jobs/'+jobId+'?project_id='+projectId;}
  return freeze({path,method});
}
export function qcRequest(requestId,family){requireQC(uuid(requestId)&&Object.hasOwn(FAMILIES,family));return freeze({schemaVersion:1,request_id:requestId,quant_family:family,params:{}});}
export async function qcSource(full,family,cryptoImpl=globalThis.crypto){
  requireQC(exact(full,['file','importDTO'])&&Object.hasOwn(FAMILIES,family));const {file,importDTO:dto}=full,v=dto?.datasetVersion,d=dto?.importReport;
  await validateImportDTO(dto,file,cryptoImpl,{method:'GET',versionId:v?.dataset_version_id});
  const selected=d.sample_columns.filter(c=>c.quant_family===FAMILIES[family]);requireQC(selected.length>0);
  return freeze({project_id:v.project_id,dataset_version_id:v.dataset_version_id,file_id:v.file_id,source_bytes:file.bytes,source_sha256:file.sha256,parser_contract:v.parser_contract,config_sha256:v.config_sha256,report_sha256:await sha(d,cryptoImpl),row_count:d.report.row_count,column_count:d.report.column_count,selected_columns_sha256:await sha(selected,cryptoImpl)});
}
function timestamp(v){return typeof v==='string'&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/.test(v)&&!v.startsWith('0000-')&&Number.isFinite(Date.parse(v))&&new Date(Date.parse(v)).toISOString()===v.slice(0,23)+'Z';}
function execution(e){requireQC(exact(e,['script','runner','output_validator','executable','r_version','entry_kind','limits_profile','limits_sha256']));for(const k of ['script','runner','output_validator','executable'])requireQC(exact(e[k],['bytes','sha256'])&&integer(e[k].bytes,1,268435456)&&hash(e[k].sha256));for(const [k,v]of Object.entries(EXECUTION_IDENTITY))requireQC(canonicalJSON(e[k])===canonicalJSON(v));}
export async function validateQCJob(job,{source,family,requestId,jobId=null,previous=null,method='GET',status=200,cryptoImpl=globalThis.crypto}){
  requireQC(exact(job,PUBLIC)&&job.schemaVersion===1&&uuid(job.job_id)&&job.request_id===requestId&&uuid(requestId)&&(!jobId||job.job_id===jobId)&&hash(job.request_sha256)&&hash(job.binding_sha256)&&Object.hasOwn(FAMILIES,family)&&job.quant_family===FAMILIES[family]&&exact(job.params,[]));
  requireQC(exact(job.source,SOURCE)&&canonicalJSON(job.source)===canonicalJSON(source));execution(job.execution);
  const requestHash=await sha({schemaVersion:1,project_id:source.project_id,dataset_version_id:source.dataset_version_id,quant_family:job.quant_family,params:{}},cryptoImpl);
  requireQC(job.request_sha256===requestHash&&job.binding_sha256===await sha({contract:CONTRACT,request_sha256:requestHash,source,execution:job.execution,quant_family:job.quant_family,params:{}},cryptoImpl));
  requireQC(['queued','running','succeeded','failed'].includes(job.status)&&integer(job.revision,1)&&timestamp(job.created_at));
  const started=job.started_at,finished=job.finished_at;
  requireQC((started===null||timestamp(started)&&started>=job.created_at)&&(finished===null||timestamp(finished)&&finished>=(started||job.created_at)));
  requireQC(job.status==='queued'?started===null&&finished===null:job.status==='running'?started!==null&&finished===null:finished!==null&&(job.status!=='succeeded'||started!==null));
  requireQC(['confirmed','reconciliation_pending'].includes(job.observation)&&(job.observation==='confirmed'||job.status==='running'));
  requireQC(job.status==='failed'?exact(job.error,['code'])&&FAILED.includes(job.error.code):job.error===null);
  requireQC(method==='GET'?status===200:method==='POST'&&status===(['queued','running'].includes(job.status)?202:200));
  if(job.status==='succeeded'){
    const r=job.result;requireQC(exact(r,['schemaVersion','job_id','binding_sha256','execution','artifacts','manifest_sha256','verified_at'])&&r.schemaVersion===1&&r.job_id===job.job_id&&r.binding_sha256===job.binding_sha256&&canonicalJSON(r.execution)===canonicalJSON(job.execution)&&timestamp(r.verified_at)&&r.verified_at>=started&&r.verified_at<=finished&&Array.isArray(r.artifacts)&&r.artifacts.length===7);
    let total=0;for(const [i,a]of r.artifacts.entries()){requireQC(exact(a,['name','bytes','sha256','media_type'])&&a.name===OUTPUTS[i]&&integer(a.bytes,1,67108864)&&hash(a.sha256)&&a.media_type===(a.name==='R-session-info.txt'?'text/plain; charset=utf-8':'text/tab-separated-values; charset=utf-8'));total+=a.bytes;}
    requireQC(total<=268435456&&r.manifest_sha256===await sha(r.artifacts,cryptoImpl));
  }else requireQC(job.result===null);
  if(previous){
    for(const k of ['job_id','request_id','request_sha256','binding_sha256','source','quant_family','params','execution','created_at'])requireQC(canonicalJSON(job[k])===canonicalJSON(previous[k]));
    if(previous.started_at!==null)requireQC(job.started_at===previous.started_at);
    requireQC(job.revision>=previous.revision&&(job.revision!==previous.revision||canonicalJSON(job)===canonicalJSON(previous)));
    if(previous.status!=='queued')requireQC(job.status!=='queued');if(['succeeded','failed'].includes(previous.status))requireQC(canonicalJSON(job)===canonicalJSON(previous));
  }
  return freeze(structuredClone(job));
}
