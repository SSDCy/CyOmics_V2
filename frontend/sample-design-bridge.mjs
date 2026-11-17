import {SampleDesignFailure,sampleDesignRoute,sampleDesignInput,strictJSON,validateSampleDesignResponse,validateSampleDesignError,sampleDesignErrors,MAX_RESPONSE,MAX_ERROR,validateSource,freeze} from './sample-design-contract.mjs';
// Disabled until the host explicitly injects a reviewed Node request implementation.
export function createSampleDesignBridge({requestImpl=null,cryptoImpl=globalThis.crypto,setTimer=setTimeout,clearTimer=clearTimeout,allowSourceFreeGetErrors=false}={}){
  return ({route,body,source,accessToken,signal,budget,onSent=()=>{}})=>new Promise((resolve,reject)=>{
    let outgoing,incoming,sent=false,settled=false,timer,size=0;const chunks=[];
    const failure=()=>{let expired=signal?.aborted===true;try{expired ||= budget.remaining()<=0;}catch{expired=true;}return new SampleDesignFailure(sent&&route?.method==='POST'||expired?504:503,sent&&route?.method==='POST'?'sample_design_result_unknown':expired?'sample_design_timeout':'sample_design_unavailable');};
    const finish=(error,result)=>{if(settled)return;settled=true;clearTimer(timer);signal?.removeEventListener('abort',abort);if(error){try{outgoing?.destroy();incoming?.destroy();}catch{}reject(error);}else resolve(result);};
    const abort=()=>finish(failure());
    try{
      route=sampleDesignRoute(route.path,route.method);
      // Internal denial-only seam: no source-free success DTO or POST is allowed.
      const sourceFreeGetErrors=allowSourceFreeGetErrors===true&&route.method==='GET'&&source==null;
      if(!sourceFreeGetErrors){try{source=freeze(structuredClone(source));validateSource(source);}catch{throw new SampleDesignFailure(503,'sample_design_persisted_invalid');}if(source.dataset_version_id!==route.dataset_version_id)throw new SampleDesignFailure(422,'invalid_sample_design');}let request=null;
      if(route.method==='POST')request=sampleDesignInput(body,route,source);else if(body!==undefined)throw new SampleDesignFailure(400,'invalid_sample_design_request');
      const remaining=Math.floor(budget.remaining());
      if(typeof requestImpl!=='function'||signal.aborted||!Number.isFinite(remaining)||remaining<1||remaining>(route.method==='POST'?55000:12000)||!(/^[1-9]\d{12}$/).test(budget.deadlineUnixMs)||typeof accessToken!=='string'||!(/^[A-Za-z0-9._-]{1,8192}$/).test(accessToken))throw Error();
      const headers={Accept:'application/json',Authorization:'Bearer '+accessToken,Origin:'http://127.0.0.1:4206','X-Cyomics-Remaining-Ms':String(remaining),'X-Cyomics-Deadline-Unix-Ms':budget.deadlineUnixMs};
      if(request)Object.assign(headers,{'Content-Type':'application/json','Content-Length':String(body.byteLength),'X-Cyomics-Request':'1'});
      timer=setTimer(abort,remaining);
      outgoing=requestImpl({hostname:'127.0.0.1',port:57430,path:route.path,method:route.method,headers,agent:false},response=>{
        incoming=response;if(settled){response.on('error',()=>{});response.destroy();return;}
        const invalid=()=>{const transportFailure=failure();finish(transportFailure.status===504?transportFailure:new SampleDesignFailure(503,'sample_design_persisted_invalid'));};response.on('error',abort);response.once('aborted',abort);
        // Close before attaching a data listener: zero success-body bytes are consumed
        // or released without a verified source. Timing/cancellation still wins.
        if(sourceFreeGetErrors&&response.statusCode===200){const fault=failure();finish(fault.status===504?fault:new SampleDesignFailure(503,'sample_design_unavailable'));return;}
        try{
          if(!Array.isArray(response.rawHeaders)||response.rawHeaders.length%2)throw Error();const h=new Map();
          for(let i=0;i<response.rawHeaders.length;i+=2){const k=response.rawHeaders[i],v=response.rawHeaders[i+1];if(typeof k!=='string'||typeof v!=='string')throw Error();const name=k.toLowerCase();if(['location','refresh','set-cookie'].includes(name))throw Error();if(['content-type','content-length','content-encoding'].includes(name)){if(h.has(name))throw Error();h.set(name,v);}}
          if(response.headers?.['set-cookie']!==undefined||!/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(h.get('content-type')||'')||![undefined,'identity'].includes(h.get('content-encoding')))throw Error();
          const status=response.statusCode;if(status!==200&&!sampleDesignErrors.has(status))throw Error();const cap=status===200?MAX_RESPONSE:MAX_ERROR,length=h.get('content-length');
          if(length!==undefined&&(!/^\d{1,10}$/.test(length)||Number(length)>cap))throw Error();
          response.on('data',chunk=>{if(settled)return;try{if(signal.aborted||budget.remaining()<=0||!(chunk instanceof Uint8Array)||size+chunk.byteLength>cap)return invalid();size+=chunk.byteLength;chunks.push(chunk);}catch{invalid();}});
          response.once('end',async()=>{if(settled)return;try{if(signal.aborted||budget.remaining()<=0||length!==undefined&&Number(length)!==size)throw Error();const parsed=strictJSON(Buffer.concat(chunks,size));const value=status===200?await validateSampleDesignResponse(parsed,route,source,{request,cryptoImpl}):validateSampleDesignError(status,parsed);if(signal.aborted||budget.remaining()<=0)throw Error();finish(undefined,{status,value});}catch{invalid();}});
          // end starts async digest validation; close after a clean end is normal.
          response.once('close',()=>{if(!response.complete&&!response.readableEnded&&!settled)invalid();});
        }catch{invalid();}
      });
      outgoing.on('error',abort);outgoing.once('close',()=>{if(!settled&&!incoming)abort();});
      if(settled){outgoing.destroy();return;}signal.addEventListener('abort',abort,{once:true});if(signal.aborted||budget.remaining()<=0)return abort();
      sent=true;onSent();outgoing.end(body);
    }catch(error){finish(error instanceof SampleDesignFailure&&!sent?error:failure());}
  });
}
