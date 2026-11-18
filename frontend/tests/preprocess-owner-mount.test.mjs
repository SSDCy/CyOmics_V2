import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
import {createPrivateProjectController,createPrivateProjectTransport} from '../private-projects.js';
import {mountPrivateTxt} from '../private-txt-upload.js';
import {server as savedServer} from './support/contrast-mount-setup.mjs';
import {first,second,dom,until,flush,deferred,memory,server as queueServer,webcrypto} from './support/preprocess-owner-setup.mjs';

const app=await readFile(new URL('../app.js',import.meta.url),'utf8');let captured;
const init=app.split(/\r?\n/).find(line=>line.startsWith('const privateProjectRoutes='));
runInNewContext(init,{createPrivateProjectTransport(options){captured=Object.fromEntries(Object.entries(options));return{};},createPrivateProjectRoutes:options=>options},{timeout:200});
const pageOptions=Object.freeze(captured),projectId=first.file.project_id,otherProject='22222222-2222-4222-8222-222222222222';
const project=id=>({project_id:id,name:'Synthetic '+id,description:'',species:'',visibility:'private',createdAt:'2026-11-18T00:00:00Z',updatedAt:'2026-11-18T00:00:00Z'});
async function harness(t,{enabled=false,queue=queueServer(),storage=memory()}={}){
  const saved=savedServer([first]),calls=[];let nonce='Q'.repeat(22);
  const fetchImpl=(path,options)=>{calls.push({path,options});if(path==='/api/v1/me')return Response.json({status:'authenticated',user:{email:'synthetic@example.org',emailVerified:true}},{headers:{'X-Cyomics-Account-Context':nonce}});if(path==='/api/v1/projects?limit=20')return Response.json({items:[project(projectId),project(otherProject)],nextCursor:null});if(path==='/api/v1/projects/'+projectId)return Response.json({project:project(projectId)});if(path==='/api/v1/projects/'+otherProject)return Response.json({project:project(otherProject)});if(path==='/api/v1/projects/'+otherProject+'/files')return Response.json({items:[],nextCursor:null});if(path.includes('/preprocessing-jobs'))return queue.fetchImpl(path,options);return saved.fetchImpl(path,options);};
  const transport=createPrivateProjectTransport({...pageOptions,preprocessingEnabled:enabled,preprocessingStorage:storage,rawQcStorage:memory(),fetchImpl}),model=createPrivateProjectController({transport});
  assert.equal(await model.start(),true);assert.equal(await model.select(projectId),true);
  let d,stop=()=>{};const mount=async({restored=false}={})=>{stop();d=dom();stop=mountPrivateTxt(d.root,model.files());await until(()=>restored?d.root.textContent.includes('已排队；尚未开始计算'):enabled?!!d.by('data-qpp-field','family'):model.files().importReport(first.file.file_id)?.status().phase==='ready');};
  await mount();t.after(()=>{stop();model.dispose();transport.forgetContext({confirmed:true});});
  return{model,transport,queue,saved,calls,storage,get d(){return d;},mount,stop(){stop();},setNonce(value){nonce=value;},
    select(family='Intensity'){const n=d.by('data-qpp-field','family');n.value=family;n.fire('change');},confirm(){d.by('data-qpp-action','confirm').click();}};
}

