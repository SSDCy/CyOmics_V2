// One finite component-only combination; header bytes and load/error events are
// synthetic. No scientific/R image, browser decoder, authorization or E2E proof.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash,webcrypto} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {dom,deferred,flush} from './support/preprocess-parameters-dom.mjs';
import {until} from './support/preprocess-owner-setup.mjs';
import {mountRVolcanoView} from '../r-volcano-view.js';
const uid=n=>String(n).padStart(8,'0')+'-1111-4111-8111-111111111111';
const digest=b=>createHash('sha256').update(b).digest('hex');
function header(){const bytes=new Uint8Array(33);bytes.set([137,80,78,71,13,10,26,10,0,0,0,13,73,72,68,82]);const v=new DataView(bytes.buffer);v.setUint32(16,2400);v.setUint32(20,1800);bytes[24]=8;bytes[25]=6;return bytes;}
function fixture(){const body=header(),verifiedContext={project_id:uid(1),actor_id:uid(2),source_result_id:uid(3),source_metadata_sha256:'a'.repeat(64),source_manifest_sha256:'b'.repeat(64),derived_version_id:uid(4),derived_metadata_sha256:'c'.repeat(64),derived_manifest_sha256:'d'.repeat(64),source_binding_sha256:'e'.repeat(64),parameters_sha256:'f'.repeat(64)};
 const {actor_id,...fields}=verifiedContext,media={'R-session.txt':'text/plain;charset=utf-8','volcano-audit.tsv':'text/tab-separated-values;charset=utf-8','volcano-points.tsv':'text/tab-separated-values;charset=utf-8','volcano.pdf':'application/pdf','volcano.png':'image/png'};
 return {body,verifiedContext,descriptor:{schemaVersion:1,contract:'r_volcano_view_v0.1',...fields,created_by:actor_id,artifacts:Object.entries(media).map(([name,media_type])=>({name,bytes:name==='volcano.png'?body.length:1,sha256:name==='volcano.png'?digest(body):'1'.repeat(64),media_type}))}};
}
function urls(){const active=new Map(),created=[],revoked=[];return{active,created,revoked,createObjectURL(blob){const u='blob:volcano-test/'+(created.length+1);created.push(u);active.set(u,blob);return u;},revokeObjectURL(u){revoked.push(u);active.delete(u);}};}
async function show(c,d,operation=c.ready){await until(()=>d.by('data-rvv-image','png')?.src?.startsWith('blob:'));d.by('data-rvv-image','png').fire('load');assert.equal(await operation,true);assert.equal(c.view().phase,'ready');}

