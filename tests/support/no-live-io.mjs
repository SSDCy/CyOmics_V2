// Preload for synthetic tests only. No live network, child services or private data.
import fs from 'node:fs';
import net from 'node:net';
import tls from 'node:tls';
import http from 'node:http';
import https from 'node:https';
import dns from 'node:dns';
import dgram from 'node:dgram';
import child from 'node:child_process';
import {relative,resolve,isAbsolute,sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import {syncBuiltinESMExports} from 'node:module';
const root=fileURLToPath(new URL('../../',import.meta.url));
const counts={network:0,listen:0,subprocess:0,privateRead:0,write:0};
const deny=key=>{counts[key]++;throw Error('Offline guard blocked '+key);};
const safe=value=>{let p;try{p=resolve(value instanceof URL?fileURLToPath(value):typeof value==='string'?value:Buffer.isBuffer(value)?value.toString():'');}catch{deny('privateRead');}const r=relative(root,p);if(!r||r==='..'||r.startsWith('..'+sep)||isAbsolute(r)||r.split(/[\\/]/).some(s=>/^(?:\.env(?:\..*)?|runtime|auth-dev|data|datasets|uploads|secrets|audit|releases)$/i.test(s)))deny('privateRead');};
globalThis.fetch=()=>deny('network');
for(const n of ['connect','createConnection'])net[n]=()=>deny('network');net.Socket.prototype.connect=()=>deny('network');net.Server.prototype.listen=()=>deny('listen');tls.connect=()=>deny('network');dgram.createSocket=()=>deny('network');
for(const api of [http,https])for(const n of ['request','get'])api[n]=()=>deny('network');
for(const api of [dns,dns.promises])for(const n of Object.keys(api))if(/^(lookup|resolve|reverse)/.test(n)&&typeof api[n]==='function')api[n]=()=>deny('network');
for(const n of ['spawn','spawnSync','exec','execSync','execFile','execFileSync','fork'])child[n]=()=>deny('subprocess');process.kill=()=>deny('subprocess');
for(const api of [fs,fs.promises]){
 for(const n of ['readFile','readFileSync','stat','statSync','lstat','lstatSync','access','accessSync','realpath','realpathSync','open','openSync','createReadStream'])if(typeof api[n]==='function'){const fn=api[n];api[n]=function(file,...args){safe(file);if((n==='open'||n==='openSync')&&args[0]!=='r'&&args[0]!==fs.constants.O_RDONLY)deny('write');return fn.call(this,file,...args);};}
 for(const n of ['writeFile','writeFileSync','appendFile','appendFileSync','createWriteStream','mkdir','mkdirSync','cp','cpSync','copyFile','copyFileSync','rm','rmSync','rmdir','rmdirSync','rename','renameSync','unlink','unlinkSync','truncate','truncateSync','chmod','chmodSync','chown','chownSync','link','linkSync','symlink','symlinkSync','utimes','utimesSync'])if(typeof api[n]==='function')api[n]=()=>deny('write');
}
syncBuiltinESMExports();
process.once('exit',()=>{console.error('OFFLINE_GUARD '+JSON.stringify(counts));if(Object.values(counts).some(Boolean))process.exitCode=1;});
