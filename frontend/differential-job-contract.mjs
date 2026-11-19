import {exact,uuid,freeze,canonicalJSON} from './sample-design-contract.mjs';
import {differentialInput,differentialInputReply} from './differential-input-contract.mjs';
import {parsePreprocessJSON} from './preprocess-owner-wire.mjs';
export const DIFFERENTIAL_JOB_MAX=32768;
export const DIFFERENTIAL_HISTORY_MAX=5*DIFFERENTIAL_JOB_MAX+512;
export class DifferentialJobFailure extends Error{constructor(status=503,code='differential_unavailable'){super(code);this.status=status;this.code=code;}}
const need=(ok,status=503,code='differential_unavailable')=>{if(!ok)throw new DifferentialJobFailure(status,code);};
export const jobHash=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v);
export function ownsDifferentialJobsRoute(raw){
 if(typeof raw!=='string')return false;const paths=[raw.split('?')[0]];
 try{paths.push(decodeURIComponent(raw.split('?')[0]),decodeURIComponent(new URL(raw,'http://127.0.0.1').pathname));}catch{}
 return paths.some(p=>/^\/api\/v1\/projects\/[^/]+\/differential-jobs(?:\/|$)/.test(p.replace(/\/{2,}/g,'/')));
}
export function differentialJobRoute(raw,method){
 const history=typeof raw==='string'&&/^\/api\/v1\/projects\/([0-9a-f-]{36})\/differential-jobs\?processed_dataset_version_id=([0-9a-f-]{36})$/.exec(raw);
 if(history){need(uuid(history[1])&&uuid(history[2]),404,'not_found');need(method==='GET',405,'method_not_allowed');return freeze({path:raw,method,kind:'differential-history',project_id:history[1],processed_dataset_version_id:history[2]});}
 const m=typeof raw==='string'&&/^\/api\/v1\/projects\/([0-9a-f-]{36})\/differential-jobs(?:\/([0-9a-f-]{36}))?(?:\?request_id=([0-9a-f-]{36}))?$/.exec(raw);
 need(m&&uuid(m[1])&&(!m[2]||uuid(m[2]))&&(!m[3]||uuid(m[3])),404,'not_found');
 need((m[2]?['GET']:['GET','POST']).includes(method),405,'method_not_allowed');
 need(method==='POST'?!m[2]&&!m[3]:m[2]?!m[3]:!!m[3],400,'invalid_differential_request');
 return freeze({path:raw,method,kind:method==='POST'?'differential-submit':m[2]?'differential-job':'differential-lookup',project_id:m[1],job_id:m[2]||null,request_id:m[3]||null});
}
export function differentialJobInput(bytes){
 need(bytes instanceof Uint8Array&&bytes.length>0,400,'invalid_differential_request');need(bytes.length<=8192,413,'differential_request_too_large');
 let value;try{value=parsePreprocessJSON(bytes);}catch{throw new DifferentialJobFailure(400,'invalid_differential_request');}
 need(exact(value,['request_id','processed_dataset_version_id','sample_design_version_id','sample_design_request_id','contrast','replicate_model'])&&uuid(value.request_id),400,'invalid_differential_request');
 const {request_id,...selection}=value;try{differentialInput(new TextEncoder().encode(canonicalJSON(selection)));}catch{throw new DifferentialJobFailure(400,'invalid_differential_request');}
 return freeze(value);
}
const errors=new Map([[400,['invalid_differential_request','invalid_differential_input','invalid_quant_diff_input_selection','invalid_sample_design_request','invalid_project_query']],
 [401,['unauthenticated']],[403,['auth_origin_rejected']],[404,['not_found','differential_job_not_found','dataset_not_found','dataset_version_not_found','file_not_found','project_not_found','processed_dataset_version_not_found','preprocessing_artifact_not_found','sample_design_not_found']],
 [405,['method_not_allowed']],[409,['differential_request_conflict','project_context_changed','differential_capacity_busy']],[413,['differential_request_too_large','project_body_too_large','sample_design_request_too_large']],
 [415,['unsupported_media_type']],[422,['differential_input_too_large']],[503,['differential_unavailable','differential_persisted_invalid']],[504,['differential_timeout','differential_submission_unknown']]]);
