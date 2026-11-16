import assert from 'node:assert/strict';
import { readFile, stat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { test } from 'node:test';

const { TXT_PREVIEW_INPUT: txtPath, FASTA_PREVIEW_INPUT: fastaPath,
  PLAYWRIGHT_MODULE: playwrightModule, CHROMIUM_EXECUTABLE: executablePath,
  PREVIEW_URL: baseURL } = process.env;
if (![txtPath, fastaPath, playwrightModule, executablePath, baseURL].every(Boolean)) {
  throw new Error('Explicit input paths, browser module/executable and PREVIEW_URL required');
}
if (baseURL !== 'http://127.0.0.1:4180') throw new Error('Only private loopback 4180 is allowed');
const { chromium } = createRequire(import.meta.url)(playwrightModule);
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const inputs = [
  [txtPath, 6186652, '301bc95ff440a4ea70912fb5ede26aa54cb15f2cb06a9efeed6e55fd749f95fb'],
  [fastaPath, 17195782, '8cb4a7994a80b6fecef0310e288c4763261026989b73a4e467d3310aad5daba5'],
];
// Static assertion messages intentionally avoid logging raw values on failure.
const same = (actual, expected, message) => assert.ok(JSON.stringify(actual) === JSON.stringify(expected), message);

test('real TXT quantification selection and one real FASTA browser regression', async t => {
  const before = await Promise.all(inputs.map(async ([path, size, digest]) => {
    const metadata = await stat(path, { bigint: true });
    const bytes = await readFile(path);
    assert.equal(bytes.length, size);
    assert.equal(sha(bytes), digest);
    return { metadata, bytes };
  }));
  // Independent raw TAB extraction: do not import the production TXT parser.
  const lines = before[0].bytes.toString('utf8').replace(/^\uFEFF/, '').split(/\r\n|\n|\r/);
  if (lines.at(-1) === '') lines.pop();
  const headers = lines.shift().split('\t'), rows = lines.map(line => line.split('\t'));
  assert.equal(headers.length, 182);
  assert.equal(rows.length, 2252);
  assert.ok(rows.every(row => row.length === 182), 'Independent TAB extraction must have 182 cells in every record');
  const idIndex = headers.indexOf('Protein IDs');
  assert.ok(idIndex >= 0);
  const firstTen = rows.slice(0, 10);
  const indices = kind => headers.flatMap((name, i) => name.startsWith(kind + ' ') ? [i] : []);
  assert.equal(indices('Intensity').length, 21);
  assert.equal(indices('LFQ intensity').length, 21);
  const multiIds = firstTen.filter(row => row[idIndex].includes(';')).length;
  const quantValues = firstTen.flatMap(row => [...indices('Intensity'), ...indices('LFQ intensity')].map(i => row[i]));
  const rawZeros = quantValues.filter(value => value === '0').length;
  const rawQuantBlanks = quantValues.filter(value => value === '').length;
  const fullBlanks = firstTen.flat().filter(value => value === '').length;
  assert.ok(multiIds > 0, 'Real first-ten preview must exercise multi-ID fields');
  assert.ok(rawZeros > 0 && fullBlanks > 0, 'Real preview must exercise zero and empty strings');
  console.log(`REAL_REFERENCE rows=2252; columns=182; types=21+21; preview=10; multiID_rows=${multiIds}; quantZeros=${rawZeros}; quantBlanks=${rawQuantBlanks}; fullBlanks=${fullBlanks}`);
  let browser;
  try {
    browser = await chromium.launch({ headless: true, executablePath });
    const context = await browser.newContext();
    const page = await context.newPage();
    const errors = [], requests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => requests.push({ url: request.url(), method: request.method() }));
    await page.route('**/*', route => route.request().url().startsWith(baseURL + '/')
      ? route.continue() : route.fulfill({ status: 200, contentType: 'text/css', body: '' }));
    await page.addInitScript(() => {
      window.__audit = { text: 0, stream: 0, writes: 0 };
      const text = File.prototype.text, stream = File.prototype.stream;
      const setItem = Storage.prototype.setItem;
      File.prototype.text = function () { window.__audit.text++; return text.call(this); };
      File.prototype.stream = function () { window.__audit.stream++; return stream.call(this); };
      Storage.prototype.setItem = function (...args) { window.__audit.writes++; return setItem.apply(this, args); };
    });
    await page.goto(baseURL + '/#/demo/tool/import');
    const input = page.locator('#file-input');
    const select = page.locator('[data-quant-select]');
    const quant = page.locator('[data-quant-preview]');
    const rawTable = page.locator('[aria-label="proteinGroups local table preview"]');
    const ready = async () => {
      await page.waitForFunction(() => document.querySelector('#file-result')?.textContent.includes('TXT 校验通过'));
      await page.waitForFunction(() => document.querySelector('#file-input')?.value === '');
    };
    const uploadDerived = async (derivedHeaders, derivedRows) => {
      const buffer = Buffer.from([derivedHeaders, ...derivedRows].map(row => row.join('\t')).join('\n'));
      await input.setInputFiles({ name: 'real-derived.txt', mimeType: 'text/plain', buffer });
      await ready();
    };
    const readMatrix = table => table.locator('tbody tr').evaluateAll(trs => trs.map(tr => [...tr.querySelectorAll('td')].map(td => td.textContent)));
    const stats = async () => {
      for (const [key, value] of [['rows', '2252'], ['columns', '182'], ['intensity', '21'], ['lfq', '21']]) {
        assert.equal(await page.locator(`[data-preview-count="${key}"]`).textContent(), value);
      }
    };
    const checkKind = async kind => {
      const cols = indices(kind);
      assert.equal(await quant.locator('[data-quant-type]').textContent(), kind);
      assert.equal(await quant.locator('[data-quant-count]').textContent(), '21');
      const table = quant.locator('[aria-label="Selected quantification preview"]');
      same(await table.locator('thead th').allTextContents(), ['Protein IDs', ...cols.map(i => headers[i])], 'Selected headers differ from raw TAB extraction');
      same(await readMatrix(table), firstTen.map(row => [row[idIndex], ...cols.map(i => row[i])]), 'Selected values/IDs differ from raw TAB strings');
      const samples = quant.locator('[aria-label="Original sample columns"]');
      same(await readMatrix(samples), cols.map(i => [kind, headers[i], headers[i].slice(kind.length + 1)]), 'Selected sample names differ from raw headers');
      assert.equal(await table.locator('tbody tr').count(), 10);
      await stats();
    };

    await t.test('default Intensity and repeated local switches preserve every raw string and DOM identity', async () => {
      await input.setInputFiles(txtPath);
      await ready();
      assert.equal(await select.inputValue(), 'intensity');
      await checkKind('Intensity');
      same(await rawTable.locator('thead th').allTextContents(), headers, 'Full raw headers changed');
      same(await readMatrix(rawTable), firstTen, 'Full original 182-column strings changed');
      await page.evaluate(() => {
        window.__kept = { input: document.querySelector('#file-input'), raw: document.querySelector('[aria-label="proteinGroups local table preview"]'),
          section: document.querySelector('[aria-label="Real file local preview"]') };
      });
      const requestCount = requests.length;
      for (const value of ['lfq', 'intensity', 'lfq', 'intensity', 'lfq', 'intensity', 'lfq', 'intensity']) {
        await select.selectOption(value);
        await checkKind(value === 'lfq' ? 'LFQ intensity' : 'Intensity');
        assert.ok(await page.evaluate(() => window.__kept.input === document.querySelector('#file-input')
          && window.__kept.raw === document.querySelector('[aria-label="proteinGroups local table preview"]')
          && window.__kept.section === document.querySelector('[aria-label="Real file local preview"]')));
        same(await readMatrix(rawTable), firstTen, 'Switch changed the full original table');
      }
      same(await page.evaluate(() => window.__audit), { text: 1, stream: 0, writes: 0 }, 'Switch reread input or wrote storage');
      assert.equal(requests.length, requestCount);
      console.log('UI REAL_TXT Intensity=21x10; LFQ=21x10; exact raw headers/strings/IDs match; full preview=182x10; switches=8; textReads=1; storageWrites=0; newRequests=0; DOM retained');
    });
    await t.test('missing type stays empty and never substitutes the other quantification type', async () => {
      for (const missing of ['Intensity', 'LFQ intensity']) {
        const derivedHeaders = headers.map(name => name.startsWith(missing + ' ') ? 'Unavailable ' + name : name);
        await uploadDerived(derivedHeaders, rows);
        assert.equal(await select.inputValue(), 'intensity');
        await select.selectOption(missing === 'Intensity' ? 'intensity' : 'lfq');
        assert.equal(await quant.locator('[data-quant-type]').textContent(), missing);
        assert.equal(await quant.locator('[data-quant-count]').textContent(), '0');
        assert.ok((await quant.textContent()).includes(`未找到 ${missing} 样本列`));
        assert.equal(await quant.locator('table').count(), 0);
        await select.selectOption(missing === 'Intensity' ? 'lfq' : 'intensity');
        assert.equal(await quant.locator('[data-quant-count]').textContent(), '21');
        assert.equal(await rawTable.locator('tbody tr').count(), 10);
      }
      console.log('UI MISSING_TYPE both directions: explicit missing message/count=0; no fallback; other type remains available');
    });
    await t.test('real-row derivatives preserve empty/zero strings and escape headers, IDs and values', async () => {
      const derivedHeaders = [...headers], derivedRows = rows.map(row => [...row]);
      derivedRows[0][idIndex] = '<img/src=x>;' + derivedRows[0][idIndex];
      for (const kind of ['Intensity', 'LFQ intensity']) {
        const cols = indices(kind);
        derivedHeaders[cols[0]] = kind + ' <img/src=x>';
        derivedRows[0][cols[0]] = '';
        derivedRows[0][cols[1]] = '0';
        derivedRows[0][cols[2]] = '<img/src=x>';
      }
      await uploadDerived(derivedHeaders, derivedRows);
      for (const value of ['intensity', 'lfq']) {
        await select.selectOption(value);
        const table = quant.locator('[aria-label="Selected quantification preview"]');
        const cells = (await readMatrix(table))[0];
        same(cells.slice(1, 4), ['', '0', '<img/src=x>'], 'Empty/zero/text values were transformed');
        assert.ok(cells[0] === derivedRows[0][idIndex], 'Multi-ID string was truncated');
        assert.equal(await quant.locator('img').count(), 0);
        assert.ok((await table.locator('thead th').allTextContents())[1].includes('<img/src=x>'));
      }
      console.log('UI STRING_SAFETY real-row in-memory derivatives: empty/0 preserved for both types; header/ID/value escaped; multi-ID retained');
    });
    await t.test('selection resets without persistence; real FASTA still uses its independent preview', async () => {
      await select.selectOption('lfq');
      await page.reload();
      await input.waitFor();
      assert.equal(await select.count(), 0);
      await input.setInputFiles(txtPath);
      await ready();
      assert.equal(await select.inputValue(), 'intensity');
      await checkKind('Intensity');
      await input.setInputFiles(fastaPath);
      await page.waitForFunction(() => document.querySelector('[data-fasta-count="records"]')?.textContent === '38387');
      await page.waitForFunction(() => document.querySelector('#file-input')?.value === '');
      assert.equal(await page.locator('[data-fasta-count="length"]').textContent(), '16367314');
      assert.equal(await page.locator('[aria-label="FASTA ID and length preview"] tbody tr').count(), 10);
      assert.equal(await select.count(), 0);
      assert.equal(await rawTable.count(), 0);
      same(await page.evaluate(() => window.__audit), { text: 1, stream: 1, writes: 0 }, 'TXT/FASTA read or storage behavior changed');
      console.log('UI REGRESSION reload clears TXT; reselect defaults Intensity; real FASTA=38387/16367314/10; metadata/selection storageWrites=0');
    });
    assert.deepEqual(errors, []);
    assert.ok(requests.every(request => request.method === 'GET' && !new URL(request.url).pathname.startsWith('/api/')));
    console.log('UI ERRORS=0; API_REQUESTS=0; no upload requests');
  } finally {
    if (browser) await browser.close();
    for (let i = 0; i < inputs.length; i++) {
      const [path, , digest] = inputs[i];
      const after = await stat(path, { bigint: true });
      assert.equal(sha(await readFile(path)), digest);
      assert.equal(after.size, before[i].metadata.size);
      assert.equal(after.mtimeNs, before[i].metadata.mtimeNs);
      console.log(`UNCHANGED ${path}; SHA256=${digest}; size/mtime unchanged`);
    }
  }
});
