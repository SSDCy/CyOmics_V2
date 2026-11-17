import test from 'node:test';
import assert from 'node:assert/strict';
import {validateQCJob} from '../raw-qc-contract.mjs';
import {job,sourceFor,rid,webcrypto} from './support/raw-qc-setup.mjs';
const options=previous=>({source:sourceFor(),family:'intensity',requestId:rid,previous,cryptoImpl:webcrypto});
test('observed start cannot be erased by failed update; legitimate queued to terminal skipped read remains valid',async()=>{
 const previous=job({status:'running'}),next=job({status:'failed'});next.started_at=null;
 await assert.rejects(validateQCJob(next,options(previous)));
 await validateQCJob(job({status:'failed'}),options(previous));
 await validateQCJob(job({status:'succeeded'}),options(job()));
});
test('observed start cannot change in later running revision; queued to running and same start to terminal remain valid',async()=>{
 const previous=job({status:'running'}),next=job({status:'running',revision:3});next.started_at='2026-11-18T01:00:02.000000Z';
 await assert.rejects(validateQCJob(next,options(previous)));
 await validateQCJob(previous,options(job()));
 await validateQCJob(job({status:'succeeded'}),options(previous));
});
