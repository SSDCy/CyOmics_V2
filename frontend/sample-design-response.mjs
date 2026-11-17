import {performance} from 'node:perf_hooks';
import {sendRawUploadResponse} from './raw-upload-response.mjs';
import {SampleDesignFailure,MAX_RESPONSE,MAX_ERROR} from './sample-design-contract.mjs';

// Reuse the reviewed byte pump, not the raw-file protocol/validation. It retains
// backpressure/finish/deadline checks for any finite Buffer; binary code is unchanged.
export async function sendSampleDesignJSONResponse(req,res,{status,value,signal,checkLive,remaining,onController,setTimer=setTimeout,clearTimer=clearTimeout,now=()=>performance.now()}){
  let body;
  try{body=Buffer.from(JSON.stringify(value));if(body.length>(status===200?MAX_RESPONSE:MAX_ERROR))throw Error();}
  catch{throw new SampleDesignFailure(503,status===200?'sample_design_persisted_invalid':'sample_design_unavailable');}
  // Pre-admission public failures have no session operation; still bound their
  // small output to 12s and connection lifetime. Private responses use caller's
  // original op, deadline and identity fence with no replacement timer.
  const local=signal?null:new AbortController(),activeSignal=signal??local.signal;
  let timer,last,started,left;
  const abort=()=>local?.abort();
  if(local){
    started=last=now();
    if(!Number.isFinite(started))throw new SampleDesignFailure();
    left=remaining?Math.min(12000,remaining()):12000;
    if(!Number.isFinite(left)||left<=0)throw new SampleDesignFailure(504,'sample_design_timeout');
    onController?.(local);
    req.once('aborted',abort);res.once('close',abort);timer=setTimer(abort,Math.max(1,Math.ceil(left)));
  }
  const guard=()=>{
    if(local){const current=now();if(!Number.isFinite(current)||current<last||current-started>=left||remaining&&remaining()<=0||req.aborted||req.socket?.destroyed)throw new SampleDesignFailure(504,'sample_design_timeout');last=current;}
    checkLive?.();
  };
  try{
    await sendRawUploadResponse(res,{status,headers:{'Content-Type':'application/json; charset=utf-8','Content-Length':String(body.length),
      'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','Connection':'close'},body,signal:activeSignal,checkLive:guard});
  }finally{
    if(local){clearTimer(timer);req.removeListener('aborted',abort);res.removeListener('close',abort);local.abort();}
  }
}
