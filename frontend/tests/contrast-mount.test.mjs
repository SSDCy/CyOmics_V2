import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {createPrivateContrastHost,mountPrivateContrastHost} from '../private-contrast-host.js';
import {first,second,clone,tick,deferred,until,dom,server,harness,key,defaultDraft,record} from './support/contrast-mount-setup.mjs';

const version=first.dto.datasetVersion.dataset_version_id;
const tree=d=>{const walk=n=>[n,...n.children.flatMap(walk)];return walk(d.root);};
const pcdAction=(d,action)=>tree(d).find(n=>n.attrs['data-pcd-action']===action);
const groupSelect=(d,id,field)=>tree(d).find(n=>n.attrs['data-pcd-id']===id&&n.attrs['data-pcd-field']===field);
function saved(f=first,family='Intensity',confirmed=false){
  const p=defaultDraft(f,family);p.rows.forEach((row,i)=>Object.assign(row,{group:i?'Treatment α':'Control β',condition:'explicit synthetic',control:i?'no':'yes',replicate:'r'+i,replicate_type:'biological',confirmed}));return record(p);
}
async function loaded(t,{confirmed=false,values=[first],backend=server(values)}={}){
  for(const f of values)backend.heads.set(key(f.dto.datasetVersion.dataset_version_id,'Intensity'),saved(f,'Intensity',confirmed));
  const h=await harness(t,{values,backend});await until(()=>values.every(f=>h.model.comparisonDesign(f.file.file_id)?.view().available));return h;
}
const addPair=c=>c.editor().addContrast({numerator_group:'Treatment α',denominator_group:'Control β'});

test('private TXT mounts one folded comparison below sample area after saved load; unconfirmed groups editable but blocked',async t=>{
  const h=await loaded(t);const c=h.model.comparisonDesign(first.file.file_id);assert.equal(c.view().design.state,'draft_incomplete');
  const nodes=tree(h.d),sample=nodes.findIndex(n=>n.className==='psdh'),comparison=nodes.findIndex(n=>n.className==='pcdh');assert.ok(comparison>sample);
  assert.equal(nodes.find(n=>n.className==='pcdh').open,false);assert.equal(nodes.filter(n=>n.tag==='input'&&n.type==='file').length,1);
  assert.equal(nodes.filter(n=>n.attrs['data-psd-action']==='save').length,1);assert.equal(nodes.filter(n=>n.attrs['data-pcd-action']==='save').length,0);
  pcdAction(h.d,'add').listeners.get('click')();const id=c.exportDraft().contrasts[0].contrast_id;
  for(const [field,value]of [['numerator_group','Treatment α'],['denominator_group','Control β']]){const n=groupSelect(h.d,id,field);n.value=value;n.listeners.get('change')();}
  assert.equal(c.view().design.state,'blocked_by_sample_design');assert.equal(c.view().analysis_ready,false);assert.match(h.d.root.textContent,/尚未全部人工确认/);
  assert.ok(c.exportDraft().contrasts.length===1);assert.equal(h.backend.posts().length,0);
});

test('without saved record or enabled service gives short save-first hint, no comparison upload/save/run controls',async t=>{
  for(const enabled of [true,false]){
    const h=await harness(t,{enabled});await until(()=>h.host().view().phase===(enabled?'empty':'not_connected'));
    const c=h.model.comparisonDesign(first.file.file_id);assert.equal(c.exportDraft(),null);assert.match(h.d.root.textContent,/请先保存样本信息/);assert.equal(pcdAction(h.d,'add'),undefined);
    assert.equal(tree(h.d).filter(n=>n.tag==='input'&&n.type==='file').length,1);assert.equal(h.backend.posts().length,0);h.cleanup();
  }
});

