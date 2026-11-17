import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash, webcrypto} from 'node:crypto';
import {createPrivateImportController,createImportTransport} from '../private-import-controller.js';
import {createRawTransport,createPrivateTxtController,mountPrivateTxt} from '../private-txt-upload.js';
import {createPrivateSampleDesignHost,mountPrivateSampleDesignHost} from '../private-sample-design-host.js';
import {createSampleDesignClient} from '../sample-design-client.js';

// All records, identities, headers and stored versions below are synthetic.
const fixtures=JSON.parse(await readFile(new URL('./fixtures/report-restore.synthetic.json',import.meta.url),'utf8'));
const clone=structuredClone, first=fixtures[0], second=fixtures[1], projectId=first.file.project_id;
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function until(fn){const end=Date.now()+1800;while(!fn()&&Date.now()<end)await tick();assert.ok(fn(),'Synthetic transition did not settle');}
const deferred=()=>{let resolve;const promise=new Promise(done=>resolve=done);return{promise,resolve};};
const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
const key=(version,family)=>version+':'+family;
const defaultDraft=(f,family,group='')=>({schemaVersion:1,dataset_version_id:f.dto.datasetVersion.dataset_version_id,quant_family:family,
  rows:f.dto.importReport.sample_columns.filter(c=>c.quant_family===family).map(c=>({column_ordinal:c.ordinal,name_raw:c.name_raw,sample_label_raw:c.sample_label_raw,
    group,condition:group?'salt stress':'',control:group?'yes':'unknown',replicate:group?'α':'',replicate_type:group?'biological':'unknown',confirmed:!!group}))});
