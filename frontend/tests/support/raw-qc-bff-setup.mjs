import {EventEmitter} from 'node:events';
import {job,first,rid,jid,clone,tick,until,deferred} from './raw-qc-setup.mjs';
import {rawQCRoute} from '../../raw-qc-bridge.mjs';
import {harness} from './raw-qc-bff-session.mjs';
export {EventEmitter,job,first,rid,jid,clone,tick,until,deferred};
export const pid=first.file.project_id,vid=first.dto.datasetVersion.dataset_version_id;
export const collection='/api/v1/datasets/'+vid+'/raw-qc-jobs?project_id='+pid,lookup=collection+'&request_id='+rid,detail='/api/v1/raw-qc-jobs/'+jid+'?project_id='+pid;
export const payload={schemaVersion:1,request_id:rid,quant_family:'intensity',params:{}};
export function requestSpy(handler=call=>call.reply(job(),call.options.method==='POST'?202:200)){
 const calls=[];
 const requestImpl=(options,callback)=>{const out=new EventEmitter(),call={options,out};calls.push(call);out.destroy=()=>{out.destroyed=true;};
  call.response=(status=200,headers=['Content-Type','application/json'])=>{const input=new EventEmitter();input.statusCode=status;input.rawHeaders=headers;input.headers={};input.destroy=()=>{input.destroyed=true;};call.input=input;callback(input);return input;};
  call.reply=(value,status=200,headers)=>{const input=call.response(status,headers);if(!input.destroyed){input.emit('data',Buffer.isBuffer(value)?value:Buffer.from(JSON.stringify(value)));input.complete=true;input.emit('end');input.emit('close');}return input;};
  out.end=body=>{call.body=body;call.ended=true;queueMicrotask(()=>handler(call));};return out;};return{calls,requestImpl};
}
export function timerClock(){let mono=1000;const timers=new Map();return{timers,now:()=>mono,advance:ms=>{mono+=ms;},setTimer(fn,ms){const key={fn,ms};timers.set(key,key);return key;},clearTimer:k=>timers.delete(k),fire(){const item=[...timers.values()][0];if(!item)throw Error('No timer');mono+=item.ms;item.fn();}};}
export const args=(method='GET',options={})=>({route:rawQCRoute(method==='POST'?collection:detail,method),body:method==='POST'?Buffer.from(JSON.stringify(payload)):undefined,accessToken:'SYNTHETIC_ACCESS_ONLY',signal:new AbortController().signal,budget:{remaining:()=>11000,deadlineUnixMs:'1795000000000'},...options});
export function session(t,{flag=true,requestConfigured=true,handler,useServer=false}={}){const spy=requestSpy(handler),h=harness(t,{...(flag==='default'?{}:{rawQcEnabled:flag}),...(requestConfigured?{rawQcRequest:spy.requestImpl}:{}),useServer});return{...h,...spy};}