test('explicit sample save supplies source without a new comparison API; same source rerenders never rebind or clear',async t=>{
  const h=await harness(t);await until(()=>h.host().view().phase==='empty');const c=h.model.comparisonDesign(first.file.file_id);
  for(const [i,row]of h.host().view().design.rows.entries())h.host().editor().update(row.column_ordinal,{group:i?'Treatment α':'Control β',condition:'c',control:i?'no':'yes',replicate:String(i),replicate_type:'biological'});
  assert.equal(c.exportDraft(),null);assert.equal(await h.host().save(),true);await until(()=>c.view().available);const id=addPair(c),before=c.exportDraft(),epoch=c.view().design.source_epoch,count=h.backend.samples().length;
  c.setExpanded(true);h.remount(true);assert.deepEqual(c.exportDraft(),before);assert.equal(h.model.comparisonDesign(first.file.file_id),c);
  assert.equal(tree(h.d).find(n=>n.className==='pcdh').open,true);assert.match(h.d.root.textContent,/Comparison design \(draft\)/);
  await h.model.reload();h.remount(false);assert.equal(c.view().design.source_epoch,epoch);assert.deepEqual(c.exportDraft(),before);assert.equal(c.exportDraft().contrasts[0].contrast_id,id);
  assert.equal(h.backend.samples().length,count);assert.equal(h.backend.posts().length,1);assert.ok(h.backend.calls.every(call=>!call.path.includes('contrast')));
});

test('ordinary sample no-op, repeated getters, folding and language remount preserve draft with full canonical source clones',async t=>{
  const h=await loaded(t);const c=h.model.comparisonDesign(first.file.file_id);addPair(c);const before=c.exportDraft(),epoch=c.view().design.source_epoch,n=h.backend.calls.length;
  assert.equal(h.host().editor().update(0,{group:'Control β'}),false);
  for(let i=0;i<5;i++){c.view();c.exportDraft();h.host().view();}c.setExpanded(true);h.remount(true);c.setExpanded(false);h.remount(false);
  assert.deepEqual(c.exportDraft(),before);assert.equal(c.view().design.source_epoch,epoch);assert.equal(h.backend.calls.length,n);
});

test('sample dirty immediately clears comparisons and stale nodes; reverting text cannot resurrect old comparison',async t=>{
  const h=await loaded(t);const c=h.model.comparisonDesign(first.file.file_id);const id=addPair(c),old=groupSelect(h.d,id,'numerator_group'),callback=old.listeners.get('change');
  h.host().editor().update(0,{group:'changed'});assert.equal(c.exportDraft(),null);assert.equal(old.value,'');assert.equal(old.listeners.size,0);assert.equal(pcdAction(h.d,'add'),undefined);
  h.host().editor().update(0,{group:'Control β'});assert.equal(c.exportDraft(),null);old.value='Treatment α';callback();assert.equal(c.exportDraft(),null);
  assert.equal(await h.host().save(),true);await until(()=>c.view().available);assert.deepEqual(c.exportDraft().contrasts,[]);
});

test('confirmed source allows method selection only; duplicate/same direction errors still work inside mounted existing editor',async t=>{
  const h=await loaded(t,{confirmed:true});const c=h.model.comparisonDesign(first.file.file_id),id=addPair(c);assert.equal(c.view().design.state,'ready_for_method_selection');assert.equal(c.view().analysis_ready,false);
  assert.equal(c.editor().addContrast({numerator_group:'Treatment α',denominator_group:'Control β'}),null);
  assert.equal(c.editor().updateContrast(id,{denominator_group:'Treatment α'}),false);assert.match(h.d.root.textContent,/分子组和分母组不能相同/);
  assert.ok(c.editor().addContrast({numerator_group:'Control β',denominator_group:'Treatment α'}));assert.equal(c.exportDraft().contrasts.length,2);
  assert.ok(tree(h.d).filter(n=>n.attrs['data-pcd-action']).every(n=>['add','remove'].includes(n.attrs['data-pcd-action'])));
});

test('family switch clears old comparisons, does not revive on switch back, and renewed saved source starts empty',async t=>{
  const h=await loaded(t);const c=h.model.comparisonDesign(first.file.file_id);addPair(c);h.host().editor().setQuantFamily('LFQ intensity');assert.equal(c.exportDraft(),null);await until(()=>h.host().view().phase==='empty');
  h.host().editor().setQuantFamily('Intensity');assert.equal(c.exportDraft(),null);assert.equal(await h.host().load(),true);await until(()=>c.view().available);assert.deepEqual(c.exportDraft().contrasts,[]);
});

