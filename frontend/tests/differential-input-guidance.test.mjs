// Only new navigation behavior; all identities, records and transport replies are synthetic.
// This DOM model verifies scoped focus/scroll calls, not browser geometry.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createQCArtifactScope} from '../raw-qc-client.js';
import {createRawTransport,createPrivateTxtController,mountPrivateTxt} from '../private-txt-upload.js';
import {first,second,projectId,server,key,defaultDraft,record} from './support/contrast-mount-setup.mjs';
import {params,queued,digest,webcrypto,dom,until,flush} from './support/preprocess-owner-setup.mjs';

const fixture=async name=>JSON.parse(await readFile(new URL('./fixtures/preprocess-v2-contract/fixtures/positive/'+name,import.meta.url),'utf8'));
const templateTask=await fixture('succeeded.json'),templateVersion=await fixture('processed-version.json');
const walk=n=>[n,...n.children.flatMap(walk)];
const by=(root,attr,value)=>walk(root).find(n=>n.attrs[attr]===value);
function processed(f,family,index){
  const id=`8800000${index}-8888-4888-8888-888888888888`,request=`7700000${index}-7777-4777-8777-777777777777`,version=`aa00000${index}-aaaa-4aaa-8aaa-aaaaaaaaaaaa`;
  const j=queued({request_id:request,quant_family:family,parameters:params()},{f,id});
  for(const k of ['status','attempt_id','started_at','finished_at','revision','runtime','result'])j[k]=structuredClone(templateTask[k]);
  j.result.processed_dataset_version_id=version;
  j.runtime.runtime_binding_sha256=digest({attempt_id:j.attempt_id,binding_sha256:j.binding_sha256,contract:'preprocessing_runtime_binding_v1',job_id:id,profile_sha256:j.runtime.profile_sha256,proposal_sha256:j.proposal_sha256,registration_id:j.runtime.registration_id});
  const v=structuredClone(templateVersion);Object.assign(v,{processed_dataset_version_id:version,job_id:id,attempt_id:j.attempt_id,project_id:j.source.project_id,parent_dataset_version_id:j.source.dataset_version_id,file_id:j.source.file_id,source:j.source,quant_family:family,parameters:j.parameters,steps:j.parameters.steps,parameters_sha256:j.parameters_sha256,proposal_sha256:j.proposal_sha256,runtime:j.runtime});return{j,v};
}
function focusDOM(){
  const d=dom(),create=d.doc.createElement.bind(d.doc);d.scrolled=[];
  d.doc.createElement=tag=>{
    const n=create(tag);
    n.querySelector=function(selector){
      const match=/^\[([^=]+)="([^"]+)"\]$/.exec(selector);
      assert.ok(match,'Only scoped attribute selectors are expected');
      return by(this,match[1],match[2])||null;
    };
    n.focus=function(options){assert.deepEqual(options,{preventScroll:true});d.doc.activeElement=this;};
    n.scrollIntoView=function(options){assert.deepEqual(options,{block:'center',behavior:'auto'});d.scrolled.push(this);};
    return n;
  };
  return d;
}
async function setup(t,{values=[first,second],family='Intensity',saved=true}={}){
  const b=server(values),bundles=values.map((f,i)=>processed(f,family,i));
  if(saved)for(const f of values){
    const design=defaultDraft(f,'Intensity');design.rows.forEach((r,i)=>Object.assign(r,{group:i?'Treatment':'Control',condition:'explicit synthetic condition',control:i?'no':'yes',replicate:'r'+i,replicate_type:'biological',confirmed:true}));
    b.heads.set(key(f.dto.datasetVersion.dataset_version_id,'Intensity'),record(design));
  }
  b.setIntercept((path,o)=>{
    if(path.includes('/processed-dataset-versions/'))return Response.json(bundles.find(x=>path.includes(x.v.processed_dataset_version_id)).v);
    if(path.includes('/preprocessing-jobs')){
      assert.equal(o.method,'GET');const url=new URL(path,'https://synthetic.invalid'),request=url.searchParams.get('request_id');
      if(path.includes('/datasets/')&&!request)return Response.json({items:bundles.filter(x=>path.includes(x.j.source.dataset_version_id)).map(x=>x.j),next_cursor:null});
      return Response.json(bundles.find(x=>path.includes(x.j.job_id)||request===x.j.request_id).j);
    }
  });
  let uuidCalls=0;const cryptoImpl={subtle:webcrypto.subtle,randomUUID(){uuidCalls++;return webcrypto.randomUUID();}};
  const transport=createRawTransport({projectId,fetchImpl:b.fetchImpl,isCurrent:b.isCurrent,sampleDesignsEnabled:true,preprocessingEnabled:true,preprocessingArtifactScope:createQCArtifactScope(),getOptions:signal=>({method:'GET',signal,credentials:'same-origin',cache:'no-store',redirect:'error',headers:{'X-Cyomics-Account-Context':'Q'.repeat(22)}})});
  const model=createPrivateTxtController({projectId,transport,cryptoImpl}),d=focusDOM(),stop=mountPrivateTxt(d.root,model);
  t.after(()=>{stop();model.dispose();});
  const row=f=>d.all().find(n=>n.attrs['data-private-txt-file']===''&&n.textContent.includes('file_id: '+f.file.file_id));
  const host=f=>model.sampleDesign(f.file.file_id),contrast=f=>model.comparisonDesign(f.file.file_id),prep=f=>model.preprocessing(f.file.file_id).diffPreparation;
  await until(()=>values.every(f=>['restored','empty'].includes(host(f)?.view().phase)&&by(row(f),'data-ppo-action','prepare-diff')?.disabled===false));
  if(saved)await until(()=>values.every(f=>contrast(f).view().available));
  await flush();
  return{b,model,d,stop,row,host,contrast,prep,uuidCalls:()=>uuidCalls,button(f,action){return by(row(f),'data-ppo-action',action);},prepare(f){this.button(f,'prepare-diff').click();}};
}
const snapshot=(h,f)=>({sample:h.host(f).editor().exportDraft(),sampleProof:h.host(f).comparisonSource(),sampleState:h.host(f).preparationState(),contrast:h.contrast(f).exportDraft(),contrastState:h.contrast(f).view().design,preparation:h.prep(f).view(),selection:h.model.diffSelection(f.file.file_id)});

