import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {mountPreprocessParameters} from '../preprocess-parameters.js';
import {dom, deferred, flush} from './support/preprocess-parameters-dom.mjs';

const IDS={project_id:'11111111-1111-4111-8111-111111111111',dataset_version_id:'22222222-2222-5222-8222-222222222222',file_id:'33333333-3333-4333-8333-333333333333'};
const OTHER='44444444-4444-4444-8444-444444444444';
const source=()=>({contextKey:'synthetic-owner:project:dataset:revision-1',datasetVersion:{...IDS},availableQuantFamilies:['Intensity','LFQ intensity']});
const base=()=>({featureEnabled:true,source:source(),onConfirm(){}});
const referenceBytes=await readFile(new URL('./fixtures/preprocess-parameters.reference.json',import.meta.url));
const reference=JSON.parse(referenceBytes.toString('utf8'));
function setup(p=base()){
  const d=dom(),ui=mountPreprocessParameters(d.root,p);
  const field=()=>d.by('data-qpp-field','family'),flag=key=>d.by('data-qpp-flag',key),button=()=>d.by('data-qpp-action','confirm');
  return{d,ui,field,flag,button,text:()=>d.root.textContent,select(value){field().value=value;field().fire('change');},toggle(key,value){flag(key).checked=value;flag(key).fire('change');}};
}

