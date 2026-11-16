import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createLoginClient, LoginFailure } from '../auth-login-sdk.mjs';

const email='synthetic@example.com',password='  PASSWORD_SECRET  ',access='secret.access.token',refresh='SECRET_REFRESH',anon='SECRET_ANON';
const user={id:'SYNTHETIC_USER',email,email_confirmed_at:'2026-11-15T00:00:00Z',aud:'authenticated'};
const token=(extra={})=>({access_token:access,refresh_token:refresh,token_type:'bearer',expires_in:3600,expires_at:Math.floor(Date.now()/1000)+3600,user,...extra});
function harness(reply,extra={}){
  const controller=new AbortController(),calls=[];let keys=0;
  const create=()=>createLoginClient({signal:controller.signal,deadline:Date.now()+15000,keyProvider:async()=>{keys++;return anon;},
    fetchImpl:async(url,options)=>{calls.push({url,options});return reply(url,options);},...extra});
  return {controller,calls,create,keys:()=>keys};
}
test('locked official SDK does password grant then authoritative getUser, never forwards cookies or retains refresh in result',async()=>{
  const pkg=JSON.parse(await readFile(new URL('../node_modules/@supabase/auth-js/package.json',import.meta.url),'utf8'));assert.equal(pkg.version,'2.86.0');
  const h=harness(url=>Response.json(url.includes('/token?')?token():user));const client=await h.create();
  const result=await client.login({email,password});
  assert.deepEqual(Object.keys(result).sort(),['accessToken','email','expiresAt','userId']);assert.equal(result.email,email);assert.equal(result.accessToken,access);
  assert.ok(result.expiresAt<=Date.now()+900000);assert.ok(!JSON.stringify(result).includes(refresh));
  assert.deepEqual(h.calls.map(c=>c.url),['http://127.0.0.1:57421/auth/v1/token?grant_type=password','http://127.0.0.1:57421/auth/v1/user']);
  const wire=JSON.parse(h.calls[0].options.body);assert.equal(wire.email,email);assert.equal(wire.password,password);
  assert.equal(h.calls[1].options.headers.Authorization,'Bearer '+access);
  for(const {options} of h.calls){assert.equal(options.headers.apikey,anon);assert.equal(options.signal,h.controller.signal);assert.equal(options.redirect,'error');assert.equal(options.credentials,'omit');assert.equal(options.cache,'no-store');assert.ok(!Object.keys(options.headers).some(k=>/cookie/i.test(k)));}
  await assert.rejects(client.login({email,password}),LoginFailure);await client.dispose();assert.equal(h.calls.length,2);
});
test('login requires same authoritative id/email as input and signin response, with finite confirmed date',async()=>{
  for(const current of [{...user,id:'different'},{...user,email:'other@example.com'},{...user,email_confirmed_at:null},{...user,email_confirmed_at:'invalid'},{...user,email_confirmed_at:123}]){
    const h=harness(url=>Response.json(url.includes('/token?')?token():current));const c=await h.create();
    await assert.rejects(c.login({email,password}),e=>e instanceof LoginFailure&&e.kind==='credentials');await c.dispose();assert.equal(h.calls.length,2);
  }
  const h=harness(url=>Response.json(url.includes('/token?')?token({user:{...user,email:'other@example.com'}}):user));const c=await h.create();
  await assert.rejects(c.login({email,password}),e=>e.kind==='credentials');await c.dispose();
});
test('me uses explicit access token and authoritative same identity; no token/password grant or refresh',async()=>{
  const h=harness(()=>Response.json(user));const c=await h.create();
  assert.deepEqual(await c.current({accessToken:access,email,userId:user.id}),{email,userId:user.id});assert.equal(h.calls.length,1);
  assert.equal(h.calls[0].options.method,'GET');assert.equal(h.calls[0].options.body,undefined);
  await assert.rejects(c.current({accessToken:access,email,userId:'wrong'}),e=>e.kind==='credentials');await c.dispose();
});
test('expiry is finite, future, conservative and at least one second; token expiry caps product TTL',async()=>{
  for(const extra of [{expires_in:null},{expires_in:0},{expires_in:-1},{expires_in:'3600'},{expires_at:'invalid'},{expires_at:1},{expires_in:0.1},{access_token:'bad token'}]){
    const h=harness(url=>Response.json(url.includes('/token?')?token(extra):user));const c=await h.create();await assert.rejects(c.login({email,password}),LoginFailure);await c.dispose();
  }
  const deadline=Math.floor(Date.now()/1000)+20;const h=harness(url=>Response.json(url.includes('/token?')?token({expires_in:10,expires_at:deadline}):user));const c=await h.create();
  const result=await c.login({email,password});assert.ok(result.expiresAt<=Date.now()+10000);assert.ok(result.expiresAt<=deadline*1000);await c.dispose();
});
test('wrong/missing/unverified credentials have one classification, limits are fixed, server errors stay unavailable',async()=>{
  for(const [status,code,kind] of [[400,'invalid_credentials','credentials'],[400,'email_not_confirmed','credentials'],[401,'SECRET_UNKNOWN','credentials'],[429,'SECRET_UNKNOWN','limited'],[500,'invalid_credentials','unavailable'],[400,'SECRET_UNKNOWN','unavailable']]){
    const h=harness(()=>Response.json({code,message:'SECRET_UPSTREAM',password,refresh_token:refresh},{status}));const c=await h.create();
    await assert.rejects(c.login({email,password}),e=>e instanceof LoginFailure&&e.kind===kind&&!e.message.includes('SECRET'));await c.dispose();assert.equal(h.calls.length,1);
  }
});
test('throwing, malformed, oversized, redirected, wrong media and slow canceled bodies never reach raw SDK error logging',async t=>{
  const logs=[];t.mock.method(console,'error',(...args)=>logs.push(args));
  const replies=[()=>{throw Error('SECRET_TRANSPORT');},()=>new Response('SECRET_REDIRECT',{status:302}),
    ()=>new Response('SECRET_HTML',{headers:{'content-type':'text/html'}}),()=>new Response('{SECRET_BAD_JSON',{headers:{'content-type':'application/json'}}),
    ()=>Response.json({secret:'s'.repeat(32768)}),()=>Response.json({message:'SECRET_UPSTREAM'},{status:503})];
  for(const reply of replies){const h=harness(reply),c=await h.create();await assert.rejects(c.login({email,password}),e=>e.kind==='unavailable'&&!e.message.includes('SECRET'));await c.dispose();assert.equal(h.calls.length,1);}
  assert.deepEqual(logs,[]);
});
test('aborted/expired construction calls no key or transport; key failure and late transport are closed',async()=>{
  const h=harness(()=>{throw Error('forbidden');});h.controller.abort();await assert.rejects(h.create(),LoginFailure);assert.equal(h.keys(),0);assert.equal(h.calls.length,0);
  let keys=0;await assert.rejects(createLoginClient({signal:new AbortController().signal,deadline:1,keyProvider:async()=>{keys++;return anon;}}),LoginFailure);assert.equal(keys,0);
  let release,entered;const ready=new Promise(r=>{entered=r;});const waiting=new Promise(r=>{release=r;});
  const late=harness(async()=>{entered();await waiting;return Response.json(token());});const c=await late.create();const pending=c.login({email,password});await ready;late.controller.abort();release();
  await assert.rejects(pending,LoginFailure);await c.dispose();assert.equal(late.calls.length,1);
});