test('navigation opens and focuses only the selected file’s existing sample and comparison controls',async t=>{
  const h=await setup(t);
  for(const f of [first,second]){
    h.contrast(f).editor().addContrast({numerator_group:'Treatment',denominator_group:'Control'});
    h.prepare(f);
    const choice=by(h.row(f),'data-ppo-diff-choice','independent-biological');assert.equal(choice.disabled,false);choice.checked=true;choice.fire('change');
    h.host(f).setExpanded(false);h.contrast(f).setExpanded(false);
  }
  await flush();const before=[snapshot(h,first),snapshot(h,second)],calls=h.b.calls.length,uuids=h.uuidCalls();
  h.button(second,'goto-sample').click();
  assert.equal(h.host(second).view().expanded,true);assert.equal(h.host(first).view().expanded,false);
  assert.equal(h.d.doc.activeElement,by(h.row(second),'data-psd-control','quant-family'));
  assert.notEqual(h.d.doc.activeElement,by(h.row(first),'data-psd-control','quant-family'));
  h.button(second,'goto-contrast').click();
  assert.equal(h.contrast(second).view().expanded,true);assert.equal(h.contrast(first).view().expanded,false);
  assert.equal(h.d.doc.activeElement,by(h.row(second),'data-pcd-field','numerator_group'));
  assert.equal(h.d.scrolled.length,2);
  await flush();assert.deepEqual([snapshot(h,first),snapshot(h,second)],before);
  assert.equal(h.b.calls.length,calls);assert.equal(h.uuidCalls(),uuids);
  h.stop();assert.equal(h.prep(second).navigate('sample'),false);assert.equal(h.prep(second).navigate('contrast'),false);
  assert.equal(h.d.scrolled.length,2);assert.equal(h.b.calls.length,calls);
  t.diagnostic('Correct second-file controls focused/scrolled; first-file hosts remain collapsed. Saved designs, contrast direction, independence and selection unchanged; navigation HTTP=0, UUID=0. Unmounted target rejected.');
});

test('LFQ guidance with unsaved Intensity inputs navigates without editing, saving, confirming or issuing HTTP',async t=>{
  const h=await setup(t,{values:[first],family:'LFQ intensity',saved:false});h.prepare(first);
  h.host(first).setExpanded(false);h.contrast(first).setExpanded(false);await flush();
  const note=by(h.row(first),'data-ppo-diff-field','family-guidance');assert.match(note.textContent,/LFQ intensity/);assert.match(note.textContent,/手动选择相同类型/);
  assert.equal(h.host(first).editor().view().quant_family,'Intensity');assert.equal(h.contrast(first).view().available,false);
  const before=snapshot(h,first),calls=h.b.calls.length,uuids=h.uuidCalls(),heads=structuredClone([...h.b.heads]);
  h.button(first,'goto-sample').click();assert.equal(h.d.doc.activeElement,by(h.row(first),'data-psd-control','quant-family'));
  h.button(first,'goto-contrast').click();assert.equal(h.d.doc.activeElement.tag,'summary');assert.equal(h.d.doc.activeElement.textContent,'比较设计（草稿）');
  await flush();assert.deepEqual(snapshot(h,first),before);assert.deepEqual([...h.b.heads],heads);
  assert.equal(h.host(first).view().expanded,true);assert.equal(h.contrast(first).view().expanded,true);
  assert.equal(h.b.calls.length,calls);assert.equal(h.uuidCalls(),uuids);assert.equal(h.b.posts().length,0);
  assert.equal(by(h.row(first),'data-ppo-diff-choice','independent-biological').checked,false);
  assert.equal(h.model.diffSelection(first.file.file_id),null);assert.equal(h.d.scrolled.length,2);
  t.diagnostic('Processed LFQ family named explicitly; original blank Intensity sample draft retained. Unavailable comparison opens to its existing save-first explanation. No default contrast, confirmation, save, analysis or additional HTTP.');
});
