import {exact,uuid,freeze,canonicalJSON} from './sample-design-contract.mjs';
import {DIFFERENTIAL_METHOD,differentialJobInput,jobHash} from './differential-job-contract.mjs';
export const DIFFERENTIAL_RESULT_NAMES=freeze(['R-session.txt','method-audit.tsv','results.tsv','sample-audit.tsv','test-set.tsv']);
export class DifferentialResultFailure extends Error{constructor(status=503,code='differential_results_unavailable'){super(code);this.status=status;this.code=code;}}
const need=ok=>{if(!ok)throw new DifferentialResultFailure();};
const fault=(status,code)=>{throw new DifferentialResultFailure(status,code);};
const stamp=v=>typeof v==='string'&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/.test(v)&&!v.startsWith('0000-')&&Number.isFinite(Date.parse(v))&&new Date(Date.parse(v)).toISOString()===v.slice(0,23)+'Z';
export function ownsDifferentialResultsRoute(raw){
 if(typeof raw!=='string')return false;const paths=[raw.split('?')[0]];try{paths.push(decodeURIComponent(raw.split('?')[0]),decodeURIComponent(new URL(raw,'http://127.0.0.1').pathname));}catch{}
 return paths.some(p=>/^\/api\/v1\/projects\/[^/]+\/differential-(?:runs|results)(?:\/|$)/.test(p.replace(/\/{2,}/g,'/')));
}
export function differentialResultRoute(raw,method){
 if(typeof raw!=='string'||raw.length>512||/[^\x21-\x7e]|[%+#]/.test(raw))fault(404,'not_found');
 const run=/^\/api\/v1\/projects\/([0-9a-f-]{36})\/differential-runs\/([0-9a-f-]{36})$/.exec(raw),lookup=/^\/api\/v1\/projects\/([0-9a-f-]{36})\/differential-results\?job_id=([0-9a-f-]{36})$/.exec(raw),result=/^\/api\/v1\/projects\/([0-9a-f-]{36})\/differential-results\/([0-9a-f-]{36})(?:\/artifacts\/([A-Za-z0-9_.-]{1,64}))?$/.exec(raw),m=run||lookup||result;
 if(!m||!uuid(m[1])||!uuid(m[2]))fault(404,'not_found');if(method!=='GET')fault(405,'method_not_allowed');
 if(result?.[3]&&!DIFFERENTIAL_RESULT_NAMES.includes(result[3]))fault(404,'differential_artifact_not_found');
 return freeze({path:raw,method,kind:run?'differential-run':lookup?'differential-result-lookup':result[3]?'artifact':'differential-result',project_id:m[1],job_id:run||lookup?m[2]:null,result_id:result?.[2]||null,name:result?.[3]||null});
}
const errors=new Map([[400,['invalid_differential_result_request','invalid_project_query']],[401,['unauthenticated']],[403,['auth_origin_rejected']],
 [404,['not_found','differential_job_not_found','differential_run_not_found','differential_result_not_found','differential_artifact_not_found','project_not_found']],
 [405,['method_not_allowed']],[409,['project_context_changed']],[415,['unsupported_media_type']],[503,['differential_results_unavailable']],[504,['differential_timeout','differential_results_timeout']]]);
export function differentialResultError(status,value,{upstream=false}={}){
 if(upstream&&status===503)return{error:{code:'differential_results_unavailable'}};
 need(exact(value,['error'])&&exact(value.error,['code'])&&errors.get(status)?.includes(value.error.code)&&!(upstream&&status===409));return value;
}
const runKeys=['schemaVersion','contract','job_id','attempt_id','registration_id','project_id','status','observation','revision','started_at','finished_at','result_id','error_code'];
const resultKeys=['schemaVersion','contract','result_id','job_id','attempt_id','registration_id','project_id','created_by','created_at','request_id','admission_binding_sha256','selection','method','input_manifest','source_analysis_ready','exit_code','scientific_status','warning_count','review_required','artifacts','manifest_sha256','receipt_sha256','stored'];
const pin=p=>exact(p,['bytes','sha256'])&&Number.isSafeInteger(p.bytes)&&p.bytes>=1&&p.bytes<=26214400&&jobHash(p.sha256);
export function differentialResultReply(route,status,value,ownerId=null){
 if(status!==200)return differentialResultError(status,value,{upstream:true});
 if(route.kind==='differential-run'){
  need(exact(value,runKeys)&&value.schemaVersion===1&&value.contract==='differential_run_v0.1'&&['job_id','attempt_id','registration_id','project_id'].every(k=>uuid(value[k]))&&value.job_id===route.job_id&&value.project_id===route.project_id&&['confirmed','reconciliation_pending'].includes(value.observation)&&stamp(value.started_at));
  if(value.status==='running')need(value.revision===2&&value.finished_at===null&&value.result_id===null&&value.error_code===null);
  else{need(['succeeded','failed'].includes(value.status)&&value.revision===3&&stamp(value.finished_at)&&value.finished_at>=value.started_at);if(value.status==='succeeded')need(uuid(value.result_id)&&value.error_code===null);else need(value.result_id===null&&['input_integrity_failed','runner_failed'].includes(value.error_code));}
 }else{
  need(['differential-result','differential-result-lookup'].includes(route.kind)&&exact(value,resultKeys)&&value.schemaVersion===1&&value.contract==='differential_result_v0.1'&&['result_id','job_id','attempt_id','registration_id','project_id','created_by','request_id'].every(k=>uuid(value[k]))&&value.project_id===route.project_id&&(!route.job_id||value.job_id===route.job_id)&&(!route.result_id||value.result_id===route.result_id)&&(!ownerId||value.created_by===ownerId)&&stamp(value.created_at));
  differentialJobInput(new TextEncoder().encode(canonicalJSON({...value.selection,request_id:value.request_id})));need(exact(value.selection,['processed_dataset_version_id','sample_design_version_id','sample_design_request_id','contrast','replicate_model'])&&canonicalJSON(value.method)===canonicalJSON(DIFFERENTIAL_METHOD)&&jobHash(value.admission_binding_sha256)&&exact(value.input_manifest,['matrix','design','preset'])&&Object.values(value.input_manifest).every(pin)&&value.source_analysis_ready===false&&value.stored===true);
  need([0,3,4].includes(value.exit_code)&&value.scientific_status===({0:'COMPUTED_CANDIDATE',3:'NO_TESTABLE_ROWS',4:'REVIEW_REQUIRED_LIMMA_WARNING'})[value.exit_code]&&Number.isSafeInteger(value.warning_count)&&value.warning_count>=0&&value.review_required===(value.warning_count>0||value.exit_code===4)&&jobHash(value.manifest_sha256)&&jobHash(value.receipt_sha256));
  need(Array.isArray(value.artifacts)&&value.artifacts.length===5);let total=0;for(const [i,p] of value.artifacts.entries()){need(exact(p,['name','bytes','sha256'])&&p.name===DIFFERENTIAL_RESULT_NAMES[i]&&pin({bytes:p.bytes,sha256:p.sha256}));total+=p.bytes;}need(total<=134217728);
 }
 return freeze(structuredClone(value));
}
export function joinDifferentialResult(job,run,meta){
 need(run.status==='succeeded'&&run.observation==='confirmed'&&job.job_id===run.job_id&&job.project_id===run.project_id&&meta.job_id===job.job_id&&meta.project_id===job.project_id&&meta.request_id===job.request_id&&meta.created_by===job.source.created_by&&meta.admission_binding_sha256===job.binding_sha256&&meta.attempt_id===run.attempt_id&&meta.registration_id===run.registration_id&&meta.result_id===run.result_id&&meta.created_at>=run.started_at&&run.started_at>=job.created_at);
 for(const key of ['selection','method','input_manifest'])need(canonicalJSON(meta[key])===canonicalJSON(job[key]));return true;
}