test('R image viewer validates exact bound pins/PNG bytes, delegates four downloads honestly and revokes URLs across failures and context/abort/late changes',async t=>{
 const d=dom(),u=urls(),f=fixture(),signal=new AbortController(),reads=[],downloads=[];let downloadResult={status:'handed_off'},live=true;
 const options={verifiedContext:f.verifiedContext,descriptor:f.descriptor,contextSignal:signal.signal,isCurrent:()=>live,cryptoImpl:webcrypto,urlImpl:u,BlobImpl:Blob,readPNG:async info=>{reads.push(info);return {body:f.body};},onDownload:async info=>{downloads.push(info);return downloadResult;}};
 const original=structuredClone(f),c=mountRVolcanoView(d.root,options);t.after(()=>c.dispose());await show(c,d);
 assert.equal(reads.length,1);assert.deepEqual(reads[0].verifiedContext,f.verifiedContext);assert.deepEqual(reads[0].descriptor,f.descriptor);assert.equal(reads[0].signal.aborted,false);assert.equal(u.active.size,1);const firstURL=d.by('data-rvv-image','png').src,blob=u.active.get(firstURL);assert.equal(blob.type,'image/png');assert.deepEqual(new Uint8Array(await blob.arrayBuffer()),f.body);
 assert.equal(d.by('data-rvv-image','png').width,2400);assert.equal(d.by('data-rvv-image','png').height,1800);assert.match(d.root.textContent,/R 原始图像/);assert.equal(d.all().some(n=>n.tag==='canvas'||n.tag==='svg'),false);
 assert.deepEqual(d.all().filter(n=>Object.hasOwn(n.attrs,'data-rvv-download')).map(n=>n.attrs['data-rvv-download']),['R-session.txt','volcano-audit.tsv','volcano-points.tsv','volcano.pdf']);
 for(const name of ['volcano.pdf','volcano-points.tsv','volcano-audit.tsv','R-session.txt']){d.by('data-rvv-download',name).click();await until(()=>!d.by('data-rvv-download',name).disabled);assert.match(d.by('data-rvv-download-state','status').textContent,/已交给浏览器/);}
 for(const item of downloads){assert.deepEqual(item.pin,f.descriptor.artifacts.find(p=>p.name===item.name));assert.deepEqual(item.descriptor,f.descriptor);assert.deepEqual(item.verifiedContext,f.verifiedContext);assert.equal(Object.hasOwn(item,'body'),false);assert.equal(item.signal.aborted,false);}
 assert.equal(reads.length,1);assert.equal(u.created.length,1);assert.doesNotMatch(d.root.textContent,/磁盘已保存|已保存到本地|Demo|selected.?15|13.?Up/i);
 downloadResult={status:'handed_off',extra:true};d.by('data-rvv-download','volcano.pdf').click();await until(()=>!d.by('data-rvv-download','volcano.pdf').disabled);assert.match(d.by('data-rvv-download-state','status').textContent,/未确认/);
 assert.equal(await c.update({...options,descriptor:null}),false);assert.equal(c.view().phase,'empty');assert.match(d.root.textContent,/暂无已保存的 R 火山图/);assert.equal(u.active.size,0);assert.ok(u.revoked.includes(firstURL));assert.equal(reads[0].signal.aborted,true);assert.equal(reads.length,1);
 const before=u.created.length;
 for(const mutate of [o=>{o.descriptor.extra=true;},o=>{o.descriptor.contract='other';},o=>{o.descriptor.schemaVersion=2;},o=>{o.descriptor.created_by=uid(9);},...Object.keys(f.verifiedContext).filter(k=>k!=='actor_id').map(k=>o=>{o.descriptor[k]=k.endsWith('_sha256')?'9'.repeat(64):uid(9);}),o=>{o.descriptor.artifacts.reverse();},o=>{o.descriptor.artifacts[1]=o.descriptor.artifacts[0];},o=>{o.descriptor.artifacts[0].media_type='text/plain';},o=>{o.descriptor.artifacts[0].bytes=0;},o=>{o.descriptor.artifacts[0].bytes=27262977;},o=>{o.descriptor.artifacts.forEach(p=>{p.bytes=27262976;});},o=>{o.verifiedContext.extra=true;}]){
  const o={...options,verifiedContext:structuredClone(f.verifiedContext),descriptor:structuredClone(f.descriptor)};mutate(o);let n=0;o.readPNG=async()=>{n++;return {body:f.body};};assert.equal(await c.update(o),false);assert.equal(n,0);assert.equal(u.active.size,0);assert.equal(d.all().filter(n=>n.className==='rvv').length,1);
 }
 for(const mutate of [b=>b.subarray(0,32),b=>{b[0]=0;return b;},b=>{new DataView(b.buffer).setUint32(8,12);return b;},b=>{b[12]=88;return b;},b=>{new DataView(b.buffer).setUint32(16,2399);return b;},b=>{new DataView(b.buffer).setUint32(20,1799);return b;}]){
  const bytes=mutate(new Uint8Array(f.body)),desc=structuredClone(f.descriptor),pin=desc.artifacts.at(-1);pin.bytes=bytes.length;pin.sha256=digest(bytes);assert.equal(await c.update({...options,descriptor:desc,readPNG:async()=>({body:bytes})}),false);assert.equal(u.active.size,0);
 }
 for(const result of [{body:new Uint8Array(34)},{body:new Uint8Array(f.body).fill(0)},{body:f.body,extra:true},{body:'PNG'}])assert.equal(await c.update({...options,readPNG:async()=>result}),false);
 assert.equal(await c.update({...options,readPNG:async()=>{throw Error('Synthetic unavailable');}}),false);assert.equal(u.created.length,before);
 // Browser image decoding failure is distinct from PNG-header/hash validation.
 const decode=c.update(options);await until(()=>d.by('data-rvv-image','png'));const badURL=d.by('data-rvv-image','png').src;d.by('data-rvv-image','png').fire('error');assert.equal(await decode,false);assert.equal(c.view().phase,'unavailable');assert.equal(u.active.size,0);assert.ok(u.revoked.includes(badURL));assert.equal(d.by('data-rvv-image','png'),undefined);
 const racing=c.update(options);await until(()=>d.by('data-rvv-image','png'));const racingImage=d.by('data-rvv-image','png');racingImage.fire('load');racingImage.fire('error');assert.equal(await racing,false);assert.equal(c.view().phase,'unavailable');assert.equal(u.active.size,0);
 // Callback completion from a previous context cannot create/revive a URL.
 const held=deferred(),old=c.update({...options,readPNG:()=>held.promise}),oldCount=u.created.length;const fresh=fixture();fresh.verifiedContext.derived_version_id=uid(20);fresh.descriptor.derived_version_id=uid(20);
 const next=c.update({...options,...fresh});await show(c,d,next);const nextURL=d.by('data-rvv-image','png').src;held.resolve({body:f.body});assert.equal(await old,false);assert.equal(u.created.length,oldCount+1);assert.equal(d.by('data-rvv-image','png').src,nextURL);
 // A physical digest that finishes after update must also be discarded.
 const slow=deferred();let hashing=false;const slowReady=c.update({...options,cryptoImpl:{subtle:{digest(){hashing=true;return slow.promise;}}}});await until(()=>hashing);await c.update({...options,descriptor:null});const created=u.created.length;slow.resolve(await webcrypto.subtle.digest('SHA-256',f.body));assert.equal(await slowReady,false);assert.equal(u.created.length,created);assert.equal(u.active.size,0);
 const again=c.update(options);await show(c,d,again);const errorImage=d.by('data-rvv-image','png'),button=d.by('data-rvv-download','volcano.pdf'),count=downloads.length;errorImage.fire('error');button.click();await flush();assert.equal(downloads.length,count);assert.equal(u.active.size,0);
 const abortReady=c.update(options);await show(c,d,abortReady);const lateDownload=deferred();downloadResult=lateDownload.promise;d.by('data-rvv-download','volcano.pdf').click();signal.abort();lateDownload.resolve({status:'handed_off'});await flush();assert.equal(c.view().phase,'disposed');assert.equal(u.active.size,0);assert.equal(d.root.textContent,'');assert.equal(d.listenerCount(),0);assert.equal(await c.update(options),false);
 // isCurrent and in-place binding changes are checked at the public boundary.
 for(const change of ['current','context','descriptor']){const q=fixture(),m=dom(),url=urls(),controller=new AbortController();let ok=true;const opt={...options,...q,contextSignal:controller.signal,isCurrent:()=>ok,urlImpl:url},v=mountRVolcanoView(m.root,opt);await show(v,m);if(change==='current')ok=false;if(change==='context')q.verifiedContext.actor_id=uid(99);if(change==='descriptor')q.descriptor.parameters_sha256='0'.repeat(64);assert.equal(v.view().phase,'disposed');assert.equal(url.active.size,0);assert.equal(m.root.textContent,'');}
 assert.deepEqual(f,original);const css=await readFile(new URL('../r-volcano-view.css',import.meta.url),'utf8');assert.match(css,/\.rvv img\{[^}]*height:auto/);assert.match(css,/@media\(max-width:520px\)/);
 t.diagnostic('Component-only synthetic PNG header/load events. Real SHA256/Blob bytes and injected URL bookkeeping, no actual PNG decoding or R/scientific/browser/Owner/auth/API/E2E evidence; no old suite.');
});