export function differentialJobError(status,value,{upstream=false}={}){
 if(upstream&&status===503)return {error:{code:'differential_unavailable'}};
 need(exact(value,['error'])&&exact(value.error,['code'])&&errors.get(status)?.includes(value.error.code)&&!(upstream&&value.error.code==='project_context_changed'));return value;
}
const keys=['schemaVersion','contract','job_id','project_id','request_id','request_sha256','binding_sha256','selection','source','method','input_manifest','status','revision','observation','created_at','started_at','finished_at','attempt_id','runtime','result','error'];
export function differentialJobReply(route,status,value,body=null){
 if(route.kind==='differential-history'&&status===200){
  need(exact(value,['schemaVersion','contract','project_id','processed_dataset_version_id','jobs'])&&value.schemaVersion===1&&value.contract==='differential_job_history_v0.1'&&value.project_id===route.project_id&&value.processed_dataset_version_id===route.processed_dataset_version_id&&Array.isArray(value.jobs)&&value.jobs.length<=5);
  need(new Set(value.jobs.map(j=>j?.request_id)).size===value.jobs.length&&new Set(value.jobs.map(j=>j?.job_id)).size===value.jobs.length);
  for(const job of value.jobs){differentialJobReply({method:'GET',project_id:route.project_id,job_id:job?.job_id},200,job);need(job.selection.processed_dataset_version_id===route.processed_dataset_version_id);}
  need(new TextEncoder().encode(canonicalJSON(value)).length<=DIFFERENTIAL_HISTORY_MAX);return freeze(structuredClone(value));
 }
 if(status===422&&value?.queued===false){
  need(route.method==='POST'&&body&&exact(value,['schemaVersion','contract','project_id','processed_dataset_version_id','sample_design_version_id','sample_design_request_id','model_input_satisfied','reasons','source_analysis_ready','input_summary','calculation_started','queued','request_id'])&&value.request_id===body.request_id&&value.model_input_satisfied===false);
  const {queued,request_id,...review}=value;const {request_id:_,...selection}=body;differentialInputReply(route,200,review,selection);return freeze(structuredClone(value));
 }
 if(![200,201].includes(status))return differentialJobError(status,value,{upstream:true});
 need(route.method==='POST'||status===200);need(exact(value,keys)&&value.schemaVersion===2&&value.contract==='differential_queued_v0.1');
 need(['job_id','project_id','request_id'].every(k=>uuid(value[k]))&&value.project_id===route.project_id&&(!route.job_id||route.job_id===value.job_id)&&(!route.request_id||route.request_id===value.request_id));
 const request=differentialJobInput(new TextEncoder().encode(canonicalJSON({...value.selection,request_id:value.request_id})));
 // Prevent request_id inside selection from being hidden by object spread.
 need(exact(value.selection,['processed_dataset_version_id','sample_design_version_id','sample_design_request_id','contrast','replicate_model'])&&(!body||canonicalJSON(request)===canonicalJSON(body)));
 need(value.status==='queued'&&value.revision===1&&value.observation==='confirmed'&&['started_at','finished_at','attempt_id','runtime','result','error'].every(k=>value[k]===null));
 need(typeof value.created_at==='string'&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/.test(value.created_at)&&!value.created_at.startsWith('0000-')&&Number.isFinite(Date.parse(value.created_at))&&new Date(Date.parse(value.created_at)).toISOString()===value.created_at.slice(0,23)+'Z');
 need(jobHash(value.request_sha256)&&jobHash(value.binding_sha256)&&canonicalJSON(value.method)===canonicalJSON(DIFFERENTIAL_METHOD));
 const s=value.source;need(s&&s.schemaVersion===2&&s.analysis_ready===false&&s.project_id===route.project_id&&s.processed_dataset_version_id===value.selection.processed_dataset_version_id&&uuid(s.file_id)&&uuid(s.parent_dataset_version_id));
 need(value.selection.contrast?.dataset_version_id===s.parent_dataset_version_id&&value.selection.contrast?.quant_family===s.quant_family&&value.selection.contrast?.sample_design_version_id===value.selection.sample_design_version_id&&value.selection.replicate_model==='independent_biological');
 need(exact(value.input_manifest,['matrix','design','preset']));for(const p of Object.values(value.input_manifest))need(exact(p,['bytes','sha256'])&&Number.isSafeInteger(p.bytes)&&p.bytes>=1&&p.bytes<=26214400&&jobHash(p.sha256));
 need(new TextEncoder().encode(canonicalJSON(value)).length<=DIFFERENTIAL_JOB_MAX);return freeze(structuredClone(value));
}
// Frozen backend admission METHOD; runtime/publication are explicitly unregistered.
export const DIFFERENTIAL_METHOD=freeze({"R_version":"4.6.1","contract":"limma_core_v0.1","contrast_direction":"numerator_minus_denominator","imputation":"none","input_binder":{"bytes":13163,"sha256":"3a67e6c41b9ded6407562fe4b92a7ee6a163d751f80f3309ec44d8e8f0576fee"},"input_scale":"log2","library_files_sha256":"7ab763eb038759748400b1ff055f2d4ea2733fd81a4ded99de2169670f76ff56","limma_version":"3.68.5","native":{"bytes":14488,"sha256":"9c572a708d444e0ec91c59935bf9b5c3fd426a75fe2b7101b2a65f4783155283"},"normalization":"none","output_contract":{"bytes":10301,"sha256":"3534ef4ddf70bf1830abed33ccf05ad8273941343edf225f44006872d44a4047"},"publication_registered":false,"replicate_model":"independent_biological","runner":{"bytes":19031,"sha256":"14a330bd1c1c84879ed8f7ad90bb48debc72449e5475402b18db6b0411a6d7d5"},"runtime_registered":false,"script":{"bytes":12103,"sha256":"9807467ba2ae54efd5ef77e5c15dbe0525d205f4d2988ba24995ea932024c908"},"statmod_version":"1.5.2","supervisor":{"bytes":30207,"sha256":"6f7839b4a867cea8fd9aef63b684ac8c9dbf63dbc7a95bb289080e8aa8074a3e"}});
