// Server-side API004 wire contract, current raw_bytea_v1 intermediate adapter only.
import {createHash} from 'node:crypto';
import {strictProjectJSON} from './projects-bridge.mjs';

export const MAX_IMPORT_SUCCESS=1114112, MAX_IMPORT_ERROR=4096;
export const importErrors=new Map([[400,['invalid_import_request']],[401,['unauthenticated']],[403,['auth_origin_rejected']],
  [404,['project_not_found','file_not_found','dataset_version_not_found']],[405,['method_not_allowed']],[409,['project_context_changed']],
  [413,['import_report_too_large','import_request_too_large']],[415,['unsupported_media_type']],[422,['txt_import_failed']],
  [429,['import_busy','import_rate_limited']],[503,['import_unavailable','import_executor_unavailable','import_source_integrity_failed','parser_release_mismatch','import_persisted_report_invalid','import_control_failed']],
  [504,['import_timeout','import_result_unknown']]]);
export class ImportFailure extends Error {constructor(status=503,code='import_unavailable'){super(code);this.status=status;this.code=code;}}
const bad=(status=400,code='invalid_import_request')=>{throw new ImportFailure(status,code);};
const uuid=v=>typeof v==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v);
const record=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const exact=(v,keys)=>record(v)&&Object.keys(v).sort().join(',')===[...keys].sort().join(',');
const scalar=v=>typeof v==='string'&&Buffer.from(v,'utf8').toString('utf8')===v;
const integer=(v,min,max)=>Number.isSafeInteger(v)&&v>=min&&v<=max;
const sha=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v);
const digest=v=>createHash('sha256').update(v).digest('hex');
export const canonicalImportJSON=v=>JSON.stringify(Array.isArray(v)?v.map(x=>JSON.parse(canonicalImportJSON(x))):record(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,JSON.parse(canonicalImportJSON(v[k]))])):v);
const same=(a,b)=>canonicalImportJSON(a)===canonicalImportJSON(b);

