import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash,webcrypto} from 'node:crypto';
import {createSampleDesignClient,createSampleDesignTransport} from '../sample-design-client.js';
import {createPrivateSampleDesignHost} from '../private-sample-design-host.js';
import {createPrivateImportController} from '../private-import-controller.js';
const fixture=JSON.parse(await readFile(new URL('./fixtures/comparison-source/source.synthetic.json',import.meta.url),'utf8'));
const clone=structuredClone,tick=()=>new Promise(r=>setImmediate(r));
async function until(fn){const end=Date.now()+1200;while(!fn()&&Date.now()<end)await tick();assert.ok(fn(),'Synthetic state did not settle');}
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve};};
const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
let serial=100;const id=()=>String(++serial).padStart(8,'0')+'-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const draft=(family='Intensity',group='synthetic group')=>({schemaVersion:1,dataset_version_id:fixture.dto.datasetVersion.dataset_version_id,quant_family:family,
  rows:fixture.dto.importReport.sample_columns.filter(c=>c.quant_family===family).map(c=>({column_ordinal:c.ordinal,name_raw:c.name_raw,sample_label_raw:c.sample_label_raw,
    group,condition:'',control:'unknown',replicate:'',replicate_type:'unknown',confirmed:false}))});
function record(design,parent=null,request_id=id()) { return{sample_design_version_id:id(),parent_version_id:parent?.sample_design_version_id??null,revision:parent?parent.revision+1:1,
  request_id,design_sha256:createHash('sha256').update(canonical(design)).digest('hex'),created_at:'2026-11-18T00:00:00.000000Z',design:clone(design)}; }
function server(initial=record(draft())){
  const heads=new Map([['Intensity',initial]]),history=new Map(),calls=[];let hook=null;
  const envelope=(family,value)=>({schemaVersion:1,project_id:fixture.file.project_id,dataset_version_id:fixture.dto.datasetVersion.dataset_version_id,quant_family:family,record:value});
  const normal=(path,o)=>{
    const url=new URL(path,'https://synthetic.invalid'),family=url.searchParams.get('quant_family')==='lfq'?'LFQ intensity':'Intensity',request=url.searchParams.get('request_id');
    if(o.method==='GET')return Response.json(envelope(family,request?history.get(request):heads.get(family)??null));
    assert.equal(o.method,'POST');const p=JSON.parse(o.body),head=heads.get(family)??null;
    if((head?.sample_design_version_id??null)!==p.expected_head_version_id)return Response.json({error:{code:'sample_design_head_conflict'}},{status:409});
    const next=record(p.design,head,p.request_id);heads.set(family,next);history.set(next.request_id,next);return Response.json(envelope(family,next));
  };
  return{heads,history,calls,envelope,normal,hook(fn){hook=fn;},fetchImpl(path,o){calls.push({path,options:o});return hook?.(path,o,normal)??normal(path,o);}};
}
async function setup(t,{backend=server(),scheduler}={}){
  let live=true,override;const listeners=new Set(),report=createPrivateImportController({file:clone(fixture.file),cryptoImpl:webcrypto,
    transport:{isCurrent:()=>live,get:()=>Response.json(clone(fixture.dto))}});assert.equal(await report.restore(),true);
  const adapter={validatedSource:()=>override===undefined?report.validatedSource():override,subscribe(fn){listeners.add(fn);return()=>listeners.delete(fn);}};
  let client;const host=createPrivateSampleDesignHost({projectId:fixture.file.project_id,fileId:fixture.file.file_id,reportModel:adapter,isCurrent:()=>live,cryptoImpl:webcrypto,
    ...(scheduler?{loadScheduler:scheduler}:{}),clientFactory:options=>(client=createSampleDesignClient({...options,transport:createSampleDesignTransport({fetchImpl:backend.fetchImpl})}))});
  t.after(()=>{host.dispose();report.dispose();});
  return{host,backend,report,get client(){return client;},lose(){live=false;},restore(){live=true;},source(value,notify=true){override=value;if(notify)for(const fn of listeners)fn();}};
}

