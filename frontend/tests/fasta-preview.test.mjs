import assert from 'node:assert/strict';
import { createReadStream } from 'node:fs';
import { readFile, stat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { TextDecoder } from 'node:util';
import { test } from 'node:test';
import { createFastaPreviewParser, FastaPreviewError } from '../fasta-preview.js';

const inputPath = process.env.FASTA_PREVIEW_INPUT;
if (!inputPath) throw new Error('FASTA_PREVIEW_INPUT is required; no demo fallback');
const expectedHash = '8cb4a7994a80b6fecef0310e288c4763261026989b73a4e467d3310aad5daba5';

function parse(text, chunkSize = 65536, options) {
  const parser = createFastaPreviewParser(options);
  for (let i = 0; i < text.length; i += chunkSize) parser.push(text.slice(i, i + chunkSize));
  return parser.finish();
}
function expectError(text, code, options, expectedLine) {
  assert.throws(() => parse(text, 7, options), error => {
    assert.ok(error instanceof FastaPreviewError);
    assert.equal(error.code, code);
    if (expectedLine !== undefined) assert.equal(error.line, expectedLine);
    assert.equal(error.message, `${code} (line ${error.line})`);
    return true;
  });
}
async function parseFile(chunkSize) {
  const parser = createFastaPreviewParser();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  for await (const chunk of createReadStream(inputPath, { highWaterMark: chunkSize })) {
    parser.push(decoder.decode(chunk, { stream: true }));
  }
  parser.push(decoder.decode());
  return parser.finish();
}

test('real FASTA identity, bounded preview, chunking and structural rejection', async t => {
  console.log(`REAL_INPUT=${inputPath}`);
  const before = await stat(inputPath, { bigint: true });
  const bytes = await readFile(inputPath);
  assert.equal(before.size, 17195782n);
  assert.equal(bytes.length, 17195782);
  const beforeHash = createHash('sha256').update(bytes).digest('hex');
  assert.equal(beforeHash, expectedHash);
  console.log(`BEFORE size=${before.size}; sha256=${beforeHash}`);
  const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  // All malformed inputs below derive from this verified real record in memory.
  const firstHeaderEnd = text.indexOf('\n');
  const nextHeader = text.indexOf('\n>', firstHeaderEnd);
  assert.ok(firstHeaderEnd > 0 && nextHeader > firstHeaderEnd);
  const header = text.slice(0, firstHeaderEnd).replace(/\r$/, '');
  const sequence = text.slice(firstHeaderEnd + 1, nextHeader).replace(/\r$/, '');
  const record = `${header}\n${sequence}\n`;
  const firstId = header.slice(1).trim().split(/\s+/)[0];
  assert.equal(firstId, 'Pt_Chr0100001');
  const originalLength = sequence.replace(/\s/g, '').length;
  let summary;

  try {
    await t.test('full real input: 38387 records, zero empty/duplicate IDs, ten summaries', async () => {
      summary = await parseFile(65536);
      assert.equal(summary.recordCount, 38387);
      assert.equal(summary.emptySequenceCount, 0);
      assert.equal(summary.duplicateIdCount, 0);
      assert.equal(summary.preview.length, 10);
      assert.deepEqual(summary.preview[0], { id: firstId, length: originalLength });
      assert.deepEqual(Object.keys(summary).sort(), ['duplicateIdCount', 'emptySequenceCount', 'preview', 'recordCount', 'totalSequenceLength']);
      assert.ok(summary.preview.every(item => Object.keys(item).sort().join(',') === 'id,length'));
      console.log(`REAL_RESULT records=${summary.recordCount}; empty=${summary.emptySequenceCount}; duplicates=${summary.duplicateIdCount}; preview=${summary.preview.length}; firstId=${summary.preview[0].id}`);
    });
    await t.test('complete real input is identical with 4093-byte UTF-8 stream chunks', async () => {
      const smallChunks = await parseFile(4093);
      assert.deepEqual(smallChunks, summary);
      console.log('CHUNK_SIZES=65536,4093; summaries identical');
    });
    await t.test('real-record derivatives: BOM, whitespace, CRLF, header token and no final newline', () => {
      const reference = parse(record, 1);
      const spacedSequence = sequence.replace(/\s/g, '').split('').join(' \t');
      const variant = `\uFEFF\r\n \t\r\n> \t${firstId} real-record description\r\n\r\n${spacedSequence}`;
      for (const chunkSize of [1, 2, 7, 65536]) {
        assert.deepEqual(parse(variant, chunkSize), reference);
      }
      assert.deepEqual(parse(record.trimEnd(), 1), reference);
    });
    await t.test('missing header, empty ID, empty sequence and duplicate ID reject', () => {
      expectError(sequence, 'SEQUENCE_BEFORE_HEADER', undefined, 1);
      expectError(`> \t\n${sequence}`, 'EMPTY_ID', undefined, 1);
      expectError(`${header}\n \t\n`, 'EMPTY_SEQUENCE', undefined, 1);
      expectError(record + record, 'DUPLICATE_ID');
      expectError(header, 'EMPTY_SEQUENCE', undefined, 1);
      expectError(' \r\n\t', 'NO_RECORDS');
    });
    await t.test('validation continues beyond the finite preview through the real-file tail', () => {
      expectError(text + '\n' + record, 'DUPLICATE_ID');
      expectError(text + '\n>\n', 'EMPTY_ID');
      expectError(text, 'RECORD_LIMIT', { maxRecords: 38386 });
    });
    await t.test('all resource ceilings reject instead of growing without bounds', () => {
      expectError(record, 'LINE_LENGTH_LIMIT', { maxLineLength: 1 });
      expectError(record, 'SEQUENCE_LENGTH_LIMIT', { maxSequenceLength: originalLength - 1 });
      expectError(record, 'TOTAL_CHARACTER_LIMIT', { maxTotalCharacters: record.length - 1 });
      expectError(record + record, 'RECORD_LIMIT', { maxRecords: 1 });
      expectError(record, 'ID_LENGTH_LIMIT', { maxIdLength: firstId.length - 1 });
      expectError(record, 'ID_BUDGET_LIMIT', { maxTotalIdCharacters: firstId.length - 1 });
      assert.equal(parse(record, 3, { previewLimit: 0 }).preview.length, 0);
      assert.equal(parse(record, 3, { maxRecords: 1, maxTotalCharacters: record.length, maxSequenceLength: originalLength }).recordCount, 1);
    });
    await t.test('options, parser lifetime and immutable summaries are checked', () => {
      for (const options of [null, [], false, { unknown: 1 }, { maxRecords: 50001 },
        { maxLineLength: 1000001 }, { maxSequenceLength: 1000001 }, { maxIdLength: 257 },
        { maxTotalIdCharacters: 2000001 }, { maxTotalCharacters: 26214401 },
        { previewLimit: 101 }, { maxRecords: 0 }, { previewLimit: -1 },
        { maxRecords: 1.5 }, { maxRecords: Infinity }, { maxRecords: '1' },
        { maxRecords: undefined }]) {
        assert.throws(() => createFastaPreviewParser(options), { code: 'INVALID_OPTIONS' });
      }
      const parser = createFastaPreviewParser();
      parser.push('');
      parser.push(record);
      const result = parser.finish();
      assert.equal(parser.finish(), result);
      assert.throws(() => parser.push(record), { code: 'PARSER_FINISHED' });
      assert.ok(Object.isFrozen(result) && Object.isFrozen(result.preview) && Object.isFrozen(result.preview[0]));
      const failed = createFastaPreviewParser();
      assert.throws(() => failed.push(123), { code: 'INVALID_CHUNK' });
      assert.throws(() => failed.push(record), { code: 'INVALID_CHUNK' });
      assert.throws(() => failed.finish(), { code: 'INVALID_CHUNK' });
    });
  } finally {
    const afterBytes = await readFile(inputPath);
    const afterHash = createHash('sha256').update(afterBytes).digest('hex');
    const after = await stat(inputPath, { bigint: true });
    assert.equal(afterHash, expectedHash);
    assert.equal(afterHash, beforeHash);
    assert.equal(after.size, before.size);
    assert.equal(after.mtimeNs, before.mtimeNs);
    console.log(`AFTER size=${after.size}; sha256=${afterHash}; size/mtime unchanged`);
  }
});
