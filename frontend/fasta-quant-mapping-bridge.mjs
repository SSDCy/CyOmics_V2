import {createHash,webcrypto} from 'node:crypto';
import {mappingRoute,mappingInput,mappingMetadata,mappingError,mappingJSON,mappingFilename,MappingFailure,MAPPING_MAX,MAPPING_META_MAX} from './fasta-quant-mapping-contract.mjs';
const sha=body=>createHash('sha256').update(body).digest('hex');
export function createMappingBridge({requestImpl=null}={}){
 return ({route,body,metadata,accessToken,signal,budget,onSent=()=>{}})=>new Promise((resolve,reject)=>{
  let outgoing,incoming,settled=false,sent=false,size=0;const chunks=[];
  const expired=()=>{try{return signal.aborted||budget.remaining()<=0;}catch{return true;}};
  const failure=()=>new MappingFailure(route?.kind==='save'&&sent||expired()?504:503,route?.kind==='save'&&sent?'fasta_mapping_result_unknown':expired()?'fasta_mapping_timeout':'fasta_mapping_unavailable');
  const finish=(error,result)=>{if(settled)return;settled=true;signal?.removeEventListener('abort',abort);if(error){try{outgoing?.destroy();incoming?.destroy();}catch{}reject(error);}else resolve(result);};
  const abort=()=>finish(failure()),live=()=>{if(settled||expired())throw failure();};
  try{
   route=mappingRoute(route?.path,route?.method);const left=Math.floor(budget.remaining());if(typeof requestImpl!=='function'||!signal||signal.aborted||!Number.isFinite(left)||left<1||left>route.capMs||!/^[1-9]\d{12}$/.test(budget.deadlineUnixMs)||typeof accessToken!=='string'||!/^[A-Za-z0-9._-]{1,8192}$/.test(accessToken))throw Error();
   const headers={Accept:route.kind==='content'?'application/octet-stream':'application/json',Authorization:'Bearer '+accessToken,Origin:'http://127.0.0.1:4206','X-Cyomics-Remaining-Ms':String(left),'X-Cyomics-Deadline-Unix-Ms':budget.deadlineUnixMs};
   if(route.kind==='save'){if(!(body instanceof Uint8Array)||body.length>512)throw Error();metadata=mappingInput(mappingJSON(body,512));headers['Content-Type']='application/json';headers['Content-Length']=String(body.length);}
   else if(body!==undefined)throw Error();
   signal.addEventListener('abort',abort,{once:true});live();outgoing=requestImpl({hostname:'127.0.0.1',port:57430,path:route.path,method:route.method,headers,agent:false},response=>{
    incoming=response;if(settled){response.on('error',()=>{});response.destroy();return;}const invalid=()=>finish(failure());response.on('error',invalid);response.once('aborted',invalid);let ended=false;
    try{
     live();if(!sent||!Array.isArray(response.rawHeaders)||response.rawHeaders.length%2)throw Error();const h=new Map();for(let n=0;n<response.rawHeaders.length;n+=2){const key=response.rawHeaders[n],value=response.rawHeaders[n+1];if(typeof key!=='string'||typeof value!=='string'||! /^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(key)||/[\x00-\x1f\x7f]/.test(value)||h.has(key.toLowerCase())||['location','refresh','set-cookie'].includes(key.toLowerCase()))throw Error();h.set(key.toLowerCase(),value);}
     const status=response.statusCode,binary=route.kind==='content'&&status===200;if(![200,201,400,401,403,404,405,409,413,415,422,429,503,504].includes(status)||status===201&&route.kind!=='save'||response.headers?.['set-cookie']!==undefined)throw Error();
     if(h.get('cache-control')!=='no-store'||h.get('x-content-type-options')!=='nosniff'||h.get('referrer-policy')!=='no-referrer'||![undefined,'identity'].includes(h.get('content-encoding'))||h.has('transfer-encoding')&&(h.get('transfer-encoding')!=='chunked'||h.has('content-length')))throw Error();
     if(binary?h.get('content-type')!=='application/json':!/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(h.get('content-type')||''))throw Error();
     const cap=binary?MAPPING_MAX:[200,201].includes(status)?MAPPING_META_MAX:4096,length=h.get('content-length');if(length!==undefined&&(!/^(?:0|[1-9]\d{0,9})$/.test(length)||Number(length)>cap)||binary&&(!length||Number(length)<1))throw Error();
     if(binary&&(!/^[0-9a-f]{64}$/.test(h.get('x-cyomics-file-sha256')||'')||h.get('content-disposition')!=='attachment; filename="'+mappingFilename(route.id)+'"'))throw Error();
     response.on('data',part=>{if(settled)return;try{live();if(!(part instanceof Uint8Array)||size+part.length>cap)throw Error();size+=part.length;chunks.push(part);}catch{invalid();}});
     response.once('end',()=>{ended=true;void(async()=>{live();if(length!==undefined&&Number(length)!==size)throw Error();const raw=Buffer.concat(chunks,size);
      if(binary){if(sha(raw)!==h.get('x-cyomics-file-sha256'))throw Error();live();finish(null,{status,body:raw,headers:{'Content-Type':'application/json','Content-Length':String(size),'Content-Disposition':'attachment; filename="'+mappingFilename(route.id)+'"','X-Cyomics-File-SHA256':h.get('x-cyomics-file-sha256')}});}
      else{const parsed=mappingJSON(raw),value=[200,201].includes(status)?await mappingMetadata(parsed,route,metadata?{input:metadata}:null,webcrypto):mappingError(status,parsed);live();finish(null,{status,value});}
     })().catch(invalid);});response.once('close',()=>{if(!ended&&!settled)invalid();});
    }catch{invalid();}
   });outgoing.on('error',abort);outgoing.once('close',()=>{if(!settled&&!incoming)abort();});live();sent=true;onSent();outgoing.end(body);
  }catch(error){finish(!sent&&error instanceof MappingFailure?error:failure());}
 });
}
