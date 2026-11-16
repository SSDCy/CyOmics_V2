import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { createAuthLoginRuntime } from '../auth-login-runtime.mjs';
import { LoginFailure } from '../auth-login-sdk.mjs';

const origin = 'http://127.0.0.1:4206', me = '/api/v1/me', login = '/api/auth/login', logout = '/api/auth/logout';
const email = 'synthetic@example.com', password = ' x ', anonymous = { error: { code: 'unauthenticated' } };
const authenticated = { status: 'authenticated', user: { email, emailVerified: true } };
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
function harness(t, options = {}) {
  let time = Date.now(), calls = 0, keys = 0;
  const timers = new Set();
  const defaultClient = async ({ signal }) => {
    calls++;
    return { login: async () => ({ email, userId: 'SYNTHETIC_USER', accessToken: 'SECRET_ACCESS', expiresAt: time + 900000 }),
      current: async () => ({ email, userId: 'SYNTHETIC_USER' }), dispose: async () => {} };
  };
  const runtime = createAuthLoginRuntime({ enabled: true, now: () => time, createClient: defaultClient,
    keyProvider: async () => { keys++; return 'SECRET_ANON'; }, ...options,
    setTimer: fn => { timers.add(fn); return fn; }, clearTimer: fn => timers.delete(fn) });
  t.after(() => runtime.dispose());
  function start(path, { cookie, method = path === me ? 'GET' : 'POST', wire, value, headers = {}, rawHeaders, address = '127.0.0.1', sendBody = true } = {}) {
    const req = new EventEmitter(); req.url = path; req.method = method;
    req.headers = { host: '127.0.0.1:4206', origin, 'content-type': 'application/json', 'x-cyomics-request': '1', ...(cookie ? { cookie } : {}), ...headers };
    if (rawHeaders) req.rawHeaders = rawHeaders;
    req.socket = { remoteAddress: address }; req.pause = () => {};
    const res = new EventEmitter(); const result = { headers: {} };
    res.getHeader = key => result.headers[key.toLowerCase()];
    res.setHeader = (key, v) => { result.headers[key.toLowerCase()] = v; };
    res.writeHead = (status, h) => { result.status = status; for (const [k,v] of Object.entries(h)) res.setHeader(k, v); };
    res.end = v => { res.writableEnded = true; result.wire = v; result.body = v === undefined ? undefined : JSON.parse(v); };
    const promise = runtime(req, res).then(owned => ({ ...result, owned }));
    if (sendBody) queueMicrotask(() => {
      if (method !== 'GET') req.emit('data', Buffer.from(wire ?? JSON.stringify(value ?? (path === logout ? {} : { email, password }))));
      req.emit('end');
    });
    return { req, res, result, promise };
  }
  const send = (path, options) => start(path, options).promise;
  const browser = async (options = {}) => {
    const result = await send(me, options); assert.equal(result.status, 401);
    return result.headers['set-cookie'].find(c => c.startsWith('cyomics_login_browser=')).split(';')[0];
  };
  return { runtime, send, start, browser, calls: () => calls, keys: () => keys, advance: ms => { time += ms; },
    expireTimers: () => { for (const fn of [...timers]) fn(); }, now: () => time };
}
const sessionCookie = result => result.headers['set-cookie'].find(c => c.startsWith('cyomics_session=')).split(';')[0];