test('actual sample reload pending/late/logout and file removal clear private comparisons and never repopulate stale DOM',async t=>{
  for(const mode of ['remove','logout','dispose']){
    const h=await loaded(t);const c=h.model.comparisonDesign(first.file.file_id),id=addPair(c),retained=groupSelect(h.d,id,'denominator_group');const held=deferred();h.backend.setIntercept((path)=>path.includes('/sample-designs?')?held.promise:null);
    const pending=h.host().load();await until(()=>h.host().view().phase==='loading');assert.equal(c.exportDraft(),null);assert.equal(retained.value,'');
    if(mode==='remove'){h.backend.setItems([]);await h.model.reload();}else if(mode==='logout'){h.backend.lose();c.view();h.model.dispose();}else h.cleanup();
    held.resolve(Response.json(h.backend.envelope(first.dto.datasetVersion.dataset_version_id,'Intensity',saved())));await pending;
    assert.equal(c.exportDraft(),null);assert.equal(c.view().disposed,true);assert.doesNotMatch(h.d.root.textContent,/Treatment α|Control β/);h.cleanup();
  }
});

test('complete raw source fingerprint preserves key order changes but clears on record/source changes; malformed binding stays unavailable',async t=>{
  const h=await loaded(t);let value=h.host().comparisonSource();const listeners=new Set(),provider={comparisonSource:()=>clone(value),subscribe(fn){listeners.add(fn);return()=>listeners.delete(fn);}};
  const c=createPrivateContrastHost({sampleHost:provider});t.after(()=>c.dispose());await until(()=>c.view().available);addPair(c);const before=c.exportDraft(),epoch=c.view().design.source_epoch;
  value=Object.fromEntries(Object.entries(value).reverse());for(const fn of listeners)fn();assert.deepEqual(c.exportDraft(),before);assert.equal(c.view().design.source_epoch,epoch);
  value=clone(value);value.sample_design_record={...value.sample_design_record,sample_design_version_id:'77777777-7777-4777-8777-777777777777'};for(const fn of listeners)fn();await until(()=>c.view().available);assert.deepEqual(c.exportDraft().contrasts,[]);
  value=clone(value);value.sample_columns[0].name_raw+='wrong';for(const fn of listeners)fn();await until(()=>c.view().design.state==='invalid');assert.equal(c.view().available,false);assert.equal(c.exportDraft(),null);
});

test('late asynchronous comparison hash cannot publish after actual sample edit or source withdrawal',async t=>{
  const h=await loaded(t);const held=deferred();const crypto={randomUUID:()=>webcrypto.randomUUID(),subtle:{digest:(...args)=>held.promise.then(()=>webcrypto.subtle.digest(...args))}};
  const c=createPrivateContrastHost({sampleHost:h.host(),cryptoImpl:crypto});t.after(()=>c.dispose());const d=dom(),stop=mountPrivateContrastHost(d.root,c);t.after(stop);
  assert.equal(c.view().design.source_checking,true);h.host().editor().update(0,{condition:'dirty while hashing'});held.resolve();await tick();await tick();
  assert.equal(c.exportDraft(),null);assert.equal(c.view().available,false);assert.match(d.root.textContent,/请先保存样本信息/);assert.equal(pcdAction(d,'add'),undefined);
});

test('per-file caches stay separate; removing one leaves the other draft; DOM unmount wipes listeners but cached model survives',async t=>{
  const h=await loaded(t,{values:[first,second]});const a=h.model.comparisonDesign(first.file.file_id),b=h.model.comparisonDesign(second.file.file_id);const aid=addPair(a),bid=addPair(b);
  const old=groupSelect(h.d,aid,'denominator_group'),handler=old.listeners.get('change');h.remount();assert.equal(old.value,'');assert.equal(old.listeners.size,0);handler();assert.equal(a.exportDraft().contrasts.length,1);
  h.backend.setItems([second.file]);await h.model.reload();assert.equal(a.exportDraft(),null);assert.equal(a.view().disposed,true);assert.equal(b.exportDraft().contrasts[0].contrast_id,bid);
});
