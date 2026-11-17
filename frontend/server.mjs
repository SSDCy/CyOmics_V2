import http from 'node:http';
import { stat, open } from 'node:fs/promises';
import { constants } from 'node:fs';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHealthBridge } from './health-bridge.mjs';
import { createAuthStatusBridge } from './auth-status-bridge.mjs';
import { createAuthRegisterRuntime } from './auth-register-runtime.mjs';
import { createAuthLoginRuntime } from './auth-login-runtime.mjs';

const privateReadAssets = Object.freeze({
  '/__private-test/project-read': Object.freeze({file: new URL('./private-project-read-check.html', import.meta.url),
    type: 'text/html; charset=utf-8', bytes: 1297, sha256: '36df94979449e5d7f923afc27bea57d16ec654158bd3728f0a001bbac38d4cb4'}),
  '/__private-test/project-read.js': Object.freeze({file: new URL('./private-project-read-check.js', import.meta.url),
    type: 'text/javascript; charset=utf-8', bytes: 9379, sha256: '2a151b6a15e67c818e28217bc470daf2ba30d664b3e6c79adb685e5311cbe5a0'}),
});
const privateReadHeaders = Object.freeze({
  'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff',
  'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
});
async function privateReadCheck(req, res, enabled) {
  if (typeof req.url !== 'string' || !(req.url === '/__private-test' || req.url.startsWith('/__private-test/'))) return false;
  const finish = (status, body, type = 'text/plain; charset=utf-8') => {
    res.writeHead(status, {...privateReadHeaders, 'Content-Type': type, 'Content-Length': Buffer.byteLength(body)});
    res.end(req.method === 'HEAD' ? undefined : body);
    return true;
  };
  const asset = Object.hasOwn(privateReadAssets, req.url) ? privateReadAssets[req.url] : null;
  const hosts = req.rawHeaders.filter((_, i) => i % 2 === 0 && req.rawHeaders[i].toLowerCase() === 'host').length;
  if (enabled !== true || !asset || !['GET', 'HEAD'].includes(req.method) || hosts !== 1 ||
      req.headers.host !== '127.0.0.1:4206' ||
      (req.headers.origin !== undefined && req.headers.origin !== 'http://127.0.0.1:4206') ||
      (req.headers['sec-fetch-site'] !== undefined && !['same-origin', 'none'].includes(req.headers['sec-fetch-site']))) {
    return finish(404, 'Not found');
  }
  try {
    const file = await open(asset.file, constants.O_RDONLY | constants.O_NOFOLLOW);
    let body;
    try {
      const info = await file.stat();
      if (!info.isFile() || info.size !== asset.bytes) throw new Error('Private asset mismatch');
      const buffer = Buffer.alloc(asset.bytes + 1);
      let used = 0;
      while (used < buffer.length) {
        const {bytesRead} = await file.read(buffer, used, buffer.length - used, used);
        if (bytesRead === 0) break;
        used += bytesRead;
      }
      body = buffer.subarray(0, used);
      if (used !== asset.bytes || createHash('sha256').update(body).digest('hex') !== asset.sha256) throw new Error('Private asset mismatch');
    } finally { await file.close(); }
    return finish(200, body, asset.type);
  } catch {
    return finish(503, 'Private check unavailable');
  }
}

export function createPreviewServer({ fetchImpl = globalThis.fetch,
  registerEnabled = process.env.CYOMICS_REGISTER_LIVE_ENABLED === '1', registerOptions = {},
  registerDiagnosticsEnabled = process.env.CYOMICS_REGISTER_DIAGNOSTICS_ENABLED === '1', registerDiagnosticSink,
  loginEnabled = process.env.CYOMICS_LOGIN_ENABLED === '1', loginOptions = {},
  projectsEnabled = false, projectOptions = {},
  privateProjectReadCheckEnabled = false, uploadsEnabled = false, uploadOptions = {},
  importsEnabled = false, importOptions = {},
} = {}) {
  let worker, stamp = 0;
  const healthBridge = createHealthBridge(fetchImpl);
  const authStatusBridge = createAuthStatusBridge({fetchImpl});
  const registration = createAuthRegisterRuntime({ ...registerOptions, fetchImpl, enabled: registerEnabled,
    diagnosticsEnabled: registerDiagnosticsEnabled,
    diagnosticsSink: registerDiagnosticSink ?? (event => console.info(JSON.stringify(event))),
  });
  const login = createAuthLoginRuntime({ ...loginOptions, fetchImpl, enabled: loginEnabled, projectsEnabled, uploadsEnabled, uploadRequest: uploadOptions.requestImpl,
    projectRequest: projectOptions.requestImpl, projectNow: projectOptions.monotonicNow,
    importsEnabled, importRequest: importOptions.requestImpl });
  const server = http.createServer(async (req, res) => {
    try {
      if (await privateReadCheck(req, res, privateProjectReadCheckEnabled)) return;
      if (await login(req, res)) return;
      if (await registration(req, res)) return;
      if (await healthBridge(req, res)) return;
      if (await authStatusBridge(req, res)) return;
      const next = (await stat('dist/server/index.js')).mtimeMs;
      if (next !== stamp) {
        worker = (await import('./dist/server/index.js?v=' + next)).default;
        stamp = next;
      }
      const port = res.socket.localPort;
      const request = new Request(`http://127.0.0.1:${port}${req.url}`, {
        method: req.method, headers: req.headers,
        ...(!['GET', 'HEAD'].includes(req.method) ? { body: req, duplex: 'half' } : {}),
      });
      const response = await worker.fetch(request, {});
      res.writeHead(response.status, Object.fromEntries(response.headers));
      res.end(Buffer.from(await response.arrayBuffer()));
    } catch {
      res.writeHead(500);
      res.end('Preview error');
    }
  });
  server.once('close', () => { registration.dispose(); login.dispose(); });
  return server;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const value = process.env.PORT ?? '4173';
  const port = Number(value);
  if (!/^\d+$/.test(value) || !Number.isInteger(port) || port < 1 || port > 65535) {
    console.error('Invalid preview PORT');
    process.exitCode = 1;
  } else {
    const server = createPreviewServer();
    server.on('error', error => {
      console.error(`Preview failed to listen: ${error.code || 'UNKNOWN'}`);
      process.exitCode = 1;
    });
    server.listen(port, '127.0.0.1', () => console.log(`Local: http://127.0.0.1:${port}`));
  }
}
