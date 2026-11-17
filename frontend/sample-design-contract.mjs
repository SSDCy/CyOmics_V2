export const MAX_REQUEST=131072, MAX_RESPONSE=196608, MAX_ERROR=4096;
export const families=Object.freeze({intensity:'Intensity',lfq:'LFQ intensity'});
export class SampleDesignFailure extends Error {constructor(status=503,code='sample_design_unavailable'){super(code);this.status=status;this.code=code;}}
export const uuid=v=>typeof v==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v);
export const exact=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
export const scalar=v=>typeof v==='string'&&!/[\uD800-\uDFFF]/u.test(v);
export const canonicalJSON=v=>Array.isArray(v)?'['+v.map(canonicalJSON).join(',')+']':v!==null&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonicalJSON(v[k])).join(',')+'}':JSON.stringify(v);
export const freeze=v=>{if(v&&typeof v==='object'&&!Object.isFrozen(v)){Object.values(v).forEach(freeze);Object.freeze(v);}return v;};
const reject=(status,code)=>{throw new SampleDesignFailure(status,code);};
const invalidRequest=()=>reject(400,'invalid_sample_design_request');
const invalidDesign=()=>reject(422,'invalid_sample_design');
const invalidPersisted=()=>reject(503,'sample_design_persisted_invalid');
export const sampleDesignErrors=new Map([[400,['invalid_sample_design_request']],[401,['unauthenticated']],[403,['auth_origin_rejected']],[404,['not_found','dataset_not_found','sample_design_not_found']],[405,['method_not_allowed']],[415,['unsupported_media_type']],[409,['sample_design_head_conflict','sample_design_request_conflict']],[413,['sample_design_request_too_large']],[422,['invalid_sample_design','sample_design_version_limit']],[503,['sample_design_unavailable','sample_design_persisted_invalid']],[504,['sample_design_timeout','sample_design_result_unknown']]]);
export function sampleDesignRoute(raw,method){
  if(typeof raw!=='string')reject(404,'not_found');
  const question=raw.indexOf('?'),path=question<0?raw:raw.slice(0,question);
  const m=/^\/api\/v1\/datasets\/([0-9a-f-]{36})\/sample-designs$/.exec(path);
  if(!m||!uuid(m[1]))reject(404,'not_found');
  if(!['GET','POST'].includes(method))reject(405,'method_not_allowed');
  if(raw.length>300||question<0||raw.includes('#')||/[^\x21-\x7e]|[%+]/.test(raw))invalidRequest();
  const q=new Map();for(const p of raw.slice(question+1).split('&')){const a=p.split('=');if(a.length!==2||!['project_id','quant_family',...(method==='GET'?['request_id']:[])].includes(a[0])||q.has(a[0]))invalidRequest();q.set(...a);}
  if(!uuid(q.get('project_id'))||!Object.hasOwn(families,q.get('quant_family'))||q.has('request_id')&&!uuid(q.get('request_id')))invalidRequest();
  return freeze({path:raw,method,dataset_version_id:m[1],project_id:q.get('project_id'),family_token:q.get('quant_family'),quant_family:families[q.get('quant_family')],request_id:q.get('request_id')??null});
}
// Duplicate keys, invalid UTF-8, non-scalar strings, non-finite numbers and deep JSON are rejected before validation.
export function strictJSON(bytes){
  const text=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(bytes);let at=0;
  const space=()=>{while(/[\x20\t\n\r]/.test(text[at]||'x'))at++;};
  const string=()=>{const start=at++;while(at<text.length){const c=text[at++];if(c==='\\'){at++;continue;}if(c==='"'){const s=JSON.parse(text.slice(start,at));if(!scalar(s))throw Error();return s;}}throw Error();};
  function value(depth){if(depth>32)throw Error();space();if(text[at]==='"')return string();if(text[at]==='{'){at++;space();const o=Object.create(null);if(text[at]==='}'){at++;return o;}for(;;){space();if(text[at]!=='"')throw Error();const k=string();space();if(Object.hasOwn(o,k)||text[at++]!==':')throw Error();o[k]=value(depth+1);space();const c=text[at++];if(c==='}')return o;if(c!==',')throw Error();}}if(text[at]==='['){at++;space();const a=[];if(text[at]===']'){at++;return a;}for(;;){a.push(value(depth+1));space();const c=text[at++];if(c===']')return a;if(c!==',')throw Error();}}for(const [s,v] of [['true',true],['false',false],['null',null]])if(text.startsWith(s,at)){at+=s.length;return v;}const re=/-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/y;re.lastIndex=at;const m=re.exec(text);if(!m||!Number.isFinite(Number(m[0])))throw Error();at=re.lastIndex;return Number(m[0]);}
  const result=value(0);space();if(at!==text.length)throw Error();return result;
}
export function validateSource(source){
  if(!exact(source,['dataset_version_id','sample_columns'])||!uuid(source.dataset_version_id)||!Array.isArray(source.sample_columns)||source.sample_columns.length>512)invalidPersisted();
  const seen=new Set();for(const c of source.sample_columns){if(!exact(c,['ordinal','name_raw','quant_family','sample_label_raw'])||!Number.isSafeInteger(c.ordinal)||c.ordinal<0||c.ordinal>511||seen.has(c.ordinal)||!Object.values(families).includes(c.quant_family)||!scalar(c.name_raw)||!scalar(c.sample_label_raw)||c.name_raw!==c.quant_family+' '+c.sample_label_raw)invalidPersisted();seen.add(c.ordinal);}return source;
}
// Source authority is supplied by a caller that has validated the import report, never by a saved design.
export function validateDesign(d,source,route){
  validateSource(source);
  if(!exact(d,['schemaVersion','dataset_version_id','quant_family','rows'])||d.schemaVersion!==1||d.dataset_version_id!==source.dataset_version_id||d.dataset_version_id!==route.dataset_version_id||d.quant_family!==route.quant_family||!Array.isArray(d.rows))invalidDesign();
  const cols=source.sample_columns.filter(c=>c.quant_family===d.quant_family),counts=new Map();for(const c of cols)counts.set(c.sample_label_raw,(counts.get(c.sample_label_raw)||0)+1);
  if(d.rows.length!==cols.length)invalidDesign();
  for(const [i,r] of d.rows.entries()){
    const c=cols[i];if(!exact(r,['column_ordinal','name_raw','sample_label_raw','group','condition','control','replicate','replicate_type','confirmed'])||!Number.isSafeInteger(r.column_ordinal)||r.column_ordinal!==c.ordinal||r.name_raw!==c.name_raw||r.sample_label_raw!==c.sample_label_raw||typeof r.confirmed!=='boolean'||!['unknown','yes','no'].includes(r.control)||!['unknown','biological','technical'].includes(r.replicate_type))invalidDesign();
    for(const [k,max] of [['group',80],['condition',160],['replicate',40]])if(!scalar(r[k])||/[\u0000-\u001f\u007f-\u009f]/u.test(r[k])||[...r[k]].length>max)invalidDesign();
    if(r.confirmed&&(!c.sample_label_raw.trim()||counts.get(c.sample_label_raw)!==1||['group','condition','replicate'].some(k=>!r[k].trim())||r.group!==r.group.trim()||r.control==='unknown'||r.replicate_type==='unknown'))invalidDesign();
  }return d;
}
export function sampleDesignInput(bytes,route,source){
  if(!(bytes instanceof Uint8Array))invalidRequest();
  if(bytes.byteLength>MAX_REQUEST)reject(413,'sample_design_request_too_large');
  let p;try{p=strictJSON(bytes);}catch{invalidRequest();}
  if(!exact(p,['schemaVersion','request_id','expected_head_version_id','design'])||p.schemaVersion!==1||!uuid(p.request_id)||p.expected_head_version_id!==null&&!uuid(p.expected_head_version_id))invalidRequest();
  validateDesign(p.design,source,route);return freeze(p);
}
export async function designSHA(d,cryptoImpl=globalThis.crypto){return [...new Uint8Array(await cryptoImpl.subtle.digest('SHA-256',new TextEncoder().encode(canonicalJSON(d))))].map(v=>v.toString(16).padStart(2,'0')).join('');}
export async function validateSampleDesignResponse(v,route,source,{request=null,head=undefined,cryptoImpl=globalThis.crypto}={}){
  try{
    if(!exact(v,['schemaVersion','project_id','dataset_version_id','quant_family','record'])||v.schemaVersion!==1||v.project_id!==route.project_id||v.dataset_version_id!==route.dataset_version_id||v.quant_family!==route.quant_family)throw Error();
    validateSource(source);if(source.dataset_version_id!==route.dataset_version_id)throw Error();
    const r=v.record;if(r===null){if(route.method!=='GET'||route.request_id!==null)throw Error();return freeze(v);}
    if(!exact(r,['sample_design_version_id','parent_version_id','revision','request_id','design_sha256','created_at','design'])||!uuid(r.sample_design_version_id)||!uuid(r.request_id)||r.parent_version_id!==null&&!uuid(r.parent_version_id)||!Number.isSafeInteger(r.revision)||r.revision<1||r.revision>100||(r.revision===1)!==(r.parent_version_id===null)||r.parent_version_id===r.sample_design_version_id||typeof r.created_at!=='string'||!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/.test(r.created_at)||r.created_at.startsWith('0000-')||!Number.isFinite(Date.parse(r.created_at))||new Date(Date.parse(r.created_at)).toISOString()!==r.created_at.slice(0,23)+'Z')throw Error();
    validateDesign(r.design,source,route);if(!/^[0-9a-f]{64}$/.test(r.design_sha256)||r.design_sha256!==await designSHA(r.design,cryptoImpl))throw Error();
    if(route.request_id!==null&&r.request_id!==route.request_id)throw Error();
    if(request&&(r.request_id!==request.request_id||r.parent_version_id!==request.expected_head_version_id||canonicalJSON(r.design)!==canonicalJSON(request.design)||head!==undefined&&r.revision!==(head===null?1:head.revision+1)))throw Error();
    return freeze(v);
  }catch{invalidPersisted();}
}
export function validateSampleDesignError(status,v){if(!exact(v,['error'])||!exact(v.error,['code'])||!sampleDesignErrors.get(status)?.includes(v.error.code))invalidPersisted();return freeze(v);}
