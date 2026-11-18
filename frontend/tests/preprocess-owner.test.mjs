import test from 'node:test';
import assert from 'node:assert/strict';
import {createPreprocessOwnerClient,createPreprocessOwnerTransport,preprocessingRoute,validatePreprocessQueued} from '../preprocess-owner-client.js';
import {mountPrivatePreprocessOwner} from '../private-preprocess-owner.js';
import {mountPrivateImportReport} from '../private-import-report-view.js';
import {client,first,second,full,params,sourceFor,queued,server,memory,timer,dom,deferred,flush,until,webcrypto,reportModel,bindingFor} from './support/preprocess-owner-setup.mjs';

test('default off touches neither source, authority, recovery nor DOM',async()=>{
  let reads=0;const bomb=new Proxy({}, {get(){reads++;throw Error('must not read');}}),c=createPreprocessOwnerClient({fullSource:bomb,transport:bomb,isCurrent(){reads++;return false;}});await c.ready;assert.equal(c.view().phase,'disabled');assert.equal(await c.submit({}),false);assert.equal(reads,0);const d=dom(),stop=mountPrivatePreprocessOwner(d.root,{reportModel:bomb,transportFactory(){reads++;}});assert.equal(d.root.children.length,0);stop();c.dispose();assert.equal(reads,0);assert.equal(createPreprocessOwnerTransport({fetchImpl:bomb}),null);
});
test('no reference makes zero API calls; explicit confirmation sends only the canonical three-key body',async t=>{
  const h=await client(t);assert.equal(h.backend.calls.length,0);assert.equal(h.uuidCalls(),0);assert.equal(h.c.view().phase,'idle');assert.equal(await h.c.submit(h.input()),true);assert.equal(h.uuidCalls(),1);assert.equal(h.backend.calls.length,1);const {path,options}=h.backend.calls[0],body=JSON.parse(options.body);assert.deepEqual(Object.keys(body).sort(),['parameters','quant_family','request_id']);assert.deepEqual(body.parameters,params());assert.equal(body.quant_family,'Intensity');assert.equal(path,'/api/v1/datasets/'+first.dto.datasetVersion.dataset_version_id+'/preprocessing-jobs?project_id='+first.file.project_id);assert.equal(options.credentials,'same-origin');assert.equal(options.cache,'no-store');assert.equal(options.redirect,'error');assert.equal(new Headers(options.headers).has('Authorization'),false);assert.equal(h.c.view().phase,'queued');assert.equal(h.c.view().analysis_ready,false);
});
test('fixed routes and status rules accept 201 creation/200 replay but reject 202 or invented wrappers',async()=>{
  const body={request_id:'77777777-7777-4777-8777-777777777777',quant_family:'Intensity',parameters:params()},dto=queued(body),config={source:sourceFor(first,'Intensity'),body,method:'POST'};
  await validatePreprocessQueued(dto,{...config,status:201});await validatePreprocessQueued(dto,{...config,status:200});await assert.rejects(validatePreprocessQueued(dto,{...config,status:202}));await assert.rejects(validatePreprocessQueued({job:dto},{...config,status:201}));
  const route=preprocessingRoute({kind:'job',projectId:first.file.project_id,jobId:dto.job_id});assert.equal(route.method,'GET');assert.equal(route.path,'/api/v1/preprocessing-jobs/'+dto.job_id+'?project_id='+first.file.project_id);
});
for(const field of ['status','result','computation_performed','worker_started','execution_registered','source','parameters_sha256','request_sha256','execution_sha256','created_at','extra'])test('queued DTO rejects invalid '+field,async()=>{
  const body={request_id:'77777777-7777-4777-8777-777777777777',quant_family:'Intensity',parameters:params()},dto=queued(body);if(field==='status')dto.status='succeeded';else if(field==='result')dto.result={processed_version_id:'fake'};else if(['computation_performed','worker_started','execution_registered'].includes(field))dto[field]=true;else if(field==='source')dto.source.source_sha256='0'.repeat(64);else if(field==='created_at')dto.created_at='2026-11-18';else dto[field]=field==='extra'?true:'bad';await assert.rejects(validatePreprocessQueued(dto,{source:sourceFor(first,'Intensity'),body,method:'POST',status:201}));
});
test('same job execution/binding cannot drift across checks or full reload',async t=>{
  const storage=memory(),backend=server(),h=await client(t,{storage,backend});await h.c.submit(h.input());const rid=h.c.reference().request_id;backend.jobs.get(rid).execution_sha256='f'.repeat(64);assert.equal(await h.c.check(),false);assert.notEqual(h.c.view().phase,'queued');h.c.dispose();const fresh=await client(t,{storage,backend});assert.notEqual(fresh.c.view().phase,'queued');assert.equal(backend.calls.filter(c=>c.options.method==='POST').length,1);
});
test('uncertain POST retains the one request, retries use GET only, and GET body is absent',async t=>{
  const backend=server({mode:'unknown'}),h=await client(t,{backend});assert.equal(await h.c.submit(h.input()),false);const rid=h.c.reference().request_id;assert.equal(h.c.view().phase,'unknown');assert.equal(h.c.view().job_id,null);assert.equal(await h.c.submit(h.input()),true);assert.equal(h.uuidCalls(),1);assert.equal(backend.calls.filter(c=>c.options.method==='POST').length,1);assert.ok(backend.calls[1].path.endsWith('&request_id='+rid));assert.equal(backend.calls[1].options.body,undefined);
});
test('timeout and late POST cannot publish queued; manual GET reconciles the original ID',async t=>{
  const held=deferred(),backend=server({held}),clock=timer(),h=await client(t,{backend,clock});const pending=h.c.submit(h.input());await until(()=>backend.calls.length===1);clock.expire();assert.equal(await pending,false);assert.equal(h.c.view().phase,'unknown');held.resolve(Response.json([...backend.jobs.values()][0],{status:201}));await flush();assert.equal(h.c.view().phase,'unknown');assert.equal(await h.c.check(),true);assert.equal(backend.calls.filter(c=>c.options.method==='POST').length,1);
});
test('refresh restores the original job via GET without UUID generation or a new POST',async t=>{
  const storage=memory(),backend=server(),h=await client(t,{storage,backend});await h.c.submit(h.input('LFQ intensity',{contaminant:true,reverse:false,site_only:true}));const ref=h.c.reference();h.c.dispose();const restored=await client(t,{storage,backend});assert.equal(restored.c.view().phase,'queued');assert.equal(restored.c.view().job_id,ref.job_id);assert.equal(restored.c.reference().request_id,ref.request_id);assert.equal(restored.uuidCalls(),0);assert.equal(backend.calls.filter(c=>c.options.method==='POST').length,1);assert.equal(restored.c.view().quant_family,'LFQ intensity');
  const stored=[...storage.values.values()].join('');assert.doesNotMatch(stored,/synthetic raw|filename|token|Authorization|QQQQ|email|raw_bytea|sample_columns/);assert.match(stored,/source_binding_sha256/);
});
test('account and different saved-file reference partitions cannot recover an earlier job',async t=>{
  const storage=memory(),backend=server(),a=await client(t,{storage,backend});await a.c.submit(a.input());const before=backend.calls.length;a.c.dispose();const b=await client(t,{storage,backend,nonce:'R'.repeat(22)});assert.equal(b.c.view().phase,'idle');assert.equal(backend.calls.length,before);const c=await client(t,{storage,backend,f:second});assert.equal(c.c.view().phase,'idle');assert.equal(backend.calls.length,before);
});
test('project/dataset/file reference identities are independently isolated before any recovery GET',async t=>{
  for(const field of ['project_id','dataset_version_id','file_id']){const storage=memory(),backend=server(),a=await client(t,{storage,backend});await a.c.submit(a.input());a.c.dispose();const key=[...storage.values.keys()][0],envelope=JSON.parse(storage.getItem(key));envelope.items[0][field]='99999999-9999-4999-8999-999999999999';storage.setItem(key,JSON.stringify(envelope));const before=backend.calls.length,b=await client(t,{storage,backend});assert.equal(b.c.view().phase,'idle');assert.equal(backend.calls.length,before);}
});
test('an old account auth rejection cannot erase newer account recovery metadata',async t=>{
  const storage=memory(),held=deferred(),backend=server({held}),a=await client(t,{storage,backend}),pending=a.c.submit(a.input());await until(()=>backend.calls.length===1);const b=await client(t,{storage,nonce:'R'.repeat(22)});await b.c.submit(b.input());const saved=[...storage.values.values()][0];held.resolve(new Response('expired',{status:401}));await pending;assert.equal(a.c.view().phase,'unauthenticated');assert.equal([...storage.values.values()][0],saved);assert.equal(b.c.view().phase,'queued');
});
test('recovery failure prevents POST instead of pretending persistence or submission succeeded',async t=>{
  const storage={getItem:()=>null,setItem(){throw Error('denied');},removeItem(){}},h=await client(t,{storage});assert.equal(await h.c.submit(h.input()),false);assert.equal(h.backend.calls.length,0);assert.equal(h.c.view().phase,'unavailable');assert.equal(h.c.reference(),null);
});
test('auth denial clears usable state and its account recovery references',async t=>{
  const backend=server({mode:'denied'}),h=await client(t,{backend});await h.c.submit(h.input());assert.equal(h.c.view().phase,'unauthenticated');assert.equal(h.c.reference(),null);assert.equal(h.storage.values.size,0);assert.equal(h.denials.length,1);assert.equal(h.c.parameterSource(),null);
});
test('authority abort cancels a pending request and rejects its late response',async t=>{
  const signal=new AbortController(),held=deferred(),backend=server({held}),h=await client(t,{backend,contextSignal:signal.signal});const pending=h.c.submit(h.input());await until(()=>backend.calls.length===1);signal.abort();assert.equal(backend.calls[0].options.signal.aborted,true);assert.equal(await pending,false);held.resolve(Response.json([...backend.jobs.values()][0],{status:201}));await flush();assert.equal(h.c.view().phase,'disposed');assert.equal(h.c.view().job_id,null);
});
test('an already-aborted authority never validates or submits the source',async()=>{
  let reads=0,adds=0;const signal={aborted:true,addEventListener(){adds++;},removeEventListener(){}},source=new Proxy({}, {get(){reads++;throw Error('No authority');}}),transport={isCurrent:()=>true};const c=createPreprocessOwnerClient({featureEnabled:true,fullSource:source,transport,contextSignal:signal});await c.ready;assert.equal(c.view().phase,'disposed');assert.equal(reads,0);assert.equal(adds,0);assert.equal(await c.submit({}),false);
});
test('only explicit different parameter confirmations create another request; repeating prior parameters reads original',async t=>{
  const h=await client(t);await h.c.submit(h.input());const original=h.c.reference().request_id;await h.c.submit(h.input('Intensity',{contaminant:true,reverse:false,site_only:false}));assert.equal(h.uuidCalls(),2);await h.c.submit(h.input());assert.equal(h.c.reference().request_id,original);assert.equal(h.uuidCalls(),2);assert.equal(h.backend.calls.filter(c=>c.options.method==='POST').length,2);
});

