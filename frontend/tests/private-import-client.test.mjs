import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import {canonicalJSON, strictImportJSON, validateImportDTO, validateImportError, sourceBinding, expectedConfiguration, IMPORT_CAP} from '../private-import-dto.js';
import {createImportTransport, createPrivateImportController} from '../private-import-controller.js';
import {createRawTransport, createPrivateTxtController, mountPrivateTxt} from '../private-txt-upload.js';
import {mountPrivateImportReport} from '../private-import-report-view.js';

const fixture=JSON.parse(await readFile(new URL('./fixtures/import-report-v2.synthetic.json',import.meta.url),'utf8'));
const clone=v=>structuredClone(v), file=()=>clone(fixture.file), dto=()=>clone(fixture.dto);
const version=fixture.dto.datasetVersion.dataset_version_id;
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve};};
const ok=(v=dto())=>()=>Response.json(v);
const error=(status,code,extra={})=>()=>Response.json({error:{code,...extra}},{status});
function harness(replies=[],extra={}){
  const calls=[],timers=new Map(),delays=[],denied=[];let live=true,clock=0,serial=0;
  const transport=createImportTransport({projectId:file().project_id,fileId:file().file_id,isCurrent:()=>live,
    getOptions:signal=>({method:'GET',credentials:'same-origin',cache:'no-store',redirect:'error',headers:{'X-Cyomics-Account-Context':'A'.repeat(22)},signal}),
    fetchImpl(path,options){calls.push([path,options]);assert.ok(replies.length,'Unexpected synthetic import request');return replies.shift()(path,options);}});
  const model=createPrivateImportController({file:file(),transport,cryptoImpl:webcrypto,onDenied:r=>denied.push(r.status),now:()=>clock,
    setTimer(fn,ms){assert.ok(ms>0&&ms<=75000);delays.push(ms);timers.set(++serial,{fn,ms});return serial;},clearTimer:id=>timers.delete(id),...extra});
  return{model,calls,timers,delays,denied,transport,advance:ms=>clock+=ms,lose:()=>live=false,
    expire(){for(const [id,v]of [...timers]){timers.delete(id);v.fn();}}};
}
function dom(){
  const create=tag=>{let own='';return{tag,children:[],listeners:new Map(),attrs:{},disabled:false,hidden:false,
    get textContent(){return own+this.children.map(c=>c.textContent).join('');},set textContent(v){own=String(v);this.children=[];},
    append(...v){this.children.push(...v);},replaceChildren(...v){own='';this.children=v;},setAttribute(k,v){this.attrs[k]=v;},
    addEventListener(k,v){this.listeners.set(k,v);},removeEventListener(k,v){if(this.listeners.get(k)===v)this.listeners.delete(k);}};};
  const root=create('root');const doc={createElement:tag=>Object.assign(create(tag),{ownerDocument:doc})};root.ownerDocument=doc;const walk=n=>[n,...n.children.flatMap(walk)];return{root,all:()=>walk(root)};
}
const binding={project_id:file().project_id,raw_file_id:file().file_id,expected_raw_sha256:file().sha256};

test('independent Python config SHA/UUID5 fixture validates; returned configuration cannot poison parser authority',async()=>{
  assert.equal(await validateImportDTO(dto(),file(),webcrypto,{method:'GET',versionId:null}).then(v=>v.datasetVersion.dataset_version_id),version);
  const c=await expectedConfiguration(webcrypto);assert.throws(()=>c.parser_release.source_pins['import_report.py'].sha256='b'.repeat(64));
  c.limits.max_bytes=1;await validateImportDTO(dto(),file(),webcrypto,{method:'GET',versionId:null});
});

