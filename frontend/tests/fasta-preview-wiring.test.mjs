import assert from 'node:assert/strict';
import { readFile, stat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { test } from 'node:test';

const fastaPath = process.env.FASTA_PREVIEW_INPUT;
const txtPath = process.env.TXT_PREVIEW_INPUT;
const playwrightModule = process.env.PLAYWRIGHT_MODULE;
const executablePath = process.env.CHROMIUM_EXECUTABLE;
const baseURL = process.env.PREVIEW_URL;
if (![fastaPath, txtPath, playwrightModule, executablePath, baseURL].every(Boolean)) {
  throw new Error('Explicit FASTA_PREVIEW_INPUT, TXT_PREVIEW_INPUT, PLAYWRIGHT_MODULE, CHROMIUM_EXECUTABLE and PREVIEW_URL required');
}
if (baseURL !== 'http://127.0.0.1:4180') throw new Error('Only the authorized loopback preview is allowed');
const { chromium } = createRequire(import.meta.url)(playwrightModule);
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const expected = [
  [fastaPath, '8cb4a7994a80b6fecef0310e288c4763261026989b73a4e467d3310aad5daba5', 17195782],
  [txtPath, '301bc95ff440a4ea70912fb5ede26aa54cb15f2cb06a9efeed6e55fd749f95fb', 6186652],
];

test('real browser FASTA wiring and TXT regression on private 4180 preview', async t => {
  const before = await Promise.all(expected.map(async ([path, digest, size]) => {
    const metadata = await stat(path, { bigint: true });
    const bytes = await readFile(path);
    assert.equal(bytes.length, size);
    assert.equal(hash(bytes), digest);
    console.log(`INPUT ${path}; size=${size}; sha256=${digest}`);
    return { metadata, bytes };
  }));
  const fasta = before[0].bytes, txt = before[1].bytes;
  const firstNewline = fasta.indexOf(10), nextHeader = fasta.indexOf('\n>', firstNewline);
  assert.ok(nextHeader > firstNewline);
  const firstRecord = fasta.subarray(0, nextHeader + 1);
  const sequence = fasta.subarray(firstNewline + 1, nextHeader);
  let browser;
  try {
    browser = await chromium.launch({ headless: true, executablePath });
    const context = await browser.newContext(); // Fresh, unauthenticated profile.
    const page = await context.newPage();
    const errors = [], requests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => requests.push({ url: request.url(), method: request.method() }));
    await page.route('**/*', route => route.request().url().startsWith(baseURL + '/')
      ? route.continue() : route.fulfill({ status: 200, contentType: 'text/css', body: '' }));
    await page.addInitScript(() => {
      window.__audit = { stream: 0, fastaText: 0, cancel: 0, release: 0, digest: 0, writes: [] };
      const setItem = Storage.prototype.setItem;
      Storage.prototype.setItem = function (key, value) {
        window.__audit.writes.push(key);
        return setItem.call(this, key, value);
      };
      if (crypto.subtle) {
        const digest = crypto.subtle.digest.bind(crypto.subtle);
        crypto.subtle.digest = (...args) => { window.__audit.digest++; return digest(...args); };
      }
      const stream = File.prototype.stream, text = File.prototype.text;
      File.prototype.text = async function () {
        if (/\.(fa|fasta)$/i.test(this.name)) window.__audit.fastaText++;
        const value = await text.call(this);
        if (window.__holdText) {
          window.__holdText = false;
          await new Promise(resolve => { window.__releaseText = resolve; window.__textHeld = true; });
        }
        return value;
      };
      File.prototype.stream = function () {
        window.__audit.stream++;
        const reader = stream.call(this).getReader();
        const mode = window.__holdStream;
        window.__holdStream = null;
        let first = true;
        return { getReader: () => ({
          async read() {
            const item = await reader.read();
            if (first && mode) {
              first = false;
              await new Promise(resolve => { window.__releaseRead = resolve; window.__readHeld = true; });
              window.__readHeld = false;
              if (mode === 'error') throw new TypeError('delayed test read failure');
            }
            return item;
          },
          cancel() { window.__audit.cancel++; return reader.cancel(); },
          releaseLock() { window.__audit.release++; reader.releaseLock(); },
        }) };
      };
    });
    const status = page.locator('#file-result');
    const preview = page.locator('#protein-groups-preview');
    const input = page.locator('#file-input');
    const upload = buffer => input.setInputFiles({ name: 'real-derived.fasta', mimeType: 'text/plain', buffer });
    const waitText = async text => {
      await page.waitForFunction(text => document.querySelector('#file-result')?.textContent.includes(text), text);
      await page.waitForFunction(() => document.querySelector('#file-input')?.value === '');
    };
    const checkFasta = async () => {
      await waitText('FASTA 结构检查通过');
      assert.equal(await page.locator('[data-fasta-count="records"]').innerText(), '38387');
      assert.equal(await page.locator('[data-fasta-count="length"]').innerText(), '16367314');
      assert.equal(await page.locator('[aria-label="FASTA ID and length preview"] tbody tr').count(), 10);
      assert.equal(await page.locator('[aria-label="FASTA ID and length preview"] tbody tr').first().locator('td').first().innerText(), 'Pt_Chr0100001');
    };
    const checkTxt = async () => {
      await waitText('TXT 校验通过');
      for (const [key, value] of [['rows', '2252'], ['columns', '182'], ['intensity', '21'], ['lfq', '21']]) {
        assert.equal(await page.locator(`[data-preview-count="${key}"]`).innerText(), value);
      }
      assert.equal(await page.locator('[aria-label="proteinGroups local table preview"] tbody tr').count(), 10);
    };
    const hold = async mode => {
      await page.evaluate(mode => { window.__readHeld = false; window.__holdStream = mode; }, mode);
      await input.setInputFiles(fastaPath);
      await page.waitForFunction(() => window.__readHeld);
    };
    const release = () => page.evaluate(() => window.__releaseRead());
    const noPreview = async () => assert.equal(await preview.locator('section').count(), 0);
    await page.goto(baseURL + '/#/demo/tool/import');
    await input.waitFor();

    await t.test('new parser asset, accurate limits and real FASTA/FA UI counts', async () => {
      const asset = await page.request.get(baseURL + '/fasta-preview.js');
      assert.equal(asset.status(), 200);
      assert.equal(await asset.text(), await readFile(new URL('../fasta-preview.js', import.meta.url), 'utf8'));
      assert.match(await page.locator('#dropzone').innerText(), /FASTA \/ FA ≤ 25 MiB/);
      assert.match(await page.locator('#dropzone').innerText(), /TXT \/ CSV \/ TSV ≤ 10 MiB/);
      await input.setInputFiles(fastaPath);
      await checkFasta();
      assert.match(await preview.innerText(), /仅结构检查，未验证氨基酸字母表/);
      await input.setInputFiles({ name: 'real-derived.fa', mimeType: 'text/plain', buffer: fasta });
      await checkFasta();
      console.log('UI FASTA/FA: records=38387; length=16367314; preview=10; controls cleared');
    });
    await t.test('real TXT regression and invalid TXT clears old success', async () => {
      await input.setInputFiles(txtPath);
      await checkTxt();
      const invalid = Buffer.from(txt);
      Buffer.from('Missing IDs').copy(invalid, 0); // Same-length mutation of real header.
      await input.setInputFiles({ name: 'real-derived.txt', mimeType: 'text/plain', buffer: invalid });
      await waitText('Protein IDs');
      await noPreview();
      console.log('UI TXT: rows=2252; columns=182; samples=21+21; invalid header clears preview');
    });
    await t.test('FASTA parse and strict UTF-8 failures remove prior results', async () => {
      await input.setInputFiles(fastaPath);
      await checkFasta();
      await upload(sequence);
      await waitText('SEQUENCE_BEFORE_HEADER');
      await noPreview();
      await input.setInputFiles(fastaPath);
      await checkFasta();
      const invalidUTF8 = Buffer.from(firstRecord);
      invalidUTF8[1] = 255;
      await upload(invalidUTF8);
      await waitText('UTF-8 解码或读取失败');
      await noPreview();
    });
    await t.test('exact 25 MiB real-derived FASTA accepted; one byte over rejected before reading', async () => {
      const boundary = Buffer.alloc(25 * 1024 * 1024, 10);
      fasta.copy(boundary);
      await upload(boundary);
      await checkFasta();
      const streamsBefore = await page.evaluate(() => window.__audit.stream);
      await upload(Buffer.concat([boundary, Buffer.from('\n')]));
      await waitText('25 MiB 限制');
      await noPreview();
      assert.equal(await page.evaluate(() => window.__audit.stream), streamsBefore);
      const oversizedTxt = Buffer.alloc(10 * 1024 * 1024 + 1, 10);
      txt.copy(oversizedTxt);
      await input.setInputFiles({ name: 'real-derived.txt', mimeType: 'text/plain', buffer: oversizedTxt });
      await waitText('10 MiB 限制');
      await noPreview();
      console.log('UI limits: 26214400-byte FASTA accepted; 26214401 rejected before stream; TXT 10485761 rejected');
    });
    await t.test('new selection cancels old FASTA and rejects stale success/error writes', async () => {
      for (const mode of ['result', 'error']) {
        const cancels = await page.evaluate(() => window.__audit.cancel);
        await hold(mode);
        await input.setInputFiles(txtPath);
        await checkTxt();
        assert.ok(await page.evaluate(() => window.__audit.cancel) > cancels);
        await release();
        await page.waitForTimeout(50);
        await checkTxt();
      }
      await page.evaluate(() => { window.__textHeld = false; window.__holdText = true; });
      await input.setInputFiles(txtPath);
      await page.waitForFunction(() => window.__textHeld);
      await input.setInputFiles(fastaPath);
      await checkFasta();
      await page.evaluate(() => window.__releaseText());
      await page.waitForTimeout(50);
      await checkFasta();
      console.log('UI lifecycle: canceled FASTA stale result/error and stale TXT cannot overwrite current preview');
    });
    await t.test('navigation and redraw cancel active readers; refresh/navigation clear success', async () => {
      await hold('result');
      const cancels = await page.evaluate(() => window.__audit.cancel);
      await page.evaluate(() => { location.hash = '/demo/hub'; });
      await page.waitForFunction(count => window.__audit.cancel > count, cancels);
      await release();
      await page.evaluate(() => { location.hash = '/demo/tool/import'; });
      await input.waitFor();
      await noPreview();
      await hold('error');
      const redrawCancels = await page.evaluate(() => window.__audit.cancel);
      await page.locator('[data-action="lang"]').click();
      await page.waitForFunction(count => window.__audit.cancel > count, redrawCancels);
      await release();
      await noPreview();
      await page.locator('[data-action="lang"]').click(); // Restore Chinese.
      await input.setInputFiles(fastaPath);
      await checkFasta();
      const audit = await page.evaluate(() => window.__audit);
      assert.equal(audit.fastaText, 0);
      assert.equal(audit.digest, 0);
      assert.ok(audit.writes.every(key => key === 'pp-lang'));
      assert.equal(audit.release, audit.stream);
      console.log(`UI isolation: FASTA text()=${audit.fastaText}; digest=${audit.digest}; metadata writes=0; released=${audit.release}/${audit.stream}`);
      await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
      await noPreview();
      assert.equal(await status.innerText(), '');
      await input.setInputFiles(fastaPath);
      await checkFasta();
      await page.goto('about:blank');
      await page.goBack();
      await input.waitFor();
      await noPreview();
      await input.setInputFiles(fastaPath);
      await checkFasta();
      await page.reload();
      await input.waitFor();
      await noPreview();
      await input.setInputFiles(txtPath);
      await checkTxt();
      await page.evaluate(() => { location.hash = '/demo/hub'; });
      await page.waitForFunction(() => !document.querySelector('#file-input'));
      await page.evaluate(() => { location.hash = '/demo/tool/import'; });
      await input.waitFor();
      await noPreview();
      console.log('UI clear: redraw/navigation cancel verified; pagehide, full navigation/back, reload and SPA navigation discard success');
    });
    await t.test('IDs are escaped and CSV/TSV keep their existing demo metadata behavior', async () => {
      const escapedId = Buffer.concat([Buffer.from('><img/src=x>\n'), sequence]);
      await upload(escapedId);
      await waitText('FASTA 结构检查通过');
      assert.equal(await preview.locator('img').count(), 0);
      assert.equal(await preview.locator('tbody tr td').first().innerText(), '<img/src=x>');
      const txtText = txt.toString('utf8');
      const twoLines = txtText.split(/\r?\n/).slice(0, 2).join('\n');
      for (const ext of ['tsv', 'csv']) {
        const buffer = Buffer.from(ext === 'csv' ? twoLines.replaceAll('\t', ',') : twoLines);
        await input.setInputFiles({ name: `real-derived.${ext}`, mimeType: 'text/plain', buffer });
        await waitText('本机校验完成');
        await noPreview();
      }
      const metadata = await page.evaluate(() => JSON.parse(localStorage.getItem('pp-demo-files')));
      assert.equal(metadata.length, 2);
      assert.deepEqual(metadata.map(item => item.type), ['tsv', 'csv']);
      assert.deepEqual(metadata.map(item => item.count), [1, 1]);
      assert.ok(metadata.every(item => item.hashes.length === 0));
    });
    assert.deepEqual(errors, []);
    assert.ok(requests.every(item => item.method === 'GET'));
    assert.ok(requests.every(item => !new URL(item.url).pathname.startsWith('/api/')));
    console.log('UI runtime errors=0; API requests=0; uploads=0');
  } finally {
    if (browser) await browser.close();
    for (let i = 0; i < expected.length; i++) {
      const [path, digest] = expected[i];
      const after = await stat(path, { bigint: true });
      assert.equal(hash(await readFile(path)), digest);
      assert.equal(after.size, before[i].metadata.size);
      assert.equal(after.mtimeNs, before[i].metadata.mtimeNs);
      console.log(`UNCHANGED ${path}; size/mtime/SHA256 match before`);
    }
  }
});
