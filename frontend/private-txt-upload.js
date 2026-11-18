import {deClassificationReadRoute} from './de-classification-read-contract.mjs';
import {createDifferentialResultOwner} from './differential-result-owner.js';
import {differentialResultRoute} from './differential-result-contract.mjs';
import {createDifferentialJobClient,createDifferentialRecovery} from './differential-job-client.js';
import {differentialJobRoute,differentialJobInput} from './differential-job-contract.mjs';
import {sha} from './raw-qc-contract.mjs';
import {differentialInput,differentialInputRoute,differentialInputReply,differentialInputError,DifferentialInputFailure,DIFFERENTIAL_REPLY_MAX} from './differential-input-contract.mjs';
import {parsePreprocessJSON} from './preprocess-owner-wire.mjs';
import {createPrivateImportController, createImportTransport} from './private-import-controller.js';
import {mountPrivateImportReport} from './private-import-report-view.js';
import {createPrivateSampleDesignHost, mountPrivateSampleDesignHost} from './private-sample-design-host.js';
import {createPrivateContrastHost, mountPrivateContrastHost} from './private-contrast-host.js';
import {createSampleDesignTransport, createSampleDesignClient} from './sample-design-client.js';
import {sampleDesignRoute, canonicalJSON, freeze} from './sample-design-contract.mjs';
import {createQCTransport} from './raw-qc-client.js';
import {createPrivateRawQCHost, mountPrivateRawQC} from './private-raw-qc.js';
const MAX = 26214400;
// One file-entry bridge to the existing verified hosts. Getters never perform I/O.
function createDiffPreparation({reportModel,projectId,fileId,isCurrent,getSample,getContrast,jobs=null,results=null,checkInput=null,onDenied=()=>{},contextSignal=null,setTimer,clearTimer,now}) {
  let disposed=false,reading=false,pinned=null,replicateModel=null,frame=null,processed=null,token=null,confirmationEpoch=0;
  let sample=null,contrast=null,stopSample=()=>{},stopContrast=()=>{},stopProcessed=()=>{};
  let review={phase:'idle',result:null,code:null},checking=null,submittedReview=null;
  const clearReview=()=>{results?.clear();jobs?.invalidateSelection();const old=checking;checking=null;old?.controller.abort();review={phase:'idle',result:null,code:null};};
  const listeners=new Set(),emit=()=>{if(!disposed)for(const fn of [...listeners])fn();};
  function environment(){try{
    if(disposed||!isCurrent())return null;const source=reportModel.validatedSource(),v=processed?.();
    if(!source||!v||v.schemaVersion!==2||v.analysis_ready!==false||v.project_id!==projectId||v.file_id!==fileId
      ||v.parent_dataset_version_id!==source.importDTO.datasetVersion.dataset_version_id||v.source.project_id!==projectId
      ||v.source.dataset_version_id!==v.parent_dataset_version_id||v.source.file_id!==fileId||v.source.source_sha256!==source.file.sha256)return null;
    return{version:v,versionKey:canonicalJSON(v),sourceKey:canonicalJSON(source)};
  }catch{return null;}}
  function state(){
    const e=environment();if(!e||pinned&&(pinned.versionKey!==e.versionKey||pinned.sourceKey!==e.sourceKey)){pinned=null;replicateModel=null;frame=null;clearReview();}
    const idle={available:!!e,active:false,processed_dataset_version_id:null,quant_family:null,sample_design_version_id:null,sample_design_request_id:null,
      sample_phase:null,sample_family:null,contrast:null,contrast_count:0,replicate_model:null,confirmation_epoch:null,can_confirm_independence:false,selection_complete:false,missing:[],analysis_ready:false,input_review:review};
    if(!pinned)return freeze(idle);
    let proof=null,sv=null,cv=null,payload=null;try{proof=sample?.comparisonSource?.()||null;sv=sample?.preparationState?.()||null;cv=contrast?.view?.()||null;payload=contrast?.exportDraft?.()||null;}catch{}
    const key=canonicalJSON({proof,payload,binding:cv?.design?.binding||null,epoch:cv?.design?.source_epoch??null,state:cv?.design?.state||null,
      sampleState:sv});
    if(frame!==key){frame=key;replicateModel=null;confirmationEpoch++;clearReview();}
    const v=pinned.version,record=proof?.sample_design_record;
    const matched=!!proof&&sv?.head_known===true&&!sv.request_unconfirmed&&!sv.busy&&!sv.dirty&&proof.project_id===projectId&&proof.dataset_version_id===v.parent_dataset_version_id&&proof.quant_family===v.quant_family
      &&record?.design?.dataset_version_id===v.parent_dataset_version_id&&record.design.quant_family===v.quant_family;
    const bound=matched&&cv?.available===true&&cv.design.binding?.project_id===projectId&&cv.design.binding.dataset_version_id===v.parent_dataset_version_id
      &&cv.design.binding.quant_family===v.quant_family&&cv.design.binding.sample_design_version_id===record.sample_design_version_id
      &&cv.design.binding.design_sha256===record.design_sha256&&payload?.dataset_version_id===v.parent_dataset_version_id
      &&payload.sample_design_version_id===record.sample_design_version_id&&payload.quant_family===v.quant_family;
    const count=bound?payload.contrasts.length:0,missing=[];
    if(sv?.quant_family&&sv.quant_family!==v.quant_family)missing.push('sample_family');
    else if(!matched)missing.push('sample_saved');
    if(matched&&!bound)missing.push('contrast_source');
    if(bound&&count===0)missing.push('contrast_missing');else if(bound&&count!==1)missing.push('contrast_single');
    if(bound&&cv.design.state==='blocked_by_sample_design')missing.push('sample_review');
    else if(bound&&count===1&&cv.design.ready_for_method_selection!==true)missing.push('contrast_review');
    const baseReady=bound&&count===1&&cv.design.ready_for_method_selection===true;
    if(baseReady&&replicateModel!=='independent_biological')missing.push('independence');
    return freeze({...idle,active:true,processed_dataset_version_id:v.processed_dataset_version_id,quant_family:v.quant_family,
      sample_design_version_id:matched?record.sample_design_version_id:null,sample_design_request_id:matched?record.request_id:null,
      sample_phase:sv?.dirty?'dirty':sv?.busy?'busy':matched?'saved':null,sample_family:sv?.quant_family||null,contrast:bound?structuredClone(payload):null,contrast_count:count,
      replicate_model:replicateModel,confirmation_epoch:confirmationEpoch,can_confirm_independence:baseReady,selection_complete:baseReady&&replicateModel==='independent_biological',missing,input_review:review});
  }
  const read=()=>{if(reading)return null;reading=true;try{return state();}catch{pinned=null;replicateModel=null;frame=null;clearReview();return freeze({available:false,active:false,analysis_ready:false});}finally{reading=false;}};
  const changed=()=>{if(disposed||reading)return;read();emit();};
  function connectHosts(){
    const nextSample=getSample(),nextContrast=getContrast();
    if(nextSample!==sample){stopSample();sample=nextSample;stopSample=sample?.subscribe?.(changed)||(()=>{});}
    if(nextContrast!==contrast){stopContrast();contrast=nextContrast;stopContrast=contrast?.subscribe?.(changed)||(()=>{});}
    changed();
  }
  const selection=()=>{const v=read();return v?.selection_complete?freeze(structuredClone({processed_dataset_version_id:v.processed_dataset_version_id,
    sample_design_version_id:v.sample_design_version_id,sample_design_request_id:v.sample_design_request_id,contrast:v.contrast,replicate_model:v.replicate_model})):null;};
  const selectionKey=()=>{const value=selection();return value&&pinned?canonicalJSON({source:pinned.sourceKey,version:pinned.versionKey,frame,confirmationEpoch,selection:value}):null;};
  async function check(){
    const selected=selection(),key=selectionKey();if(!selected||!key||checking||jobs?.view().busy||typeof checkInput!=='function')return false;
    const w={controller:new AbortController(),key,start:now(),last:now(),timer:null};checking=w;review={phase:'checking',result:null,code:null};emit();
    const live=()=>{const stamp=now();if(checking!==w||disposed||!isCurrent()||selectionKey()!==key||w.controller.signal.aborted||!Number.isFinite(stamp)||stamp<w.last||stamp-w.start>=15000)throw new DifferentialInputFailure();w.checkContext?.();w.last=stamp;};
    let reader;const aborted=new Promise((_,reject)=>w.controller.signal.addEventListener('abort',()=>reject(new DifferentialInputFailure()),{once:true}));
    w.timer=setTimer(()=>w.controller.abort(),15000);
    try{
      const result=await Promise.race([(async()=>{
        live();const received=await checkInput(selected,{signal:w.controller.signal});w.checkContext=received.checkCurrent;live();const response=received.response;
        if([401,403].includes(response.status)){onDenied({status:response.status});throw new DifferentialInputFailure(response.status,response.status===401?'unauthenticated':'auth_origin_rejected');}
        if(response.redirected||!/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(response.headers.get('content-type')||'')||['location','refresh'].some(k=>response.headers.has(k)))throw new DifferentialInputFailure();
        const cap=response.status===200?DIFFERENTIAL_REPLY_MAX:4096,length=response.headers.get('content-length');
        if(length!==null&&(!/^[0-9]{1,10}$/.test(length)||Number(length)>cap))throw new DifferentialInputFailure();
        reader=response.body.getReader();const chunks=[];let size=0;
        for(;;){const item=await reader.read();live();if(item.done)break;if(!(item.value instanceof Uint8Array)||size+item.value.length>cap)throw new DifferentialInputFailure();chunks.push(item.value);size+=item.value.length;}
        if(length!==null&&Number(length)!==size)throw new DifferentialInputFailure();const raw=new Uint8Array(size);let at=0;for(const c of chunks){raw.set(c,at);at+=c.length;}
        const value=parsePreprocessJSON(raw),route=differentialInputRoute('/api/v1/projects/'+projectId+'/differential-input','POST');live();
        if(response.status!==200){const error=differentialInputError(response.status,value);if(response.status===409&&error.error.code==='project_context_changed')onDenied({status:409,code:'project_context_changed'});throw new DifferentialInputFailure(response.status,error.error.code);}
        return differentialInputReply(route,200,value,selected);
      })(),aborted]);live();review={phase:'reviewed',result,code:null};checking=null;emit();return true;
    }catch(error){
      if(error?.message==='project_context_changed'&&!disposed){clearReview();pinned=null;replicateModel=null;frame=null;onDenied({status:409,code:'project_context_changed'});emit();return false;}
      if(checking===w&&selectionKey()===key&&!disposed&&isCurrent()){
        checking=null;review={phase:'unavailable',result:null,code:error instanceof DifferentialInputFailure?error.code:'differential_input_unavailable'};
        if(error?.status===404){replicateModel=null;confirmationEpoch++;}emit();
      }return false;
    }finally{clearTimer(w.timer);reader?.cancel().catch(()=>{});if(checking===w)checking=null;w.controller.abort();}
  }
  const canSubmit=()=>{const chosen=selection();return !!chosen&&review.phase==='reviewed'&&review.result?.model_input_satisfied===true&&submittedReview!==review&&jobs?.canSubmit(chosen)===true;};
  async function submitJob(){
    if(!canSubmit())return false;const chosen=selection(),key=selectionKey(),checked=review,source=pinned.version;submittedReview=checked;emit();
    const ok=await jobs.submit({selection:chosen,source,inputManifest:checked.result.input_summary.input_pins,currentSelection:()=>!disposed&&selectionKey()===key&&review===checked});
    const rejected=jobs.view().rejection;if(rejected&&selectionKey()===key&&canonicalJSON(rejected.selection)===canonicalJSON(chosen)){review={phase:'reviewed',result:rejected.review,code:null};}
    emit();return ok;
  }
  const stopJobs=jobs?.subscribe(emit)||(()=>{});
  const stopResults=results?.subscribe(()=>{if(!reading)emit();})||(()=>{});
  const stopReport=reportModel.subscribe(changed);
  contextSignal?.addEventListener('abort',changed,{once:true});
  return {
    connectHosts,
    navigate(kind){const value=read();if(!value?.active||!value.available)return false;const host=kind==='sample'?sample:kind==='contrast'?contrast:null;return host?.navigatePreparation?.()===true;},
    bindProcessed(getter,subscribe){
      if(disposed||typeof getter!=='function'||typeof subscribe!=='function')return()=>{};
      stopProcessed();pinned=null;replicateModel=null;frame=null;clearReview();const own={};token=own;processed=getter;stopProcessed=subscribe(changed);changed();
      return()=>{if(token!==own)return;stopProcessed();stopProcessed=()=>{};token=null;processed=null;pinned=null;replicateModel=null;frame=null;clearReview();emit();};
    },
    prepare(){const e=environment();if(!e)return false;clearReview();pinned=e;replicateModel=null;frame=null;connectHosts();sample?.setExpanded(true);contrast?.setExpanded(true);changed();return true;},
    setReplicateModel(value,expectedEpoch){const v=read();if(!v?.active||expectedEpoch!==v.confirmation_epoch||value!==null&&value!=='independent_biological'||value!==null&&!v.can_confirm_independence)return false;if(replicateModel!==value)clearReview();replicateModel=value;emit();return true;},
    view(){const value=read()||{available:false,active:false,analysis_ready:false};return freeze({...value,can_submit_job:canSubmit(),differential_jobs:jobs?.view()||null});},
    selection,checkInput:check,submitJob,checkJob:requestId=>jobs?.check(requestId)||Promise.resolve(false),
    subscribe(fn){if(disposed)return()=>{};listeners.add(fn);return()=>listeners.delete(fn);},
    dispose(){if(disposed)return;clearReview();disposed=true;stopJobs();stopResults();results?.dispose();jobs?.dispose();contextSignal?.removeEventListener('abort',changed);stopReport();stopSample();stopContrast();stopProcessed();processed=null;pinned=null;replicateModel=null;frame=null;for(const fn of [...listeners])fn();listeners.clear();}
  };
}
const exact = (v, keys) => v !== null && typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === keys.length && keys.every(k => Object.hasOwn(v, k));
const fields = ['file_id', 'project_id', 'request_id', 'filename', 'format', 'bytes', 'sha256', 'status', 'createdAt'];
export const rawUuid = v => typeof v === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v);
export function validTxtName(v) {
  return typeof v === 'string' && !/[\uD800-\uDFFF\u0000-\u001f\u007f-\u009f/\\]/u.test(v)
    && /\.txt$/i.test(v) && new TextEncoder().encode(v).length >= 1 && new TextEncoder().encode(v).length <= 255;
}
const utc = v => typeof v === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(v)
  && Number.isFinite(Date.parse(v)) && new Date(v).toISOString().slice(0, 19) === v.slice(0, 19);
