import http from 'node:http';
import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { readFile } from 'node:fs/promises';
import { createAuthLoginRuntime } from '../../auth-login-runtime.mjs';
import { createPreviewServer } from '../../server.mjs';

const origin='http://127.0.0.1:4206', path='/api/v1/projects', me='/api/v1/me', login='/api/auth/login', logout='/api/auth/logout';
const users={A:{id:'11111111-1111-4111-8111-111111111111',email:'a@example.invalid',email_confirmed_at:'2026-11-15T00:00:00Z',aud:'authenticated'},
  B:{id:'22222222-2222-4222-8222-222222222222',email:'b@example.invalid',email_confirmed_at:'2026-11-15T00:00:00Z',aud:'authenticated'}};
const project={project_id:users.A.id,name:'Private A',description:'',species:'',visibility:'private',createdAt:'2026-11-16T00:00:00.000000Z',updatedAt:'2026-11-16T00:00:00.000000Z'};
const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};
export function harness(t, options={}) {
  const useServer=options.useServer===true; options={...options};delete options.useServer;
  const buildRuntime=config=>{
    if(!useServer)return createAuthLoginRuntime(config);
    const old=http.createServer;let handler;let created;
    http.createServer=callback=>{handler=callback;created=new EventEmitter();created.listen=()=>{throw Error('NO_REAL_LISTEN');};return created;};
    let server;try{server=createPreviewServer({fetchImpl:config.fetchImpl,registerEnabled:false,registerDiagnosticsEnabled:false,loginEnabled:config.enabled,loginOptions:config,projectsEnabled:config.projectsEnabled,rawQcEnabled:config.rawQcEnabled,rawQcOptions:{requestImpl:config.rawQcRequest},rawQcArtifactsEnabled:config.rawQcArtifactsEnabled,rawQcArtifactOptions:{requestImpl:config.rawQcArtifactRequest}});}finally{http.createServer=old;}
    const runtime=(req,res)=>handler(req,res);runtime.dispose=()=>server.emit('close');return runtime;
  };
  let wall=Date.now(),mono=1000,authMode='ok',reply=call=>call.deliver({items:[project],nextCursor:null});
  const auth=[],bridges=[],timers=new Map();
  const requestImpl=(request,callback)=>{
    const out=new EventEmitter(),call={request,out};bridges.push(call);
    out.destroy=()=>{out.destroyed=true;};
    call.deliver=(value,status=200,headers=['Content-Type','application/json'])=>{
      const response=new EventEmitter();response.statusCode=status;response.rawHeaders=headers;response.headers=Object.fromEntries(Array.from({length:headers.length/2},(_,i)=>[headers[i*2].toLowerCase(),headers[i*2+1]]));response.destroy=()=>{response.destroyed=true;};call.response=response;callback(response);
      response.emit('data',Buffer.isBuffer(value)?value:Buffer.from(JSON.stringify(value)));response.emit('end');
    };
    out.end=body=>{call.body=body;queueMicrotask(()=>reply(call));};return out;
  };
  const runtime=buildRuntime({enabled:true,projectsEnabled:true,uploadsEnabled:true,now:()=>wall,projectNow:()=>mono,
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
  function start(url, {cookie,view,method=url===login||url===logout?'POST':'GET',wire,value,headers={},rawHeaders,sendBody=true,response={}}={}) {
    const req=new EventEmitter();req.url=url;req.method=method;req.pause=()=>{};req.socket={remoteAddress:'127.0.0.1',destroyed:false};
    req.headers={host:'127.0.0.1:4206',origin,'x-cyomics-request':'1','content-type':'application/json',...(cookie?{cookie}:{}),...(view?{'x-cyomics-account-context':view}:{}),...headers};
    req.rawHeaders=rawHeaders??Object.entries(req.headers).filter(([,v])=>v!==undefined).flat();
    const res=new EventEmitter(),result={headers:{}},chunks=[];res.socket={destroyed:false};
    res.writes=[];res.endCalls=[];res.destroyCalls=0;res.headersSent=false;res.writableFinished=false;
    res.setHeader=(key,val)=>{result.headers[key.toLowerCase()]=val;};res.getHeader=key=>result.headers[key.toLowerCase()];
    res.writeHead=(status,h)=>{assert.equal(res.headersSent,false,'headers must be written once');res.headersSent=true;result.status=status;for(const [k,v] of Object.entries(h))res.setHeader(k,v);};
    const capture=()=>{const wire=Buffer.concat(chunks);result.wire=result.headers['content-type']?.startsWith('application/octet-stream')?wire:wire.toString();result.body=chunks.length===0?undefined:result.headers['content-type']?.startsWith('application/octet-stream')?wire:JSON.parse(wire.toString());};
    res.write=chunk=>{assert.ok(!res.destroyed&&!res.writableEnded,'write after close/end');const bytes=Buffer.from(chunk);res.writes.push(bytes);chunks.push(bytes);response.onWrite?.(res.writes.length,res);return response.writeResults?.[res.writes.length-1]??true;};
    res.finish=()=>{if(res.destroyed||res.writableFinished)return;assert.equal(res.writableEnded,true);res.writableFinished=true;capture();res.emit('finish');};
    res.end=wire=>{assert.ok(!res.destroyed&&!res.writableEnded,'end after close/end');res.endCalls.push(wire);if(wire!==undefined)chunks.push(Buffer.from(wire));res.writableEnded=true;capture();response.onEnd?.(res);if(response.autoFinish!==false)queueMicrotask(res.finish);};
    res.destroy=()=>{if(res.destroyed)return;res.destroyCalls++;res.destroyed=true;res.socket.destroyed=true;capture();res.emit('close');};
    options.artifactWireSetup?.(req,res,{url,response});
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
