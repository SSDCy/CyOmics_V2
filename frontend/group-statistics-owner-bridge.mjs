import {createHash,webcrypto} from 'node:crypto';
import {groupReadRoute,groupReadDescriptor,groupReadError,groupReadJSON,GroupReadFailure,GROUP_MEDIA,GROUP_META_MAX,GROUP_FILE_MAX} from './group-statistics-owner-contract.mjs';
const sha=body=>createHash('sha256').update(body).digest('hex');
export function createGroupReadBridge({requestImpl=null}={}){
 return ({route,body,metadata,accessToken,signal,budget,onSent=()=>{}})=>new Promise((resolve,reject)=>{
  let outgoing,incoming,settled=false,sent=false,size=0;const chunks=[];
  const expired=()=>{try{return signal.aborted||budget.remaining()<=0;}catch{return true;}};
  const failure=()=>new GroupReadFailure(route?.kind==='save'&&sent||expired()?504:503,route?.kind==='save'&&sent?'group_statistics_read_timeout':expired()?'group_statistics_read_timeout':'group_statistics_unavailable');
  const finish=(error,result)=>{if(settled)return;settled=true;signal?.removeEventListener('abort',abort);if(error){try{outgoing?.destroy();incoming?.destroy();}catch{}reject(error);}else resolve(result);};
  const abort=()=>finish(failure()),live=()=>{if(settled||expired())throw failure();};
  try{
   route=groupReadRoute(route?.path,route?.method);const left=Math.floor(budget.remaining());if(typeof requestImpl!=='function'||!signal||signal.aborted||!Number.isFinite(left)||left<1||left>route.capMs||!/^[1-9]\d{12}$/.test(budget.deadlineUnixMs)||typeof accessToken!=='string'||!/^[A-Za-z0-9._-]{1,8192}$/.test(accessToken))throw Error();
   const headers={Accept:route.kind==='artifact'?GROUP_MEDIA[route.name]:'application/json',Authorization:'Bearer '+accessToken,Origin:'http://127.0.0.1:4206','X-Cyomics-Remaining-Ms':String(left),'X-Cyomics-Deadline-Unix-Ms':budget.deadlineUnixMs};
   if(body!==undefined)throw Error();
   signal.addEventListener('abort',abort,{once:true});live();outgoing=requestImpl({hostname:'127.0.0.1',port:57430,path:route.path,method:route.method,headers,agent:false},response=>{
    incoming=response;if(settled){response.on('error',()=>{});response.destroy();return;}const invalid=()=>finish(failure());response.on('error',invalid);response.once('aborted',invalid);let ended=false;
    try{
     live();if(!sent||!Array.isArray(response.rawHeaders)||response.rawHeaders.length%2)throw Error();const h=new Map();for(let n=0;n<response.rawHeaders.length;n+=2){const key=response.rawHeaders[n],value=response.rawHeaders[n+1];if(typeof key!=='string'||typeof value!=='string'||! /^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(key)||/[\x00-\x1f\x7f]/.test(value)||h.has(key.toLowerCase())||['location','refresh','set-cookie'].includes(key.toLowerCase()))throw Error();h.set(key.toLowerCase(),value);}
     const status=response.statusCode,binary=route.kind==='artifact'&&status===200;if(![200,400,401,403,404,405,409,503,504].includes(status)||response.headers?.['set-cookie']!==undefined)throw Error();
     if(h.get('cache-control')!=='no-store'||h.get('x-content-type-options')!=='nosniff'||h.get('referrer-policy')!=='no-referrer'||h.get('content-security-policy')!=="default-src 'none'; sandbox"||![undefined,'identity'].includes(h.get('content-encoding'))||h.has('transfer-encoding')&&(h.get('transfer-encoding')!=='chunked'||h.has('content-length')))throw Error();
     if(binary?h.get('content-type')!==GROUP_MEDIA[route.name]:!/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(h.get('content-type')||''))throw Error();
     const cap=binary?GROUP_FILE_MAX:[200,201].includes(status)?GROUP_META_MAX:4096,length=h.get('content-length');if(length!==undefined&&(!/^(?:0|[1-9]\d{0,9})$/.test(length)||Number(length)>cap)||binary&&(!length||Number(length)<1))throw Error();
     if(binary&&(!/^[0-9a-f]{64}$/.test(h.get('x-cyomics-file-sha256')||'')||h.get('content-disposition')!=='attachment; filename="'+route.name+'"'))throw Error();
     response.on('data',part=>{if(settled)return;try{live();if(!(part instanceof Uint8Array)||size+part.length>cap)throw Error();size+=part.length;chunks.push(part);}catch{invalid();}});
     response.once('end',()=>{ended=true;void(async()=>{live();if(length!==undefined&&Number(length)!==size)throw Error();const raw=Buffer.concat(chunks,size);
      if(binary){if(sha(raw)!==h.get('x-cyomics-file-sha256'))throw Error();live();finish(null,{status,body:raw,headers:{'Content-Type':GROUP_MEDIA[route.name],'Content-Length':String(size),'Content-Disposition':'attachment; filename="'+route.name+'"','X-Cyomics-File-SHA256':h.get('x-cyomics-file-sha256')}});}
      else{const parsed=groupReadJSON(raw),value=[200,201].includes(status)?await groupReadDescriptor(parsed,route,{cryptoImpl:webcrypto}):groupReadError(status,parsed);live();finish(null,{status,value});}
     })().catch(invalid);});response.once('close',()=>{if(!ended&&!settled)invalid();});
    }catch{invalid();}
   });outgoing.on('error',abort);outgoing.once('close',()=>{if(!settled&&!incoming)abort();});live();sent=true;onSent();outgoing.end(body);
  }catch(error){finish(!sent&&error instanceof GroupReadFailure?error:failure());}
 });
}