test('credential 4xx accepts recognized code, error_code, matching legacy status and identical dual strings',async t=>{
  const codes=['invalid_credentials','email_not_confirmed','user_not_found','user_banned','bad_jwt','session_not_found','session_expired'];
  const fixtures=codes.flatMap(code=>[
    {status:400,body:{code}},
    {status:400,body:{error_code:code}},
    {status:400,body:{code:400,error_code:code}},
    {status:400,body:{code,error_code:code}},
  ]);
  fixtures.push({status:422,body:{code:422,error_code:'invalid_credentials'}});
  for(const {status,body} of fixtures){
    const h=harness(()=>Response.json(body,{status})),c=await h.create();
    await assert.rejects(c.login({email,password}),e=>e instanceof LoginFailure&&e.kind==='credentials'&&e.message==='credentials');
    await c.dispose();assert.equal(h.calls.length,1);assert.equal(h.keys(),1);
  }
  t.diagnostic(`compatible credential fixture cases: ${fixtures.length}`);
});

test('conflicting, unknown and malformed error fields stay unavailable without guessing from messages',async t=>{
  const known='invalid_credentials';
  const fixtures=[
    {code:known,error_code:'email_not_confirmed'},
    {code:'unknown',error_code:known}, {code:known,error_code:'unknown'},
    {code:null,error_code:known}, {code:known,error_code:null},
    {code:'',error_code:known}, {code:false,error_code:known},
    {code:[],error_code:known}, {code:known,error_code:[]},
    {code:{},error_code:known}, {code:known,error_code:{}},
    {code:401,error_code:known}, {code:400.5,error_code:known},
    {code:'400',error_code:known}, {code:400},
    {msg:'invalid login credentials',error:'email not confirmed'},
    {code:'unknown'}, {error_code:'unknown'},
    null, [], 'invalid_credentials', 400,
  ];
  for(const body of fixtures){
    const h=harness(()=>Response.json(body,{status:400})),c=await h.create();
    await assert.rejects(c.login({email,password}),e=>e instanceof LoginFailure&&e.kind==='unavailable'&&e.message==='unavailable');
    await c.dispose();assert.equal(h.calls.length,1);
  }
  t.diagnostic(`conservative error-shape fixture cases: ${fixtures.length}`);
});

test('401/403, 429 and 5xx retain precedence and never expose upstream messages or SDK logs',async t=>{
  const logs=[];for(const method of ['error','warn','log'])t.mock.method(console,method,(...args)=>logs.push(args));
  const upstream='SYNTHETIC_UPSTREAM_SECRET';
  const fixtures=[
    [401,{code:'invalid_credentials',error_code:'email_not_confirmed'},'credentials'],
    [403,{error_code:'unknown'},'credentials'],
    [429,{error_code:'invalid_credentials'},'limited'],
    [429,{code:null,error_code:[]},'limited'],
    [500,{code:'invalid_credentials'},'unavailable'],
    [503,{error_code:'email_not_confirmed'},'unavailable'],
    [500,{code:500,error_code:'invalid_credentials'},'unavailable'],
    [503,{code:'invalid_credentials',error_code:'invalid_credentials'},'unavailable'],
  ];
  for(const [status,fields,kind] of fixtures){
    const h=harness(()=>Response.json({...fields,msg:upstream,message:upstream,error:upstream,password,refresh_token:refresh},{status})),c=await h.create();
    await assert.rejects(c.login({email,password}),e=>e instanceof LoginFailure&&e.kind===kind&&e.message===kind
      &&![upstream,password,refresh].some(secret=>JSON.stringify(e).includes(secret)));
    await c.dispose();assert.equal(h.calls.length,1);
  }
  assert.deepEqual(logs,[]);
  t.diagnostic(`status precedence and sanitized-output fixture cases: ${fixtures.length}`);
});
