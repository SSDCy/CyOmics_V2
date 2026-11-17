import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import {importRoute,importInput,validateImportSuccess,validateImportError as serverError,MAX_IMPORT_SUCCESS} from '../imports-contract.mjs';
import {validateImportDTO,validateImportError as browserError,importVersionKey,expectedConfiguration,LEGACY_IMPORT_PROFILE as OLD,CURRENT_IMPORT_PROFILE as NEW,IMPORT_CAP,LIMITS} from '../private-import-dto.js';
import {createImportTransport,createPrivateImportController} from '../private-import-controller.js';
import {createPrivateTxtController,createRawTransport,mountPrivateTxt} from '../private-txt-upload.js';
import {harness as bffHarness} from './raw-upload-harness.mjs';
const load=async name=>JSON.parse(await readFile(new URL(name,import.meta.url),'utf8'));
const legacy=await load('./fixtures/import-report-v1.synthetic.json'),current=await load('./fixtures/import-report-v2.synthetic.json');
const large=await load('./fixtures/import-capacity-large-v2.synthetic.json'),oneM=await load('./fixtures/import-capacity-1m-v1.synthetic.json');
const twoM=await load('./fixtures/import-capacity-2m-v2.synthetic.json'),over2M=await load('./fixtures/import-capacity-over2m-v2.synthetic.json');
const contract=await load('../../../profile-stage/approved-profiles.json'),oracles=await load('../../../profile-stage/synthetic-version-oracles.json');
const clone=v=>structuredClone(v),tick=()=>new Promise(resolve=>setImmediate(resolve));
const response=value=>()=>Response.json(value);
const fail=(status,code,more={})=>()=>Response.json({error:{code,...more}},{status});
function route(file,method='GET',version=null){
  const path='/api/v1/uploads/'+file.file_id+'/complete';
  return method==='POST'?{...importRoute(path,'POST'),project_id:file.project_id}:importRoute(path+'?project_id='+file.project_id+(version?'&version_id='+version:''),'GET');
}
const context=(method='GET',versionId=null)=>({method,versionId});
function reprofile(value,id){
  const result=clone(value),v=result.dto.datasetVersion,p=contract.profiles[id],key=oracles[v.file_id][id];
  v.configuration=clone(p.configuration);v.config_sha256=p.config_sha256;v.dataset_version_id=key;result.dto.importReport.report.dataset_version_id=key;return result;
}
async function accepts(value,method='GET',version=null){
  assert.equal(validateImportSuccess(value.dto,route(value.file,method,version)),value.dto);
  assert.equal(await validateImportDTO(value.dto,value.file,webcrypto,context(method,version)),value.dto);
}
async function rejects(value,method='GET',version=null){
  assert.throws(()=>validateImportSuccess(value.dto,route(value.file,method,version)));
  await assert.rejects(validateImportDTO(value.dto,value.file,webcrypto,context(method,version)));
}
function harness(file,replies){
  const calls=[],denials=[],timers=new Map();let active=true,serial=0;
  const transport=createImportTransport({projectId:file.project_id,fileId:file.file_id,isCurrent:()=>active,
    getOptions:signal=>({method:'GET',credentials:'same-origin',redirect:'error',cache:'no-store',signal,headers:{'X-Cyomics-Account-Context':'P'.repeat(22)}}),
    fetchImpl(path,options){calls.push([path,options]);assert.ok(replies.length,'Unexpected profile request');return replies.shift()(path,options);}});
  const model=createPrivateImportController({file,transport,cryptoImpl:webcrypto,onDenied:r=>denials.push(r.status),
    setTimer(fn,ms){timers.set(++serial,{fn,ms});return serial;},clearTimer:id=>timers.delete(id)});
  return{model,calls,timers,denials,lose(){active=false;}};
}
function dom(){
  const make=tag=>{let own='';return{tag,children:[],listeners:new Map(),attrs:{},disabled:false,hidden:false,
    get textContent(){return own+this.children.map(x=>x.textContent).join('');},set textContent(v){own=String(v);this.children=[];},append(...n){this.children.push(...n);},replaceChildren(...n){own='';this.children=n;},
    setAttribute(k,v){this.attrs[k]=v;},addEventListener(k,v){this.listeners.set(k,v);},removeEventListener(k,v){if(this.listeners.get(k)===v)this.listeners.delete(k);}};};
  const doc={createElement:tag=>Object.assign(make(tag),{ownerDocument:doc})},root=doc.createElement('root');return{root};
}

