const endpoint = 'http://127.0.0.1:4186/api/health';
const semver = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*)?(?:\+[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*)?$/;

// Test injection changes transport only; the production target is never configurable.
export function createHealthBridge(fetchImpl = globalThis.fetch) {
  return async (req, res) => {
    let pathname;
    try { pathname = new URL(req.url, 'http://127.0.0.1').pathname; }
    catch { return false; }
    if (pathname !== '/api/health') return false;
    const reply = (status, body, headers = {}) => {
      if (res.destroyed) return;
      res.writeHead(status, {
        'Content-Type': 'application/json; charset=utf-8',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', ...headers,
      });
      res.end(JSON.stringify(body));
    };
    if (req.method !== 'GET') {
      reply(405, { error: { code: 'method_not_allowed' } }, { Allow: 'GET' });
      return true;
    }
    // Do not let query/absolute-form variants fall through to the prototype Worker.
    if (req.url !== '/api/health') {
      reply(404, { error: { code: 'not_found' } });
      return true;
    }
    const controller = new AbortController();
    let timer, reader, upstream;
    const deadline = new Promise((_, reject) => {
      timer = setTimeout(() => {
        controller.abort();
        reject(new Error('timeout'));
      }, 2000);
    });
    const readStatus = async () => {
      upstream = await fetchImpl(endpoint, {
        method: 'GET', redirect: 'error', signal: controller.signal,
        cache: 'no-store', credentials: 'omit', headers: { Accept: 'application/json' },
      });
      if (upstream.status !== 200 || !/^application\/json(?:\s*;|$)/i.test(upstream.headers.get('content-type') || '')) {
        throw new Error('invalid response');
      }
      reader = upstream.body.getReader();
      const chunks = [];
      let bytes = 0;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        bytes += value.byteLength;
        if (bytes > 4096) throw new Error('response limit');
        chunks.push(value);
      }
      const result = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if (result?.status !== 'ok' || result.service !== 'Cy_Omics_v2.0-backend'
          || typeof result.version !== 'string' || result.version.length > 64 || !semver.test(result.version)) {
        throw new Error('invalid health');
      }
      return { status: 'ok', service: 'Cy_Omics_v2.0-backend', version: result.version };
    };
    try {
      reply(200, await Promise.race([readStatus(), deadline]));
    } catch {
      reply(503, { error: { code: 'backend_unavailable' } });
    } finally {
      clearTimeout(timer);
      controller.abort();
      if (reader) void reader.cancel().catch(() => {});
      else if (upstream?.body) void upstream.body.cancel().catch(() => {});
    }
    return true;
  };
}
