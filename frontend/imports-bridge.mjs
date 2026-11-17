import http from 'node:http';
import {strictProjectJSON} from './projects-bridge.mjs';
import {ImportFailure,importErrors,importRoute,importInput,validateImportSuccess,validateImportError,MAX_IMPORT_SUCCESS,MAX_IMPORT_ERROR} from './imports-contract.mjs';
export {ImportFailure,importRoute,importInput,ownsImportRoute} from './imports-contract.mjs';

export function readImportBody(req,signal,method){
  return new Promise((resolve,reject)=>{
    let size=0,done=false;const chunks=[],cap=method==='POST'?1024:0;
    const cleanup=()=>{req.removeListener('data',data);req.removeListener('end',end);signal.removeEventListener('abort',abort);};
    const finish=error=>{if(done)return;done=true;cleanup();if(error){req.pause?.();reject(error);}else resolve(Buffer.concat(chunks,size));};
    const data=chunk=>{if(!(chunk instanceof Uint8Array))return finish(new ImportFailure(400,'invalid_import_request'));if(size+chunk.byteLength>cap)return finish(new ImportFailure(cap?413:400,cap?'import_request_too_large':'invalid_import_request'));size+=chunk.byteLength;chunks.push(chunk);};
    const end=()=>finish(),abort=()=>finish(new ImportFailure(504,'import_timeout')),error=()=>finish(new ImportFailure(400,'invalid_import_request'));
    req.on('data',data);req.once('end',end);req.on('error',error);req.once('close',()=>req.removeListener('error',error));signal.addEventListener('abort',abort,{once:true});if(signal.aborted)abort();
  });
}

export function createImportBridge({requestImpl=http.request}={}){
  if(typeof requestImpl!=='function')throw new TypeError('Invalid import transport');
  return ({route,body,accessToken,signal,budget,onSent=()=>{}})=>new Promise((resolve,reject)=>{
    let outgoing,incoming,sent=false,settled=false,size=0;const chunks=[];
    const failure=()=>{let expired=signal?.aborted===true;try{expired=expired||budget.remaining()<=0;}catch{expired=true;}const unknown=route?.method==='POST'&&sent;return new ImportFailure(unknown||expired?504:503,unknown?'import_result_unknown':expired?'import_timeout':'import_unavailable');};
    const finish=(error,result)=>{if(settled)return;settled=true;signal?.removeEventListener('abort',abort);if(error){try{outgoing?.destroy();incoming?.destroy();}catch{}reject(error);}else resolve(result);};
    const abort=()=>finish(failure());
    try{
      const selected=route.project_id;route=importRoute(route.path,route.method);
      if(route.method==='POST'){const payload=importInput(body);if(payload.project_id!==selected)throw Error();route.project_id=selected;}
      else if(body!==undefined)throw Error();
      const remaining=Math.floor(budget.remaining());
      if(signal.aborted||!Number.isFinite(remaining)||remaining<1||remaining>(route.method==='POST'?55000:12000)||!(/^[1-9]\d{12}$/).test(budget.deadlineUnixMs)
          ||typeof accessToken!=='string'||!(/^[A-Za-z0-9._-]{1,8192}$/).test(accessToken))throw Error();
      const headers={Accept:'application/json',Authorization:'Bearer '+accessToken,Origin:'http://127.0.0.1:4206','X-Cyomics-Remaining-Ms':String(remaining),'X-Cyomics-Deadline-Unix-Ms':budget.deadlineUnixMs};
      if(route.method==='POST')Object.assign(headers,{'Content-Type':'application/json','Content-Length':String(body.byteLength),'X-Cyomics-Request':'1'});
      outgoing=requestImpl({hostname:'127.0.0.1',port:57430,method:route.method,path:route.path,headers,agent:false},response=>{
        incoming=response;
        if(settled){response.on('error',()=>{});response.destroy();return;}
        const invalid=()=>finish(failure());response.on('error',invalid);response.once('aborted',invalid);
        try{
          if(!Array.isArray(response.rawHeaders)||response.rawHeaders.length%2)throw Error();
          const values=new Map();
          for(let i=0;i<response.rawHeaders.length;i+=2){const k=response.rawHeaders[i],v=response.rawHeaders[i+1];if(typeof k!=='string'||typeof v!=='string')throw Error();const name=k.toLowerCase();if(['set-cookie','location','refresh'].includes(name))throw Error();if(['content-type','content-length','content-encoding'].includes(name)){if(values.has(name))throw Error();values.set(name,v);}}
          if(response.headers?.['set-cookie']!==undefined||!(/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i).test(values.get('content-type')||'')||![undefined,'identity'].includes(values.get('content-encoding')))throw Error();
          const status=response.statusCode;if(status!==200&&!importErrors.has(status))throw Error();
          const cap=status===200?MAX_IMPORT_SUCCESS:MAX_IMPORT_ERROR,length=values.get('content-length');
          if(length!==undefined&&(!(/^[0-9]{1,10}$/).test(length)||Number(length)>cap))throw Error();
          response.on('data',chunk=>{if(settled)return;try{if(signal.aborted||budget.remaining()<=0||!(chunk instanceof Uint8Array)||size+chunk.byteLength>cap)return invalid();size+=chunk.byteLength;chunks.push(chunk);}catch{invalid();}});
          response.once('end',()=>{if(settled)return;try{
            if(signal.aborted||budget.remaining()<=0||length!==undefined&&Number(length)!==size)throw Error();
            const parsed=strictProjectJSON(Buffer.concat(chunks,size));
            const value=status===200?validateImportSuccess(parsed,route):validateImportError(status,parsed,route);
            finish(undefined,{status,value});
          }catch{invalid();}});
          response.once('close',()=>{if(!settled)invalid();});
        }catch{invalid();}
      });
      outgoing.on('error',abort);outgoing.once('close',()=>{if(!settled&&!incoming)abort();});
      if(settled){outgoing.destroy();return;}
      signal.addEventListener('abort',abort,{once:true});if(signal.aborted||budget.remaining()<=0)return abort();
      // Conservatively unknown once the POST may have reached the kernel/upstream.
      sent=true;onSent();outgoing.end(body);
    }catch(error){finish(error instanceof ImportFailure&&!sent?error:failure());}
  });
}