test('frozen independent configuration hashes and per-file UUID5 agree for exactly both approved profiles',async()=>{
  for(const value of[legacy,current]){
    const id=value===legacy?OLD:NEW,expected=contract.profiles[id],key=await importVersionKey(value.file,webcrypto,id);
    assert.equal(key.key,oracles[value.file.file_id][id]);assert.equal(key.configHash,expected.config_sha256);assert.deepEqual(key.config,expected.configuration);
    const config=await expectedConfiguration(webcrypto,id);assert.throws(()=>config.parser_release.source_pins['protein_groups_parser.py'].bytes++);
  }
  assert.equal(LIMITS.max_bytes,26214400);assert.equal(LIMITS.max_cells,2000000);assert.equal(IMPORT_CAP,1114112);assert.equal(MAX_IMPORT_SUCCESS,1114112);
  await assert.rejects(importVersionKey(current.file,webcrypto,'arbitrary_profile'));
});

test('GET without version reads either exact profile; explicit GET matches the file/profile key; POST permits only current',async()=>{
  for(const value of[legacy,current]){
    const key=value.dto.datasetVersion.dataset_version_id;await accepts(value);await accepts(value,'GET',key);
    await rejects(value,'GET',oracles[value.file.file_id][value===legacy?NEW:OLD]);
  }
  await accepts(current,'POST');await rejects(legacy,'POST');
  assert.throws(()=>validateImportSuccess(current.dto,{...route(current.file,'POST'),version_id:current.dto.datasetVersion.dataset_version_id}));
  await assert.rejects(validateImportDTO(current.dto,current.file,webcrypto,context('POST',current.dto.datasetVersion.dataset_version_id)));
  assert.throws(()=>validateImportSuccess(current.dto,{...route(current.file),method:'PUT'}));
  await assert.rejects(validateImportDTO(current.dto,current.file,webcrypto));
  await assert.rejects(validateImportDTO(current.dto,current.file,webcrypto,{method:'GET',versionId:null,profile:NEW}));
  assert.throws(()=>importInput(Buffer.from(JSON.stringify({project_id:current.file.project_id,profile_id:NEW}))));
  assert.throws(()=>importRoute(route(current.file).path+'&profile_id='+NEW,'GET'));
});

test('matched legacy retains10MiB/1M bounds; new allows25MiB/2M while rows/columns/metadata caps remain fixed',async()=>{
  await accepts(oneM);await accepts(twoM);await accepts(large);await rejects(over2M);
  await rejects(reprofile(twoM,OLD));await rejects(reprofile(large,OLD));
  const tooBig=clone(legacy);tooBig.file.bytes=10485761;
  for(const source of[tooBig.dto.datasetVersion.source,tooBig.dto.importReport.report.source])source.expected_bytes=tooBig.file.bytes;
  tooBig.dto.importReport.report.observed_bytes=tooBig.file.bytes;await rejects(tooBig);
  for(const field of['row_count','column_count']){
    const invalid=clone(large);invalid.dto.importReport.report[field]=field==='row_count'?10001:513;await rejects(invalid);
  }
  const huge=clone(current),col=huge.dto.importReport.columns[1];col.sample_label_raw='x'.repeat(300000);col.name_raw='Intensity '+col.sample_label_raw;
  huge.dto.importReport.sample_columns=huge.dto.importReport.columns.filter(c=>c.quant_family!==null);await rejects(huge);
});

