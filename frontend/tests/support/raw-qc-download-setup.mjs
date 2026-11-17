import {createHash,webcrypto} from 'node:crypto';
import {createRawQCClient,createQCTransport,createQCArtifactScope} from '../../raw-qc-client.js';
import {job,first,clone,digest,memory,until,tick,deferred} from './raw-qc-setup.mjs';
export {webcrypto,first,clone,digest,memory,until,tick,deferred,createQCArtifactScope,createRawQCClient,createQCTransport};
export const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
export const payload=(name,family='intensity')=>new Uint8Array([...new TextEncoder().encode('SYNTHETIC QC '+family+' '+name+'\r\n'),0,255]);
export function completed({family='intensity',f=first,requestId,jobId,bodies=new Map()}={}){
 const j=job({family,f,requestId,jobId,status:'succeeded'});
 j.result.artifacts.forEach(a=>{const bytes=bodies.get(a.name)||payload(a.name,family);a.bytes=bytes.byteLength;a.sha256=hash(bytes);});j.result.manifest_sha256=digest(j.result.artifacts);return j;
}
export function binary(j,name,body=payload(name,j.quant_family==='Intensity'?'intensity':'lfq'),options={}){
 const a=j.result.artifacts.find(a=>a.name===name);
 return new Response(body,{status:options.status||200,headers:{'content-type':'application/octet-stream','content-length':String(a.bytes),'content-disposition':'attachment; filename="'+name+'"','x-cyomics-file-sha256':a.sha256,'cache-control':'no-store','x-content-type-options':'nosniff','referrer-policy':'no-referrer',...options.headers}});
}
export function clock(){let now=0,serial=0;const timers=new Map();return{now:()=>now,timers,setTimer(fn,ms){const id=++serial;timers.set(id,{fn,at:now+ms});return id;},clearTimer(id){timers.delete(id);},advance(ms){const end=now+ms;for(;;){const next=[...timers].filter(([,x])=>x.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;timers.delete(next[0]);now=next[1].at;next[1].fn();}now=end;}};}
export function saver(hook=()=>{}){
 const active=new Map(),revoked=[],clicks=[],anchors=[];let serial=0;
 const documentImpl={body:{append(a){anchors.push(a);hook('append');}},createElement(tag){if(tag!=='a')throw Error('Only a download anchor is allowed');hook('createElement');return{style:{},href:'',download:'',click(){hook('click');clicks.push({name:this.download,url:this.href});},removeAttribute(k){this[k]='';},remove(){hook('remove');}};}};
 const urlImpl={createObjectURL(blob){const u='blob:synthetic/'+(++serial);active.set(u,blob);hook('createObjectURL');return u;},revokeObjectURL(u){revoked.push(u);active.delete(u);hook('revokeObjectURL');}};
 return{documentImpl,urlImpl,BlobImpl:Blob,active,revoked,clicks,anchors};
}
export async function setup(t,{family='intensity',f=first,scope=createQCArtifactScope(),enabled=true,storage=memory(),cryptoImpl=webcrypto,save=saver(),timers=clock(),artifactReply=null,metadataReply=null,transport=null,bodies=new Map()}={}){
 let live=true,currentJob=null;const calls=[],denials=[];
 const fetchImpl=(path,o)=>{calls.push({path,o});if(path.includes('/artifacts/'))return artifactReply?artifactReply(currentJob,path,o):binary(currentJob,path.split('/artifacts/')[1].split('?')[0],bodies.get(path.split('/artifacts/')[1].split('?')[0]));if(o.method==='POST'){const b=JSON.parse(o.body);currentJob=completed({family,f,requestId:b.request_id,bodies,jobId:family==='lfq'?'99999999-9999-4999-8999-999999999999':undefined});}return metadataReply?metadataReply(currentJob,path,o):Response.json(currentJob,{status:200});};
 const tx=transport||createQCTransport({projectId:f.file.project_id,datasetVersionId:f.dto.datasetVersion.dataset_version_id,fetchImpl,isCurrent:()=>live,getOptions:signal=>({signal,credentials:'same-origin',cache:'no-store',redirect:'error',headers:{'X-Cyomics-Account-Context':'Q'.repeat(22),Authorization:'do-not-forward','X-Other':'no'}}),cryptoImpl:webcrypto,storage,rawQcArtifactsEnabled:enabled,artifactScope:scope});
 const c=createRawQCClient({fullSource:{file:f.file,importDTO:f.dto},family,transport:tx,cryptoImpl,onDenied:r=>denials.push(r),...save,...timers});await c.ready;const stop=c.attach();t.after(()=>{stop();c.dispose();});
 return{c,stop,calls,denials,save,timers,scope,storage,transport:tx,fetchImpl,lose(){live=false;},job:()=>currentJob,async start(){return c.start();},downloads:()=>calls.filter(c=>c.path.includes('/artifacts/'))};
}
