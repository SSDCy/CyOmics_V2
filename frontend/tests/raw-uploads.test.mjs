import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {harness} from './raw-upload-harness.mjs';
import {uploadRoute,uploadHeaders,createRawUploadBridge,MAX_UPLOAD_BYTES} from '../raw-uploads-bridge.mjs';
const pid='11111111-1111-4111-8111-111111111111',rid='66666666-6666-4666-8666-666666666666',fid='77777777-7777-4777-8777-777777777777';
const base='/api/v1/projects/'+pid,save=base+'/raw-uploads';
const raw=Buffer.from('\ufeffID\tValue\r\nx\t1\r\n\r\n'),sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const file={file_id:fid,project_id:pid,request_id:rid,filename:'  raw.TXT',format:'proteinGroups_txt',bytes:raw.length,sha256:sha(raw),status:'stored',createdAt:'2026-11-16T00:00:00.000000Z'};
const heads=(bytes=raw)=>({'content-type':'application/octet-stream','idempotency-key':rid,'x-cyomics-filename':Buffer.from(file.filename).toString('base64url'),'x-cyomics-file-format':'proteinGroups_txt','x-cyomics-file-bytes':String(bytes.length),'x-cyomics-file-sha256':sha(bytes)});
const tick=()=>new Promise(setImmediate);

