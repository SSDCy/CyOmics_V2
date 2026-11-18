import {readFile} from 'node:fs/promises';
import {createHash,webcrypto} from 'node:crypto';
import {dom as basicDOM,deferred,flush} from './preprocess-parameters-dom.mjs';
import {createPreprocessOwnerClient,createPreprocessOwnerTransport} from '../../preprocess-owner-client.js';
export {webcrypto,deferred,flush};
export const fixtures=JSON.parse(await readFile(new URL('../fixtures/contrast-mount-report-restore.synthetic.json',import.meta.url),'utf8'));
export const first=fixtures[0],second=fixtures[1];
export const reference=JSON.parse(await readFile(new URL('../fixtures/preprocess-parameters.reference.json',import.meta.url),'utf8'));
export const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
export const digest=v=>createHash('sha256').update(canonical(v)).digest('hex');
export const params=(flags={contaminant:false,reverse:false,site_only:false})=>({...structuredClone(reference),filter_flags:{...flags}});
export const full=f=>({file:structuredClone(f.file),importDTO:structuredClone(f.dto)});
export const sourceFor=(f,family)=>{const v=f.dto.datasetVersion,r=f.dto.importReport;return{project_id:v.project_id,dataset_version_id:v.dataset_version_id,file_id:v.file_id,source_bytes:f.file.bytes,source_sha256:f.file.sha256,parser_contract:v.parser_contract,config_sha256:v.config_sha256,report_sha256:digest(r),row_count:r.report.row_count,column_count:r.report.column_count,selected_columns_sha256:digest(r.sample_columns.filter(c=>c.quant_family===family))};};
export function queued(body,{f=first,id='88888888-8888-4888-8888-888888888888'}={}){return{schemaVersion:1,job_id:id,request_id:body.request_id,request_sha256:digest({project_id:f.file.project_id,dataset_version_id:f.dto.datasetVersion.dataset_version_id,request:body}),binding_sha256:'b'.repeat(64),source:sourceFor(f,body.quant_family),quant_family:body.quant_family,parameters:structuredClone(body.parameters),parameters_sha256:digest(body.parameters),execution_sha256:'e'.repeat(64),status:'queued',created_at:'2026-11-18T01:00:00.000000Z',result:null,computation_performed:false,worker_started:false,execution_registered:false};}
export function memory(){const values=new Map();return{values,getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};}
export function timer(){let time=0,serial=0;const timers=new Map();return{now:()=>time,setTimer(fn,ms){const id=++serial;timers.set(id,{fn,ms});return id;},clearTimer:id=>timers.delete(id),expire(){const pair=[...timers][0];if(!pair)throw Error('No active timeout');time+=pair[1].ms;pair[1].fn();}};}
export const tick=()=>new Promise(resolve=>setImmediate(resolve));
export async function until(fn){const end=Date.now()+1600;while(!fn()&&Date.now()<end)await tick();if(!fn())throw Error('Synthetic transition did not settle');}
export function dom(){const d=basicDOM();const create=d.doc.createElement.bind(d.doc);d.doc.createElement=tag=>{const n=create(tag);n.replaceChildren=function(...nodes){this.textContent='';this.append(...nodes);};return n;};d.root.replaceChildren=function(...nodes){this.textContent='';this.append(...nodes);};return d;}
export function server({f=first,jobs=new Map(),mode='normal',held=null,tamper=null}={}){const calls=[];let currentMode=mode;
  const fetchImpl=(path,options)=>{calls.push({path,options});let value;
    if(options.method==='POST'){const body=JSON.parse(options.body);value=queued(body,{f});jobs.set(body.request_id,value);if(held)return held.promise;if(currentMode==='unknown')return Response.json({error:{code:'preprocessing_submission_unknown'}},{status:504});if(currentMode==='denied')return new Response('synthetic denied',{status:401});}
    else{const url=new URL(path,'https://synthetic.invalid'),rid=url.searchParams.get('request_id');value=rid?jobs.get(rid):[...jobs.values()].find(j=>path.includes(j.job_id));}
    if(!value)return Response.json({error:{code:'preprocessing_job_not_found'}},{status:404});return Response.json(tamper?tamper(structuredClone(value)):value,{status:options.method==='POST'?201:200});};
  return{fetchImpl,calls,jobs,setMode(v){currentMode=v;}};
}
export async function client(t,{f=first,storage=memory(),backend=server({f}),nonce='Q'.repeat(22),featureEnabled=true,contextSignal=null,cryptoImpl=webcrypto,clock=timer()}={}){
  let live=true,uuidCalls=0;const crypto={subtle:cryptoImpl.subtle,randomUUID(){uuidCalls++;return cryptoImpl.randomUUID();}};
  const transport=createPreprocessOwnerTransport({featureEnabled,projectId:f.file.project_id,datasetVersionId:f.dto.datasetVersion.dataset_version_id,storage,fetchImpl:backend.fetchImpl,isCurrent:()=>live,cryptoImpl:crypto,getOptions:signal=>({signal,credentials:'same-origin',cache:'no-store',redirect:'error',body:'MUST_NOT_FORWARD',headers:{'X-Cyomics-Account-Context':nonce,Authorization:'MUST_NOT_FORWARD'}})});
  const denials=[],c=createPreprocessOwnerClient({featureEnabled,fullSource:full(f),transport,isCurrent:()=>live,contextSignal,cryptoImpl:crypto,...clock,onDenied:r=>denials.push(r)});t.after(()=>c.dispose());await c.ready;
  return{c,transport,storage,backend,denials,clock,uuidCalls:()=>uuidCalls,lose(){live=false;},input(family='Intensity',flags){const s=c.parameterSource();return{contextKey:s.contextKey,source:{...s.datasetVersion},quant_family:family,parameters:params(flags)};}};
}
export function reportModel(f=first){let current=f?full(f):null;const listeners=new Set();return{validatedSource:()=>current,status(){if(!current)return{phase:'unavailable'};const r=current.importDTO.importReport;return{phase:'ready',source_verified:true,report_verified:true,report_saved:true,binding:{project_id:current.file.project_id,raw_file_id:current.file.file_id,expected_raw_sha256:current.file.sha256},source_filename:current.file.filename,report_version_id:current.importDTO.datasetVersion.dataset_version_id,report:{...r.report,columns:r.columns}};},subscribe(fn){listeners.add(fn);return()=>listeners.delete(fn);},start(){},check(){},set(f){current=f?full(f):null;for(const fn of [...listeners])fn();}};}
export const bindingFor=f=>({project_id:f.file.project_id,raw_file_id:f.file.file_id,expected_raw_sha256:f.file.sha256});
