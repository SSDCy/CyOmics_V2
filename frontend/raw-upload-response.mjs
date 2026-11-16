import {UploadFailure} from './raw-uploads-bridge.mjs';

// Keep the caller's deadline, identity fence and concurrency slot until finish.
export function sendRawUploadResponse(res,{status,headers,body,signal,checkLive}){
  return new Promise((resolve,reject)=>{
    let offset=0,ending=false,waiting=false,settled=false;
    const unavailable=()=>new UploadFailure(503,'uploads_unavailable');
    const cleanup=()=>{
      res.removeListener('drain',drain);res.removeListener('finish',finish);
      res.removeListener('error',failed);res.removeListener('close',failed);
      signal.removeEventListener('abort',failed);
    };
    const settle=error=>{
      if(settled)return;settled=true;cleanup();
      if(error)reject(error);else resolve();
    };
    const guard=()=>{
      checkLive();
      if(signal.aborted||res.destroyed||res.socket?.destroyed||!ending&&res.writableEnded)throw unavailable();
    };
    const failed=()=>settle(unavailable());
    const finish=()=>{
      if(settled)return;
      try{guard();if(!ending||!res.writableFinished)throw unavailable();settle();}
      catch(error){settle(error);}
    };
    const pump=()=>{
      if(settled)return;
      try{
        guard();
        while(offset<body.length){
          guard();const end=Math.min(offset+65536,body.length);
          const accepted=res.write(body.subarray(offset,end));offset=end;guard();
          if(!accepted){waiting=true;return;}
        }
        guard();ending=true;res.end();
        if(res.writableFinished)finish();
      }catch(error){settle(error);}
    };
    const drain=()=>{if(!settled&&waiting){waiting=false;pump();}};
    res.on('drain',drain);res.once('finish',finish);res.once('error',failed);res.once('close',failed);
    signal.addEventListener('abort',failed,{once:true});
    try{guard();res.writeHead(status,headers);pump();}catch(error){settle(error);}
  });
}