test('exact source and every persisted DTO layer reject mismatches, forged counts, columns, spans and warnings',async()=>{
  const mutations=[
    v=>v.extra=true,v=>v.datasetVersion.extra=true,v=>v.datasetVersion.project_id=file().file_id,v=>v.datasetVersion.file_id=file().project_id,
    v=>v.datasetVersion.dataset_version_id=file().project_id,v=>v.datasetVersion.config_sha256='b'.repeat(64),v=>v.datasetVersion.configuration.limits.max_bytes++,
    v=>v.datasetVersion.source.expected_bytes++,v=>v.datasetVersion.source.raw_version_key+='x',v=>v.datasetVersion.source.expected_raw_sha256='b'.repeat(64),
    v=>v.datasetVersion.createdAt='2026-02-30T00:00:00.000000Z',v=>v.datasetVersion.representation='table',v=>v.datasetVersion.parsed_table_materialized=true,
    v=>v.importReport.extra=true,v=>v.importReport.schemaVersion=2,v=>v.importReport.parsed_table_materialized=true,
    v=>v.importReport.report.source.project_id=file().file_id,v=>v.importReport.report.status='saved',v=>v.importReport.report.observed_raw_sha256='b'.repeat(64),
    v=>v.importReport.report.persisted=false,v=>v.importReport.report.dataset_version_id=file().project_id,v=>v.importReport.report.source_complete=false,
    v=>v.importReport.report.scientific_analysis_status='completed',v=>v.importReport.report.association_status='associated',v=>v.importReport.report.association_counts={},
    v=>v.importReport.report.issues=[{}],v=>v.importReport.report.row_count=10001,v=>v.importReport.report.row_count=true,v=>v.importReport.report.column_count=513,
    v=>v.importReport.report.intensity_sample_count++,v=>v.importReport.report.member_occurrences=100001,v=>v.importReport.report.empty_group_count=3,
    v=>v.importReport.report.quant_exact_zero_string_cells=99,v=>v.importReport.report.crlf_count=-1,v=>v.importReport.report.leading_bom=0,
    v=>v.importReport.columns.reverse(),v=>v.importReport.columns[0].name_raw='Protein ids',v=>v.importReport.columns[1].sample_label_raw='trimmed',
    v=>v.importReport.columns[2].quant_family='LFQ',v=>v.importReport.columns[0].name_raw='\uD800',v=>v.importReport.sample_columns.reverse(),
    v=>v.importReport.header_span.start_byte=1,v=>v.importReport.header_span.end_byte=129,v=>v.importReport.header_span.start_line=0,
    v=>v.importReport.row_source_spans.pop(),v=>v.importReport.row_source_spans[0].record_number=0,v=>v.importReport.row_source_spans[0].start_byte=79,
    v=>v.importReport.row_source_spans[0].end_byte=129,v=>v.importReport.row_source_spans[1].start_line=0,v=>v.importReport.row_source_spans[0].extra='text',
    v=>v.importReport.warnings=[],v=>v.importReport.warnings[0].count=2,v=>v.importReport.report.extra='private',
  ];
  for(const mutate of mutations){const v=dto();mutate(v);await assert.rejects(validateImportDTO(v,file(),webcrypto,{method:'GET',versionId:null}),/invalid/);}
  for(const change of [v=>v.extra=true,v=>v.filename='../x.txt',v=>v.filename='x\0.txt',v=>v.request_id='bad',v=>v.createdAt='2026-02-30T00:00:00Z',v=>v.bytes=0]){
    const f=file();change(f);assert.throws(()=>sourceBinding(f));
  }
});

test('JSON preflight rejects duplicate escaped keys, nesting, arrays and unpaired Unicode without accepting JS extensions',()=>{
  for(const text of ['{"a":1,"a":2}','{"a":1,"\\u0061":2}','{"x":{"a":1,"a":2}}','[1,]','{"x":NaN}','{"x":"\\ud800"}','['.repeat(34)+'0'+']'.repeat(34),'['+'0,'.repeat(10000)+'0]'])assert.throws(()=>strictImportJSON(text));
  assert.deepEqual(strictImportJSON(' {"a":[0,false,null,"🧪"]} '),{a:[0,false,null,'🧪']});
});

