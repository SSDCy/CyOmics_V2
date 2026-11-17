export const IMPORT_CAP = 1114112;
export const canonicalUUID = v => typeof v === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v);
const hexSHA = v => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v);
const exact = (v, keys) => v !== null && typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === keys.length && keys.every(k => Object.hasOwn(v, k));
const int = v => Number.isSafeInteger(v) && v >= 0;
const scalar = v => typeof v === 'string' && !/[\uD800-\uDFFF]/u.test(v);
const require = condition => { if (!condition) throw Error('import_persisted_report_invalid'); };
export const LIMITS = Object.freeze({max_bytes:10485760,max_records:10000,max_columns:512,max_cells:1000000,max_field_units:1000000,max_member_occurrences:100000,max_id_units:2000000});
const PINS = Object.freeze({ 'import_report.py':Object.freeze({bytes:3771,sha256:'6127539ae5ab09c4709022e3bf97c6cb62fc1acd8206384e24439750fdffebca'}),
  'protein_groups_parser.py':Object.freeze({bytes:13809,sha256:'520d7c00f4a501483402885a2a71844274fe4430e6cc410e40789380bc0edaad'})});
export const canonicalJSON = v => {
  if (Array.isArray(v)) return '[' + v.map(canonicalJSON).join(',') + ']';
  if (v !== null && typeof v === 'object') return '{' + Object.keys(v).sort().map(k => JSON.stringify(k) + ':' + canonicalJSON(v[k])).join(',') + '}';
  return JSON.stringify(v);
};
const hex = b => [...new Uint8Array(b)].map(v => v.toString(16).padStart(2,'0')).join('');
const digest = async (text, crypto) => hex(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text)));