test('login bootstrap is anonymous, non-client-chosen, exact/no-store and calls no Auth', async t => {
  const h = harness(t);
  const result = await h.send(me, { cookie: 'cyomics_login_browser=' + 'A'.repeat(43), headers: { origin: undefined } });
  assert.equal(result.status, 401); assert.deepEqual(result.body, anonymous); assert.equal(h.calls(), 0); assert.equal(h.keys(), 0);
  const c = result.headers['set-cookie'][0]; assert.match(c, /^cyomics_login_browser=[A-Za-z0-9_-]{43}; Path=\/; HttpOnly; SameSite=Lax; Max-Age=900$/);
  assert.ok(!c.includes('A'.repeat(43))); assert.equal(result.headers['cache-control'], 'no-store');
  assert.equal(result.headers['referrer-policy'], 'no-referrer'); assert.equal(result.headers['x-content-type-options'], 'nosniff');
});
test('login runtime requires explicit enable and validates program-only loopback origin seam', async t => {
  const h = harness(t, { enabled: false });
  for (const [path, code] of [[me,'session_unavailable'],[login,'login_unavailable'],[logout,'logout_unavailable']]) {
    const r = await h.send(path); assert.equal(r.status, 503); assert.deepEqual(r.body, { error: { code } }); assert.equal(r.headers['set-cookie'], undefined);
  }
  assert.equal(h.calls(), 0);
  for (const expectedOrigin of ['http://localhost:4206','https://127.0.0.1:4206','http://127.0.0.1:0','http://127.0.0.1:65536',origin+'/',origin+'?x',origin+'#x','http://u@127.0.0.1:4206',undefined]) {
    if (expectedOrigin === undefined) continue;
    assert.throws(() => createAuthLoginRuntime({ expectedOrigin }), /Invalid private login origin/);
  }
  const custom = harness(t, { expectedOrigin:'http://127.0.0.1:43210' });
  assert.equal((await custom.send(me)).status,403);
  assert.equal((await custom.send(me,{headers:{host:'127.0.0.1:43210',origin:'http://127.0.0.1:43210'}})).status,401);
});
test('login exact path, methods, host/origin/JSON/CSRF/duplicate headers and cookies reject before client', async t => {
  const h = harness(t);
  for (const path of [login+'?x=1',login+'/', '/api/auth/log%69n','//api/v1/me','/api/v1/%6de','/api/auth/logout/extra'])
    assert.equal((await h.send(path)).status,404);
  const method = await h.send(login,{method:'GET'}); assert.equal(method.status,405); assert.equal(method.headers.allow,'POST');
  for (const headers of [{host:'localhost:4206'},{origin:undefined},{origin:'https://evil.test'},{'x-cyomics-request':undefined},{'sec-fetch-site':'same-site'}])
    assert.equal((await h.send(login,{headers})).status,403);
  assert.equal((await h.send(login,{headers:{'content-type':'text/plain'}})).status,415);
  for (const name of ['host','origin','x-cyomics-request','content-type','cookie']) {
    const headers = {host:'127.0.0.1:4206',origin,'x-cyomics-request':'1','content-type':'application/json',cookie:'cyomics_login_browser='+'A'.repeat(43)};
    const rawHeaders = Object.entries(headers).flat();rawHeaders.push(name,headers[name]);
    assert.ok([403,415].includes((await h.send(login,{headers,rawHeaders})).status));
  }
  assert.equal((await h.send(me,{cookie:'cyomics_session=a; cyomics_session=b'})).status,403);
  assert.equal(h.calls(),0);
});
test('strict body rejects duplicates, extras, bad UTF8 password surrogates, oversized or empty; context is required', async t => {
  const h = harness(t), cookie = await h.browser();
  for (const wire of ['null','[]','{}','{"email":"synthetic@example.com","password":"x","password":"y"}',
    JSON.stringify({email,password,extra:true}),JSON.stringify({email,password:''}),JSON.stringify({email,password:'a'.repeat(73)}),
    JSON.stringify({email,password:'\ud800'}),JSON.stringify({email:'bad@example',password}),'{']) {
    const r = await h.send(login,{cookie,wire});assert.equal(r.status,400,wire);assert.deepEqual(r.body,{error:{code:'invalid_login'}});
  }
  assert.equal((await h.send(login,{cookie,wire:' '.repeat(4097)})).status,413);
  const missing = await h.send(login);assert.equal(missing.status,409);assert.deepEqual(missing.body,{error:{code:'login_context_required'}});
  assert.equal(h.calls(),0);
});
test('login passes original short password and normalized email, returns opaque bound cookies and authoritative me', async t => {
  let seen;
  const h=harness(t,{createClient:async()=>({login:async value=>{seen=value;return {email,userId:'id',accessToken:'SECRET_ACCESS',expiresAt:Date.now()+90000};},
    current:async()=>({email,userId:'id'}),dispose:async()=>{}})});
  const browser=await h.browser(), r=await h.send(login,{cookie:browser,value:{email:' '+email+' ',password}});
  assert.equal(r.status,200);assert.deepEqual(r.body,authenticated);assert.deepEqual(seen,{email,password});
  const c=r.headers['set-cookie'].find(c=>c.startsWith('cyomics_session='));
  assert.match(c,/^cyomics_session=[A-Za-z0-9_-]{43}; Path=\/api; HttpOnly; SameSite=Lax; Max-Age=[1-9][0-9]?$/);
  const cookie=browser+'; '+sessionCookie(r);const current=await h.send(me,{cookie});assert.deepEqual(current.body,authenticated);
  assert.equal(current.headers['set-cookie'],undefined);
  assert.equal((await h.send(me,{cookie:await h.browser()+'; '+sessionCookie(r)})).status,401);
  for (const output of [JSON.stringify(r),JSON.stringify(current)]) assert.ok(!output.includes('SECRET_ACCESS'));
});
test('failed replacement and transient me failures preserve old session; successful login rotates it', async t => {
  let mode='ok', count=0;
  const h=harness(t,{createClient:async()=>({login:async()=>{
    if(mode==='bad')throw new LoginFailure('credentials');if(mode==='offline')throw new LoginFailure();
    return {email,userId:'id',accessToken:'ACCESS_'+(++count),expiresAt:Date.now()+900000};},
    current:async()=>{if(mode==='offline')throw new LoginFailure();return {email,userId:'id'};},dispose:async()=>{}})});
  const b=await h.browser(), first=await h.send(login,{cookie:b}), old=b+'; '+sessionCookie(first);
  mode='bad';assert.equal((await h.send(login,{cookie:old})).status,401);
  mode='offline';assert.equal((await h.send(login,{cookie:old})).status,503);const unavailable=await h.send(me,{cookie:old});
  assert.equal(unavailable.status,503);assert.equal(unavailable.headers['set-cookie'],undefined);
  mode='ok';assert.equal((await h.send(me,{cookie:old})).status,200);
  const second=await h.send(login,{cookie:old});assert.equal(second.status,200);assert.notEqual(sessionCookie(first),sessionCookie(second));
  assert.equal((await h.send(me,{cookie:old})).status,401);
  assert.equal((await h.send(me,{cookie:b+'; '+sessionCookie(second)})).status,200);
});
test('invalid authority revokes local session, while logout is strict, local, idempotent and clears only login cookies', async t => {
  let invalid=false,calls=0;
  const h=harness(t,{createClient:async()=>{calls++;return {login:async()=>({email,userId:'id',accessToken:'ACCESS',expiresAt:Date.now()+900000}),
    current:async()=>{if(invalid)throw new LoginFailure('credentials');return {email,userId:'id'};},dispose:async()=>{}};}});
  const b=await h.browser(),logged=await h.send(login,{cookie:b}),cookie=b+'; '+sessionCookie(logged);
  invalid=true;assert.equal((await h.send(me,{cookie})).status,401);invalid=false;const before=calls;
  assert.equal((await h.send(me,{cookie})).status,401);assert.equal(calls,before);
  assert.equal((await h.send(logout,{cookie,value:{extra:1}})).status,400);
  for(const c of [cookie,undefined,cookie]){
    const r=await h.send(logout,{cookie:c});assert.deepEqual(r.body,{status:'signed_out'});assert.equal(r.status,200);
    assert.deepEqual(r.headers['set-cookie'],['cyomics_session=; Path=/api; HttpOnly; SameSite=Lax; Max-Age=0','cyomics_login_browser=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0']);
  }
  assert.equal(calls,before);
});
test('logout fences a late login, late me and even a late client factory without issuing a session', async t => {
  for(const stage of ['login','me','factory']){
    const entered=deferred(),late=deferred();let hold=false,disposed=0;
    const h=harness(t,{createClient:async()=>{
      if(hold&&stage==='factory'){entered.resolve();await late.promise;}
      return {login:async()=>{if(hold&&stage==='login'){entered.resolve();await late.promise;}return {email,userId:'id',accessToken:'ACCESS',expiresAt:Date.now()+900000};},
        current:async()=>{if(hold&&stage==='me'){entered.resolve();await late.promise;}return {email,userId:'id'};},dispose:async()=>{disposed++;}};
    }});
    const b=await h.browser(),first=await h.send(login,{cookie:b}),cookie=b+'; '+sessionCookie(first);hold=true;
    const pending=h.start(stage==='me'?me:login,{cookie});await entered.promise;
    const out=await h.send(logout,{cookie});assert.equal(out.status,200);
    const result=await pending.promise;assert.notEqual(result.status,200);assert.equal(result.headers['set-cookie'],undefined);
    late.resolve();await new Promise(setImmediate);assert.ok(disposed>=1);
    assert.equal((await h.send(me,{cookie})).status,401);
  }
});
test('new login supersedes pending login; only newest identity can rotate, logout during body read still wins', async t => {
  const entered=deferred(),late=deferred();let count=0;
  const h=harness(t,{createClient:async()=>({login:async()=>{const n=++count;if(n===1){entered.resolve();await late.promise;}
    return {email,userId:'id',accessToken:'ACCESS_'+n,expiresAt:Date.now()+900000};},current:async()=>({email,userId:'id'}),dispose:async()=>{}})});
  const b=await h.browser(),first=h.start(login,{cookie:b});await entered.promise;
  const second=await h.send(login,{cookie:b});assert.equal(second.status,200);assert.equal((await first.promise).status,504);
  late.resolve();await new Promise(setImmediate);assert.equal(first.result.headers['set-cookie'],undefined);
  const out=h.start(logout,{cookie:b,sendBody:false});
  const third=await h.send(login,{cookie:b});assert.equal(third.status,200);
  out.req.emit('data',Buffer.from('{}'));out.req.emit('end');assert.equal((await out.promise).status,200);
  assert.equal((await h.send(me,{cookie:b+'; '+sessionCookie(third)})).status,401);
});
test('deadline, disconnect and dispose fence late results, and me deadline retains live session for recovery', async t => {
  for(const mode of ['timer','disconnect','dispose']){
    const entered=deferred(),late=deferred();const h=harness(t,{createClient:async()=>({login:async()=>{entered.resolve();await late.promise;return {email,userId:'id',accessToken:'ACCESS',expiresAt:Date.now()+900000};},dispose:async()=>{}})});
    const b=await h.browser(),pending=h.start(login,{cookie:b});await entered.promise;
    if(mode==='timer')h.expireTimers();else if(mode==='disconnect'){pending.req.aborted=true;pending.req.emit('aborted');}else h.runtime.dispose();
    const result=await pending.promise;assert.notEqual(result.status,200);assert.equal(result.headers['set-cookie'],undefined);
    late.resolve();await new Promise(setImmediate);assert.equal(pending.result.headers['set-cookie'],undefined);
  }
  let hold=false;const entered=deferred(),late=deferred();
  const h=harness(t,{createClient:async()=>({login:async()=>({email,userId:'id',accessToken:'ACCESS',expiresAt:Date.now()+900000}),
    current:async()=>{if(hold){entered.resolve();await late.promise;}return {email,userId:'id'};},dispose:async()=>{}})});
  const b=await h.browser(),r=await h.send(login,{cookie:b}),cookie=b+'; '+sessionCookie(r);hold=true;
  const pending=h.start(me,{cookie});await entered.promise;h.expireTimers();assert.equal((await pending.promise).status,503);
  hold=false;late.resolve();assert.equal((await h.send(me,{cookie})).status,200);
});
test('browser/IP attempt limits, expired session, restarted runtime, context cap and limiter cap are bounded', async t => {
  const h=harness(t,{createClient:async()=>({login:async()=>{throw new LoginFailure('credentials');},dispose:async()=>{}})});
  const b=await h.browser();for(let i=0;i<5;i++)assert.equal((await h.send(login,{cookie:b})).status,401);
  assert.equal((await h.send(login,{cookie:b})).status,429);
  const b2=await h.browser();for(let i=0;i<5;i++)assert.equal((await h.send(login,{cookie:b2})).status,401);
  assert.equal((await h.send(login,{cookie:await h.browser()})).status,429);
  h.advance(600001);assert.equal((await h.send(login,{cookie:b})).status,401);
  const full=harness(t);const browsers=[];
  for(let i=0;i<256;i++)browsers.push(await full.browser());
  assert.equal((await full.send(me)).status,503);
  for(let i=0;i<256;i++)assert.equal((await full.send(login,{cookie:browsers[i],address:'10.0.'+Math.floor(i/250)+'.'+(i%250+1)})).status,200);
  assert.equal((await full.send(login,{cookie:browsers[0],address:'10.9.9.9'})).status,503);
  full.advance(600001);assert.equal((await full.send(login,{cookie:browsers[0],address:'10.9.9.9'})).status,200);
  full.advance(900001);assert.equal((await full.send(me,{cookie:browsers[0]})).status,401);
  const fresh=harness(t);assert.equal((await fresh.send(me,{cookie:browsers[1]+'; cyomics_session='+'B'.repeat(43)})).status,401);
});
test('[LOGIN-RACE-001] slow logout is idempotent after expiry or another logout, and wins over a late login',async t=>{
  for(const scenario of ['expired','already_closed','late_login']){
    let hold=false;const entered=deferred(),late=deferred();
    const h=harness(t,{createClient:async()=>({login:async()=>{if(hold){entered.resolve();await late.promise;}return {email,userId:'id',accessToken:'ACCESS',expiresAt:Date.now()+900000};},
      current:async()=>({email,userId:'id'}),dispose:async()=>{}})});
    const b=await h.browser(),first=await h.send(login,{cookie:b}),cookie=b+'; '+sessionCookie(first);
    if(scenario==='expired')h.advance(899000);
    const out=h.start(logout,{cookie,sendBody:false});let pending;
    if(scenario==='expired'){
      // Keep logout's own 15s deadline: context/session have only 1s left when it starts.
      h.advance(1001);await h.send(me);
    }
    if(scenario==='already_closed')assert.equal((await h.send(logout,{cookie})).status,200);
    else if(scenario==='late_login'){hold=true;pending=h.start(login,{cookie});await entered.promise;}
    out.req.emit('data',Buffer.from('{}'));out.req.emit('end');assert.equal((await out.promise).status,200);
    if(pending){assert.equal((await pending.promise).status,504);late.resolve();await new Promise(setImmediate);assert.equal(pending.result.headers['set-cookie'],undefined);}
    assert.equal((await h.send(me,{cookie})).status,401);
  }
});
test('[LOGIN-EXPIRY-001] authority completes after known expiry or sweep aborts it: both are 401, never cached identity',async t=>{
  for(const sweep of [false,true]){
    let hold=false;const entered=deferred(),late=deferred();
    const h=harness(t,{createClient:async()=>({login:async()=>({email,userId:'id',accessToken:'ACCESS',expiresAt:Date.now()+900000}),
      current:async()=>{if(hold){entered.resolve();await late.promise;}return {email,userId:'id'};},dispose:async()=>{}})});
    const b=await h.browser(),logged=await h.send(login,{cookie:b}),cookie=b+'; '+sessionCookie(logged);
    h.advance(899000);hold=true;const pending=h.start(me,{cookie});await entered.promise;h.advance(1001);
    if(sweep)await h.send(me); // A different request invokes the same expiry sweep as the timer.
    late.resolve();const result=await pending.promise;assert.equal(result.status,401);assert.deepEqual(result.body,anonymous);
    hold=false;assert.equal((await h.send(me,{cookie})).status,401);
  }
});
test('partial login body is canceled by logout, and 15s body deadline never calls a client',async t=>{
  const h=harness(t),b=await h.browser(),pending=h.start(login,{cookie:b,sendBody:false});
  assert.equal((await h.send(logout,{cookie:b})).status,200);assert.equal((await pending.promise).status,504);assert.equal(h.calls(),0);
  const next=h.start(login,{cookie:await h.browser(),sendBody:false});h.expireTimers();assert.equal((await next.promise).status,504);assert.equal(h.calls(),0);
});
