import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {EXECUTION_IDENTITY,validateQCJob} from '../raw-qc-contract.mjs';
import {job,sourceFor,rid,webcrypto,digest} from './support/raw-qc-setup.mjs';
test('v3 identity equals full14 public metadata independently recomputed',async()=>{
 const doc=JSON.parse(await readFile(new URL('../../docs/RAW_QC_EXECUTION_V3.json',import.meta.url),'utf8'));
 const envelope={limits_profile:doc.limits_profile,limits:doc.limits};
 const canonical=v=>v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
 assert.equal(createHash('sha256').update(canonical(envelope)).digest('hex'),'fe1e05ac949ed027969d4ae4475e0e66bbc44cdc1d9310486d33aa334f40aad0');
 assert.equal(Object.keys(doc.limits).length,14);assert.equal(EXECUTION_IDENTITY.limits_profile,doc.limits_profile);assert.equal(EXECUTION_IDENTITY.limits_sha256,doc.limits_sha256);
});
test('old v1/v2 arbitrary identity and extra policy reject even with recomputed binding',async()=>{
 for(const patch of [{limits_profile:'raw_qc_limits_v1'},{limits_profile:'raw_qc_limits_v2',limits_sha256:'a77bf3593e366e7fb999ebc6e6fcbd7b0dde0d4d45d5342f408e5c1de1210ce0'},{limits_profile:'arbitrary'},{limits_sha256:'0'.repeat(64)},{process_policy:{}}]){
  const j=job();Object.assign(j.execution,patch);j.binding_sha256=digest({contract:'raw_qc_job_v0.1',request_sha256:j.request_sha256,source:j.source,execution:j.execution,quant_family:j.quant_family,params:{}});
  await assert.rejects(validateQCJob(j,{source:sourceFor(),family:'intensity',requestId:rid,cryptoImpl:webcrypto}));
 }
});

