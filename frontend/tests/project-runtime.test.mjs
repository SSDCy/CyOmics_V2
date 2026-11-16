import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { readFile } from 'node:fs/promises';
import { createAuthLoginRuntime } from '../auth-login-runtime.mjs';
import { createPreviewServer } from '../server.mjs';

const origin='http://127.0.0.1:4206', path='/api/v1/projects', me='/api/v1/me', login='/api/auth/login', logout='/api/auth/logout';
const users={A:{id:'11111111-1111-4111-8111-111111111111',email:'a@example.invalid',email_confirmed_at:'2026-11-15T00:00:00Z',aud:'authenticated'},
  B:{id:'22222222-2222-4222-8222-222222222222',email:'b@example.invalid',email_confirmed_at:'2026-11-15T00:00:00Z',aud:'authenticated'}};
const project={project_id:users.A.id,name:'Private A',description:'',species:'',visibility:'private',createdAt:'2026-11-16T00:00:00.000000Z',updatedAt:'2026-11-16T00:00:00.000000Z'};
const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};
function harness(t, options={}) {
  let wall=Date.now(),mono=1000,authMode='ok',reply=call=>call.deliver({items:[project],nextCursor:null});
  const auth=[],bridges=[],timers=new Map();
  const requestImpl=(request,callback)=>{
    const out=new EventEmitter(),call={request,out};bridges.push(call);
    out.destroy=()=>{out.destroyed=true;};
    call.deliver=(value,status=200,headers=['Content-Type','application/json'])=>{
      const response=new EventEmitter();response.statusCode=status;response.rawHeaders=headers;response.destroy=()=>{response.destroyed=true;};call.response=response;callback(response);
      response.emit('data',Buffer.isBuffer(value)?value:Buffer.from(JSON.stringify(value)));response.emit('end');
    };
    out.end=body=>{call.body=body;queueMicrotask(()=>reply(call));};return out;
  };
  const runtime=createAuthLoginRuntime({enabled:true,projectsEnabled:true,now:()=>wall,projectNow:()=>mono,
    keyProvider:async()=> 'SYNTHETIC_ANON',
    fetchImpl:async(url,opts)=>{
      auth.push({url,opts});
      if(authMode==='bad')return Response.json({code:'invalid_credentials'},{status:400});
      if(authMode==='offline')return Response.json({code:'unavailable'},{status:503});
      const who=opts.method==='POST'?(JSON.parse(opts.body).email===users.B.email?'B':'A'):(opts.headers.Authorization==='Bearer ACCESS_B'?'B':'A');
      return Response.json(opts.method==='POST'?{access_token:'ACCESS_'+who,refresh_token:'REFRESH_SECRET',token_type:'bearer',expires_in:3600,expires_at:Math.floor(wall/1000)+3600,user:users[who]}:users[who]);
    },projectRequest:requestImpl,...options,
    setTimer:(fn,ms)=>{const token={fn,ms};timers.set(token,token);return token;},clearTimer:token=>timers.delete(token)});
  t.after(()=>runtime.dispose());
  function start(url, {cookie,view,method=url===login||url===logout?'POST':'GET',wire,value,headers={},rawHeaders,sendBody=true}={}) {
    const req=new EventEmitter();req.url=url;req.method=method;req.pause=()=>{};req.socket={remoteAddress:'127.0.0.1',destroyed:false};
    req.headers={host:'127.0.0.1:4206',origin,'x-cyomics-request':'1','content-type':'application/json',...(cookie?{cookie}:{}),...(view?{'x-cyomics-account-context':view}:{}),...headers};
    req.rawHeaders=rawHeaders??Object.entries(req.headers).filter(([,v])=>v!==undefined).flat();
    const res=new EventEmitter(),result={headers:{}};res.socket={destroyed:false};
    res.setHeader=(key,val)=>{result.headers[key.toLowerCase()]=val;};res.getHeader=key=>result.headers[key.toLowerCase()];
    res.writeHead=(status,h)=>{result.status=status;for(const [k,v] of Object.entries(h))res.setHeader(k,v);};
    res.end=wire=>{res.writableEnded=true;result.wire=wire;result.body=wire===undefined?undefined:JSON.parse(wire);};
    const promise=runtime(req,res).then(owned=>({...result,owned}));
    if(sendBody)queueMicrotask(()=>{const data=wire??(method==='POST'?JSON.stringify(value??(url===logout?{}:{email:users.A.email,password:'SYNTHETIC_PASSWORD'})):undefined);
      if(data!==undefined)req.emit('data',Buffer.isBuffer(data)?data:Buffer.from(data));req.emit('end');});
    return {req,res,result,promise};
  }
  const send=(url,opts)=>start(url,opts).promise;
  async function signed(who='A',oldCookie) {
    const bootstrap=oldCookie?undefined:await send(me);const browser=oldCookie?.split(';')[0]??bootstrap.headers['set-cookie'][0].split(';')[0];
    const r=await send(login,{cookie:oldCookie??browser,value:{email:users[who].email,password:'SYNTHETIC_PASSWORD'}});assert.equal(r.status,200);assert.equal(r.headers['x-cyomics-account-context'],undefined);
    const cookie=browser+'; '+r.headers['set-cookie'].find(c=>c.startsWith('cyomics_session=')).split(';')[0];
    const current=await send(me,{cookie});assert.equal(current.status,200);const view=current.headers['x-cyomics-account-context'];assert.match(view,/^[A-Za-z0-9_-]{22}$/);
    return {cookie,view,current,login:r};
  }
  return {runtime,start,send,signed,auth,bridges,timers,now:()=>wall,mono:()=>mono,
    reply:fn=>{reply=fn;},mode:value=>{authMode=value;},advance:(ms,which='both')=>{if(which!=='mono')wall+=ms;if(which!=='wall')mono+=ms;},
    expire:()=>{for(const timer of [...timers.values()])timer.fn();}};
}