test('actual validated load supplies exact five-key source and full immutable independent record without confirming samples',async t=>{
  const h=await setup(t);await until(()=>h.host.view().phase==='restored');const count=h.backend.calls.length,s=h.host.comparisonSource(),r=h.client.savedRecord('Intensity');
  assert.deepEqual(Object.keys(s).sort(),['project_id','dataset_version_id','quant_family','sample_columns','sample_design_record'].sort());
  assert.deepEqual(s.sample_design_record,h.backend.heads.get('Intensity'));assert.deepEqual(s.sample_columns,fixture.dto.importReport.sample_columns);
  assert.equal(s.sample_design_record.design.rows[0].confirmed,false);assert.equal(h.host.view().design.rows[0].confirmed,false);assert.equal(h.backend.calls.length,count);
  assert.ok(Object.isFrozen(r.design.rows[0])&&Object.isFrozen(s.sample_columns[0]));assert.throws(()=>r.design.rows[0].group='forged',TypeError);assert.throws(()=>s.sample_design_record.revision=90,TypeError);
  assert.notEqual(r,h.client.savedRecord('Intensity'));assert.notEqual(s,h.host.comparisonSource());assert.equal(h.host.comparisonSource().sample_design_record.design.rows[0].group,'synthetic group');
});

test('actual explicit save changes the saved record; local edits/no-op/review changes cannot masquerade as saved rows',async t=>{
  const h=await setup(t);await until(()=>h.host.view().phase==='restored');const old=h.host.comparisonSource().sample_design_record.sample_design_version_id,ed=h.host.editor();
  assert.equal(ed.update(1,{group:'synthetic group'}),false);assert.ok(h.host.comparisonSource());
  ed.update(1,{group:'local unsaved'});assert.equal(h.host.comparisonSource(),null);assert.equal(h.client.savedRecord('Intensity').design.rows[0].group,'synthetic group');
  assert.equal(await h.host.save(),true);const s=h.host.comparisonSource();assert.ok(s);assert.notEqual(s.sample_design_record.sample_design_version_id,old);assert.equal(s.sample_design_record.design.rows[0].group,'local unsaved');
  ed.update(1,{condition:'c',control:'yes',replicate:'1',replicate_type:'biological'});assert.equal(ed.setConfirmed(1,true),true);assert.equal(h.host.comparisonSource(),null);
});

test('no record, default disconnected client and missing source yield null and getters issue no requests',async t=>{
  const h=await setup(t,{backend:server(null)});await until(()=>h.host.view().phase==='empty');const n=h.backend.calls.length;
  assert.equal(h.client.savedRecord('Intensity'),null);assert.equal(h.client.savedRecord('bad'),null);assert.equal(h.host.comparisonSource(),null);assert.equal(h.backend.calls.length,n);
  const c=createSampleDesignClient({projectId:fixture.file.project_id,datasetVersionId:fixture.dto.datasetVersion.dataset_version_id,
    source:{dataset_version_id:fixture.dto.datasetVersion.dataset_version_id,sample_columns:fixture.dto.importReport.sample_columns},isCurrent:()=>true});
  assert.equal(c.savedRecord('Intensity'),null);c.dispose();
  const host=createPrivateSampleDesignHost({reportModel:{status:()=>({ready:true,saved:true}),validatedSource:()=>null},isCurrent:()=>true});assert.equal(host.comparisonSource(),null);host.dispose();
});

test('queued restore has no saved source; pending load/save are null and attempted edits during load remain rejected',async t=>{
  const queued=[];const h=await setup(t,{scheduler:run=>new Promise(resolve=>queued.push(()=>resolve(run())))});await until(()=>h.host.view().phase==='queued');
  assert.equal(h.host.comparisonSource(),null);assert.equal(h.backend.calls.length,0);const task=queued.shift()();await until(()=>h.host.view().phase==='restored');await task;
  const held=deferred();h.backend.hook(()=>held.promise);const loading=h.host.load();await until(()=>h.host.view().phase==='loading');assert.equal(h.host.comparisonSource(),null);assert.equal(h.client.savedRecord('Intensity'),null);
  assert.equal(h.host.editor().update(1,{group:'blocked edit'}),false);held.resolve(h.backend.normal(h.backend.calls.at(-1).path,h.backend.calls.at(-1).options));assert.equal(await loading,true);assert.ok(h.host.comparisonSource());
  const write=deferred();h.backend.hook(()=>write.promise);h.host.editor().update(1,{group:'saved later'});const saving=h.host.save();await until(()=>h.host.view().phase==='saving');
  assert.equal(h.host.comparisonSource(),null);assert.equal(h.client.savedRecord('Intensity'),null);write.resolve(h.backend.normal(h.backend.calls.at(-1).path,h.backend.calls.at(-1).options));assert.equal(await saving,true);assert.ok(h.host.comparisonSource());
});

