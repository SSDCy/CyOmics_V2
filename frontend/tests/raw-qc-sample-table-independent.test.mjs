import test from 'node:test';import assert from 'node:assert/strict';import {readFile} from 'node:fs/promises';
import {parseSampleQCTable,SAMPLE_QC_HEADERS} from '../raw-qc-client.js';
import {setup,binary,webcrypto,until,deferred,createQCArtifactScope} from './support/raw-qc-download-setup.mjs';
const name='sample-qc.tsv',enc=s=>new TextEncoder().encode(s),q=s=>'"'+s.replaceAll('"','""')+'"';
const row=(label='NA',sum='0',median='NA')=>['1',q('Intensity '+label),q(label),'3','1','1','1','1',sum,median].join('\t');
const bytes=(r=[row()])=>enc(SAMPLE_QC_HEADERS.map(q).join('\t')+'\n'+r.join('\n')+'\n');
const rejectable=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
async function ready(t,data=bytes(),options={}){const h=await setup(t,{bodies:new Map([[name,data]]),...options});assert.equal(await h.start(),true);return h;}

test('independent csv oracle compares40 exact tiny-R cells and full order without R or statistics',async()=>{
 const oracle=JSON.parse(await readFile(new URL('./fixtures/sample-qc-independent-oracle.json',import.meta.url),'utf8'));let count=0;
 for(const o of oracle){const raw=new Uint8Array(await readFile(new URL('./fixtures/sample-qc-'+o.family+'.golden.tsv',import.meta.url)));const p=parseSampleQCTable(raw);assert.deepEqual(p.headers,o.headers);assert.deepEqual(p.rows.map(r=>p.headers.map(k=>r[k])),o.rows);count+=o.rows.length*o.headers.length;}
 assert.equal(count,40);
});
test('BOM bareCR unsafe count quoted numeric and overflow refuse; labels remain exact opaque strings',()=>{
 const raw=bytes();for(const bad of [new Uint8Array([239,187,191,...raw]),enc(new TextDecoder().decode(raw).replace('\n','\r')),bytes([row().replace('\t3\t','\t9007199254740992\t')]),bytes([row('x','1e309')]),bytes([row('x','"0"')])])assert.throws(()=>parseSampleQCTable(bad),e=>e.code==='sample_qc_table_invalid');
 const label='__proto__\t</td><img src=x onerror="SYNTHETIC">\r\n=SUM(1,2)\u2028末尾',p=parseSampleQCTable(bytes([row(label,'+2.500e-03','0')]));
 assert.equal(p.rows[0].sample_label_raw,label);assert.equal(p.rows[0].numeric_sum,'+2.500e-03');assert.equal(p.rows[0].positive_median,'0');assert.ok(Object.isFrozen(p.rows)&&Object.isFrozen(p.rows[0]));assert.throws(()=>{p.rows[0].sample_label_raw='changed';});
});
test('preview cannot touch Blob URL anchor or document, even when traps are present',async t=>{
 const used={blob:0,url:0,anchor:0,append:0};const save={BlobImpl:class{constructor(){used.blob++;throw Error('preview created Blob');}},urlImpl:{createObjectURL(){used.url++;throw Error('preview URL');},revokeObjectURL(){}},documentImpl:{createElement(){used.anchor++;throw Error('preview anchor');},body:{append(){used.append++;throw Error('preview append');}}}};
 const h=await ready(t,bytes(),{save});assert.equal(await h.c.previewSampleQC(),true);assert.deepEqual(used,{blob:0,url:0,anchor:0,append:0});assert.equal(h.downloads().length,1);assert.equal(h.scope.busy(),false);
});
test('full bytes without EOF expose no table and do not start SHA; EOF then permits exact table',async t=>{
 const data=bytes(),eof=deferred();let reads=0,hashes=0;
 const cryptoImpl={randomUUID:()=>webcrypto.randomUUID(),subtle:{digest(...a){hashes++;return webcrypto.subtle.digest(...a);}}};
 const h=await ready(t,data,{cryptoImpl,artifactReply:j=>{const resp=binary(j,name,data);return{status:200,redirected:false,headers:resp.headers,body:{getReader(){return{read(){reads++;return reads===1?Promise.resolve({done:false,value:data}):eof.promise;},cancel(){return Promise.resolve();},releaseLock(){}};}}};}});
 hashes=0;const pending=h.c.previewSampleQC();await until(()=>reads===2);assert.equal(hashes,0);assert.equal(h.c.view().sample_qc_preview.table,null);eof.resolve({done:true});assert.equal(await pending,true);assert.equal(hashes,1);assert.equal(h.c.view().sample_qc_preview.table.rows.length,1);
});
test('cancelled digest rejection holds capacity until settlement and never revives table',async t=>{
 const held=rejectable(),scope=createQCArtifactScope();let block=false;
 const cryptoImpl={randomUUID:()=>webcrypto.randomUUID(),subtle:{digest(...a){return block?held.promise:webcrypto.subtle.digest(...a);}}};
 const h=await ready(t,bytes(),{cryptoImpl,scope}),other=await ready(t,bytes(),{scope});block=true;
 const p=h.c.previewSampleQC();await until(()=>h.c.view().sample_qc_preview.phase==='verifying');h.c.cancelSampleQCPreview();assert.equal(await p,false);assert.equal(scope.busy(),true);assert.equal(await other.c.previewSampleQC(),false);held.reject(Error('SYNTHETIC_HASH_FAILURE'));await until(()=>!scope.busy());assert.equal(h.c.view().sample_qc_preview.table,null);assert.equal(await other.c.previewSampleQC(),true);assert.equal(h.save.clicks.length,0);
});
test('disposed source cannot release unresolved digest lease; a new client must wait',async t=>{
 const held=deferred(),scope=createQCArtifactScope();let block=false;
 const cryptoImpl={randomUUID:()=>webcrypto.randomUUID(),subtle:{digest(...a){return block?held.promise.then(()=>webcrypto.subtle.digest(...a)):webcrypto.subtle.digest(...a);}}};
 const first=await ready(t,bytes(),{cryptoImpl,scope});block=true;const p=first.c.previewSampleQC();await until(()=>first.c.view().sample_qc_preview.phase==='verifying');first.c.dispose();assert.equal(await p,false);
 const second=await ready(t,bytes(),{scope});assert.equal(scope.busy(),true);assert.equal(await second.c.previewSampleQC(),false);held.resolve();await until(()=>!scope.busy());assert.equal(first.c.view().sample_qc_preview.table,null);assert.equal(await second.c.previewSampleQC(),true);
});
test('synchronous digest throw clears table and lease without Blob handoff',async t=>{
 let fail=false;const cryptoImpl={randomUUID:()=>webcrypto.randomUUID(),subtle:{digest(...a){if(fail)throw Error('SYNTHETIC_SYNC_HASH');return webcrypto.subtle.digest(...a);}}};
 const h=await ready(t,bytes(),{cryptoImpl});fail=true;assert.equal(await h.c.previewSampleQC(),false);assert.equal(h.scope.busy(),false);assert.equal(h.c.view().sample_qc_preview.table,null);assert.equal(h.save.active.size,0);assert.equal(h.save.clicks.length,0);fail=false;assert.equal(await h.c.previewSampleQC(),true);
});
test('cancel during ready notification erases table and denies a stale success return',async t=>{
 const h=await ready(t);const unsub=h.c.subscribe(()=>{if(h.c.view().sample_qc_preview.phase==='ready')h.c.cancelSampleQCPreview();});assert.equal(await h.c.previewSampleQC(),false);unsub();assert.equal(h.c.view().sample_qc_preview.table,null);assert.equal(h.scope.busy(),false);assert.equal(h.save.clicks.length,0);assert.equal(await h.c.previewSampleQC(),true);
});
test('preview overlimit does not occupy lease or lock other ordinary artifact downloads',async t=>{
 const h=await ready(t,new Uint8Array(1048577));assert.equal(await h.c.previewSampleQC(),false);assert.equal(h.scope.busy(),false);assert.equal(h.downloads().length,0);assert.equal(h.c.canDownloadArtifact(name),true);assert.equal(h.c.canDownloadArtifact('summary.tsv'),true);assert.equal(await h.c.downloadArtifact('summary.tsv'),true);assert.equal(h.downloads().length,1);
 // Saver is an in-RAM fake, not a browser or filesystem download.
 assert.equal(h.save.clicks.length,1);assert.equal(h.save.clicks[0].name.endsWith('_summary.tsv'),true);
});
test('401 during explicit preview discards old and new table with no auto-request or saved blob',async t=>{
 const h=await ready(t,bytes(),{artifactReply:()=>new Response('',{status:401})});assert.equal(await h.c.previewSampleQC(),false);assert.equal(h.c.view().sample_qc_preview.table,null);assert.equal(h.c.view().phase,'unavailable');assert.equal(h.downloads().length,1);assert.equal(await h.c.previewSampleQC(),false);assert.equal(h.downloads().length,1);assert.equal(h.save.clicks.length,0);
});