test('errors allow only nested status-matched fixed code, appropriate UUID key and bounded exact issue coordinates',()=>{
  const issue={code:'TXT_ROW_WIDTH',record_number:2,line:3,column_ordinal:1,limit_key:null};
  assert.deepEqual(validateImportError(422,{error:{code:'txt_import_failed',issues:[issue]}}).issues,[issue]);
  assert.equal(validateImportError(504,{error:{code:'import_result_unknown',version_key:version}},{method:'POST',versionId:null}).version_key,version);
  for(const [status,body]of [[504,{error:{code:'import_result_unknown'},version_key:version}],[422,{error:{code:'txt_import_failed',issues:Array(9).fill(issue)}}],
    [422,{error:{code:'txt_import_failed',issues:[{...issue,raw:'secret'}]}}],[422,{error:{code:'txt_import_failed',issues:[{...issue,code:'secret'}]}}],
    [422,{error:{code:'txt_import_failed',issues:[{...issue,line:-1}]}}],[422,{error:{code:'txt_import_failed',issues:[{...issue,limit_key:'secret'}]}}],
    [400,{error:{code:'invalid_import_request',version_key:version}}],[503,{error:{code:'txt_import_failed'}}],[504,{error:{code:'import_result_unknown',version_key:'BAD'}}]])assert.throws(()=>validateImportError(status,body));
});

test('explicit POST body and closure-scoped options are exact; only validated equal fresh GET enables ready and suppresses duplicates',async()=>{
  const later=deferred(),reordered=JSON.parse(JSON.stringify(dto(),(k,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.entries(v).reverse()):v));
  const h=harness([ok(),()=>later.promise]);const task=h.model.start();assert.equal(await h.model.start(),false);
  for(let i=0;h.calls.length<2&&i<80;i++)await tick();assert.equal(h.calls.length,2);assert.equal(h.model.status().report_saved,false);
  later.resolve(Response.json(reordered));assert.equal(await task,true);assert.equal(h.model.status().phase,'ready');assert.equal(h.model.status().report.row_count,2);
  assert.deepEqual(h.calls.map(([p,o])=>[o.method,p]),[['POST','/api/v1/uploads/'+file().file_id+'/complete'],['GET','/api/v1/uploads/'+file().file_id+'/complete?project_id='+file().project_id+'&version_id='+version]]);
  assert.equal(h.calls[0][1].body,JSON.stringify({project_id:file().project_id}));assert.equal(h.calls[1][1].body,undefined);
  for(const [,o]of h.calls){assert.equal(o.credentials,'same-origin');assert.equal(o.cache,'no-store');assert.equal(o.redirect,'error');assert.equal(o.headers['X-Cyomics-Account-Context'],'A'.repeat(22));assert.equal(o.headers.Authorization,undefined);assert.ok(o.signal instanceof AbortSignal);}
  assert.equal(h.calls[0][1].headers['Content-Type'],'application/json');assert.equal(h.calls[0][1].headers['X-Cyomics-Request'],'1');
  assert.deepEqual(h.delays,[75000,60000,15000]);assert.equal(h.timers.size,0);assert.equal(await h.model.start(),false);assert.equal(await h.model.check(),false);assert.equal(h.calls.length,2);
  assert.equal(JSON.stringify(h.model.status()).includes('A'.repeat(22)),false);h.model.dispose();assert.equal(h.model.status().binding,null);assert.equal(h.model.status().source_filename,'');
});

test('restore is bounded read only once; no-version404 is idle, valid restore is ready, unconfigured transport stays unavailable',async()=>{
  for(const [reply,phase]of [[error(404,'dataset_version_not_found'),'idle'],[ok(),'ready'],[error(503,'import_unavailable'),'unavailable']]){
    const h=harness([reply]);await h.model.restore();await h.model.restore();assert.equal(h.model.status().phase,phase);assert.equal(h.calls.length,1);assert.equal(h.calls[0][1].method,'GET');assert.equal(h.calls[0][0].includes('version_id'),false);assert.deepEqual(h.delays,[15000,15000]);h.model.dispose();
  }
  const m=createPrivateImportController({file:file()});await m.restore();assert.equal(m.status().phase,'unavailable');assert.equal(await m.start(),false);m.dispose();
});

