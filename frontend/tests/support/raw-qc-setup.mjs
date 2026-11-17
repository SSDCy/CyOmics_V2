import {readFile} from 'node:fs/promises';
import {createHash,webcrypto} from 'node:crypto';
import {first,second,tick,until,deferred,dom,server} from './contrast-mount-setup.mjs';
import {createRawTransport,createPrivateTxtController,mountPrivateTxt} from '../../private-txt-upload.js';
import {createRawQCClient,createQCTransport} from '../../raw-qc-client.js';
export {first,second,tick,until,deferred,dom,server,webcrypto,createRawQCClient};
const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
export const digest=v=>createHash('sha256').update(canonical(v)).digest('hex');
export const clone=structuredClone;
export const synthetic=JSON.parse(await readFile(new URL('../fixtures/raw-qc.synthetic.json',import.meta.url),'utf8'));
export const rid='77777777-7777-4777-8777-777777777777',jid='88888888-8888-4888-8888-888888888888';
export const sourceFor=(f=first,family='intensity')=>{const v=f.dto.datasetVersion,d=f.dto.importReport;return{project_id:v.project_id,dataset_version_id:v.dataset_version_id,file_id:v.file_id,source_bytes:f.file.bytes,source_sha256:f.file.sha256,parser_contract:v.parser_contract,config_sha256:v.config_sha256,report_sha256:digest(d),row_count:d.report.row_count,column_count:d.report.column_count,selected_columns_sha256:digest(d.sample_columns.filter(c=>c.quant_family===(family==='intensity'?'Intensity':'LFQ intensity')))};};
export function job({f=first,family='intensity',requestId=rid,jobId=jid,status='queued',revision=status==='queued'?1:status==='running'?2:3}={}){
 const source=sourceFor(f,family),execution=clone(synthetic.execution),quant_family=family==='intensity'?'Intensity':'LFQ intensity',request_sha256=digest({schemaVersion:1,project_id:source.project_id,dataset_version_id:source.dataset_version_id,quant_family,params:{}});
 const j={schemaVersion:1,job_id:jobId,request_id:requestId,request_sha256,binding_sha256:digest({contract:'raw_qc_job_v0.1',request_sha256,source,execution,quant_family,params:{}}),source,quant_family,params:{},execution,status,revision,created_at:'2026-11-18T01:00:00.000000Z',started_at:status==='queued'?null:'2026-11-18T01:00:01.000000Z',finished_at:['failed','succeeded'].includes(status)?'2026-11-18T01:00:03.000000Z':null,observation:'confirmed',error:status==='failed'?{code:'runner_failed'}:null,result:null};
 if(status==='succeeded'){const artifacts=synthetic.names.map(name=>({name,bytes:42,sha256:'b'.repeat(64),media_type:name==='R-session-info.txt'?'text/plain; charset=utf-8':'text/tab-separated-values; charset=utf-8'}));j.result={schemaVersion:1,job_id:jobId,binding_sha256:j.binding_sha256,execution:clone(execution),artifacts,manifest_sha256:digest(artifacts),verified_at:'2026-11-18T01:00:02.000000Z'};}return j;
}
export function memory(){const values=new Map();return{values,getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};}
export function clock(){let time=0,id=0;const timers=new Map();return{timers,now:()=>time,setTimer(fn,ms){const n=++id;timers.set(n,{fn,ms});return n;},clearTimer:n=>timers.delete(n),fire(ms){const pair=[...timers].find(([,v])=>v.ms===ms);if(!pair)throw Error('No synthetic timer '+ms);timers.delete(pair[0]);time+=ms;pair[1].fn();}};}
export async function client(t,{f=first,family='intensity',storage=memory(),fetchImpl,nonce='Q'.repeat(22),cryptoImpl=webcrypto,clientOptions={}}={}){
 let live=true;const calls=[],denials=[];let currentJob=null;
 const fetcher=fetchImpl||((path,o)=>{if(o.method==='POST'){const b=JSON.parse(o.body);currentJob=job({f,family,requestId:b.request_id});return Response.json(currentJob,{status:202});}return Response.json(currentJob);});
 const transport=createQCTransport({projectId:f.file.project_id,datasetVersionId:f.dto.datasetVersion.dataset_version_id,cryptoImpl,storage,isCurrent:()=>live,getOptions:signal=>({method:'GET',signal,credentials:'same-origin',cache:'no-store',redirect:'error',headers:{'X-Cyomics-Account-Context':nonce,Authorization:'synthetic-must-not-forward','X-Other':'no'}}),fetchImpl:(path,o)=>{calls.push({path,o});return fetcher(path,o);}});
 const c=createRawQCClient({fullSource:{file:f.file,importDTO:f.dto},family,transport,cryptoImpl,onDenied:v=>denials.push(v),...clientOptions});t.after(()=>c.dispose());await c.ready;return{c,calls,storage,transport,denials,lose(){live=false;}};
}
export async function mounted(t,{enabled=true,storage=memory(),values=[first],fetchQC}={}){
 const backend=server(values),jobs=new Map();
 backend.setIntercept((path,o)=>{if(!path.includes('raw-qc-jobs'))return null;if(fetchQC)return fetchQC(path,o);if(o.method==='POST'){const b=JSON.parse(o.body),family=b.quant_family,f=values.find(f=>path.includes(f.dto.datasetVersion.dataset_version_id)),j=job({f,family,requestId:b.request_id,jobId:family==='lfq'?'99999999-9999-4999-8999-999999999999':jid});jobs.set(j.job_id,j);return Response.json(j,{status:202});}const id=/raw-qc-jobs\/([0-9a-f-]{36})/.exec(path)?.[1],j=jobs.get(id);return j?Response.json(j):Response.json({error:{code:'raw_qc_job_not_found'}},{status:404});});
 const transport=createRawTransport({projectId:first.file.project_id,fetchImpl:backend.fetchImpl,getOptions:signal=>({method:'GET',signal,credentials:'same-origin',cache:'no-store',redirect:'error',headers:{'X-Cyomics-Account-Context':'Q'.repeat(22)}}),isCurrent:backend.isCurrent,rawQcEnabled:enabled,rawQcStorage:storage,sampleDesignsEnabled:true,cryptoImpl:webcrypto});
 const model=createPrivateTxtController({projectId:first.file.project_id,transport,cryptoImpl:webcrypto});let d=dom(),stop=mountPrivateTxt(d.root,model);const cleanup=()=>{stop();model.dispose();};t.after(cleanup);await until(()=>values.every(f=>model.importReport(f.file.file_id)?.status().phase==='ready'));
 return{backend,model,storage,jobs,get d(){return d;},host:(id=first.file.file_id)=>model.rawQC(id),cleanup,remount(en=false){stop();d=dom();stop=mountPrivateTxt(d.root,model,en?(_,english)=>english:zh=>zh);},qcCalls:()=>backend.calls.filter(c=>c.path.includes('raw-qc-jobs'))};
}
