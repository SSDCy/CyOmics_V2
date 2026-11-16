import { parseProteinGroups } from './protein-groups.js';
import { createFastaPreviewParser } from './fasta-preview.js';

export const ASSOCIATION_LIMITS = Object.freeze({
  maxGroups: 10000, maxTokens: 100000, maxIdCharacters: 2000000,
});
const tables = new WeakMap(), indexes = new WeakMap();

export class ProteinAssociationError extends Error {
  constructor(code) { super(code); this.name = 'ProteinAssociationError'; this.code = code; }
}
const error = code => new ProteinAssociationError(code);
function checkedLimits(options) {
  if (!options || typeof options !== 'object' || Array.isArray(options)
      || ![Object.prototype, null].includes(Object.getPrototypeOf(options))) throw error('INVALID_LIMITS');
  const limits = { ...ASSOCIATION_LIMITS };
  for (const key of Reflect.ownKeys(options)) {
    if (!Object.hasOwn(limits, key)) throw error('INVALID_LIMITS');
    const value = options[key];
    if (!Number.isSafeInteger(value) || value < 1 || value > ASSOCIATION_LIMITS[key]) throw error('INVALID_LIMITS');
    limits[key] = value;
  }
  return limits;
}

/** Full validation, zero matrix preview retention. Only ID metadata is retained.
 * Character budget counts raw Protein IDs fields plus all trimmed token copies.
 * The caller must also enforce the 10 MiB file-byte gate before decoding.
 */
export function parseProteinGroupsForAssociation(text, options = {}) {
  const limits = checkedLimits(options);
  if (typeof text !== 'string' || text.length > 10 * 1024 * 1024) throw error('TEXT_LIMIT');
  const groups = [];
  let tokenCount = 0, idCharacters = 0, collectionFailure;
  const reject = code => { collectionFailure = error(code); throw collectionFailure; };
  try {
    const parsed = parseProteinGroups(text, 0, { onRecord(record) {
      if (groups.length >= limits.maxGroups) reject('GROUP_LIMIT');
      tokenCount += record.tokens.length;
      if (tokenCount > limits.maxTokens) reject('TOKEN_LIMIT');
      idCharacters += record.proteinIds.length + record.tokens.reduce((sum, id) => sum + id.length, 0);
      if (idCharacters > limits.maxIdCharacters) reject('ID_CHARACTER_LIMIT');
      groups.push(Object.freeze({ recordNumber: record.recordNumber,
        proteinIds: record.proteinIds, tokens: Object.freeze([...record.tokens]) }));
    } });
    const snapshot = Object.freeze({ basis: 'Protein IDs', rowCount: parsed.rowCount });
    tables.set(snapshot, groups);
    return snapshot;
  } catch (failure) {
    groups.length = 0;
    throw collectionFailure || failure;
  }
}

/** Index publication only after successful finish. Original FASTA limits apply.
 * The caller owns streaming UTF-8 decoding and the 25 MiB file-byte gate.
 * No sequence, complete input, or mutable Map is exposed or retained.
 */
export function createFastaAssociationParser(options = {}) {
  let staged = new Map(), failure, snapshot;
  let parser = createFastaPreviewParser(options, { onRecord(record) {
    staged.set(record.id, record.length);
  } });
  const reject = cause => {
    staged?.clear(); staged = null; parser = null; failure = cause; throw cause;
  };
  return Object.freeze({
    push(chunk) {
      if (failure) throw failure;
      if (snapshot) throw error('INDEX_FINISHED');
      try { parser.push(chunk); }
      catch (cause) { reject(cause); }
    },
    finish() {
      if (failure) throw failure;
      if (snapshot) return snapshot;
      try {
        const summary = parser.finish();
        snapshot = Object.freeze({ recordCount: summary.recordCount,
          totalSequenceLength: summary.totalSequenceLength });
        indexes.set(snapshot, staged);
        staged = null;
        parser = null;
        return snapshot;
      } catch (cause) { reject(cause); }
    },
    abort() {
      staged?.clear();
      staged = null; parser = null;
      if (snapshot) { indexes.get(snapshot)?.clear(); indexes.delete(snapshot); }
      failure = error('INDEX_ABORTED');
    },
  });
}

/** Only snapshots produced by successful validators above are accepted. */
export function associateProteinGroups(table, fasta) {
  const groups = tables.get(table), index = indexes.get(fasta);
  if (!groups || !index) throw error('UNVALIDATED_INPUT');
  const summary = { rowCount: groups.length, allMatchRows: 0, partialMatchRows: 0,
    zeroMatchRows: 0, emptyIdRows: 0, tokenOccurrences: 0,
    distinctTokenCount: 0, matchedDistinctTokenCount: 0, unmatchedDistinctTokenCount: 0 };
  const distinct = new Set();
  const rows = groups.map(group => {
    let hits = 0;
    const matches = group.tokens.map(id => {
      distinct.add(id);
      const length = index.has(id) ? index.get(id) : null;
      if (length !== null) hits++;
      return Object.freeze({ id, length });
    });
    const emptyIds = !matches.length;
    const status = !emptyIds && hits === matches.length ? 'all' : hits ? 'partial' : 'none';
    summary[status === 'all' ? 'allMatchRows' : status === 'partial' ? 'partialMatchRows' : 'zeroMatchRows']++;
    summary.emptyIdRows += Number(emptyIds);
    summary.tokenOccurrences += matches.length;
    return Object.freeze({ recordNumber: group.recordNumber, proteinIds: group.proteinIds,
      emptyIds, status, matches: Object.freeze(matches) });
  });
  summary.distinctTokenCount = distinct.size;
  for (const id of distinct) {
    if (index.has(id)) summary.matchedDistinctTokenCount++;
    else summary.unmatchedDistinctTokenCount++;
  }
  return Object.freeze({ basis: 'Protein IDs', summary: Object.freeze(summary), rows: Object.freeze(rows) });
}

/** Explicitly release private metadata. Consumers must also drop joined results. */
export function releaseAssociationInputs(...snapshots) {
  for (const snapshot of snapshots) {
    const groups = tables.get(snapshot), index = indexes.get(snapshot);
    if (groups) { groups.length = 0; tables.delete(snapshot); }
    if (index) { index.clear(); indexes.delete(snapshot); }
  }
}