test('a valid but unequal readback cannot save; POST unknown never retries and explicit 404 stays unknown until exact read-only recovery',async()=>{
  const different=dto();different.datasetVersion.createdAt='2026-11-17T00:00:01.000001Z';
  for(const first of [[ok(),ok(different)],[error(504,'import_result_unknown',{version_key:version})],[()=>Promise.reject(Error('secret transport'))]]){
    const h=harness([...first,error(404,'dataset_version_not_found'),ok()]);await h.model.start();assert.equal(h.model.status().phase,'unknown');assert.equal(h.model.status().report,null);
    const count=h.calls.length;assert.equal(await h.model.start(),false);assert.equal(h.calls.length,count);
    assert.equal(await h.model.check(),false);assert.equal(h.model.status().phase,'unknown');assert.equal(await h.model.check(),true);assert.equal(h.model.status().phase,'ready');
    assert.equal(h.calls.filter(([,o])=>o.method==='POST').length,1);assert.equal(JSON.stringify(h.model.status()).includes('secret'),false);h.model.dispose();
  }
});

test('strict known422 preserves safe issue coordinates, enables only manual retry, and wrong version-key cannot leak or mark success',async()=>{
  const issue={code:'TXT_ROW_WIDTH',record_number:2,line:3,column_ordinal:1,limit_key:null};
  const h=harness([error(422,'txt_import_failed',{issues:[issue]}),ok(),ok()]);await h.model.start();assert.equal(h.model.status().phase,'failed');assert.deepEqual(h.model.status().issues,[issue]);assert.equal(h.calls.length,1);assert.equal(h.model.status().can_start,true);
  assert.equal(await h.model.start(),true);h.model.dispose();
  const wrong=harness([error(504,'import_result_unknown',{version_key:file().project_id})]);await wrong.model.start();assert.equal(wrong.model.status().phase,'unknown');assert.equal(wrong.model.status().report,null);wrong.model.dispose();
});

test('401/403/409 and project/file404 clear bound state and delegate denial even with malformed bodies',async()=>{
  for(const reply of [()=>{const r=new Response('secret',{status:401});Object.defineProperty(r,'redirected',{value:true});return r;},()=>new Response('secret',{status:401}),()=>new Response('secret',{status:403}),()=>new Response('secret',{status:409}),error(404,'project_not_found'),error(404,'file_not_found')]){
    const h=harness([reply]);await h.model.start();assert.equal(h.denied.length,1);assert.equal(h.model.status().report,null);assert.equal(h.model.status().binding,null);assert.equal(h.model.status().source_filename,'');assert.equal(h.model.status().can_start,false);assert.equal(h.timers.size,0);h.model.dispose();
  }
});

test('MIME, redirect, UTF8, duplicate keys, byte caps, length mismatch and forged200 all produce unknown without retries',async()=>{
  const text=JSON.stringify(dto());
  const bad=[()=>new Response(text,{headers:{'content-type':'text/plain'}}),()=>{const r=Response.json(dto());Object.defineProperty(r,'redirected',{value:true});return r;},
    ()=>new Response(new Uint8Array([0xff]),{headers:{'content-type':'application/json'}}),()=>new Response('{"x":1,"x":2}',{headers:{'content-type':'application/json'}}),
    ()=>new Response(text,{headers:{'content-type':'application/json','content-length':String(IMPORT_CAP+1)}}),()=>new Response(' '.repeat(IMPORT_CAP+1),{headers:{'content-type':'application/json'}}),
    ()=>new Response(text,{headers:{'content-type':'application/json','content-length':String(new TextEncoder().encode(text).length+1)}}),()=>Response.json({ok:true}),
    ()=>new Response(' '.repeat(4097),{status:422,headers:{'content-type':'application/json'}})];
  for(const reply of bad){const h=harness([reply]);await h.model.start();assert.equal(h.model.status().phase,'unknown');assert.equal(h.model.status().report_saved,false);assert.equal(h.calls.length,1);assert.equal(await h.model.start(),false);assert.equal(h.timers.size,0);h.model.dispose();}
});