export function ownsImportRoute(raw){
  if(typeof raw!=='string')return false;
  try{return decodeURIComponent(new URL(raw,'http://127.0.0.1').pathname).replace(/\/{2,}/g,'/').startsWith('/api/v1/uploads');}
  catch{return raw.startsWith('/api/v1/uploads');}
}
export function importRoute(raw,method){
  if(typeof raw!=='string'||raw.length>256||raw.includes('#'))bad();
  const match=/^\/api\/v1\/uploads\/([0-9a-f-]{36})\/complete(?:\?([^#]*))?$/.exec(raw);
  if(!match||!uuid(match[1]))bad();
  if(!['GET','POST'].includes(method))bad(405,'method_not_allowed');
  const values=new Map();
  if(method==='POST'&&match[2]!==undefined)bad();
  if(method==='GET'){
    for(const part of (match[2]??'').split('&')){
      const pair=part.split('=');
      if(pair.length!==2||!['project_id','version_id'].includes(pair[0])||values.has(pair[0])||!uuid(pair[1]))bad();
      values.set(...pair);
    }
    if(!values.has('project_id'))bad();
  }
  return {method,path:raw,file_id:match[1],project_id:values.get('project_id')??null,version_id:values.get('version_id')??null};
}
export function importInput(raw){
  try{if(raw.byteLength>1024)bad(413,'import_request_too_large');const v=strictProjectJSON(raw);if(!exact(v,['project_id'])||!uuid(v.project_id))bad();return{project_id:v.project_id};}
  catch(e){if(e instanceof ImportFailure)throw e;bad();}
}

const freezeProfile=v=>{if(v&&typeof v==='object'&&!Object.isFrozen(v)){Object.values(v).forEach(freezeProfile);Object.freeze(v);}return v;};
const approvedProfiles=freezeProfile({"txt_10mib_1mcells_v1":{"config_sha256":"8f2d0412a6784c17ebcb0673b2c5c70dd76178bb361b7dc700ebaf110f733ad0","configuration":{"adapter_version":"raw_bytea_v1","limits":{"max_bytes":10485760,"max_cells":1000000,"max_columns":512,"max_field_units":1000000,"max_id_units":2000000,"max_member_occurrences":100000,"max_records":10000},"parsed_table_materialized":false,"parser_contract":"protein_groups_txt_v1","parser_release":{"sha256":"a1eafab03bb08a3153280c18742e071f12dd97ea6d4ce122f256836bd136c909","source_commit":"ad3bad6e8c3c8c0b9f42551cd9a893876e659227","source_pins":{"import_report.py":{"bytes":3771,"sha256":"6127539ae5ab09c4709022e3bf97c6cb62fc1acd8206384e24439750fdffebca"},"protein_groups_parser.py":{"bytes":13809,"sha256":"520d7c00f4a501483402885a2a71844274fe4430e6cc410e40789380bc0edaad"}}},"publisher_contract":"raw_backed_dataset_import_v1","report_schema_version":1,"representation":"raw_backed","schemaVersion":1}},"txt_25mib_2mcells_v2":{"config_sha256":"fbc263a8d18792c936bcd4873bafab6fc524039c19b53286539bfd5b1cb588d2","configuration":{"adapter_version":"raw_bytea_v1","limits":{"max_bytes":26214400,"max_cells":2000000,"max_columns":512,"max_field_units":1000000,"max_id_units":2000000,"max_member_occurrences":100000,"max_records":10000},"parsed_table_materialized":false,"parser_contract":"protein_groups_txt_v1","parser_release":{"sha256":"ac12f513aa54331e19dc826f27950687bc4cb544a636d5be2e75b93cc3f7c1c0","source_commit":"7834bef44bc05f1634cdd351283dc8e851107df6","source_pins":{"import_report.py":{"bytes":3771,"sha256":"6127539ae5ab09c4709022e3bf97c6cb62fc1acd8206384e24439750fdffebca"},"protein_groups_parser.py":{"bytes":13809,"sha256":"a681a57dd0c943623484e86865a5014b7befc1ee5d89ee62cc5b9988438b59f0"}}},"publisher_contract":"raw_backed_dataset_import_v1","report_schema_version":1,"representation":"raw_backed","schemaVersion":1}}});
const currentProfile=approvedProfiles['txt_25mib_2mcells_v2'];
function stableKey(file,profile=currentProfile){
  const namespace=Buffer.from('3bc2494484f25e4380cb9d536a8b9231','hex');
  const hash=createHash('sha1').update(namespace).update(file+':protein_groups_txt_v1:'+profile.config_sha256).digest().subarray(0,16);
  hash[6]=hash[6]&15|80;hash[8]=hash[8]&63|128;
  const s=hash.toString('hex');return `${s.slice(0,8)}-${s.slice(8,12)}-${s.slice(12,16)}-${s.slice(16,20)}-${s.slice(20)}`;
}
const reportFields=['source','status','observed_bytes','observed_raw_sha256','source_complete','issues','row_count','column_count','intensity_sample_count','lfq_sample_count','member_occurrences','empty_group_count','quant_empty_string_cells','quant_exact_zero_string_cells','leading_bom','crlf_count','bare_cr_count','bare_lf_count','parser_contract_version','association_status','association_counts','dataset_version_id','persisted','scientific_analysis_status'];
function span(v,bytes,header=false){
  const keys=['start_byte','end_byte','start_line','end_line',...(header?[]:['record_number'])];
  if(!exact(v,keys)||!integer(v.start_byte,0,bytes)||!integer(v.end_byte,v.start_byte,bytes)||!integer(v.start_line,1,bytes+1)||!integer(v.end_line,v.start_line,bytes+1)||header&&(v.start_byte!==0||v.start_line!==1))throw Error();
}
export function validateImportSuccess(value,route){
  try{
    if(!exact(value,['datasetVersion','importReport']))throw Error();
    const v=value.datasetVersion,d=value.importReport;
    if(!route||!uuid(route.project_id)||!uuid(route.file_id)||!['GET','POST'].includes(route.method)
        ||route.version_id!=null&&!uuid(route.version_id)||route.method==='POST'&&route.version_id!=null)throw Error();
    const profile=Object.values(approvedProfiles).find(p=>v?.config_sha256===p.config_sha256&&same(v.configuration,p.configuration));
    if(!profile||route.method==='POST'&&profile!==currentProfile)throw Error();
    const limits=profile.configuration.limits;
    if(!exact(v,['dataset_version_id','project_id','file_id','parser_contract','configuration','config_sha256','source','representation','parsed_table_materialized','createdAt'])
        ||v.project_id!==route.project_id||v.file_id!==route.file_id||v.dataset_version_id!==stableKey(route.file_id,profile)
        ||route.version_id!==null&&route.version_id!==undefined&&v.dataset_version_id!==route.version_id
        ||v.parser_contract!=='protein_groups_txt_v1'||!same(v.configuration,profile.configuration)||v.config_sha256!==profile.config_sha256
        ||v.representation!=='raw_backed'||v.parsed_table_materialized!==false
        ||typeof v.createdAt!=='string'||!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/.test(v.createdAt)||v.createdAt.startsWith('0000-')
        ||!Number.isFinite(Date.parse(v.createdAt))||new Date(Date.parse(v.createdAt)).toISOString()!==v.createdAt.slice(0,23)+'Z')throw Error();
    const s=v.source;
    if(!exact(s,['project_id','raw_file_id','raw_version_key','expected_bytes','expected_raw_sha256','role','format','adapter_version'])
        ||s.project_id!==route.project_id||s.raw_file_id!==route.file_id||!integer(s.expected_bytes,1,limits.max_bytes)||!sha(s.expected_raw_sha256)
        ||s.raw_version_key!=='raw_bytea_v1:'+route.file_id+':'+s.expected_raw_sha256||s.role!=='quantification'||s.format!=='proteinGroups_txt'||s.adapter_version!=='raw_bytea_v1')throw Error();
    if(!exact(d,['schemaVersion','representation','parsed_table_materialized','report','columns','sample_columns','header_span','row_source_spans','warnings'])
        ||d.schemaVersion!==1||d.representation!=='raw_backed'||d.parsed_table_materialized!==false)throw Error();
    const r=d.report;
    if(!exact(r,reportFields)||!same(r.source,s)||r.status!=='parsed'||r.persisted!==true||r.dataset_version_id!==v.dataset_version_id
        ||r.source_complete!==true||r.observed_bytes!==s.expected_bytes||r.observed_raw_sha256!==s.expected_raw_sha256
        ||r.parser_contract_version!=='protein_groups_txt_v1'||!Array.isArray(r.issues)||r.issues.length!==0||r.association_status!=='not_requested'
        ||r.association_counts!==null||r.scientific_analysis_status!=='not_started'||typeof r.leading_bom!=='boolean'
        ||!integer(r.row_count,1,limits.max_records)||!integer(r.column_count,2,limits.max_columns)||r.row_count*r.column_count>limits.max_cells
        ||!integer(r.intensity_sample_count,0,r.column_count)||!integer(r.lfq_sample_count,0,r.column_count-r.intensity_sample_count)
        ||!integer(r.member_occurrences,0,limits.max_member_occurrences)||!integer(r.empty_group_count,0,r.row_count))throw Error();
    const cells=r.row_count*(r.intensity_sample_count+r.lfq_sample_count);
    if(!integer(r.quant_empty_string_cells,0,cells)||!integer(r.quant_exact_zero_string_cells,0,cells-r.quant_empty_string_cells))throw Error();
    for(const k of ['crlf_count','bare_cr_count','bare_lf_count'])if(!integer(r[k],0,s.expected_bytes))throw Error();
    if(2*r.crlf_count+r.bare_cr_count+r.bare_lf_count>s.expected_bytes)throw Error();
    if(!Array.isArray(d.columns)||d.columns.length!==r.column_count)throw Error();
    for(const [i,c]of d.columns.entries()){
      if(!exact(c,['ordinal','name_raw','quant_family','sample_label_raw'])||c.ordinal!==i||!scalar(c.name_raw)||c.name_raw.length>limits.max_field_units||c.name_raw.includes('\0'))throw Error();
      const family=['Intensity','LFQ intensity'].find(p=>c.name_raw.startsWith(p+' '))??null;
      if(c.quant_family!==family||c.sample_label_raw!==(family?c.name_raw.slice(family.length+1):null))throw Error();
    }
    if(d.columns.filter(c=>c.name_raw==='Protein IDs').length!==1||!Array.isArray(d.sample_columns)||!same(d.sample_columns,d.columns.filter(c=>c.quant_family!==null))
        ||r.intensity_sample_count!==d.columns.filter(c=>c.quant_family==='Intensity').length||r.lfq_sample_count!==d.columns.filter(c=>c.quant_family==='LFQ intensity').length)throw Error();
    span(d.header_span,s.expected_bytes,true);
    if(!Array.isArray(d.row_source_spans)||d.row_source_spans.length!==r.row_count)throw Error();
    let last=d.header_span;
    for(const [i,current]of d.row_source_spans.entries()){
      span(current,s.expected_bytes);if(current.record_number!==i+1||current.start_byte<last.end_byte||current.start_line<last.end_line)throw Error();last=current;
    }
    const warnings=[];
    for(const [field,code]of [['empty_group_count','EMPTY_PROTEIN_GROUPS'],['quant_empty_string_cells','QUANT_EMPTY_STRING_CELLS']])if(r[field])warnings.push({code,count:r[field]});
    if(!Array.isArray(d.warnings)||!same(d.warnings,warnings)||Buffer.byteLength(JSON.stringify(d))>1048576||Buffer.byteLength(JSON.stringify(value))>MAX_IMPORT_SUCCESS)throw Error();
    return value;
  }catch{throw new ImportFailure(503,'import_persisted_report_invalid');}
}

const issueCodes=new Set(['INVALID_SOURCE_REF','INVALID_LIMITS','INVALID_CONTROL','CONTROL_FAILED','INPUT_READ_FAILED','INVALID_CHUNK','SOURCE_INTEGRITY_MISMATCH','INVALID_UTF8','NUL_BYTE','EMPTY_FILE','TXT_HEADER_REQUIRED','TXT_PROTEIN_IDS_HEADER_COUNT','TXT_ROW_WIDTH','TXT_QUOTE_SYNTAX','RESOURCE_LIMIT','CANCELLED']);
const limitKeys=new Set(['max_bytes','max_records','max_columns','max_cells','max_field_units','max_member_occurrences','max_id_units']);
export function validateImportError(status,value,route){
  if(!exact(value,['error'])||!record(value.error)||!importErrors.get(status)?.includes(value.error.code))throw Error();
  const e=value.error,keys=['code'];
  if(Object.hasOwn(e,'version_key')){if(e.code!=='import_result_unknown'||!uuid(e.version_key)||!route||route.method!=='POST'||route.version_id!=null||!uuid(route.file_id)||e.version_key!==stableKey(route.file_id))throw Error();keys.push('version_key');}
  if(Object.hasOwn(e,'issues')){
    if(e.code!=='txt_import_failed'||!Array.isArray(e.issues)||e.issues.length>8)throw Error();keys.push('issues');
    for(const issue of e.issues){
      if(!exact(issue,['code','record_number','line','column_ordinal','limit_key'])||!issueCodes.has(issue.code)||issue.limit_key!==null&&!limitKeys.has(issue.limit_key)
          ||['record_number','line','column_ordinal'].some(k=>issue[k]!==null&&!integer(issue[k],0,Number.MAX_SAFE_INTEGER)))throw Error();
    }
  }
  if(!exact(e,keys)||Buffer.byteLength(JSON.stringify(value))>MAX_IMPORT_ERROR)throw Error();return value;
}
