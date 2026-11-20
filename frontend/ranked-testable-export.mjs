import {canonicalJSON,uuid} from './sample-design-contract.mjs';
export const RANKED_HEADER=Object.freeze('source_row_ordinal protein_ids_raw contrast_id test_status reason log2FC FC FC_reason AveExpr moderated_t df_residual df_total P.Value adj.P.Val n_obs_num n_obs_den n_fit_num n_fit_den'.split(' '));
const need=v=>{if(!v)throw Error('ranked_export_invalid');},hex=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v),finite=v=>/^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$/.test(v)&&Number.isFinite(Number(v));
// Deterministic export of original statistics, not fitting, gene mapping or GSEA.
export function rankedTestableExport({bytes,rows,source,context}){
 need(bytes instanceof Uint8Array&&bytes.length>0&&bytes.length<=26214400&&bytes.at(-1)===10&&!bytes.includes(13)&&!bytes.includes(0)&&!(bytes[0]===239&&bytes[1]===187&&bytes[2]===191));
 need(uuid(source?.result_id)&&hex(source.manifest_sha256)&&hex(source.results_sha256)&&Array.isArray(rows)&&rows.length>0&&rows.length<=10000);
 const pair=context?.selection?.contrast?.contrasts;need(Array.isArray(pair)&&pair.length===1);const p=pair[0];for(const k of ['contrast_id','numerator_group','denominator_group'])need(typeof p[k]==='string'&&p[k].length>0&&!/[\u0000-\u001f\u007f-\u009f]/.test(p[k]));
 const lines=new TextDecoder('utf-8',{fatal:true}).decode(bytes).split('\n');lines.pop();need(canonicalJSON(lines.shift().split('\t'))===canonicalJSON(RANKED_HEADER)&&lines.length===rows.length);let previous=0;const included=[];
 for(const [i,line] of lines.entries()){const a=line.split('\t'),r=rows[i];need(a.length===18&&Number.isSafeInteger(r.source_row_ordinal)&&r.source_row_ordinal>previous&&a[0]===String(r.source_row_ordinal)&&a[1]===r.protein_ids_raw&&a[2]===p.contrast_id&&['testable','not_testable'].includes(a[3]));previous=r.source_row_ordinal;if(a[3]==='testable'){need(finite(a[9]));included.push({line,ordinal:r.source_row_ordinal,t:Number(a[9])});}}
 included.sort((a,b)=>a.t===b.t?a.ordinal-b.ordinal:a.t>b.t?-1:1);
 const extra=['rank','source_result_id','source_manifest_sha256','source_results_sha256','numerator_group','denominator_group','ranking_rule'];
 const text=RANKED_HEADER.concat(extra).join('\t')+'\n'+included.map((r,i)=>r.line+'\t'+[i+1,source.result_id,source.manifest_sha256,source.results_sha256,p.numerator_group,p.denominator_group,'signed_moderated_t_desc_source_row_ordinal_asc'].join('\t')+'\n').join('');
 const out=new TextEncoder().encode(text);need(out.length<=33554432);return {bytes:out,count:included.length};
}