test('75s overall and 15s GET budgets cover fetch, stream and async validation; monotonic expiry rejects late success',async()=>{
  const pending=deferred(),h=harness([()=>pending.promise]);const task=h.model.start();for(let i=0;!h.calls.length&&i<80;i++)await tick();assert.equal(h.calls.length,1);assert.deepEqual(h.delays,[75000,60000]);h.expire();assert.equal(await task,false);assert.equal(h.model.status().phase,'unknown');assert.equal(h.timers.size,0);assert.equal(h.calls[0][1].signal.aborted,true);pending.resolve(Response.json(dto()));await tick();h.model.dispose();
  const get=harness([()=>new Response(new ReadableStream(),{headers:{'content-type':'application/json'}})]);const load=get.model.restore();await tick();get.expire();assert.equal(await load,false);assert.equal(get.model.status().phase,'unavailable');assert.equal(get.timers.size,0);get.model.dispose();
  const mono=harness([()=>{mono.advance(60000);return Response.json(dto());}]);await mono.model.start();assert.equal(mono.model.status().phase,'unknown');assert.equal(mono.calls.length,1);mono.model.dispose();
  const combined=harness([()=>{combined.advance(59999);return Response.json(dto());},()=>{combined.advance(15000);return Response.json(dto());}]);await combined.model.start();assert.equal(combined.model.status().phase,'unknown');assert.equal(combined.calls.length,2);assert.equal(combined.timers.size,0);combined.model.dispose();
  const slow=deferred(),crypto={subtle:{digest:()=>slow.promise}},sha=harness([ok()],{cryptoImpl:crypto});const hashing=sha.model.start();await tick();sha.advance(75000);sha.expire();assert.equal(await hashing,false);assert.equal(sha.model.status().phase,'failed');assert.equal(sha.calls.length,0);slow.resolve(new Uint8Array(32).buffer);await tick();sha.model.dispose();
});

test('dispose, identity invalidation and subscriber reentry suppress every late report and clear private snapshots',async()=>{
  for(const mode of ['dispose','identity']){
    const later=deferred(),h=harness([()=>later.promise]);const task=h.model.start();for(let i=0;!h.calls.length&&i<80;i++)await tick();assert.equal(h.calls.length,1);if(mode==='dispose')h.model.dispose();else h.lose();later.resolve(Response.json(dto()));assert.equal(await task,false);
    assert.equal(h.model.status().binding,null);assert.equal(h.model.status().source_filename,'');assert.equal(h.model.status().report,null);assert.equal(h.calls.length,1);assert.equal(h.timers.size,0);h.model.dispose();
  }
  for(const phase of ['parsing','ready']){
    const h=harness([ok(),ok()]);h.model.subscribe(()=>{if(h.model.status().phase===phase)h.model.dispose();});assert.equal(await h.model.start(),false);assert.equal(h.calls.length,phase==='parsing'?0:2);assert.equal(h.model.status().report,null);assert.equal(h.timers.size,0);
  }
  const restored=harness([ok()]);restored.model.subscribe(()=>{if(restored.model.status().phase==='ready')restored.model.dispose();});assert.equal(await restored.model.restore(),false);assert.equal(restored.model.status().report,null);
});

