import {SampleDesignFailure,MAX_REQUEST} from './sample-design-contract.mjs';
export function ownsSampleDesignRoute(raw){
  if(typeof raw!=='string')return false;
  try{const path=decodeURIComponent(new URL(raw,'http://127.0.0.1').pathname).replace(/\/{2,}/g,'/');return path.startsWith('/api/v1/datasets/')&&path.includes('sample-designs');}
  catch{return raw.startsWith('/api/v1/datasets/')&&raw.includes('sample-designs');}
}
export function readSampleDesignBody(req,signal,method){
  return new Promise((resolve,reject)=>{
    const chunks=[];let size=0,done=false;const cap=method==='POST'?MAX_REQUEST:0;
    const cleanup=()=>{req.removeListener('data',data);req.removeListener('end',end);signal.removeEventListener('abort',abort);};
    const finish=error=>{if(done)return;done=true;cleanup();if(error){req.pause?.();reject(error);}else resolve(Buffer.concat(chunks,size));};
    const data=chunk=>{if(!(chunk instanceof Uint8Array))return finish(new SampleDesignFailure(400,'invalid_sample_design_request'));if(size+chunk.byteLength>cap)return finish(new SampleDesignFailure(cap?413:400,cap?'sample_design_request_too_large':'invalid_sample_design_request'));size+=chunk.byteLength;chunks.push(chunk);};
    const end=()=>finish(),abort=()=>finish(new SampleDesignFailure(504,'sample_design_timeout')),error=()=>finish(new SampleDesignFailure(400,'invalid_sample_design_request'));
    req.on('data',data);req.once('end',end);req.on('error',error);req.once('close',()=>req.removeListener('error',error));signal.addEventListener('abort',abort,{once:true});if(signal.aborted)abort();
  });
}
