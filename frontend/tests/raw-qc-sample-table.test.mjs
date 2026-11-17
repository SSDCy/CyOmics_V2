import test from 'node:test';import assert from 'node:assert/strict';import {readFile} from 'node:fs/promises';import {createHash} from 'node:crypto';
import {parseSampleQCTable,SAMPLE_QC_HEADERS} from '../raw-qc-client.js';
import {setup,binary,payload,webcrypto,until,tick,deferred,createQCArtifactScope} from './support/raw-qc-download-setup.mjs';
const encode=s=>new TextEncoder().encode(s),quote=s=>'"'+s.replaceAll('"','""')+'"',header=SAMPLE_QC_HEADERS.map(quote).join('\t');
const row=(label='synthetic alpha',median='NA')=>['1',quote('Intensity '+label),quote(label),'3','1','1','1','1','1.25e+03',median].join('\t');
const table=(rows=[row()],eol='\n')=>encode(header+eol+rows.join(eol)+eol),sampleName='sample-qc.tsv';
async function ready(t,bytes=table(),options={}){const h=await setup(t,{bodies:new Map([[sampleName,bytes]]),...options});assert.equal(await h.start(),true);return h;}

test('two exact tiny R golden outputs retain all header/cell strings and registered provenance hashes',async()=>{
 const pins=JSON.parse(await readFile(new URL('./fixtures/sample-qc-golden-pins.json',import.meta.url),'utf8'));for(const family of ['intensity','lfq']){const bytes=new Uint8Array(await readFile(new URL('./fixtures/sample-qc-'+family+'.golden.tsv',import.meta.url))),pin=pins.find(p=>p.family===family);assert.equal(bytes.length,pin.bytes);assert.equal(createHash('sha256').update(bytes).digest('hex'),pin.sha256);const parsed=parseSampleQCTable(bytes);assert.deepEqual(parsed.headers,SAMPLE_QC_HEADERS);const expected=family==='intensity'?[['1','Intensity alpha','alpha','3','1','1','1','1','3','3'],['3','Intensity beta','beta','3','2','1','0','0','6','3']]:[['2','LFQ intensity alpha','alpha','3','1','1','1','1','2','2'],['4','LFQ intensity beta','beta','3','1','1','1','0','4','4']];assert.deepEqual(parsed.rows.map(r=>SAMPLE_QC_HEADERS.map(k=>r[k])),expected);assert.ok(Object.isFrozen(parsed.rows[0]));}
 const script=await readFile(new URL('../../backend/cyomics_projects/r_raw_qc/approved_raw_qc.R',import.meta.url));assert.equal(script.length,4949);assert.equal(createHash('sha256').update(script).digest('hex'),'2072a20e9dca53ab9ff03ef5a0a5d6aa9556dcee8004b5f28ce2fa4345624d64');
});
test('LF/CRLF and quoted tab/newline/doubled quotes preserve the original decoded labels',()=>{
 const label='NA\t"<script>text</script>"\r\n第二行';for(const eol of ['\n','\r\n']){const p=parseSampleQCTable(table([row(label,'0')],eol));assert.equal(p.rows[0].sample_label_raw,label);assert.equal(p.rows[0].name_raw,'Intensity '+label);assert.equal(p.rows[0].positive_median,'0');assert.equal(p.rows[0].numeric_sum,'1.25e+03');}
});
test('unquoted NA median stays distinct from0, and quoted label NA is an ordinary string',()=>{
 const p=parseSampleQCTable(table([row('NA','NA'),row('zero','0'),row('scientific','2.50e-03')]));assert.equal(p.rows[0].sample_label_raw,'NA');assert.equal(p.rows[0].positive_median,'NA');assert.equal(p.rows[1].positive_median,'0');assert.equal(p.rows[2].positive_median,'2.50e-03');
});
for(const [label,make]of [
 ['duplicate header',()=>encode(header.replace('"blank"','"zero"')+'\n'+row()+'\n')],['missing header',()=>encode(header.split('\t').slice(1).join('\t')+'\n'+row()+'\n')],['extra header',()=>encode(header+'\t"extra"\n'+row()+'\n')],['reordered header',()=>encode(header.replace('"zero"\t"blank"','"blank"\t"zero"')+'\n'+row()+'\n')],['missing cell',()=>table([row().split('\t').slice(1).join('\t')])],['extra cell',()=>table([row()+'\tx'])],['unclosed quote',()=>table([row('unclosed').replace('"synthetic alpha"','"x')+'"'])],['quote in unquoted token',()=>table([row().replace('1.25e+03','1"2')])],['junk after quote',()=>table([row().replace('"synthetic alpha"','"label"junk')])],['bare CR record',()=>encode(header+'\r'+row())],['blank extra record',()=>table([row(),''])],['header only',()=>encode(header+'\n')],['invalid UTF8',()=>new Uint8Array([255])],['NUL',()=>table([row('x\0y')])],['quoted NA numeric',()=>table([row('a','"NA"')])],['negative median',()=>table([row('a','-1')])],['infinite sum',()=>table([row().replace('1.25e+03','1e999')])],['fractional count',()=>table([row().replace('\t3\t','\t3.5\t')])]
])test('strict parser rejects '+label,()=>assert.throws(()=>parseSampleQCTable(make()),e=>e.code==='sample_qc_table_invalid'));
test('4096 rows and exactly1MiB accepted; over either limit rejects the whole table without truncation',()=>{
 assert.equal(parseSampleQCTable(table(Array(4096).fill(row()))).rows.length,4096);assert.throws(()=>parseSampleQCTable(table(Array(4097).fill(row()))),e=>e.code==='sample_qc_preview_limit');
 const base=table([row('')]).length,bytes=table([row('x'.repeat((1048576-base)/2))]);assert.equal(bytes.length,1048576);assert.equal(parseSampleQCTable(bytes).rows.length,1);assert.throws(()=>parseSampleQCTable(new Uint8Array(1048577)),e=>e.code==='sample_qc_preview_limit');
});
test('no auto GET; one explicit preview uses same manifest and exposes frozen strings only, with no Blob',async t=>{
 const h=await ready(t);assert.equal(h.downloads().length,0);const ref=h.c.reference(),wire=JSON.stringify(h.job());assert.equal(await h.c.previewSampleQC(),true);assert.equal(h.downloads().length,1);assert.match(h.downloads()[0].path,/\/artifacts\/sample-qc.tsv\?project_id=/);assert.equal(h.save.active.size,0);assert.equal(h.save.clicks.length,0);assert.equal(h.scope.busy(),false);const p=h.c.view().sample_qc_preview;assert.equal(p.phase,'ready');assert.equal(p.table.rows[0].positive_median,'NA');assert.equal(p.table.rows[0].numeric_sum,'1.25e+03');assert.ok(Object.isFrozen(p.table.rows[0]));assert.deepEqual(h.c.reference(),ref);assert.equal(JSON.stringify(h.job()),wire);assert.doesNotMatch(JSON.stringify(h.c.view()),/sha256|binding|QQQQ|blob:|execution/);
});
test('preview has no document/Blob/URL dependency; disabled artifact gate never previews',async t=>{
 const h=await ready(t,table(),{save:{documentImpl:null,urlImpl:null,BlobImpl:null}});assert.equal(h.c.canDownloadArtifact(sampleName),false);assert.equal(h.c.canPreviewSampleQC(),true);assert.equal(await h.c.previewSampleQC(),true);const off=await ready(t,table(),{enabled:false});assert.equal(await off.c.previewSampleQC(),false);assert.equal(off.downloads().length,0);
});
test('manifest>1MiB displays limit with zero preview GET while original complete download still works',async t=>{
 const h=await ready(t,new Uint8Array(1048577));assert.equal(await h.c.previewSampleQC(),false);assert.equal(h.downloads().length,0);assert.equal(h.c.view().sample_qc_preview.phase,'too_large');assert.equal(h.c.view().sample_qc_preview.table,null);assert.equal(await h.c.downloadArtifact(sampleName),true);assert.equal(h.downloads().length,1);assert.equal(h.save.clicks.length,1);
});
test('valid SHA but4097rows or malformed TSV never publish partial table and leave raw download available',async t=>{
 for(const bytes of [table(Array(4097).fill(row())),encode('bad\tTSV\n')]){const h=await ready(t,bytes);assert.equal(await h.c.previewSampleQC(),false);assert.equal(h.c.view().sample_qc_preview.table,null);assert.equal(h.c.canDownloadArtifact(sampleName),true);assert.equal(h.save.clicks.length,0);}
});
test('bad encoding and wrong body SHA cannot replace a previously shown table',async t=>{
 const bytes=table(),h=await ready(t,bytes);await h.c.previewSampleQC();h.c.cancelSampleQCPreview();const bad=await ready(t,new Uint8Array([255]));assert.equal(await bad.c.previewSampleQC(),false);assert.equal(bad.c.view().sample_qc_preview.table,null);
 const wrong=await ready(t,bytes,{artifactReply:j=>binary(j,sampleName,new Uint8Array(bytes).fill(120))});assert.equal(await wrong.c.previewSampleQC(),false);assert.equal(wrong.c.view().sample_qc_preview.table,null);assert.equal(wrong.save.active.size,0);
});
test('preview and download share the same lease, including cancelled unresolved hash',async t=>{
 const scope=createQCArtifactScope(),held=deferred();let block=false;const cryptoImpl={randomUUID:()=>webcrypto.randomUUID(),subtle:{digest:(...args)=>block?held.promise.then(()=>webcrypto.subtle.digest(...args)):webcrypto.subtle.digest(...args)}};
 const a=await ready(t,table(),{scope,cryptoImpl}),b=await ready(t,table(),{scope});block=true;const p=a.c.previewSampleQC();await until(()=>a.c.view().sample_qc_preview.phase==='verifying');assert.equal(await b.c.downloadArtifact(sampleName),false);a.c.cancelSampleQCPreview();assert.equal(await p,false);assert.equal(a.c.view().sample_qc_preview.table,null);assert.equal(scope.busy(),true);assert.equal(await b.c.previewSampleQC(),false);held.resolve();await until(()=>!scope.busy());assert.equal(a.c.view().sample_qc_preview.table,null);assert.equal(await b.c.previewSampleQC(),true);
});
test('last detach, cancellation and identity loss clear tables; late replies cannot resurrect',async t=>{
 const a=await ready(t);await a.c.previewSampleQC();a.stop();assert.equal(a.c.view().sample_qc_preview.table,null);const b=await ready(t);await b.c.previewSampleQC();b.c.cancelSampleQCPreview();assert.equal(b.c.view().sample_qc_preview.table,null);
 const held=deferred(),c=await ready(t,table(),{artifactReply:()=>held.promise});const p=c.c.previewSampleQC();c.lose();c.c.view();held.resolve(binary(c.job(),sampleName,table()));assert.equal(await p,false);assert.equal(c.c.view().sample_qc_preview.table,null);assert.equal(c.save.clicks.length,0);
});
test('409 requires explicit successful metadata check before preview resumes, with no new POST',async t=>{
 let conflict=true;const bytes=table(),h=await ready(t,bytes,{artifactReply:j=>conflict?Response.json({error:{code:'raw_qc_result_not_ready'}},{status:409}):binary(j,sampleName,bytes)});await h.c.previewSampleQC();assert.equal(h.c.view().sample_qc_preview.table,null);assert.equal(h.c.canPreviewSampleQC(),false);await h.c.check({explicit:false});assert.equal(h.c.canPreviewSampleQC(),false);conflict=false;await h.c.check();assert.equal(await h.c.previewSampleQC(),true);assert.equal(h.calls.filter(x=>x.o.method==='POST').length,1);
});
