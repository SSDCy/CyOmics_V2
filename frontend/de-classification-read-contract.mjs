import {exact,uuid,freeze,canonicalJSON} from './sample-design-contract.mjs';
import {DifferentialResultFailure} from './differential-result-contract.mjs';
import {DE_CLASSIFICATION_ARTIFACTS} from './de-classification-summary.js';
import {parsePreprocessJSON} from './preprocess-owner-wire.mjs';
export const DE_FILE_MAX=27262976,DE_TOTAL_MAX=134217728;
export const PLOT_REPRO_MAX=33554432;
export const isPlotReproRoute=r=>['volcano-reproducibility','heatmap-reproducibility'].includes(r?.kind);
export function plotReproFilename(plot,derived){if(!['volcano','heatmap'].includes(plot)||!uuid(derived))throw new DEClassificationReadFailure();return 'cyomics_'+plot+'_'+derived+'_reproducibility.zip';}
export function plotReproError(route,status,value,{upstream=false}={}){
 if(!isPlotReproRoute(route))throw new DEClassificationReadFailure();const plot=route.kind.split('-')[0];
 const allowed=new Map([[400,['invalid_r_'+plot+'_request']],[401,['unauthenticated']],[403,['auth_origin_rejected']],[404,['not_found']],[405,['method_not_allowed']],[413,['r_'+plot+'_reproducibility_too_large']],[503,['r_'+plot+'_unavailable']],[504,['r_'+plot+'_read_timeout']]]);
 if(!upstream&&status===409&&exact(value,['error'])&&exact(value.error,['code'])&&value.error.code==='project_context_changed')return value;
 if(!exact(value,['error'])||!exact(value.error,['code'])||!allowed.get(status)?.includes(value.error.code))throw new DEClassificationReadFailure(503,'r_'+plot+'_unavailable');return value;
}
export const VOLCANO_MEDIA=freeze({'R-session.txt':'text/plain;charset=utf-8','volcano-audit.tsv':'text/tab-separated-values;charset=utf-8','volcano-points.tsv':'text/tab-separated-values;charset=utf-8','volcano.png':'image/png','volcano.svg':'image/svg+xml'});
export const VOLCANO_ARTIFACTS=freeze(Object.keys(VOLCANO_MEDIA));
export const HEATMAP_MEDIA=freeze({'R-session.txt':'text/plain;charset=utf-8','heatmap-audit.tsv':'text/tab-separated-values;charset=utf-8','heatmap-matrix.tsv':'text/tab-separated-values;charset=utf-8','heatmap-row-map.tsv':'text/tab-separated-values;charset=utf-8','heatmap-samples.tsv':'text/tab-separated-values;charset=utf-8','heatmap-zscore.tsv':'text/tab-separated-values;charset=utf-8','heatmap.png':'image/png','heatmap.svg':'image/svg+xml'});
export const HEATMAP_ARTIFACTS=freeze(Object.keys(HEATMAP_MEDIA));
// Generation and status share one strict implementation; plot families stay isolated.
export class VolcanoRenderFailure extends DifferentialResultFailure{constructor(status=503,code='r_volcano_unavailable'){super(status,code);}}
export class HeatmapRenderFailure extends DifferentialResultFailure{constructor(status=503,code='r_heatmap_unavailable'){super(status,code);}}
function createPlotRenderContract(plot,Failure){
const renderFault=(status,code)=>{throw new Failure(status,code);};
const renderNeed=v=>{if(!v)renderFault(503,'r_'+plot+'_unavailable');};
function owns(raw){if(typeof raw!=='string')return false;const paths=[raw.split('?')[0]];try{paths.push(decodeURIComponent(paths[0]),decodeURIComponent(new URL(raw,'http://127.0.0.1').pathname));}catch{}return paths.some(p=>/^\/api\/v1\/projects\/[^/]+\/derived-classifications\/[^/]+\/(volcano|heatmap)\/render(?:\/|$)/.exec(p.replace(/\/{2,}/g,'/'))?.[1]===plot);}
function route(raw,method){
 if(typeof raw!=='string'||raw.length>512||/[^\x21-\x7e]|[%+#]/.test(raw))renderFault(404,'not_found');
 const m=/^\/api\/v1\/projects\/([0-9a-f-]{36})\/derived-classifications\/([0-9a-f-]{36})\/(volcano|heatmap)\/render(?:\?request_id=([0-9a-f-]{36}))?$/.exec(raw);
 if(!m||!uuid(m[1])||!uuid(m[2])||m[3]!==plot||m[4]&&!uuid(m[4]))renderFault(404,'not_found');
 if(!['POST','GET'].includes(method))renderFault(405,'method_not_allowed');
 if(method==='POST'&&m[4]||method==='GET'&&!m[4])renderFault(400,'invalid_r_'+plot+'_request');
 return freeze({path:raw,method,kind:method==='POST'?plot+'-render-submit':plot+'-render-status',project_id:m[1],derived_version_id:m[2],request_id:m[4]||null});
}
function input(raw){try{if(!(raw instanceof Uint8Array)||raw.length<1||raw.length>128)throw Error();const v=parsePreprocessJSON(raw);if(!exact(v,['request_id'])||!uuid(v.request_id))throw Error();return freeze(structuredClone(v));}catch{renderFault(400,'invalid_r_'+plot+'_request');}}
const renderErrors=new Map([[400,['invalid_r_'+plot+'_request']],[401,['unauthenticated']],[403,['auth_origin_rejected']],[404,['not_found']],[405,['method_not_allowed']],[409,['r_'+plot+'_capacity_busy']],[503,['r_'+plot+'_unavailable']],[504,['r_'+plot+'_submission_unknown','r_'+plot+'_read_timeout']]]);
function error(status,value,{upstream=false}={}){
 if(!upstream&&status===409&&exact(value,['error'])&&exact(value.error,['code'])&&value.error.code==='project_context_changed')return value; // Existing local nonce fence, never an upstream render DTO.
 renderNeed(exact(value,['error'])&&exact(value.error,['code'])&&renderErrors.get(status)?.includes(value.error.code));return value;
}
function reply(route,status,v,request=null,ownerId=null){
 if(![200,201].includes(status))return error(status,v,{upstream:true});
 renderNeed((route.method==='POST'||status===200)&&exact(v,['schemaVersion','contract','project_id','request_id','derived_version_id','source_result_id','render_id','binding_sha256','state','observation','error_code','descriptor'])&&v.schemaVersion===1&&v.contract==='r_'+plot+'_render_v0.1');
 renderNeed(['project_id','request_id','derived_version_id','source_result_id','render_id'].every(k=>uuid(v[k]))&&hash(v.binding_sha256)&&v.project_id===route.project_id&&v.derived_version_id===route.derived_version_id&&v.request_id===(request?.request_id||route.request_id));
 renderNeed(['queued','running','ready','failed','unknown'].includes(v.state)&&v.observation===(v.state==='unknown'?'unknown':'confirmed')&&v.error_code===(v.state==='unknown'?'r_'+plot+'_submission_unknown':v.state==='failed'?'r_'+plot+'_render_failed':null));
 if(v.state==='ready'){
  const read=deClassificationReadRoute('/api/v1/projects/'+route.project_id+'/derived-classifications/'+route.derived_version_id+'/'+plot,'GET');
  const d=deClassificationReadReply(read,200,v.descriptor,ownerId);renderNeed(d.source_result_id===v.source_result_id);
 }else renderNeed(v.descriptor===null);
 renderNeed(new TextEncoder().encode(canonicalJSON(v)).length<=32768);return freeze(structuredClone(v));
}
 return freeze({plot,Failure,owns,route,input,error,reply});
}
const volcanoRenderContract=createPlotRenderContract('volcano',VolcanoRenderFailure),heatmapRenderContract=createPlotRenderContract('heatmap',HeatmapRenderFailure);
export const {owns:ownsVolcanoRenderRoute,route:volcanoRenderRoute,input:volcanoRenderInput,error:volcanoRenderError,reply:volcanoRenderReply}=volcanoRenderContract;
export const {owns:ownsHeatmapRenderRoute,route:heatmapRenderRoute,input:heatmapRenderInput,error:heatmapRenderError,reply:heatmapRenderReply}=heatmapRenderContract;
export function getPlotRenderContract(plot){if(plot==='volcano')return volcanoRenderContract;if(plot==='heatmap')return heatmapRenderContract;throw Error('invalid_plot_family');}
export class DEClassificationReadFailure extends DifferentialResultFailure{constructor(status=503,code='de_classification_results_unavailable'){super(status,code);}}
const fault=(status,code)=>{throw new DEClassificationReadFailure(status,code);};
const need=v=>{if(!v)fault(503,'de_classification_results_unavailable');};
const hash=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v);
const count=v=>Number.isSafeInteger(v)&&v>=0&&v<=10000;
export function ownsDEClassificationReadRoute(raw){if(typeof raw!=='string')return false;const paths=[raw.split('?')[0]];try{paths.push(decodeURIComponent(paths[0]),decodeURIComponent(new URL(raw,'http://127.0.0.1').pathname));}catch{}return paths.some(p=>/^\/api\/v1\/projects\/[^/]+\/(?:differential-classifications(?:\/|$)|derived-classifications\/[^/]+\/(?:volcano|heatmap)(?:\/|$))/.test(p.replace(/\/{2,}/g,'/')));}
export function deClassificationReadRoute(raw,method){
 if(typeof raw!=='string'||raw.length>512||/[^\x21-\x7e]|[%+#]/.test(raw))fault(404,'not_found');
 const repro=/^\/api\/v1\/projects\/([0-9a-f-]{36})\/derived-classifications\/([0-9a-f-]{36})\/(volcano|heatmap)\/reproducibility(\?.*)?$/.exec(raw);
 if(repro){if(!uuid(repro[1])||!uuid(repro[2]))fault(404,'not_found');if(method!=='GET')fault(405,'method_not_allowed');if(repro[4])fault(400,'invalid_r_'+repro[3]+'_request');return freeze({path:raw,method,kind:repro[3]+'-reproducibility',project_id:repro[1],derived_version_id:repro[2],name:plotReproFilename(repro[3],repro[2])});}
 const volcano=/^\/api\/v1\/projects\/([0-9a-f-]{36})\/derived-classifications\/([0-9a-f-]{36})\/volcano(?:\/artifacts\/([A-Za-z0-9_.-]{1,64}))?$/.exec(raw);
 if(volcano){if(!uuid(volcano[1])||!uuid(volcano[2])||volcano[3]&&!VOLCANO_ARTIFACTS.includes(volcano[3]))fault(404,'not_found');if(method!=='GET')fault(405,'method_not_allowed');return freeze({path:raw,method,kind:volcano[3]?'volcano-artifact':'volcano-metadata',project_id:volcano[1],derived_version_id:volcano[2],name:volcano[3]||null});}
 const heatmap=/^\/api\/v1\/projects\/([0-9a-f-]{36})\/derived-classifications\/([0-9a-f-]{36})\/heatmap(?:\/artifacts\/([A-Za-z0-9_.-]{1,64}))?$/.exec(raw);
 if(heatmap){if(!uuid(heatmap[1])||!uuid(heatmap[2])||heatmap[3]&&!HEATMAP_ARTIFACTS.includes(heatmap[3]))fault(404,'not_found');if(method!=='GET')fault(405,'method_not_allowed');return freeze({path:raw,method,kind:heatmap[3]?'heatmap-artifact':'heatmap-metadata',project_id:heatmap[1],derived_version_id:heatmap[2],name:heatmap[3]||null});}
 const lookup=/^\/api\/v1\/projects\/([0-9a-f-]{36})\/differential-classifications\?request_id=([0-9a-f-]{36})$/.exec(raw),version=/^\/api\/v1\/projects\/([0-9a-f-]{36})\/differential-classifications\/([0-9a-f-]{36})(?:\/files\/([A-Za-z0-9_.-]{1,64}))?$/.exec(raw),m=lookup||version;
 if(!m||!uuid(m[1])||!uuid(m[2]))fault(404,'not_found');if(method!=='GET')fault(405,'method_not_allowed');
 if(version?.[3]&&!DE_CLASSIFICATION_ARTIFACTS.includes(version[3]))fault(404,'de_classification_artifact_not_found');
 return freeze({path:raw,method,kind:lookup?'classification-lookup':version[3]?'artifact':'classification-version',project_id:m[1],request_id:lookup?m[2]:null,derived_version_id:version?.[2]||null,name:version?.[3]||null});
}
const errors=new Map([[400,['invalid_de_classification_request','invalid_r_volcano_request','invalid_r_heatmap_request']],[401,['unauthenticated']],[403,['auth_origin_rejected']],[404,['not_found','de_classification_version_not_found','de_classification_source_not_found','de_classification_artifact_not_found']],[405,['method_not_allowed']],[409,['project_context_changed']],[503,['r_volcano_unavailable','r_heatmap_unavailable','de_classification_results_unavailable','de_classification_persisted_invalid','de_parent_binding_mismatch']],[504,['de_classification_read_timeout','r_volcano_read_timeout','r_heatmap_read_timeout']]]);
export function deClassificationReadError(status,value,{upstream=false,route=null}={}){if(isPlotReproRoute(route))return plotReproError(route,status,value,{upstream});if(upstream&&status===503&&(!exact(value,['error'])||!exact(value.error,['code'])||!errors.get(status).includes(value.error.code)))return{error:{code:'de_classification_results_unavailable'}};need(exact(value,['error'])&&exact(value.error,['code'])&&errors.get(status)?.includes(value.error.code));return value;}
const keys=('schemaVersion contract derived_version_id request_id project_id source_result_id source_manifest_sha256 source_binding_sha256 source_binding parameters parameters_sha256 core status DE_members_evaluated counts artifacts manifest_sha256 created_at created_by source_analysis_ready member_set').split(' ');
const bindingKeys=('schemaVersion contract owner_id project_id source_result_id source_job_id source_attempt_id source_registration_id source_request_id source_contrast_id admission_binding_sha256 input_snapshot_sha256 source_metadata_sha256 source_receipt_sha256 source_manifest_sha256 source_artifacts source_status source_warning_count source_method_sha256 contrast_direction').split(' ');
export function deClassificationReadReply(route,status,value,ownerId=null){
 if(status!==200)return deClassificationReadError(status,value,{upstream:true,route});
 if(route.kind==='volcano-metadata'||route.kind==='heatmap-metadata'){
  const heatmap=route.kind==='heatmap-metadata',names=heatmap?HEATMAP_ARTIFACTS:VOLCANO_ARTIFACTS,media=heatmap?HEATMAP_MEDIA:VOLCANO_MEDIA;
  need(exact(value,['schemaVersion','contract','project_id','created_by','source_result_id','derived_version_id','source_metadata_sha256','source_manifest_sha256','derived_metadata_sha256','derived_manifest_sha256','source_binding_sha256','parameters_sha256','artifacts'])&&value.schemaVersion===1&&value.contract===(heatmap?'r_heatmap_view_v0.1':'r_volcano_view_v0.1'));
  need(['project_id','created_by','source_result_id','derived_version_id'].every(k=>uuid(value[k]))&&value.project_id===route.project_id&&value.derived_version_id===route.derived_version_id&&(!ownerId||value.created_by===ownerId));
  need(['source_metadata_sha256','source_manifest_sha256','derived_metadata_sha256','derived_manifest_sha256','source_binding_sha256','parameters_sha256'].every(k=>hash(value[k]))&&Array.isArray(value.artifacts)&&value.artifacts.length===names.length);let total=0;
  value.artifacts.forEach((p,i)=>{need(exact(p,['name','bytes','sha256','media_type'])&&p.name===names[i]&&p.media_type===media[p.name]&&Number.isSafeInteger(p.bytes)&&p.bytes>=1&&p.bytes<=DE_FILE_MAX&&hash(p.sha256));total+=p.bytes;});need(total<=DE_TOTAL_MAX&&new TextEncoder().encode(canonicalJSON(value)).length<=32768);return freeze(structuredClone(value));
 }
 need(route.kind!=='artifact'&&exact(value,keys)&&value.schemaVersion===1&&value.contract==='de_classification_version_v0.1');
 for(const k of ['derived_version_id','request_id','project_id','source_result_id','created_by'])need(uuid(value[k]));
 need(value.project_id===route.project_id&&(!route.request_id||value.request_id===route.request_id)&&(!route.derived_version_id||value.derived_version_id===route.derived_version_id)&&(!ownerId||value.created_by===ownerId)&&value.source_analysis_ready===false);
 for(const k of ['source_manifest_sha256','source_binding_sha256','parameters_sha256','manifest_sha256'])need(hash(value[k]));
 need(typeof value.created_at==='string'&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/.test(value.created_at)&&Number.isFinite(Date.parse(value.created_at)));
 const p=value.parameters;need(exact(p,['schema_version','input_scale','adjusted_p_max','absolute_log2fc_min'])&&p.schema_version==='de_classification_v0.1'&&p.input_scale==='log2'&&['adjusted_p_max','absolute_log2fc_min'].every(k=>typeof p[k]==='string'&&p[k].length>=1&&p[k].length<=4096));
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

// Admission is immutable queued history, never an execution/completion DTO.
export const CLASSIFICATION_CORE=freeze({contract:'de_classification_v0.1',script_pin:{bytes:10335,sha256:'65e24d287a1546dd7a9fa0480f2a899d4d91eb4b11cda7a4802256fb90f3c421'},validator_pin:{bytes:12074,sha256:'2d37d97fcbbe6bb5c323c887b8c168e68c19945904dd3fe5bc9b247174a5d584'}});
export class ClassificationJobFailure extends DifferentialResultFailure{constructor(status=503,code='de_classification_jobs_unavailable'){super(status,code);}}
const jobFault=(status,code)=>{throw new ClassificationJobFailure(status,code);};
const jobNeed=v=>{if(!v)jobFault(503,'de_classification_jobs_unavailable');};
const parameterKeys=['schema_version','input_scale','adjusted_p_max','absolute_log2fc_min'];
const numericToken=v=>typeof v==='string'&&/^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$/.test(v)&&Number.isFinite(Number(v));
export function classificationParameters(value){
 if(!exact(value,parameterKeys)||value.schema_version!=='de_classification_v0.1'||value.input_scale!=='log2'||!numericToken(value.adjusted_p_max)||!(Number(value.adjusted_p_max)>0&&Number(value.adjusted_p_max)<=1)||!numericToken(value.absolute_log2fc_min)||Number(value.absolute_log2fc_min)<0)jobFault(400,'invalid_de_classification_request');
 const text='key\tvalue\n'+parameterKeys.map(k=>k+'\t'+value[k]+'\n').join('');if(new TextEncoder().encode(text).length>4096)jobFault(400,'invalid_de_classification_request');return freeze(structuredClone(value));
}
export function classificationParametersBytes(value){const p=classificationParameters(value);return new TextEncoder().encode('key\tvalue\n'+parameterKeys.map(k=>k+'\t'+p[k]+'\n').join(''));}
export function classificationJobInput(raw){
 try{if(!(raw instanceof Uint8Array)||raw.length<1||raw.length>8192)throw Error();const value=parsePreprocessJSON(raw);if(!exact(value,['request_id','source_result_id','parameters'])||!uuid(value.request_id)||!uuid(value.source_result_id))throw Error();classificationParameters(value.parameters);return freeze(structuredClone(value));}catch{jobFault(400,'invalid_de_classification_request');}
}
export function ownsClassificationJobRoute(raw){if(typeof raw!=='string')return false;const paths=[raw.split('?')[0]];try{paths.push(decodeURIComponent(paths[0]),decodeURIComponent(new URL(raw,'http://127.0.0.1').pathname));}catch{}return paths.some(p=>/^\/api\/v1\/projects\/[^/]+\/classification-jobs(?:\/|$)/.test(p.replace(/\/{2,}/g,'/')));}
export function classificationJobRoute(raw,method){
 if(typeof raw!=='string'||raw.length>256||/[^\x21-\x7e]|[%+#]/.test(raw))jobFault(404,'not_found');
 const history=/^\/api\/v1\/projects\/([0-9a-f-]{36})\/classification-jobs\?source_result_id=([0-9a-f-]{36})&view=history$/.exec(raw);
 if(history){if(!uuid(history[1])||!uuid(history[2]))jobFault(404,'not_found');if(method!=='GET')jobFault(405,'method_not_allowed');return freeze({path:raw,method,kind:'classification-history',project_id:history[1],source_result_id:history[2]});}
 const run=/^\/api\/v1\/projects\/([0-9a-f-]{36})\/classification-jobs\?request_id=([0-9a-f-]{36})&view=run$/.exec(raw);
 if(run){if(!uuid(run[1])||!uuid(run[2]))jobFault(404,'not_found');if(method!=='GET')jobFault(405,'method_not_allowed');return freeze({path:raw,method,kind:'classification-run-lookup',project_id:run[1],request_id:run[2]});}
 const match=/^\/api\/v1\/projects\/([0-9a-f-]{36})\/classification-jobs(?:\?request_id=([0-9a-f-]{36}))?$/.exec(raw);
 if(!match||!uuid(match[1])||match[2]&&!uuid(match[2]))jobFault(404,'not_found');
 if(method!==(match[2]?'GET':'POST'))jobFault(405,'method_not_allowed');
 return freeze({path:raw,method,kind:match[2]?'classification-job-lookup':'classification-job-submit',project_id:match[1],request_id:match[2]||null});
}
const jobErrors=new Map([[400,['invalid_de_classification_request']],[401,['unauthenticated']],[403,['auth_origin_rejected']],[404,['not_found','de_classification_source_not_found','de_classification_job_not_found']],[405,['method_not_allowed']],[409,['de_classification_request_conflict','de_classification_capacity_busy','project_context_changed']],[503,['de_classification_jobs_unavailable','de_classification_persisted_invalid','de_parent_binding_mismatch']],[504,['de_classification_submission_unknown','de_classification_read_timeout']]]);
export function classificationJobError(status,value,{upstream=false}={}){if(upstream&&status===503&&(!exact(value,['error'])||!exact(value.error,['code'])||!jobErrors.get(status).includes(value.error.code)))return{error:{code:'de_classification_jobs_unavailable'}};jobNeed(exact(value,['error'])&&exact(value.error,['code'])&&jobErrors.get(status)?.includes(value.error.code));return value;}
const queuedKeys=('schemaVersion contract classification_job_id project_id request_id created_by source_result_id source_manifest_sha256 source_binding_sha256 parameters parameters_sha256 core created_at status observation').split(' ');
const runKeys=('schemaVersion contract classification_job_id project_id request_id created_by source_result_id source_manifest_sha256 source_binding_sha256 parameters_sha256 created_at state observation attempt_id claimed_at finished_at error_code derived_version_id scientific_status').split(' ');
const runStamp=v=>typeof v==='string'&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/.test(v)&&!v.startsWith('0000-')&&Number.isFinite(Date.parse(v))&&new Date(Date.parse(v)).toISOString()===v.slice(0,23)+'Z';
function classificationRunReply(route,status,v,ownerId){
 if(status!==200)return classificationJobError(status,v,{upstream:true});
 jobNeed(route.method==='GET'&&exact(v,runKeys)&&v.schemaVersion===1&&v.contract==='de_classification_run_v0.1'&&v.observation==='confirmed');
 jobNeed(['classification_job_id','project_id','request_id','created_by','source_result_id'].every(k=>uuid(v[k]))&&v.project_id===route.project_id&&v.request_id===route.request_id&&(!ownerId||v.created_by===ownerId));
 jobNeed(['source_manifest_sha256','source_binding_sha256','parameters_sha256'].every(k=>hash(v[k]))&&runStamp(v.created_at));
 const attempted=uuid(v.attempt_id)&&runStamp(v.claimed_at),result=uuid(v.derived_version_id)&&['CLASSIFIED','BLOCKED_SOURCE_NO_TESTABLE_ROWS','BLOCKED_SOURCE_REVIEW_REQUIRED'].includes(v.scientific_status),noResult=v.derived_version_id===null&&v.scientific_status===null;
 if(v.state==='queued')jobNeed(['attempt_id','claimed_at','finished_at','error_code','derived_version_id','scientific_status'].every(k=>v[k]===null));
 else if(v.state==='claimed')jobNeed(attempted&&['finished_at','error_code','derived_version_id','scientific_status'].every(k=>v[k]===null));
 else if(v.state==='reconciliation_pending')jobNeed(attempted&&result&&v.finished_at===null&&v.error_code===null);
 else if(v.state==='finished')jobNeed(runStamp(v.finished_at)&&(attempted&&v.finished_at>=v.claimed_at&&(noResult&&['input_integrity_failed','runner_failed'].includes(v.error_code)||result&&v.error_code===null)||result&&v.error_code===null&&v.attempt_id===null&&v.claimed_at===null));
 else jobNeed(false);
 jobNeed(new TextEncoder().encode(canonicalJSON(v)).length<=32768);return freeze(structuredClone(v));
}
export function classificationJobReply(route,status,value,body=null,ownerId=null){
 if(route.kind==='classification-history'){
  if(status!==200)return classificationJobError(status,value,{upstream:true});
  jobNeed(route.method==='GET'&&exact(value,['schemaVersion','contract','project_id','source_result_id','items'])&&value.schemaVersion===1&&value.contract==='de_classification_history_v0.1'&&value.project_id===route.project_id&&value.source_result_id===route.source_result_id&&Array.isArray(value.items)&&value.items.length<=5);
  const ids=new Set(),requests=new Set();let previous=null;
  for(const item of value.items){classificationJobReply({method:'GET',kind:'classification-job-lookup',project_id:route.project_id,request_id:item?.request_id},200,item,null,ownerId);jobNeed(item.source_result_id===route.source_result_id&&!ids.has(item.classification_job_id)&&!requests.has(item.request_id));ids.add(item.classification_job_id);requests.add(item.request_id);if(previous)jobNeed(previous.created_at>item.created_at||previous.created_at===item.created_at&&previous.classification_job_id>item.classification_job_id);previous=item;}
  jobNeed(new TextEncoder().encode(canonicalJSON(value)).length<=32768);return freeze(structuredClone(value));
 }
 if(route.kind==='classification-run-lookup')return classificationRunReply(route,status,value,ownerId);
 if(![200,201].includes(status))return classificationJobError(status,value,{upstream:true});
 jobNeed((route.method==='POST'||status===200)&&exact(value,queuedKeys)&&value.schemaVersion===1&&value.contract==='de_classification_queued_v0.1');
 for(const k of ['classification_job_id','project_id','request_id','created_by','source_result_id'])jobNeed(uuid(value[k]));
 jobNeed(value.project_id===route.project_id&&(!route.request_id||value.request_id===route.request_id)&&(!ownerId||value.created_by===ownerId)&&value.status==='queued'&&value.observation==='confirmed');
 for(const k of ['source_manifest_sha256','source_binding_sha256','parameters_sha256'])jobNeed(hash(value[k]));
 classificationParameters(value.parameters);jobNeed(canonicalJSON(value.core)===canonicalJSON(CLASSIFICATION_CORE));
 jobNeed(typeof value.created_at==='string'&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/.test(value.created_at)&&!value.created_at.startsWith('0000-')&&Number.isFinite(Date.parse(value.created_at))&&new Date(Date.parse(value.created_at)).toISOString()===value.created_at.slice(0,23)+'Z');
 if(body)jobNeed(value.request_id===body.request_id&&value.source_result_id===body.source_result_id&&canonicalJSON(value.parameters)===canonicalJSON(body.parameters));
 return freeze(structuredClone(value));
}