export const isRawFile = (v, projectId) => exact(v, fields) && rawUuid(v.file_id) && v.project_id === projectId && rawUuid(projectId)
  && rawUuid(v.request_id) && validTxtName(v.filename) && v.format === 'proteinGroups_txt' && Number.isSafeInteger(v.bytes)
  && v.bytes >= 1 && v.bytes <= MAX && typeof v.sha256 === 'string' && /^[0-9a-f]{64}$/.test(v.sha256) && v.status === 'stored' && utc(v.createdAt);
const same = (a, b) => fields.every(k => a[k] === b[k]);
const orderTime = v => { const s = v.replace(/(?:Z|\+00:00)$/, '').split('.'); return s[0] + '.' + (s[1] || '').padEnd(6, '0'); };
export function isRawList(v, projectId) {
  if (!exact(v, ['items', 'nextCursor']) || v.nextCursor !== null || !Array.isArray(v.items) || v.items.length > 4 || !v.items.every(x => isRawFile(x, projectId))) return false;
  if (new Set(v.items.map(x => x.file_id)).size !== v.items.length || new Set(v.items.map(x => x.request_id)).size !== v.items.length || v.items.reduce((s, x) => s + x.bytes, 0) > 33554432) return false;
  return v.items.every((x, i) => !i || orderTime(v.items[i - 1].createdAt) > orderTime(x.createdAt)
    || orderTime(v.items[i - 1].createdAt) === orderTime(x.createdAt) && v.items[i - 1].file_id > x.file_id);
}
export const txtFilenameHeader = name => {
  if (!validTxtName(name)) throw Error('invalid_upload');
  return btoa(String.fromCharCode(...new TextEncoder().encode(name))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
};
const hex = bytes => [...new Uint8Array(bytes)].map(x => x.toString(16).padStart(2, '0')).join('');
const bad = code => { throw Error(code); };
const errors = {400: ['invalid_upload', 'upload_checksum_mismatch', 'invalid_upload_query'], 401: ['unauthenticated'], 403: ['auth_origin_rejected'],
  404: ['project_not_found', 'file_not_found'], 405: ['method_not_allowed'], 409: ['project_context_changed', 'upload_request_conflict'],
  413: ['upload_too_large', 'upload_quota_exceeded'], 415: ['unsupported_media_type'], 429: ['upload_rate_limited', 'upload_busy'],
  503: ['uploads_unavailable'], 504: ['upload_result_unknown']};
const errorCode = (status, body) => exact(body, ['error']) && exact(body.error, ['code']) && (errors[status] || []).includes(body.error.code) ? body.error.code : 'uploads_unavailable';

// getOptions/isCurrent close over the existing private transport's nonce and revision.
// The nonce is used only in request options, never returned in public state.
export function createRawTransport({projectId, fetchImpl, getOptions, isCurrent, sampleDesignsEnabled = false, sampleDesignClientFactory = createSampleDesignClient, rawQcEnabled = false, rawQcArtifactsEnabled = false, artifactScope = null, rawQcStorage, preprocessingEnabled = false, preprocessingStorage, preprocessingArtifactScope = null, preprocessingContextSignal = null, onPreprocessingScope = null, cryptoImpl = globalThis.crypto}) {
  if (!rawUuid(projectId)) bad('invalid_upload');
  const preprocessingController = preprocessingEnabled === true ? new AbortController() : null;
  const abortPreprocessing = () => preprocessingController?.abort();
  if (preprocessingController && preprocessingContextSignal) {
    if (preprocessingContextSignal.aborted) abortPreprocessing();
    else preprocessingContextSignal.addEventListener('abort', abortPreprocessing, {once: true});
  }
  const preprocessingCurrent = () => !!preprocessingController && !preprocessingController.signal.aborted && isCurrent() === true;
  const base = '/api/v1/projects/' + projectId;
  const send = (suffix, signal, upload) => {
    if (!isCurrent()) bad('project_context_changed');
    const options = getOptions(signal);
    if (upload) return fetchImpl(base + suffix, {...options, method: 'POST', headers: {...options.headers, ...upload.headers}, body: upload.file});
    return fetchImpl(base + suffix, options);
  };
  const differentialContext=signal=>{if(!preprocessingCurrent()||signal.aborted)bad('project_context_changed');const o=getOptions(signal),nonce=new Headers(o?.headers).get('X-Cyomics-Account-Context');
    if(o?.credentials!=='same-origin'||o.cache!=='no-store'||o.redirect!=='error'||typeof nonce!=='string'||!/^[A-Za-z0-9_-]{22}$/.test(nonce))bad('project_context_changed');return nonce;};
  async function differentialSend(route,signal,body,expectedNonce=null){
    const nonce=differentialContext(signal);if(expectedNonce!==null&&nonce!==expectedNonce)bad('project_context_changed');
    const response=await fetchImpl(route.path,{method:route.method,...(body!==undefined?{body}:{}),signal,credentials:'same-origin',cache:'no-store',redirect:'error',
      headers:{'X-Cyomics-Account-Context':nonce,...(route.method==='POST'?{'Content-Type':'application/json','X-Cyomics-Request':'1'}:{})}});
    const checkCurrent=()=>{if(differentialContext(signal)!==nonce)bad('project_context_changed');};checkCurrent();return {response,checkCurrent};
  }
  const rawTransport={isCurrent,
    checkDifferentialInput(selection,{signal}){const body=JSON.stringify(selection);differentialInput(new TextEncoder().encode(body));return differentialSend(differentialInputRoute(base+'/differential-input','POST'),signal,body);},
    differentialJobs(config){
      if(preprocessingEnabled!==true||config.projectId!==projectId||!rawUuid(config.fileId)||typeof config.isCurrent!=='function')return null;
      const recovery=createDifferentialRecovery(preprocessingStorage);let nonce0=null;
      const current=()=>preprocessingCurrent()&&config.isCurrent()===true;
      return Object.freeze({isCurrent:current,recovery,
        async scope(signal){if(!current())bad('project_context_changed');const nonce=differentialContext(signal),value=await sha({domain:'cyomics/differential/account/v1',context:nonce},cryptoImpl);
          if(!current()||differentialContext(signal)!==nonce)bad('project_context_changed');nonce0=nonce;onPreprocessingScope?.(value,recovery);return value;},
        send({kind,body,requestId,signal}){if(!current()||nonce0===null||!['submit','lookup'].includes(kind))bad('project_context_changed');
          const route=differentialJobRoute(base+'/differential-jobs'+(kind==='lookup'?'?request_id='+requestId:''),kind==='submit'?'POST':'GET');
          const wire=kind==='submit'?canonicalJSON(body):undefined;if(wire!==undefined)differentialJobInput(new TextEncoder().encode(wire));return differentialSend(route,signal,wire,nonce0);}
      });
    },
    differentialResults(config){
      if(preprocessingEnabled!==true||config.projectId!==projectId||!rawUuid(config.fileId)||typeof config.isCurrent!=='function')return null;let nonce0=null;
      const current=()=>preprocessingCurrent()&&config.isCurrent()===true;
      const context=signal=>{if(!current())bad('project_context_changed');const nonce=differentialContext(signal);if(nonce0===null)nonce0=nonce;if(nonce!==nonce0)bad('project_context_changed');return nonce;};
      return Object.freeze({isCurrent:current,artifactScope:preprocessingArtifactScope,
        send({kind,jobId,resultId,classificationRequestId,derivedVersionId,name,signal}){context(signal);let path;
          if(kind==='run')path=base+'/differential-runs/'+jobId;else if(kind==='lookup')path=base+'/differential-results?job_id='+jobId;
          else if(kind==='result'||kind==='artifact')path=base+'/differential-results/'+resultId+(kind==='artifact'?'/artifacts/'+name:'');
          else if(kind==='classification-lookup')path=base+'/differential-classifications?request_id='+classificationRequestId;
          else if(kind==='classification-version'||kind==='classification-artifact')path=base+'/differential-classifications/'+derivedVersionId+(kind==='classification-artifact'?'/files/'+name:'');else bad('project_context_changed');
          return differentialSend(kind.startsWith('classification-')?deClassificationReadRoute(path,'GET'):differentialResultRoute(path,'GET'),signal,undefined,nonce0);},
        async rowMap(job,signal){const nonce=context(signal);if(job?.project_id!==projectId||job.source?.file_id!==config.fileId)bad('project_context_changed');
          const original=rawTransport.preprocessing({projectId,fileId:config.fileId,datasetVersionId:job.source.parent_dataset_version_id,isCurrent:current});
          const response=await original.send({kind:'artifact',versionId:job.selection.processed_dataset_version_id,name:'row-map.tsv',signal});
          const checkCurrent=()=>{if(context(signal)!==nonce)bad('project_context_changed');};checkCurrent();return{response,checkCurrent};}
      });
    },
    preprocessingEnabled: preprocessingEnabled === true,
    preprocessingContextSignal: preprocessingController?.signal || null,
    disposePreprocessing() { abortPreprocessing(); preprocessingContextSignal?.removeEventListener('abort', abortPreprocessing); },
    preprocessing(config) {
      if (preprocessingEnabled !== true) return null;
      if (!preprocessingCurrent() || config.projectId !== projectId || !rawUuid(config.datasetVersionId) || !rawUuid(config.fileId) || typeof config.isCurrent !== 'function') bad('project_context_changed');
      const current = () => preprocessingCurrent() && config.isCurrent() === true;
      let reviewed = null, loading = null;
      const load = async () => {
        if (!current()) bad('project_context_changed');
        if (!loading) loading = import('./preprocess-owner-client.js').then(({createPreprocessOwnerTransport}) => {
          if (!current()) bad('project_context_changed');
          return createPreprocessOwnerTransport({featureEnabled: true, projectId, datasetVersionId: config.datasetVersionId,
            fetchImpl, getOptions, isCurrent: current, storage: preprocessingStorage, artifactScope: preprocessingArtifactScope, cryptoImpl});
        });
        const value = await loading; if (!current()) bad('project_context_changed'); reviewed = value; return value;
      };
      return Object.freeze({isCurrent: current, artifactScope: preprocessingArtifactScope,
        recovery: Object.freeze({load(scope) { if (!reviewed || !current()) bad('project_context_changed'); return reviewed.recovery.load(scope); },
          put(scope, ref) { if (!reviewed || !current()) bad('project_context_changed'); return reviewed.recovery.put(scope, ref); },
          clear(scope) { reviewed?.recovery.clear(scope); }}),
        async scope() { const value = await load(), scope = await value.scope(); if (!current()) bad('project_context_changed');
          if (typeof onPreprocessingScope === 'function') onPreprocessingScope(scope, value.recovery);
          if (!current()) bad('project_context_changed'); return scope; },
        async send(request) { const value = await load(); if (!current()) bad('project_context_changed'); return value.send(request); }
      });
    },
    rawQC(config) {
      if (rawQcEnabled !== true) return null;
      if (config.projectId !== projectId || !rawUuid(config.datasetVersionId) || !rawUuid(config.fileId)) bad('project_context_changed');
      return createQCTransport({projectId, datasetVersionId: config.datasetVersionId, fetchImpl, getOptions, isCurrent, cryptoImpl, storage: rawQcStorage, rawQcArtifactsEnabled, artifactScope});
    },
    sampleDesigns(config) {
      if (sampleDesignsEnabled !== true || typeof sampleDesignClientFactory !== 'function') return null;
      if (config.projectId !== projectId || config.source?.dataset_version_id !== config.datasetVersionId) bad('project_context_changed');
      const fetchSample = (path, options) => {
        const route = sampleDesignRoute(path, options.method);
        if (!isCurrent() || !config.isCurrent({project_id: projectId, dataset_version_id: config.datasetVersionId, quant_family: route.quant_family})
          || route.project_id !== projectId || route.dataset_version_id !== config.datasetVersionId || options.signal.aborted) bad('project_context_changed');
        const baseOptions = getOptions(options.signal), context = new Headers(baseOptions.headers).get('X-Cyomics-Account-Context');
        if (baseOptions.credentials !== 'same-origin' || baseOptions.cache !== 'no-store' || baseOptions.redirect !== 'error'
          || typeof context !== 'string' || !/^[A-Za-z0-9_-]{22}$/.test(context)) bad('project_context_changed');
        // Only the existing account-context header crosses this boundary. Keep
        // the sample transport's JSON/CSRF headers, method and bounded body.
        return fetchImpl(route.path, {...options, headers: {...options.headers, 'X-Cyomics-Account-Context': context}});
      };
      return sampleDesignClientFactory({...config, transport: createSampleDesignTransport({fetchImpl: fetchSample})});
    },
    imports: fileId => createImportTransport({projectId,fileId,fetchImpl,getOptions,isCurrent}),
    list: ({signal}) => send('/files', signal),
    receipt: (id, {signal}) => { if (!rawUuid(id)) bad('invalid_upload'); return send('/raw-uploads/' + id, signal); },
    upload: (request, {signal}) => send('/raw-uploads', signal, request),
    content: (id, {signal}) => { if (!rawUuid(id)) bad('invalid_upload'); return send('/files/' + id + '/content', signal); },
  };return rawTransport;
}

export function createPrivateTxtController({projectId, transport = null, onDenied = () => {}, cryptoImpl = globalThis.crypto,
  setTimer = setTimeout, clearTimer = clearTimeout, now = () => performance.now(),
  documentImpl = globalThis.document, urlImpl = globalThis.URL, BlobImpl = globalThis.Blob, differentialResultOptions = {},
  sampleDesignClientFactory = typeof transport?.sampleDesigns === 'function' ? config => transport.sampleDesigns(config) : null} = {}) {
  if (!rawUuid(projectId)) bad('invalid_upload');
  let disposed = false, started = false, op = null, file = null, attempt = null, generation = 0;
  let state = {phase: 'idle', code: null, items: [], chosen: null, receipt: null, unknown: false};
  let listConfirmed = false, selectionError = null;
  const urls = new Set();
  const importReports = new Map();
  let restoreTail = Promise.resolve();
  function scheduleRestore(run) {
    const next = restoreTail.then(() => disposed ? false : run());
    restoreTail = next.catch(() => false);
    return next;
  }
  function reconcileImports(items) { for (const [id, entry] of importReports) { const next = items.find(x => x.file_id === id); if (!next || !same(next, entry.file)) { entry.preprocessing?.dispose(); entry.rawQC?.dispose(); entry.contrasts?.dispose(); entry.design?.dispose(); entry.model.dispose(); importReports.delete(id); } } }
  function importReport(id) {
    if (disposed || !listConfirmed || !transport?.isCurrent()) return null;
    const stored = state.items.find(x => x.file_id === id); if (!stored || !isRawFile(stored, projectId)) return null;
    if (!importReports.has(id)) importReports.set(id, {file: {...stored}, model: createPrivateImportController({file: stored, transport: transport.imports?.(id) || null, restoreScheduler: scheduleRestore, onDenied(response) { dispose(); onDenied(response); }})});
    return importReports.get(id).model;
  }
  function preprocessing(id) {
    if (disposed || transport?.preprocessingEnabled !== true) return null;
    const reportModel = importReport(id); if (!reportModel) return null;
    const entry = importReports.get(id);
    if (!entry.preprocessing) {
      const controller = new AbortController();
      const current = () => !disposed && !controller.signal.aborted && importReports.get(id) === entry && transport.isCurrent() === true;
      const abort = () => controller.abort(), parentSignal = transport.preprocessingContextSignal;
      if (parentSignal?.aborted) abort(); else parentSignal?.addEventListener('abort', abort, {once: true});
      const denied=response=>{if(current()){dispose();onDenied(response);}};
      const jobs=createDifferentialJobClient({projectId,fileId:id,transport:transport.differentialJobs?.({projectId,fileId:id,isCurrent:current})||null,
        isCurrent:current,contextSignal:controller.signal,cryptoImpl,setTimer,clearTimer,now,onDenied:denied});
      const results=createDifferentialResultOwner({projectId,fileId:id,jobs,transport:transport.differentialResults?.({projectId,fileId:id,isCurrent:current})||null,
        source:()=>{const status=reportModel.status();return status.phase==='ready'&&status.report_saved&&status.report_verified&&status.source_verified?reportModel.validatedSource():null;},
        isCurrent:current,subscribeSource:fn=>reportModel.subscribe(fn),contextSignal:controller.signal,onDenied:denied,cryptoImpl,setTimer,clearTimer,now,
        documentImpl,urlImpl,BlobImpl,provenance:differentialResultOptions.provenance});
      const preparation=createDiffPreparation({reportModel,projectId,fileId:id,isCurrent:current,jobs,results,getSample:()=>entry.design||null,getContrast:()=>entry.contrasts||null,
        checkInput:(selection,options)=>transport.checkDifferentialInput?.(selection,options),contextSignal:controller.signal,setTimer,clearTimer,now,onDenied(response){if(current()){dispose();onDenied(response);}}});
      const options = Object.freeze({featureEnabled: true, diffPreparation:preparation,differentialResults:results, contextSignal: controller.signal, isCurrent: current, clientOptions: {cryptoImpl},
        transportFactory(config) {
          if (!current()) return null;
          const full = reportModel.validatedSource(), version = full?.importDTO?.datasetVersion;
          if (!full || config.projectId !== projectId || config.fileId !== id || config.datasetVersionId !== version.dataset_version_id) return null;
          const fingerprint = canonicalJSON(full);
          return transport.preprocessing({...config, isCurrent: () => {
            const latest = current() ? reportModel.validatedSource() : null;
            return latest !== null && canonicalJSON(latest) === fingerprint;
          }});
        },
        onDenied(response) { if (current()) { dispose(); onDenied(response); } }});
      entry.preprocessing = {options, preparation, dispose() { preparation.dispose(); abort(); parentSignal?.removeEventListener('abort', abort); }};
    }
    return entry.preprocessing.options;
  }
  function rawQC(id) {
    const reportModel = importReport(id); if (!reportModel) return null;
    const entry = importReports.get(id);
    if (!entry.rawQC) entry.rawQC = createPrivateRawQCHost({reportModel, projectId, fileId: id, cryptoImpl,
      transportFactory: config => transport?.rawQC?.(config) || null,
      isCurrent: () => !disposed && importReports.get(id) === entry && transport?.isCurrent() === true,
      onDenied(response) { dispose(); onDenied(response); }});
    return entry.rawQC;
  }
  function sampleDesign(id) {
    const reportModel = importReport(id); if (!reportModel) return null;
    const entry = importReports.get(id);
    if (!entry.design) entry.design = createPrivateSampleDesignHost({reportModel, projectId, fileId: id, cryptoImpl,
      isCurrent: () => !disposed && importReports.get(id) === entry && transport?.isCurrent() === true,
      clientFactory: sampleDesignClientFactory, loadScheduler: scheduleRestore, onDenied(response) { dispose(); onDenied(response); }});
    entry.preprocessing?.preparation.connectHosts();return entry.design;
  }
  function comparisonDesign(id) {
    const sampleHost = sampleDesign(id); if (!sampleHost) return null;
    const entry = importReports.get(id);
    if (!entry.contrasts) entry.contrasts = createPrivateContrastHost({sampleHost, cryptoImpl});
    entry.preprocessing?.preparation.connectHosts();return entry.contrasts;
  }
  function diffSelection(id){return !disposed?importReports.get(id)?.preprocessing?.preparation.selection()||null:null;}
  const idleDownload = () => ({phase: 'idle', file_id: null, code: null});
  let downloadState = idleDownload();
  const listeners = new Set();
  const current = work => !disposed && op === work && generation === work.generation && transport?.isCurrent() === true && !work.controller.signal.aborted;
  const guard = work => {
    if (now() >= work.deadline || work.readDeadline !== null && now() >= work.readDeadline) { cancel(work); bad('cancelled'); }
    if (!current(work)) bad('stale');
  };
  const emit = () => { if (!disposed) for (const fn of listeners) fn(); };
  const update = patch => { state = {...state, ...patch}; emit(); };
  function cancel(work) {
    if (!work) return; work.controller.abort(); clearTimer(work.timer); clearTimer(work.readTimer);
    if (work.kind === 'download') {
      if (work.reader && work.cancelledReader !== work.reader) {
        work.cancelledReader = work.reader; work.cancelledResponse = work.response;
        try { void work.reader.cancel()?.catch(() => {}); } catch {}
      } else if (!work.reader && work.response && work.cancelledResponse !== work.response) {
        work.cancelledResponse = work.response;
        try { void work.response.body?.cancel()?.catch(() => {}); } catch {}
      }
      return;
    }
    try { void (work.reader ? work.reader.cancel() : work.response?.body?.cancel())?.catch(() => {}); } catch {}
  }
  function begin(ms) {
    const work = {controller: new AbortController(), generation, timer: null, readTimer: null, readDeadline: null, reader: null, response: null, deadline: now() + ms};
    work.cancelled = new Promise((_, reject) => work.controller.signal.addEventListener('abort', () => reject(Error('cancelled')), {once: true}));
    void work.cancelled.catch(() => {}); op = work; work.timer = setTimer(() => cancel(work), ms); return work;
  }
  const wait = async (work, pending) => { const result = await Promise.race([pending, work.cancelled]); guard(work); return result; };
  async function request(work, kind, input) {
    guard(work); work.response = null;
    const remaining = Math.floor(work.deadline - now()); if (remaining <= 0) bad('cancelled');
    const ms = Math.min(kind === 'upload' ? 60000 : 15000, remaining);
    work.readDeadline = now() + ms; work.readTimer = setTimer(() => cancel(work), ms);
    try {
      const pending = Promise.resolve(kind === 'list' ? transport.list({signal: work.controller.signal}) : transport[kind](input, {signal: work.controller.signal})).then(response => {
        try { guard(work); } catch (error) { try { void response.body?.cancel()?.catch(() => {}); } catch {} throw error; }
        return response;
      });
      const response = await wait(work, pending); work.response = response;
      if (response.redirected || !/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(response.headers.get('content-type') || '')) bad('uploads_unavailable');
      const cap = kind === 'list' ? 16384 : 4096, length = response.headers.get('content-length');
      if (length !== null && (!/^\d+$/.test(length) || Number(length) > cap)) bad('uploads_unavailable');
      const reader = response.body?.getReader(); if (!reader) bad('uploads_unavailable'); work.reader = reader;
      const chunks = []; let size = 0;
      for (;;) { const part = await wait(work, reader.read()); if (part.done) break; size += part.value.byteLength; if (size > cap) bad('uploads_unavailable'); chunks.push(part.value); }
      const bytes = new Uint8Array(size); let offset = 0; for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
      const body = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes)); guard(work);
      if ([401, 403].includes(response.status) || response.status === 409 && errorCode(response.status, body) !== 'upload_request_conflict' || response.status === 404 && errorCode(response.status, body) === 'project_not_found') {
        const code = errorCode(response.status, body); dispose(); onDenied({status: response.status, body}); bad(code);
      }
      return {status: response.status, body};
    } catch (error) {
      if (!disposed && [401, 403, 409].includes(work.response?.status)) {
        const status = work.response.status; dispose(); onDenied({status, body: {}});
      }
      throw error;
    } finally {
      clearTimer(work.readTimer); work.readTimer = null; work.readDeadline = null;
      try { void work.reader?.cancel()?.catch(() => {}); work.reader?.releaseLock(); } catch {}
      work.reader = null;
    }
  }
  const checkedReceipt = (response, expected) => {
    if (response.status !== 200 || !exact(response.body, ['file']) || !isRawFile(response.body.file, projectId)) bad(errorCode(response.status, response.body));
    const value = response.body.file;
    if (expected && (!['request_id', 'filename', 'bytes', 'sha256', 'format'].every(k => value[k] === expected[k]) || expected.file_id && !same(value, expected))) bad('upload_result_unknown');
    return {...value};
  };
  async function readList(work) {
    const response = await request(work, 'list');
    if (response.status !== 200 || !isRawList(response.body, projectId)) bad(errorCode(response.status, response.body));
    return response.body.items.map(x => ({...x}));
  }
  function finish(work) { if (op === work) { op = null; cancel(work); emit(); } }
  function lostBinding() {
    if (disposed) return true;
    if (transport && !transport.isCurrent()) { dispose(); onDenied({status: 409, body: {}}); return true; }
    return false;
  }
  function revokeURL(lease) {
    urls.delete(lease); clearTimer(lease.timer); lease.timer = null;
    const url = lease.url; lease.url = null;
    if (url !== null) { try { urlImpl.revokeObjectURL(url); } catch {} }
    const anchor = lease.anchor; lease.anchor = null;
    if (anchor) { anchor.removeAttribute?.('href'); anchor.removeAttribute?.('download'); anchor.remove?.(); }
  }
  const revokeURLs = () => { for (const lease of [...urls]) revokeURL(lease); };
  function resetDownload() { revokeURLs(); downloadState = idleDownload(); }
  function canDownload(id) {
    return !disposed && !op && urls.size === 0 && !state.unknown && state.phase !== 'unavailable' && listConfirmed
      && transport?.isCurrent() === true && rawUuid(id) && state.items.some(x => x.file_id === id && isRawFile(x, projectId))
      && typeof documentImpl?.createElement === 'function' && typeof documentImpl?.body?.append === 'function'
      && typeof urlImpl?.createObjectURL === 'function' && typeof urlImpl?.revokeObjectURL === 'function' && typeof BlobImpl === 'function';
  }
  function cancelDownload() {
    if (op?.kind === 'download') cancel(op);
    revokeURLs();
  }
  async function download(id) {
    if (transport && !transport.isCurrent()) { lostBinding(); return false; }
    if (!canDownload(id)) return false;
    const expected = {...state.items.find(x => x.file_id === id)};
    resetDownload();
    const work = begin(60000); work.kind = 'download';
    let buffer = null, lease = null, handedOff = false;
    downloadState = {phase: 'reading', file_id: id, code: null};
    try {
      emit();
      // An emit listener can dispose this project or replace the identity.
      guard(work);
      const pending = Promise.resolve(transport.content(id, {signal: work.controller.signal})).then(response => {
        work.response = response;
        try { guard(work); } catch (error) { cancel(work); throw error; }
        return response;
      });
      const response = await wait(work, pending); guard(work);
      if ([401, 403, 409].includes(response.status)) { dispose(); onDenied({status: response.status, body: {}}); bad('download_unconfirmed'); }
      if (response.status !== 200 || response.redirected) bad('download_unconfirmed');
      const h = response.headers, disposition = h.get('Content-Disposition');
      if (h.get('Content-Type') !== 'application/octet-stream' || h.get('Content-Length') !== String(expected.bytes)
        || h.get('X-Cyomics-File-Sha256') !== expected.sha256 || h.get('Cache-Control') !== 'no-store'
        || h.get('X-Content-Type-Options') !== 'nosniff' || h.get('Referrer-Policy') !== 'no-referrer'
        || typeof disposition !== 'string' || !disposition.startsWith('attachment;') || /[\r\n]/.test(disposition)) bad('download_unconfirmed');
      guard(work); buffer = new Uint8Array(expected.bytes);
      const reader = response.body?.getReader(); if (!reader) bad('download_unconfirmed'); work.reader = reader;
      let size = 0;
      for (;;) {
        guard(work); const part = await wait(work, reader.read()); guard(work); if (part.done) break;
        if (!(part.value instanceof Uint8Array) || size + part.value.byteLength > expected.bytes || size + part.value.byteLength > MAX) bad('download_unconfirmed');
        buffer.set(part.value, size); size += part.value.byteLength;
      }
      if (size !== expected.bytes) bad('download_unconfirmed');
      downloadState = {phase: 'verifying', file_id: id, code: null}; emit(); guard(work);
      if (!await wait(work, verifyRawContent(buffer.buffer, expected, cryptoImpl))) bad('download_unconfirmed');
      guard(work);
      const blob = new BlobImpl([buffer], {type: 'application/octet-stream'}); guard(work);
      lease = {url: null, timer: null, anchor: null}; urls.add(lease);
      guard(work); lease.url = urlImpl.createObjectURL(blob); guard(work);
      lease.anchor = documentImpl.createElement('a'); guard(work);
      lease.anchor.href = lease.url; lease.anchor.download = expected.filename; lease.anchor.style.display = 'none';
      documentImpl.body.append(lease.anchor); guard(work);
      downloadState = {phase: 'handoff', file_id: id, code: null}; emit(); guard(work);
      // This is the final fence before handing verified bytes to the browser.
      const click = lease.anchor.click; guard(work);
      click.call(lease.anchor); handedOff = true; guard(work);
      lease.timer = setTimer(() => {
        const owned = urls.has(lease);
        revokeURL(lease);
        if (owned && !disposed && transport?.isCurrent() === true) emit();
      }, 1000); guard(work);
      downloadState = {phase: 'handed_off', file_id: id, code: null}; emit(); guard(work);
      return true;
    } catch {
      if (!lostBinding() && op === work) { downloadState = {phase: 'unconfirmed', file_id: id, code: 'download_unconfirmed'}; emit(); }
      return false;
    } finally {
      buffer = null;
      try {
        if (work.reader && work.cancelledReader !== work.reader) { work.cancelledReader = work.reader; work.cancelledResponse = work.response; void work.reader.cancel()?.catch(() => {}); }
        work.reader?.releaseLock();
      } catch {}
      work.reader = null;
      if (lease && (!handedOff || !current(work))) revokeURL(lease);
      finish(work);
    }
  }
  async function reload() {
    if (disposed || op || state.unknown) return false;
    if (!transport) { update({phase: 'unavailable', code: 'uploads_unavailable', items: []}); return false; }
    resetDownload(); listConfirmed = false; selectionError = null;
    const work = begin(15000); update({phase: 'loading', code: null, items: []});
    try { const items = await readList(work); guard(work); listConfirmed = true; reconcileImports(items); update({items, phase: 'ready'}); return true; }
    catch { if (!lostBinding() && op === work) { file = null; attempt = null; update({items: [], chosen: null, receipt: null, phase: 'unavailable', code: 'uploads_unavailable'}); } return false; }
    finally { finish(work); }
  }
  function select(selected) {
    if (disposed || op || state.unknown) return false;
    resetDownload();
    generation++; file = null; attempt = null; selectionError = null;
    const actualBytes = Number.isSafeInteger(selected?.size) && selected.size >= 0 ? selected.size : null;
    const code = typeof selected?.name === 'string' && !/\.txt$/i.test(selected.name) ? 'not_txt'
      : !selected || !validTxtName(selected.name) || actualBytes === null || typeof selected.arrayBuffer !== 'function' ? 'invalid_file'
      : actualBytes === 0 ? 'empty_file' : actualBytes > MAX ? 'too_large' : null;
    if (code) {
      selectionError = {code, actualBytes, maxBytes: MAX};
      update({chosen: null, receipt: null, phase: 'invalid', code: 'invalid_upload'}); return false;
    }
    file = selected; update({chosen: {filename: selected.name, bytes: selected.size}, receipt: null, phase: 'chosen', code: null}); return true;
  }
  async function confirm(work) {
    const received = checkedReceipt(await request(work, 'receipt', attempt.request_id), attempt);
    const items = await readList(work); guard(work);
    if (!items.some(x => same(x, received))) bad('upload_result_unknown');
    attempt = null; file = null; listConfirmed = true; reconcileImports(items);
    update({phase: 'saved', code: null, items, receipt: received, chosen: null, unknown: false});
    return true;
  }
  async function save() {
    if (disposed || op || !file || state.unknown || !transport || state.phase === 'unavailable') return false;
    resetDownload(); const work = begin(60000); let postAttempted = false;
    update({phase: 'hashing', code: null});
    try {
      const raw = file; let bytes = await wait(work, raw.arrayBuffer());
      if (!(bytes instanceof ArrayBuffer) || bytes.byteLength !== raw.size || bytes.byteLength > MAX) bad('invalid_upload');
      const sha256 = hex(await wait(work, cryptoImpl.subtle.digest('SHA-256', bytes))); bytes = null;
      const request_id = cryptoImpl.randomUUID(); if (!rawUuid(request_id)) bad('invalid_upload');
      attempt = {request_id, filename: raw.name, bytes: raw.size, sha256, format: 'proteinGroups_txt'};
      const headers = {'Content-Type': 'application/octet-stream', 'Idempotency-Key': request_id, 'X-Cyomics-Filename': txtFilenameHeader(raw.name),
        'X-Cyomics-File-Format': 'proteinGroups_txt', 'X-Cyomics-File-Bytes': String(raw.size), 'X-Cyomics-File-Sha256': sha256, 'X-Cyomics-Request': '1'};
      guard(work); update({phase: 'saving'}); guard(work); postAttempted = true;
      const response = await request(work, 'upload', {file: raw, headers});
      if (![200, 201].includes(response.status)) {
        const code = errorCode(response.status, response.body);
        if ([400, 409, 413, 415, 429].includes(response.status) && code !== 'uploads_unavailable') { attempt = null; update({phase: 'failed', code}); return false; }
        bad('upload_result_unknown');
      }
      if (!exact(response.body, ['file']) || !isRawFile(response.body.file, projectId)) bad('upload_result_unknown');
      const initial = checkedReceipt({...response, status: 200}, attempt); attempt = initial;
      update({phase: 'confirming'}); return await confirm(work);
    } catch {
      if (!lostBinding() && op === work) update({phase: postAttempted ? 'unknown' : 'failed', code: postAttempted ? 'upload_result_unknown' : 'invalid_upload', unknown: postAttempted, receipt: null});
      return false;
    } finally { finish(work); }
  }
  async function check() {
    if (disposed || op || !attempt || !state.unknown || !transport) return false;
    resetDownload(); const work = begin(15000); update({phase: 'confirming', code: null});
    try { return await confirm(work); }
    catch { if (!lostBinding() && op === work) update({phase: 'unknown', code: 'upload_result_unknown', unknown: true, receipt: null}); return false; }
    finally { finish(work); }
  }
  function dispose() {
    if (disposed) return; disposed = true; generation++; const old = op; op = null; cancel(old); file = null; attempt = null;
    listConfirmed = false; selectionError = null; resetDownload();
    transport?.disposePreprocessing?.();
    reconcileImports([]);
    state = {phase: 'disposed', code: null, items: [], chosen: null, receipt: null, unknown: false};
    for (const fn of listeners) fn(); listeners.clear();
  }
  return {select, save, check, reload, dispose, download, canDownload, cancelDownload, importReport, preprocessing, rawQC, sampleDesign, comparisonDesign, diffSelection,
    start() { if (!started) { started = true; return reload(); } return Promise.resolve(false); },
    status() { return {...state, selectionError: selectionError && {...selectionError}, download: {...downloadState}, items: state.items.map(x => ({...x})), chosen: state.chosen && {...state.chosen}, receipt: state.receipt && {...state.receipt}, busy: !!op,
      canChoose: !disposed && !op && !state.unknown && state.phase !== 'unavailable', canSave: !disposed && !!file && !!transport && !op && !state.unknown && state.phase !== 'unavailable'}; },
    subscribe(fn) { if (disposed) return () => {}; listeners.add(fn); return () => listeners.delete(fn); },
  };
}