test('multiple stored file controllers survive raw repaint/language changes and never auto-write or duplicate pending POST',async()=>{
  const second={...file(),file_id:'55555555-5555-4555-8555-555555555555',request_id:'66666666-6666-4666-8666-666666666666',sha256:'b'.repeat(64),createdAt:'2026-11-15T00:00:00Z'},later=deferred(),calls=[];
  let rawItems=[file(),second];
  const transport=createRawTransport({projectId:file().project_id,isCurrent:()=>true,getOptions:signal=>({method:'GET',credentials:'same-origin',cache:'no-store',redirect:'error',headers:{'X-Cyomics-Account-Context':'A'.repeat(22)},signal}),fetchImpl(path,o){calls.push([path,o]);if(path.endsWith('/files'))return Response.json({items:rawItems,nextCursor:null});if(o.method==='POST')return later.promise;if(path.includes('version_id'))return Response.json(dto());return error(404,'dataset_version_not_found')();}});
  const m=createPrivateTxtController({projectId:file().project_id,transport,cryptoImpl:webcrypto}),zh=dom();let stop=mountPrivateTxt(zh.root,m);await tick();await tick();
  assert.equal(calls.filter(([p])=>p.startsWith('/api/v1/uploads/')).length,2);assert.equal(calls.some(([,o])=>o.method==='POST'),false);
  const one=m.importReport(file().file_id),two=m.importReport(second.file_id),task=one.start();assert.equal(await one.start(),false);for(let i=0;!calls.some(([,o])=>o.method==='POST')&&i<80;i++)await tick();assert.equal(calls.filter(([,o])=>o.method==='POST').length,1);
  m.select(new File(['x'],'another.txt'));assert.equal(m.importReport(file().file_id),one);stop();const en=dom();stop=mountPrivateTxt(en.root,m,(_,english)=>english);
  assert.equal(m.importReport(file().file_id),one);assert.equal(m.importReport(second.file_id),two);assert.equal(calls.filter(([,o])=>o.method==='POST').length,1);
  later.resolve(Response.json(dto()));assert.equal(await task,true);await tick();assert.match(en.root.textContent,/Import report saved/);assert.equal(calls.filter(([,o])=>o.method==='POST').length,1);
  rawItems=[second];await m.reload();assert.equal(one.status().binding,null);assert.equal(m.importReport(file().file_id),null);assert.equal(m.importReport(second.file_id),two);
  stop();m.dispose();assert.equal(two.status().binding,null);assert.equal(en.root.textContent,'');
});

test('replaced source SHA gets a fresh controller, and child auth denial clears the entire raw parent',async()=>{
  let items=[file()],denials=0,deny=false;
  const transport=createRawTransport({projectId:file().project_id,isCurrent:()=>true,getOptions:signal=>({method:'GET',headers:{},signal}),fetchImpl(path){return path.endsWith('/files')?Response.json({items,nextCursor:null}):deny?new Response('private',{status:401}):error(404,'dataset_version_not_found')();}});
  const m=createPrivateTxtController({projectId:file().project_id,transport,onDenied(){denials++;}});await m.start();const old=m.importReport(file().file_id);
  items=[{...file(),sha256:'b'.repeat(64)}];await m.reload();assert.equal(old.status().binding,null);const next=m.importReport(file().file_id);assert.notEqual(next,old);
  deny=true;await next.restore();assert.equal(denials,1);assert.equal(m.status().phase,'disposed');assert.deepEqual(m.status().items,[]);assert.equal(next.status().binding,null);
});

test('fixed bilingual failure reason and validated numeric positions are safe; raw error text and forged issue fields never render',()=>{
  for(const en of [false,true])for(const malformed of [false,true]){
    const d=dom(),issue={code:'TXT_ROW_WIDTH',record_number:2,line:3,column_ordinal:1,limit_key:null};if(malformed)issue.raw='SECRET <img>';
    const state={phase:'failed',source_verified:true,binding,error_verified:true,error_code:'txt_import_failed',issues:[issue],error:'SECRET <script>'};
    const model={status:()=>state,subscribe:()=>()=>{},start(){},check(){}};const stop=mountPrivateImportReport(d.root,model,{binding,t:en?(_,eng)=>eng:zh=>zh});
    assert.match(d.root.textContent,en?/TXT format or content validation failed/:/TXT 格式或内容校验未通过/);
    assert.equal(d.root.textContent.includes('TXT_ROW_WIDTH'),!malformed);assert.doesNotMatch(d.root.textContent,/SECRET|导入报告已保存|Import report saved/);stop();assert.equal(d.root.textContent,'');
  }
});

