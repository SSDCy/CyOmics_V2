import assert from 'node:assert/strict';
import { readFile, stat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
import { parseProteinGroups } from '../protein-groups.js';
import { createFastaPreviewParser, FASTA_PREVIEW_LIMITS } from '../fasta-preview.js';
import { parseProteinGroups as beforeTxt } from '../outputs/V2-T010-B1-before/protein-groups.js';
import { createFastaPreviewParser as beforeFasta, FASTA_PREVIEW_LIMITS as beforeLimits } from '../outputs/V2-T010-B1-before/fasta-preview.js';
import { parseProteinGroupsForAssociation, createFastaAssociationParser,
  associateProteinGroups, releaseAssociationInputs } from '../protein-association.js';

const txtPath = process.env.TXT_PREVIEW_INPUT, fastaPath = process.env.FASTA_PREVIEW_INPUT;
if (!txtPath || !fastaPath) throw new Error('Explicit TXT_PREVIEW_INPUT and FASTA_PREVIEW_INPUT required; no demo fallback');
const inputs = [
  [txtPath, 6186652, '301bc95ff440a4ea70912fb5ede26aa54cb15f2cb06a9efeed6e55fd749f95fb'],
  [fastaPath, 17195782, '8cb4a7994a80b6fecef0310e288c4763261026989b73a4e467d3310aad5daba5'],
];
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
// Avoid exposing raw matrix cells or sequences in assertion diagnostics.
const same = (actual, expected, message) => assert.ok(JSON.stringify(actual) === JSON.stringify(expected), message);
const finishText = (parser, text, chunkSize = 65536) => {
  for (let i = 0; i < text.length; i += chunkSize) parser.push(text.slice(i, i + chunkSize));
  return parser.finish();
};

test('B1 real-file association, callback isolation and publication boundaries', async t => {
  const before = await Promise.all(inputs.map(async ([path, size, digest]) => {
    const metadata = await stat(path, { bigint: true }), bytes = await readFile(path);
    assert.equal(bytes.length, size);
    assert.equal(sha(bytes), digest);
    console.log(`INPUT ${path}; size=${size}; sha256=${digest}`);
    return { metadata, bytes };
  }));
  const decode = bytes => new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  const txt = decode(before[0].bytes), fasta = decode(before[1].bytes);
  // Independent raw TAB/reference FASTA extraction, not either production parser.
  const lines = txt.split(/\r\n|\n|\r/);
  if (lines.at(-1) === '') lines.pop();
  const headers = lines.shift().split('\t'), rawRows = lines.map(line => line.split('\t'));
  assert.equal(headers.length, 182);
  assert.equal(rawRows.length, 2252);
  assert.ok(rawRows.every(row => row.length === 182));
  const idIndex = headers.indexOf('Protein IDs');
  const referenceGroups = rawRows.map((row, i) => ({ recordNumber: i + 1,
    proteinIds: row[idIndex], tokens: row[idIndex].split(';').map(id => id.trim()).filter(Boolean) }));
  const referenceIndex = new Map();
  const records = fasta.split(/^>/m).slice(1);
  for (const record of records) {
    const newline = record.search(/[\r\n]/);
    const id = record.slice(0, newline).trim().split(/\s+/)[0];
    const length = record.slice(newline).replace(/\s/g, '').length;
    assert.ok(!referenceIndex.has(id));
    referenceIndex.set(id, length);
  }
  assert.equal(referenceIndex.size, 38387);
  const firstHeaderEnd = fasta.indexOf('\n'), nextHeader = fasta.indexOf('\n>', firstHeaderEnd);
  const firstId = fasta.slice(1, firstHeaderEnd).trim().split(/\s+/)[0];
  const sequence = fasta.slice(firstHeaderEnd + 1, nextHeader);
  const firstRecord = fasta.slice(0, nextHeader + 1);
  const tableOf = ids => [headers.join('\t'), ...ids.map((id, i) => {
    const row = [...rawRows[i % rawRows.length]]; row[idIndex] = id; return row.join('\t');
  })].join('\n');
  // A compact projection of a real record for large boundary derivatives.
  const compact = ids => ['Protein IDs\tMajority protein IDs', ...ids.map(id => id + '\t' + firstId)].join('\n');
  const makeIndex = (text = fasta, options) => finishText(createFastaAssociationParser(options), text);
  let table, index;
  try {
    await t.test('default results and preview ten remain byte-value equivalent to B1-before modules', () => {
      const old = beforeTxt(txt), current = parseProteinGroups(txt);
      same(current, old, 'Default TXT result, original strings or quantification indices changed');
      assert.equal(current.previewRows.length, 10);
      same(FASTA_PREVIEW_LIMITS, beforeLimits, 'Original FASTA ceilings changed');
      same(finishText(createFastaPreviewParser(), fasta), finishText(beforeFasta(), fasta), 'Default FASTA summary changed');
      console.log('DEFAULTS TXT=2252/182/10/21+21; FASTA=38387/16367314/10; equal to saved before-modules');
    });
    await t.test('all provisional record callbacks contain only ID metadata and mutations cannot damage previews', () => {
      const captured = [];
      const current = parseProteinGroups(txt, 10, { onRecord(record) {
        same(Object.keys(record).sort(), ['proteinIds', 'recordNumber', 'tokens'], 'TXT callback leaked matrix data');
        captured.push({ ...record, tokens: [...record.tokens] });
        record.recordNumber = -1; record.proteinIds = 'changed'; record.tokens.length = 0;
      } });
      same(captured, referenceGroups, 'TXT callback metadata differs from full independent extraction');
      same(current, beforeTxt(txt), 'TXT callback mutation damaged default preview');
      let callbacks = 0;
      const parser = createFastaPreviewParser({}, { onRecord(record) {
        same(Object.keys(record).sort(), ['id', 'length'], 'FASTA callback leaked sequence data');
        assert.ok(referenceIndex.has(record.id) && referenceIndex.get(record.id) === record.length, 'Callback ID/length mismatch');
        callbacks++; record.id = 'changed'; record.length = -1;
      } });
      same(finishText(parser, fasta, 4093), finishText(beforeFasta(), fasta), 'FASTA callback mutation damaged default preview');
      assert.equal(callbacks, 38387);
      console.log('CALLBACKS TXT=2252; FASTA=38387; metadata-only; mutation isolation passed');
    });
    await t.test('full join matches every original group/ID/length including rows beyond ten and last group', () => {
      table = parseProteinGroupsForAssociation(txt); index = makeIndex();
      assert.deepEqual(Object.keys(table).sort(), ['basis', 'rowCount']);
      assert.deepEqual(Object.keys(index).sort(), ['recordCount', 'totalSequenceLength']);
      const joined = associateProteinGroups(table, index);
      same(joined.summary, { rowCount: 2252, allMatchRows: 2223, partialMatchRows: 0,
        zeroMatchRows: 29, emptyIdRows: 0, tokenOccurrences: 4060,
        distinctTokenCount: 4060, matchedDistinctTokenCount: 3994, unmatchedDistinctTokenCount: 66 }, 'Full join summary differs from independent T007 baseline');
      assert.equal(joined.basis, 'Protein IDs');
      assert.equal(joined.rows.length, referenceGroups.length);
      joined.rows.forEach((row, i) => {
        const group = referenceGroups[i];
        const matches = group.tokens.map(id => ({ id, length: referenceIndex.get(id) ?? null }));
        const hits = matches.filter(match => match.length !== null).length;
        const status = !matches.length ? 'none' : hits === matches.length ? 'all' : hits ? 'partial' : 'none';
        same(row, { recordNumber: group.recordNumber, proteinIds: group.proteinIds,
          emptyIds: !matches.length, status, matches }, 'Per-group original ID/order/length/status mismatch');
        assert.ok(Object.isFrozen(row) && Object.isFrozen(row.matches) && row.matches.every(Object.isFrozen));
      });
      assert.equal(joined.rows.at(-1).recordNumber, 2252);
      assert.ok(Object.isFrozen(joined) && Object.isFrozen(joined.rows) && Object.isFrozen(joined.summary));
      console.log('JOIN rows=2252; all=2223; partial=0; zero=29; distinct=4060; matched=3994; unmatched=66; every group/ID/length/order verified through row 2252');
    });
    await t.test('real-record derivatives retain empty/partial/repeated IDs and exact case/prefix rules', () => {
      const field = ` ${firstId} ; ${firstId.toLowerCase()} ; REV__${firstId} ; ${firstId}.1 ; CON__${firstId} `;
      const derived = parseProteinGroupsForAssociation(tableOf([' ; \t'.replace('\t', ' '), field, `${firstId};${firstId}`]));
      const joined = associateProteinGroups(derived, index);
      assert.equal(joined.rows.length, 3);
      assert.equal(joined.rows[0].emptyIds, true);
      assert.equal(joined.rows[0].status, 'none');
      assert.equal(joined.summary.emptyIdRows, 1);
      assert.equal(joined.summary.zeroMatchRows, 1);
      assert.equal(joined.rows[1].status, 'partial');
      assert.ok(joined.rows[1].proteinIds === field, 'Original group string changed');
      assert.equal(joined.rows[1].matches.length, 5);
      assert.equal(joined.rows[1].matches.filter(match => match.length !== null).length, 1);
      assert.equal(joined.rows[2].matches.length, 2);
      assert.equal(joined.rows[2].status, 'all');
      assert.equal(joined.summary.tokenOccurrences, 7);
      assert.equal(joined.summary.distinctTokenCount, 5);
      releaseAssociationInputs(derived);
    });
    await t.test('safe keys use Map and forged/released/unfinished inputs never join', () => {
      const ids = ['__proto__', 'constructor', 'toString'];
      const safeIndex = makeIndex(ids.map(id => `>${id}\n${sequence}\n`).join(''));
      const safeTable = parseProteinGroupsForAssociation(tableOf([ids.join(';')]));
      const joined = associateProteinGroups(safeTable, safeIndex);
      assert.equal(joined.rows[0].status, 'all');
      assert.ok(joined.rows[0].matches.every(match => match.length === referenceIndex.get(firstId)));
      assert.equal(Object.getPrototypeOf({}), Object.prototype);
      for (const bad of [{}, null, { basis: 'Protein IDs', rowCount: 1 }]) {
        assert.throws(() => associateProteinGroups(bad, index), { code: 'UNVALIDATED_INPUT' });
      }
      const unfinished = createFastaAssociationParser(); unfinished.push(firstRecord);
      assert.throws(() => associateProteinGroups(table, unfinished), { code: 'UNVALIDATED_INPUT' });
      unfinished.abort(); assert.throws(() => unfinished.finish(), { code: 'INDEX_ABORTED' });
      releaseAssociationInputs(safeTable, safeIndex);
      assert.throws(() => associateProteinGroups(safeTable, safeIndex), { code: 'UNVALIDATED_INPUT' });
    });
    await t.test('late TXT/FASTA errors discard provisional collections and never publish snapshots', () => {
      const broken = rawRows.map(row => [...row]); broken.at(-1).pop();
      let published;
      assert.throws(() => { published = parseProteinGroupsForAssociation([headers.join('\t'), ...broken.map(row => row.join('\t'))].join('\n')); });
      assert.equal(published, undefined);
      for (const invalid of [fasta + '\n' + firstRecord, fasta + '\n>\n', fasta + '\n>UNIQUE_EMPTY_TAIL\n']) {
        const pending = createFastaAssociationParser();
        assert.throws(() => { published = finishText(pending, invalid); });
        assert.equal(published, undefined);
        assert.throws(() => pending.finish()); assert.throws(() => pending.push(firstRecord));
      }
      const aborted = createFastaAssociationParser(); aborted.push(firstRecord); aborted.abort();
      assert.throws(() => aborted.push(firstRecord), { code: 'INDEX_ABORTED' });
      console.log('FAILURE_GATE late record errors/duplicate ID/empty ID/empty sequence/abort: no snapshots published');
    });
    await t.test('exact association ceilings accept boundaries and reject overflow without truncation', () => {
      const groups = Array(10000).fill(firstId);
      const atGroups = parseProteinGroupsForAssociation(compact(groups));
      assert.equal(atGroups.rowCount, 10000); releaseAssociationInputs(atGroups);
      assert.throws(() => parseProteinGroupsForAssociation(compact([...groups, firstId])), { code: 'GROUP_LIMIT' });
      const shortId = firstId[0];
      const many = Array(100000).fill(shortId).join(';');
      const atTokens = parseProteinGroupsForAssociation(compact([many]));
      assert.equal(associateProteinGroups(atTokens, index).summary.tokenOccurrences, 100000);
      releaseAssociationInputs(atTokens);
      assert.throws(() => parseProteinGroupsForAssociation(compact([many + ';' + shortId])), { code: 'TOKEN_LIMIT' });
      const padded = ' '.repeat(2000000 - 2 * firstId.length) + firstId;
      const atCharacters = parseProteinGroupsForAssociation(compact([padded]));
      assert.equal(atCharacters.rowCount, 1); releaseAssociationInputs(atCharacters);
      assert.throws(() => parseProteinGroupsForAssociation(compact([padded + ' '])), { code: 'ID_CHARACTER_LIMIT' });
      assert.throws(() => parseProteinGroupsForAssociation(tableOf([firstId, firstId]), { maxGroups: 1 }), { code: 'GROUP_LIMIT' });
      assert.throws(() => parseProteinGroupsForAssociation(tableOf([firstId + ';' + firstId]), { maxTokens: 1 }), { code: 'TOKEN_LIMIT' });
      assert.throws(() => parseProteinGroupsForAssociation(' '.repeat(10 * 1024 * 1024 + 1)), { code: 'TEXT_LIMIT' });
      for (const options of [{ maxRecords: 1 }, { maxSequenceLength: referenceIndex.get(firstId) - 1 }, { maxTotalCharacters: firstRecord.length - 1 }]) {
        const pending = createFastaAssociationParser(options);
        assert.throws(() => finishText(pending, options.maxRecords ? firstRecord + firstRecord : firstRecord));
        assert.throws(() => pending.finish());
      }
      console.log('LIMITS exact 10000 groups/100000 token occurrences/2000000 ID characters accepted; +1 rejected; FASTA original ceilings retained');
    });
    await t.test('callback configuration/errors/thenables/reentrancy fail safely; lifecycle and limit options stay strict', () => {
      let accessorReads = 0;
      const accessorCallbacks = Object.defineProperty({}, 'onRecord', { get() { accessorReads++; return () => {}; } });
      for (const callbacks of [null, [], false, { unknown: () => {} }, { onRecord: undefined },
        { onRecord: 1 }, { onRecord: async () => {} }, { onRecord: function* () {} },
        Object.create({ onRecord() {} }), { [Symbol('unknown')]: () => {} }, accessorCallbacks]) {
        assert.throws(() => parseProteinGroups(tableOf([firstId]), 10, callbacks), { code: 'INVALID_CALLBACKS' });
        assert.throws(() => createFastaPreviewParser({}, callbacks), { code: 'INVALID_CALLBACKS' });
      }
      assert.equal(accessorReads, 0);
      for (const onRecord of [() => { throw new Error('do not echo callback details'); }, () => Promise.reject(new Error('do not echo promise details'))]) {
        assert.throws(() => parseProteinGroups(tableOf([firstId]), 10, { onRecord }), { code: 'CALLBACK_FAILED' });
        const parser = createFastaPreviewParser({}, { onRecord }); parser.push(firstRecord);
        assert.throws(() => parser.finish(), { code: 'CALLBACK_FAILED' });
        assert.throws(() => parser.finish(), { code: 'CALLBACK_FAILED' });
        assert.throws(() => parser.push(firstRecord), { code: 'CALLBACK_FAILED' });
      }
      let reentrant;
      reentrant = createFastaPreviewParser({}, { onRecord() { reentrant.finish(); } });
      reentrant.push(firstRecord);
      assert.throws(() => reentrant.finish(), { code: 'CALLBACK_FAILED' });
      for (const options of [null, [], { maxGroups: 10001 }, { maxTokens: 100001 }, { maxIdCharacters: 2000001 },
        { maxGroups: 0 }, { maxTokens: 1.5 }, { maxGroups: Infinity }, { maxTokens: '1' }, { unknown: 1 }]) {
        assert.throws(() => parseProteinGroupsForAssociation(txt, options), { code: 'INVALID_LIMITS' });
      }
      let limitReads = 0;
      const changingLimit = { get maxGroups() { limitReads++; return limitReads === 1 ? 1 : 10001; } };
      assert.throws(() => parseProteinGroupsForAssociation(tableOf([firstId, firstId]), changingLimit), { code: 'GROUP_LIMIT' });
      assert.equal(limitReads, 1);
      assert.throws(() => createFastaAssociationParser({ maxRecords: 50001 }), { code: 'INVALID_OPTIONS' });
      assert.throws(() => createFastaAssociationParser({ previewLimit: 101 }), { code: 'INVALID_OPTIONS' });
      assert.throws(() => createFastaPreviewParser({ onRecord() {} }), { code: 'INVALID_OPTIONS' });
      const finished = createFastaAssociationParser(); finished.push(firstRecord);
      const snapshot = finished.finish(); assert.equal(snapshot, finished.finish());
      assert.throws(() => finished.push(firstRecord), { code: 'INDEX_FINISHED' });
      assert.equal(associateProteinGroups(table, snapshot).summary.rowCount, 2252);
      finished.abort(); assert.throws(() => associateProteinGroups(table, snapshot), { code: 'UNVALIDATED_INPUT' });
      console.log('CALLBACK_GUARDS invalid/async/throwing/thenable/reentrant cases rejected; default previews isolated; snapshots immutable and releaseable');
    });
  } finally {
    releaseAssociationInputs(table, index);
    for (let i = 0; i < inputs.length; i++) {
      const [path, , digest] = inputs[i], metadata = await stat(path, { bigint: true });
      assert.equal(sha(await readFile(path)), digest);
      assert.equal(metadata.size, before[i].metadata.size);
      assert.equal(metadata.mtimeNs, before[i].metadata.mtimeNs);
      console.log(`UNCHANGED ${path}; size/mtime/SHA256 match before`);
    }
  }
});
