import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
import {createPrivateProjectTransport} from '../private-projects.js';
import {createPrivateTxtController,mountPrivateTxt} from '../private-txt-upload.js';
import {SAMPLE_QC_HEADERS} from '../raw-qc-client.js';
import {createPrivateRawQCHost,mountPrivateRawQC} from '../private-raw-qc.js';
import {first,second,server,dom,until,tick,deferred,job,memory,webcrypto,digest} from './support/raw-qc-setup.mjs';
import {completed,binary} from './support/raw-qc-download-setup.mjs';

// Capture ONLY the real page's literal initialization statement. The whole app,
// its demo routes, localStorage and browser globals are never evaluated here.
const app = await readFile(new URL('../app.js',import.meta.url),'utf8');
const statements = app.split(/\r?\n/).filter(line=>line.startsWith('const privateProjectRoutes=createPrivateProjectRoutes('));
assert.equal(statements.length,1);
let captured;
runInNewContext(statements[0],{createPrivateProjectTransport(options){captured=Object.fromEntries(Object.entries(options));return{};},createPrivateProjectRoutes:options=>options},{timeout:200,contextCodeGeneration:{strings:false,wasm:false}});
const pageOptions=Object.freeze(captured);
const action=(h,name)=>h.d.all().find(n=>n.attrs['data-prqc-action']===name);
const cells=h=>{const scroll=h.d.all().find(n=>n.className==='prqc-sample-scroll'),walk=n=>[n,...n.children.flatMap(walk)];return scroll?walk(scroll).filter(n=>n.tag==='td'):[];};
const rows=h=>h.d.all().filter(n=>n.className==='prqc-artifact');
const press=async(h,name)=>{const button=action(h,name);assert.ok(button,'Missing '+name);assert.equal(button.disabled,false);button.listeners.get('click')();await tick();};
const quoted=value=>'"'+value.replaceAll('"','""')+'"';
const sampleBytes=family=>new TextEncoder().encode(SAMPLE_QC_HEADERS.map(quoted).join('\t')+'\n1\t'+quoted((family==='intensity'?'Intensity ':'LFQ intensity ')+'<img onerror=synthetic>')+'\t'+quoted('NA\t"合成样本"')+'\t3\t1\t1\t1\t0\t7.25\tNA\n');

async function mountedPage(t,{options=pageOptions,storage=memory(),values=[first],postMode='normal',postHeld=null,artifactHeld=null}={}){
  const backend=server(values),allCalls=[],jobs=new Map();let nonce='Q'.repeat(22),serial=0,currentMode=postMode;
  const fixtureFor=path=>values.find(f=>path.includes(f.dto.datasetVersion.dataset_version_id));
  const fetchImpl=(path,o)=>{
    allCalls.push({path,options:o});
    if(path==='/api/v1/me')return Response.json({synthetic:true},{headers:{'X-Cyomics-Account-Context':nonce}});
    if(!path.includes('/raw-qc-jobs'))return backend.fetchImpl(path,o);
    if(o.method==='POST'){
      const body=JSON.parse(o.body),f=fixtureFor(path),family=body.quant_family,id=String(81000000+(++serial))+'-1111-4111-8111-111111111111';assert.ok(f);
      const record={f,family,requestId:body.request_id,jobId:id,status:'queued',observation:'confirmed',tamper:null};jobs.set(id,record);
      if(postHeld&&serial===1)return postHeld.promise;
      if(currentMode==='unknown')return Response.json({error:{code:'raw_qc_submission_unknown'}},{status:504});
      if(currentMode==='denied')return new Response('synthetic-denied',{status:401});
      return Response.json(job(record),{status:202});
    }
    const found=/raw-qc-jobs\/([0-9a-f-]{36})/.exec(path),id=found?.[1];
    const record=id?jobs.get(id):[...jobs.values()].find(j=>path.includes(j.requestId));assert.ok(record,'Unexpected synthetic RawQC lookup');
    if(path.includes('/artifacts/'))return artifactHeld?artifactHeld.promise:binary(value(record),'sample-qc.tsv',sampleBytes(record.family));
    return Response.json(value(record));
  };
  function value(record){
    let v=record.status==='succeeded'?completed({...record,bodies:new Map([['sample-qc.tsv',sampleBytes(record.family)]])}):job(record);
    v.observation=record.observation;
    if(record.revision!==undefined)v.revision=record.revision;
    if(record.tamper)v=record.tamper(v);
    return v;
  }
  const projectTransport=createPrivateProjectTransport({...options,rawQcStorage:storage,fetchImpl});
  await projectTransport.me({signal:new AbortController().signal});
  const transport=projectTransport.rawFiles(first.file.project_id),model=createPrivateTxtController({projectId:first.file.project_id,transport,cryptoImpl:webcrypto});
  let d=dom(),stop=mountPrivateTxt(d.root,model);
  t.after(()=>{stop();model.dispose();projectTransport.forgetContext({confirmed:true});});
  await until(()=>values.every(f=>model.importReport(f.file.file_id)?.status().phase==='ready'));
  return{get d(){return d;},model,backend,allCalls,storage,projectTransport,transport,jobs,value,
    host:(id=first.file.file_id)=>model.rawQC(id),
    qcCalls:()=>allCalls.filter(c=>c.path.includes('/raw-qc-jobs')),
    posts:()=>allCalls.filter(c=>c.path.includes('/raw-qc-jobs')&&c.options.method==='POST'),
    record:()=>[...jobs.values()].at(-1),setMode(mode){currentMode=mode;},
    async ownerSwitch(){nonce='R'.repeat(22);await projectTransport.me({signal:new AbortController().signal});model.status();},
    remount(en=false){stop();d=dom();stop=mountPrivateTxt(d.root,model,en?(_,english)=>english:zh=>zh);}
  };
}
async function choose(h,family='intensity',id=first.file.file_id){const host=h.host(id);host.setExpanded(true);host.selectFamily(family);await until(()=>host.view().phase==='idle');return host;}
async function startClick(h){await press(h,'start');await until(()=>h.host().view().phase==='queued'||h.host().view().phase==='unknown'||h.host().view().disposed);}

