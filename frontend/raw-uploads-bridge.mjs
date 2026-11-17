import http from 'node:http';
import {createHash} from 'node:crypto';
import {strictProjectJSON} from './projects-bridge.mjs';

export class UploadFailure extends Error {
  constructor(status=503,code='uploads_unavailable'){super(code);this.status=status;this.code=code;}
}
const bad=(status,code)=>{throw new UploadFailure(status,code);};
export const MAX_UPLOAD_BYTES=26214400;
const uuid='[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
const patterns=[['save','POST',new RegExp(`^/api/v1/projects/(${uuid})/raw-uploads$`)],
  ['receipt','GET',new RegExp(`^/api/v1/projects/(${uuid})/raw-uploads/(${uuid})$`)],
  ['list','GET',new RegExp(`^/api/v1/projects/(${uuid})/files$`)],
  ['content','GET',new RegExp(`^/api/v1/projects/(${uuid})/files/(${uuid})/content$`)]];
export function ownsUploadRoute(raw){
  try {const path=decodeURIComponent(new URL(raw,'http://127.0.0.1').pathname);return path.startsWith('/api/v1/projects/')&&path.split('/').some(p=>['files','raw-uploads'].includes(p));}
  catch{return typeof raw==='string'&&/\/(?:files|raw-uploads)(?:\/|\?|$)/.test(raw);}
}
export function uploadRoute(raw,method){
  if(typeof raw!=='string'||raw.includes('?'))bad(400,'invalid_upload_query');
  for(const [kind,expected,re] of patterns){const match=re.exec(raw);if(match){if(method!==expected)bad(405,'method_not_allowed');return{kind,method,path:raw,project_id:match[1],id:match[2]??null,long:kind==='save'||kind==='content'};}}
  bad(404,'file_not_found');
}
const exact=(v,keys)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join(',')===[...keys].sort().join(',');
const canonicalUUID=v=>typeof v==='string'&&new RegExp(`^${uuid}$`).test(v);
const validName=v=>typeof v==='string'&&Buffer.from(v).toString('utf8')===v&&Buffer.byteLength(v)>=1&&Buffer.byteLength(v)<=255&&/\.txt$/i.test(v)&&!/[\u0000-\u001f\u007f-\u009f/\\]/u.test(v);
const digest=v=>createHash('sha256').update(v).digest('hex');
export function uploadHeaders(single){
  if(single('content-type')!=='application/octet-stream')bad(415,'unsupported_media_type');
  if(![undefined,'identity'].includes(single('content-encoding')))bad(415,'unsupported_media_type');
  const id=single('idempotency-key'),encoded=single('x-cyomics-filename'),size=single('x-cyomics-file-bytes'),sha=single('x-cyomics-file-sha256');
  if(!canonicalUUID(id)||typeof encoded!=='string'||encoded.length>340||!(/^[A-Za-z0-9_-]+$/).test(encoded))bad(400,'invalid_upload');
  let name;try{const bytes=Buffer.from(encoded,'base64url');if(bytes.toString('base64url')!==encoded)throw Error();name=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(bytes);}catch{bad(400,'invalid_upload');}
  if(!validName(name)||single('x-cyomics-file-format')!=='proteinGroups_txt'||typeof sha!=='string'||!(/^[0-9a-f]{64}$/).test(sha))bad(400,'invalid_upload');
  if(typeof size!=='string'||!(/^[1-9]\d{0,7}$/).test(size)||Number(size)>MAX_UPLOAD_BYTES)bad(413,'upload_too_large');
  return{request_id:id,filename:name,format:'proteinGroups_txt',bytes:Number(size),sha256:sha,
    headers:{'Idempotency-Key':id,'X-Cyomics-Filename':encoded,'X-Cyomics-File-Format':'proteinGroups_txt','X-Cyomics-File-Bytes':size,'X-Cyomics-File-Sha256':sha}};
}
export function readUploadBody(req,signal,cap){
  return new Promise((resolve,reject)=>{
    let count=0,done=false;const chunks=[];
    const cleanup=()=>{req.removeListener('data',data);req.removeListener('end',end);signal.removeEventListener('abort',abort);};
    const finish=(error)=>{if(done)return;done=true;cleanup();if(error){req.pause?.();reject(error);}else resolve(Buffer.concat(chunks,count));};
    const data=chunk=>{if(!(chunk instanceof Uint8Array))return finish(new UploadFailure(400,'invalid_upload'));if(count+chunk.byteLength>cap)return finish(new UploadFailure(cap?413:400,cap?'upload_too_large':'invalid_upload_query'));count+=chunk.byteLength;chunks.push(chunk);};
    const end=()=>finish(),abort=()=>finish(new UploadFailure()),error=()=>finish(new UploadFailure(400,'invalid_upload'));
    req.on('data',data);req.once('end',end);req.on('error',error);req.once('close',()=>req.removeListener('error',error));signal.addEventListener('abort',abort,{once:true});if(signal.aborted)abort();
  });
}
const errors=new Map([[400,['invalid_upload','upload_checksum_mismatch','invalid_upload_query']],[401,['unauthenticated']],[403,['auth_origin_rejected']],[404,['project_not_found','file_not_found']],[405,['method_not_allowed']],[409,['project_context_changed','upload_request_conflict']],[413,['upload_too_large','upload_quota_exceeded']],[415,['unsupported_media_type']],[429,['upload_rate_limited','upload_busy']],[503,['uploads_unavailable']],[504,['upload_result_unknown']]]);
function file(value,pid){
  if(!exact(value,['file_id','project_id','request_id','filename','format','bytes','sha256','status','createdAt'])||!canonicalUUID(value.file_id)||!canonicalUUID(value.request_id)||value.project_id!==pid||!validName(value.filename)||value.format!=='proteinGroups_txt'||value.status!=='stored'||!Number.isInteger(value.bytes)||value.bytes<1||value.bytes>MAX_UPLOAD_BYTES||!(/^[0-9a-f]{64}$/).test(value.sha256)||typeof value.createdAt!=='string'||!(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/).test(value.createdAt)||!Number.isFinite(Date.parse(value.createdAt))||new Date(Date.parse(value.createdAt)).toISOString().slice(0,19)!==value.createdAt.slice(0,19))throw Error('Invalid upload response');
}
function publicValue(route,status,value){
  if(errors.has(status)){if(!exact(value,['error'])||!exact(value.error,['code'])||!errors.get(status).includes(value.error.code))throw Error();return value;}
  if(route.kind==='list'){if(status!==200||!exact(value,['items','nextCursor'])||value.nextCursor!==null||!Array.isArray(value.items)||value.items.length>4)throw Error();value.items.forEach(v=>file(v,route.project_id));if(new Set(value.items.map(v=>v.file_id)).size!==value.items.length)throw Error();}
  else{if(!exact(value,['file']))throw Error();file(value.file,route.project_id);if(route.kind==='receipt'&&value.file.request_id!==route.id)throw Error();}
  return value;
}
export function createRawUploadBridge({requestImpl=http.request}={}){
  return ({route,body,metadata,accessToken,signal,budget,onSent=()=>{}})=>new Promise((resolve,reject)=>{
    let outgoing,incoming,settled=false,sent=false;const chunks=[];let size=0;
    const failure=()=>new UploadFailure(route.kind==='save'&&sent?504:503,route.kind==='save'&&sent?'upload_result_unknown':'uploads_unavailable');
    const finish=(error,value)=>{if(settled)return;settled=true;signal.removeEventListener('abort',abort);if(error){outgoing?.destroy();incoming?.destroy();reject(error);}else resolve(value);};
    const abort=()=>finish(failure());
    try{
      route=uploadRoute(route.path,route.method);
      const left=Math.floor(budget.remaining());
      if(signal.aborted||!Number.isFinite(left)||left<1||left>(route.long?55000:12000)||!(/^[1-9]\d{12}$/).test(budget.deadlineUnixMs)||typeof accessToken!=='string'||!(/^[A-Za-z0-9._-]{1,8192}$/).test(accessToken))throw Error();
      const headers={Accept:route.kind==='content'?'application/octet-stream':'application/json',Authorization:'Bearer '+accessToken,'X-Cyomics-Remaining-Ms':String(left),'X-Cyomics-Deadline-Unix-Ms':budget.deadlineUnixMs};
      if(route.kind==='save'){
        if(!metadata||body.length!==metadata.bytes||digest(body)!==metadata.sha256)bad(400,'upload_checksum_mismatch');
        const safe=uploadHeaders(k=>({'content-type':'application/octet-stream','idempotency-key':metadata.request_id,'x-cyomics-filename':Buffer.from(metadata.filename,'utf8').toString('base64url'),'x-cyomics-file-format':metadata.format,'x-cyomics-file-bytes':String(metadata.bytes),'x-cyomics-file-sha256':metadata.sha256})[k]);
        Object.assign(headers,safe.headers,{'Content-Type':'application/octet-stream','Content-Length':String(body.length)});
      }
      outgoing=requestImpl({hostname:'127.0.0.1',port:57430,method:route.method,path:route.path,headers,agent:false},response=>{
        incoming=response;if(settled){response.on('error',()=>{});response.destroy();return;}
        const invalid=()=>finish(failure());response.on('error',invalid);response.once('aborted',invalid);
        try{
          if(response.headers?.['set-cookie']!==undefined)throw Error();
          const status=response.statusCode,isError=errors.has(status),binary=route.kind==='content'&&!isError;
          if(!isError&&!(route.kind==='save'?[200,201]:[200]).includes(status))throw Error();
          const one=name=>{const v=response.headers[name];if(typeof v!=='string')throw Error();if(Array.isArray(response.rawHeaders)){let n=0;for(let i=0;i<response.rawHeaders.length;i+=2)if(response.rawHeaders[i].toLowerCase()===name)n++;if(n!==1)throw Error();}return v;};
          const media=one('content-type');if(binary?media!=='application/octet-stream':!(/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i).test(media))throw Error();
          const cap=binary?MAX_UPLOAD_BYTES:isError?4096:16384;
          const length=response.headers['content-length'];if(length!==undefined&&(!(/^\d+$/).test(one('content-length'))||Number(length)>cap))throw Error();
          let sha,disposition;
          if(binary){if(length===undefined||Number(length)<1)throw Error();sha=one('x-cyomics-file-sha256');disposition=one('content-disposition');if(!(/^[0-9a-f]{64}$/).test(sha)||!disposition.startsWith('attachment;')||/[\r\n]/.test(disposition))throw Error();for(const [k,v]of [['cache-control','no-store'],['x-content-type-options','nosniff'],['referrer-policy','no-referrer']])if(one(k)!==v)throw Error();}
          response.on('data',chunk=>{if(settled)return;if(!(chunk instanceof Uint8Array)||size+chunk.byteLength>cap)return invalid();size+=chunk.byteLength;chunks.push(chunk);});
          response.once('end',()=>{if(settled)return;try{
            if(signal.aborted||budget.remaining()<=0||length!==undefined&&Number(length)!==size)throw Error();const bytes=Buffer.concat(chunks,size);
            if(binary){if(digest(bytes)!==sha)throw Error();finish(undefined,{status,body:bytes,headers:{'Content-Type':'application/octet-stream','Content-Length':String(size),'Content-Disposition':disposition,'X-Cyomics-File-Sha256':sha}});}
            else{const value=publicValue(route,status,strictProjectJSON(bytes));if(route.kind==='save'&&!isError&&Object.entries(metadata).some(([k,v])=>k!=='headers'&&value.file[k]!==v))throw Error();finish(undefined,{status,value});}
          }catch{invalid();}});
        }catch{invalid();}
      });
      outgoing.once('error',()=>finish(failure()));signal.addEventListener('abort',abort,{once:true});if(signal.aborted)return abort();sent=true;onSent();outgoing.end(body);
    }catch(error){finish(error instanceof UploadFailure?error:failure());}
  });
}
