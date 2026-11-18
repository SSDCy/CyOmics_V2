import {exact,uuid,scalar,canonicalJSON,freeze} from './sample-design-contract.mjs';
import {parsePreprocessJSON} from './preprocess-owner-wire.mjs';
export {canonicalJSON};
export const DIFFERENTIAL_INPUT_MAX=8192,DIFFERENTIAL_REPLY_MAX=16384;
export class DifferentialInputFailure extends Error{constructor(status=503,code='differential_input_unavailable'){super(code);this.status=status;this.code=code;}}
const reject=(status=503,code='differential_input_unavailable')=>{throw new DifferentialInputFailure(status,code);};
export function ownsDifferentialInputRoute(raw){
 if(typeof raw!=='string')return false;const paths=[raw.split('?')[0]];
 try{paths.push(decodeURIComponent(raw.split('?')[0]),decodeURIComponent(new URL(raw,'http://127.0.0.1').pathname));}catch{}
 return paths.some(p=>/^\/api\/v1\/projects\/[^/]+\/differential-input(?:\/|$)/.test(p.replace(/\/{2,}/g,'/')));
}
export function differentialInputRoute(raw,method){
 const m=typeof raw==='string'&&/^\/api\/v1\/projects\/([0-9a-f-]{36})\/differential-input$/.exec(raw);
 if(!m||!uuid(m[1]))reject(404,'not_found');if(method!=='POST')reject(405,'method_not_allowed');
 return freeze({path:raw,method,kind:'differential-input',project_id:m[1]});
}
export function differentialInput(bytes){
 if(!(bytes instanceof Uint8Array)||!bytes.length)reject(400,'invalid_differential_input');
 if(bytes.length>DIFFERENTIAL_INPUT_MAX)reject(413,'differential_input_too_large');
 let v;try{v=parsePreprocessJSON(bytes);}catch{reject(400,'invalid_differential_input');}
 if(!exact(v,['processed_dataset_version_id','sample_design_version_id','sample_design_request_id','contrast','replicate_model'])
  ||!['processed_dataset_version_id','sample_design_version_id','sample_design_request_id'].every(k=>uuid(v[k]))
  ||!v.contrast||typeof v.contrast!=='object'||Array.isArray(v.contrast)||!scalar(v.replicate_model)||[...v.replicate_model].length<1||[...v.replicate_model].length>64)reject(400,'invalid_differential_input');
 return freeze(v);
}
// Fixed diagnostics from quant_diff_input, contrast_design and sample_design.
// No user strings or arbitrary server messages are rendered as reasons.
export const differentialReasons=freeze([
 'waiting_server_input','server_input_required','invalid_processed_version','processed_version_mismatch','paired_design_not_supported',
 'independent_biological_model_required','complete_artifact_set_required','artifact_pin_mismatch','parameter_binding_mismatch',
 'independent_sample_columns_required','invalid_import_sample_columns','selected_columns_order_or_binding_mismatch',
 'invalid_saved_sample_design','sample_parent_version_mismatch','quant_family_mismatch','invalid_contrast','single_explicit_contrast_required',
 'confirmed_sample_design_required','insufficient_numerator_biological_replicates','insufficient_denominator_biological_replicates',
 'limma_core_sample_limit_exceeded','design_not_full_rank','working_column_order_mismatch','empty_working_matrix',
 'invalid_source_row_ordinal','invalid_working_numeric_token','invalid_artifact_table','artifact_shape_mismatch',
 'row_map_binding_mismatch','mask_alignment_mismatch','unsupported_tsv_cell','invalid_server_input',
 'invalid_source','invalid_payload_keys','invalid_schema_version','dataset_version_mismatch','sample_version_mismatch',
 'invalid_contrast_list','invalid_contrast_keys','invalid_contrast_id','duplicate_contrast_id','invalid_group_choice',
 'unknown_group_choice','same_group','duplicate_ordered_pair','contrasts_empty','contrasts_incomplete',
 'included_group_control_inconsistent','included_group_control_unknown','mixed_replicate_types','technical_replicates_require_policy',
 'unknown_replicate_type','replicate_label_missing','duplicate_replicate_labels','no_selected_columns','ambiguous_sample_labels',
 'unconfirmed_rows','group_control_inconsistent'
]);
const errors=new Map([[400,['invalid_differential_input','invalid_quant_diff_input_selection','invalid_sample_design_request','invalid_project_request']],
 [401,['unauthenticated']],[403,['auth_origin_rejected']],
 [404,['not_found','project_not_found','file_not_found','dataset_not_found','dataset_version_not_found','processed_dataset_version_not_found','preprocessing_artifact_not_found','sample_design_not_found']],
 [405,['method_not_allowed']],[409,['project_context_changed']],[413,['differential_input_too_large','project_request_too_large','sample_design_request_too_large']],
 [415,['unsupported_media_type']],[503,['differential_input_unavailable']]]);
export function differentialInputError(status,value,{upstream=false}={}){
 if(upstream&&status===503)return {error:{code:'differential_input_unavailable'}};
 if(!exact(value,['error'])||!exact(value.error,['code'])||!errors.get(status)?.includes(value.error.code)
  ||upstream&&status===409)reject();return value;
}
const int=(v,min,max)=>Number.isSafeInteger(v)&&!Object.is(v,-0)&&v>=min&&v<=max;
const hash=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v);
export function differentialInputReply(route,status,v,selection){
 if(status!==200)return differentialInputError(status,v,{upstream:true});
 if(!selection||!exact(v,['schemaVersion','contract','project_id','processed_dataset_version_id','sample_design_version_id','sample_design_request_id','model_input_satisfied','reasons','source_analysis_ready','input_summary','calculation_started'])
  ||v.schemaVersion!==1||v.contract!=='differential_input_review_v0.1'||v.project_id!==route.project_id
  ||!['processed_dataset_version_id','sample_design_version_id','sample_design_request_id'].every(k=>v[k]===selection[k])
  ||typeof v.model_input_satisfied!=='boolean'||v.source_analysis_ready!==false||v.calculation_started!==false
  ||!Array.isArray(v.reasons)||v.reasons.length>differentialReasons.length||new Set(v.reasons).size!==v.reasons.length||!v.reasons.every(c=>differentialReasons.includes(c)))reject();
 const s=v.input_summary;
 if(!v.model_input_satisfied){if(!v.reasons.length||s!==null)reject();}
 else{
  const row=selection.contrast?.contrasts?.[0];
  if(v.reasons.length||selection.replicate_model!=='independent_biological'||selection.contrast?.contrasts?.length!==1
   ||!exact(row,['contrast_id','numerator_group','denominator_group'])||!uuid(row.contrast_id)
   ||!['numerator_group','denominator_group'].every(k=>scalar(row[k])&&row[k].length>0&&[...row[k]].length<=80&&!/[\x00-\x1f\x7f-\x9f]/.test(row[k]))||row.numerator_group===row.denominator_group
   ||!exact(s,['row_count','sample_count','design_rank','contrast','input_pins'])||!int(s.row_count,1,10000)||!int(s.sample_count,4,64)||s.design_rank!==2
   ||canonicalJSON(s.contrast)!==canonicalJSON(row)||!exact(s.input_pins,['matrix','design','preset']))reject();
  for(const p of Object.values(s.input_pins))if(!exact(p,['bytes','sha256'])||!int(p.bytes,1,26214400)||!hash(p.sha256))reject();
 }
 return freeze(structuredClone(v));
}
