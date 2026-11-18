import {exact,uuid,freeze,canonicalJSON} from './sample-design-contract.mjs';
import {DifferentialResultFailure} from './differential-result-contract.mjs';
import {DE_CLASSIFICATION_ARTIFACTS} from './de-classification-summary.js';
export const DE_FILE_MAX=27262976,DE_TOTAL_MAX=134217728;
export class DEClassificationReadFailure extends DifferentialResultFailure{constructor(status=503,code='de_classification_results_unavailable'){super(status,code);}}
const fault=(status,code)=>{throw new DEClassificationReadFailure(status,code);};
const need=v=>{if(!v)fault(503,'de_classification_results_unavailable');};
const hash=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v);
const count=v=>Number.isSafeInteger(v)&&v>=0&&v<=10000;
export function ownsDEClassificationReadRoute(raw){if(typeof raw!=='string')return false;const paths=[raw.split('?')[0]];try{paths.push(decodeURIComponent(paths[0]),decodeURIComponent(new URL(raw,'http://127.0.0.1').pathname));}catch{}return paths.some(p=>/^\/api\/v1\/projects\/[^/]+\/differential-classifications(?:\/|$)/.test(p.replace(/\/{2,}/g,'/')));}
export function deClassificationReadRoute(raw,method){
 if(typeof raw!=='string'||raw.length>512||/[^\x21-\x7e]|[%+#]/.test(raw))fault(404,'not_found');
 const lookup=/^\/api\/v1\/projects\/([0-9a-f-]{36})\/differential-classifications\?request_id=([0-9a-f-]{36})$/.exec(raw),version=/^\/api\/v1\/projects\/([0-9a-f-]{36})\/differential-classifications\/([0-9a-f-]{36})(?:\/files\/([A-Za-z0-9_.-]{1,64}))?$/.exec(raw),m=lookup||version;
 if(!m||!uuid(m[1])||!uuid(m[2]))fault(404,'not_found');if(method!=='GET')fault(405,'method_not_allowed');
 if(version?.[3]&&!DE_CLASSIFICATION_ARTIFACTS.includes(version[3]))fault(404,'de_classification_artifact_not_found');
 return freeze({path:raw,method,kind:lookup?'classification-lookup':version[3]?'artifact':'classification-version',project_id:m[1],request_id:lookup?m[2]:null,derived_version_id:version?.[2]||null,name:version?.[3]||null});
}
const errors=new Map([[400,['invalid_de_classification_request']],[401,['unauthenticated']],[403,['auth_origin_rejected']],[404,['not_found','de_classification_version_not_found','de_classification_source_not_found','de_classification_artifact_not_found']],[405,['method_not_allowed']],[409,['project_context_changed']],[503,['de_classification_results_unavailable','de_classification_persisted_invalid','de_parent_binding_mismatch']],[504,['de_classification_read_timeout']]]);
export function deClassificationReadError(status,value,{upstream=false}={}){if(upstream&&status===503&&(!exact(value,['error'])||!exact(value.error,['code'])||!errors.get(status).includes(value.error.code)))return{error:{code:'de_classification_results_unavailable'}};need(exact(value,['error'])&&exact(value.error,['code'])&&errors.get(status)?.includes(value.error.code));return value;}
const keys=('schemaVersion contract derived_version_id request_id project_id source_result_id source_manifest_sha256 source_binding_sha256 source_binding parameters parameters_sha256 core status DE_members_evaluated counts artifacts manifest_sha256 created_at created_by source_analysis_ready member_set').split(' ');
const bindingKeys=('schemaVersion contract owner_id project_id source_result_id source_job_id source_attempt_id source_registration_id source_request_id source_contrast_id admission_binding_sha256 input_snapshot_sha256 source_metadata_sha256 source_receipt_sha256 source_manifest_sha256 source_artifacts source_status source_warning_count source_method_sha256 contrast_direction').split(' ');
export function deClassificationReadReply(route,status,value,ownerId=null){
 if(status!==200)return deClassificationReadError(status,value,{upstream:true});
 need(route.kind!=='artifact'&&exact(value,keys)&&value.schemaVersion===1&&value.contract==='de_classification_version_v0.1');
 for(const k of ['derived_version_id','request_id','project_id','source_result_id','created_by'])need(uuid(value[k]));
 need(value.project_id===route.project_id&&(!route.request_id||value.request_id===route.request_id)&&(!route.derived_version_id||value.derived_version_id===route.derived_version_id)&&(!ownerId||value.created_by===ownerId)&&value.source_analysis_ready===false);
 for(const k of ['source_manifest_sha256','source_binding_sha256','parameters_sha256','manifest_sha256'])need(hash(value[k]));
 need(typeof value.created_at==='string'&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/.test(value.created_at)&&Number.isFinite(Date.parse(value.created_at)));
 const p=value.parameters;need(exact(p,['schema_version','input_scale','adjusted_p_max','absolute_log2fc_min'])&&p.schema_version==='de_classification_v0.1'&&p.input_scale==='log2'&&['adjusted_p_max','absolute_log2fc_min'].every(k=>typeof p[k]==='string'&&p[k].length>=1&&p[k].length<=128));
 const core=value.core;need(exact(core,['contract','script_pin','validator_pin'])&&core.contract==='de_classification_v0.1');for(const pin of [core.script_pin,core.validator_pin])need(exact(pin,['bytes','sha256'])&&Number.isSafeInteger(pin.bytes)&&pin.bytes>0&&pin.bytes<=262144&&hash(pin.sha256));
 const b=value.source_binding;need(exact(b,bindingKeys)&&b.schemaVersion===1&&b.contract==='de_parent_binding_v0.1'&&b.owner_id===value.created_by&&b.project_id===value.project_id&&b.source_result_id===value.source_result_id&&b.source_manifest_sha256===value.source_manifest_sha256&&b.contrast_direction==='numerator_minus_denominator');
 for(const k of ['source_job_id','source_attempt_id','source_registration_id','source_request_id'])need(uuid(b[k]));for(const k of ['admission_binding_sha256','input_snapshot_sha256','source_metadata_sha256','source_receipt_sha256','source_manifest_sha256','source_method_sha256'])need(hash(b[k]));
 need(typeof b.source_contrast_id==='string'&&/^[A-Za-z0-9_-]{1,64}$/.test(b.source_contrast_id)&&Number.isSafeInteger(b.source_warning_count)&&b.source_warning_count>=0&&Array.isArray(b.source_artifacts)&&b.source_artifacts.length===5);
 const evaluated=value.status==='CLASSIFIED';need(['CLASSIFIED','BLOCKED_SOURCE_NO_TESTABLE_ROWS','BLOCKED_SOURCE_REVIEW_REQUIRED'].includes(value.status)&&value.DE_members_evaluated===evaluated&&exact(value.counts,['Up','Down','NS','not_testable','not_evaluated','selected_DE_rows']));
 for(const [k,v] of Object.entries(value.counts))need(!evaluated&&['Up','Down','NS','selected_DE_rows'].includes(k)?v===null:count(v));
 let total=0;need(Array.isArray(value.artifacts)&&value.artifacts.length===6);for(const [i,pin] of value.artifacts.entries()){need(exact(pin,['name','bytes','sha256'])&&pin.name===DE_CLASSIFICATION_ARTIFACTS[i]&&Number.isSafeInteger(pin.bytes)&&pin.bytes>=1&&pin.bytes<=DE_FILE_MAX&&hash(pin.sha256));total+=pin.bytes;}need(total<=DE_TOTAL_MAX);
 if(evaluated){const s=value.member_set;need(exact(s,['member_set_id','derived_version_id','selected_artifact_sha256','member_count'])&&uuid(s.member_set_id)&&s.derived_version_id===value.derived_version_id&&s.selected_artifact_sha256===value.artifacts.find(p=>p.name==='selected-DE.tsv').sha256&&s.member_count===value.counts.selected_DE_rows);}else need(value.member_set===null);
 need(new TextEncoder().encode(canonicalJSON(value)).length<=32768);return freeze(structuredClone(value));
}
