import {createHash,webcrypto} from 'node:crypto';
import {fastaRoute,fastaName,fastaDisposition,fastaReply,fastaHash,parseFastaJSON,FastaFailure,FASTA_MAX,FASTA_JSON_MAX} from './private-fasta-contract.mjs';
const fail=(status=503,code='fasta_intake_unavailable')=>{throw new FastaFailure(status,code);};
const sha=body=>createHash('sha256').update(body).digest('hex');
export function fastaUploadHeaders(single){
 if(single('content-type')!=='application/octet-stream'||![undefined,'identity'].includes(single('content-encoding')))fail(415,'unsupported_media_type');
 const request_id=single('idempotency-key'),encoded=single('x-cyomics-filename'),size=single('x-cyomics-file-bytes'),digest=single('x-cyomics-file-sha256');let filename;
 try{if(typeof encoded!=='string'||!/^[A-Za-z0-9_-]{1,340}$/.test(encoded))throw Error();const raw=Buffer.from(encoded,'base64url');if(raw.toString('base64url')!==encoded)throw Error();filename=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(raw);}catch{fail(400,'invalid_fasta_request');}
 if(!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(request_id||'')||!fastaName(filename)||!fastaHash(digest)||single('x-cyomics-file-format')!=='protein_fasta')fail(400,'invalid_fasta_request');
 if(!/^[1-9]\d{0,7}$/.test(size||'')||Number(size)>FASTA_MAX)fail(413,'fasta_too_large');return{request_id,filename,bytes:Number(size),sha256:digest};
}
export function readFastaBody(req,signal,cap){return new Promise((resolve,reject)=>{
 let size=0,done=false;const chunks=[];const cleanup=()=>{req.removeListener('data',data);req.removeListener('end',end);req.removeListener('close',closed);signal.removeEventListener('abort',abort);};
 const finish=error=>{if(done)return;done=true;cleanup();if(error){req.pause?.();reject(error);}else resolve(Buffer.concat(chunks,size));};
 const data=part=>{if(!(part instanceof Uint8Array))return finish(new FastaFailure(400,'invalid_fasta_request'));if(size+part.length>cap)return finish(new FastaFailure(cap?413:400,cap?'fasta_too_large':'invalid_fasta_request'));size+=part.length;chunks.push(part);};
 const end=()=>finish(),closed=()=>finish(new FastaFailure()),abort=()=>finish(new FastaFailure(504,'fasta_intake_timeout')),error=()=>finish(new FastaFailure(400,'invalid_fasta_request'));
 req.on('data',data);req.once('end',end);req.on('error',error);req.once('close',closed);req.once('close',()=>req.removeListener('error',error));signal.addEventListener('abort',abort,{once:true});if(signal.aborted)abort();
});}
export function createFastaBridge({requestImpl=null}={}){
 return ({route,body,metadata,accessToken,signal,budget,onSent=()=>{}})=>new Promise((resolve,reject)=>{
  let outgoing,incoming,settled=false,sent=false,size=0;const chunks=[];
  const expired=()=>{try{return signal.aborted||budget.remaining()<=0;}catch{return true;}};
  const failure=()=>new FastaFailure(route?.kind==='save'&&sent||expired()?504:503,route?.kind==='save'&&sent?'fasta_result_unknown':expired()?'fasta_intake_timeout':'fasta_intake_unavailable');
  const finish=(error,result)=>{if(settled)return;settled=true;signal?.removeEventListener('abort',abort);if(error){try{outgoing?.destroy();incoming?.destroy();}catch{}reject(error);}else resolve(result);};
  const abort=()=>finish(failure()),live=()=>{if(settled||expired())throw failure();};
  try{
   route=fastaRoute(route?.path,route?.method);const left=Math.floor(budget.remaining());if(typeof requestImpl!=='function'||!signal||signal.aborted||!Number.isFinite(left)||left<1||left>route.capMs||!/^[1-9]\d{12}$/.test(budget.deadlineUnixMs)||typeof accessToken!=='string'||!/^[A-Za-z0-9._-]{1,8192}$/.test(accessToken))throw Error();
   const headers={Accept:route.kind==='content'?'application/octet-stream':'application/json',Authorization:'Bearer '+accessToken,Origin:'http://127.0.0.1:4206','X-Cyomics-Remaining-Ms':String(left),'X-Cyomics-Deadline-Unix-Ms':budget.deadlineUnixMs};
   if(route.kind==='save'){
    if(!Buffer.isBuffer(body)||!metadata||body.length!==metadata.bytes||sha(body)!==metadata.sha256)fail(400,'fasta_checksum_mismatch');
    const source={'content-type':'application/octet-stream','idempotency-key':metadata.request_id,'x-cyomics-filename':Buffer.from(metadata.filename,'utf8').toString('base64url'),'x-cyomics-file-bytes':String(metadata.bytes),'x-cyomics-file-sha256':metadata.sha256,'x-cyomics-file-format':'protein_fasta'};fastaUploadHeaders(k=>source[k]);Object.assign(headers,{'Content-Type':'application/octet-stream','Content-Length':String(body.length),'X-Cyomics-Request':'1','Idempotency-Key':metadata.request_id,'X-Cyomics-Filename':source['x-cyomics-filename'],'X-Cyomics-File-Bytes':String(body.length),'X-Cyomics-File-SHA256':metadata.sha256,'X-Cyomics-File-Format':'protein_fasta'});
   }else if(body!==undefined)fail(400,'invalid_fasta_request');
   signal.addEventListener('abort',abort,{once:true});live();outgoing=requestImpl({hostname:'127.0.0.1',port:57430,path:route.path,method:route.method,headers,agent:false},response=>{
    incoming=response;if(settled){response.on('error',()=>{});response.destroy();return;}const invalid=()=>finish(failure());response.on('error',invalid);response.once('aborted',invalid);let ended=false;
    try{
     live();if(!sent||!Array.isArray(response.rawHeaders)||response.rawHeaders.length%2)throw Error();const h=new Map();for(let n=0;n<response.rawHeaders.length;n+=2){const key=response.rawHeaders[n],value=response.rawHeaders[n+1];if(typeof key!=='string'||typeof value!=='string'||! /^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(key)||/[\x00-\x1f\x7f]/.test(value)||h.has(key.toLowerCase())||['location','refresh','set-cookie'].includes(key.toLowerCase()))throw Error();h.set(key.toLowerCase(),value);}
     const status=response.statusCode,binary=route.kind==='content'&&status===200;if(![200,201,400,401,403,404,405,409,413,415,422,429,503,504].includes(status)||status===201&&route.kind!=='save'||response.headers?.['set-cookie']!==undefined)throw Error();
     if(h.get('cache-control')!=='no-store'||h.get('x-content-type-options')!=='nosniff'||h.get('referrer-policy')!=='no-referrer'||![undefined,'identity'].includes(h.get('content-encoding'))||h.has('transfer-encoding')&&(h.get('transfer-encoding')!=='chunked'||h.has('content-length')))throw Error();
     if(binary?h.get('content-type')!=='application/octet-stream':!/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(h.get('content-type')||''))throw Error();
     const cap=binary?FASTA_MAX:[200,201].includes(status)?FASTA_JSON_MAX:4096,length=h.get('content-length');if(length!==undefined&&(!/^(?:0|[1-9]\d{0,9})$/.test(length)||Number(length)>cap)||binary&&(!length||Number(length)<1))throw Error();
     let filename;if(binary){const d=h.get('content-disposition'),prefix="attachment; filename=\"source.fasta\"; filename*=UTF-8''";if(!d?.startsWith(prefix)||!fastaHash(h.get('x-cyomics-file-sha256')))throw Error();filename=decodeURIComponent(d.slice(prefix.length));if(fastaDisposition(filename)!==d)throw Error();}
     response.on('data',part=>{if(settled)return;try{live();if(!(part instanceof Uint8Array)||size+part.length>cap)throw Error();size+=part.length;chunks.push(part);}catch{invalid();}});
     response.once('end',()=>{ended=true;void(async()=>{live();if(length!==undefined&&Number(length)!==size)throw Error();const raw=Buffer.concat(chunks,size);
      if(binary){if(sha(raw)!==h.get('x-cyomics-file-sha256'))throw Error();live();finish(null,{status,body:raw,headers:{'Content-Type':'application/octet-stream','Content-Length':String(size),'Content-Disposition':fastaDisposition(filename),'X-Cyomics-File-SHA256':h.get('x-cyomics-file-sha256')}});}
      else{const value=await fastaReply(route,status,parseFastaJSON(raw),metadata,webcrypto);live();finish(null,{status,value});}
     })().catch(invalid);});response.once('close',()=>{if(!ended&&!settled)invalid();});
    }catch{invalid();}
   });outgoing.on('error',abort);outgoing.once('close',()=>{if(!settled&&!incoming)abort();});live();sent=true;onSent();outgoing.end(body);
  }catch(error){finish(!sent&&error instanceof FastaFailure?error:failure());}
 });
}
