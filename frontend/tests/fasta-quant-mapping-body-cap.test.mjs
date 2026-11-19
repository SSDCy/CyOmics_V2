import test from 'node:test';
import assert from 'node:assert/strict';
import {harness} from './support/raw-qc-bff-session.mjs';
test('S1-FASTA-QUANT-MAPPING-UI-001-body-cap narrow stream-error regression',async t=>{
 let native=0;const h=harness(t,{fastaMappingEnabled:true,fastaMappingRequest(){native++;throw Error('must not send');}}),s=await h.signed(),path='/api/v1/projects/11111111-1111-4111-8111-111111111111/fasta-quant-mappings';
 const r=await h.send(path,{cookie:s.cookie,view:s.view,method:'POST',wire:Buffer.alloc(513,32),headers:{'content-type':'application/json'}});assert.equal(r.status,413);assert.equal(r.body.error.code,'fasta_mapping_too_large');assert.equal(native,0);
 const g=await h.send(path+'/lookup?dataset_version_id=11111111-1111-4111-8111-111111111111&fasta_source_id=22222222-2222-4222-8222-222222222222&fasta_index_version_id=33333333-3333-4333-8333-333333333333',{cookie:s.cookie,view:s.view,method:'GET',wire:Buffer.from('x')});assert.equal(g.status,400);assert.equal(g.body.error.code,'invalid_fasta_mapping_request');assert.equal(native,0);
 t.diagnostic('Newly discovered shared-body-reader error translation only; no replay of passed full mapping target or old suites.');
});
