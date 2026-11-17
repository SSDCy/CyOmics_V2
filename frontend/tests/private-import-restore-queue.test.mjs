import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import {createPrivateTxtController,createRawTransport,mountPrivateTxt} from '../private-txt-upload.js';
import {createPrivateImportController} from '../private-import-controller.js';
import {createPrivateProjectController,createPrivateProjectTransport} from '../private-projects.js';

const fixtures=JSON.parse(await readFile(new URL('./fixtures/report-restore.synthetic.json',import.meta.url),'utf8'));
const current=fixtures[0],legacy=fixtures[1],projectId=current.file.project_id;
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return{promise,resolve,reject};};
async function until(predicate){const end=Date.now()+1200;while(!predicate()&&Date.now()<end)await tick();assert.ok(predicate(),'Synthetic state did not settle as expected');}
function dom(){
  const make=tag=>{let own='';return{tag,children:[],attrs:{},listeners:new Map(),disabled:false,hidden:false,
    get textContent(){return own+this.children.map(c=>c.textContent).join('');},set textContent(v){own=String(v);this.children=[];},
    append(...n){this.children.push(...n);},replaceChildren(...n){own='';this.children=n;},setAttribute(k,v){this.attrs[k]=v;},
    addEventListener(k,v){this.listeners.set(k,v);},removeEventListener(k,v){if(this.listeners.get(k)===v)this.listeners.delete(k);}};};
  const doc={createElement:tag=>Object.assign(make(tag),{ownerDocument:doc})},root=doc.createElement('root');return{root};
}
function server(){
  let items=[current.file,legacy.file],active=0,maxInFlight=0,live=true;
  const calls=[],first=deferred();let firstDone=null,firstResponse=null,held=true;
  const reportCalls=()=>calls.filter(c=>c.path.startsWith('/api/v1/uploads/'));
  function tracked(value,status,done){
    const bytes=new TextEncoder().encode(JSON.stringify(value));let sent=false,finished=false;
    const finish=()=>{if(!finished){finished=true;done();}};
    return{status,redirected:false,headers:new Headers({'content-type':'application/json','content-length':String(bytes.length)}),body:{
      getReader(){return{read(){if(!sent){sent=true;return Promise.resolve({done:false,value:bytes});}finish();return Promise.resolve({done:true});},cancel(){finish();return Promise.resolve();},releaseLock(){}};},cancel(){finish();return Promise.resolve();}}};
  }
  const fetchImpl=(path,options)=>{
    calls.push({path,options});assert.equal(options.method,'GET','This test permits no automatic POST');
    if(path==='/api/v1/projects/'+projectId+'/files')return Response.json({items,nextCursor:null});
    const match=/^\/api\/v1\/uploads\/([0-9a-f-]{36})\/complete\?project_id=([0-9a-f-]{36})$/.exec(path);assert.ok(match,'Unexpected synthetic route');assert.equal(match[2],projectId);
    const value=fixtures.find(f=>f.file.file_id===match[1]);assert.ok(value);
    active++;maxInFlight=Math.max(maxInFlight,active);let ended=false;
    const done=()=>{if(!ended){ended=true;active--;}};options.signal.addEventListener('abort',done,{once:true});
    if(active>1)return tracked({error:{code:'import_busy'}},429,done);
    if(held){held=false;firstDone=done;firstResponse=()=>tracked(value.dto,200,done);return first.promise;}
    return tracked(value.dto,200,done);
  };
  return{calls,fetchImpl,reportCalls,get maxInFlight(){return maxInFlight;},isCurrent:()=>live,lose(){live=false;},setItems(value){items=value;},
    release(status=200,code='dataset_version_not_found'){
      first.resolve(status===200?firstResponse():tracked({error:{code}},status,firstDone));
    },reject(){firstDone();first.reject(Error('synthetic disconnected GET'));}};
}
function setup(t,values=[current.file,legacy.file]){
  const backend=server();backend.setItems(values);
  const transport=createRawTransport({projectId,fetchImpl:backend.fetchImpl,getOptions:signal=>({method:'GET',credentials:'same-origin',cache:'no-store',redirect:'error',headers:{'X-Cyomics-Account-Context':'Q'.repeat(22)},signal}),isCurrent:backend.isCurrent});
  const model=createPrivateTxtController({projectId,transport});let d=dom(),stop=mountPrivateTxt(d.root,model);
  const cleanup=()=>{stop();model.dispose();};t.after(cleanup);
  return{backend,model,get root(){return d.root;},remount(en=false){stop();d=dom();stop=mountPrivateTxt(d.root,model,en?(_,english)=>english:zh=>zh);},cleanup};
}
const ready=(model,values=[current,legacy])=>values.every(value=>model.importReport(value.file.file_id)?.status().phase==='ready');