test('only exact POST raw and GET content receive long profiles; metadata and aliases never do',()=>{
  assert.equal(uploadRoute(save,'POST').long,true);assert.equal(uploadRoute(base+'/files/'+fid+'/content','GET').long,true);
  assert.equal(uploadRoute(base+'/files','GET').long,false);assert.equal(uploadRoute(save+'/'+rid,'GET').long,false);
  for(const [path,method]of [[save,'GET'],[save+'?x=1','POST'],[save+'/','POST'],[save.replace('111','%31%31%31'),'POST'],[base+'/files/'+fid+'/content','POST'],['http://evil'+save,'POST']])assert.throws(()=>uploadRoute(path,method));
});
test('strict canonical metadata rejects duplicated encodings, paths, controls, format and sizes',()=>{
  const valid=heads();assert.equal(uploadHeaders(k=>valid[k]).filename,'  raw.TXT');
  for(const name of ['../x.txt','x\\y.txt','x\n.txt','x\u0080.txt','x.csv','x'.repeat(252)+'.txt']){
    const value={...valid,'x-cyomics-filename':Buffer.from(name).toString('base64url')};assert.throws(()=>uploadHeaders(k=>value[k]));
  }
  for(const change of [{'x-cyomics-filename':'YQ=='},{'x-cyomics-file-format':'fasta'},{'x-cyomics-file-bytes':'01'},{'x-cyomics-file-bytes':String(MAX_UPLOAD_BYTES+1)},{'content-encoding':'gzip'},{'content-type':'text/plain'},{'idempotency-key':'BAD'}]){const value={...valid,...change};assert.throws(()=>uploadHeaders(k=>value[k]));}
});
test('explicit gateoff returns unavailable without Auth, body forwarding or new API work',async t=>{
  const h=harness(t,{uploadsEnabled:false});const r=await h.send(save,{method:'POST',wire:raw,headers:heads()});assert.equal(r.status,503);assert.equal(h.auth.length,0);assert.equal(h.bridges.length,0);
});
test('BOM/CRLF raw body and fixed metadata cross BFF unchanged with server-originated long timing hints',async t=>{
  const h=harness(t),s=await h.signed();h.reply(c=>c.deliver({file},201));
  const r=await h.send(save,{...s,method:'POST',wire:raw,headers:heads()});assert.equal(r.status,201);assert.deepEqual(r.body,{file});
  const c=h.bridges[0];assert.deepEqual(c.body,raw);assert.equal(c.request.hostname,'127.0.0.1');assert.equal(c.request.port,57430);assert.equal(c.request.headers['X-Cyomics-Remaining-Ms'],'55000');assert.equal(c.request.headers.Authorization,'Bearer ACCESS_A');assert.equal(c.request.headers['Content-Length'],String(raw.length));
  for(const k of ['Cookie','Origin','X-Cyomics-Account-Context','X-Owner-Id'])assert.equal(c.request.headers[k],undefined);
  for(const secret of ['ACCESS_A','REFRESH_SECRET','SYNTHETIC_ANON'])assert.ok(!JSON.stringify(r).includes(secret));
});
test('list and request receipt use short budgets and strict bounded public schemas',async t=>{
  const h=harness(t),s=await h.signed();h.reply(c=>c.deliver(c.request.path.endsWith('/files')?{items:[file],nextCursor:null}:{file}));
  for(const path of [base+'/files',save+'/'+rid]){const r=await h.send(path,s);assert.equal(r.status,200);assert.equal(h.bridges.at(-1).request.headers['X-Cyomics-Remaining-Ms'],'12000');}
});
test('download is explicit authenticated fetch, exact binary SHA and attachment headers, never JSON/text conversion',async t=>{
  const h=harness(t),s=await h.signed();h.reply(c=>c.deliver(raw,200,['Content-Type','application/octet-stream','Content-Length',String(raw.length),'Content-Disposition',"attachment; filename=\"raw.txt\"; filename*=UTF-8''raw.txt",'X-Cyomics-File-Sha256',sha(raw),'Cache-Control','no-store','X-Content-Type-Options','nosniff','Referrer-Policy','no-referrer']));
  const r=await h.send(base+'/files/'+fid+'/content',s);assert.equal(r.status,200);assert.deepEqual(r.body,raw);assert.equal(h.bridges[0].request.headers['X-Cyomics-Remaining-Ms'],'55000');assert.equal(r.headers['cache-control'],'no-store');
});
test('invalid identity/view, browser auth or timing hints, cross-origin and duplicate nonce do zero upstream work',async t=>{
  const h=harness(t);assert.equal((await h.send(base+'/files')).status,401);const s=await h.signed();
  for(const view of [undefined,'bad','Z'.repeat(22)])assert.equal((await h.send(base+'/files',{cookie:s.cookie,view})).status,409);
  for(const extra of [{authorization:'Bearer CLIENT'},{'x-owner-id':pid},{'x-cyomics-remaining-ms':'55000'},{origin:'http://evil'},{'sec-fetch-site':'same-site'}])assert.equal((await h.send(base+'/files',{...s,headers:extra})).status,403);
  const headers={host:'127.0.0.1:4206',origin:'http://127.0.0.1:4206','content-type':'application/json','x-cyomics-request':'1',cookie:s.cookie,'x-cyomics-account-context':s.view};const duplicate=[...Object.entries(headers).flat(),'X-Cyomics-Account-Context',s.view];assert.equal((await h.send(base+'/files',{...s,headers,rawHeaders:duplicate})).status,409);assert.equal(h.bridges.length,0);
});
test('raw EOF, expected SHA, compression, metadata GET bodies and size are enforced before forwarding',async t=>{
  const h=harness(t),s=await h.signed();
  for(const change of [{'x-cyomics-file-bytes':String(raw.length+1)},{'x-cyomics-file-sha256':'0'.repeat(64)},{'content-encoding':'gzip'}])assert.ok([400,415].includes((await h.send(save,{...s,method:'POST',wire:raw,headers:{...heads(),...change}})).status));
  assert.equal((await h.send(base+'/files',{...s,wire:raw})).status,400);assert.equal(h.bridges.length,0);
});
test('201/replay200 and fixed 409/413/429 errors preserve exact meanings; sent unknown never retries',async t=>{
  const h=harness(t),s=await h.signed();
  for(const [status,value]of [[201,{file}],[200,{file}],[409,{error:{code:'upload_request_conflict'}}],[413,{error:{code:'upload_quota_exceeded'}}],[429,{error:{code:'upload_busy'}}]]){h.reply(c=>c.deliver(value,status));const r=await h.send(save,{...s,method:'POST',wire:raw,headers:heads()});assert.equal(r.status,status);assert.deepEqual(r.body,value);}
  h.reply(c=>c.out.emit('error',Error('private transport failure')));const n=h.bridges.length;const r=await h.send(save,{...s,method:'POST',wire:raw,headers:heads()});assert.equal(r.status,504);assert.equal(r.body.error.code,'upload_result_unknown');await tick();assert.equal(h.bridges.length,n+1);
});
test('replacement login and logout fence pending raw responses without forwarding old file or clearing new account',async t=>{
  const h=harness(t),a=await h.signed();let late,entered;const started=new Promise(r=>entered=r);h.reply(c=>{late=c;entered();});
  const pending=h.start(save,{...a,method:'POST',wire:raw,headers:heads()});await started;const b=await h.signed('B',a.cookie);const stale=await pending.promise;assert.notEqual(stale.status,201);assert.equal(stale.headers['set-cookie'],undefined);assert.ok(!stale.wire.includes(file.filename));late.deliver({file},201);await tick();assert.equal((await h.send('/api/v1/me',b)).body.user.email,'b@example.invalid');
  const next=h.start(base+'/files',b);await tick();await h.send('/api/auth/logout',b);const cancelled=await next.promise;assert.notEqual(cancelled.status,200);
});
test('long deadlines consume body time, expired session overrides long budget, per-subject pending save is busy',async t=>{
  const h=harness(t),s=await h.signed();let entered,late;const start=new Promise(r=>entered=r);h.reply(c=>{late=c;entered();});
  const pending=h.start(save,{...s,method:'POST',wire:raw,headers:heads()});await start;
  const second=await h.send(save,{...s,method:'POST',wire:raw,headers:heads()});assert.equal(second.status,429);
  h.advance(55001);late.deliver({file},201);const timeout=await pending.promise;assert.equal(timeout.status,504);assert.equal(timeout.body.error.code,'upload_result_unknown');
  h.advance(900000);assert.equal((await h.send(base+'/files',s)).status,401);
});
test('bad upstream fields, owner leaks, duplicate JSON, cookies and binary corruption fail closed',async t=>{
  const h=harness(t),s=await h.signed();
  for(const value of [{items:[{...file,owner_id:pid}],nextCursor:null},{items:Array(5).fill(file),nextCursor:null},{items:[{...file,createdAt:'2026-02-31T00:00:00.000000Z'}],nextCursor:null},Buffer.from('{"items":[],"items":[],"nextCursor":null}')]){h.reply(c=>c.deliver(value));assert.equal((await h.send(base+'/files',s)).status,503);}
  h.reply(c=>c.deliver({items:[],nextCursor:null},200,['Content-Type','application/json','Set-Cookie','SECRET=1']));assert.equal((await h.send(base+'/files',s)).status,503);
  h.reply(c=>c.deliver(raw,200,['Content-Type','application/octet-stream','Content-Length',String(raw.length),'Content-Disposition','attachment; filename="x.txt"','X-Cyomics-File-Sha256','0'.repeat(64),'Cache-Control','no-store','X-Content-Type-Options','nosniff','Referrer-Policy','no-referrer']));assert.equal((await h.send(base+'/files/'+fid+'/content',s)).status,503);
});
test('per-process long work cap is two and per-subject writing attempts stop at ten',async t=>{
  const h=harness(t),a=await h.signed(),b=await h.signed('B');const held=[];h.reply(c=>held.push(c));
  const one=h.start(base+'/files/'+fid+'/content',a),two=h.start(base+'/files/'+fid+'/content',b);await tick();assert.equal(held.length,2);
  const third=await h.send(save,{...a,method:'POST',wire:raw,headers:heads()});assert.equal(third.status,429);assert.equal(third.body.error.code,'upload_busy');assert.equal(h.bridges.length,2);
  h.expire();await Promise.all([one.promise,two.promise]);
  const fresh=harness(t),s=await fresh.signed();fresh.reply(c=>c.deliver({file},200));
  for(let i=0;i<10;i++)assert.equal((await fresh.send(save,{...s,method:'POST',wire:raw,headers:heads()})).status,200);
  const limit=await fresh.send(save,{...s,method:'POST',wire:raw,headers:heads()});assert.equal(limit.status,429);assert.equal(limit.body.error.code,'upload_rate_limited');assert.equal(fresh.bridges.length,10);
});
test('body consumption uses the original long deadline and only remaining time is handed to API',async t=>{
  const h=harness(t),s=await h.signed();h.reply(c=>c.deliver({file},201));
  const pending=h.start(save,{...s,method:'POST',headers:heads(),sendBody:false});await tick();h.advance(54000);pending.req.emit('data',raw);pending.req.emit('end');
  assert.equal((await pending.promise).status,201);assert.equal(h.bridges[0].request.headers['X-Cyomics-Remaining-Ms'],'1000');
  const late=h.start(save,{...s,method:'POST',headers:heads(),sendBody:false});await tick();h.advance(55001);late.req.emit('data',raw);late.req.emit('end');
  const result=await late.promise;assert.equal(result.status,503);assert.equal(result.body.error.code,'uploads_unavailable');assert.equal(h.bridges.length,1);
});

