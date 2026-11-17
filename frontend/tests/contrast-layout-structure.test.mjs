import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {first,until,harness,server,key,defaultDraft,record} from './support/contrast-mount-setup.mjs';

const groupNames=['Synthetic control – deliberately long exact source group name α β γ','Synthetic treatment – extended condition label for narrow screen δ ε'];
const has=(node,cls)=>(node.className||'').split(/\s+/).includes(cls);
async function loaded(t){
  const backend=server(),design=defaultDraft(first,'Intensity');
  design.rows.forEach((row,i)=>Object.assign(row,{group:groupNames[i],condition:'synthetic layout only',control:i?'no':'yes',replicate:'r'+i,replicate_type:'biological',confirmed:true}));
  backend.heads.set(key(first.dto.datasetVersion.dataset_version_id,'Intensity'),record(design));
  const h=await harness(t,{backend});await until(()=>h.model.comparisonDesign(first.file.file_id)?.view().available);return h;
}

test('actual uploader gives only its file row/info/download scoped layout hooks; one selectable upload and unchanged download listener',async t=>{
  const h=await loaded(t),rows=h.d.all().filter(n=>Object.hasOwn(n.attrs,'data-private-txt-file'));assert.equal(rows.length,1);
  assert.ok(has(rows[0],'list-row'));assert.equal(rows[0].children.length,2);
  const [info,download]=rows[0].children;assert.ok(has(info,'private-txt-file-info'));assert.equal(download.tag,'button');assert.ok(has(download,'private-txt-file-download'));
  assert.equal(download.type,'button');assert.equal(download.disabled,!h.model.canDownload(first.file.file_id));assert.equal(typeof download.listeners.get('click'),'function');
  assert.ok(info.children.some(n=>n.children.some(child=>has(child,'pcdh'))));
  assert.equal(h.d.all().filter(n=>n.tag==='input'&&n.type==='file').length,1);assert.equal(h.backend.posts().length,0);
});

test('long names remain exact options and complete direction text through English/fold/DOM remount; dirty still clears',async t=>{
  const h=await loaded(t),c=h.model.comparisonDesign(first.file.file_id),id=c.editor().addContrast({numerator_group:groupNames[1],denominator_group:groupNames[0]});
  c.setExpanded(true);h.remount(true);const epoch=c.view().design.source_epoch;
  const check=()=>{for(const name of groupNames){assert.ok(h.d.all().some(n=>n.tag==='option'&&n.value===name&&n.textContent===name));assert.ok(h.d.all().some(n=>has(n,'pcd-formula')&&n.textContent.includes(name)));}assert.equal(c.exportDraft().contrasts[0].contrast_id,id);};
  check();c.setExpanded(false);h.remount();c.setExpanded(true);h.remount(true);check();assert.equal(c.view().design.source_epoch,epoch);
  assert.equal(c.editor().addContrast({numerator_group:groupNames[1],denominator_group:groupNames[0]}),null);
  assert.ok(c.editor().addContrast({numerator_group:groupNames[0],denominator_group:groupNames[1]}));
  h.host().editor().update(0,{condition:'changed'});assert.equal(c.exportDraft(),null);assert.ok(!h.d.root.textContent.includes('FC = '+groupNames[1]));assert.equal(h.backend.posts().length,0);
});

test('layout contract bounds the flex item and panel, stacks mobile download, scrolls the table internally without clipping or zoom restrictions',async()=>{
  const css=await readFile(new URL('../private-contrast-host.css',import.meta.url),'utf8');
  const block=selector=>{const pattern=selector.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');return new RegExp(pattern+'\\{([^}]+)\\}').exec(css)?.[1]||'';};
  assert.match(block('.list-row[data-private-txt-file]>.private-txt-file-info'),/min-width:0/);assert.match(block('.list-row[data-private-txt-file]>.private-txt-file-info'),/width:0/);assert.match(block('.list-row[data-private-txt-file]>.private-txt-file-info'),/flex:1/);
  assert.match(block('.pcdh'),/width:100%/);assert.match(block('.pcdh'),/min-width:0/);assert.match(block('.pcdh,.pcdh *'),/box-sizing:border-box/);
  assert.match(block('.pcdh .pcd-source-scroll'),/overflow-x:auto/);assert.match(block('.pcdh .pcd-source-scroll'),/max-width:100%/);
  assert.match(block('.pcdh .pcd-list'),/grid-template-columns:minmax\(0,1fr\)/);assert.match(block('.pcdh .pcd-heading>*'),/overflow-wrap:anywhere/);
  assert.match(css,/@media\(max-width:520px\)\{\.list-row\[data-private-txt-file\]\{flex-direction:column\}\.list-row\[data-private-txt-file\]>\.private-txt-file-info\{width:100%\}/);
  for(const viewport of [320,390])assert.ok(viewport<=520,'Mobile rules apply; this assertion is structural, not a browser geometry measurement');
  assert.doesNotMatch(css,/overflow(?:-x|-y)?\s*:\s*(?:hidden|clip)|text-overflow\s*:\s*ellipsis|(?:zoom|transform)\s*:/i);
  const html=await readFile(new URL('../index.html',import.meta.url),'utf8');assert.doesNotMatch(html,/user-scalable\s*=\s*no|maximum-scale\s*=\s*1/);
});