let sequence=100;
const versionId=()=>String(++sequence).padStart(8,'0')+'-1111-4111-8111-111111111111';
function record(design,parent=null,requestId=versionId()){
  return{sample_design_version_id:versionId(),parent_version_id:parent?.sample_design_version_id??null,revision:parent?parent.revision+1:1,request_id:requestId,
    design_sha256:createHash('sha256').update(canonical(design)).digest('hex'),created_at:'2026-11-17T00:00:00.000000Z',design:clone(design)};
}
function dom(){
  const make=tag=>{let own='';return{tag,children:[],attrs:{},listeners:new Map(),disabled:false,hidden:false,value:'',checked:false,open:false,
    get textContent(){return own+this.children.map(c=>c.textContent).join('');},set textContent(v){own=String(v);this.children=[];},
    append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){own='';this.children=nodes;},setAttribute(k,v){this.attrs[k]=v;},
    addEventListener(k,v){this.listeners.set(k,v);},removeEventListener(k,v){if(this.listeners.get(k)===v)this.listeners.delete(k);}};};
  const doc={createElement:tag=>Object.assign(make(tag),{ownerDocument:doc})},root=doc.createElement('root');
  const walk=n=>[n,...n.children.flatMap(walk)];return{root,all:()=>walk(root)};
}
const field=(d,ordinal,name)=>d.all().find(n=>n.attrs['data-psd-ordinal']===String(ordinal)&&n.attrs['data-psd-field']===name);
const action=(d,name)=>d.all().find(n=>n.attrs['data-psd-action']===name);
function server(values=[first]){
  let items=values.map(f=>f.file),live=true;const calls=[],heads=new Map(),history=new Map();let intercept=null;
  function envelope(version,family,r){return{schemaVersion:1,project_id:projectId,dataset_version_id:version,quant_family:family,record:r};}
  const normal=(path,options)=>{
    if(path===`/api/v1/projects/${projectId}/files`)return Response.json({items,nextCursor:null});
    const report=/^\/api\/v1\/uploads\/([0-9a-f-]{36})\/complete\?project_id=([0-9a-f-]{36})$/.exec(path);
    if(report){assert.equal(options.method,'GET');assert.equal(report[2],projectId);const f=values.find(f=>f.file.file_id===report[1]);assert.ok(f);return Response.json(f.dto);}
    const match=/^\/api\/v1\/datasets\/([0-9a-f-]{36})\/sample-designs\?project_id=([0-9a-f-]{36})&quant_family=(intensity|lfq)(?:&request_id=([0-9a-f-]{36}))?$/.exec(path);
    assert.ok(match,'Unexpected synthetic route');assert.equal(match[2],projectId);
    const version=match[1],family=match[3]==='intensity'?'Intensity':'LFQ intensity';
    if(options.method==='GET'){
      const found=match[4]?history.get(match[4]):heads.get(key(version,family))??null;
      if(match[4]&&!found)return Response.json({error:{code:'sample_design_not_found'}},{status:404});
      return Response.json(envelope(version,family,found));
    }
    assert.equal(options.method,'POST');const body=JSON.parse(options.body),head=heads.get(key(version,family))??null;
    assert.deepEqual(Object.keys(body).sort(),['schemaVersion','request_id','expected_head_version_id','design'].sort());
    if(body.expected_head_version_id!==(head?.sample_design_version_id??null))return Response.json({error:{code:'sample_design_head_conflict'}},{status:409});
    const next=record(body.design,head,body.request_id);heads.set(key(version,family),next);history.set(next.request_id,next);
    return Response.json(envelope(version,family,next));
  };
  const fetchImpl=(path,options)=>{calls.push({path,options});return intercept?.(path,options,normal)??normal(path,options);};
  return{calls,heads,history,fetchImpl,normal,envelope,isCurrent:()=>live,lose(){live=false;},setItems(v){items=v;},setIntercept(fn){intercept=fn;},
    samples:()=>calls.filter(c=>c.path.includes('/sample-designs?')),posts:()=>calls.filter(c=>c.options.method==='POST')};
}
async function harness(t,{values=[first],backend=server(values),enabled=true,factory}={}){
  const denials=[];
  const transport=createRawTransport({projectId,fetchImpl:backend.fetchImpl,isCurrent:backend.isCurrent,sampleDesignsEnabled:enabled,
    ...(factory?{sampleDesignClientFactory:factory}:{}),getOptions:signal=>({method:'GET',credentials:'same-origin',cache:'no-store',redirect:'error',signal,
      headers:{'X-Cyomics-Account-Context':'Q'.repeat(22),'X-Do-Not-Forward':'synthetic-extra','Authorization':'must-not-forward'}})});
  const model=createPrivateTxtController({projectId,transport,cryptoImpl:webcrypto,onDenied:response=>denials.push(response)});
  let d=dom(),stop=mountPrivateTxt(d.root,model);
  const cleanup=()=>{stop();model.dispose();};t.after(cleanup);
  await until(()=>values.every(f=>model.importReport(f.file.file_id)?.status().phase==='ready'));
  return{model,backend,transport,denials,get d(){return d;},host:(id=first.file.file_id)=>model.sampleDesign(id),
    remount(en=false){stop();d=dom();stop=mountPrivateTxt(d.root,model,en?(_,english)=>english:zh=>zh);},cleanup};
}
const settled=host=>['empty','restored','not_connected','failed','unknown','source_unavailable'].includes(host.view().phase);

test('source accessor publishes only the full frozen confirmed DTO while auth/project remain valid',async()=>{
  let live=true;const f=clone(first),transport=createImportTransport({projectId,fileId:f.file.file_id,fetchImpl:()=>Response.json(f.dto),
    getOptions:signal=>({method:'GET',signal}),isCurrent:()=>live});
  const model=createPrivateImportController({file:f.file,transport,cryptoImpl:webcrypto});
  assert.equal(model.validatedSource(),null);const pending=model.restore();assert.equal(model.validatedSource(),null);assert.equal(await pending,true);
  const source=model.validatedSource();assert.ok(Object.isFrozen(source)&&Object.isFrozen(source.importDTO.importReport.sample_columns));
  assert.equal(source.importDTO.datasetVersion.dataset_version_id,f.dto.datasetVersion.dataset_version_id);
  assert.deepEqual(source.importDTO.importReport.sample_columns,f.dto.importReport.sample_columns);assert.equal(model.status().report.sample_columns,undefined);
  assert.deepEqual(Object.keys(source).sort(),['file','importDTO']);assert.ok(!JSON.stringify(source).includes('Authorization'));
  live=false;assert.equal(model.validatedSource(),null);model.dispose();assert.equal(model.validatedSource(),null);
});

