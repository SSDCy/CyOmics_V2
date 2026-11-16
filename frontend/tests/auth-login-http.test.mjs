import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { createPreviewServer } from '../server.mjs';

const email='synthetic@example.com',password=' PASSWORD_SECRET ',access='secret.access.token',refresh='SECRET_REFRESH',anon='SECRET_ANON';
const user={id:'SECRET_USER',email,email_confirmed_at:'2026-11-15T00:00:00Z',aud:'authenticated'};
const auth={status:'authenticated',user:{email,emailVerified:true}};
async function fixture(t,{enabled=true,reply,defaults=false}={}){
  const calls=[],logs=[];let keys=0,expectedOrigin;
  const server=createPreviewServer({registerEnabled:false,loginEnabled:enabled,
    loginOptions:{keyProvider:async()=>{keys++;return anon;},...(defaults?{}:{expectedOrigin:'http://127.0.0.1:4206'})},
    fetchImpl:async(url,options)=>{calls.push({url,options});if(reply)return reply(url,options);
      return Response.json(url.includes('/token?')?{access_token:access,refresh_token:refresh,token_type:'bearer',expires_in:3600,user}:user);}});
  server.listen(0,'127.0.0.1');await once(server,'listening');const port=server.address().port;
  t.after(()=>new Promise(resolve=>{server.close(resolve);server.closeAllConnections();}));
  const send=(path,{method=path==='/api/v1/me'?'GET':'POST',cookie,wire,value,headers={}}={})=>new Promise((resolve,reject)=>{
    const payload=method==='GET'?undefined:wire??JSON.stringify(value??(path==='/api/auth/logout'?{}:{email,password}));
    const req=http.request({host:'127.0.0.1',port,path,method,headers:{host:'127.0.0.1:4206',origin:'http://127.0.0.1:4206','x-cyomics-request':'1',
      ...(payload===undefined?{}:{'content-type':'application/json','content-length':Buffer.byteLength(payload)}),...(cookie?{cookie}:{}),...headers}},res=>{
      const chunks=[];res.on('data',c=>chunks.push(c));res.on('error',reject);res.on('end',()=>{const text=Buffer.concat(chunks).toString();resolve({status:res.statusCode,headers:res.headers,text,body:JSON.parse(text)});});});
    req.on('error',reject);req.end(payload);
  });
  const browser=async()=>{const r=await send('/api/v1/me');assert.equal(r.status,401);return r.headers['set-cookie'][0].split(';')[0];};
  return {server,port,send,browser,calls,keys:()=>keys};
}
const sid=r=>r.headers['set-cookie'].find(c=>c.startsWith('cyomics_session=')).split(';')[0];
test('temporary real HTTP with official SDK mock: bootstrap/login/authoritative me/logout; no tokens in public body, cookies, headers or logs',async t=>{
  const logs=[];t.mock.method(console,'error',(...args)=>logs.push(args));t.mock.method(console,'info',(...args)=>logs.push(args));
  const h=await fixture(t),b=await h.browser();assert.equal(h.calls.length,0);assert.equal(h.keys(),0);
  const r=await h.send('/api/auth/login',{cookie:b});assert.equal(r.status,200);assert.deepEqual(r.body,auth);
  assert.equal(h.calls.length,2);assert.deepEqual(h.calls.map(c=>new URL(c.url).pathname),['/auth/v1/token','/auth/v1/user']);
  const cookie=b+'; '+sid(r),current=await h.send('/api/v1/me',{cookie});assert.equal(current.status,200);assert.deepEqual(current.body,auth);assert.equal(h.calls.length,3);
  for(const response of [r,current]){
    const output=JSON.stringify(response);for(const secret of [access,refresh,password,anon,user.id])assert.ok(!output.includes(secret));
    assert.equal(response.headers['cache-control'],'no-store');assert.equal(response.headers['access-control-allow-origin'],undefined);
    assert.ok(!JSON.stringify(response.headers).match(/diagnostic|debug/i));
  }
  const out=await h.send('/api/auth/logout',{cookie});assert.equal(out.status,200);assert.deepEqual(out.body,{status:'signed_out'});
  const check=await h.send('/api/v1/me',{cookie});assert.equal(check.status,401);assert.equal(h.calls.length,3);assert.deepEqual(logs,[]);
});
test('temporary HTTP default remains exact 4206 and disabled never reads keys or Auth',async t=>{
  const h=await fixture(t,{enabled:false});
  for(const path of ['/api/v1/me','/api/auth/login','/api/auth/logout'])assert.equal((await h.send(path)).status,503);
  assert.equal((await h.send('/api/v1/me',{headers:{host:'127.0.0.1:'+h.port,origin:'http://127.0.0.1:'+h.port}})).status,403);
  assert.equal(h.keys(),0);assert.equal(h.calls.length,0);
});
test('temporary HTTP rejects aliases/CSRF/body ambiguity with zero credential I/O, preserves literal password bytes',async t=>{
  const h=await fixture(t),b=await h.browser();
  for(const path of ['/api/auth/login?x=1','/api/auth/log%69n','//api/v1/me','/api/v1/me/'])assert.equal((await h.send(path)).status,404);
  for(const headers of [{origin:'http://evil.test'},{'x-cyomics-request':'0'},{'content-type':'application/x-www-form-urlencoded'},{cookie:b+'; '+b}])
    assert.ok([403,415].includes((await h.send('/api/auth/login',{cookie:b,headers})).status));
  assert.equal((await h.send('/api/auth/login',{cookie:b,wire:'{"email":"synthetic@example.com","password":"x","password":"y"}'})).status,400);
  assert.equal((await h.send('/api/auth/login',{cookie:b,wire:' '.repeat(4097)})).status,413);
  assert.equal(h.calls.length,0);assert.equal(h.keys(),0);
  assert.equal((await h.send('/api/auth/login',{cookie:b,value:{email,password:' x '}})).status,200);
  assert.equal(JSON.parse(h.calls[0].options.body).password,' x ');
});
test('temporary HTTP does not enumerate missing/wrong/unconfirmed users; handles upstream limits/transport with fixed body and no cookie',async t=>{
  for(const [status,code,expected,publicCode] of [[400,'invalid_credentials',401,'invalid_credentials'],[400,'email_not_confirmed',401,'invalid_credentials'],
    [400,'user_not_found',401,'invalid_credentials'],[429,'over_request_rate_limit',429,'login_rate_limited'],[503,'SECRET',503,'login_unavailable']]){
    const h=await fixture(t,{reply:()=>Response.json({code,message:'SECRET_UPSTREAM',access_token:access},{status})}),b=await h.browser();
    const r=await h.send('/api/auth/login',{cookie:b});assert.equal(r.status,expected);assert.deepEqual(r.body,{error:{code:publicCode}});assert.equal(r.headers['set-cookie'],undefined);
  }
});
test('test-only origin injection serves owned actual loopback Host/Origin without relaxing validation',async t=>{
  // Reserve an owned listener first, then attach the factory request handler for its actual port.
  const transport=http.createServer();transport.listen(0,'127.0.0.1');await once(transport,'listening');
  const port=transport.address().port,expectedOrigin='http://127.0.0.1:'+port;
  const preview=createPreviewServer({registerEnabled:false,loginEnabled:true,loginOptions:{expectedOrigin,keyProvider:async()=>{throw Error('forbidden key');}},
    fetchImpl:async()=>{throw Error('forbidden network');}});
  transport.on('request',preview.listeners('request')[0]);
  t.after(()=>new Promise(resolve=>{transport.close(()=>{preview.emit('close');resolve();});transport.closeAllConnections();}));
  const r=await fetch(expectedOrigin+'/api/v1/me',{headers:{origin:expectedOrigin},redirect:'error'});
  assert.equal(r.status,401);assert.deepEqual(await r.json(),{error:{code:'unauthenticated'}});
  const denied=await fetch(expectedOrigin+'/api/v1/me',{headers:{origin:'http://127.0.0.1:4206'}});assert.equal(denied.status,403);
});
