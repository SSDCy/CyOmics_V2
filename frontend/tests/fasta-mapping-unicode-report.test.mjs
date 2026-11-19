import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash,webcrypto} from 'node:crypto';
import {quantBinding,sha,asciiJSON,canonicalJSON} from '../fasta-quant-mapping-contract.mjs';
import {sha as originalUTF8SHA} from '../raw-qc-contract.mjs';
const f=JSON.parse(await readFile(new URL('./fixtures/fasta-mapping-unicode-report.expected.json',import.meta.url),'utf8'));
const hash=text=>createHash('sha256').update(text,'utf8').digest('hex');
test('S1-FASTA-QUANT-MAPPING-UI-001-unicode-report existing UTF8 saved report hash only',async t=>{
 const report=f.full.importDTO.importReport,binding=await quantBinding(f.full,webcrypto);
 assert.equal(binding.report_sha256,f.saved_report_sha256);assert.equal(binding.report_sha256,await originalUTF8SHA(report,webcrypto));assert.equal(hash(canonicalJSON(report)),f.saved_report_sha256);assert.equal(Buffer.byteLength(canonicalJSON(report),'utf8'),f.utf8_bytes);
 assert.equal(await sha(report,webcrypto),f.ascii_report_sha256);assert.equal(hash(asciiJSON(report)),f.ascii_report_sha256);assert.equal(Buffer.byteLength(asciiJSON(report),'utf8'),f.ascii_bytes);assert.notEqual(f.saved_report_sha256,f.ascii_report_sha256);
 const escaped=JSON.parse(asciiJSON(report));assert.deepEqual(escaped,report);const alternate=structuredClone(f.full);alternate.importDTO.importReport=escaped;assert.equal((await quantBinding(alternate,webcrypto)).report_sha256,f.saved_report_sha256);
 const literal=structuredClone(f.full);literal.importDTO.importReport.sample_columns[0].name_raw+='\\u6837';assert.notEqual((await quantBinding(literal,webcrypto)).report_sha256,f.saved_report_sha256);
 const ascii=JSON.parse(JSON.stringify(f.full).replace(/[^\x00-\x7f]/g,'_'));const asciiReport=ascii.importDTO.importReport;assert.equal((await quantBinding(ascii,webcrypto)).report_sha256,await sha(asciiReport,webcrypto));
 const inputs={algorithm:'protein_groups_exact_fasta_members_v1',quantification:binding,unicode_evidence:'样本"A\\path'};assert.equal(await sha(inputs,webcrypto),hash(asciiJSON(inputs)));assert.notEqual(await sha(inputs,webcrypto),hash(canonicalJSON(inputs)));
 t.diagnostic('One narrow synthetic report regression, independent Python UTF8/ASCII saved-hash oracle plus existing raw-QC UTF8 SHA. Core/input ASCII hash unchanged; original composition/rawcap targets not replayed; no actual DB/parser/network.');
});