test('app and every reusable layer stay default false, with no preprocessing DOM or API',async t=>{
  assert.equal(pageOptions.preprocessingEnabled,false);const h=await harness(t);assert.equal(h.model.files().preprocessing(first.file.file_id),null);assert.equal(h.d.by('data-qpp-field','family'),undefined);assert.equal(h.d.all().some(n=>n.className==='ppo'),false);assert.equal(h.queue.calls.length,0);assert.equal(h.d.all().filter(n=>n.type==='file').length,1);assert.match(h.d.root.textContent,/导入报告已保存/);
});
test('explicit true travels through actual project→rawFiles→report factory, preserving existing sections and wire/source',async t=>{
  const h=await harness(t,{enabled:true}),entry=h.model.files().preprocessing(first.file.file_id);assert.ok(entry.contextSignal instanceof AbortSignal);assert.equal(entry.contextSignal.aborted,false);assert.equal(entry.isCurrent(),true);assert.equal(h.d.by('data-qpp-field','family').value,'');assert.equal(h.queue.calls.length,0);
  for(const cls of ['prqc','psdh','pcdh'])assert.ok(h.d.all().some(n=>n.className===cls),cls);assert.equal(h.d.all().filter(n=>n.type==='file').length,1);
  h.select();h.confirm();await until(()=>h.d.root.textContent.includes('已排队；尚未开始计算'));const post=h.queue.calls[0],body=JSON.parse(post.options.body);assert.deepEqual(Object.keys(body).sort(),['parameters','quant_family','request_id']);assert.equal(body.quant_family,'Intensity');assert.deepEqual(body.parameters.filter_flags,{contaminant:false,reverse:false,site_only:false});assert.equal(new Headers(post.options.headers).get('X-Cyomics-Account-Context'),'Q'.repeat(22));assert.equal(post.options.credentials,'same-origin');assert.equal(post.path,'/api/v1/datasets/'+first.dto.datasetVersion.dataset_version_id+'/preprocessing-jobs?project_id='+projectId);
});
test('unknown POST survives report/view remount as GET of the same request, without new upload or POST',async t=>{
  const queue=queueServer({mode:'unknown'}),h=await harness(t,{enabled:true,queue});h.select();h.confirm();await until(()=>h.d.root.textContent.includes('提交结果未知'));const rid=JSON.parse(queue.calls[0].options.body).request_id;await h.mount({restored:true});assert.equal(queue.calls.filter(c=>c.options.method==='POST').length,1);assert.ok(queue.calls.some(c=>c.options.method==='GET'&&c.path.endsWith('&request_id='+rid)));assert.equal(h.calls.filter(c=>c.options.method==='POST'&&c.path.includes('/raw-uploads')).length,0);
});
test('view unmount immediately aborts its pending request while leaving file identity available for recovery',async t=>{
  const held=deferred(),queue=queueServer({held}),h=await harness(t,{enabled:true,queue});const options=h.model.files().preprocessing(first.file.file_id);h.select();h.confirm();await until(()=>queue.calls.length===1);h.stop();assert.equal(queue.calls[0].options.signal.aborted,true);assert.equal(options.contextSignal.aborted,false);held.resolve(Response.json([...queue.jobs.values()][0],{status:201}));await flush();await h.mount({restored:true});assert.equal(queue.calls.filter(c=>c.options.method==='POST').length,1);
});
test('project selection disposes old file/controller signals and cannot show its late queue response',async t=>{
  const held=deferred(),queue=queueServer({held}),h=await harness(t,{enabled:true,queue}),oldFiles=h.model.files(),options=oldFiles.preprocessing(first.file.file_id);h.select();h.confirm();await until(()=>queue.calls.length===1);assert.equal(await h.model.select(otherProject),true);assert.equal(options.contextSignal.aborted,true);assert.equal(options.isCurrent(),false);assert.equal(queue.calls[0].options.signal.aborted,true);held.resolve(Response.json([...queue.jobs.values()][0],{status:201}));await flush();assert.doesNotMatch(h.d.root.textContent,/已排队；尚未开始计算|88888888/);
});
test('removing a stored file cancels its pending host before a late response can republish',async t=>{
  const held=deferred(),queue=queueServer({held}),h=await harness(t,{enabled:true,queue}),options=h.model.files().preprocessing(first.file.file_id);h.select();h.confirm();await until(()=>queue.calls.length===1);h.saved.setItems([]);await h.model.files().reload();assert.equal(options.contextSignal.aborted,true);assert.equal(queue.calls[0].options.signal.aborted,true);held.resolve(Response.json([...queue.jobs.values()][0],{status:201}));await flush();assert.doesNotMatch(h.d.root.textContent,/已排队；尚未开始计算/);assert.equal(h.model.files().preprocessing(first.file.file_id),null);
});
test('report invalidation aborts the existing request and rejects the formerly captured source factory',async t=>{
  const held=deferred(),queue=queueServer({held}),h=await harness(t,{enabled:true,queue}),model=h.model.files().importReport(first.file.file_id),options=h.model.files().preprocessing(first.file.file_id),config={projectId,fileId:first.file.file_id,datasetVersionId:first.dto.datasetVersion.dataset_version_id};const transport=options.transportFactory(config);assert.equal(transport.isCurrent(),true);h.select();h.confirm();await until(()=>queue.calls.length===1);model.dispose();assert.equal(queue.calls[0].options.signal.aborted,true);assert.equal(transport.isCurrent(),false);assert.equal(options.transportFactory(config),null);held.resolve(Response.json([...queue.jobs.values()][0],{status:201}));await flush();assert.doesNotMatch(h.d.root.textContent,/已排队；尚未开始计算/);
});
test('uncertain context loss aborts synchronously but keeps recovery; confirmed loss clears only the reviewed current scope',async t=>{
  const h=await harness(t,{enabled:true});h.select();h.confirm();await until(()=>h.d.root.textContent.includes('已排队；尚未开始计算'));const before=[...h.storage.values.values()][0],options=h.model.files().preprocessing(first.file.file_id);h.transport.forgetContext({confirmed:false});assert.equal(options.contextSignal.aborted,true);assert.equal(options.isCurrent(),false);assert.equal([...h.storage.values.values()][0],before);h.transport.forgetContext({confirmed:true});assert.equal(h.storage.values.size,0);
});
test('actual account revision cancels old requests and cannot restore an earlier account reference',async t=>{
  const held=deferred(),queue=queueServer({held}),h=await harness(t,{enabled:true,queue}),old=h.model.files().preprocessing(first.file.file_id);h.select();h.confirm();await until(()=>queue.calls.length===1);const oldRef=[...h.storage.values.values()][0];h.setNonce('R'.repeat(22));assert.equal(await h.model.refresh(),true);assert.equal(old.contextSignal.aborted,true);assert.equal(queue.calls[0].options.signal.aborted,true);assert.equal([...h.storage.values.values()][0],oldRef);held.resolve(Response.json([...queue.jobs.values()][0],{status:201}));await flush();await h.model.select(projectId);await h.mount();assert.equal(h.d.by('data-qpp-field','family').value,'');assert.equal(queue.calls.filter(c=>c.options.method==='GET').length,0);assert.equal(queue.calls.filter(c=>c.options.method==='POST').length,1);
});
test('a preprocessing auth refusal follows the real parent denial path and clears usable state',async t=>{
  const queue=queueServer({mode:'denied'}),h=await harness(t,{enabled:true,queue}),options=h.model.files().preprocessing(first.file.file_id);h.select();h.confirm();await until(()=>h.model.files()===null);assert.equal(h.model.status().ready,false);assert.equal(options.contextSignal.aborted,true);assert.equal(h.storage.values.size,0);assert.equal(queue.calls.filter(c=>c.options.method==='POST').length,1);assert.doesNotMatch(h.d.root.textContent,/已排队；尚未开始计算/);
});
test('a late A auth refusal cannot invalidate B or clear its newer recovery reference',async t=>{
  const held=deferred(),queue=queueServer(),original=queue.fetchImpl;queue.fetchImpl=(path,options)=>{const response=original(path,options);if(options.method==='POST'&&queue.calls.filter(c=>c.options.method==='POST').length===1){void response.body.cancel();return held.promise;}return response;};
  const h=await harness(t,{enabled:true,queue});h.select();h.confirm();await until(()=>queue.calls.length===1);h.setNonce('R'.repeat(22));await h.model.refresh();await h.model.select(projectId);await h.mount();h.select();h.confirm();await until(()=>h.d.root.textContent.includes('已排队；尚未开始计算'));const before=[...h.storage.values.values()][0];held.resolve(new Response('old A denied',{status:401}));await flush();assert.equal(h.model.status().identity,'authenticated');assert.ok(h.model.files());assert.equal([...h.storage.values.values()][0],before);assert.match(h.d.root.textContent,/已排队；尚未开始计算/);assert.equal(queue.calls.filter(c=>c.options.method==='POST').length,2);
});
test('a late old scope cannot become a new account scope or erase its recovery envelope',async t=>{
  const h=await harness(t),original=Object.getOwnPropertyDescriptor(globalThis,'crypto'),held=deferred();let waiting=false;
  try{
    Object.defineProperty(globalThis,'crypto',{configurable:true,value:{randomUUID:()=>webcrypto.randomUUID(),subtle:{digest(algorithm,data){if(new TextDecoder().decode(data).includes('cyomics/preprocessing/account/v1')){waiting=true;return held.promise.then(()=>webcrypto.subtle.digest(algorithm,data));}return webcrypto.subtle.digest(algorithm,data);}}}});
    let nonce='Q'.repeat(22);const storage=memory(),transport=createPrivateProjectTransport({enabled:true,preprocessingEnabled:true,preprocessingStorage:storage,fetchImpl:()=>Response.json({status:'authenticated',user:{email:'synthetic@example.org',emailVerified:true}},{headers:{'X-Cyomics-Account-Context':nonce}})});
    await transport.me({signal:new AbortController().signal});const a=transport.rawFiles(projectId),config={projectId,fileId:first.file.file_id,datasetVersionId:first.dto.datasetVersion.dataset_version_id,isCurrent:()=>!a.preprocessingContextSignal.aborted},old=a.preprocessing(config),pending=old.scope();void pending.catch(()=>{});await until(()=>waiting);
    Object.defineProperty(globalThis,'crypto',original);nonce='R'.repeat(22);await transport.me({signal:new AbortController().signal});const b=transport.rawFiles(projectId),fresh=b.preprocessing({...config,isCurrent:()=>!b.preprocessingContextSignal.aborted}),scope=await fresh.scope();const snapshot=JSON.stringify({v:1,scope,items:[]});storage.setItem('cyomics:preprocessing:refs:v1',snapshot);held.resolve();await assert.rejects(pending);assert.equal(storage.getItem('cyomics:preprocessing:refs:v1'),snapshot);transport.forgetContext({confirmed:false});assert.equal(storage.getItem('cyomics:preprocessing:refs:v1'),snapshot);transport.forgetContext({confirmed:true});assert.equal(storage.values.size,0);a.disposePreprocessing();b.disposePreprocessing();
  }finally{Object.defineProperty(globalThis,'crypto',original);held.resolve();}
});