test('reference is the exact Root contract and only boolean filter_flags are variable',()=>{
  assert.equal(createHash('sha256').update(referenceBytes).digest('hex'),'c84e77dc7c9910238356add0f78e31874ebfa3b20ad6175dae59439c3c806e07');
  assert.deepEqual(reference.filter_flags,{contaminant:true,reverse:true,site_only:true});
});
test('default off and nonboolean feature values create no DOM or callback',()=>{
  for(const enabled of [undefined,false,1,'true']){const d=dom();let calls=0;const ui=mountPreprocessParameters(d.root,{...base(),featureEnabled:enabled,onConfirm(){calls++;}});assert.equal(d.created.length,1);assert.equal(d.root.children.length,0);assert.equal(d.listenerCount(),0);assert.equal(calls,0);ui.dispose();}
});
test('no validated source means waiting, disabled controls, and no fake confirmation',()=>{
  let calls=0;const h=setup({featureEnabled:true,onConfirm(){calls++;}});assert.match(h.text(),/等待提供已验证的数据源/);assert.equal(h.field().disabled,true);assert.equal(h.button().disabled,true);h.button().fire('click');assert.equal(calls,0);assert.doesNotMatch(h.text(),/本页参数已确认/);
});
test('family is a single explicit choice and all three filters start false',()=>{
  const h=setup();assert.equal(h.field().tag,'select');assert.notEqual(h.field().multiple,true);assert.deepEqual(h.field().children.map(n=>n.value),['','Intensity','LFQ intensity']);assert.equal(h.field().value,'');assert.equal(h.button().textContent,'确认预处理参数');assert.equal(h.button().disabled,true);for(const key of ['contaminant','reverse','site_only'])assert.equal(h.flag(key).checked,false);assert.match(h.text(),/R base::log2/);assert.match(h.text(),/分别留痕/);assert.match(h.text(),/不做标准化，不填补/);
});
test('single available LFQ family is not auto-selected or expanded into other methods',()=>{
  const p=base();p.source.availableQuantFamilies=['LFQ intensity'];const h=setup(p);assert.equal(h.field().value,'');assert.deepEqual(h.field().children.map(n=>n.value),['','LFQ intensity']);h.select('Intensity');assert.equal(h.field().value,'');h.select('LFQ intensity');assert.equal(h.field().value,'LFQ intensity');assert.equal(h.button().disabled,false);
});
test('missing callback disables confirmation even after valid choices; event dispatch cannot bypass it',async()=>{
  const p=base();p.onConfirm=null;const h=setup(p);h.select('Intensity');h.toggle('reverse',true);assert.equal(h.button().disabled,true);assert.match(h.text(),/尚未接入/);h.button().fire('click');await flush();assert.doesNotMatch(h.text(),/本页参数已确认/);
});
test('no available families has an explicit explanation and does not guess a family',()=>{
  const p=base();p.source.availableQuantFamilies=[];const h=setup(p);assert.match(h.text(),/没有可用定量类型/);assert.equal(h.button().disabled,true);assert.equal(h.field().disabled,true);assert.equal(h.field().children.length,1);
});
for(const families of [['LFQ'],['intensity'],['Normalized'],['Intensity','Intensity'],['Intensity','LFQ intensity','extra'],null,'Intensity'])test('invalid family availability rejects the source: '+JSON.stringify(families),()=>{
  const p=base();p.source.availableQuantFamilies=families;const h=setup(p);assert.equal(h.button().disabled,true);assert.equal(h.field().disabled,true);assert.match(h.text(),/等待提供已验证的数据源/);
});
for(const patch of [{contextKey:''},{contextKey:' '},{contextKey:'a\n'},{datasetVersion:null},{datasetVersion:{...IDS,dataset_version_id:'bad-id'}}])test('invalid source identity/context blocks confirmation: '+JSON.stringify(patch),()=>{
  const p=base();p.source={...p.source,...patch};const h=setup(p);h.button().fire('click');assert.equal(h.button().disabled,true);assert.equal(h.field().disabled,true);
});
for(let mask=0;mask<8;mask++)test('all boolean flag combinations preserve the exact fixed contract: '+mask,async()=>{
  const p=base(),calls=[];p.onConfirm=request=>calls.push(request);const before=JSON.stringify(p.source),h=setup(p);h.select(mask%2?'LFQ intensity':'Intensity');const selected={contaminant:!!(mask&1),reverse:!!(mask&2),site_only:!!(mask&4)};for(const [key,value]of Object.entries(selected))h.toggle(key,value);h.button().click();await flush();assert.equal(calls.length,1);const r=calls[0];assert.deepEqual(Object.keys(r).sort(),['contextKey','parameters','quant_family','source']);assert.deepEqual(r.source,IDS);assert.equal(r.contextKey,p.source.contextKey);assert.equal(r.quant_family,mask%2?'LFQ intensity':'Intensity');assert.deepEqual(r.parameters,{...reference,filter_flags:selected});for(const value of [r,r.source,r.parameters,r.parameters.filter_flags,r.parameters.steps])assert.ok(Object.isFrozen(value));assert.throws(()=>{r.parameters.normalization='quantile';},TypeError);assert.equal(JSON.stringify(p.source),before);assert.match(h.text(),/本页参数已确认；未提交或运行任务/);assert.equal(h.button().disabled,true);
});
test('injected unimplemented methods or knobs cannot override the fixed preset',async()=>{
  const p={...base(),parameters:{...reference,pseudocount:1,normalization:'quantile',imputation:'mean'},normalization:'zscore',seed:123,flags:{reverse:true}};let sent;p.onConfirm=request=>{sent=request;};const h=setup(p);h.select('Intensity');h.button().click();await flush();assert.deepEqual(sent.parameters,{...reference,filter_flags:{contaminant:false,reverse:false,site_only:false}});assert.equal(h.d.all().filter(n=>n.tag==='select').length,1);assert.equal(h.d.all().filter(n=>n.tag==='input').length,3);
});
test('pending handoff prevents duplicate clicks but never presents a submitted/running/succeeded job',async()=>{
  const held=deferred(),p=base();let calls=0;p.onConfirm=()=>{calls++;return held.promise;};const h=setup(p);h.select('Intensity');h.button().fire('click');h.button().fire('click');assert.equal(calls,1);assert.equal(h.button().disabled,true);assert.equal(h.button().textContent,'确认预处理参数');assert.match(h.text(),/正在回传参数；未提交或运行任务/);held.resolve({status:'succeeded',job_id:'FAKE_JOB_SHOULD_NOT_SHOW'});await flush();assert.match(h.text(),/本页参数已确认/);assert.doesNotMatch(h.text(),/FAKE_JOB|succeeded|任务成功/);
});
test('each filter change invalidates confirmation; returning to the old value does not revive it',async()=>{
  for(const key of ['contaminant','reverse','site_only']){const p=base(),h=setup(p);h.select('Intensity');h.button().click();await flush();assert.match(h.text(),/本页参数已确认/);h.toggle(key,true);assert.doesNotMatch(h.text(),/本页参数已确认/);assert.equal(h.button().disabled,false);h.toggle(key,false);assert.doesNotMatch(h.text(),/本页参数已确认/);assert.equal(h.button().disabled,false);}
});
test('family change invalidates confirmation without auto-confirming when changed back',async()=>{
  const h=setup();h.select('Intensity');h.button().click();await flush();h.select('LFQ intensity');assert.equal(h.button().disabled,false);assert.doesNotMatch(h.text(),/本页参数已确认/);h.select('Intensity');assert.doesNotMatch(h.text(),/本页参数已确认/);assert.equal(h.button().disabled,false);
});
test('spurious unchanged native change events do not discard a valid page confirmation',async()=>{
  const h=setup();h.select('Intensity');h.button().click();await flush();h.select('Intensity');h.toggle('reverse',false);assert.match(h.text(),/本页参数已确认/);assert.equal(h.button().disabled,true);
});
for(const mode of ['owner','project','version','file','families'])test('source '+mode+' change clears choices and fences old pending confirmation',async()=>{
  const held=deferred(),p=base();p.onConfirm=()=>held.promise;const h=setup(p);h.select('Intensity');h.toggle('reverse',true);h.button().click();const s=source();if(mode==='owner')s.contextKey='synthetic-other-owner:revision-2';if(mode==='project')s.datasetVersion.project_id=OTHER;if(mode==='version')s.datasetVersion.dataset_version_id=OTHER;if(mode==='file')s.datasetVersion.file_id=OTHER;if(mode==='families')s.availableQuantFamilies=['LFQ intensity'];h.ui.update({...p,source:s});assert.equal(h.field().value,'');for(const key of ['contaminant','reverse','site_only'])assert.equal(h.flag(key).checked,false);held.resolve();await flush();assert.doesNotMatch(h.text(),/本页参数已确认/);assert.equal(h.button().disabled,true);
});
test('a late old result cannot confirm a new in-flight parameter choice',async()=>{
  const a=deferred(),b=deferred(),p=base();let calls=0;p.onConfirm=()=>++calls===1?a.promise:b.promise;const h=setup(p);h.select('Intensity');h.button().click();h.toggle('contaminant',true);h.button().click();a.resolve();await flush();assert.match(h.text(),/正在回传参数/);assert.doesNotMatch(h.text(),/本页参数已确认/);b.resolve();await flush();assert.match(h.text(),/本页参数已确认/);assert.equal(calls,2);
});
test('late rejection after a parameter/source change cannot pollute the current explanation',async()=>{
  for(const mode of ['parameter','source']){const held=deferred(),p=base();p.onConfirm=()=>held.promise;const h=setup(p);h.select('Intensity');h.button().click();if(mode==='parameter')h.toggle('site_only',true);else h.ui.update({...p,source:{...source(),contextKey:'synthetic-new-context'}});held.reject(Error('PRIVATE_OLD_FAILURE'));await flush();assert.doesNotMatch(h.text(),/PRIVATE_OLD_FAILURE|参数回传未能确认/);}
});
test('same source and language rerender preserve settings/confirmation without callbacks',async()=>{
  const p=base();let calls=0;p.onConfirm=()=>calls++;const h=setup(p);h.select('Intensity');h.toggle('reverse',true);h.button().click();await flush();h.ui.update({...p,lang:'en',source:{...source(),availableQuantFamilies:['LFQ intensity','Intensity']}});assert.equal(h.field().value,'Intensity');assert.equal(h.flag('reverse').checked,true);assert.match(h.text(),/Parameters confirmed on this page; no task has been submitted or run/);assert.equal(h.button().textContent,'Confirm preprocessing parameters');assert.equal(calls,1);
});
test('removing or replacing the callback revokes pending confirmation while preserving choices',async()=>{
  const held=deferred(),p=base();p.onConfirm=()=>held.promise;const h=setup(p);h.select('LFQ intensity');h.toggle('reverse',true);h.button().click();h.ui.update({...p,onConfirm:null});held.resolve();await flush();assert.equal(h.field().value,'LFQ intensity');assert.equal(h.flag('reverse').checked,true);assert.match(h.text(),/尚未接入/);assert.doesNotMatch(h.text(),/本页参数已确认/);h.ui.update({...p,onConfirm(){}});assert.equal(h.button().disabled,false);assert.doesNotMatch(h.text(),/本页参数已确认/);
});
test('errors are generic, do not leak details, and require an explicit manual retry',async()=>{
  for(const asyncFailure of [false,true]){const p=base();let calls=0;p.onConfirm=()=>{calls++;if(asyncFailure)return Promise.reject(Error('PRIVATE_CALLBACK_DETAIL'));throw Error('PRIVATE_CALLBACK_DETAIL');};const h=setup(p);h.select('Intensity');h.button().click();await flush();assert.equal(calls,1);assert.match(h.text(),/不会自动重试/);assert.doesNotMatch(h.text(),/PRIVATE_CALLBACK_DETAIL|本页参数已确认/);assert.equal(h.button().disabled,false);await flush();assert.equal(calls,1);h.button().click();await flush();assert.equal(calls,2);}
});
test('reentrant click cannot duplicate a confirmation and reentrant context change cannot be confirmed late',async()=>{
  const p=base();let h,calls=0;p.onConfirm=()=>{calls++;h.button().fire('click');};h=setup(p);h.select('Intensity');h.button().click();await flush();assert.equal(calls,1);
  const q=base();let other;q.onConfirm=()=>{other.ui.update({...q,source:{...source(),contextKey:'synthetic-context-2'}});throw Error('PRIVATE_REENTRANT');};other=setup(q);other.select('Intensity');other.button().click();await flush();assert.equal(other.field().value,'');assert.doesNotMatch(other.text(),/本页参数已确认|PRIVATE_REENTRANT|参数回传未能确认/);
});
test('native controls have labels, single-select semantics, status announcements and no hidden key trap',()=>{
  const h=setup();assert.equal(h.field().parentNode.tag,'label');assert.ok(h.field().attrs['aria-label']);for(const key of ['contaminant','reverse','site_only']){assert.equal(h.flag(key).type,'checkbox');assert.equal(h.flag(key).parentNode.tag,'label');assert.ok(h.flag(key).attrs['aria-label']);}assert.equal(h.button().tag,'button');assert.equal(h.button().type,'button');assert.equal(h.d.all().some(n=>n.attrs.role==='status'&&n.attrs['aria-live']==='polite'),true);assert.equal(h.d.all().some(n=>n.listeners.has('keydown')||n.attrs.tabindex==='-1'),false);assert.equal(h.d.all().some(n=>n.type==='file'),false);assert.equal(h.d.all().some(n=>n.tag==='a'||n.tag==='form'),false);
});
test('the host upload stays untouched and private context is not rendered',()=>{
  const d=dom(),upload=d.doc.createElement('input');upload.type='file';upload.value='HOST_UPLOAD_UNCHANGED';d.root.append(upload);const p=base();p.source.contextKey='PRIVATE_CONTEXT_<img>';const ui=mountPreprocessParameters(d.root,p);assert.equal(upload.value,'HOST_UPLOAD_UNCHANGED');assert.equal(d.all().filter(n=>n.type==='file').length,1);assert.doesNotMatch(d.root.textContent,/PRIVATE_CONTEXT|<img>/);assert.equal(d.all().some(n=>['img','script','iframe'].includes(n.tag)),false);ui.dispose();assert.equal(d.root.children.length,1);assert.equal(upload.value,'HOST_UPLOAD_UNCHANGED');
});
test('disable/dispose wipes current private nodes and late callback cannot remount or confirm',async()=>{
  for(const mode of ['disable','dispose']){const held=deferred(),p=base();p.onConfirm=()=>held.promise;const h=setup(p);h.select('Intensity');h.toggle('reverse',true);h.button().click();const retained=h.d.all().slice(1);if(mode==='disable')h.ui.update({...p,featureEnabled:false});else h.ui.dispose();assert.equal(h.d.root.children.length,0);assert.equal(h.d.listenerCount(),0);assert.ok(retained.every(n=>n.textContent===''));held.resolve();await flush();assert.equal(h.d.root.children.length,0);if(mode==='dispose'){assert.equal(h.ui.update(p),false);assert.equal(h.ui.dispose(),false);}else{h.ui.update(p);assert.equal(h.field().value,'');assert.equal(h.flag('reverse').checked,false);assert.doesNotMatch(h.text(),/本页参数已确认/);}}
});
test('full update does not accidentally inherit the prior source or callback',()=>{
  const p=base(),h=setup(p);h.select('Intensity');h.toggle('reverse',true);h.ui.update({featureEnabled:true});assert.equal(h.field().value,'');assert.equal(h.flag('reverse').checked,false);assert.equal(h.button().disabled,true);assert.doesNotMatch(h.text(),new RegExp(IDS.dataset_version_id));
});