test('default-off does no sample requests or fake save; scoped editor stays compact and product text avoids internals',async t=>{
  const h=await harness(t,{enabled:false});await until(()=>h.host().view().phase==='not_connected');
  assert.equal(h.backend.samples().length,0);assert.equal(h.d.all().filter(n=>n.tag==='input'&&n.type==='file').length,1);
  assert.equal(action(h.d,'save').disabled,true);assert.equal(action(h.d,'load').disabled,true);
  assert.match(h.d.root.textContent,/保存服务尚未启用/);assert.doesNotMatch(h.d.root.textContent,/适配器|DTO|已恢复此输入|样本信息已收到保存确认/);
  assert.ok(h.d.all().find(n=>n.className==='psdh'&&n.open===false));
  assert.equal(h.host().editor().update(1,{group:'local only'}),true);assert.equal(await h.host().save(),false);assert.equal(await h.host().load({discardDirty:true}),false);
  h.remount(true);assert.equal(field(h.d,1,'group').value,'local only');assert.match(h.d.root.textContent,/Saving is not enabled/);assert.equal(h.backend.samples().length,0);
});

test('confirmed report restore and complete controller reconstruction load each file once using only GET',async t=>{
  const backend=server([first,second]);
  for(const f of [first,second])backend.heads.set(key(f.dto.datasetVersion.dataset_version_id,'Intensity'),record(defaultDraft(f,'Intensity','G Ω')));
  for(let pass=0;pass<2;pass++){
    const h=await harness(t,{values:[first,second],backend});await until(()=>[first,second].every(f=>h.host(f.file.file_id).view().phase==='restored'));
    assert.equal(h.backend.samples().length,(pass+1)*2);assert.equal(backend.posts().length,0);
    for(const f of [first,second]){
      const host=h.host(f.file.file_id),view=host.view();assert.equal(view.design.rows[0].group,'G Ω');assert.equal(view.design.rows[0].name_raw,f.dto.importReport.sample_columns[0].name_raw);
      assert.equal(view.design.rows[0].confirmed,true);assert.equal(view.analysis_ready,false);assert.equal(await h.model.importReport(f.file.file_id).restore(),false);
    }
    h.remount(true);await h.model.reload();await tick();assert.equal(backend.samples().length,(pass+1)*2);h.cleanup();
  }
  assert.ok(backend.calls.every(c=>c.options.method==='GET'));
});

test('null GET leaves empty draft un-restored; dirty draft and expansion survive repaint, reload and language remount',async t=>{
  const h=await harness(t);await until(()=>h.host().view().phase==='empty');const host=h.host(),editor=host.editor();
  assert.equal(host.view().design.rows[0].group,'');assert.doesNotMatch(h.d.root.textContent,/已恢复此输入版本/);
  editor.update(1,{group:'literal <svg onload=bad>'});host.setExpanded(true);h.remount();await h.model.reload();h.remount(true);
  assert.equal(h.host(),host);assert.equal(h.host().editor(),editor);assert.equal(field(h.d,1,'group').value,'literal <svg onload=bad>');
  assert.equal(host.view().design.dirty,true);assert.equal(h.backend.samples().length,1);assert.equal(h.backend.posts().length,0);
  assert.equal(h.d.all().find(n=>n.className==='psdh').open,true);assert.ok(!h.d.all().some(n=>n.tag==='svg'));
  assert.equal(await host.load(),false);assert.equal(h.backend.samples().length,1);
});

test('family heads are independent; switching back neither refetches nor inherits confirmation; explicit POST uses current family CAS',async t=>{
  const backend=server(),version=first.dto.datasetVersion.dataset_version_id;
  const i=record(defaultDraft(first,'Intensity','I')),l=record(defaultDraft(first,'LFQ intensity','L'));
  backend.heads.set(key(version,'Intensity'),i);backend.heads.set(key(version,'LFQ intensity'),l);
  const h=await harness(t,{backend});await until(()=>h.host().view().phase==='restored');const host=h.host(),ed=host.editor();
  ed.setQuantFamily('LFQ intensity');await until(()=>host.view().phase==='restored'&&host.view().design.quant_family==='LFQ intensity');
  assert.equal(host.view().design.rows[0].group,'L');ed.update(2,{group:'L2'});assert.equal(await host.save(),true);
  const post=backend.posts()[0];assert.equal(JSON.parse(post.options.body).expected_head_version_id,l.sample_design_version_id);assert.ok(post.path.endsWith('quant_family=lfq'));
  ed.setQuantFamily('Intensity');assert.equal(host.view().design.rows[0].group,'I');assert.equal(host.view().design.rows[0].confirmed,false);assert.equal(host.view().head_version_id,i.sample_design_version_id);
  ed.setQuantFamily('LFQ intensity');assert.equal(host.view().design.rows[0].group,'L2');assert.equal(host.view().design.rows[0].confirmed,false);
  assert.equal(backend.samples().filter(c=>c.options.method==='GET').length,2);assert.equal(backend.posts().length,1);
  const headers=new Headers(post.options.headers);assert.equal(headers.get('X-Cyomics-Account-Context'),'Q'.repeat(22));assert.equal(headers.get('Content-Type'),'application/json');assert.equal(headers.get('X-Cyomics-Request'),'1');
  assert.equal(headers.get('Authorization'),null);assert.equal(headers.get('X-Do-Not-Forward'),null);assert.equal(post.options.credentials,'same-origin');
});

