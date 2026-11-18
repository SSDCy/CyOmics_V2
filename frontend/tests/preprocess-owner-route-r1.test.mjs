// Route identity and failure atomicity only; no actual backend/R execution.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createPreprocessOwnerClient,createPreprocessOwnerTransport} from '../preprocess-owner-client.js';
import {validatePreprocessTask} from '../preprocess-owner-wire.mjs';
import {first,full,params,queued,digest,webcrypto} from './support/preprocess-owner-setup.mjs';
import {saver,clock} from './support/raw-qc-download-setup.mjs';
const clone=v=>structuredClone(v),fixture=async name=>JSON.parse(await readFile(new URL('./fixtures/preprocess-v2-contract/fixtures/positive/'+name,import.meta.url),'utf8'));
const taskTemplate=await fixture('succeeded.json'),versionTemplate=await fixture('processed-version.json');
const idA='88888888-8888-4888-8888-888888888888',idB='66666666-6666-4666-8666-666666666666',wrongId='99999999-9999-4999-8999-999999999999';
const bodyOf=j=>({request_id:j.request_id,quant_family:j.quant_family,parameters:j.parameters});
function original(){return queued({request_id:'77777777-7777-4777-8777-777777777777',quant_family:'Intensity',parameters:params()});}
function complete(q){const j=clone(q);for(const k of ['status','attempt_id','started_at','finished_at','revision','runtime','result'])j[k]=clone(taskTemplate[k]);j.runtime.runtime_binding_sha256=digest({attempt_id:j.attempt_id,binding_sha256:j.binding_sha256,contract:'preprocessing_runtime_binding_v1',job_id:j.job_id,profile_sha256:j.runtime.profile_sha256,proposal_sha256:j.proposal_sha256,registration_id:j.runtime.registration_id});return j;}
function version(j){const v=clone(versionTemplate);Object.assign(v,{job_id:j.job_id,attempt_id:j.attempt_id,project_id:j.source.project_id,parent_dataset_version_id:j.source.dataset_version_id,file_id:j.source.file_id,source:clone(j.source),quant_family:j.quant_family,parameters:clone(j.parameters),steps:clone(j.parameters.steps),parameters_sha256:j.parameters_sha256,proposal_sha256:j.proposal_sha256,runtime:clone(j.runtime)});return v;}
function wrong(j){const v=clone(j);v.job_id=wrongId;if(v.runtime)v.runtime.runtime_binding_sha256=digest({attempt_id:v.attempt_id,binding_sha256:v.binding_sha256,contract:'preprocessing_runtime_binding_v1',job_id:v.job_id,profile_sha256:v.runtime.profile_sha256,proposal_sha256:v.proposal_sha256,registration_id:v.runtime.registration_id});return v;}
const snapshot=c=>clone({job_id:c.view().job_id,task:c.view().task,history:c.view().history,reference:c.reference(),version:c.view().version});
async function setup(t,{items=[original()]}={}){
  let mode='wrong';const calls=[],save=saver(),timers=clock(),storage={getItem:()=>null,setItem(){throw Error('synthetic storage unavailable');},removeItem(){}};
  const transport=createPreprocessOwnerTransport({featureEnabled:true,projectId:first.file.project_id,datasetVersionId:first.dto.datasetVersion.dataset_version_id,cryptoImpl:webcrypto,storage,isCurrent:()=>true,getOptions:signal=>({signal,credentials:'same-origin',cache:'no-store',redirect:'error',headers:{'X-Cyomics-Account-Context':'Q'.repeat(22)}}),fetchImpl(path,options){
    calls.push({path,method:options.method});const url=new URL(path,'https://synthetic.invalid');
    if(url.pathname.includes('/artifacts/'))throw Error('No binary request is allowed for rejected metadata');
    if(url.pathname.includes('/processed-dataset-versions/'))return Response.json(version(items.find(j=>j.status==='succeeded')));
    if(url.pathname.includes('/datasets/')&&!url.searchParams.has('request_id'))return Response.json({items,next_cursor:null});
    const j=url.searchParams.has('request_id')?items.find(j=>j.request_id===url.searchParams.get('request_id')):items.find(j=>j.job_id===url.pathname.split('/').at(-1));
    assert.ok(j,'fixture must resolve the requested original job');return Response.json(mode==='wrong'?wrong(j):j);
  }});
  const c=createPreprocessOwnerClient({featureEnabled:true,fullSource:full(first),transport,cryptoImpl:webcrypto,...save,...timers});t.after(()=>c.dispose());await c.ready;assert.equal(c.view().job_id,items[0].job_id);assert.equal(transport.recovery.load(await transport.scope()).length,0);
  return{c,calls,save,timers,valid(){mode='valid';}};
}
function assertUnchanged(h,before){assert.deepEqual(snapshot(h.c),before,'rejected route must preserve selected task, history, recovery reference and version');assert.equal(h.c.view().history.some(j=>j.job_id===wrongId),false);assert.equal(h.calls.filter(c=>c.method==='POST').length,0);}
test('R1: wrong job with no writable recovery cannot mutate queued selection, history, refs or cache',async t=>{
  const h=await setup(t),before=snapshot(h.c);assert.equal(await h.c.selectJob(idA),false);assertUnchanged(h,before);const count=h.calls.length;assert.equal(await h.c.selectJob(wrongId),false);assert.equal(h.calls.length,count,'wrong response was never inserted into selectable task cache');h.valid();assert.equal(await h.c.selectJob(idA),true);assert.deepEqual(snapshot(h.c),before);
});
test('R1: failed selection of another historical job preserves the previous successful version',async t=>{
  const a=complete(original()),b=original();b.job_id=idB;b.request_id='55555555-5555-4555-8555-555555555555';b.created_at='2026-11-18T00:59:00.000000Z';b.request_sha256=digest({project_id:b.source.project_id,dataset_version_id:b.source.dataset_version_id,request:bodyOf(b)});
  const h=await setup(t,{items:[a,b]}),before=snapshot(h.c);assert.ok(before.version);assert.equal(await h.c.selectJob(idB),false);assertUnchanged(h,before);const count=h.calls.length;assert.equal(await h.c.selectJob(wrongId),false);assert.equal(h.calls.length,count);h.valid();assert.equal(await h.c.selectJob(idB),true);assert.equal(h.c.view().job_id,idB);assert.equal(h.c.view().version,null);
});
test('R1: original-request lookup rejects another job and preserves committed state without storage',async t=>{
  const h=await setup(t,{items:[complete(original())]}),before=snapshot(h.c);assert.equal(await h.c.check(),false);assertUnchanged(h,before);assert.ok(h.calls.at(-1).path.endsWith('&request_id='+before.reference.request_id));h.valid();assert.equal(await h.c.check(),true);assert.deepEqual(snapshot(h.c),before);
});
test('R1: fresh download job GET rejects another well-formed job before activation or binary requests',async t=>{
  const h=await setup(t,{items:[complete(original())]}),before=snapshot(h.c),count=h.calls.length;assert.equal(await h.c.downloadArtifact('summary.tsv'),false);assertUnchanged(h,before);assert.equal(h.calls.length,count+1);assert.match(h.calls.at(-1).path,new RegExp('/preprocessing-jobs/'+idA+'\\?'));assert.equal(h.save.clicks.length,0);assert.equal(h.save.active.size,0);assert.equal(h.c.view().can_download,true);const after=h.calls.length;assert.equal(await h.c.selectJob(wrongId),false);assert.equal(h.calls.length,after);
});
test('R1: final frozen failed-null-runtime fixture is rejected without changing the wire implementation',async()=>{
  const j=JSON.parse(await readFile(new URL('./fixtures/preprocess-v2-contract/final-failed-state/failed-null-runtime-invalid.json',import.meta.url),'utf8'));assert.equal(j.status,'failed');assert.equal(j.runtime,null);await assert.rejects(validatePreprocessTask(j,{source:j.source,cryptoImpl:webcrypto}));
});