// The download consumer bounds the stream before calling this checksum helper.
export async function verifyRawContent(buffer, receipt, cryptoImpl = globalThis.crypto) {
  return buffer instanceof ArrayBuffer && isRawFile(receipt, receipt?.project_id) && buffer.byteLength === receipt.bytes
    && hex(await cryptoImpl.subtle.digest('SHA-256', buffer)) === receipt.sha256;
}

export function mountPrivateTxt(root, model, t = zh => zh) {
  const doc = root.ownerDocument, bindings = [];
  const make = (tag, text = '', cls = '') => { const n = doc.createElement(tag); n.textContent = text; n.className = cls; return n; };
  const bind = (n, event, fn) => { n.addEventListener(event, fn); bindings.push([n, event, fn]); };
  const heading = make('h3', t('项目原始文件', 'Project original files'));
  const picker = make('label', t('添加定量表 TXT', 'Add quantification TXT'), 'field'), input = make('input'); input.type = 'file'; input.accept = '.txt'; input.setAttribute('aria-label', t('选择一个定量表 TXT', 'Choose one quantification TXT')); picker.append(input);
  picker.append(make('small', t('单文件：1 字节至 25 MiB（26214400 字节）', 'Single file: 1 byte to 25 MiB (26214400 bytes)')));
  const chosen = make('p', '', 'note'), result = make('p', '', 'signup-state'); result.setAttribute('role', 'status'); result.setAttribute('aria-live', 'polite');
  const actions = make('div', '', 'row'), save = make('button', t('保存到此项目', 'Save to this project'), 'primary'), check = make('button', t('只读查询保存结果', 'Check save result (read-only)')), reload = make('button', t('刷新文件列表', 'Reload files'));
  for (const button of [save, check, reload]) button.type = 'button'; actions.append(save, check, reload);
  const files = make('div'), note = make('p', t('文件保存、导入报告和分析任务分别记录；FASTA 尚未关联。', 'File storage, import reports and analysis tasks are recorded separately. FASTA has not been linked.'), 'note');
  root.replaceChildren(heading, picker, chosen, actions, result, files, note); let disposed = false, rowBindings = [], reportViews = [];
  const clearRows = () => { for (const stop of reportViews) stop(); reportViews = []; for (const [n, fn] of rowBindings) n.removeEventListener('click', fn); rowBindings = []; };
  function paint() {
    if (disposed) return; const v = model.status(); input.disabled = !v.canChoose; save.disabled = !v.canSave; check.hidden = !v.unknown; check.disabled = v.busy; reload.disabled = v.busy || v.unknown;
    chosen.textContent = v.chosen ? v.chosen.filename + ' · ' + v.chosen.bytes + ' bytes · ' + (v.unknown ? t('保存状态待确认', 'Save status unconfirmed') : t('当前选择', 'Current selection')) : '';
    const messages = {loading: ['正在读取服务端文件列表…', 'Reading server files…'], unavailable: ['暂时无法确认服务器文件状态，请重新检查。', 'Server file status cannot be confirmed right now. Please check again.'], invalid: ['请选择一个有效 TXT（1 字节至 25 MiB）。', 'Choose one valid TXT (1 byte to 25 MiB).'], hashing: ['正在校验原始字节…', 'Checking original bytes…'], saving: ['正在保存原始文件…', 'Saving original file…'], confirming: ['正在回读确认保存结果…', 'Confirming the server receipt…'], saved: ['原始文件已保存并回读确认，导入状态见文件报告。', 'Original file stored and confirmed by readback. See its import report for parsing status.'], unknown: ['保存结果未知，请只读查询；暂勿重复保存。', 'Save outcome unknown. Use read-only lookup; do not resubmit.'], failed: ['本次保存未确认，请检查错误后手动操作。', 'Save not confirmed. Review the error before another manual action.']};
    result.textContent = messages[v.phase] ? t(...messages[v.phase]) + (v.code ? ' (' + v.code + ')' : '') : '';
    if (v.phase === 'invalid' && v.selectionError) {
      const reasons = {empty_file: ['文件为空。', 'The file is empty.'], not_txt: ['仅支持 TXT 文件。', 'Only TXT files are supported.'],
        too_large: ['文件超过单文件大小上限。', 'The file exceeds the single-file size limit.'], invalid_file: ['文件无法读取，或文件名/大小无效。', 'The file cannot be read, or its name or size is invalid.']};
      const bytes = v.selectionError.actualBytes, actual = Number.isSafeInteger(bytes) && bytes >= 0 ? bytes + ' bytes (' + (bytes / 1048576).toFixed(2) + ' MiB)' : t('未知', 'Unknown');
      result.textContent = t(...(reasons[v.selectionError.code] || reasons.invalid_file)) + ' ' + t('实际大小', 'Actual size') + ': ' + actual
        + ' · ' + t('允许范围', 'Allowed range') + ': 1–' + MAX + ' bytes (25 MiB)';
    }
    const downloadMessages = {reading: ['正在读取原始 TXT…', 'Reading original TXT…'], verifying: ['正在校验下载字节…', 'Verifying download bytes…'], handoff: ['正在交给浏览器保存…', 'Handing off to the browser…'], handed_off: ['已校验并交给浏览器保存。', 'Verified and handed to the browser for saving.'], unconfirmed: ['未能确认下载，请手动重试。', 'Download could not be confirmed. Retry manually.']};
    if (downloadMessages[v.download.phase]) result.textContent = t(...downloadMessages[v.download.phase]);
    clearRows();
    files.replaceChildren();
    if (!v.items.length) files.append(make('p', v.phase === 'ready' ? t('服务端暂无已保存文件。', 'No stored server files.') : '', 'note'));
    for (const item of v.items) {
      const row = make('div', '', 'list-row'), info = make('div', '', 'private-txt-file-info'); row.setAttribute('data-private-txt-file', '');
      info.append(make('strong', item.filename), make('p', item.bytes + ' bytes · ' + item.createdAt, 'note'));
      const details = make('details'), summary = make('summary', t('文件详情', 'File details')); details.append(summary, make('p', 'file_id: ' + item.file_id, 'note'), make('p', 'SHA256: ' + item.sha256, 'sequence')); info.append(details); row.append(info);
      const download = make('button', t('下载原始 TXT', 'Download original TXT'), 'private-txt-file-download'); download.type = 'button'; download.disabled = !model.canDownload(item.file_id);
      const click = () => { if (!disposed) void model.download(item.file_id); }; download.addEventListener('click', click); rowBindings.push([download, click]); row.append(download);
      const reportModel = model.importReport(item.file_id);
      if (reportModel) {
        const reportRoot = make('div'); info.append(reportRoot);
        reportViews.push(mountPrivateImportReport(reportRoot, reportModel, {binding: {project_id: item.project_id, raw_file_id: item.file_id, expected_raw_sha256: item.sha256}, t, preprocessing: model.preprocessing?.(item.file_id) || undefined}));
        const qc = model.rawQC?.(item.file_id);
        if (qc) { const qcRoot = make('div'); info.append(qcRoot); reportViews.push(mountPrivateRawQC(qcRoot, qc, {t})); }
        const design = model.sampleDesign?.(item.file_id);
        if (design) { const designRoot = make('div'); info.append(designRoot); reportViews.push(mountPrivateSampleDesignHost(designRoot, design, {t})); }
        const comparisons = model.comparisonDesign?.(item.file_id);
        if (comparisons) { const contrastRoot = make('div'); info.append(contrastRoot); reportViews.push(mountPrivateContrastHost(contrastRoot, comparisons, {t})); }
        void reportModel.restore();
      }
      files.append(row);
    }
  }
  bind(input, 'change', () => { if (!disposed) { model.select(input.files?.[0]); input.value = ''; } });
  bind(save, 'click', () => { if (!disposed) void model.save(); }); bind(check, 'click', () => { if (!disposed) void model.check(); }); bind(reload, 'click', () => { if (!disposed) void model.reload(); });
  const unsubscribe = model.subscribe(paint); paint(); void model.start();
  return () => { if (disposed) return; disposed = true; model.cancelDownload(); unsubscribe(); clearRows(); for (const [n, event, fn] of bindings) n.removeEventListener(event, fn); input.value = ''; root.replaceChildren(); };
}
