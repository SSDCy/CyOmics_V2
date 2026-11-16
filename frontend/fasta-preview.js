// Pure, bounded FASTA structure preview. No I/O, sequence storage or analysis.
export const FASTA_PREVIEW_LIMITS = Object.freeze({
  maxRecords: 50000,
  previewLimit: 10,
  maxIdLength: 256,
  maxTotalIdCharacters: 2000000,
  maxLineLength: 1000000,
  maxSequenceLength: 1000000,
  maxTotalCharacters: 25 * 1024 * 1024,
});
const ceilings = Object.freeze({ ...FASTA_PREVIEW_LIMITS, previewLimit: 100 });
const whitespace = /\s/u;

export class FastaPreviewError extends Error {
  constructor(code, line = 0) {
    super(`${code} (line ${line})`);
    this.name = 'FastaPreviewError';
    this.code = code;
    this.line = line;
  }
}

/**
 * push(string) accepts sequential decoded text chunks; finish() returns a frozen
 * summary. The caller owns streaming UTF-8 decoding and any file-byte limit.
 * Counts/limits use UTF-16 code units, making text-chunk boundaries irrelevant.
 * A header starts with > in column 1; its ID is the first whitespace-delimited
 * token after > (leading whitespace is allowed). Blank lines are ignored.
 * Sequence length counts non-whitespace units, without alphabet validation,
 * normalization, sequence hashing, sequence deduplication or storing sequences.
 * Options may lower limits, but cannot raise the documented hard ceilings.
 * Retained state is bounded by the ID budget, preview limit and one header ID;
 * no whole line or sequence is buffered. Caller-owned input chunks are not kept.
 */
// onRecord metadata is provisional until finish succeeds; callback is synchronous.
export function createFastaPreviewParser(options = {}, callbacks = {}) {
  if (!callbacks || typeof callbacks !== 'object' || Array.isArray(callbacks)
      || ![Object.prototype, null].includes(Object.getPrototypeOf(callbacks))
      || Reflect.ownKeys(callbacks).some(key => key !== 'onRecord')) {
    throw new FastaPreviewError('INVALID_CALLBACKS');
  }
  const descriptor = Object.getOwnPropertyDescriptor(callbacks, 'onRecord');
  const onRecord = descriptor?.value;
  if (descriptor && (!Object.hasOwn(descriptor, 'value') || typeof onRecord !== 'function'
      || Object.prototype.toString.call(onRecord) !== '[object Function]')) {
    throw new FastaPreviewError('INVALID_CALLBACKS');
  }
  if (!options || typeof options !== 'object' || Array.isArray(options)) {
    throw new FastaPreviewError('INVALID_OPTIONS');
  }
  const limits = { ...FASTA_PREVIEW_LIMITS };
  for (const key of Reflect.ownKeys(options)) {
    if (!Object.hasOwn(limits, key)) throw new FastaPreviewError('INVALID_OPTIONS');
    const value = options[key];
    if (!Number.isSafeInteger(value) || value < (key === 'previewLimit' ? 0 : 1)
        || value > ceilings[key]) throw new FastaPreviewError('INVALID_OPTIONS');
    limits[key] = value;
  }

  const ids = new Set();
  const preview = [];
  let line = 1, lineLength = 0, totalCharacters = 0, totalIdCharacters = 0;
  let recordCount = 0, totalSequenceLength = 0, sequenceLength = 0;
  let headerLine = 0, id = '', headerPhase = 'leading';
  let hasRecord = false, inHeader = false, atStart = true, skipLF = false;
  let failure, result, inCallback = false;

  function reject(code, atLine = line) {
    failure = new FastaPreviewError(code, atLine);
    throw failure;
  }
  function endHeader() {
    if (!id) reject('EMPTY_ID', headerLine);
    if (ids.has(id)) reject('DUPLICATE_ID', headerLine);
    if (totalIdCharacters + id.length > limits.maxTotalIdCharacters) {
      reject('ID_BUDGET_LIMIT', headerLine);
    }
    ids.add(id);
    totalIdCharacters += id.length;
    inHeader = false;
  }
  function endRecord() {
    if (!hasRecord) return;
    if (!sequenceLength) reject('EMPTY_SEQUENCE', headerLine);
    recordCount++;
    totalSequenceLength += sequenceLength;
    if (preview.length < limits.previewLimit) {
      preview.push(Object.freeze({ id, length: sequenceLength }));
    }
    if (onRecord) {
      inCallback = true;
      try {
        const returned = onRecord({ id, length: sequenceLength });
        if (returned && typeof returned.then === 'function') {
          Promise.resolve(returned).catch(() => {});
          reject('CALLBACK_FAILED', headerLine);
        }
      } catch {
        reject('CALLBACK_FAILED', headerLine);
      } finally {
        inCallback = false;
      }
      if (failure) throw failure;
    }
  }
  function endLine() {
    if (inHeader) endHeader();
    line++;
    lineLength = 0;
  }

  return Object.freeze({
    push(chunk) {
      if (failure) throw failure;
      if (inCallback) reject('REENTRANT_CALL');
      if (result) throw new FastaPreviewError('PARSER_FINISHED', line);
      if (typeof chunk !== 'string') reject('INVALID_CHUNK');
      for (let i = 0; i < chunk.length; i++) {
        const char = chunk[i];
        if (++totalCharacters > limits.maxTotalCharacters) reject('TOTAL_CHARACTER_LIMIT');
        if (atStart) {
          atStart = false;
          if (char === '\uFEFF') continue;
        }
        if (skipLF) {
          skipLF = false;
          if (char === '\n') continue;
        }
        if (char === '\r' || char === '\n') {
          endLine();
          skipLF = char === '\r';
          continue;
        }
        if (++lineLength > limits.maxLineLength) reject('LINE_LENGTH_LIMIT');
        if (lineLength === 1 && char === '>') {
          endRecord();
          if (recordCount >= limits.maxRecords) reject('RECORD_LIMIT');
          hasRecord = true;
          inHeader = true;
          headerLine = line;
          id = '';
          headerPhase = 'leading';
          sequenceLength = 0;
        } else if (inHeader) {
          if (whitespace.test(char)) {
            if (headerPhase === 'token') headerPhase = 'description';
          } else if (headerPhase !== 'description') {
            headerPhase = 'token';
            if (id.length >= limits.maxIdLength) reject('ID_LENGTH_LIMIT');
            id += char;
          }
        } else if (!whitespace.test(char)) {
          if (!hasRecord) reject('SEQUENCE_BEFORE_HEADER');
          if (++sequenceLength > limits.maxSequenceLength) reject('SEQUENCE_LENGTH_LIMIT');
        }
      }
    },
    finish() {
      if (failure) throw failure;
      if (inCallback) reject('REENTRANT_CALL');
      if (result) return result;
      if (inHeader) endHeader();
      endRecord();
      if (!recordCount) reject('NO_RECORDS');
      result = Object.freeze({
        recordCount,
        preview: Object.freeze(preview),
        totalSequenceLength,
        emptySequenceCount: 0,
        duplicateIdCount: 0,
      });
      ids.clear();
      return result;
    },
  });
}