test('capacity exact25MiB metadata is accepted and25MiB+1 is rejected before forwarding',async t=>{
  assert.equal(MAX_UPLOAD_BYTES,26214400);
  const valid={...heads(),'x-cyomics-file-bytes':'26214400'};assert.equal(uploadHeaders(k=>valid[k]).bytes,26214400);
  assert.throws(()=>uploadHeaders(k=>({...valid,'x-cyomics-file-bytes':'26214401'})[k]),e=>e.status===413&&e.code==='upload_too_large');
  const h=harness(t),s=await h.signed();const r=await h.send(save,{...s,method:'POST',wire:raw,headers:{...heads(),'x-cyomics-file-bytes':'26214401'}});
  assert.equal(r.status,413);assert.equal(r.body.error.code,'upload_too_large');assert.equal(h.bridges.length,0);
});

test('capacity synthetic18.11MiB and25MiB raw uploads cross BFF unchanged with verified receipts',async t=>{
  for(const size of[18988424,26214400]){
    const h=harness(t),s=await h.signed();
    const bytes=Buffer.alloc(size,0x61),stored={...file,filename:'capacity-synthetic.txt',bytes:size,sha256:sha(bytes)};
    h.reply(c=>c.deliver({file:stored},201));const r=await h.send(save,{...s,method:'POST',wire:bytes,headers:{...heads(bytes),'x-cyomics-filename':Buffer.from(stored.filename).toString('base64url')}});
    assert.equal(r.status,201);assert.deepEqual(r.body,{file:stored});assert.ok(h.bridges.at(-1).body.equals(bytes));assert.equal(h.bridges.at(-1).request.headers['Content-Length'],String(size));assert.equal(h.bridges.length,1);
  }
});

test('capacity synthetic25MiB binary response still validates SHA and rejects a25MiB+1 declared response',async t=>{
  const h=harness(t),s=await h.signed(),bytes=Buffer.alloc(26214400,0x62),digest=sha(bytes);
  const headers=size=>['Content-Type','application/octet-stream','Content-Length',String(size),'Content-Disposition','attachment; filename="capacity-synthetic.txt"','X-Cyomics-File-Sha256',digest,'Cache-Control','no-store','X-Content-Type-Options','nosniff','Referrer-Policy','no-referrer'];
  h.reply(c=>c.deliver(bytes,200,headers(bytes.length)));const r=await h.send(base+'/files/'+fid+'/content',s);assert.equal(r.status,200);assert.equal(r.body.length,bytes.length);assert.equal(sha(r.body),digest);
  h.reply(c=>c.deliver(raw,200,headers(26214401)));assert.equal((await h.send(base+'/files/'+fid+'/content',s)).status,503);assert.equal(h.bridges.length,2);
});