async function view(t,{enabled=true,backend=server(),model=reportModel(),storage=memory(),contextSignal=null,connected=true,viaImport=false}={}){
  const d=dom();let factories=0;
  const config={featureEnabled:enabled,contextSignal,clientOptions:{cryptoImpl:webcrypto},transportFactory({projectId,datasetVersionId}){factories++;return connected?createPreprocessOwnerTransport({featureEnabled:true,projectId,datasetVersionId,fetchImpl:backend.fetchImpl,storage,isCurrent:()=>!contextSignal?.aborted,cryptoImpl:webcrypto,getOptions:signal=>({signal,credentials:'same-origin',cache:'no-store',redirect:'error',headers:{'X-Cyomics-Account-Context':'Q'.repeat(22)}})}):null;}};
  const stop=viaImport?mountPrivateImportReport(d.root,model,{binding:bindingFor(first),preprocessing:config}):mountPrivatePreprocessOwner(d.root,{...config,reportModel:model,binding:bindingFor(first)});t.after(stop);if(enabled&&connected)await until(()=>d.by('data-qpp-field','family')||d.root.textContent.includes('已排队；尚未开始计算'));
  return{d,model,backend,storage,factories:()=>factories,stop,select(value){const n=d.by('data-qpp-field','family');n.value=value;n.fire('change');},confirm(){d.by('data-qpp-action','confirm').click();}};
}
test('report hook stays absent with feature false and keeps the original report DOM/requests unchanged',async t=>{
  const h=await view(t,{enabled:false,viaImport:true});await flush();assert.equal(h.factories(),0);assert.equal(h.d.by('data-qpp-field','family'),undefined);assert.equal(h.backend.calls.length,0);assert.equal(h.d.all().some(n=>n.className==='ppo'),false);assert.match(h.d.root.textContent,/导入报告已保存/);
});
test('existing parameter card is reused and removed before queued status; no processed/result UI is invented',async t=>{
  const h=await view(t,{viaImport:true});assert.equal(h.backend.calls.length,0);assert.equal(h.d.by('data-qpp-field','family').value,'');for(const key of ['contaminant','reverse','site_only'])assert.equal(h.d.by('data-qpp-flag',key).checked,false);h.select('Intensity');h.confirm();await until(()=>h.d.root.textContent.includes('已排队；尚未开始计算'));assert.equal(h.d.by('data-qpp-field','family'),undefined);assert.doesNotMatch(h.d.root.textContent,/本页参数已确认；未提交|计算已完成|processed_version|分析就绪/);assert.equal(h.backend.calls.filter(c=>c.options.method==='POST').length,1);assert.equal(h.d.all().some(n=>n.type==='file'),false);
});
test('disconnected queue never provides a usable confirm handler or fake task',async t=>{
  const h=await view(t,{connected:false});assert.match(h.d.root.textContent,/接口尚未接入/);assert.equal(h.d.by('data-qpp-action','confirm'),undefined);assert.equal(h.backend.calls.length,0);
});
test('source removal while POST is pending wipes the old report task and rejects late queued UI',async t=>{
  const held=deferred(),backend=server({held}),h=await view(t,{backend});h.select('Intensity');h.confirm();await until(()=>backend.calls.length===1);h.model.set(null);assert.equal(backend.calls[0].options.signal.aborted,true);held.resolve(Response.json([...backend.jobs.values()][0],{status:201}));await flush();assert.doesNotMatch(h.d.root.textContent,/已排队；尚未开始计算|88888888/);assert.match(h.d.root.textContent,/请先保存并确认导入报告/);
});
test('unknown UI exposes only original-request read checks and never repeats POST on remount',async t=>{
  const backend=server({mode:'unknown'}),h=await view(t,{backend});h.select('Intensity');h.confirm();await until(()=>h.d.root.textContent.includes('提交结果未知'));assert.equal(h.d.by('data-qpp-action','confirm'),undefined);h.d.by('data-ppo-action','check').click();await until(()=>h.d.root.textContent.includes('已排队；尚未开始计算'));h.stop();const reloaded=await view(t,{backend,storage:h.storage,viaImport:true});assert.match(reloaded.d.root.textContent,/已排队；尚未开始计算/);assert.equal(reloaded.d.by('data-qpp-action','confirm'),undefined);assert.equal(backend.calls.filter(c=>c.options.method==='POST').length,1);
});