test('unknown/hybrid config, SHA, limits, source pins and owner/file/SHA mutations cannot borrow either approved profile',async()=>{
  const changes=[
    v=>v.datasetVersion.config_sha256='a'.repeat(64),v=>v.datasetVersion.configuration.limits.max_cells++,
    v=>v.datasetVersion.configuration.limits.max_bytes=v.datasetVersion.configuration.limits.max_bytes===10485760?26214400:10485760,v=>v.datasetVersion.configuration.limits.max_records++,
    v=>v.datasetVersion.configuration.parser_release.source_commit='a'.repeat(40),v=>v.datasetVersion.configuration.parser_release.sha256='a'.repeat(64),
    v=>v.datasetVersion.configuration.parser_release.source_pins['protein_groups_parser.py'].sha256='a'.repeat(64),
    v=>v.datasetVersion.configuration.parser_release.source_pins['import_report.py'].bytes++,v=>v.datasetVersion.configuration.extra=true,
    v=>v.datasetVersion.project_id=legacy.file.file_id,v=>v.datasetVersion.file_id=legacy.file.project_id,
    v=>v.datasetVersion.source.expected_raw_sha256='c'.repeat(64),v=>v.datasetVersion.source.raw_version_key+='x',
    v=>v.importReport.report.observed_raw_sha256='c'.repeat(64),v=>v.importReport.report.persisted=false,
    v=>v.importReport.report.association_status='done',v=>v.importReport.report.scientific_analysis_status='completed',
    v=>v.importReport.row_source_spans[0].start_byte=0,v=>v.importReport.sample_columns.reverse(),
  ];
  for(const original of[legacy,current])for(const change of changes){const value=clone(original);change(value.dto);assert.notDeepEqual(value.dto,original.dto,'Mutation must change the synthetic fixture');await rejects(value);}
  const consistentWrongRaw=clone(current);for(const source of[consistentWrongRaw.dto.datasetVersion.source,consistentWrongRaw.dto.importReport.report.source]){
    source.expected_raw_sha256='c'.repeat(64);source.raw_version_key='raw_bytea_v1:'+current.file.file_id+':'+source.expected_raw_sha256;
  }
  consistentWrongRaw.dto.importReport.report.observed_raw_sha256='c'.repeat(64);
  await assert.rejects(validateImportDTO(consistentWrongRaw.dto,current.file,webcrypto,context()));
});

test('unknown version_key requires actual POST and the current per-file profile at BFF; GET and legacy keys reject',()=>{
  const key=oracles[current.file.file_id][NEW],oldKey=oracles[current.file.file_id][OLD],value={error:{code:'import_result_unknown',version_key:key}};
  assert.equal(serverError(504,value,route(current.file,'POST')),value);assert.equal(browserError(504,value,context('POST')).version_key,key);
  for(const r of[undefined,route(current.file),route(current.file,'GET',key),{...route(current.file,'POST'),file_id:large.file.file_id},
    {...route(current.file,'POST'),version_id:oldKey}])assert.throws(()=>serverError(504,value,r));
  assert.throws(()=>serverError(504,{error:{code:'import_result_unknown',version_key:oldKey}},route(current.file,'POST')));
  assert.throws(()=>browserError(504,value,context('GET')));
});

test('legacy refresh stays read-only with its original version, and invalid current responses do not trigger a legacy fallback',async()=>{
  const h=harness(legacy.file,[response(legacy.dto)]);assert.equal(await h.model.restore(),true);assert.equal(h.model.status().report_version_id,legacy.dto.datasetVersion.dataset_version_id);
  assert.equal(h.calls.length,1);assert.equal(h.calls[0][1].method,'GET');assert.equal(h.calls[0][0].includes('version_id'),false);assert.equal(await h.model.start(),false);h.model.dispose();
  const corrupt=clone(current.dto);corrupt.datasetVersion.configuration.parser_release.source_commit='unknown';
  for(const reply of[response(corrupt),fail(503,'import_persisted_report_invalid')]){
    const bad=harness(current.file,[reply,response(legacy.dto)]);await bad.model.restore();assert.equal(bad.model.status().phase,'unavailable');assert.equal(bad.model.status().report,null);assert.equal(bad.calls.length,1);bad.model.dispose();
  }
});