test('conflict retains dirty draft and blocks save until explicit reload of the newer head',async t=>{
  const h=await harness(t);await until(()=>h.host().view().phase==='empty');const host=h.host(),version=first.dto.datasetVersion.dataset_version_id;
  host.editor().update(1,{group:'my changes'});const external=record(defaultDraft(first,'Intensity','someone else'));h.backend.heads.set(key(version,'Intensity'),external);
  assert.equal(await host.save(),false);assert.equal(host.view().phase,'conflict');assert.equal(host.view().design.rows[0].group,'my changes');assert.equal(host.view().design.dirty,true);
  assert.equal(host.editor().view().can_save,false);assert.match(h.d.root.textContent,/已有更新版本/);assert.equal(h.backend.posts().length,1);
  assert.equal(await host.load(),false);assert.equal(await host.load({discardDirty:true}),true);assert.equal(host.view().phase,'restored');assert.equal(host.view().design.rows[0].group,'someone else');
  host.editor().update(1,{group:'reviewed'});assert.equal(await host.save(),true);assert.equal(JSON.parse(h.backend.posts()[1].options.body).expected_head_version_id,external.sample_design_version_id);
});

test('unknown write is never retried; request lookup preserves edits and requires explicit latest GET before next CAS',async t=>{
  const h=await harness(t);await until(()=>h.host().view().phase==='empty');const host=h.host(),version=first.dto.datasetVersion.dataset_version_id;
  let corrupt=true;h.backend.setIntercept((path,options,normal)=>{if(corrupt&&path.includes('/sample-designs?')&&options.method==='POST'){corrupt=false;normal(path,options);return new Response('{truncated',{headers:{'Content-Type':'application/json'}});}});
  host.editor().update(1,{group:'sent draft'});assert.equal(await host.save(),false);assert.equal(host.view().phase,'unknown');assert.equal(h.backend.posts().length,1);
  assert.equal(await host.save(),false);assert.equal(await host.load({discardDirty:true}),false);assert.equal(h.backend.posts().length,1);
  host.editor().update(1,{group:'keep newer local edit'});const querying=host.querySave();assert.equal(field(h.d,1,'group').disabled,true);assert.equal(action(h.d,'load').disabled,true);
  assert.equal(await querying,true);assert.equal(host.view().phase,'queried');assert.equal(host.view().design.rows[0].group,'keep newer local edit');assert.equal(host.view().design.dirty,true);assert.equal(host.view().head_known,false);
  assert.equal(action(h.d,'load').disabled,false);assert.equal(field(h.d,1,'group').disabled,false);assert.equal(action(h.d,'save').disabled,true);
  assert.equal(await host.save(),false);assert.equal(h.backend.samples().filter(c=>c.path.includes('request_id=')).length,1);
  const written=h.backend.heads.get(key(version,'Intensity')),latest=record(defaultDraft(first,'Intensity','newer external'),written);h.backend.heads.set(key(version,'Intensity'),latest);
  assert.equal(await host.load({discardDirty:true}),true);assert.equal(host.view().design.rows[0].group,'newer external');host.editor().update(1,{group:'last edit'});
  assert.equal(await host.save(),true);assert.equal(JSON.parse(h.backend.posts()[1].options.body).expected_head_version_id,latest.sample_design_version_id);
});