test('conflict/typed failed POST dirty clears only on explicit current verified GET, preserving host discard rule',async t=>{
  for(const code of ['sample_design_head_conflict','invalid_sample_design']){
    const h=await setup(t);await until(()=>h.host.view().phase==='restored');h.host.editor().update(1,{group:'dirty'});
    h.backend.hook((path,o)=>o.method==='POST'?Response.json({error:{code}},{status:code.includes('conflict')?409:422}):null);
    assert.equal(await h.host.save(),false);assert.equal(h.client.status('Intensity').dirty,true);assert.equal(h.client.savedRecord('Intensity'),null);assert.equal(h.host.comparisonSource(),null);
    assert.equal(await h.host.load(),false);assert.equal(await h.host.load({discardDirty:true}),true);assert.equal(h.client.status('Intensity').dirty,false);assert.ok(h.host.comparisonSource());h.host.dispose();
  }
});

test('failed and aborted GET after a failed POST never clean dirty or expose old record',async t=>{
  const h=await setup(t);await until(()=>h.host.view().phase==='restored');h.host.editor().update(1,{group:'dirty'});
  h.backend.hook((path,o)=>Response.json({error:{code:o.method==='POST'?'invalid_sample_design':'sample_design_unavailable'}},{status:o.method==='POST'?422:503}));
  await h.host.save();assert.equal(await h.host.load({discardDirty:true}),false);assert.equal(h.client.status('Intensity').dirty,true);assert.equal(h.host.comparisonSource(),null);
  const hold=deferred();h.backend.hook(()=>hold.promise);const signal=new AbortController();const pending=h.client.loadDraft({dataset_version_id:fixture.dto.datasetVersion.dataset_version_id,quant_family:'Intensity'},{signal:signal.signal});signal.abort();
  await assert.rejects(pending);assert.equal(h.client.status('Intensity').dirty,true);assert.equal(h.client.savedRecord('Intensity'),null);hold.resolve(h.backend.normal(h.backend.calls.at(-1).path,h.backend.calls.at(-1).options));await tick();assert.equal(h.host.comparisonSource(),null);
});

test('unknown result and historical query never supply comparison source or current clean evidence',async t=>{
  const h=await setup(t);await until(()=>h.host.view().phase==='restored');h.host.editor().update(1,{group:'uncertain'});
  h.backend.hook((path,o,normal)=>{if(o.method==='POST'){normal(path,o);return new Response('{bad',{headers:{'Content-Type':'application/json'}});}});
  await h.host.save();assert.equal(h.host.view().phase,'unknown');assert.equal(h.host.comparisonSource(),null);
  const held=deferred();h.backend.hook((path,o,normal)=>path.includes('request_id=')?held.promise:null);const query=h.host.querySave();await until(()=>h.host.view().phase==='querying');assert.equal(h.host.comparisonSource(),null);
  held.resolve(h.backend.normal(h.backend.calls.at(-1).path,h.backend.calls.at(-1).options));assert.equal(await query,true);assert.equal(h.client.status('Intensity').dirty,true);assert.equal(h.host.comparisonSource(),null);
  h.backend.hook(null);assert.equal(await h.host.load({discardDirty:true}),true);assert.ok(h.host.comparisonSource());
});

test('switching family and back does not resurrect unconfirmed old head even when model dirty stayed false',async t=>{
  const h=await setup(t);await until(()=>h.host.view().phase==='restored');assert.ok(h.host.comparisonSource());h.host.editor().setQuantFamily('LFQ intensity');
  assert.equal(h.host.comparisonSource(),null);await until(()=>h.host.view().phase==='empty');h.host.editor().setQuantFamily('Intensity');
  assert.equal(h.host.view().design.dirty,false);assert.ok(h.client.savedRecord('Intensity'));assert.equal(h.host.comparisonSource(),null);
  assert.equal(await h.host.load(),true);assert.ok(h.host.comparisonSource());
});

