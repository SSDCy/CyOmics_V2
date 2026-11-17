import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {EventEmitter} from 'node:events';
import {readFile} from 'node:fs/promises';
import {createPreviewServer} from '../server.mjs';
const origin='http://127.0.0.1:4206',me='/api/v1/me',login='/api/auth/login',logout='/api/auth/logout';
// Explicit synthetic authentication seam, not an SDK login or a real credential.
const users={A:{id:'11111111-1111-4111-8111-111111111111',email:'a@example.invalid'}};
const fixture=JSON.parse(await readFile(new URL('./fixtures/import-complete-v2.synthetic.json',import.meta.url),'utf8'));
const pid=fixture.datasetVersion.project_id,vid=fixture.datasetVersion.dataset_version_id,fid=fixture.datasetVersion.file_id;
const importURL='/api/v1/uploads/'+fid+'/complete?project_id='+pid+'&version_id='+vid;
const sampleURL='/api/v1/datasets/'+vid+'/sample-designs?project_id='+pid+'&quant_family=intensity';
const sample={schemaVersion:1,project_id:pid,dataset_version_id:vid,quant_family:'Intensity',record:null};
function hostHarness(t,{sampleFlag,override=false}={}){
 const nativeCalls=[],overrideCalls=[];let handler,created=0,listens=0;
 function spy(calls){return (request,callback)=>{
  const out=new EventEmitter();out.destroy=()=>{out.destroyed=true;};const call={request,out};calls.push(call);
  out.end=body=>{call.body=body;queueMicrotask(()=>{
   const response=new EventEmitter();response.statusCode=200;response.rawHeaders=['Content-Type','application/json'];response.headers={'content-type':'application/json'};response.destroy=()=>{response.destroyed=true;};callback(response);
   assert.equal(request.hostname,'127.0.0.1');assert.equal(request.port,57430);assert.equal(request.method,'GET');
   assert.ok([importURL,sampleURL].includes(request.path),'only the two fixed synthetic upstream paths are allowed');
   response.emit('data',Buffer.from(JSON.stringify(request.path===importURL?fixture:sample)));response.emit('end');
  });};return out;
 };}
 const oldRequest=http.request,oldCreateServer=http.createServer;
 http.request=spy(nativeCalls);
 http.createServer=callback=>{created++;handler=callback;const server=new EventEmitter();server.listen=()=>{listens++;throw Error('REAL_LISTEN_FORBIDDEN');};return server;};
 let server;
 try{server=createPreviewServer({registerEnabled:false,registerDiagnosticsEnabled:false,loginEnabled:true,projectsEnabled:true,importsEnabled:true,
  loginOptions:{createClient:async()=>({login:async()=>({email:users.A.email,userId:users.A.id,accessToken:'SYNTHETIC_HOST_ACCESS',expiresAt:Date.now()+900000}),current:async()=>({email:users.A.email,userId:users.A.id}),dispose:async()=>{}})},
  ...(sampleFlag===undefined?{}:{sampleDesignsEnabled:sampleFlag}),...(override?{sampleDesignOptions:{requestImpl:spy(overrideCalls)}}:{})});
 }catch(error){http.request=oldRequest;http.createServer=oldCreateServer;throw error;}
 t.after(()=>{server.emit('close');http.request=oldRequest;http.createServer=oldCreateServer;assert.equal(created,1);assert.equal(listens,0);});
 const runtime=handler;
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
  return {send,signed,nativeCalls,overrideCalls};
}
async function prime(h){const s=await h.signed();const report=await h.send(importURL,s);assert.equal(report.status,200);assert.deepEqual(report.body,fixture);return s;}
test('host default transport uses native request after explicit sample enablement and verified report GET',async t=>{
 const h=hostHarness(t,{sampleFlag:true}),s=await prime(h);const r=await h.send(sampleURL,s);assert.equal(r.status,200);assert.deepEqual(r.body,sample);
 assert.deepEqual(h.nativeCalls.map(c=>c.request.path),[importURL,sampleURL]);assert.equal(h.overrideCalls.length,0);
 assert.equal(h.nativeCalls[1].request.headers.Authorization,'Bearer SYNTHETIC_HOST_ACCESS');assert.equal(h.nativeCalls[1].request.headers.Cookie,undefined);assert.equal(r.headers['set-cookie'],undefined);
});
test('host explicit sample request override wins over native request fallback',async t=>{
 const h=hostHarness(t,{sampleFlag:true,override:true}),s=await prime(h);assert.equal((await h.send(sampleURL,s)).status,200);
 assert.deepEqual(h.nativeCalls.map(c=>c.request.path),[importURL]);assert.deepEqual(h.overrideCalls.map(c=>c.request.path),[sampleURL]);
});
test('host sample feature omitted remains off and sends no sample request',async t=>{
 const h=hostHarness(t),s=await prime(h);const r=await h.send(sampleURL,s);assert.equal(r.status,503);assert.equal(r.body.error.code,'sample_design_unavailable');
 assert.deepEqual(h.nativeCalls.map(c=>c.request.path),[importURL]);assert.equal(h.overrideCalls.length,0);
});