test('wrong-family or wrong-version GET response fails without applying content or auto retry',async t=>{
  for(const mode of ['family','version']){
    const backend=server();backend.setIntercept((path,options)=>{
      if(path.includes('/sample-designs?')){const version=mode==='version'?second.dto.datasetVersion.dataset_version_id:first.dto.datasetVersion.dataset_version_id;
        const family=mode==='family'?'LFQ intensity':'Intensity';return Response.json(backend.envelope(version,family,record(defaultDraft(first,family,'must not apply'))));}
    });
    const h=await harness(t,{backend});await until(()=>h.host().view().phase==='failed');assert.equal(h.host().view().design.rows[0].group,'');assert.equal(h.host().editor().view().can_save,false);
    h.remount();await h.model.reload();await tick();assert.equal(backend.samples().length,1);assert.equal(backend.posts().length,0);h.cleanup();
  }
});

test('late load after family switch is aborted and cannot overwrite the new family',async t=>{
  const backend=server(),held=deferred();let firstGet=true,signal;
  backend.setIntercept((path,options,normal)=>{if(firstGet&&path.includes('/sample-designs?')){firstGet=false;signal=options.signal;return held.promise;}});
  const h=await harness(t,{backend});await until(()=>backend.samples().length===1);const host=h.host();host.editor().setQuantFamily('LFQ intensity');
  await until(()=>host.view().phase==='empty'&&host.view().design.quant_family==='LFQ intensity');assert.equal(signal.aborted,true);
  held.resolve(Response.json(backend.envelope(first.dto.datasetVersion.dataset_version_id,'Intensity',record(defaultDraft(first,'Intensity','late private fact')))));
  await tick();assert.equal(host.view().design.rows[0].group,'');assert.doesNotMatch(h.d.root.textContent,/late private fact/);assert.equal(backend.samples().length,2);assert.equal(backend.posts().length,0);
});

test('401 clears uploader, cached private models and retained input nodes; late results after dispose cannot restore',async t=>{
  const h=await harness(t);await until(()=>h.host().view().phase==='empty');const host=h.host();host.editor().update(1,{group:'private text'});const oldField=field(h.d,1,'group');
  h.backend.setIntercept((path)=>path.includes('/sample-designs?')?Response.json({error:{code:'unauthenticated'}},{status:401}):null);
  assert.equal(await host.load({discardDirty:true}),false);assert.equal(h.denials[0].status,401);assert.equal(h.model.status().phase,'disposed');assert.equal(host.view().design,null);
  assert.equal(oldField.value,'');assert.equal(h.d.all().filter(n=>n.attrs['data-psd-field']).length,0);assert.doesNotMatch(h.d.root.textContent,/private text/);
});

test('dataset404 clears only its file source, stays blocked across repaint and does not refetch',async t=>{
  const backend=server([first,second]),v=first.dto.datasetVersion.dataset_version_id;
  backend.setIntercept((path)=>path.includes('/datasets/'+v+'/sample-designs?')?Response.json({error:{code:'dataset_not_found'}},{status:404}):null);
  const h=await harness(t,{values:[first,second],backend});await until(()=>h.host().view().phase==='source_unavailable'&&h.host(second.file.file_id).view().phase==='empty');
  assert.equal(h.host().editor(),null);assert.ok(h.host(second.file.file_id).editor());h.remount();await h.model.reload();await tick();assert.equal(backend.samples().length,2);assert.equal(backend.posts().length,0);
});

test('auth context loss and file removal clear caches; delayed GET cannot repopulate stale DOM',async t=>{
  for(const mode of ['auth','remove','dispose']){
    const backend=server(),held=deferred();let signal;
    backend.setIntercept((path,options)=>{if(path.includes('/sample-designs?')){signal=options.signal;return held.promise;}});
    const h=await harness(t,{backend});await until(()=>backend.samples().length===1);const host=h.host();
    if(mode==='auth'){backend.lose();assert.equal(host.view().design,null);}else if(mode==='remove'){backend.setItems([]);await h.model.reload();}else h.cleanup();
    assert.equal(signal.aborted,true);held.resolve(Response.json(backend.envelope(first.dto.datasetVersion.dataset_version_id,'Intensity',record(defaultDraft(first,'Intensity','late secret')))));
    await tick();assert.equal(host.view().design,null);assert.doesNotMatch(h.d.root.textContent,/late secret/);assert.equal(backend.posts().length,0);h.cleanup();
  }
});

