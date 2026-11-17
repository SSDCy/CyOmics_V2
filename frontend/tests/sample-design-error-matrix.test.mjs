import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {webcrypto} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {sampleDesignRoute,sampleDesignInput,validateSource,validateDesign,validateSampleDesignResponse,validateSampleDesignError,MAX_REQUEST} from '../sample-design-contract.mjs';
import {createSampleDesignBridge} from '../sample-design-bridge.mjs';
import {createSampleDesignClient} from '../sample-design-client.js';
// All matrix inputs, report descriptors, transport implementations and tokens are synthetic.
const design=()=>structuredClone(JSON.parse(readFileSync(new URL('./fixtures/sample-design-canonical.synthetic.json',import.meta.url),'utf8')).design);
const id=n=>'00000000-0000-0000-0000-'+String(n).padStart(12,'0');
const source={dataset_version_id:id(2),sample_columns:[{ordinal:3,name_raw:'Intensity 样本😀',sample_label_raw:'样本😀',quant_family:'Intensity'}]};
const path='/api/v1/datasets/'+id(2)+'/sample-designs?project_id='+id(1)+'&quant_family=intensity';
const route=method=>sampleDesignRoute(path,method);
const payload=()=>({schemaVersion:1,request_id:id(9),expected_head_version_id:null,design:design()});
const encode=v=>new TextEncoder().encode(JSON.stringify(v));
const matches=(status,code)=>e=>{assert.equal(e.status,status);assert.equal(e.code,code);return true;};
const bad=(fn,status,code)=>assert.throws(fn,matches(status,code));
const rejects=(p,status,code)=>assert.rejects(p,matches(status,code));
const jsonResponse=(v,status=200)=>new Response(JSON.stringify(v),{status,headers:{'Content-Type':'application/json'}});
const empty={schemaVersion:1,project_id:id(1),dataset_version_id:id(2),quant_family:'Intensity',record:null};
const typed=new Map([[400,['invalid_sample_design_request']],[401,['unauthenticated']],[403,['auth_origin_rejected']],[404,['not_found','dataset_not_found','sample_design_not_found']],[405,['method_not_allowed']],[409,['sample_design_head_conflict','sample_design_request_conflict']],[413,['sample_design_request_too_large']],[415,['unsupported_media_type']],[422,['invalid_sample_design','sample_design_version_limit']],[503,['sample_design_unavailable','sample_design_persisted_invalid']],[504,['sample_design_timeout','sample_design_result_unknown']]]);
function nodeTransport(reply){return (options,callback)=>{const outgoing=new EventEmitter();outgoing.destroy=()=>{};outgoing.end=()=>queueMicrotask(()=>{const incoming=new EventEmitter();incoming.rawHeaders=reply.headers??['Content-Type','application/json'];incoming.headers={};incoming.statusCode=reply.status??200;incoming.destroy=()=>{};callback(incoming);incoming.emit('data',reply.bytes??encode(reply.value));incoming.complete=true;incoming.emit('end');incoming.emit('close');});return outgoing;};}
const args=method=>({route:route(method),body:method==='POST'?encode(payload()):undefined,source,accessToken:'synthetic.matrix.token',signal:new AbortController().signal,budget:{remaining:()=>1000,deadlineUnixMs:'1794873600000'}});
const client=transport=>createSampleDesignClient({projectId:id(1),datasetVersionId:id(2),source,transport,isCurrent:()=>true,cryptoImpl:webcrypto});
const context={dataset_version_id:id(2),quant_family:'Intensity'};

