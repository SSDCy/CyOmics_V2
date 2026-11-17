import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';
import {createPrivateProjectTransport,createPrivateProjectRoutes} from '../private-projects.js';

// Reuse the reviewed synthetic source; no real login, server or stored data.
const f=JSON.parse(await readFile(new URL('./fixtures/sample-design-source.synthetic.json',import.meta.url),'utf8'));
const projectId=f.file.project_id,version=f.dto.datasetVersion.dataset_version_id,other='22222222-2222-4222-8222-222222222222';
const source={dataset_version_id:version,sample_columns:f.dto.importReport.sample_columns};
const context='Q'.repeat(22),signal=()=>new AbortController().signal,tick=()=>new Promise(resolve=>setImmediate(resolve));
async function until(fn){const end=Date.now()+1500;while(!fn()&&Date.now()<end)await tick();assert.ok(fn(),'Synthetic flow did not settle');}
const configuration={projectId,datasetVersionId:version,source,isCurrent:()=>true,cryptoImpl:webcrypto};
function backend(){
  const calls=[];
  const fetchImpl=(path,options)=>{
    calls.push({path,options});assert.equal(options.method,'GET','This flag task must never auto-write');
    if(path==='/api/v1/me')return Response.json({status:'authenticated',user:{email:'synthetic@research.invalid',emailVerified:true}},{headers:{'X-Cyomics-Account-Context':context}});
    assert.equal(new Headers(options.headers).get('X-Cyomics-Account-Context'),context);
    const item={project_id:projectId,name:'Synthetic private project',description:'',species:'',visibility:'private',createdAt:'2026-11-16T00:00:00Z',updatedAt:'2026-11-16T00:00:00Z'};
    if(path==='/api/v1/projects?limit=20')return Response.json({items:[item],nextCursor:null});
    if(path==='/api/v1/projects/'+projectId)return Response.json({project:item});
    if(path==='/api/v1/projects/'+projectId+'/files')return Response.json({items:[f.file],nextCursor:null});
    if(path==='/api/v1/uploads/'+f.file.file_id+'/complete?project_id='+projectId)return Response.json(f.dto);
    assert.equal(path,'/api/v1/datasets/'+version+'/sample-designs?project_id='+projectId+'&quant_family=intensity');
    return Response.json({schemaVersion:1,project_id:projectId,dataset_version_id:version,quant_family:'Intensity',record:null});
  };
  return{calls,fetchImpl};
}

test('private-project constructor remains closed overall, and omitted/false/nonboolean sample flag stays off',async()=>{
  let calls=0;assert.equal(createPrivateProjectTransport({sampleDesignsEnabled:true,fetchImpl:()=>calls++}),null);assert.equal(calls,0);
  for(const flag of [undefined,false,1,'true']){
    const b=backend(),options={enabled:true,fetchImpl:b.fetchImpl};if(flag!==undefined)options.sampleDesignsEnabled=flag;
    const transport=createPrivateProjectTransport(options);await transport.me({signal:signal()});
    const raw=transport.rawFiles(projectId);assert.equal(raw.sampleDesigns(configuration),null);assert.equal(b.calls.length,1);transport.forgetContext();
  }
});

test('explicit true reaches same-project rawFiles client while retaining account context and fixed routes',async()=>{
  const b=backend(),transport=createPrivateProjectTransport({enabled:true,sampleDesignsEnabled:true,fetchImpl:b.fetchImpl});
  const before=transport.rawFiles(projectId);assert.equal(before.isCurrent(),false);
  await transport.me({signal:signal()});const raw=transport.rawFiles(projectId),client=raw.sampleDesigns(configuration);assert.ok(client);
  assert.equal(await client.loadDraft({dataset_version_id:version,quant_family:'Intensity'},{signal:signal()}),null);
  assert.equal(client.status('Intensity').head_known,true);assert.equal(b.calls.length,2);
  assert.throws(()=>raw.sampleDesigns({...configuration,projectId:other}),/project_context_changed/);
  assert.equal(b.calls.length,2);transport.forgetContext();assert.equal(raw.isCurrent(),false);
  await assert.rejects(client.loadDraft({dataset_version_id:version,quant_family:'Intensity'},{signal:signal()}));assert.equal(b.calls.length,2);
  assert.equal(b.calls[1].options.credentials,'same-origin');assert.equal(new Headers(b.calls[1].options.headers).get('Authorization'),null);client.dispose();
});

test('actual app startup declaration reaches the real private route, uploader, verified report and design GET with UI002+R2',async t=>{
  const app=await readFile(new URL('../app.js',import.meta.url),'utf8');
  const declarations=app.split(/\r?\n/).filter(line=>line.startsWith('const privateProjectRoutes='));assert.equal(declarations.length,1);
  const b=backend();let options,transport,routes;
  vm.runInNewContext(declarations[0],{
    createPrivateProjectTransport(value){options=value;transport=createPrivateProjectTransport({...value,fetchImpl:b.fetchImpl});return transport;},
    createPrivateProjectRoutes(value){routes=createPrivateProjectRoutes(value);return routes;},
  },{timeout:1000});
  assert.equal(options.enabled,true);assert.equal(options.sampleDesignsEnabled,true);t.after(()=>routes.dispose());
  const project=routes.enter('/workspace/projects');await project.start();assert.equal(project.status().ready,true);await project.select(projectId);
  const files=project.files();await files.start();const report=files.importReport(f.file.file_id),host=files.sampleDesign(f.file.file_id);
  assert.equal(await report.restore(),true);await until(()=>host.view().phase==='empty');
  assert.equal(host.view().connected,true);assert.equal(host.view().design.rows[0].name_raw,source.sample_columns[0].name_raw);
  assert.equal(b.calls.filter(c=>c.path.includes('/sample-designs?')).length,1);assert.ok(b.calls.every(c=>c.options.method==='GET'));
  routes.enter('/demo/projects');assert.equal(files.status().phase,'disposed');assert.equal(host.view().design,null);assert.equal(transport.hasContext(),false);
});