test('source version change drops previous model and late save; source proof is never synthesized from status',async t=>{
  const legacy=JSON.parse(await readFile(new URL('./fixtures/import-report-v1.synthetic.json',import.meta.url),'utf8'));
  let value={file:first.file,importDTO:first.dto};const listeners=new Set(),report={validatedSource:()=>value,subscribe(fn){listeners.add(fn);return()=>listeners.delete(fn);}};
  const backend=server(),held=deferred();let signal,block=false;
  backend.setIntercept((path,options)=>{if(block&&options.method==='POST'){signal=options.signal;return held.promise;}});
  const host=createPrivateSampleDesignHost({reportModel:report,projectId,fileId:first.file.file_id,isCurrent:()=>true,cryptoImpl:webcrypto,
    clientFactory:options=>createSampleDesignClient({...options,transport:({route,body,signal})=>backend.fetchImpl(route.path,{method:route.method,body,signal})})});
  t.after(()=>host.dispose());const d=dom(),stop=mountPrivateSampleDesignHost(d.root,host);t.after(stop);
  await until(()=>host.view().phase==='empty');const old=host.editor();old.update(1,{group:'old version'});block=true;const pending=host.save();await until(()=>!!signal);
  value={file:legacy.file,importDTO:legacy.dto};for(const fn of listeners)fn();assert.equal(signal.aborted,true);assert.equal(await pending,false);await until(()=>host.view().phase==='empty');
  assert.notEqual(host.editor(),old);assert.equal(host.view().design.binding.dataset_version_id,legacy.dto.datasetVersion.dataset_version_id);assert.equal(host.view().design.rows[0].group,'');
  held.resolve(Response.json({unexpected:'late'}));await tick();assert.doesNotMatch(d.root.textContent,/old version/);assert.equal(old.exportDraft(),null);
});

test('manual edits during source validation suppress automatic load rather than overwrite dirty draft',async t=>{
  const listeners=new Set(),report={validatedSource:()=>({file:first.file,importDTO:first.dto}),subscribe(fn){listeners.add(fn);return()=>listeners.delete(fn);}};
  const backend=server(),host=createPrivateSampleDesignHost({reportModel:report,projectId,fileId:first.file.file_id,isCurrent:()=>true,cryptoImpl:webcrypto,
    clientFactory:options=>createSampleDesignClient({...options,transport:({route,body,signal})=>backend.fetchImpl(route.path,{method:route.method,body,signal})})});
  t.after(()=>host.dispose());let changed=false;
  const stop=host.subscribe(()=>{const editor=host.editor();if(!changed&&editor?.view().source_phase==='ready'){changed=true;editor.update(1,{group:'typed before initial load'});}});t.after(stop);
  await until(()=>changed&&host.view().phase==='dirty');await tick();assert.equal(backend.samples().length,0);assert.equal(host.view().design.rows[0].group,'typed before initial load');
  assert.equal(await host.load(),false);assert.equal(await host.load({discardDirty:true}),false);assert.equal(backend.samples().length,1);assert.equal(host.view().design.rows[0].group,'typed before initial load');assert.equal(host.view().phase,'dirty');
});

test('uploader serializes auto loads after metadata; queued clients start no timer and dirty/manual actions cannot duplicate GET',async t=>{
  const backend=server([first,second]),held=deferred(),timers=new Set();let starts=0,maxTimers=0;
  backend.setIntercept((path,options)=>path.includes('/datasets/'+first.dto.datasetVersion.dataset_version_id+'/sample-designs?')?held.promise:null);
  const h=await harness(t,{values:[first,second],backend,factory:options=>createSampleDesignClient({...options,
    setTimer(fn,ms){starts++;const id=setTimeout(fn,ms);timers.add(id);maxTimers=Math.max(maxTimers,timers.size);return id;},clearTimer(id){timers.delete(id);clearTimeout(id);}})});
  await until(()=>backend.samples().length===1&&h.host(second.file.file_id).view().phase==='queued');
  const firstSample=backend.calls.findIndex(c=>c.path.includes('/sample-designs?'));
  assert.equal(backend.calls.slice(0,firstSample).filter(c=>c.path.includes('/complete?')).length,2);
  assert.equal(starts,1);assert.equal(timers.size,1);const queued=h.host(second.file.file_id);
  assert.equal(queued.editor().view().can_load,false);assert.equal(await queued.load({discardDirty:true}),false);
  queued.editor().update(1,{group:'typed while queued'});h.remount();assert.equal(backend.samples().length,1);
  held.resolve(Response.json(backend.envelope(first.dto.datasetVersion.dataset_version_id,'Intensity',null)));
  await until(()=>h.host().view().phase==='empty'&&queued.editor().view().can_load);assert.equal(queued.view().phase,'dirty');assert.equal(starts,1);assert.equal(backend.samples().length,1);
  assert.equal(await queued.load({discardDirty:true}),false);assert.equal(starts,2);assert.equal(maxTimers,1);assert.equal(backend.samples().length,2);assert.equal(backend.posts().length,0);
});

