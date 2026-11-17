import {canonicalUUID, sourceBinding, strictImportJSON, validateImportDTO, validateImportError, canonicalJSON, IMPORT_CAP, importVersionKey} from './private-import-dto.js';
const freeze = v => { if(v && typeof v==='object'&&!Object.isFrozen(v)){Object.values(v).forEach(freeze);Object.freeze(v);}return v; };
const fail = () => {throw Error('import_unavailable');};
export function createImportTransport({projectId,fileId,fetchImpl,getOptions,isCurrent}) {
  if(!canonicalUUID(projectId)||!canonicalUUID(fileId))fail();const path='/api/v1/uploads/'+fileId+'/complete';
  return {isCurrent,
    get(version,{signal}){if(!isCurrent()||version!==null&&!canonicalUUID(version))fail();return fetchImpl(path+'?project_id='+projectId+(version?'&version_id='+version:''),getOptions(signal));},
    post({signal}){if(!isCurrent())fail();const options=getOptions(signal);return fetchImpl(path,{...options,method:'POST',headers:{...options.headers,'Content-Type':'application/json','X-Cyomics-Request':'1'},body:JSON.stringify({project_id:projectId})});},
  };
}
export function createPrivateImportController({file,transport=null,onDenied=()=>{},cryptoImpl=globalThis.crypto,setTimer=setTimeout,clearTimer=clearTimeout,now=()=>performance.now()}={}) {
  const source=sourceBinding(file),stored=freeze({...file}),binding=freeze({project_id:source.project_id,raw_file_id:source.raw_file_id,expected_raw_sha256:source.expected_raw_sha256});
  let disposed=false,restored=false,op=null,generation=0,confirmed=null,version=null;
  let phase='idle',code=null,issues=[],uncertain=false;
  const listeners=new Set(),emit=()=>{if(!disposed)for(const fn of listeners)fn();};
  const current=work=>!disposed&&op===work&&work.generation===generation&&transport?.isCurrent()===true&&!work.controller.signal.aborted;
  function cancel(work){if(!work)return;work.controller.abort();clearTimer(work.timer);clearTimer(work.requestTimer);try{void(work.reader?work.reader.cancel():work.response?.body?.cancel())?.catch(()=>{});}catch{}}
  function guard(work){if(now()>=work.deadline||work.requestDeadline!==null&&now()>=work.requestDeadline)cancel(work);if(!current(work))fail();}
  function begin(ms){const work={controller:new AbortController(),generation,deadline:now()+ms,requestDeadline:null,timer:null,requestTimer:null,reader:null,response:null};
    work.cancelled=new Promise((_,reject)=>work.controller.signal.addEventListener('abort',()=>reject(Error('cancelled')),{once:true}));void work.cancelled.catch(()=>{});op=work;work.timer=setTimer(()=>cancel(work),ms);return work;}
  const wait=async(work,pending)=>{const value=await Promise.race([pending,work.cancelled]);guard(work);return value;};
  function change(next,error=null){phase=next;code=error;confirmed=null;issues=[];emit();}
  function lose(){if(disposed)return true;if(!transport?.isCurrent()){dispose();onDenied({status:409,body:{}});return true;}return false;}
  function denied(status,body){dispose();onDenied({status,body});}
  async function request(work,kind){
    guard(work);work.response=null;const ms=Math.min(kind==='post'?60000:15000,Math.floor(work.deadline-now()));if(ms<1)fail();
    work.requestDeadline=now()+ms;work.requestTimer=setTimer(()=>cancel(work),ms);
    try{
      const pending=Promise.resolve(kind==='post'?transport.post({signal:work.controller.signal}):transport.get(version,{signal:work.controller.signal})).then(response=>{
        try{guard(work);}catch(error){try{void response.body?.cancel()?.catch(()=>{});}catch{}throw error;}return response;});
      const response=await wait(work,pending);work.response=response;
      if([401,403,409].includes(response.status)){denied(response.status,{});fail();}
      if(response.redirected)fail();
      if(!/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(response.headers.get('content-type')||''))fail();
      const cap=response.status===200?IMPORT_CAP:4096,length=response.headers.get('content-length');
      if(length!==null&&(!/^\d+$/.test(length)||Number(length)>cap))fail();
      const reader=response.body?.getReader();if(!reader)fail();work.reader=reader;const parts=[];let size=0;
      for(;;){const p=await wait(work,reader.read());if(p.done)break;if(!(p.value instanceof Uint8Array)||size+p.value.byteLength>cap)fail();size+=p.value.byteLength;parts.push(p.value);}
      if(length!==null&&Number(length)!==size)fail();
      const bytes=new Uint8Array(size);let offset=0;for(const p of parts){bytes.set(p,offset);offset+=p.byteLength;}
      const body=strictImportJSON(new TextDecoder('utf-8',{fatal:true}).decode(bytes));guard(work);
      if(response.status===200){await wait(work,validateImportDTO(body,stored,cryptoImpl));return{ok:true,body:freeze(body)};}
      const error=validateImportError(response.status,body);guard(work);
      if(response.status===404&&['project_not_found','file_not_found'].includes(error.code)){denied(response.status,body);fail();}
      if(error.version_key){const expected=await wait(work,importVersionKey(stored,cryptoImpl));if(error.version_key!==expected.key)fail();version=error.version_key;}
      return{ok:false,status:response.status,error:freeze(error)};
    }finally{clearTimer(work.requestTimer);work.requestTimer=null;work.requestDeadline=null;try{void work.reader?.cancel()?.catch(()=>{});work.reader?.releaseLock();}catch{}work.reader=null;}
  }
  function finish(work){if(op===work){op=null;cancel(work);emit();}}
  async function read(manual=false){
    if(disposed||op||!transport?.isCurrent()||manual&&!['unknown','failed','unavailable'].includes(phase))return false;
    const work=begin(15000);change('checking');
    try{guard(work);const result=await request(work,'get');guard(work);
      if(result.ok){confirmed=result.body;version=confirmed.datasetVersion.dataset_version_id;uncertain=false;phase='ready';code=null;issues=[];emit();guard(work);return true;}
      if(result.error.code==='dataset_version_not_found'&&!uncertain){phase='idle';code=null;emit();return false;}
      if(uncertain){change('unknown',result.error.code);return false;}
      change('unavailable',result.error.code);return false;
    }catch{if(!lose()){change(uncertain?'unknown':'unavailable',uncertain?'import_result_unknown':'import_unavailable');}return false;}
    finally{finish(work);}
  }
  async function start(){
    if(disposed||op||uncertain||!transport?.isCurrent()||!['idle','failed'].includes(phase))return false;
    const work=begin(75000);let sent=false;change('parsing');
    try{guard(work);sent=true;const first=await request(work,'post');guard(work);
      if(!first.ok){
        if([400,405,413,415,422,429].includes(first.status)){phase='failed';code=first.error.code;issues=first.error.issues||[];uncertain=false;emit();return false;}
        uncertain=true;change('unknown',first.error.code);return false;
      }
      version=first.body.datasetVersion.dataset_version_id;
      const second=await request(work,'get');guard(work);
      if(!second.ok||canonicalJSON(first.body)!==canonicalJSON(second.body))fail();
      guard(work);
      confirmed=second.body;uncertain=false;phase='ready';code=null;issues=[];emit();guard(work);return true;
    }catch{if(!lose()){uncertain=sent;change(sent?'unknown':'failed',sent?'import_result_unknown':'import_unavailable');}return false;}
    finally{finish(work);}
  }
  function dispose(){if(disposed)return;disposed=true;generation++;const old=op;op=null;cancel(old);confirmed=null;version=null;phase='unavailable';code=null;issues=[];uncertain=false;for(const fn of listeners)fn();listeners.clear();}
  return{start,check:()=>read(true),restore(){if(restored||disposed)return Promise.resolve(false);restored=true;return read();},dispose,
    status(){const valid=!disposed&&transport?.isCurrent()===true,report=confirmed?.importReport;
      return{phase:valid?phase:'unavailable',source_verified:valid,binding:valid?{...binding}:null,source_filename:valid?stored.filename:'',
        report_verified:valid&&phase==='ready'&&!!confirmed,report_saved:valid&&phase==='ready'&&!!confirmed,report_version_id:valid?confirmed?.datasetVersion.dataset_version_id||null:null,
        report:valid&&phase==='ready'?freeze({...report.report,columns:report.columns}):null,error_verified:valid&&!!code,error_code:valid?code:null,issues:valid?issues.map(x=>({...x})):[],
        can_start:valid&&!op&&!uncertain&&['idle','failed'].includes(phase),can_check:valid&&!op&&['unknown','failed','unavailable'].includes(phase)};},
    subscribe(fn){if(disposed)return()=>{};listeners.add(fn);return()=>listeners.delete(fn);}};
}
