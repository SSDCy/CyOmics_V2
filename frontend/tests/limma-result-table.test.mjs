// Pure presentation checks. The pinned native outputs are SYNTHETIC, not research data.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash,webcrypto} from 'node:crypto';
import {dom,deferred,flush} from './support/preprocess-parameters-dom.mjs';
import {mountLimmaResultTable,LIMMA_RESULT_ARTIFACTS} from '../limma-result-table.js';
const fixture=new URL('./fixtures/limma-results-native-synthetic/',import.meta.url),provenance=JSON.parse(await readFile(new URL('provenance.json',fixture),'utf8'));
const originals=await Promise.all(provenance.artifacts.map(async p=>({...p,body:new Uint8Array(await readFile(new URL(p.name,fixture)))})));
const id=n=>String(n).padStart(8,'0')+'-1111-4111-8111-111111111111',clone=structuredClone;
const rows=[[1,'SYN_DUP'],[2,'SYN_DOWN'],[4,'SYN_UNCHANGED'],[5,'SYN_MISSING'],[8,'SYN_DUP'],[9,'SYN_MULTI_A;SYN_MULTI_B'],[12,'SYN_PARTIAL']].map(([source_row_ordinal,protein_ids_raw])=>({source_row_ordinal,protein_ids_raw}));
const context=()=>({project_id:id(1),job_id:id(2),binding_sha256:'b'.repeat(64),provenance:'synthetic_engineering_verification',
 source:{file_id:id(3),parent_dataset_version_id:id(4),source_sha256:'a'.repeat(64),filename:'synthetic-engineering-only.txt'},
 selection:{processed_dataset_version_id:id(5),sample_design_version_id:id(6),sample_design_request_id:id(7),replicate_model:'independent_biological',
 contrast:{schemaVersion:1,dataset_version_id:id(4),sample_design_version_id:id(6),quant_family:'LFQ intensity',contrasts:[{contrast_id:'c_reverse',numerator_group:'外层原分子组 β',denominator_group:'外层原分母组 α'}]}}});
function options(extra={}){return{verifiedOwnerContext:context(),contextSignal:new AbortController().signal,isCurrent:()=>true,artifacts:clone(originals),rows:clone(rows),cryptoImpl:webcrypto,pageSize:5,...extra};}
async function loaded(t,extra={}){const d=dom(),o=options(extra),c=mountLimmaResultTable(d.root,o);t.after(()=>c.dispose());assert.equal(await c.ready,true);return{d,o,c};}
const bodyRows=d=>d.all().filter(n=>Object.hasOwn(n.attrs,'data-lrt-source-row'));
const row=(d,id)=>bodyRows(d).find(n=>n.attrs['data-lrt-source-row']===String(id));
const cell=(d,id,key)=>row(d,id)?.children.find(n=>n.attrs['data-lrt-cell']===key)?.textContent;
function replaceFile(artifacts,name,change){const a=artifacts.find(a=>a.name===name),text=new TextDecoder().decode(a.body),next=change(text);a.body=new TextEncoder().encode(next);a.bytes=a.body.byteLength;a.sha256=createHash('sha256').update(a.body).digest('hex');}