test('queued auto load skips a removed file or old family without starting a request timer',async t=>{
  for(const mode of ['remove','family','auth']){
    const backend=server([first,second]),held=deferred();let starts=0;
    backend.setIntercept((path)=>path.includes('/datasets/'+first.dto.datasetVersion.dataset_version_id+'/sample-designs?')?held.promise:null);
    const h=await harness(t,{values:[first,second],backend,factory:options=>createSampleDesignClient({...options,setTimer(fn,ms){starts++;return setTimeout(fn,ms);}})});
    await until(()=>backend.samples().length===1&&h.host(second.file.file_id).view().phase==='queued');const queued=h.host(second.file.file_id);
    if(mode==='remove'){backend.setItems([first.file]);await h.model.reload();}
    else if(mode==='family')queued.editor().setQuantFamily('LFQ intensity');
    else{backend.lose();assert.equal(queued.view().design,null);h.model.dispose();}
    held.resolve(Response.json(backend.envelope(first.dto.datasetVersion.dataset_version_id,'Intensity',null)));
    if(mode==='family'){
      await until(()=>queued.view().phase==='empty'&&queued.view().design.quant_family==='LFQ intensity');
      assert.equal(backend.samples().length,2);assert.ok(backend.samples()[1].path.endsWith('quant_family=lfq'));assert.equal(starts,2);
    }else{await tick();await tick();assert.equal(backend.samples().length,1);assert.equal(starts,1);assert.equal(queued.view().design,null);}
    assert.equal(backend.posts().length,0);h.cleanup();
  }
});

test('R2 typed409 project_context_changed on GET or POST clears all private state, unlike head conflict',async t=>{
  for(const method of ['GET','POST']){
    const h=await harness(t);await until(()=>h.host().view().phase==='empty');const host=h.host();host.editor().update(1,{group:'must be cleared'});const retained=field(h.d,1,'group');
    h.backend.setIntercept((path,options)=>path.includes('/sample-designs?')&&options.method===method?Response.json({error:{code:'project_context_changed'}},{status:409}):null);
    const ok=method==='POST'?await host.save():await host.load({discardDirty:true});assert.equal(ok,false);
    assert.deepEqual(h.denials.map(r=>[r.status,r.body.error.code]),[[409,'project_context_changed']]);assert.equal(h.model.status().phase,'disposed');
    assert.equal(host.view().design,null);assert.equal(retained.value,'');assert.doesNotMatch(h.d.root.textContent,/must be cleared/);h.cleanup();
  }
});

test('sample transport never permits arbitrary URLs, other project/dataset routes or extra account headers',async t=>{
  let transport;const h=await harness(t,{factory:options=>{transport=options.transport;return createSampleDesignClient(options);}});
  await until(()=>h.host().view().phase==='empty');const count=h.backend.calls.length,signal=new AbortController().signal;
  for(const path of ['https://external.invalid/api/v1/datasets/'+first.dto.datasetVersion.dataset_version_id+'/sample-designs?project_id='+projectId+'&quant_family=intensity',
    '/api/v1/datasets/'+second.dto.datasetVersion.dataset_version_id+'/sample-designs?project_id='+projectId+'&quant_family=intensity',
    '/api/v1/datasets/'+first.dto.datasetVersion.dataset_version_id+'/sample-designs?project_id='+second.file.file_id+'&quant_family=intensity']){
    assert.throws(()=>transport({route:{path,method:'GET'},signal}));
  }
  assert.equal(h.backend.calls.length,count);assert.ok(!JSON.stringify(h.host().view()).includes('Account-Context'));
});
