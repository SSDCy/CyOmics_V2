import test from 'node:test';import assert from 'node:assert/strict';
import {setup,binary,payload,hash,webcrypto,first,clone,digest,memory,until,tick,deferred,saver,clock,completed,createQCArtifactScope,createRawQCClient} from './support/raw-qc-download-setup.mjs';
import {createPrivateProjectTransport} from '../private-projects.js';
import {createRawTransport,createPrivateTxtController,mountPrivateTxt} from '../private-txt-upload.js';
import {createPrivateRawQCHost,mountPrivateRawQC} from '../private-raw-qc.js';
import {createQCTransport} from '../raw-qc-client.js';
import {dom} from './support/contrast-mount-setup.mjs';
const name='raw-matrix.tsv';

test('off capability, invalid names and no success never request artifact content',async t=>{
 const off=await setup(t,{enabled:false});await off.start();assert.equal(off.c.view().artifact_downloads,false);assert.equal(await off.c.downloadArtifact(name),false);assert.equal(off.downloads().length,0);
 const h=await setup(t);for(const n of [name,'../raw-matrix.tsv','raw-matrix.tsv?token=x','RAW-matrix.tsv','x','%2f'])assert.equal(await h.c.downloadArtifact(n),false);assert.equal(h.downloads().length,0);await h.start();assert.equal(await h.c.downloadArtifact('../raw-matrix.tsv'),false);
});
test('seven single clicks verify exact raw bytes before Blob and use safe ASCII family/full UUID names',async t=>{
 for(const family of ['intensity','lfq']){const h=await setup(t,{family});await h.start();const original=JSON.stringify(h.job()),request=h.c.reference();
  for(const a of h.job().result.artifacts){assert.equal(await h.c.downloadArtifact(a.name),true);const saved=h.save.clicks.at(-1);assert.equal(saved.name,'raw-qc_'+family+'_'+h.job().job_id+'_'+a.name);const blob=h.save.active.get(saved.url);assert.equal(hash(new Uint8Array(await blob.arrayBuffer())),a.sha256);assert.deepEqual(new Uint8Array(await blob.arrayBuffer()),payload(a.name,family));assert.equal(h.c.view().download.phase,'handed_off');h.timers.advance(1000);assert.equal(h.save.active.size,0);}
  assert.equal(h.downloads().length,7);assert.equal(JSON.stringify(h.job()),original);assert.deepEqual(h.c.reference(),request);assert.deepEqual(h.calls.filter(x=>!x.path.includes('/artifacts/')).map(x=>x.o.method),['POST','GET']);
  for(const call of h.downloads()){assert.equal(call.o.method,'GET');assert.equal(call.o.credentials,'same-origin');assert.equal(call.o.cache,'no-store');assert.equal(call.o.redirect,'error');assert.deepEqual(Object.keys(call.o.headers),['X-Cyomics-Account-Context']);assert.ok(call.path.endsWith('?project_id='+first.file.project_id));assert.equal(call.o.body,undefined);}
  assert.doesNotMatch(JSON.stringify(h.c.view())+JSON.stringify([...h.storage.values]),/sha256|binding|blob:|QQQQ|execution|token/);
 }
});
test('double clicks and cross-family clients share one lease including URL lifetime',async t=>{
 const scope=createQCArtifactScope(),held=deferred(),a=await setup(t,{scope,artifactReply:()=>held.promise}),b=await setup(t,{scope,family:'lfq'});await a.start();await b.start();const p=a.c.downloadArtifact(name);assert.equal(await a.c.downloadArtifact(name),false);assert.equal(await b.c.downloadArtifact(name),false);assert.equal(a.downloads().length,1);assert.equal(b.downloads().length,0);held.resolve(binary(a.job(),name));assert.equal(await p,true);assert.equal(await b.c.downloadArtifact(name),false);a.timers.advance(1000);assert.equal(await b.c.downloadArtifact(name),true);
});
test('cancelled non-cancellable digest holds shared lease until actual settlement; late hash cannot create Blob',async t=>{
 const scope=createQCArtifactScope(),held=deferred();let block=false;const cryptoImpl={randomUUID:()=>webcrypto.randomUUID(),subtle:{digest:(...args)=>block?held.promise.then(()=>webcrypto.subtle.digest(...args)):webcrypto.subtle.digest(...args)}};
 const a=await setup(t,{scope,cryptoImpl}),b=await setup(t,{scope,family:'lfq'});await a.start();await b.start();block=true;const p=a.c.downloadArtifact(name);await until(()=>a.c.view().download.phase==='verifying');a.c.cancelArtifactDownload();await p;assert.equal(scope.busy(),true);assert.equal(await b.c.downloadArtifact(name),false);assert.equal(a.save.active.size,0);held.resolve();await until(()=>!scope.busy());assert.equal(a.save.clicks.length,0);assert.equal(await b.c.downloadArtifact(name),true);
});
test('60s total hash deadline is unconfirmed and still holds the digest lease to settlement',async t=>{
 const held=deferred();let block=false;const cryptoImpl={randomUUID:()=>webcrypto.randomUUID(),subtle:{digest:(...args)=>block?held.promise.then(()=>webcrypto.subtle.digest(...args)):webcrypto.subtle.digest(...args)}};
 const h=await setup(t,{cryptoImpl});await h.start();block=true;const p=h.c.downloadArtifact(name);await until(()=>h.c.view().download.phase==='verifying');h.timers.advance(60000);assert.equal(await p,false);assert.equal(h.c.view().download.phase,'unconfirmed');assert.equal(h.scope.busy(),true);held.resolve();await until(()=>!h.scope.busy());assert.equal(h.save.clicks.length,0);
});
test('15s idle response deadline aborts and leaves no flight or timer',async t=>{
 const held=deferred(),h=await setup(t,{artifactReply:()=>held.promise});await h.start();const p=h.c.downloadArtifact(name);h.timers.advance(15000);assert.equal(await p,false);assert.equal(h.c.view().download.code,'raw_qc_timeout');assert.equal(h.scope.busy(),false);assert.equal(h.timers.timers.size,0);held.resolve(binary(h.job(),name));await tick();assert.equal(h.save.active.size,0);
});
for(const [header,value]of [['content-type','text/plain'],['content-length','01'],['content-length','999999999'],['content-encoding','gzip'],['content-encoding','Identity'],['content-disposition','attachment; filename="../x"'],['x-cyomics-file-sha256','A'.repeat(64)],['cache-control','public'],['x-content-type-options',''],['referrer-policy','']])test('strict success header rejects '+header+'='+value,async t=>{
 const h=await setup(t,{artifactReply:j=>binary(j,name,payload(name),{headers:{[header]:value}})});await h.start();assert.equal(await h.c.downloadArtifact(name),false);assert.equal(h.save.clicks.length,0);assert.equal(h.save.active.size,0);assert.equal(h.scope.busy(),false);
});
for(const mode of ['short','long','wrong-hash','read-error','missing-eof','redirect','206'])test('bounded raw reader rejects '+mode,async t=>{
 let cancelled=0;const h=await setup(t,{artifactReply:j=>{const bytes=payload(name);let body=bytes;if(mode==='short')body=bytes.slice(1);if(mode==='long')body=new Uint8Array([...bytes,0]);if(mode==='wrong-hash')body=new Uint8Array(bytes).fill(99);if(mode==='read-error')body=new ReadableStream({start(c){c.error(Error('synthetic read'));}});if(mode==='missing-eof')body=new ReadableStream({start(c){c.enqueue(bytes);},cancel(){cancelled++;}});const r=binary(j,name,body,{status:mode==='206'?206:200});if(mode==='redirect')Object.defineProperty(r,'redirected',{value:true});return r;}});await h.start();const p=h.c.downloadArtifact(name);if(mode==='missing-eof'){await tick();h.timers.advance(15000);}assert.equal(await p,false);assert.equal(h.save.clicks.length,0);assert.equal(h.scope.busy(),false);if(mode==='missing-eof')assert.equal(cancelled,1);
});
for(const [status,code]of [[404,'raw_qc_artifact_not_found'],[503,'raw_qc_artifact_integrity_failed'],[503,'raw_qc_persisted_invalid'],[503,'raw_qc_unavailable'],[504,'raw_qc_timeout']])test('bounded known error '+status+'/'+code+' never changes the succeeded job',async t=>{
 const h=await setup(t,{artifactReply:()=>Response.json({error:{code}},{status})});await h.start();const ref=h.c.reference();assert.equal(await h.c.downloadArtifact(name),false);assert.equal(h.c.view().phase,'succeeded');assert.deepEqual(h.c.reference(),ref);assert.equal(h.save.clicks.length,0);
});
test('409 blocks even malformed error until explicit successful check; auto check/remount cannot unlock',async t=>{
 let conflict=true;const h=await setup(t,{artifactReply:j=>conflict?Response.json({error:{code:'raw_qc_result_not_ready'}},{status:409}):binary(j,name)});await h.start();await h.c.downloadArtifact(name);assert.equal(h.c.canDownloadArtifact(name),false);await h.c.check({explicit:false});assert.equal(h.c.canDownloadArtifact(name),false);h.stop();assert.equal(h.c.view().download.code,'raw_qc_result_not_ready');const detach=h.c.attach();assert.equal(h.c.canDownloadArtifact(name),false);assert.equal(h.c.view().download.code,'raw_qc_result_not_ready');conflict=false;await h.c.check();assert.equal(h.c.canDownloadArtifact(name),true);assert.equal(h.c.view().download.code,null);assert.equal(await h.c.downloadArtifact(name),true);assert.equal(h.calls.filter(x=>x.o.method==='POST').length,1);detach();
 const bad=await setup(t,{artifactReply:()=>new Response('bad',{status:409})});await bad.start();await bad.c.downloadArtifact(name);assert.equal(bad.c.canDownloadArtifact(name),false);
});
for(const status of [401,403])test('HTTP '+status+' clears refs/private state before late body can be used',async t=>{
 const h=await setup(t,{artifactReply:()=>new Response('private error',{status})});await h.start();await h.c.downloadArtifact(name);assert.equal(h.c.view().disposed,true);assert.deepEqual(h.c.view().artifacts,[]);assert.equal(h.storage.values.size,0);assert.equal(h.denials.length,1);assert.equal(h.save.active.size,0);
});
test('context 409 uses existing confirmed-denial path and clears private recovery',async t=>{
 const h=await setup(t,{artifactReply:()=>Response.json({error:{code:'project_context_changed'}},{status:409})});await h.start();await h.c.downloadArtifact(name);assert.equal(h.c.view().disposed,true);assert.equal(h.storage.values.size,0);assert.equal(h.denials[0].status,409);
});
for(const raw of ['x'.repeat(4097),'{"error":{"code":"private/path"}}','{"error":{"code":"raw_qc_unavailable","code":"raw_qc_unavailable"}}'])test('error bytes/schema fail closed '+raw.length,async t=>{
 const h=await setup(t,{artifactReply:()=>new Response(raw,{status:503,headers:{'content-type':'application/json'}})});await h.start();await h.c.downloadArtifact(name);assert.equal(h.save.clicks.length,0);assert.doesNotMatch(JSON.stringify(h.c.view()),/private\/path/);
});
for(const stage of ['createObjectURL','createElement','append'])test('reentrant identity loss at '+stage+' prevents click and revokes owned URL',async t=>{
 let h;const save=saver(at=>{if(at===stage){h.lose();h.c.view();}});h=await setup(t,{save});await h.start();assert.equal(await h.c.downloadArtifact(name),false);assert.equal(save.clicks.length,0);assert.equal(save.active.size,0);assert.equal(h.scope.busy(),false);
});
test('identity loss from handing_off emit fences final anchor.click',async t=>{
 const h=await setup(t);await h.start();h.c.subscribe(()=>{if(h.c.view().download.phase==='handing_off'){h.lose();h.c.view();}});assert.equal(await h.c.downloadArtifact(name),false);assert.equal(h.save.clicks.length,0);assert.equal(h.save.active.size,0);
});
test('last unmount and metadata check revoke successful Blob lease without changing UUID or POST',async t=>{
 const h=await setup(t);await h.start();const ref=h.c.reference();await h.c.downloadArtifact(name);h.stop();assert.equal(h.save.active.size,0);assert.equal(h.scope.busy(),false);assert.equal(h.c.canDownloadArtifact(name),false);const again=h.c.attach();await h.c.downloadArtifact(name);await h.c.check();assert.equal(h.save.active.size,0);assert.deepEqual(h.c.reference(),ref);assert.equal(h.calls.filter(x=>x.o.method==='POST').length,1);again();
});
test('private transport owns shared scope and forgetContext cancels an in-flight download immediately',async t=>{
 const held=deferred();let current;const calls=[];const tx=createPrivateProjectTransport({enabled:true,rawQcEnabled:true,rawQcArtifactsEnabled:true,rawQcStorage:memory(),fetchImpl:(path,o)=>{calls.push(path);if(path==='/api/v1/me')return Response.json({SYNTHETIC:true},{headers:{'X-Cyomics-Account-Context':'Q'.repeat(22)}});if(path.includes('/artifacts/'))return held.promise;if(o.method==='POST')current=completed({requestId:JSON.parse(o.body).request_id});return Response.json(current);}});await tx.me({signal:new AbortController().signal});const raw=tx.rawFiles(first.file.project_id),config={projectId:first.file.project_id,datasetVersionId:first.dto.datasetVersion.dataset_version_id,fileId:first.file.file_id},a=raw.rawQC(config),b=raw.rawQC(config);assert.equal(a.artifactDownload.scope,b.artifactDownload.scope);const h=await setup(t,{transport:a});await h.start();const p=h.c.downloadArtifact(name);tx.forgetContext({confirmed:true});assert.equal(await p,false);assert.equal(h.c.view().disposed,true);assert.equal(h.save.active.size,0);held.resolve(binary(current,name));await tick();assert.equal(h.save.clicks.length,0);
});
test('rawQC false is a parent gate even when artifact flag and scope are supplied',()=>{
 const tx=createRawTransport({projectId:first.file.project_id,rawQcEnabled:false,rawQcArtifactsEnabled:true,artifactScope:createQCArtifactScope()});assert.equal(tx.rawQC({}),null);
});
test('64MiB exact boundary hashes and hands off; 64MiB+1 manifest never becomes eligible',async t=>{
 const big=new Uint8Array(67108864),bodies=new Map([[name,big]]),h=await setup(t,{bodies});await h.start();assert.equal(await h.c.downloadArtifact(name),true);assert.equal(h.save.active.size,1);h.c.cancelArtifactDownload();assert.equal(h.save.active.size,0);
 const invalid=await setup(t,{metadataReply:j=>{const next=clone(j);next.result.artifacts.find(a=>a.name===name).bytes=67108865;next.result.manifest_sha256=digest(next.result.artifacts);return Response.json(next);}});assert.equal(await invalid.start(),false);assert.equal(await invalid.c.downloadArtifact(name),false);assert.equal(invalid.downloads().length,0);
});
for(const attribute of ['href','download'])test('reentrant anchor '+attribute+' setter is fenced before append/click',async t=>{
 let h;const save=saver(),create=save.documentImpl.createElement;save.documentImpl.createElement=tag=>{const a=create(tag);let value='';Object.defineProperty(a,attribute,{get:()=>value,set:v=>{value=v;if(v){h.lose();h.c.view();}},configurable:true});return a;};h=await setup(t,{save});await h.start();assert.equal(await h.c.downloadArtifact(name),false);assert.equal(save.anchors.length,0);assert.equal(save.active.size,0);assert.equal(save.clicks.length,0);
});
async function hostUI(t,{held=null,enabled=true}={}){
 let full={file:first.file,importDTO:first.dto},live=true;const listeners=new Set(),jobs=new Map(),calls=[],scope=createQCArtifactScope(),save=saver(),timers=clock();
 const reportModel={validatedSource:()=>full,subscribe(fn){listeners.add(fn);return()=>listeners.delete(fn);}};
 const host=createPrivateRawQCHost({reportModel,projectId:first.file.project_id,fileId:first.file.file_id,isCurrent:()=>live,cryptoImpl:webcrypto,clientOptions:{...save,...timers},transportFactory:config=>createQCTransport({projectId:config.projectId,datasetVersionId:config.datasetVersionId,storage:memory(),cryptoImpl:webcrypto,isCurrent:()=>live,rawQcArtifactsEnabled:enabled,artifactScope:scope,getOptions:signal=>({signal,credentials:'same-origin',cache:'no-store',redirect:'error',headers:{'X-Cyomics-Account-Context':'Q'.repeat(22)}}),fetchImpl:(path,o)=>{calls.push({path,o});if(path.includes('/artifacts/')){const j=jobs.get(path.split('?')[0].split('/')[4]);return held?held.promise:binary(j,path.split('/artifacts/')[1].split('?')[0]);}if(o.method==='POST'){const b=JSON.parse(o.body),j=completed({family:b.quant_family,requestId:b.request_id,jobId:b.quant_family==='lfq'?'99999999-9999-4999-8999-999999999999':undefined});jobs.set(j.job_id,j);return Response.json(j);}return Response.json(jobs.get(path.split('?')[0].split('/')[4]));}})});
 const d=dom(),stop=mountPrivateRawQC(d.root,host);host.setExpanded(true);host.selectFamily('intensity');await until(()=>host.view().phase==='idle');await host.start();t.after(()=>{stop();host.dispose();});return{host,d,save,scope,timers,calls,jobs,stop,remove(){full=null;for(const fn of listeners)fn();},lose(){live=false;host.view();}};
}
test('actual view only renders seven enabled single-file buttons under both gates, no SHA/URL data attributes',async t=>{
 const h=await hostUI(t),buttons=()=>h.d.all().filter(n=>n.className==='prqc-artifact-download');assert.equal(buttons().length,7);assert.ok(buttons().every(n=>!n.disabled));assert.equal(await h.host.downloadArtifact(name),true);assert.ok(buttons().every(n=>n.disabled));assert.match(h.d.root.textContent,/已校验并交给浏览器保存/);assert.doesNotMatch(JSON.stringify(h.d.all().map(n=>n.attrs)),/sha256|blob:|QQQQ|binding/);h.timers.advance(1000);assert.ok(buttons().every(n=>!n.disabled));const off=await hostUI(t,{enabled:false});assert.equal(off.d.all().filter(n=>n.className==='prqc-artifact-download').length,0);
});
for(const action of ['family','source','fold','unmount','context'])test('actual host '+action+' cancels a pending GET and fences late success',async t=>{
 const held=deferred(),h=await hostUI(t,{held});const j=[...h.jobs.values()][0],oldButtons=h.d.all().filter(n=>n.className==='prqc-artifact-download');const p=h.host.downloadArtifact(name);assert.equal(h.calls.filter(c=>c.path.includes('/artifacts/')).length,1);
 if(action==='family')h.host.selectFamily('lfq');if(action==='source')h.remove();if(action==='fold')h.host.setExpanded(false);if(action==='unmount')h.stop();if(action==='context')h.lose();held.resolve(binary(j,name));assert.equal(await p,false);assert.equal(h.save.clicks.length,0);assert.equal(h.save.active.size,0);assert.equal(h.scope.busy(),false);assert.ok(oldButtons.every(n=>n.disabled));
});
test('actual host switch after successful handoff revokes its URL and next family downloads its own bytes',async t=>{
 const h=await hostUI(t);await h.host.downloadArtifact(name);assert.equal(h.save.active.size,1);h.host.selectFamily('lfq');assert.equal(h.save.active.size,0);await until(()=>h.host.view().phase==='idle');await h.host.start();assert.equal(await h.host.downloadArtifact(name),true);const saved=h.save.clicks.at(-1);assert.match(saved.name,/raw-qc_lfq_/);assert.deepEqual(new Uint8Array(await h.save.active.get(saved.url).arrayBuffer()),payload(name,'lfq'));
});