test('lost POST and unknown keys always check only current UUID; an existing legacy report never confirms the new write',async()=>{
  const key=oracles[current.file.file_id][NEW],oldKey=oracles[current.file.file_id][OLD];
  for(const first of[()=>Promise.reject(Error('synthetic disconnect')),fail(504,'import_result_unknown'),
    fail(504,'import_result_unknown',{version_key:key}),fail(504,'import_result_unknown',{version_key:oldKey}),fail(504,'import_result_unknown',{version_key:oracles[large.file.file_id][NEW]})]){
    const h=harness(current.file,[first,response(legacy.dto),fail(404,'dataset_version_not_found'),response(current.dto)]);
    await h.model.start();assert.equal(h.model.status().phase,'unknown');assert.equal(h.calls.length,1);assert.equal(await h.model.start(),false);
    assert.equal(await h.model.check(),false);assert.equal(h.model.status().phase,'unknown');assert.equal(h.model.status().report,null);
    assert.equal(await h.model.check(),false);assert.equal(h.model.status().phase,'unknown');assert.equal(h.model.status().can_start,false);
    assert.equal(await h.model.check(),true);assert.equal(h.model.status().report_version_id,key);
    assert.ok(h.calls.slice(1).every(([path,o])=>o.method==='GET'&&path.endsWith('&version_id='+key)));assert.equal(h.calls.filter(([,o])=>o.method==='POST').length,1);assert.equal(h.timers.size,0);h.model.dispose();
  }
});

test('current POST requires exact fresh current readback; old-profile200 on POST or fresh GET remains unknown',async()=>{
  for(const replies of[[response(legacy.dto)],[response(current.dto),response(legacy.dto)]]){
    const h=harness(current.file,replies);await h.model.start();assert.equal(h.model.status().phase,'unknown');assert.equal(h.model.status().report_saved,false);h.model.dispose();
  }
  const h=harness(current.file,[response(current.dto),response(current.dto)]);assert.equal(await h.model.start(),true);assert.equal(h.model.status().report_version_id,current.dto.datasetVersion.dataset_version_id);h.model.dispose();
});

test('large synthetic metadata restores into the original private file card and report view without extra upload or write',async()=>{
  const calls=[],f=large.file;
  const transport=createRawTransport({projectId:f.project_id,isCurrent:()=>true,getOptions:signal=>({method:'GET',credentials:'same-origin',cache:'no-store',redirect:'error',headers:{},signal}),
    fetchImpl(path,options){calls.push([path,options]);return path.endsWith('/files')?Response.json({items:[f],nextCursor:null}):Response.json(large.dto);}});
  const model=createPrivateTxtController({projectId:f.project_id,transport}),d=dom();const stop=mountPrivateTxt(d.root,model);
  for(let i=0;!d.root.textContent.includes('导入报告已保存')&&i<160;i++)await tick();
  assert.match(d.root.textContent,/导入报告已保存/);assert.ok(d.root.textContent.includes(String(large.dto.importReport.report.row_count)));
  assert.equal(calls.length,2);assert.ok(calls.every(([,o])=>o.method==='GET'));assert.equal(model.importReport(f.file_id).status().report_version_id,large.dto.datasetVersion.dataset_version_id);
  stop();model.dispose();assert.equal(d.root.textContent,'');
});

test('fake BFF preserves legacy reads, rejects legacy writes and streams large current POST/GET metadata within unchanged caps',async t=>{
  const h=bffHarness(t,{importsEnabled:true}),s=await h.signed();
  const path='/api/v1/uploads/'+legacy.file.file_id+'/complete',read=path+'?project_id='+legacy.file.project_id;
  h.reply(c=>c.deliver(legacy.dto));assert.equal((await h.send(read,s)).status,200);
  assert.equal((await h.send(read+'&version_id='+legacy.dto.datasetVersion.dataset_version_id,s)).status,200);
  const rejected=await h.send(path,{...s,method:'POST',value:{project_id:legacy.file.project_id}});
  assert.equal(rejected.status,504);assert.equal(rejected.body.error.code,'import_result_unknown');assert.equal(rejected.body.error.version_key,undefined);
  const bigPath='/api/v1/uploads/'+large.file.file_id+'/complete';h.reply(c=>c.deliver(large.dto));
  const posted=await h.send(bigPath,{...s,method:'POST',value:{project_id:large.file.project_id}});assert.equal(posted.status,200);assert.deepEqual(posted.body,large.dto);
  const reread=await h.send(bigPath+'?project_id='+large.file.project_id+'&version_id='+large.dto.datasetVersion.dataset_version_id,s);
  assert.equal(reread.status,200);assert.deepEqual(reread.body,large.dto);assert.ok(Buffer.byteLength(JSON.stringify(reread.body))<=MAX_IMPORT_SUCCESS);
  assert.equal(h.bridges.length,5);
});
