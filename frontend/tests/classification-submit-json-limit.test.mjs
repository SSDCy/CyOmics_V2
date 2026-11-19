// One CSUI01 boundary regression. Synthetic Auth/native wire only; no real
// Owner HTTP/Auth/DB/R or original author/read/scientific suites.
import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {createHash} from 'node:crypto';
import {classificationJobInput,classificationParametersBytes,CLASSIFICATION_CORE} from '../de-classification-read-contract.mjs';
import {canonicalJSON} from '../sample-design-contract.mjs';
import {readPreprocessBody} from '../preprocess-bridge.mjs';
import {harness} from './support/raw-qc-bff-session.mjs';
import {wire} from './support/differential-result-composition.mjs';
const enc=new TextEncoder(),uid=n=>String(n).padStart(8,'0')+'-1111-4111-8111-111111111111';
async function readBody(bytes,{method='POST',flags=[false,false,false,false,false]}={}){
 const req=new EventEmitter();req.pause=()=>{};const pending=readPreprocessBody(req,new AbortController().signal,method,...flags);
 queueMicrotask(()=>{req.emit('data',bytes);req.emit('end');});return pending;
}

test('CSUI01: classification JSON8192 accepts original TSV4096 across parser/stream/Auth;8193 rejects and legacy limits stay unchanged',async t=>{
 const prefix='key\tvalue\nschema_version\tde_classification_v0.1\ninput_scale\tlog2\nadjusted_p_max\t0.05\nabsolute_log2fc_min\t';
 const zero='0'.repeat(4096-enc.encode(prefix+'\n').length),parameters={schema_version:'de_classification_v0.1',input_scale:'log2',adjusted_p_max:'0.05',absolute_log2fc_min:zero};
 const expectedTSV=enc.encode(prefix+zero+'\n');assert.equal(expectedTSV.byteLength,4096);assert.deepEqual(classificationParametersBytes(parameters),expectedTSV);
 const input={request_id:uid(21),source_result_id:uid(8),parameters},json=Buffer.from(canonicalJSON(input));assert.ok(json.byteLength>4096&&json.byteLength<=8192);
 const atLimit=Buffer.concat([json,Buffer.alloc(8192-json.byteLength,32)]),overLimit=Buffer.concat([atLimit,Buffer.from(' ')]);
 assert.equal(atLimit.byteLength,8192);assert.equal(overLimit.byteLength,8193);
 assert.deepEqual(classificationJobInput(json),input);assert.deepEqual(classificationJobInput(atLimit),input);
 assert.throws(()=>classificationJobInput(overLimit),error=>error.status===400&&error.code==='invalid_de_classification_request');
 assert.throws(()=>classificationParametersBytes({...parameters,absolute_log2fc_min:zero+'0'}),error=>error.code==='invalid_de_classification_request');
 const jobsFlags=[false,false,false,false,true];assert.deepEqual(await readBody(json,{flags:jobsFlags}),json);assert.deepEqual(await readBody(atLimit,{flags:jobsFlags}),atLimit);
 await assert.rejects(readBody(overLimit,{flags:jobsFlags}),error=>error.status===413);

 const native=wire(),project='11111111-1111-4111-8111-111111111111',path='/api/v1/projects/'+project+'/classification-jobs';
 native.reply(call=>{
  assert.equal(call.options.path,path);assert.equal(call.options.method,'POST');const received=JSON.parse(call.body);assert.deepEqual(received,input);assert.equal(received.parameters.absolute_log2fc_min.length,zero.length);
  call.deliver({schemaVersion:1,contract:'de_classification_queued_v0.1',classification_job_id:uid(31),project_id:project,request_id:input.request_id,created_by:project,source_result_id:input.source_result_id,source_manifest_sha256:'a'.repeat(64),source_binding_sha256:'b'.repeat(64),parameters:received.parameters,parameters_sha256:createHash('sha256').update(expectedTSV).digest('hex'),core:CLASSIFICATION_CORE,created_at:'2026-11-19T00:00:00.000000Z',status:'queued',observation:'confirmed'},native.calls.length===1?201:200);
 });
 const h=harness(t,{useServer:true,classificationJobsEnabled:true,classificationJobsRequest:native.request,preprocessingEnabled:true,preprocessingRequest:native.request}),identity=await h.signed();
 for(const payload of [json,atLimit]){
  const result=await h.send(path,{method:'POST',cookie:identity.cookie,view:identity.view,wire:payload,headers:{'content-length':String(payload.byteLength)}});
  assert.ok([200,201].includes(result.status));assert.equal(result.body.parameters.absolute_log2fc_min,zero);assert.equal(result.body.parameters_sha256,createHash('sha256').update(expectedTSV).digest('hex'));
 }
 const sent=native.calls.length,rejected=await h.send(path,{method:'POST',cookie:identity.cookie,view:identity.view,wire:overLimit,headers:{'content-length':'8193'}});
 assert.notEqual(rejected.status,200);assert.notEqual(rejected.status,201);assert.equal(native.calls.length,sent,'Auth Content-Length rejects before native request');
 // Existing generic preprocessing POST4096 and old differential POST8192 keep
 // their own reader caps; existing read-only GET still accepts no body.
 assert.equal((await readBody(Buffer.alloc(4096,32))).length,4096);
 await assert.rejects(readBody(Buffer.alloc(4097,32)),error=>error.status===413);
 assert.equal((await readBody(atLimit,{flags:[false,true,false,false,false]})).length,8192);
 await assert.rejects(readBody(overLimit,{flags:[false,true,false,false,false]}),error=>error.status===413);
 await assert.rejects(readBody(Buffer.from('x'),{method:'GET',flags:[false,false,false,true,false]}),error=>error.status===400);
 const oldPath='/api/v1/datasets/'+uid(4)+'/preprocessing-jobs?project_id='+project;
 const old=await h.send(oldPath,{method:'POST',cookie:identity.cookie,view:identity.view,wire:Buffer.alloc(4097,32),headers:{'content-length':'4097'}});assert.equal(old.status,413);assert.equal(native.calls.length,sent);
 t.diagnostic('Legal original numeric-zero token yields TSV4096 and JSON'+json.byteLength+'; parser/stream/Auth accept it and paddedJSON8192 without token normalization.8193 rejects at all3 before native IO; TSV4097 rejects. Legacy preprocess4096/differential8192/readGET0 caps unchanged. Oversize error mapping otherwise preserved.');
});
