// Synthetic metadata and mock event streams only; no listener or actual service.
import {EventEmitter} from 'node:events';
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {harness} from './raw-qc-bff-session.mjs';
import {preprocessRoute} from '../../preprocess-bridge.mjs';
export {EventEmitter,harness};
export const pid='33333333-3333-4333-8333-333333333333',vid='44444444-4444-4444-8444-444444444444',fid='55555555-5555-4555-8555-555555555555',rid='66666666-6666-4666-8666-666666666666',jid='77777777-7777-4777-8777-777777777777';
const parameters=JSON.parse(await readFile(new URL('../fixtures/preprocess-parameters.reference.json',import.meta.url),'utf8'));
export const payload={request_id:rid,quant_family:'Intensity',parameters};
export const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
export const digest=v=>createHash('sha256').update(canonical(v)).digest('hex');
export const collection='/api/v1/datasets/'+vid+'/preprocessing-jobs?project_id='+pid,lookup=collection+'&request_id='+rid,detail='/api/v1/preprocessing-jobs/'+jid+'?project_id='+pid;
export function job(){return{schemaVersion:2,job_id:jid,request_id:rid,
 request_sha256:digest({project_id:pid,dataset_version_id:vid,request:payload}),binding_sha256:'f'.repeat(64),
 source:{project_id:pid,dataset_version_id:vid,file_id:fid,source_bytes:61,source_sha256:'a'.repeat(64),parser_contract:'protein_groups_txt_v1',config_sha256:'b'.repeat(64),report_sha256:'c'.repeat(64),row_count:2,column_count:3,selected_columns_sha256:'d'.repeat(64)},
 quant_family:payload.quant_family,parameters:structuredClone(parameters),parameters_sha256:digest(parameters),proposal_sha256:'e'.repeat(64),
 status:'queued',created_at:'2026-11-18T01:00:00.000000Z',result:null,revision:1,started_at:null,finished_at:null,observation:'confirmed',error:null,runtime:null,attempt_id:null};}
export function requestSpy(handler=call=>call.reply(job(),call.options.method==='POST'?201:200)){
 const calls=[];
 const requestImpl=(options,callback)=>{
  const out=new EventEmitter(),call={options,out};calls.push(call);out.destroy=()=>{out.destroyed=true;};
  call.response=(status=200,headers=['Content-Type','application/json'])=>{
   const input=new EventEmitter();input.statusCode=status;input.rawHeaders=headers;input.headers={};
   for(let i=0;i<headers.length;i+=2)input.headers[headers[i].toLowerCase()]=headers[i+1];
   input.destroy=()=>{input.destroyed=true;};call.input=input;callback(input);return input;
  };
  call.reply=(value,status=200,headers)=>{const input=call.response(status,headers);if(!input.destroyed){input.emit('data',Buffer.isBuffer(value)?value:Buffer.from(JSON.stringify(value)));input.emit('end');input.emit('close');}return input;};
  out.end=body=>{call.body=body;call.ended=true;queueMicrotask(()=>handler(call));};return out;
 };
 return{calls,requestImpl};
}
export const tick=()=>new Promise(resolve=>setImmediate(resolve));
export async function until(check){for(let i=0;i<100;i++){if(check())return;await tick();}throw Error('mock boundary did not settle');}
export function timerClock(){let mono=1000;const timers=new Map();return{timers,now:()=>mono,advance:ms=>{mono+=ms;},setTimer(fn,ms){const key={fn,ms};timers.set(key,key);return key;},clearTimer:key=>timers.delete(key),fire(){const key=[...timers.values()][0];if(!key)throw Error('No synthetic timer');mono+=key.ms;key.fn();}};}
export const args=(method='GET',options={})=>({route:preprocessRoute(method==='POST'?collection:detail,method),body:method==='POST'?Buffer.from(JSON.stringify(payload)):undefined,accessToken:'SYNTHETIC_ACCESS_ONLY',signal:new AbortController().signal,budget:{remaining:()=>11000,deadlineUnixMs:'1795000000000'},...options});
export function session(t,{flag=true,requestConfigured=true,handler,useServer=false,...options}={}){
 const spy=requestSpy(handler),h=harness(t,{...(flag==='default'?{}:{preprocessingEnabled:flag}),...(requestConfigured?{preprocessingRequest:spy.requestImpl}:{}),useServer,...options});
 return{...h,...spy};
}
