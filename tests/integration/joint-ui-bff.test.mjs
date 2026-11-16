import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {joint,A,B,FID,raw,tick,deferred} from './joint-ui-bff-harness.mjs';
const sha=b=>createHash('sha256').update(b).digest('hex');
function input(){const file=new File([raw],'QA 原样.TXT');let reads=0;const read=file.arrayBuffer.bind(file);file.arrayBuffer=()=>{reads++;return read();};return{file,reads:()=>reads};}
const posts=j=>j.browser.filter(c=>c.options.method==='POST');
test('real UI selection reads zero bytes; one explicit save crosses actual BFF/bridge unchanged and confirms both full readbacks',async t=>{
  const j=await joint(t),f=input();await j.files.start();const before=j.browser.length;
  assert.equal(j.files.select(f.file),true);assert.equal(j.browser.length,before);assert.equal(f.reads(),0);assert.equal(posts(j).length,0);
  assert.equal(await j.files.save(),true);assert.equal(f.reads(),1);assert.equal(posts(j).length,1);
  const request=posts(j)[0];assert.equal(request.options.body,f.file);
  assert.equal(request.options.headers['X-Cyomics-Account-Context'],j.h.bridges[0].request.headers['X-Cyomics-Account-Context']??j.browser[1].options.headers['X-Cyomics-Account-Context']);
  assert.equal(request.options.headers['X-Cyomics-File-Sha256'],sha(raw));
  assert.equal(request.options.headers['X-Cyomics-Filename'],Buffer.from(f.file.name).toString('base64url'));
  const upstream=j.h.bridges.find(c=>c.request.method==='POST');assert.deepEqual(upstream.body,raw);
  assert.equal(upstream.request.headers['Content-Length'],String(raw.length));assert.equal(upstream.request.headers['X-Cyomics-Remaining-Ms'],'55000');
  assert.equal(upstream.request.headers.Authorization,'Bearer ACCESS_A');
  for(const k of ['Cookie','Origin','X-Cyomics-Account-Context','X-Owner-Id'])assert.equal(upstream.request.headers[k],undefined);
  const stored=j.stored.get(A),state=j.files.status();assert.equal(Object.keys(stored).length,9);assert.deepEqual(state.receipt,stored);assert.deepEqual(state.items,[stored]);assert.equal(state.phase,'saved');
  assert.deepEqual(j.browser.slice(before).map(c=>[c.options.method,c.path]),[['POST','/api/v1/projects/'+A+'/raw-uploads'],['GET','/api/v1/projects/'+A+'/raw-uploads/'+stored.request_id],['GET','/api/v1/projects/'+A+'/files']]);
  assert.ok(j.states.some(s=>s.phase==='confirming'&&s.receipt===null));assert.equal(j.h.timers.size,0);
});
test('all nine fields of receipt and server list participate in confirmation through the actual bridge',async t=>{
  const changes={file_id:B,project_id:B,request_id:B,filename:'other.txt',format:'parsed',bytes:raw.length+1,sha256:'0'.repeat(64),status:'parsed',createdAt:'2026-11-16T00:00:01.000000Z'};
  for(const endpoint of ['receipt','list'])for(const [key,value]of Object.entries(changes)){
    const j=await joint(t);j.override(c=>{
      if(c.request.method==='GET'&&j.stored.has(A)&&((endpoint==='receipt'&&c.request.path.includes('/raw-uploads/'))||(endpoint==='list'&&c.request.path.endsWith('/files')))){
        const changed={...j.stored.get(A),[key]:value};c.deliver(endpoint==='receipt'?{file:changed}:{items:[changed],nextCursor:null});return true;
      }
    });
    j.files.select(input().file);assert.equal(await j.files.save(),false,endpoint+' '+key);assert.notEqual(j.files.status().phase,'saved');assert.equal(j.files.status().receipt,null);assert.equal(posts(j).length,1);j.model.dispose();
  }
});
test('a new real TXT controller reloads only the controlled server list, with no selected File or client persistence',async t=>{
  const j=await joint(t);j.files.select(input().file);assert.equal(await j.files.save(),true);const record=j.stored.get(A),before=j.browser.length;
  // Re-entering the project creates a fresh TXT controller and disposes the old one.
  assert.equal(await j.model.select(A),true);const fresh=j.model.files();assert.notEqual(fresh,j.files);assert.equal(fresh.status().chosen,null);assert.equal(await fresh.start(),true);
  assert.deepEqual(fresh.status().items,[record]);assert.equal(fresh.status().receipt,null);assert.equal(fresh.status().canSave,false);
  assert.deepEqual(j.browser.slice(before).map(c=>[c.options.method,c.path]),[['GET','/api/v1/projects/'+A],['GET','/api/v1/projects/'+A+'/files']]);assert.equal(posts(j).length,1);
});
test('actual sent upstream failure becomes unknown; receipt404 and blocked save/reload never repeat POST',async t=>{
  const j=await joint(t);j.override(c=>{
    if(c.request.method==='POST'){c.out.emit('error',Error('synthetic upstream uncertain'));return true;}
    if(c.request.path.includes('/raw-uploads/')){c.deliver({error:{code:'file_not_found'}},404);return true;}
  });
  j.files.select(input().file);assert.equal(await j.files.save(),false);assert.equal(j.files.status().phase,'unknown');assert.equal(j.files.status().unknown,true);
  assert.equal(await j.files.save(),false);assert.equal(await j.files.reload(),false);assert.equal(j.files.select(input().file),false);
  assert.equal(await j.files.check(),false);assert.equal(j.files.status().unknown,true);await tick();assert.equal(posts(j).length,1);
  assert.equal(j.browser.at(-1).options.method,'GET');assert.match(j.browser.at(-1).path,/\/raw-uploads\/[0-9a-f-]+$/);
});
test('switching project disposes the real old controller and aborts a late old receipt',async t=>{
  const j=await joint(t),entered=deferred();let late;
  j.override(c=>{if(c.request.method==='POST'){late=c;entered.resolve();return true;}});
  j.files.select(input().file);const pending=j.files.save();await entered.promise;
  assert.equal(await j.model.select(B),true);assert.equal(await pending,false);assert.equal(j.files.status().phase,'disposed');
  late.deliver({file:{file_id:FID,project_id:A,request_id:posts(j)[0].options.headers['Idempotency-Key'],filename:'QA 原样.TXT',format:'proteinGroups_txt',bytes:raw.length,sha256:sha(raw),status:'stored',createdAt:'2026-11-16T00:00:00.000000Z'}},201);await tick();
  assert.equal(j.model.status().selected.project_id,B);assert.equal(j.model.files().status().receipt,null);assert.equal(posts(j).length,1);assert.equal(late.out.destroyed,true);
});
test('real replacement identity fences BFF pending save; late old success cannot appear or clear the new identity',async t=>{
  const j=await joint(t),entered=deferred();let late;
  j.override(c=>{if(c.request.method==='POST'){late=c;entered.resolve();return true;}});
  j.files.select(input().file);const pending=j.files.save();await entered.promise;const b=await j.replaceIdentity();await j.model.refresh();
  assert.equal(await pending,false);late.deliver({error:{code:'upload_result_unknown'}},504);await tick();
  assert.equal(j.files.status().phase,'disposed');assert.equal(j.model.status().email,'b@example.invalid');assert.equal(j.model.status().selected,null);
  assert.equal((await j.h.send('/api/v1/me',b)).body.user.email,'b@example.invalid');assert.equal(posts(j).length,1);assert.equal(late.out.destroyed,true);
});
test('actual BFF upload gate off yields truthful unavailable through real UI without upstream upload work',async t=>{
  const j=await joint(t,{uploadsEnabled:false}),before=j.h.bridges.length,auth=j.h.auth.length;
  assert.equal(await j.files.start(),false);assert.equal(j.files.status().phase,'unavailable');assert.equal(j.files.status().code,'uploads_unavailable');assert.deepEqual(j.files.status().items,[]);
  assert.equal(j.files.status().canChoose,false);assert.equal(j.files.status().canSave,false);
  const f=input();assert.equal(await j.files.save(),false);assert.equal(f.reads(),0);assert.equal(posts(j).length,0);assert.equal(j.h.bridges.length,before);assert.equal(j.h.auth.length,auth);
});
test('known boundary: direct programmatic select bypasses disabled UI and hashes/posts once; actual off BFF rejects503 with no upstream work',async t=>{
  const j=await joint(t,{uploadsEnabled:false}),before=j.h.bridges.length,auth=j.h.auth.length,replies=[];
  const original=j.h.start;j.h.start=(path,options)=>{const p=original(path,options);void p.promise.then(reply=>replies.push({path,status:reply.status,body:reply.body}));return p;};
  assert.equal(await j.files.start(),false);assert.equal(j.files.status().phase,'unavailable');assert.equal(j.files.status().canChoose,false);
  const f=input();assert.equal(j.files.select(f.file),true);assert.equal(j.files.status().phase,'chosen');assert.equal(await j.files.save(),false);
  assert.equal(f.reads(),1);assert.equal(posts(j).length,1);const reply=replies.find(r=>r.path.endsWith('/raw-uploads'));
  assert.deepEqual(reply,{path:'/api/v1/projects/'+A+'/raw-uploads',status:503,body:{error:{code:'uploads_unavailable'}}});
  assert.equal(j.h.bridges.length,before);assert.equal(j.h.auth.length,auth);assert.equal(j.files.status().receipt,null);assert.equal(j.files.status().phase,'unknown');
  t.diagnostic(JSON.stringify({known_boundary_not_fixed:true,disabled_UI_bypassed_by_direct_select:true,hash_reads:f.reads(),browser_POSTs:posts(j).length,BFF_status:reply.status,BFF_code:reply.body.error.code,controlled_upstream_calls:j.h.bridges.length-before,synthetic_Auth_calls:j.h.auth.length-auth,controller_phase:j.files.status().phase}));
});
