import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {parseProteinGroups} from '../protein-groups.js';
import {createFastaPreviewParser} from '../fasta-preview.js';
import {parseProteinGroupsForAssociation,createFastaAssociationParser,associateProteinGroups,releaseAssociationInputs} from '../protein-association.js';
const paths=[process.env.TXT_PREVIEW_INPUT,process.env.FASTA_PREVIEW_INPUT];
if(paths.some(x=>!x))throw Error('Explicit real input paths required; no demo fallback');
const hash=b=>createHash('sha256').update(b).digest('hex');
test('supplied real data: preview, exact complete association and rejection',async t=>{
 const buffers=await Promise.all(paths.map(p=>readFile(p))),digests=buffers.map(hash);
 assert.equal(digests[0],'301bc95ff440a4ea70912fb5ede26aa54cb15f2cb06a9efeed6e55fd749f95fb');
 assert.equal(digests[1],'8cb4a7994a80b6fecef0310e288c4763261026989b73a4e467d3310aad5daba5');
 const [txt,fasta]=buffers.map(b=>new TextDecoder('utf-8',{fatal:true}).decode(b));
 await t.test('quantification original strings and both 21-column types',()=>{
  const p=parseProteinGroups(txt),rows=txt.trimEnd().split(/\r?\n/).slice(1,11).map(x=>x.split('\t'));
  assert.equal(p.rowCount,2252);assert.equal(p.columnCount,182);assert.equal(p.intensityColumns.length,21);assert.equal(p.lfqColumns.length,21);
  assert.ok(JSON.stringify(p.previewRows)===JSON.stringify(rows),'Original preview strings changed');
 });
 const preview=createFastaPreviewParser(),index=createFastaAssociationParser();
 for(let i=0;i<fasta.length;i+=65536){const chunk=fasta.slice(i,i+65536);preview.push(chunk);index.push(chunk);}
 const summary=preview.finish(),f=index.finish(),table=parseProteinGroupsForAssociation(txt),joined=associateProteinGroups(table,f);
 await t.test('FASTA bounded summary and full join counts',()=>{
  assert.equal(summary.recordCount,38387);assert.equal(summary.totalSequenceLength,16367314);assert.equal(summary.preview.length,10);
  assert.equal(joined.summary.rowCount,2252);assert.equal(joined.summary.allMatchRows,2223);assert.equal(joined.summary.partialMatchRows,0);assert.equal(joined.summary.zeroMatchRows,29);
  assert.equal(Math.ceil(joined.rows.length/50),46);assert.equal(joined.rows.slice(2250).length,2);assert.equal(joined.rows[1204].matches.length,23);
 });
 await t.test('every group member/order/length matches an independent line parser',()=>{
  const expectedIndex=new Map();let id=null,len=0;
  for(const raw of fasta.split(/\r?\n/)){if(raw.startsWith('>')){if(id!==null)expectedIndex.set(id,len);id=raw.slice(1).trim().split(/\s/)[0];len=0;}else len+=raw.replace(/\s/g,'').length;}if(id!==null)expectedIndex.set(id,len);
  const expectedRows=txt.trimEnd().split(/\r?\n/).slice(1);
  for(let i=0;i<expectedRows.length;i++){const ids=expectedRows[i].split('\t')[0],expected=ids.split(';').map(x=>x.trim()).filter(Boolean).map(id=>({id,length:expectedIndex.get(id)??null}));assert.equal(joined.rows[i].proteinIds,ids);assert.ok(JSON.stringify(joined.rows[i].matches)===JSON.stringify(expected),'Group ID/order/length mismatch');}
 });
 await t.test('invalid header and last-record width are rejected',()=>{
  assert.throws(()=>parseProteinGroups(txt.replace(/^Protein IDs\t/,'Missing ID\t')),/Protein IDs/);
  const lines=txt.trimEnd().split(/\r?\n/),last=lines.at(-1).split('\t');last.pop();lines[lines.length-1]=last.join('\t');assert.throws(()=>parseProteinGroups(lines.join('\n')),/2252/);
 });
 releaseAssociationInputs(table,f);
 assert.throws(()=>associateProteinGroups(table,f),{code:'UNVALIDATED_INPUT'});
 for(let i=0;i<paths.length;i++)assert.equal(hash(await readFile(paths[i])),digests[i]);
});
