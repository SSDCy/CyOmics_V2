import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash, webcrypto} from 'node:crypto';
import {createPrivateImportController,createImportTransport} from '../../private-import-controller.js';
import {createRawTransport,createPrivateTxtController,mountPrivateTxt} from '../../private-txt-upload.js';
import {createPrivateSampleDesignHost,mountPrivateSampleDesignHost} from '../../private-sample-design-host.js';
import {createSampleDesignClient} from '../../sample-design-client.js';

// All records, identities, headers and stored versions below are synthetic.
const fixtures=JSON.parse(await readFile(new URL('../fixtures/contrast-mount-report-restore.synthetic.json',import.meta.url),'utf8'));
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


export {fixtures,first,second,projectId,clone,tick,deferred,until,dom,field,action,server,harness,key,defaultDraft,record};