test('same-source rebind and revoked source cannot revive a cached record or trigger I/O through getter',async t=>{
  const h=await setup(t);await until(()=>h.host.view().phase==='restored');const original=h.report.validatedSource(),n=h.backend.calls.length;
  h.source(null,false);assert.equal(h.host.comparisonSource(),null);h.source(original,false);assert.equal(h.host.comparisonSource(),null);assert.equal(h.backend.calls.length,n);
  assert.equal(await h.host.load(),true);assert.ok(h.host.comparisonSource());
  const fresh={file:clone(original.file),importDTO:clone(original.importDTO)};const waiting=[];
  h.backend.hook(()=>new Promise(resolve=>waiting.push(resolve)));h.source(fresh);assert.equal(h.host.comparisonSource(),null);await until(()=>waiting.length===1);
  assert.equal(h.host.comparisonSource(),null);waiting[0](h.backend.normal(h.backend.calls.at(-1).path,h.backend.calls.at(-1).options));await until(()=>h.host.view().phase==='restored');assert.ok(h.host.comparisonSource());
});

test('logout/dispose/context loss and late response cannot restore comparison source',async t=>{
  for(const mode of ['logout','dispose']){
    const h=await setup(t);await until(()=>h.host.view().phase==='restored');const hold=deferred();h.backend.hook(()=>hold.promise);const pending=h.host.load();await until(()=>h.host.view().phase==='loading');
    if(mode==='logout'){h.lose();assert.equal(h.host.comparisonSource(),null);h.host.view();}else h.host.dispose();
    hold.resolve(h.backend.normal(h.backend.calls.at(-1).path,h.backend.calls.at(-1).options));await pending;assert.equal(h.host.comparisonSource(),null);assert.equal(h.client.savedRecord('Intensity'),null);h.restore();assert.equal(h.host.comparisonSource(),null);h.host.dispose();
  }
});

test('bad record/hash cannot become saved source and provenance descriptors come from import, never design rows',async t=>{
  const b=server();b.heads.get('Intensity').design_sha256='0'.repeat(64);const h=await setup(t,{backend:b});await until(()=>h.host.view().phase==='failed');assert.equal(h.client.savedRecord('Intensity'),null);assert.equal(h.host.comparisonSource(),null);
  b.heads.set('Intensity',record(draft()));assert.equal(await h.host.load(),true);const source=h.host.comparisonSource();assert.equal(source.sample_columns.length,2);assert.equal(source.sample_design_record.design.rows.length,1);
  assert.deepEqual(source.sample_columns.map(c=>c.quant_family),['Intensity','LFQ intensity']);
});

test('a new validated dataset cancels old load and only a matching fresh record can become comparison source; changed project stays null',async t=>{
  const legacy=JSON.parse(await readFile(new URL('./fixtures/comparison-source/legacy.synthetic.json',import.meta.url),'utf8'));
  const h=await setup(t);await until(()=>h.host.view().phase==='restored');const oldClient=h.client,hold=deferred(),oldVersion=fixture.dto.datasetVersion.dataset_version_id;
  const design=draft();design.dataset_version_id=legacy.dto.datasetVersion.dataset_version_id;const fresh=record(design);
  h.backend.hook((path)=>path.includes(oldVersion)?hold.promise:Response.json({schemaVersion:1,project_id:fixture.file.project_id,dataset_version_id:design.dataset_version_id,quant_family:'Intensity',record:fresh}));
  const pending=h.host.load();await until(()=>h.host.view().phase==='loading');
  const report=createPrivateImportController({file:legacy.file,cryptoImpl:webcrypto,transport:{isCurrent:()=>true,get:()=>Response.json(legacy.dto)}});t.after(()=>report.dispose());assert.equal(await report.restore(),true);
  h.source(report.validatedSource());assert.equal(h.host.comparisonSource(),null);await until(()=>h.host.view().phase==='restored');hold.resolve(h.backend.normal('/api/v1/datasets/'+oldVersion+'/sample-designs?quant_family=intensity',{method:'GET'}));
  assert.equal(await pending,false);assert.equal(oldClient.savedRecord('Intensity'),null);assert.equal(h.host.comparisonSource().dataset_version_id,design.dataset_version_id);
  const changed=clone(report.validatedSource());changed.file.project_id='99999999-9999-4999-8999-999999999999';h.source(changed);assert.equal(h.host.comparisonSource(),null);assert.equal(h.host.editor(),null);
});
