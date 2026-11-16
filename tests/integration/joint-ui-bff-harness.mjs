// No sockets, real Auth, database, or scientific input. Upstream is an explicit model.
import assert from 'node:assert/strict';
import {harness} from '../../frontend/tests/raw-upload-harness.mjs';
import {createPrivateProjectTransport,createPrivateProjectController} from '../../frontend/private-projects.js';
export const A='11111111-1111-4111-8111-111111111111',B='22222222-2222-4222-8222-222222222222',FID='44444444-4444-4444-8444-444444444444';
export const raw=Buffer.from([239,187,191,81,65,9,49,13,10,0,255,13,10]);
export const tick=()=>new Promise(setImmediate);
export function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve};}
const project=id=>({project_id:id,name:'QA offline '+id[0],description:'',species:'',visibility:'private',createdAt:'2026-11-16T00:00:00.000000Z',updatedAt:'2026-11-16T00:00:00.000000Z'});
export async function joint(t,{uploadsEnabled=true}={}){
  const h=harness(t,{uploadsEnabled}),signed=await h.signed();
  const jar=new Map(signed.cookie.split('; ').map(c=>{const i=c.indexOf('=');return[c.slice(0,i),c.slice(i+1)];}));
  const browser=[],states=[],stored=new Map();let override;
  h.reply(call=>{
    if(override?.(call)===true)return;
    const {path,method,headers}=call.request;
    if(path==='/api/v1/projects?limit=20')return call.deliver({items:[project(A),project(B)],nextCursor:null});
    const pid=path.split('/')[4];
    if(path==='/api/v1/projects/'+pid)return call.deliver({project:project(pid)});
    if(method==='POST'){
      const item={file_id:FID,project_id:pid,request_id:headers['Idempotency-Key'],filename:Buffer.from(headers['X-Cyomics-Filename'],'base64url').toString('utf8'),format:headers['X-Cyomics-File-Format'],bytes:Number(headers['X-Cyomics-File-Bytes']),sha256:headers['X-Cyomics-File-Sha256'],status:'stored',createdAt:'2026-11-16T00:00:00.000000Z'};
      stored.set(pid,item);return call.deliver({file:item},201);
    }
    if(path.endsWith('/files'))return call.deliver({items:stored.has(pid)?[stored.get(pid)]:[],nextCursor:null});
    if(path.includes('/raw-uploads/'))return stored.has(pid)?call.deliver({file:stored.get(pid)}):call.deliver({error:{code:'file_not_found'}},404);
    assert.fail('Unexpected controlled upstream route '+path);
  });
  const fetchImpl=async(path,options={})=>{
    const call={path,options};browser.push(call);
    assert.equal(options.credentials,'same-origin');assert.equal(options.cache,'no-store');assert.equal(options.redirect,'error');
    assert.equal(options.headers?.Authorization,undefined);
    if(options.signal?.aborted)throw new DOMException('Aborted','AbortError');
    // This is browser wire serialization, separate from the controller's explicit hash read.
    const wire=options.body instanceof Blob?Buffer.from(await Blob.prototype.arrayBuffer.call(options.body)):options.body===undefined?undefined:Buffer.from(options.body);
    const headers=Object.fromEntries(Object.entries(options.headers||{}).map(([k,v])=>[k.toLowerCase(),v]));
    if(wire)headers['content-length']=String(wire.length);
    const cookie=[...jar].map(([k,v])=>k+'='+v).join('; ');
    const pending=h.start(path,{method:options.method||'GET',wire,headers,cookie});
    const abort=()=>{pending.req.aborted=true;pending.req.emit('aborted');};options.signal?.addEventListener('abort',abort,{once:true});
    try{
      const reply=await pending.promise;
      if(options.signal?.aborted)throw new DOMException('Aborted','AbortError');
      for(const set of reply.headers['set-cookie']||[]){const pair=set.split(';')[0],i=pair.indexOf('=');jar.set(pair.slice(0,i),pair.slice(i+1));}
      const visible=Object.fromEntries(Object.entries(reply.headers).filter(([k])=>k!=='set-cookie'));
      return new Response(reply.wire,{status:reply.status,headers:visible});
    }finally{options.signal?.removeEventListener('abort',abort);}
  };
  const transport=createPrivateProjectTransport({enabled:true,fetchImpl});
  const model=createPrivateProjectController({transport});t.after(()=>model.dispose());
  assert.equal(await model.start(),true);assert.equal(await model.select(A),true);
  const files=model.files();files.subscribe(()=>states.push(files.status()));
  return{h,model,files,transport,browser,states,stored,override(fn){override=fn;},
    async replaceIdentity(){const cookie=[...jar].map(([k,v])=>k+'='+v).join('; ');const next=await h.signed('B',cookie);jar.clear();for(const c of next.cookie.split('; ')){const i=c.indexOf('=');jar.set(c.slice(0,i),c.slice(i+1));}return next;}};
}
