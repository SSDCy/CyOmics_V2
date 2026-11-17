import {SampleDesignFailure,families,uuid,freeze,canonicalJSON,sampleDesignRoute,validateSource,validateDesign,sampleDesignInput,strictJSON,validateSampleDesignResponse,validateSampleDesignError,MAX_RESPONSE,MAX_ERROR} from './sample-design-contract.mjs';
const fail=(code='sample_design_unavailable',status=503)=>{throw new SampleDesignFailure(status,code);};
const token=family=>Object.keys(families).find(k=>families[k]===family);
// No fetch, cookies, token or arbitrary options are accepted implicitly.
export function createSampleDesignTransport({fetchImpl=null}={}){
  if(typeof fetchImpl!=='function')return null;
  return ({route,body,signal,onSent=()=>{}})=>{
    route=sampleDesignRoute(route.path,route.method);if(signal.aborted)fail('sample_design_timeout',504);
    const options={method:route.method,credentials:'same-origin',redirect:'error',cache:'no-store',signal,headers:{Accept:'application/json'}};
    if(route.method==='POST'){options.headers['Content-Type']='application/json';options.headers['X-Cyomics-Request']='1';options.body=body;onSent();}
    return fetchImpl(route.path,options);
  };
}
// One immutable trusted source per client. isCurrent(binding) must reflect the
// current project/dataset/family in the host, including switches during I/O.
export function createSampleDesignClient({projectId,datasetVersionId,source,transport=null,isCurrent=()=>false,cryptoImpl=globalThis.crypto,setTimer=setTimeout,clearTimer=clearTimeout,now=()=>performance.now()}={}){
  try{source=freeze(structuredClone(source));validateSource(source);}catch{fail('sample_design_persisted_invalid');}
  if(!uuid(projectId)||!uuid(datasetVersionId)||source.dataset_version_id!==datasetVersionId)fail('invalid_sample_design',422);
  let disposed=false,operation=null;const scopes=new Map();
  const state=family=>{if(!Object.values(families).includes(family))fail('invalid_sample_design',422);if(!scopes.has(family))scopes.set(family,{headKnown:false,head:null,dirty:false,phase:transport?'idle':'not_connected',uncertain:null});return scopes.get(family);};
  const binding=family=>freeze({project_id:projectId,dataset_version_id:datasetVersionId,quant_family:family});
  const current=work=>!disposed&&operation===work&&isCurrent(binding(work.family))===true&&!work.controller.signal.aborted&&now()<work.deadline;
  const guard=work=>{if(!current(work))fail('sample_design_context_changed',409);};
  function begin(family,signal,method){
    if(disposed||operation||typeof transport!=='function'||isCurrent(binding(family))!==true)fail();
    const controller=new AbortController(),work={family,controller,sent:false,method,reader:null,deadline:now()+(method==='POST'?60000:15000)};
    const abort=()=>controller.abort();work.external=signal;work.abort=abort;signal?.addEventListener('abort',abort,{once:true});
    work.cancelled=new Promise((_,reject)=>controller.signal.addEventListener('abort',()=>reject(new SampleDesignFailure(504,work.sent?'sample_design_result_unknown':'sample_design_timeout')),{once:true}));void work.cancelled.catch(()=>{});
    work.timer=setTimer(abort,method==='POST'?60000:15000);operation=work;if(signal?.aborted)abort();return work;
  }
  async function wait(work,promise){const result=await Promise.race([promise,work.cancelled]);guard(work);return result;}
  function finish(work){clearTimer(work.timer);work.external?.removeEventListener('abort',work.abort);try{void work.reader?.cancel()?.catch(()=>{});work.reader?.releaseLock();}catch{}if(operation===work)operation=null;work.controller.abort();}
  const routeFor=(family,method,requestId=null)=>sampleDesignRoute('/api/v1/datasets/'+datasetVersionId+'/sample-designs?project_id='+projectId+'&quant_family='+token(family)+(requestId?'&request_id='+requestId:''),method);
  async function request(work,route,payload=null,head=undefined){
    guard(work);const body=payload?JSON.stringify(payload):undefined;
    if(route.method==='POST')work.sent=true; // Conservative: injected transport may send before returning.
    const response=await wait(work,Promise.resolve(transport({route,body,signal:work.controller.signal,onSent:()=>{work.sent=true;}})).then(r=>{if(!current(work)){try{void r.body?.cancel()?.catch(()=>{});}catch{}guard(work);}return r;}));
    if(response.redirected||!/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(response.headers.get('content-type')||'')||![null,'identity'].includes(response.headers.get('content-encoding')))fail('sample_design_persisted_invalid');
    const cap=response.status===200?MAX_RESPONSE:MAX_ERROR,length=response.headers.get('content-length');if(length!==null&&(!/^\d{1,10}$/.test(length)||Number(length)>cap))fail('sample_design_persisted_invalid');
    const reader=response.body?.getReader();if(!reader)fail('sample_design_persisted_invalid');work.reader=reader;let size=0;const parts=[];
    for(;;){const p=await wait(work,reader.read());if(p.done)break;if(!(p.value instanceof Uint8Array)||size+p.value.byteLength>cap)fail('sample_design_persisted_invalid');size+=p.value.byteLength;parts.push(p.value);}
    if(length!==null&&Number(length)!==size)fail('sample_design_persisted_invalid');const bytes=new Uint8Array(size);let at=0;for(const part of parts){bytes.set(part,at);at+=part.byteLength;}
    let value;try{value=strictJSON(bytes);}catch{fail('sample_design_persisted_invalid');}guard(work);
    if(response.status!==200){const validated=validateSampleDesignError(response.status,value);throw new SampleDesignFailure(response.status,validated.error.code);}
    const result=await wait(work,validateSampleDesignResponse(value,route,source,{request:payload,head,cryptoImpl}));return result.record;
  }
  function noteFailure(s,work,error,payload){
    const rejected=new Set(['invalid_sample_design_request','auth_origin_rejected','not_found','method_not_allowed','unsupported_media_type','unauthenticated','dataset_not_found','sample_design_not_found','sample_design_head_conflict','sample_design_request_conflict','sample_design_request_too_large','invalid_sample_design','sample_design_version_limit']);
    if(work.method==='POST'&&work.sent&&!rejected.has(error.code)){
      s.phase='unknown';s.uncertain=freeze(structuredClone(payload));s.headKnown=false;
    }else if(['sample_design_head_conflict','sample_design_request_conflict'].includes(error.code)&&work.method==='POST'){s.phase='conflict';s.headKnown=false;}else s.phase='failed';
  }
  async function loadDraft(context,{signal}={}){
    if(context?.dataset_version_id!==datasetVersionId)fail('invalid_sample_design',422);const s=state(context.quant_family);if(s.uncertain)fail('sample_design_result_unknown',504);
    const work=begin(context.quant_family,signal,'GET');s.phase='loading';
    try{const r=await request(work,routeFor(work.family,'GET'));guard(work);s.head=r;s.headKnown=true;s.phase=r?'loaded':'not_found';return r?.design??null;}
    catch(error){s.headKnown=false;s.phase='failed';throw error;}finally{finish(work);}
  }
  async function saveDraft(design,{signal}={}){
    const snapshot=freeze(structuredClone(design)),family=snapshot?.quant_family,s=state(family);s.dirty=true;
    if(s.uncertain)fail('sample_design_result_unknown',504);if(!s.headKnown)fail('sample_design_head_required',409);
    const route=routeFor(family,'POST');validateDesign(snapshot,source,route);
    const payload=freeze({schemaVersion:1,request_id:cryptoImpl.randomUUID(),expected_head_version_id:s.head?.sample_design_version_id??null,design:snapshot});
    sampleDesignInput(new TextEncoder().encode(JSON.stringify(payload)),route,source);
    const work=begin(family,signal,'POST');s.phase='saving';
    try{const r=await request(work,route,payload,s.head);guard(work);s.head=r;s.headKnown=true;s.dirty=false;s.phase='saved';return r.design;}
    catch(error){noteFailure(s,work,error,payload);if(s.phase==='unknown')throw new SampleDesignFailure(504,'sample_design_result_unknown');throw error;}finally{finish(work);}
  }
  async function resolveRequest(family,{signal}={}){
    const s=state(family),pending=s.uncertain;if(!pending)fail('sample_design_no_uncertain_request',409);
    const work=begin(family,signal,'GET');
    try{const r=await request(work,routeFor(family,'GET',pending.request_id));guard(work);
      if(r.parent_version_id!==pending.expected_head_version_id||r.revision!==(s.head===null?1:s.head.revision+1)||canonicalJSON(r.design)!==canonicalJSON(pending.design))fail('sample_design_persisted_invalid');
      s.uncertain=null;s.headKnown=false;s.phase='resolved';// A historical request result is never treated as the current head.
      return r.design;
    }finally{finish(work);}
  }
  return {saveDraft,loadDraft,resolveRequest,
    status(family){const s=state(family);return freeze({phase:s.phase,dirty:s.dirty,head_known:s.headKnown,head_version_id:s.head?.sample_design_version_id??null,uncertain_request_id:s.uncertain?.request_id??null,persistence_verified:!disposed&&isCurrent(binding(family))===true&&s.phase==='saved'&&!s.dirty,analysis_ready:false});},
    dispose(){disposed=true;operation?.controller.abort();},
  };
}