// Detect duplicate keys and excessive nesting before normal JSON materialization.
export function strictImportJSON(text) {
  let i=0; const ws=()=>{while(/\s/.test(text[i]||'') && i<text.length)i++;};
  function string(){const start=i++;let escape=false;for(;i<text.length;i++){const c=text[i];if(!escape&&c==='"'){i++;const v=JSON.parse(text.slice(start,i));require(scalar(v));return v;}if(!escape&&c==='\\')escape=true;else escape=false;}throw Error('invalid_json');}
  function value(depth){require(depth<=32);ws();const c=text[i];
    if(c==='"'){string();return;}if(c==='{'||c==='['){const object=c==='{',end=object?'}':']',keys=new Set();let count=0;i++;ws();if(text[i]===end){i++;return;}
      for(;;){require(++count<=10000);ws();if(object){require(text[i]==='"');const key=string();require(!keys.has(key));keys.add(key);ws();require(text[i++]===':');}value(depth+1);ws();if(text[i]===end){i++;return;}require(text[i++]===',');}}
    else{const match=/^(?:null|true|false|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/.exec(text.slice(i));require(!!match);i+=match[0].length;}}
  value(0);ws();require(i===text.length);return JSON.parse(text);
}
export function sourceBinding(file) {
  require(exact(file,['file_id','project_id','request_id','filename','format','bytes','sha256','status','createdAt'])
    && canonicalUUID(file.project_id) && canonicalUUID(file.file_id) && canonicalUUID(file.request_id) && hexSHA(file.sha256) && int(file.bytes) && file.bytes>=1 && file.bytes<=LIMITS.max_bytes
    && file.format==='proteinGroups_txt' && file.status==='stored' && scalar(file.filename)
    && !/[\u0000-\u001f\u007f-\u009f/\\]/u.test(file.filename) && /\.txt$/i.test(file.filename) && new TextEncoder().encode(file.filename).length<=255
    && typeof file.createdAt==='string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(file.createdAt)
    && Number.isFinite(Date.parse(file.createdAt)) && new Date(file.createdAt).toISOString().slice(0,19)===file.createdAt.slice(0,19));
  return {project_id:file.project_id,raw_file_id:file.file_id,raw_version_key:'raw_bytea_v1:'+file.file_id+':'+file.sha256,
    expected_bytes:file.bytes,expected_raw_sha256:file.sha256,role:'quantification',format:'proteinGroups_txt',adapter_version:'raw_bytea_v1'};
}
export async function expectedConfiguration(crypto=globalThis.crypto) {
  return {schemaVersion:1,publisher_contract:'raw_backed_dataset_import_v1',parser_contract:'protein_groups_txt_v1',report_schema_version:1,
    parser_release:{source_commit:'ad3bad6e8c3c8c0b9f42551cd9a893876e659227',source_pins:PINS,sha256:await digest(canonicalJSON(PINS),crypto)},
    adapter_version:'raw_bytea_v1',representation:'raw_backed',parsed_table_materialized:false,limits:{...LIMITS}};
}
export async function importVersionKey(file, crypto=globalThis.crypto) {
  const config=await expectedConfiguration(crypto),configHash=await digest(canonicalJSON(config),crypto);
  const namespace='3bc2494484f25e4380cb9d536a8b9231',ns=Uint8Array.from(namespace.match(/../g),v=>parseInt(v,16));
  const name=new TextEncoder().encode(file.file_id+':protein_groups_txt_v1:'+configHash),bytes=new Uint8Array(16+name.length);bytes.set(ns);bytes.set(name,16);
  const hash=new Uint8Array(await crypto.subtle.digest('SHA-1',bytes)).slice(0,16);hash[6]=(hash[6]&15)|80;hash[8]=(hash[8]&63)|128;
  const h=hex(hash);return {config,configHash,key:[h.slice(0,8),h.slice(8,12),h.slice(12,16),h.slice(16,20),h.slice(20)].join('-')};
}
export async function validateImportDTO(dto,file,crypto=globalThis.crypto) {
  const source=sourceBinding(file),expected=await importVersionKey(file,crypto);require(exact(dto,['datasetVersion','importReport']));
  const v=dto.datasetVersion,d=dto.importReport;
  require(exact(v,['dataset_version_id','project_id','file_id','parser_contract','configuration','config_sha256','source','representation','parsed_table_materialized','createdAt'])
    && v.dataset_version_id===expected.key && v.project_id===file.project_id && v.file_id===file.file_id && v.parser_contract==='protein_groups_txt_v1'
    && canonicalJSON(v.configuration)===canonicalJSON(expected.config) && v.config_sha256===expected.configHash && canonicalJSON(v.source)===canonicalJSON(source)
    && v.representation==='raw_backed' && v.parsed_table_materialized===false && typeof v.createdAt==='string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/.test(v.createdAt)
    && Number.isFinite(Date.parse(v.createdAt)) && new Date(v.createdAt).toISOString().slice(0,19)===v.createdAt.slice(0,19));
  require(exact(d,['schemaVersion','representation','parsed_table_materialized','report','columns','sample_columns','header_span','row_source_spans','warnings'])
    && d.schemaVersion===1 && d.representation==='raw_backed' && d.parsed_table_materialized===false && new TextEncoder().encode(canonicalJSON(d)).length<=1048576);
  const r=d.report;require(exact(r,['source','status','observed_bytes','observed_raw_sha256','source_complete','issues','row_count','column_count','intensity_sample_count','lfq_sample_count','member_occurrences','empty_group_count','quant_empty_string_cells','quant_exact_zero_string_cells','leading_bom','crlf_count','bare_cr_count','bare_lf_count','parser_contract_version','association_status','association_counts','dataset_version_id','persisted','scientific_analysis_status'])
    && canonicalJSON(r.source)===canonicalJSON(source) && r.status==='parsed' && r.observed_bytes===file.bytes && r.observed_raw_sha256===file.sha256 && r.source_complete===true
    && Array.isArray(r.issues) && r.issues.length===0 && r.parser_contract_version==='protein_groups_txt_v1' && r.association_status==='not_requested' && r.association_counts===null
    && r.dataset_version_id===expected.key && r.persisted===true && r.scientific_analysis_status==='not_started' && typeof r.leading_bom==='boolean');
  for(const k of ['row_count','column_count','intensity_sample_count','lfq_sample_count','member_occurrences','empty_group_count','quant_empty_string_cells','quant_exact_zero_string_cells','crlf_count','bare_cr_count','bare_lf_count'])require(int(r[k]));
  require(r.row_count>=1 && r.row_count<=10000 && r.column_count>=2 && r.column_count<=512 && r.row_count*r.column_count<=1000000 && r.member_occurrences<=100000 && r.empty_group_count<=r.row_count
    && Array.isArray(d.columns) && d.columns.length===r.column_count && Array.isArray(d.row_source_spans) && d.row_source_spans.length===r.row_count);
  let familyA=0,familyB=0,protein=0;
  d.columns.forEach((c,i)=>{require(exact(c,['ordinal','name_raw','quant_family','sample_label_raw']) && c.ordinal===i && scalar(c.name_raw) && c.name_raw.length<=LIMITS.max_field_units
    && [null,'Intensity','LFQ intensity'].includes(c.quant_family) && (c.quant_family===null?c.sample_label_raw===null:scalar(c.sample_label_raw)&&c.name_raw===c.quant_family+' '+c.sample_label_raw));
    if(c.name_raw==='Protein IDs')protein++;if(c.quant_family==='Intensity')familyA++;if(c.quant_family==='LFQ intensity')familyB++;});
  require(protein===1 && familyA===r.intensity_sample_count && familyB===r.lfq_sample_count && r.quant_empty_string_cells+r.quant_exact_zero_string_cells<=r.row_count*(familyA+familyB)
    && canonicalJSON(d.sample_columns)===canonicalJSON(d.columns.filter(c=>c.quant_family!==null)));
  const span=(s,keys)=>exact(s,keys)&&keys.every(k=>int(s[k]));
  const h=d.header_span;require(span(h,['start_byte','end_byte','start_line','end_line'])&&h.start_byte===0&&h.end_byte<=file.bytes&&h.start_line===1&&h.end_line>=1);
  let previous=h.end_byte;
  d.row_source_spans.forEach((s,i)=>{require(span(s,['record_number','start_byte','end_byte','start_line','end_line'])&&s.record_number===i+1&&previous<=s.start_byte&&s.start_byte<=s.end_byte&&s.end_byte<=file.bytes&&s.start_line>=1&&s.start_line<=s.end_line);previous=s.end_byte;});
  const warnings=[];for(const [field,code]of [['empty_group_count','EMPTY_PROTEIN_GROUPS'],['quant_empty_string_cells','QUANT_EMPTY_STRING_CELLS']])if(r[field])warnings.push({code,count:r[field]});
  require(canonicalJSON(d.warnings)===canonicalJSON(warnings));return dto;
}
const ISSUE_CODES=new Set(['INVALID_SOURCE_REF','INVALID_LIMITS','INVALID_CONTROL','CONTROL_FAILED','INPUT_READ_FAILED','INVALID_CHUNK','SOURCE_INTEGRITY_MISMATCH','INVALID_UTF8','NUL_BYTE','EMPTY_FILE','TXT_HEADER_REQUIRED','TXT_PROTEIN_IDS_HEADER_COUNT','TXT_ROW_WIDTH','TXT_QUOTE_SYNTAX','RESOURCE_LIMIT','CANCELLED']);
export const IMPORT_ERRORS=Object.freeze(Object.fromEntries(Object.entries({400:['invalid_import_request'],401:['unauthenticated'],403:['auth_origin_rejected'],404:['project_not_found','file_not_found','dataset_version_not_found'],405:['method_not_allowed'],409:['project_context_changed'],413:['import_report_too_large','import_request_too_large'],415:['unsupported_media_type'],422:['txt_import_failed'],429:['import_busy','import_rate_limited'],503:['import_unavailable','import_executor_unavailable','import_source_integrity_failed','parser_release_mismatch','import_persisted_report_invalid','import_control_failed'],504:['import_timeout','import_result_unknown']}).map(([k,v])=>[k,Object.freeze(v)])));
export function validateImportError(status,body) {
  require(exact(body,['error']) && body.error && typeof body.error==='object');const e=body.error;require((IMPORT_ERRORS[status]||[]).includes(e.code));
  const keys=['code'];if(Object.hasOwn(e,'version_key')){require(e.code==='import_result_unknown'&&canonicalUUID(e.version_key));keys.push('version_key');}
  if(Object.hasOwn(e,'issues')){require(e.code==='txt_import_failed'&&Array.isArray(e.issues)&&e.issues.length<=8);keys.push('issues');
    for(const issue of e.issues){require(exact(issue,['code','record_number','line','column_ordinal','limit_key'])&&ISSUE_CODES.has(issue.code)
      && ['record_number','line','column_ordinal'].every(k=>issue[k]===null||int(issue[k])&&issue[k]<=10485761)
      && (issue.limit_key===null||Object.hasOwn(LIMITS,issue.limit_key)));}}
  require(exact(e,keys));return e;
}
