import {fileURLToPath} from 'node:url';
import {resolve as pathResolve, relative, isAbsolute} from 'node:path';
const root=fileURLToPath(new URL('../../',import.meta.url));
const browserImports=new Set(['/auth-login.js','/private-projects.js','/private-txt-upload.js','/raw-upload-response.mjs']);
export async function resolve(specifier,context,next){
  if(browserImports.has(specifier))return next(new URL('../../frontend'+specifier,import.meta.url).href,context);
  return next(specifier,context);
}
export async function load(url,context,next){
  if(url.startsWith('file:')){const p=fileURLToPath(url),r=relative(root,p);if(r==='..'||r.startsWith('..'+(process.platform==='win32'?'\\':'/'))||isAbsolute(r))throw Error('Offline module outside repository');}
  else if(!url.startsWith('node:'))throw Error('Offline module protocol rejected');
  return next(url,context);
}