test('R1 transient restore failure stays unconfirmed until explicit read-only UI check recovers the same controller',async()=>{
  for(const failure of ['503','429','network','timeout']){
    const later=deferred();
    const first=failure==='503'?error(503,'import_unavailable'):failure==='429'?error(429,'import_rate_limited'):
      failure==='network'?()=>Promise.reject(Error('synthetic transient')):()=>new Response(new ReadableStream(),{headers:{'content-type':'application/json'}});
    const h=harness([first,()=>later.promise]),d=dom();
    const stop=mountPrivateImportReport(d.root,h.model,{binding});
    const restored=h.model.restore();
    if(failure==='timeout'){await tick();h.expire();}
    assert.equal(await restored,false);assert.equal(h.model.status().phase,'unavailable');
    assert.equal(h.model.status().report,null);assert.equal(h.model.status().report_saved,false);assert.equal(h.model.status().source_verified,true);
    assert.equal(h.model.status().can_start,false);assert.equal(h.model.status().can_check,true);
    assert.equal(d.all().some(n=>n.tag==='dd'),false);assert.doesNotMatch(d.root.textContent,/导入报告已保存|synthetic\.txt/);
    const button=d.all().find(n=>n.tag==='button'&&n.textContent==='查询导入结果');
    assert.equal(button.hidden,false);assert.equal(button.disabled,false);
    assert.equal(await h.model.restore(),false);await tick();assert.equal(h.calls.length,1);
    const click=button.listeners.get('click');click();click();
    assert.equal(h.calls.length,2);assert.equal(h.model.status().phase,'checking');assert.equal(h.model.status().can_check,false);
    assert.equal(d.all().some(n=>n.tag==='dd'),false);assert.equal(await h.model.check(),false);
    later.resolve(Response.json(dto()));
    for(let i=0;h.model.status().phase!=='ready'&&i<80;i++)await tick();await tick();
    assert.equal(h.model.status().phase,'ready');assert.equal(h.model.status().report_saved,true);assert.match(d.root.textContent,/导入报告已保存/);
    assert.equal(h.model.status().report.row_count,fixture.dto.importReport.report.row_count);
    assert.equal(h.calls.length,2);assert.ok(h.calls.every(([,o])=>o.method==='GET'&&o.body===undefined));
    assert.equal(h.calls[1][0],'/api/v1/uploads/'+file().file_id+'/complete?project_id='+file().project_id);
    assert.equal(h.timers.size,0);stop();h.model.dispose();
  }
});

test('R1 retry remains gated by source/context/transport and preserves unknown-write semantics through transient and404 reads',async()=>{
  const h=harness([error(503,'import_unavailable')]);await h.model.restore();
  const d=dom(),stop=mountPrivateImportReport(d.root,h.model,{binding:{...binding,expected_raw_sha256:'b'.repeat(64)}});
  const wrong=d.all().find(n=>n.tag==='button'&&n.textContent==='查询导入结果');
  assert.equal(wrong.hidden,true);assert.equal(wrong.disabled,true);wrong.listeners.get('click')();assert.equal(h.calls.length,1);stop();
  h.lose();assert.equal(h.model.status().source_verified,false);assert.equal(h.model.status().can_check,false);assert.equal(await h.model.check(),false);assert.equal(h.calls.length,1);
  const lost=dom(),end=mountPrivateImportReport(lost.root,h.model,{binding});const denied=lost.all().find(n=>n.tag==='button'&&n.textContent==='查询导入结果');
  assert.equal(denied.hidden,true);assert.equal(denied.disabled,true);denied.listeners.get('click')();assert.equal(h.calls.length,1);end();h.model.dispose();
  const noTransport=createPrivateImportController({file:file()});await noTransport.restore();assert.equal(noTransport.status().can_check,false);assert.equal(await noTransport.check(),false);noTransport.dispose();
  const unknown=harness([error(504,'import_result_unknown',{version_key:version}),error(503,'import_unavailable'),error(404,'dataset_version_not_found'),ok()]);
  await unknown.model.start();assert.equal(unknown.model.status().phase,'unknown');
  for(let i=0;i<2;i++){
    assert.equal(await unknown.model.check(),false);assert.equal(unknown.model.status().phase,'unknown');assert.equal(unknown.model.status().can_start,false);
    assert.equal(unknown.model.status().report,null);assert.equal(await unknown.model.start(),false);
  }
  assert.equal(await unknown.model.check(),true);assert.equal(unknown.model.status().phase,'ready');
  assert.equal(unknown.calls.filter(([,o])=>o.method==='POST').length,1);assert.equal(unknown.calls.length,4);unknown.model.dispose();
});
