import test from 'node:test';import assert from 'node:assert/strict';
import {SAMPLE_QC_HEADERS,createQCTransport,createQCArtifactScope} from '../raw-qc-client.js';
import {createPrivateRawQCHost,mountPrivateRawQC} from '../private-raw-qc.js';
import {first,memory,until,deferred,dom,webcrypto} from './support/raw-qc-setup.mjs';
import {completed,binary,saver,clock} from './support/raw-qc-download-setup.mjs';
const q=s=>'"'+s.replaceAll('"','""')+'"',bytes=new TextEncoder().encode(SAMPLE_QC_HEADERS.map(q).join('\t')+'\n1\t'+q('Intensity <img onerror="synthetic">')+'\t'+q('NA\t"名字"\r\nsecond')+'\t2\t0\t1\t1\t1\t0\tNA\n');
async function ui(t,{enabled=true,held=null}={}){
 let full={file:first.file,importDTO:first.dto},live=true;const listeners=new Set(),jobs=new Map(),calls=[],scope=createQCArtifactScope(),save=saver(),timers=clock();
 const host=createPrivateRawQCHost({projectId:first.file.project_id,fileId:first.file.file_id,reportModel:{validatedSource:()=>full,subscribe(fn){listeners.add(fn);return()=>listeners.delete(fn);}},isCurrent:()=>live,cryptoImpl:webcrypto,clientOptions:{...save,...timers},transportFactory:config=>createQCTransport({projectId:config.projectId,datasetVersionId:config.datasetVersionId,isCurrent:()=>live,storage:memory(),cryptoImpl:webcrypto,rawQcArtifactsEnabled:enabled,artifactScope:scope,getOptions:signal=>({signal,credentials:'same-origin',cache:'no-store',redirect:'error',headers:{'X-Cyomics-Account-Context':'Q'.repeat(22)}}),fetchImpl:(path,o)=>{calls.push({path,o});if(path.includes('/artifacts/')){const j=jobs.get(path.split('/')[4]);return held?held.promise:binary(j,'sample-qc.tsv',bytes);}if(o.method==='POST'){const b=JSON.parse(o.body),j=completed({requestId:b.request_id,family:b.quant_family,jobId:b.quant_family==='lfq'?'99999999-9999-4999-8999-999999999999':undefined,bodies:new Map([['sample-qc.tsv',bytes]])});jobs.set(j.job_id,j);return Response.json(j);}return Response.json(jobs.get(path.split('?')[0].split('/')[4]));}})});
 let d=dom(),stop=mountPrivateRawQC(d.root,host);host.setExpanded(true);host.selectFamily('intensity');await until(()=>host.view().phase==='idle');await host.start();t.after(()=>{stop();host.dispose();});return{host,save,calls,jobs,scope,get d(){return d;},remove(){full=null;for(const fn of listeners)fn();},lose(){live=false;host.view();},stop(){stop();},remount(){stop();d=dom();stop=mountPrivateRawQC(d.root,host,{t:(_,en)=>en});}};
}
const cells=h=>h.d.all().filter(n=>n.tag==='td'),action=(h,name)=>h.d.all().find(n=>n.attrs['data-prqc-action']===name);
test('actual view renders all ten raw fields safely and only after one explicit verified click',async t=>{
 const h=await ui(t);assert.equal(cells(h).length,0);assert.equal(h.calls.filter(c=>c.path.includes('/artifacts/')).length,0);assert.equal(await h.host.previewSampleQC(),true);assert.equal(cells(h).length,10);assert.equal(cells(h)[9].textContent,'NA');assert.equal(cells(h)[8].textContent,'0');assert.match(cells(h)[1].textContent,/<img onerror/);assert.equal(h.d.all().some(n=>n.tag==='img'),false);const scroll=h.d.all().find(n=>n.className==='prqc-sample-scroll');assert.equal(scroll.attrs.tabindex,'0');assert.equal(scroll.attrs.role,'region');assert.equal(h.save.clicks.length,0);assert.equal(h.save.active.size,0);
});
for(const mode of ['cancel','fold-table','fold-results','fold-qc','family','source','context','unmount','remount'])test('actual view '+mode+' wipes table and retained td strings without auto-request',async t=>{
 const h=await ui(t);await h.host.previewSampleQC();const old=cells(h),count=h.calls.length;
 if(mode==='cancel')action(h,'cancel-preview').listeners.get('click')();
 if(mode==='fold-table'){const d=h.d.all().find(n=>n.className==='prqc-sample-preview');d.open=false;d.listeners.get('toggle')();}
 if(mode==='fold-results'){const d=h.d.all().find(n=>n.className==='prqc-artifacts');d.open=false;d.listeners.get('toggle')();}
 if(mode==='fold-qc')h.host.setExpanded(false);if(mode==='family')h.host.selectFamily('lfq');if(mode==='source')h.remove();if(mode==='context')h.lose();if(mode==='unmount')h.stop();if(mode==='remount')h.remount();
 assert.ok(old.every(n=>n.textContent===''));assert.equal(cells(h).length,0);assert.equal(h.calls.length,count);assert.equal(h.save.active.size,0);
});
test('late preview source reply cannot refill an erased view',async t=>{
 const held=deferred(),h=await ui(t,{held}),p=h.host.previewSampleQC();h.remove();held.resolve(binary([...h.jobs.values()][0],'sample-qc.tsv',bytes));assert.equal(await p,false);assert.equal(cells(h).length,0);assert.equal(h.save.clicks.length,0);
});
test('disabled capability hides preview controls and never creates fake table',async t=>{
 const h=await ui(t,{enabled:false});assert.equal(h.d.all().find(n=>n.className==='prqc-preview-tools').hidden,true);assert.equal(await h.host.previewSampleQC(),false);assert.equal(cells(h).length,0);assert.equal(h.calls.filter(c=>c.path.includes('/artifacts/')).length,0);
});
