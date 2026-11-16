// Local-only parser. No persistence, network calls, normalization or analysis.
// Record callbacks are provisional until this synchronous parse returns.
export function parseProteinGroups(text, previewLimit = 10, callbacks = {}) {
  const callbackError = (code, record = 0) => Object.assign(
    new Error(`proteinGroups TXT：${code}（记录 ${record}）。`), { code });
  if (!callbacks || typeof callbacks !== 'object' || Array.isArray(callbacks)
      || ![Object.prototype, null].includes(Object.getPrototypeOf(callbacks))
      || Reflect.ownKeys(callbacks).some(key => key !== 'onRecord' && key !== 'onQuantificationRow' && key !== 'onQualityFlagRow')) {
    throw callbackError('INVALID_CALLBACKS');
  }
  const descriptor = Object.getOwnPropertyDescriptor(callbacks, 'onRecord');
  const onRecord = descriptor?.value;
  if (descriptor && (!Object.hasOwn(descriptor, 'value') || typeof onRecord !== 'function'
      || Object.prototype.toString.call(onRecord) !== '[object Function]')) {
    throw callbackError('INVALID_CALLBACKS');
  }
  const quantDescriptor = Object.getOwnPropertyDescriptor(callbacks, 'onQuantificationRow');
  const onQuantificationRow = quantDescriptor?.value;
  if (quantDescriptor && (!Object.hasOwn(quantDescriptor, 'value') || typeof onQuantificationRow !== 'function'
      || Object.prototype.toString.call(onQuantificationRow) !== '[object Function]'
      || Object.getPrototypeOf(onQuantificationRow) !== Function.prototype)) {
    throw callbackError('INVALID_CALLBACKS');
  }
  const qualityDescriptor = Object.getOwnPropertyDescriptor(callbacks, 'onQualityFlagRow');
  const onQualityFlagRow = qualityDescriptor?.value;
  if (qualityDescriptor && (!Object.hasOwn(qualityDescriptor, 'value') || typeof onQualityFlagRow !== 'function'
      || Object.prototype.toString.call(onQualityFlagRow) !== '[object Function]'
      || Object.getPrototypeOf(onQualityFlagRow) !== Function.prototype)) {
    throw callbackError('INVALID_CALLBACKS');
  }
  if (typeof text !== 'string' || !text.length) throw new Error('proteinGroups TXT：文件为空。');
  if (text.includes('\0')) throw new Error('proteinGroups TXT：含二进制空字节，无法按 TAB 表格解析。');
  text = text.replace(/^\uFEFF/, '');
  let headers, proteinIdsIndex, rowCount = 0;
  let quantificationIndices;
  let qualityColumns;
  const previewRows = [];
  function accept(row) {
    if (!headers) {
      if (row.length < 2) throw new Error('proteinGroups TXT：表头必须使用 TAB 制表符分隔。');
      if (row.filter(name => name === 'Protein IDs').length !== 1) {
        throw new Error('proteinGroups TXT：必须包含且仅包含一个 Protein IDs 字段。');
      }
      headers = row;
      proteinIdsIndex = headers.indexOf('Protein IDs');
      if (onQuantificationRow) quantificationIndices = headers.flatMap((name, index) =>
        name.startsWith('Intensity ') || name.startsWith('LFQ intensity ') ? [index] : []);
      if (onQualityFlagRow) qualityColumns = ['Reverse', 'Potential contaminant', 'Only identified by site'].map(name => {
        const indices = Object.freeze(headers.flatMap((header, index) => header === name ? [index] : []));
        return Object.freeze({name, indices, available: indices.length === 1});
      });
      return;
    }
    if (row.length !== headers.length) {
      throw new Error(`proteinGroups TXT：第 ${rowCount + 1} 条数据记录列数为 ${row.length}，应为 ${headers.length}；预览已拒绝。`);
    }
    rowCount++;
    if (previewRows.length < previewLimit) previewRows.push(row);
    if (onRecord) {
      const proteinIds = row[proteinIdsIndex];
      try {
        // Fresh metadata: mutations cannot alter row cells or default preview.
        const returned = onRecord({ recordNumber: rowCount, proteinIds,
          tokens: proteinIds.split(';').map(token => token.trim()).filter(Boolean) });
        if (returned && typeof returned.then === 'function') {
          Promise.resolve(returned).catch(() => {});
          throw callbackError('CALLBACK_FAILED', rowCount);
        }
      } catch {
        throw callbackError('CALLBACK_FAILED', rowCount);
      }
    }
    if (onQuantificationRow) {
      try {
        // Fresh, frozen cells never expose a mutable parser row or header.
        const cells = Object.freeze(quantificationIndices.map(columnIndex =>
          Object.freeze({ columnIndex, value: row[columnIndex] })));
        const returned = onQuantificationRow(Object.freeze({ recordNumber: rowCount, cells }));
        if (returned && typeof returned.then === 'function') {
          Promise.resolve(returned).catch(() => {});
          throw callbackError('CALLBACK_FAILED', rowCount);
        }
      } catch {
        throw callbackError('CALLBACK_FAILED', rowCount);
      }
    }
    if (onQualityFlagRow) {
      try {
        const flags = Object.freeze(qualityColumns.map(column => Object.freeze({
          ...column, value: column.available ? row[column.indices[0]] : null,
        })));
        const returned = onQualityFlagRow(Object.freeze({recordNumber: rowCount, flags}));
        if (returned && typeof returned.then === 'function') {
          Promise.resolve(returned).catch(() => {});
          throw callbackError('CALLBACK_FAILED', rowCount);
        }
      } catch {
        throw callbackError('CALLBACK_FAILED', rowCount);
      }
    }
  }
  let row = [], value = '', quoted = false, closedQuote = false, recordStarted = false;
  function endField() { row.push(value); value = ''; closedQuote = false; }
  function endRecord() { endField(); accept(row); row = []; recordStarted = false; }
  // TSV quoted fields support escaped quotes, TAB and line breaks in a field.
  // Cell strings, zeros, empty cells and multi-ID fields are never cleaned.
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (quoted) {
      if (char === '"') {
        if (text[i + 1] === '"') { value += '"'; i++; }
        else { quoted = false; closedQuote = true; }
      } else value += char;
      continue;
    }
    if (char === '\t') { endField(); recordStarted = true; }
    else if (char === '\r' || char === '\n') {
      endRecord();
      if (char === '\r' && text[i + 1] === '\n') i++;
    } else if (closedQuote) {
      throw new Error('proteinGroups TXT：引号字段结束后存在非法字符，预览已拒绝。');
    } else if (char === '"' && value === '') {
      quoted = true; recordStarted = true;
    } else { value += char; recordStarted = true; }
  }
  if (quoted) throw new Error('proteinGroups TXT：存在未闭合的引号字段，预览已拒绝。');
  if (recordStarted || row.length || value.length || closedQuote) endRecord();
  if (!headers || rowCount === 0) throw new Error('proteinGroups TXT：需要表头和至少一条数据记录。');
  const sampleColumns = prefix => headers.flatMap((columnName, index) =>
    columnName.startsWith(prefix + ' ')
      ? [{ index, columnName, sampleName: columnName.slice(prefix.length + 1) }]
      : []);
  return { headers, rowCount, columnCount: headers.length, previewRows,
    intensityColumns: sampleColumns('Intensity'), lfqColumns: sampleColumns('LFQ intensity') };
}