test('regression: legacy/current cards and a full model rebuild each restore once with only one GET in flight',async t=>{
  for(let refresh=0;refresh<2;refresh++){
    const h=setup(t);await until(()=>h.backend.reportCalls().length>=1);
    assert.equal(h.backend.reportCalls().length,1);const queued=h.model.importReport(legacy.file.file_id);
    assert.equal(queued.status().phase,'checking');assert.equal(queued.status().can_start,false);assert.equal(queued.status().can_check,false);
    assert.equal(await queued.start(),false);assert.equal(await queued.check(),false);assert.equal(await queued.restore(),false);
    h.backend.release();await until(()=>ready(h.model));
    assert.equal(h.backend.reportCalls().length,2);assert.equal(h.backend.maxInFlight,1);
    assert.equal(h.model.importReport(current.file.file_id).status().report_version_id,current.dto.datasetVersion.dataset_version_id);
    assert.equal(h.model.importReport(legacy.file.file_id).status().report_version_id,legacy.dto.datasetVersion.dataset_version_id);
    assert.ok(h.backend.calls.every(c=>c.options.method==='GET'));h.cleanup();
  }
});

test('all four existing files follow list order and each initial restore is issued at most once',async t=>{
  const h=setup(t,fixtures.map(f=>f.file));await until(()=>h.backend.reportCalls().length>=1);assert.equal(h.backend.reportCalls().length,1);
  for(const value of fixtures.slice(1))assert.equal(h.model.importReport(value.file.file_id).status().phase,'checking');
  h.backend.release();await until(()=>ready(h.model,fixtures));assert.equal(h.backend.maxInFlight,1);
  assert.deepEqual(h.backend.reportCalls().map(c=>c.path.split('/')[4]),fixtures.map(f=>f.file.file_id));
  for(const value of fixtures){assert.equal(await h.model.importReport(value.file.file_id).restore(),false);assert.equal(h.model.importReport(value.file.file_id).status().report_version_id,value.dto.datasetVersion.dataset_version_id);}
  assert.equal(h.backend.reportCalls().length,4);
});

test('first404/429/503/network failure releases the next initial GET but never retries the failed file',async t=>{
  for(const failure of[404,429,503,'network']){
    const h=setup(t);await until(()=>h.backend.reportCalls().length>=1);assert.equal(h.backend.reportCalls().length,1);
    if(failure==='network')h.backend.reject();else h.backend.release(failure,failure===404?'dataset_version_not_found':failure===429?'import_busy':'import_unavailable');
    await until(()=>h.model.importReport(legacy.file.file_id)?.status().phase==='ready');
    const first=h.model.importReport(current.file.file_id);assert.equal(first.status().phase,failure===404?'idle':'unavailable');assert.equal(first.status().report,null);
    assert.equal(await first.restore(),false);await h.model.reload();await tick();assert.equal(h.backend.reportCalls().length,2);assert.equal(h.backend.maxInFlight,1);h.cleanup();
  }
});

test('repaint and Chinese/English remount retain the same queued controllers and cannot enqueue them again',async t=>{
  const h=setup(t);await until(()=>h.backend.reportCalls().length>=1);const one=h.model.importReport(current.file.file_id),two=h.model.importReport(legacy.file.file_id);
  h.remount(true);await h.model.reload();h.remount(false);await tick();
  assert.equal(h.model.importReport(current.file.file_id),one);assert.equal(h.model.importReport(legacy.file.file_id),two);assert.equal(h.backend.reportCalls().length,1);
  h.backend.release();await until(()=>ready(h.model));h.remount(true);await tick();assert.match(h.root.textContent,/Import report saved/);assert.equal(h.backend.reportCalls().length,2);assert.equal(h.backend.maxInFlight,1);
});

