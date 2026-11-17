import {EventEmitter} from 'node:events';
import {createHash} from 'node:crypto';
import {harness} from './raw-qc-bff-session.mjs';
import {pid,jid,tick,until} from './raw-qc-bff-setup.mjs';
export {pid,jid,tick,until};
export const path=name=>'/api/v1/raw-qc-jobs/'+jid+'/artifacts/'+name+'?project_id='+pid;
export const sha=body=>createHash('sha256').update(body).digest('hex');
export const bytes=Buffer.from('SYNTHETIC artifact\n');
export function headers(body=bytes,name='raw-matrix.tsv'){
 return ['Content-Type','application/octet-stream','Content-Length',String(body.length),
  'Content-Disposition','attachment; filename="'+name+'"','X-Cyomics-File-Sha256',sha(body),
  'Cache-Control','no-store','X-Content-Type-Options','nosniff','Referrer-Policy','no-referrer'];
}
export function close(stream){if(stream.closed)return;stream.closed=true;stream.emit('close');}
export function spy(handler=call=>call.reply(),{ignoreOutDestroy=false,ignoreIncomingDestroy=false,beforeCallback}={}){
 const calls=[];
 const requestImpl=(options,callback)=>{
  const out=new EventEmitter(),call={options,out};calls.push(call);
  out.destroy=()=>{out.destroyed=true;if(!ignoreOutDestroy)close(out);};
  call.response=(status=200,raw=headers())=>{
   const input=new EventEmitter();input.statusCode=status;input.rawHeaders=raw;input.headers={};
   for(let i=0;i<raw.length;i+=2)input.headers[raw[i].toLowerCase()]=raw[i+1];
   input.destroy=()=>{input.destroyed=true;if(!ignoreIncomingDestroy)close(input);};call.input=input;beforeCallback?.(input);callback(input);return input;
  };
  call.reply=(body=bytes,status=200,raw)=>{
   body=Buffer.isBuffer(body)?body:Buffer.from(JSON.stringify(body));
   const selected=raw??(status===200?headers(body):['Content-Type','application/json','Content-Length',String(body.length)]);
   const input=call.response(status,selected);
   if(!input.destroyed){input.emit('data',body);input.complete=true;input.emit('end');close(input);close(out);}
   return input;
  };
  out.end=()=>{call.ended=true;queueMicrotask(()=>handler(call));};return out;
 };
 return {calls,requestImpl};
}
export function wire(req,res,{url,response}){
 if(!url.includes('artifacts'))return;
 req.destroy=()=>{req.destroyed=true;req.socket.destroyed=true;if(!response.ignoreReqClose)close(req);};
 req.once('end',()=>{req.complete=true;if(!response.ignoreReqClose)queueMicrotask(()=>close(req));});
 res.once('finish',()=>{if(!response.ignoreResponseClose)queueMicrotask(()=>close(res));});
 if(response.ignoreResponseClose){res.destroy=()=>{res.destroyCalls++;res.destroyed=true;res.socket.destroyed=true;};}
}
export function session(t,{handler,transport={},useServer=false,flags={},requestConfigured=true}={}){
 const transportSpy=spy(handler,transport);
 const h=harness(t,{useServer,rawQcEnabled:true,rawQcArtifactsEnabled:true,
  ...(requestConfigured?{rawQcArtifactRequest:transportSpy.requestImpl}:{}),...flags,artifactWireSetup:wire});
 return {...h,...transportSpy};
}