test('pinned actual R synthetic artifacts render original direction/numeric tokens, exact source and honest provenance',async t=>{
 assert.equal(provenance.classification,'synthetic_engineering_verification');assert.equal(provenance.research_results,false);assert.deepEqual([...LIMMA_RESULT_ARTIFACTS].sort(),originals.map(a=>a.name).sort());
 for(const a of originals){assert.equal(a.body.byteLength,a.bytes);assert.equal(createHash('sha256').update(a.body).digest('hex'),a.sha256);}
 const {d,o,c}=await loaded(t);assert.equal(c.view().row_count,7);assert.equal(cell(d,1,'log2FC'),'-3');assert.equal(cell(d,2,'FC'),'7.99999999999999');assert.equal(cell(d,1,'P.Value'),'0.00463687301429682');assert.equal(cell(d,1,'adj.P.Val'),'0.0139106190428905');
 const pair=o.verifiedOwnerContext.selection.contrast.contrasts[0];assert.equal(cell(d,1,'direction'),pair.numerator_group+' − '+pair.denominator_group);assert.doesNotMatch(d.by('data-lrt-field','direction').textContent,/\bg[12]\b/);assert.ok(d.root.textContent.includes(o.verifiedOwnerContext.selection.processed_dataset_version_id));assert.match(d.by('data-lrt-field','method').textContent,/R 4.6.1.*limma 3.68.5.*none/);assert.match(d.by('data-lrt-provenance','synthetic_engineering_verification').textContent,/不是用户研究结果/);assert.equal(bodyRows(d).length,5);
});
test('search and pagination retain repeated/semicolon IDs, source order and not_testable NA; no NS or threshold classification',async t=>{
 const {d,c}=await loaded(t),search=d.by('data-lrt-control','search'),status=d.by('data-lrt-control','status');search.value='SYN_DUP';search.fire('input');assert.equal(c.view().filtered_count,2);assert.deepEqual(bodyRows(d).map(n=>n.attrs['data-lrt-source-row']),['1','8']);
 search.value='SYN_MULTI_B';search.fire('input');assert.equal(cell(d,9,'protein_ids_raw'),'SYN_MULTI_A;SYN_MULTI_B');search.value='';search.fire('input');d.by('data-lrt-control','next').click();assert.deepEqual(bodyRows(d).map(n=>n.attrs['data-lrt-source-row']),['9','12']);d.by('data-lrt-control','previous').click();assert.equal(bodyRows(d)[0].attrs['data-lrt-source-row'],'1');
 status.value='not_testable';status.fire('change');assert.equal(c.view().filtered_count,1);assert.equal(cell(d,5,'test_status'),'不可检验 (not_testable)');assert.equal(cell(d,5,'P.Value'),'不可检验（NA）');assert.equal(cell(d,5,'adj.P.Val'),'不可检验（NA）');assert.equal(cell(d,5,'n_obs_num'),'1');assert.equal(cell(d,5,'n_obs_den'),'3');assert.match(cell(d,5,'reason'),/insufficient_numerator_observations/);assert.ok(!row(d,5).textContent.includes('NS'));assert.ok(!row(d,5).textContent.includes('不显著'));
 status.value='testable';status.fire('change');assert.equal(c.view().filtered_count,6);assert.equal(row(d,5),undefined);search.value='does not exist';search.fire('input');assert.equal(c.view().filtered_count,0);assert.match(d.root.textContent,/统计结果未改变/);assert.equal(d.by('data-lrt-control','next').disabled,true);
});
test('ID whitespace/empty/markup remain literal; testable FC NA preserves FC_reason without numeric replacement',async t=>{
 const artifacts=clone(originals),lineage=clone(rows),raw=['  A ; B  ','','<img src=x onerror=alert(1)>; A'];lineage[0].protein_ids_raw=raw[0];lineage[1].protein_ids_raw=raw[1];lineage[2].protein_ids_raw=raw[2];
 replaceFile(artifacts,'results.tsv',text=>{const lines=text.split('\n');for(let i=0;i<3;i++){const cells=lines[i+1].split('\t');cells[1]=raw[i];if(i===0){cells[6]='NA';cells[7]='overflow';}lines[i+1]=cells.join('\t');}return lines.join('\n');});
 const {d}=await loaded(t,{artifacts,rows:lineage});assert.equal(cell(d,1,'protein_ids_raw'),raw[0]);assert.equal(cell(d,2,'protein_ids_raw'),'');assert.equal(cell(d,4,'protein_ids_raw'),raw[2]);assert.equal(d.all().filter(n=>n.tag==='img').length,0);assert.equal(cell(d,1,'FC'),'缺失（NA）');assert.match(cell(d,1,'FC_reason'),/overflow/);assert.equal(cell(d,1,'test_status'),'可检验 (testable)');assert.equal(cell(d,1,'log2FC'),'-3');
});
test('missing/altered artifacts, wrong lineage, contrast or formal direction fail closed and clear an earlier table',async t=>{
 const {d,c}=await loaded(t);
 for(const change of [o=>{o.artifacts.pop();},o=>{o.artifacts[0].body[0]^=1;},o=>{o.artifacts[1].bytes++;},o=>{o.rows[0].protein_ids_raw='wrong';},o=>{o.verifiedOwnerContext.selection.contrast.contrasts[0].contrast_id='different';},o=>{replaceFile(o.artifacts,'method-audit.tsv',s=>s.replace('numerator\tg1','numerator\tg2'));}]){
  const o=options();change(o);assert.equal(await c.update(o),false);assert.equal(c.view().row_count,0);assert.equal(c.view().phase,'invalid');assert.equal(bodyRows(d).length,0);assert.equal(d.all().filter(n=>Object.hasOwn(n.attrs,'data-lrt-download')).length,0);
 }
});
test('changed job/source/selection clears immediately and stale digest completion cannot restore the old table',async t=>{
 const d=dom(),held=deferred();let first=true;const slow={subtle:{digest(alg,bytes){if(first){first=false;return held.promise;}return webcrypto.subtle.digest(alg,bytes);}}},old=options({cryptoImpl:slow}),c=mountLimmaResultTable(d.root,old);t.after(()=>c.dispose());const initial=c.ready;
 const fresh=options();fresh.verifiedOwnerContext.job_id=id(99);fresh.verifiedOwnerContext.source.filename='new-owner-verified-source.txt';fresh.verifiedOwnerContext.selection.sample_design_request_id=id(88);const pending=c.update(fresh);assert.equal(bodyRows(d).length,0);assert.equal(c.view().phase,'verifying');assert.equal(await pending,true);assert.equal(c.view().job_id,id(99));held.resolve(await webcrypto.subtle.digest('SHA-256',old.artifacts[0].body));assert.equal(await initial,false);assert.equal(c.view().job_id,id(99));assert.ok(!d.root.textContent.includes(old.verifiedOwnerContext.job_id));assert.match(d.root.textContent,/new-owner-verified-source/);
});
test('Owner abort or in-place context/source/selection change disposes data and listeners; no automatic demo rendering',async t=>{
 const empty=dom(),off=mountLimmaResultTable(empty.root);assert.equal(await off.ready,false);assert.equal(empty.root.textContent,'');off.dispose();
 const authority=new AbortController(),a=await loaded(t,{contextSignal:authority.signal});authority.abort();assert.equal(a.c.view().phase,'disposed');assert.equal(a.d.root.textContent,'');assert.equal(a.d.listenerCount(),0);
 for(const mutate of [v=>{v.job_id=id(50);},v=>{v.source.source_sha256='d'.repeat(64);},v=>{v.selection.contrast.contrasts[0].numerator_group='changed';}]){const h=await loaded(t);mutate(h.o.verifiedOwnerContext);assert.equal(h.c.view().phase,'disposed');assert.equal(h.d.root.textContent,'');assert.equal(h.d.listenerCount(),0);}
});
test('five downloads use only supplied Owner callback/pins; unconfirmed/late returns never create a file or fake success',async t=>{
 const no=await loaded(t);assert.equal(no.d.all().filter(n=>Object.hasOwn(n.attrs,'data-lrt-download')&&n.disabled).length,5);
 const calls=[],h=await loaded(t,{onDownload:async info=>{calls.push(info);return{status:'handed_off'};}});
 for(const name of LIMMA_RESULT_ARTIFACTS){h.d.by('data-lrt-download',name).click();await flush();}
 assert.deepEqual(calls.map(c=>c.name),[...LIMMA_RESULT_ARTIFACTS]);for(const call of calls){const pin=originals.find(a=>a.name===call.name);assert.deepEqual(call.pin,{bytes:pin.bytes,sha256:pin.sha256});assert.equal(call.verifiedOwnerContext.job_id,h.o.verifiedOwnerContext.job_id);assert.equal(Object.hasOwn(call,'body'),false);assert.equal(call.signal.aborted,false);}
 assert.match(h.d.root.textContent,/请查看浏览器的下载状态/);
 const held=deferred(),oldCalls=[],o=options({onDownload:info=>{oldCalls.push(info);return held.promise;}});await h.c.update(o);h.d.by('data-lrt-download','results.tsv').click();const next=options({onDownload:async()=>undefined});next.verifiedOwnerContext.job_id=id(70);await h.c.update(next);assert.equal(oldCalls[0].signal.aborted,true);held.resolve({status:'handed_off'});await flush();assert.ok(!h.d.root.textContent.includes('已由授权通道交给浏览器保存'));h.d.by('data-lrt-download','results.tsv').click();await flush();assert.match(h.d.root.textContent,/下载尚未确认/);assert.equal(h.d.all().some(n=>n.tag==='a'),false);
});
test('component-scoped layout/DOM evidence preserves long labels and all12 columns with internal scroll and mobile controls',async t=>{
 const ctx=context();ctx.selection.contrast.contrasts[0].numerator_group='原始分子组：'+ 'long_label_β'.repeat(5);ctx.selection.contrast.contrasts[0].denominator_group='原始分母组：'+'control_α'.repeat(6);const {d}=await loaded(t,{verifiedOwnerContext:ctx});
 assert.equal(d.all().filter(n=>n.tag==='th'&&n.attrs.scope==='col').length,12);const scroll=d.all().find(n=>n.className==='lrt-scroll');assert.equal(scroll.tabIndex,0);assert.equal(scroll.attrs.role,'region');assert.ok(scroll.attrs['aria-label']);assert.ok(d.root.textContent.includes(ctx.selection.contrast.contrasts[0].numerator_group));assert.ok(d.root.textContent.includes(ctx.selection.contrast.contrasts[0].denominator_group));
 const css=await readFile(new URL('../limma-result-table.css',import.meta.url),'utf8');assert.match(css,/\.lrt-scroll\{[^}]*width:100%[^}]*min-width:0[^}]*max-width:100%[^}]*overflow-x:auto/);assert.match(css,/\.lrt-protein\{[^}]*white-space:pre-wrap/);assert.match(css,/@media\(max-width:520px\)/);assert.match(css,/\.lrt \.lrt-controls>label\{width:100%;flex:1 1 100%\}/);assert.match(css,/\.lrt \.lrt-downloads>button\{width:100%\}/);assert.doesNotMatch(css,/overflow(?:-x|-y)?\s*:\s*(?:hidden|clip)|text-overflow\s*:\s*ellipsis|(?:zoom|transform)\s*:/i);
 // Structural layout evidence only: this DOM model is not a browser geometry engine.
});