test('disposing or losing the account context skips queued GETs and suppresses late report/DOM updates',async t=>{
  for(const mode of['dispose','account']){
    const h=setup(t);await until(()=>h.backend.reportCalls().length>=1);const one=h.model.importReport(current.file.file_id),two=h.model.importReport(legacy.file.file_id);
    if(mode==='dispose')h.cleanup();else h.backend.lose();h.backend.release();await until(()=>one.status().binding===null&&two.status().binding===null);await tick();
    assert.equal(h.backend.reportCalls().length,1);assert.equal(one.status().report,null);assert.equal(two.status().report,null);assert.doesNotMatch(h.root.textContent,/导入报告已保存|Import report saved/);
    assert.equal(h.backend.reportCalls()[0].options.signal.aborted,true);h.cleanup();
  }
});

test('removing a queued file disposes only that report and skips its pending initial GET',async t=>{
  const h=setup(t);await until(()=>h.backend.reportCalls().length>=1);const removed=h.model.importReport(legacy.file.file_id);
  h.backend.setItems([current.file]);await h.model.reload();assert.equal(removed.status().binding,null);assert.equal(h.model.importReport(legacy.file.file_id),null);
  h.backend.release();await until(()=>h.model.importReport(current.file.file_id)?.status().phase==='ready');await tick();assert.equal(h.backend.reportCalls().length,1);assert.equal(removed.status().report,null);assert.equal(h.root.textContent.includes(legacy.file.filename),false);
});

test('queued time consumes no read deadline; parse/check stay disabled until its own15s GET budget starts',async t=>{
  let release,clock=0,serial=0;const timers=new Map(),calls=[];
  const model=createPrivateImportController({file:legacy.file,cryptoImpl:webcrypto,now:()=>clock,
    restoreScheduler:run=>new Promise(resolve=>{release=()=>resolve(run());}),
    transport:{isCurrent:()=>true,get(version,{signal}){calls.push({version,signal});return Response.json(legacy.dto);},post(){assert.fail('queued restore must not POST');}},
    setTimer(fn,ms){timers.set(++serial,{fn,ms});return serial;},clearTimer:id=>timers.delete(id)});t.after(()=>model.dispose());
  const task=model.restore();assert.equal(model.status().phase,'checking');assert.equal(timers.size,0);assert.equal(calls.length,0);clock+=100000;
  assert.equal(await model.start(),false);assert.equal(await model.check(),false);assert.equal(await model.restore(),false);release();
  assert.deepEqual([...timers.values()].map(v=>v.ms),[15000,15000]);assert.equal(await task,true);assert.equal(calls.length,1);assert.equal(timers.size,0);assert.equal(model.status().report_version_id,legacy.dto.datasetVersion.dataset_version_id);
});

test('actual private project switch disposes the old uploader and suppresses its queued second report GET',async t=>{
  const backend=server(),other='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  const item=id=>({project_id:id,name:'Synthetic project',description:'',species:'',visibility:'private',createdAt:'2026-11-16T00:00:00Z',updatedAt:'2026-11-16T00:00:00Z'});
  const transport=createPrivateProjectTransport({enabled:true,fetchImpl(path,options){
    if(path==='/api/v1/me')return Response.json({status:'authenticated',user:{email:'owner@research.invalid',emailVerified:true}},{headers:{'X-Cyomics-Account-Context':'Q'.repeat(22)}});
    if(path==='/api/v1/projects?limit=20')return Response.json({items:[item(projectId),item(other)],nextCursor:null});
    if(path==='/api/v1/projects/'+projectId||path==='/api/v1/projects/'+other)return Response.json({project:item(path.endsWith(other)?other:projectId)});
    return backend.fetchImpl(path,options);
  }}),parent=createPrivateProjectController({transport});t.after(()=>parent.dispose());await parent.start();await parent.select(projectId);
  const files=parent.files(),d=dom(),stop=mountPrivateTxt(d.root,files);t.after(stop);await until(()=>backend.reportCalls().length>=1);assert.equal(backend.reportCalls().length,1);
  const queued=files.importReport(legacy.file.file_id);await parent.select(other);backend.release();await until(()=>queued.status().binding===null);await tick();
  assert.equal(files.status().phase,'disposed');assert.equal(backend.reportCalls().length,1);assert.equal(queued.status().report,null);assert.doesNotMatch(d.root.textContent,/导入报告已保存/);
});
