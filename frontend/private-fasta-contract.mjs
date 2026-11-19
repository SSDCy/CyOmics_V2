import {exact,uuid,freeze} from './sample-design-contract.mjs';
import {parsePreprocessJSON} from './preprocess-owner-wire.mjs';
export const FASTA_MAX=26214400,FASTA_JSON_MAX=65536;
export const FASTA_CONFIG_SHA='380475bafbccc807ba0745ca595d3f6fe4953412a6d8403f5f14dab2fd2ce0ac';
export class FastaFailure extends Error{constructor(status=503,code='fasta_intake_unavailable',issue=null){super(code);this.status=status;this.code=code;this.issue=issue;}}
const fail=(status=503,code='fasta_intake_unavailable')=>{throw new FastaFailure(status,code);};
const need=v=>{if(!v)fail();};
export const fastaHash=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v);
export const fastaName=v=>typeof v==='string'&&new TextEncoder().encode(v).length>=1&&new TextEncoder().encode(v).length<=255&&!/[\uD800-\uDFFF\u0000-\u001f\u007f-\u009f/\\]/u.test(v)&&/\.(fa|faa|fasta)$/i.test(v);
const stamp=v=>typeof v==='string'&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/.test(v)&&Number.isFinite(Date.parse(v))&&new Date(Date.parse(v)).toISOString()===v.slice(0,23)+'Z';
const integer=(v,min,max)=>Number.isSafeInteger(v)&&v>=min&&v<=max;
export function ownsFastaRoute(raw){if(typeof raw!=='string')return false;let path=raw.split('?')[0];try{path=decodeURIComponent(path);}catch{}return /\/api\/v1\/projects\/[^/]+\/fasta-sources(?:\/|$)/.test(path.replace(/\/{2,}/g,'/'));}
export function fastaRoute(raw,method){
 if(typeof raw!=='string'||raw.length>512||/[^\x21-\x7e]|[%+#]/.test(raw))fail(404,'fasta_source_not_found');
 const m=/^\/api\/v1\/projects\/([0-9a-f-]{36})\/fasta-sources(?:\/(?:requests\/([0-9a-f-]{36})|([0-9a-f-]{36})(\/content)?))?(\?.*)?$/.exec(raw);
 if(!m||!uuid(m[1])||m[2]&&!uuid(m[2])||m[3]&&!uuid(m[3]))fail(404,'fasta_source_not_found');if(m[5])fail(400,'invalid_fasta_request');
 const collection=!m[2]&&!m[3];if(!(collection?['GET','POST']:['GET']).includes(method))fail(405,'method_not_allowed');
 const kind=collection?(method==='POST'?'save':'list'):m[2]?'receipt':m[4]?'content':'source';return freeze({path:raw,method,kind,project_id:m[1],id:m[2]||m[3]||null,long:kind!=='list',capMs:kind==='list'?9000:50000});
}
export function fastaFilenameHeader(name){if(!fastaName(name))fail(400,'invalid_fasta_request');return btoa(String.fromCharCode(...new TextEncoder().encode(name))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');}
export function fastaDisposition(name){if(!fastaName(name))fail();return "attachment; filename=\"source.fasta\"; filename*=UTF-8''"+encodeURIComponent(name).replace(/[!'()*]/g,c=>'%'+c.charCodeAt(0).toString(16).toUpperCase());}
export const FASTA_SUMMARY_KEYS=['record_count','distinct_id_count','duplicate_record_excess','valid_canonical_record_count','invalid_canonical_record_count','total_raw_sequence_characters','line_count','blank_lines'];
function saved(v,pid){
 need(exact(v,['source','index']));const s=v.source,i=v.index;need(exact(s,['source_id','project_id','request_id','filename','format','bytes','sha256','status','createdAt'])&&uuid(s.source_id)&&uuid(s.request_id)&&s.project_id===pid&&fastaName(s.filename)&&s.format==='protein_fasta'&&s.status==='stored'&&integer(s.bytes,1,FASTA_MAX)&&fastaHash(s.sha256)&&stamp(s.createdAt));
 need(exact(i,['index_version_id','project_id','source_id','source_bytes','source_sha256','index_contract','sequence_identity_contract','config_sha256','index_manifest_sha256','representation','index_materialized','source_complete','status','summary','createdAt'])&&uuid(i.index_version_id)&&i.project_id===pid&&i.source_id===s.source_id&&i.source_bytes===s.bytes&&i.source_sha256===s.sha256&&i.index_contract==='fasta_ascii_identity_index_v0.1'&&i.sequence_identity_contract==='sequence_identity_review_v0.1'&&i.config_sha256===FASTA_CONFIG_SHA&&fastaHash(i.index_manifest_sha256)&&i.representation==='fasta_source_backed_index'&&i.index_materialized===false&&i.source_complete===true&&i.status==='indexed'&&stamp(i.createdAt));
 const n=i.summary;need(exact(n,FASTA_SUMMARY_KEYS));const bounds=[[1,50000],[1,50000],[0,49999],[0,50000],[0,50000],[0,s.bytes],[1,500000],[0,500000]];FASTA_SUMMARY_KEYS.forEach((k,x)=>need(integer(n[k],...bounds[x])));need(n.distinct_id_count+n.duplicate_record_excess===n.record_count&&n.valid_canonical_record_count+n.invalid_canonical_record_count===n.record_count&&n.record_count+n.blank_lines<=n.line_count&&n.valid_canonical_record_count<=n.total_raw_sequence_characters);return v;
}
export async function fastaVersionId(source,cryptoImpl=globalThis.crypto){
 const ns=Uint8Array.from('ae925a0c687758a0a4f81ec6a8301191'.match(/../g),v=>parseInt(v,16)),name=new TextEncoder().encode([source.project_id,source.source_id,source.sha256,'fasta_ascii_identity_index_v0.1',FASTA_CONFIG_SHA].join(':')),raw=new Uint8Array(ns.length+name.length);raw.set(ns);raw.set(name,ns.length);const bytes=new Uint8Array(await cryptoImpl.subtle.digest('SHA-1',raw)).slice(0,16);bytes[6]=(bytes[6]&15)|80;bytes[8]=(bytes[8]&63)|128;const h=[...bytes].map(n=>n.toString(16).padStart(2,'0')).join('');return h.slice(0,8)+'-'+h.slice(8,12)+'-'+h.slice(12,16)+'-'+h.slice(16,20)+'-'+h.slice(20);
}
export async function fastaReply(route,status,v,expected=null,cryptoImpl=globalThis.crypto){
 if(![200,201].includes(status))return fastaError(status,v,{upstream:true});need((route.kind==='save'||status===200)&&route.kind!=='content');
 const items=route.kind==='list'?(need(exact(v,['items','nextCursor'])&&v.nextCursor===null&&Array.isArray(v.items)&&v.items.length<=4),v.items):[v];
 for(const item of items){saved(item,route.project_id);need(await fastaVersionId(item.source,cryptoImpl)===item.index.index_version_id);if(route.kind==='receipt')need(item.source.request_id===route.id);if(route.kind==='source')need(item.source.source_id===route.id);if(expected){for(const k of ['request_id','filename','bytes','sha256'])need(item.source[k]===expected[k]);if(expected.source_id)need(item.source.source_id===expected.source_id);}}
 if(route.kind==='list'){need(new Set(items.map(x=>x.source.source_id)).size===items.length&&new Set(items.map(x=>x.source.request_id)).size===items.length&&items.reduce((n,x)=>n+x.source.bytes,0)<=104857600);for(let x=1;x<items.length;x++){const a=items[x-1].source,b=items[x].source;need(a.createdAt>b.createdAt||a.createdAt===b.createdAt&&a.source_id>b.source_id);}}
 return freeze(structuredClone(v));
}
const errors=new Map([[400,['invalid_fasta_request','fasta_checksum_mismatch']],[401,['unauthenticated']],[403,['auth_origin_rejected']],[404,['project_not_found','fasta_source_not_found']],[405,['method_not_allowed']],[409,['fasta_request_conflict']],[413,['fasta_too_large','fasta_quota_exceeded']],[415,['unsupported_media_type']],[422,['fasta_index_failed']],[429,['fasta_intake_busy','fasta_rate_limited']],[503,['fasta_intake_unavailable','fasta_source_integrity_failed','fasta_index_persisted_invalid','fasta_parser_release_mismatch']],[504,['fasta_intake_timeout','fasta_result_unknown']]]);
const issueCodes=['INVALID_LIMITS','INVALID_INPUT','CHUNK_LIMIT','INVALID_CHUNK','BYTE_LIMIT','INVALID_UTF8','UTF8_BOM','NON_ASCII_FASTA','LINE_LIMIT','LONE_CR','LINE_LENGTH_LIMIT','RECORD_LIMIT','EMPTY_ID','ID_LENGTH_LIMIT','ID_CHARACTER_LIMIT','SEQUENCE_BEFORE_HEADER','SEQUENCE_LENGTH_LIMIT','NO_RECORDS'];
export function fastaError(status,v,{upstream=false}={}){
 need(exact(v,['error']));const e=v.error;if(!upstream&&status===409&&exact(e,['code'])&&e.code==='project_context_changed')return v;
 need((exact(e,['code'])||status===422&&exact(e,['code','issue']))&&errors.get(status)?.includes(e.code));if(e.issue)need(status===422&&exact(e.issue,['code','line','category'])&&issueCodes.includes(e.issue.code)&&integer(e.issue.line,0,500000)&&e.issue.category===(['UTF8_BOM','NON_ASCII_FASTA','LONE_CR'].includes(e.issue.code)?'unsupported':'invalid_or_limit'));return v;
}
export function parseFastaJSON(raw){if(!(raw instanceof Uint8Array)||raw.length>FASTA_JSON_MAX)fail();return parsePreprocessJSON(raw);}