test('official SDK synthetic login -> opaque cookies -> authoritative me -> actual BFF; public nonce only on me and no token forwarding to browser',async t=>{
  const h=harness(t),s=await h.signed();assert.equal(h.auth.length,3);
  const result=await h.send(path,{...s,headers:{authorization:'Bearer CLIENT_TOKEN','x-user-id':users.B.id,'x-cyomics-remaining-ms':'1','x-cyomics-deadline-unix-ms':'9999999999999',forwarded:'evil'}});
  assert.equal(result.status,200);assert.deepEqual(result.body,{items:[project],nextCursor:null});assert.equal(h.auth.length,3);
  const call=h.bridges[0];assert.equal(call.request.hostname,'127.0.0.1');assert.equal(call.request.port,57430);
  assert.equal(call.request.headers.Authorization,'Bearer ACCESS_A');assert.equal(call.request.headers['X-Cyomics-Remaining-Ms'],'12000');
  for(const name of ['Cookie','Origin','X-Cyomics-Account-Context','X-User-Id','Forwarded'])assert.equal(call.request.headers[name],undefined);
  assert.equal(result.headers['x-cyomics-account-context'],undefined);assert.equal(result.headers['set-cookie'],undefined);
  const again=await h.send(me,s);assert.equal(again.headers['x-cyomics-account-context'],s.view);assert.deepEqual(again.body,s.current.body);
  for(const value of [result,s.current,s.login])for(const secret of ['ACCESS_A','REFRESH_SECRET','SYNTHETIC_ANON','SYNTHETIC_PASSWORD'])assert.ok(!JSON.stringify(value).includes(secret));
});
test('missing/expired binding precedes nonce; valid session missing, duplicate, bad or stale nonce does zero Auth and bridge work',async t=>{
  const h=harness(t);assert.equal((await h.send(path)).status,401);const s=await h.signed(),before=h.auth.length;
  for(const view of [undefined,'bad','X'.repeat(22)])assert.equal((await h.send(path,{cookie:s.cookie,view})).status,409);
  const headers={host:'127.0.0.1:4206',origin,cookie:s.cookie,'x-cyomics-request':'1','content-type':'application/json','x-cyomics-account-context':s.view};
  const rawHeaders=[...Object.entries(headers).flat(),'X-Cyomics-Account-Context',s.view];
  assert.equal((await h.send(path,{...s,headers,rawHeaders})).status,409);assert.equal(h.bridges.length,0);assert.equal(h.auth.length,before);
  h.advance(900001);assert.equal((await h.send(path,{cookie:s.cookie})).status,401);
});
test('successful B replacement fences pending A401, never clears B cookies or emits A body; only Bme emits stable Bnonce',async t=>{
  const h=harness(t),a=await h.signed(),entered=deferred();let late;
  h.reply(call=>{late=call;entered.resolve();});const pending=h.start(path,a);await entered.promise;
  const b=await h.signed('B',a.cookie);assert.notEqual(b.view,a.view);const stale=await pending.promise;
  assert.notEqual(stale.status,200);assert.equal(stale.headers['set-cookie'],undefined);assert.ok(!stale.wire.includes('Private A'));
  late.deliver({error:{code:'unauthenticated'}},401);await new Promise(setImmediate);
  const current=await h.send(me,b);assert.equal(current.status,200);assert.equal(current.body.user.email,users.B.email);assert.equal(current.headers['x-cyomics-account-context'],b.view);
  const count=h.bridges.length;assert.equal((await h.send(path,{cookie:b.cookie,view:a.view})).status,409);assert.equal(h.bridges.length,count);
});
test('failed login and temporary me503/project503 preserve otherwise valid session and nonce; current API401 revokes exactly local snapshot',async t=>{
  const h=harness(t),s=await h.signed();h.mode('bad');assert.equal((await h.send(login,{cookie:s.cookie})).status,401);
  h.mode('offline');const down=await h.send(me,s);assert.equal(down.status,503);assert.equal(down.headers['x-cyomics-account-context'],undefined);
  h.mode('ok');assert.equal((await h.send(me,s)).headers['x-cyomics-account-context'],s.view);
  h.reply(call=>call.deliver({error:{code:'projects_unavailable'}},503));assert.equal((await h.send(path,s)).status,503);
  assert.equal((await h.send(me,s)).status,200);
  h.reply(call=>call.deliver({error:{code:'unauthenticated'}},401));assert.equal((await h.send(path,s)).status,401);
  const before=h.auth.length,anon=await h.send(me,s);assert.equal(anon.status,401);assert.equal(anon.headers['x-cyomics-account-context'],undefined);assert.equal(h.auth.length,before);
});
test('origin/method/raw path/query, duplicate JSON, invalid UTF8, 8192 bound and GET body fail locally without bridge',async t=>{
  const h=harness(t),s=await h.signed();
  for(const [url,options,status] of [
    [path,{method:'POST',value:{name:'x'},headers:{origin:'http://evil.invalid'}},403],
    [path,{headers:{host:'evil.invalid'}},403],[path,{headers:{'sec-fetch-site':'cross-site'}},403],
    [path,{method:'DELETE'},405],['/api/v1/projects/%31'+users.A.id.slice(1),{},404],
    ['//api/v1/projects',{},404],['/%61pi/v1/projects',{},404],
    [path+'?limit=1&limit=2',{},400],[path,{wire:'GET_BODY'},400],
    [path,{method:'POST',wire:'{"name":"a","name":"b"}'},400],
    [path,{method:'POST',wire:'{"name":"a","owner_id":"x"}'},400],
    [path,{method:'POST',wire:Buffer.from([255])},400],
    [path,{method:'POST',wire:' '.repeat(8193)},413],
    [path,{method:'POST',headers:{'content-type':'text/plain'},wire:'{}'},415]]) {
    const result=await h.send(url,{...s,...options});assert.equal(result.status,status,url);
  }
  assert.equal(h.bridges.length,0);
  h.reply(call=>call.deliver({project},201));const ok=await h.send(path,{...s,method:'POST',value:{name:'  Private A  '}});assert.equal(ok.status,201);
  assert.deepEqual(JSON.parse(h.bridges[0].body),{name:'Private A',description:'',species:''});
});
test('12s includes slow input and native response; server budget headers inherit 1s, unsent expiry503 and sent uncertain504',async t=>{
  for(const sent of [false,true]) {
    const h=harness(t),s=await h.signed(),entered=deferred();h.reply(()=>entered.resolve());
    const pending=h.start(path,{...s,method:'POST',sendBody:false});
    if(sent){h.advance(11000);pending.req.emit('data',Buffer.from('{"name":"x"}'));pending.req.emit('end');await entered.promise;
      assert.equal(h.bridges[0].request.headers['X-Cyomics-Remaining-Ms'],'1000');assert.equal(Number(h.bridges[0].request.headers['X-Cyomics-Deadline-Unix-Ms']),h.now()+1000);h.advance(1001);
    }else h.advance(12001);
    h.expire();const result=await pending.promise;assert.equal(result.status,sent?504:503);assert.equal(h.bridges.length,sent?1:0);
    if(!sent){pending.req.emit('error',Error('late aborted request'));pending.req.emit('close');assert.equal(pending.req.listenerCount('error'),0);}
    if(sent)assert.equal(h.bridges[0].out.destroyed,true);
  }
  for(const which of ['wall','mono']) {
    const h=harness(t),s=await h.signed(),entered=deferred();h.reply(()=>entered.resolve());const pending=h.start(path,s);await entered.promise;
    h.advance(12001,which);h.bridges[0].deliver({items:[project],nextCursor:null});assert.equal((await pending.promise).status,503);
  }
});
test('logout, expiry sweep, dispose and disconnect abort/destroy pending operations, and late data never resurrects them',async t=>{
  for(const mode of ['logout','expiry','dispose','disconnect']) {
    const h=harness(t),s=await h.signed(),entered=deferred();let late;h.reply(call=>{late=call;entered.resolve();});
    const pending=h.start(path,s);await entered.promise;
    if(mode==='logout')assert.equal((await h.send(logout,s)).status,200);
    else if(mode==='expiry'){h.advance(900001);await h.send(me);}
    else if(mode==='dispose')h.runtime.dispose();else{pending.req.aborted=true;pending.req.emit('aborted');}
    const result=await pending.promise;assert.notEqual(result.status,200);assert.equal(late.out.destroyed,true);
    late.deliver({items:[project],nextCursor:null});await new Promise(setImmediate);assert.ok(!JSON.stringify(pending.result).includes('Private A'));
  }
});
test('project ownership remains default-off even via loginOptions and arbitrary env; factory uses no listening and CLI has no project gate',async t=>{
  const h=harness(t,{projectsEnabled:false}),s=await h.signed();assert.equal((await h.send(path,s)).status,503);assert.equal(h.bridges.length,0);
  for(const url of [path,path+'/'+users.A.id,path+'?limit=1'])assert.equal((await h.send(url)).body.error.code,'projects_unavailable');
  const server=createPreviewServer({loginEnabled:true,loginOptions:{projectsEnabled:true,keyProvider:()=>assert.fail('no key')},projectOptions:{requestImpl:()=>assert.fail('no bridge')}});
  assert.equal(server.listening,false);const req=new EventEmitter();req.url=path;req.method='GET';req.headers={};
  const res=new EventEmitter(),done=deferred();let status;res.writeHead=s=>{status=s;};res.end=()=>done.resolve();
  server.emit('request',req,res);await done.promise;assert.equal(status,503);server.emit('close');
  const source=await readFile(new URL('../server.mjs',import.meta.url),'utf8');assert.match(source,/projectsEnabled = false/);assert.doesNotMatch(source,/process\.env\.[A-Z_]*PROJECT/);
});