test('real app initialization explicitly enables both existing QC switches without changing other project options',()=>{
  assert.deepEqual(pageOptions,{enabled:true,sampleDesignsEnabled:true,rawQcEnabled:true,rawQcArtifactsEnabled:true});
  assert.equal(Object.isFrozen(pageOptions),true);
});
test('actual app options reach the existing TXT/QC host with one uploader and no automatic submission',async t=>{
  const h=await mountedPage(t);await choose(h);assert.equal(h.qcCalls().length,0);assert.equal(h.host().view().phase,'idle');assert.equal(action(h,'start').disabled,false);assert.equal(h.host().view().task.artifact_downloads,true);
  assert.equal(h.d.all().filter(n=>n.tag==='input'&&n.type==='file').length,1);assert.ok(h.d.all().some(n=>n.className==='psdh'));assert.ok(h.d.all().some(n=>n.className==='pcdh'));
});
test('missing saved source stays waiting and cannot construct a QC request or enable start',async t=>{
  let calls=0;const d=dom(),host=createPrivateRawQCHost({projectId:first.file.project_id,fileId:first.file.file_id,reportModel:{validatedSource:()=>null,subscribe:()=>()=>{}},transportFactory(){calls++;throw Error('No saved source');}}),stop=mountPrivateRawQC(d.root,host);t.after(()=>{stop();host.dispose();});host.setExpanded(true);assert.equal(host.view().phase,'waiting');assert.match(d.root.textContent,/请先完成并保存导入报告/);assert.equal(d.all().find(n=>n.attrs['data-prqc-action']==='start').disabled,true);assert.equal(await host.start(),false);assert.equal(calls,0);
});
test('reusable project transport still defaults off when the page omits its explicit switches',async t=>{
  const h=await mountedPage(t,{options:{enabled:true,sampleDesignsEnabled:true}});h.host().setExpanded(true);h.host().selectFamily('intensity');assert.equal(h.host().view().phase,'not_connected');assert.equal(h.host().view().task.artifact_downloads,false);assert.equal(h.qcCalls().length,0);assert.equal(action(h,'start').hidden,true);
});
test('task opt-in alone cannot enable artifact preview without the second explicit switch',async t=>{
  const h=await mountedPage(t,{options:{enabled:true,sampleDesignsEnabled:true,rawQcEnabled:true}});await choose(h);await startClick(h);h.record().status='succeeded';await h.host().check();assert.equal(h.host().view().phase,'succeeded');assert.equal(rows(h).length,7);assert.equal(h.host().view().task.artifact_downloads,false);assert.equal(await h.host().previewSampleQC(),false);assert.equal(h.qcCalls().filter(c=>c.path.includes('/artifacts/')).length,0);
});
test('click submits one exact bound request through the fixed API, carrying only current account context',async t=>{
  const h=await mountedPage(t);await choose(h);await startClick(h);assert.equal(h.posts().length,1);const {path,options}=h.posts()[0],body=JSON.parse(options.body);
  assert.equal(path,'/api/v1/datasets/'+first.dto.datasetVersion.dataset_version_id+'/raw-qc-jobs?project_id='+first.file.project_id);
  assert.deepEqual(Object.keys(body).sort(),['params','quant_family','request_id','schemaVersion']);assert.equal(body.quant_family,'intensity');assert.deepEqual(body.params,{});assert.equal(options.credentials,'same-origin');assert.equal(options.cache,'no-store');assert.equal(options.redirect,'error');assert.equal(new Headers(options.headers).get('X-Cyomics-Account-Context'),'Q'.repeat(22));assert.equal(new Headers(options.headers).has('Authorization'),false);assert.equal(h.host().view().task.analysis_ready,false);
  action(h,'start').listeners.get('click')();await tick();assert.equal(h.posts().length,1);
});
test('UI follows confirmed queued/running/reconciliation-pending/succeeded states without inventing a result',async t=>{
  const h=await mountedPage(t);await choose(h);await startClick(h);assert.match(h.d.root.textContent,/排队中/);assert.equal(rows(h).length,0);h.record().status='running';await press(h,'check');await until(()=>h.host().view().phase==='running');assert.match(h.d.root.textContent,/运行中/);assert.equal(rows(h).length,0);
  h.record().observation='reconciliation_pending';h.record().revision=3;await h.host().check();assert.match(h.d.root.textContent,/任务状态核对中/);assert.equal(rows(h).length,0);h.record().observation='confirmed';h.record().status='succeeded';h.record().revision=4;await h.host().check();assert.equal(h.host().view().phase,'succeeded');assert.equal(h.host().view().task.observation,'confirmed');assert.match(h.d.root.textContent,/基础质控已完成/);assert.equal(rows(h).length,7);assert.equal(cells(h).length,0);assert.equal(h.posts().length,1);
});
test('failed backend job has no success or artifacts and is never automatically resubmitted',async t=>{
  const h=await mountedPage(t);await choose(h);await startClick(h);h.record().status='failed';await h.host().check();assert.equal(h.host().view().phase,'failed');assert.match(h.d.root.textContent,/基础质控未完成/);assert.equal(rows(h).length,0);assert.equal(cells(h).length,0);assert.equal(action(h,'start').disabled,true);await tick();assert.equal(h.posts().length,1);
});
test('uncertain submission stays unknown; only explicit GET reconciliation recovers the same request',async t=>{
  const h=await mountedPage(t,{postMode:'unknown'});await choose(h);await startClick(h);assert.equal(h.host().view().phase,'unknown');assert.match(h.d.root.textContent,/勿重复提交/);assert.equal(action(h,'start').disabled,true);action(h,'start').listeners.get('click')();await tick();assert.equal(h.posts().length,1);await press(h,'check');await until(()=>h.host().view().phase==='queued');assert.equal(h.posts().length,1);assert.ok(h.qcCalls().some(c=>c.options.method==='GET'&&c.path.includes('&request_id='+h.record().requestId)));
});
test('seven validated manifest entries precede explicit sample preview; all ten raw values are preserved',async t=>{
  const h=await mountedPage(t);await choose(h);await startClick(h);h.record().status='succeeded';await h.host().check();assert.deepEqual(rows(h).map(n=>n.children[2].textContent),['raw-matrix.tsv','numeric-matrix.tsv','blank-mask.tsv','zero-mask.tsv','sample-qc.tsv','summary.tsv','R-session-info.txt']);assert.equal(h.qcCalls().filter(c=>c.path.includes('/artifacts/')).length,0);await press(h,'preview-sample-qc');await until(()=>h.host().view().task.sample_qc_preview.phase==='ready');
  assert.deepEqual(cells(h).map(n=>n.textContent),['1','Intensity <img onerror=synthetic>','NA\t"合成样本"','3','1','1','1','0','7.25','NA']);assert.equal(h.d.all().some(n=>n.tag==='img'),false);assert.match(h.d.root.textContent,/未重新计算统计/);assert.equal(h.qcCalls().filter(c=>c.path.includes('/artifacts/')).length,1);assert.equal(h.posts().length,1);
});
test('incomplete or tampered manifest cannot enable artifacts or the sample preview',async t=>{
  const h=await mountedPage(t);await choose(h);await startClick(h);h.record().status='succeeded';h.record().tamper=j=>{j.result.artifacts.pop();j.result.manifest_sha256=digest(j.result.artifacts);return j;};assert.equal(await h.host().check(),false);assert.notEqual(h.host().view().phase,'succeeded');assert.equal(rows(h).length,0);assert.equal(await h.host().previewSampleQC(),false);assert.equal(h.qcCalls().filter(c=>c.path.includes('/artifacts/')).length,0);
});
test('artifact hash mismatch leaves sample table hidden rather than showing unverified R values',async t=>{
  const held=deferred(),h=await mountedPage(t,{artifactHeld:held});await choose(h);await startClick(h);h.record().status='succeeded';await h.host().check();const pending=h.host().previewSampleQC();await until(()=>h.qcCalls().some(c=>c.path.includes('/artifacts/')));const bytes=sampleBytes('intensity');bytes[bytes.length-2]^=1;held.resolve(binary(h.value(h.record()),'sample-qc.tsv',bytes));assert.equal(await pending,false);assert.equal(cells(h).length,0);assert.notEqual(h.host().view().task.sample_qc_preview.phase,'ready');
});
test('Intensity and LFQ retain independent source/family requests and do not share a ready table',async t=>{
  const h=await mountedPage(t);await choose(h);await startClick(h);h.record().status='succeeded';await h.host().check();await h.host().previewSampleQC();const old=cells(h);assert.equal(old.length,10);await choose(h,'lfq');assert.ok(old.every(n=>n.textContent===''));assert.equal(cells(h).length,0);await startClick(h);assert.deepEqual(h.posts().map(c=>JSON.parse(c.options.body).quant_family),['intensity','lfq']);h.record().status='succeeded';await h.host().check();await h.host().previewSampleQC();assert.equal(cells(h)[1].textContent,'LFQ intensity <img onerror=synthetic>');assert.equal(h.host().view().task.analysis_ready,false);
});
test('saved-file removal disposes the host and fences a late POST response',async t=>{
  const held=deferred(),h=await mountedPage(t,{postHeld:held});const host=await choose(h),pending=host.start();await until(()=>h.posts().length===1);const record=h.record();h.backend.setItems([]);await h.model.reload();held.resolve(Response.json(job(record),{status:202}));assert.equal(await pending,false);assert.equal(host.view().disposed,true);assert.equal(rows(h).length,0);assert.equal(cells(h).length,0);assert.equal(h.posts().length,1);
});
test('a different saved source can submit while the removed source late reply remains fenced',async t=>{
  const held=deferred(),h=await mountedPage(t,{values:[first,second],postHeld:held}),old=await choose(h),pending=old.start();await until(()=>h.posts().length===1);const oldRecord=h.record();h.backend.setItems([second.file]);await h.model.reload();const current=await choose(h,'intensity',second.file.file_id);assert.equal(await current.start(),true);assert.equal(current.view().phase,'queued');const currentTask=current.view().task.task;held.resolve(Response.json(job(oldRecord),{status:202}));assert.equal(await pending,false);assert.equal(old.view().disposed,true);assert.equal(current.view().phase,'queued');assert.equal(current.view().task.task,currentTask);assert.equal(h.posts().length,2);assert.ok(h.posts()[1].path.includes(second.dto.datasetVersion.dataset_version_id));assert.equal(rows(h).length,0);
});
test('Owner nonce change cancels pending artifact work and old transport cannot affect a new account',async t=>{
  const held=deferred(),storage=memory(),h=await mountedPage(t,{storage,artifactHeld:held});const host=await choose(h);await startClick(h);h.record().status='succeeded';await host.check();const pending=host.previewSampleQC();await until(()=>h.qcCalls().some(c=>c.path.includes('/artifacts/')));const record=h.record();await h.ownerSwitch();held.resolve(binary(h.value(record),'sample-qc.tsv',sampleBytes(record.family)));assert.equal(await pending,false);host.view();assert.equal(cells(h).length,0);assert.equal(h.transport.isCurrent(),false);assert.equal(storage.values.size,0);const fresh=h.projectTransport.rawFiles(first.file.project_id);assert.equal(fresh.isCurrent(),true);assert.equal(h.posts().length,1);
});
test('expired backend identity disposes private TXT/QC instead of retaining a demo or success state',async t=>{
  const h=await mountedPage(t,{postMode:'denied'});const host=await choose(h);await host.start();assert.equal(h.model.status().phase,'disposed');assert.equal(host.view().disposed,true);assert.equal(rows(h).length,0);assert.equal(cells(h).length,0);assert.equal(h.storage.values.size,0);
});
test('language/DOM remount preserves the same task and never triggers another POST or sample read',async t=>{
  const h=await mountedPage(t);await choose(h);await startClick(h);h.record().status='succeeded';await h.host().check();const request=h.record().requestId,retained=rows(h);h.remount(true);assert.ok(retained.every(n=>n.textContent===''));assert.equal(rows(h).length,7);assert.equal(h.record().requestId,request);assert.equal(h.posts().length,1);assert.equal(cells(h).length,0);assert.equal(h.qcCalls().filter(c=>c.path.includes('/artifacts/')).length,0);assert.match(h.d.root.textContent,/Basic QC completed/);
});