test('R1 route status/code matrix: path404 then method405 then bad query400',()=>{
  for(const p of ['/api/v1/other',path.replace('sample-designs','sample-design'),path.replace(id(2),'BAD'), 'https://evil.test'+path])bad(()=>sampleDesignRoute(p,'GET'),404,'not_found');
  bad(()=>sampleDesignRoute('/api/v1/other','DELETE'),404,'not_found');
  for(const method of ['PUT','DELETE','get',null])bad(()=>sampleDesignRoute(path,method),405,'method_not_allowed');
  bad(()=>sampleDesignRoute(path.replace('intensity','INVALID'),'DELETE'),405,'method_not_allowed');
  for(const p of [path.split('?')[0],path+'&x=y',path+'&project_id='+id(1),path.replace('intensity','INVALID'),path.replace('project_id','%70roject_id'),path.replace('intensity','%ZZ'),path.replace('intensity','inten+sity'),path+'&request_id=bad',path+'#x'])bad(()=>sampleDesignRoute(p,'GET'),400,'invalid_sample_design_request');
  bad(()=>sampleDesignRoute(path+'&request_id='+id(9),'POST'),400,'invalid_sample_design_request');
});
test('R1 request syntax/wrapper matrix normalizes JSON failures to400',()=>{
  const cases=[new Uint8Array([255]),new TextEncoder().encode('{"schemaVersion":1,"schemaVersion":1}'),new TextEncoder().encode('{'),encode({...payload(),extra:true}),encode({...payload(),request_id:'bad'}),encode({...payload(),expected_head_version_id:'bad'}),encode({...payload(),expected_head_version_id:false}),encode({...payload(),schemaVersion:true}),encode({schemaVersion:1,request_id:id(9),expected_head_version_id:null})];
  for(const bytes of cases)bad(()=>sampleDesignInput(bytes,route('POST'),source),400,'invalid_sample_design_request');
  bad(()=>sampleDesignInput('not bytes',route('POST'),source),400,'invalid_sample_design_request');
  bad(()=>sampleDesignInput(new Uint8Array(MAX_REQUEST+1),route('POST'),source),413,'sample_design_request_too_large');
});
test('R1 valid wrapper with design binding/row/confirmation semantics is422',()=>{
  for(const mutate of [p=>p.design=null,p=>p.design.schemaVersion=2,p=>p.design.dataset_version_id=id(7),p=>p.design.quant_family='LFQ intensity',p=>p.design.rows=[],p=>p.design.rows[0].column_ordinal=4,p=>p.design.rows[0].name_raw='changed',p=>p.design.rows[0].sample_label_raw='changed',p=>p.design.rows[0].group='x'.repeat(81),p=>p.design.rows[0].control='INVALID',p=>p.design.rows[0].confirmed=true]){const p=payload();mutate(p);bad(()=>sampleDesignInput(encode(p),route('POST'),source),422,'invalid_sample_design');}
  const d=design();d.rows[0].group='\ud800';bad(()=>validateDesign(d,source,route('POST')),422,'invalid_sample_design');
  bad(()=>validateDesign(design(),{...source,dataset_version_id:id(7)},route('POST')),422,'invalid_sample_design');
});
test('R1 internal source corruption is503 and successful response corruption stays503',async()=>{
  for(const s of [null,{...source,sample_columns:[{ordinal:3}]},{...source,sample_columns:[{...source.sample_columns[0],ordinal:true}]}]){
    bad(()=>validateSource(s),503,'sample_design_persisted_invalid');bad(()=>validateDesign(design(),s,route('POST')),503,'sample_design_persisted_invalid');
  }
  await rejects(validateSampleDesignResponse({...empty,project_id:id(8)},route('GET'),source),503,'sample_design_persisted_invalid');
  await rejects(validateSampleDesignResponse({...empty,record:{}},route('GET'),source),503,'sample_design_persisted_invalid');
});
test('R1 strict typed error allowlist includes400/403/405/415 and existing errors',()=>{
  for(const [status,codes] of typed)for(const code of codes)assert.equal(validateSampleDesignError(status,{error:{code}}).error.code,code);
  for(const [status,body] of [[400,{error:{code:'invalid_sample_design'}}],[403,{error:{code:'auth_origin_rejected',extra:true}}],[405,{error:{code:'unsupported_media_type'}}],[415,{error:{code:'UNKNOWN'}}]])bad(()=>validateSampleDesignError(status,body),503,'sample_design_persisted_invalid');
});
test('R1 Node inbound request matrix returns exact errors before transport',async()=>{
  let count=0;const bridge=createSampleDesignBridge({requestImpl:()=>{count++;throw Error();}});
  for(const [mutate,status,code] of [
    [a=>a.route={path:'/api/v1/other',method:'POST'},404,'not_found'],[a=>a.route={path,method:'DELETE'},405,'method_not_allowed'],[a=>a.route={path:path+'&x=y',method:'POST'},400,'invalid_sample_design_request'],
    [a=>a.body=new TextEncoder().encode('{"schemaVersion":1,"schemaVersion":1}'),400,'invalid_sample_design_request'],[a=>a.body=encode({...payload(),request_id:'bad'}),400,'invalid_sample_design_request'],
    [a=>{const p=payload();p.design.rows[0].column_ordinal=4;a.body=encode(p);},422,'invalid_sample_design'],[a=>a.source={...source,dataset_version_id:id(8)},422,'invalid_sample_design'],
    [a=>a.source=null,503,'sample_design_persisted_invalid'],[a=>a.body=new Uint8Array(MAX_REQUEST+1),413,'sample_design_request_too_large']
  ]){const a=args('POST');mutate(a);await rejects(bridge(a),status,code);}
  const get=args('GET');get.body=encode({});await rejects(bridge(get),400,'invalid_sample_design_request');assert.equal(count,0);
});
test('R1 Node malformed upstream is GET503 persisted_invalid and sent POST504 unknown',async()=>{
  for(const reply of [
    {bytes:new TextEncoder().encode('{')},{bytes:new TextEncoder().encode('{"schemaVersion":1,"schemaVersion":1}')},{bytes:new Uint8Array([255])},{value:{...empty,project_id:id(9)}},
    {value:empty,headers:['Content-Type','text/html']},{value:{error:{code:'untrusted'}},status:400}
  ])for(const method of ['GET','POST']){const bridge=createSampleDesignBridge({requestImpl:nodeTransport(reply)});await rejects(bridge(args(method)),method==='GET'?503:504,method==='GET'?'sample_design_persisted_invalid':'sample_design_result_unknown');}
});
test('R1 Node known typed400/403/405/415 passes through without unknown',async()=>{
  for(const status of [400,403,405,415]){const code=typed.get(status)[0];const bridge=createSampleDesignBridge({requestImpl:nodeTransport({status,value:{error:{code}}})});const r=await bridge(args('POST'));assert.equal(r.status,status);assert.equal(r.value.error.code,code);}
});
test('R1 client typed rejection matrix remains failed/conflict, dirty, and has no uncertainty or hidden I/O',async()=>{
  for(const [status,codes] of typed){if(status>=503)continue;for(const code of codes){
    let calls=0;const c=client(({route})=>{calls++;return route.method==='GET'?jsonResponse(empty):jsonResponse({error:{code}},status);});
    await c.loadDraft(context);await rejects(c.saveDraft(design()),status,code);const s=c.status('Intensity');assert.equal(s.phase,status===409?'conflict':'failed');assert.equal(s.dirty,true);assert.equal(s.uncertain_request_id,null);assert.equal(s.persistence_verified,false);assert.equal(calls,2);
  }}
});
test('R1 browser bad response JSON or shape is GET503; after POST it is504 unknown',async()=>{
  for(const badResponse of [()=>new Response('{',{headers:{'Content-Type':'application/json'}}),()=>jsonResponse({...empty,dataset_version_id:id(8)}),()=>jsonResponse({error:{code:'bad'}},400)]){
    const read=client(()=>badResponse());await rejects(read.loadDraft(context),503,'sample_design_persisted_invalid');assert.equal(read.status('Intensity').head_known,false);
    let calls=0;const write=client(({route})=>{calls++;return route.method==='GET'?jsonResponse(empty):badResponse();});await write.loadDraft(context);await rejects(write.saveDraft(design()),504,'sample_design_result_unknown');assert.equal(write.status('Intensity').phase,'unknown');assert.equal(write.status('Intensity').dirty,true);assert.equal(calls,2);
  }
});
test('R1 browser pre-send invalid design is422 and internal source is503',async()=>{
  let calls=0;const c=client(()=>{calls++;return jsonResponse(empty);});await c.loadDraft(context);const d=design();d.rows[0].group='x'.repeat(81);await rejects(c.saveDraft(d),422,'invalid_sample_design');assert.equal(calls,1);assert.equal(c.status('Intensity').dirty,true);
  bad(()=>createSampleDesignClient({projectId:id(1),datasetVersionId:id(2),source:null}),503,'sample_design_persisted_invalid');
});
