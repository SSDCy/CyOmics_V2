// One small SVG-format component case; no actual image decoding/Owner/R/E2E.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash,webcrypto} from 'node:crypto';
import {dom} from './support/preprocess-parameters-dom.mjs';
import {until} from './support/preprocess-owner-setup.mjs';
import {mountRVolcanoView} from '../r-volcano-view.js';

test('five ordered artifacts allow SVG-only image download through authorized callback; PNG remains preview and PDF is rejected',async t=>{
 const uid=n=>String(n).padStart(8,'0')+'-1111-4111-8111-111111111111',body=new Uint8Array(33);body.set([137,80,78,71,13,10,26,10,0,0,0,13,73,72,68,82]);new DataView(body.buffer).setUint32(16,2400);new DataView(body.buffer).setUint32(20,1800);
 const verifiedContext={project_id:uid(1),actor_id:uid(2),source_result_id:uid(3),source_metadata_sha256:'a'.repeat(64),source_manifest_sha256:'b'.repeat(64),derived_version_id:uid(4),derived_metadata_sha256:'c'.repeat(64),derived_manifest_sha256:'d'.repeat(64),source_binding_sha256:'e'.repeat(64),parameters_sha256:'f'.repeat(64)}, {actor_id,...fields}=verifiedContext;
 const media={'R-session.txt':'text/plain;charset=utf-8','volcano-audit.tsv':'text/tab-separated-values;charset=utf-8','volcano-points.tsv':'text/tab-separated-values;charset=utf-8','volcano.png':'image/png','volcano.svg':'image/svg+xml'},descriptor={schemaVersion:1,contract:'r_volcano_view_v0.1',...fields,created_by:actor_id,artifacts:Object.entries(media).map(([name,media_type])=>({name,bytes:name==='volcano.png'?33:1,sha256:name==='volcano.png'?createHash('sha256').update(body).digest('hex'):'1'.repeat(64),media_type}))};
 const d=dom(),signal=new AbortController(),calls=[],revoked=[];let reads=0;
 const options={verifiedContext,descriptor,contextSignal:signal.signal,isCurrent:()=>true,cryptoImpl:webcrypto,urlImpl:{createObjectURL:()=> 'blob:svg-case/png-only',revokeObjectURL:url=>revoked.push(url)},BlobImpl:Blob,readPNG:async()=>{reads++;return {body};},onDownload:async info=>{calls.push(info);return {status:'handed_off'};}};
 const c=mountRVolcanoView(d.root,options);t.after(()=>c.dispose());await until(()=>d.by('data-rvv-image','png')?.src);d.by('data-rvv-image','png').fire('load');assert.equal(await c.ready,true);
 assert.equal(reads,1);assert.deepEqual(descriptor.artifacts.map(p=>p.name),Object.keys(media));assert.equal(d.by('data-rvv-download','volcano.svg').textContent,'SVG 图');
 assert.deepEqual(d.all().filter(n=>Object.hasOwn(n.attrs,'data-rvv-download')).map(n=>n.attrs['data-rvv-download']),['R-session.txt','volcano-audit.tsv','volcano-points.tsv','volcano.svg']);
 assert.equal(d.by('data-rvv-download','volcano.png'),undefined);assert.equal(d.by('data-rvv-download','volcano.pdf'),undefined);assert.equal(d.all().some(n=>n.tag==='svg'||n.tag==='object'||n.tag==='iframe'||n.tag==='a'),false);assert.doesNotMatch(d.root.textContent,/PDF|JPEG/);
 d.by('data-rvv-download','volcano.svg').click();await until(()=>!d.by('data-rvv-download','volcano.svg').disabled);assert.equal(calls.length,1);assert.equal(calls[0].name,'volcano.svg');assert.deepEqual(calls[0].pin,descriptor.artifacts[4]);assert.equal(calls[0].pin.media_type,'image/svg+xml');assert.deepEqual(calls[0].descriptor,descriptor);assert.deepEqual(calls[0].verifiedContext,verifiedContext);assert.equal(calls[0].signal.aborted,false);assert.equal(Object.hasOwn(calls[0],'body'),false);assert.equal(reads,1);assert.match(d.by('data-rvv-download-state','status').textContent,/已交给浏览器/);
 for(const change of [x=>{[x.artifacts[3],x.artifacts[4]]=[x.artifacts[4],x.artifacts[3]];},x=>{x.artifacts[4].media_type='application/pdf';},x=>{x.artifacts.splice(3,2,{name:'volcano.pdf',bytes:1,sha256:'1'.repeat(64),media_type:'application/pdf'},descriptor.artifacts[3]);}]){const bad=structuredClone(descriptor);change(bad);assert.equal(await c.update({...options,descriptor:bad}),false);assert.equal(d.by('data-rvv-image','png'),undefined);assert.equal(d.by('data-rvv-download','volcano.svg'),undefined);assert.equal(reads,1);}
 assert.deepEqual(revoked,['blob:svg-case/png-only']);assert.equal(calls.length,1);
});
