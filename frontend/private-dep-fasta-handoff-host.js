import {mountDEPFastaHandoff} from './dep-fasta-handoff-component.js';
import {quantBinding} from './fasta-quant-mapping-contract.mjs';
import {canonicalJSON,uuid} from './sample-design-contract.mjs';
// Reads only the existing verified models. This host is not an authorization or
// scientific validator; the backend owns complete source/FASTA revalidation.
export function mountPrivateDEPFastaHandoffHost(root,{projectId,fileId,mapping=null,results=null,getSource,isCurrent=()=>false,contextSignal=null,
 subscribeSource=()=>()=>{},clientFactory=null,onDenied=()=>{},cryptoImpl=globalThis.crypto,documentImpl=root.ownerDocument,urlImpl=globalThis.URL,
 BlobImpl=globalThis.Blob,setTimer=setTimeout,clearTimer=clearTimeout,t=zh=>zh}={}){
 const doc=root.ownerDocument,note=doc.createElement('p'),slot=doc.createElement('div');note.className='lrt-note';note.setAttribute('role','status');root.append(note,slot);
 let disposed=false,ready=false,generation=0,entry=null,pending=null,scope=null;
 try{scope=cryptoImpl.randomUUID();if(!uuid(scope))scope=null;}catch{}
 const component=mountDEPFastaHandoff(slot,{enabled:false,t});
 const live=()=>{try{return !disposed&&ready&&contextSignal?.aborted===false&&isCurrent()===true;}catch{return false;}};
 function capture(){try{
  if(!live()||!uuid(projectId)||!uuid(fileId)||!scope||typeof clientFactory!=='function')return null;
  const p=results?.classificationPresentation?.(),m=mapping?.status?.(),full=getSource?.();
  if(!p||p.isCurrent?.()!==true||p.contextSignal?.aborted!==false||p.parentPresentation?.isCurrent?.()!==true||p.parentPresentation.contextSignal?.aborted!==false)return null;
  const parent=p.parentMetadata,d=p.derivedMetadata,c=p.parentPresentation.verifiedOwnerContext,q=m?.meta?.inputs?.quantification,f=full?.file,v=full?.importDTO?.datasetVersion;
  if(m?.phase!=='complete'||m.busy||m.unknown||!m.document||!m.meta||m.meta.status!=='stored'||!uuid(m.meta.mapping_version_id)||m.meta.project_id!==projectId||m.meta.created_by!==parent?.created_by)return null;
  if(!f||!v||f.project_id!==projectId||f.file_id!==fileId||v.project_id!==projectId||v.file_id!==fileId||!q||q.project_id!==projectId||q.raw_file_id!==fileId||q.dataset_version_id!==v.dataset_version_id||q.raw_sha256!==f.sha256)return null;
  if(c?.project_id!==projectId||c.source?.file_id!==fileId||c.source.parent_dataset_version_id!==v.dataset_version_id||c.source.source_sha256!==f.sha256||canonicalJSON(c.selection)!==canonicalJSON(parent?.selection))return null;
  if(parent?.project_id!==projectId||!uuid(parent.created_by)||!uuid(parent.result_id)||!uuid(parent.selection?.processed_dataset_version_id)||!uuid(parent.selection?.sample_design_version_id)||!uuid(parent.selection?.sample_design_request_id))return null;
  if(d?.project_id!==projectId||!uuid(d.derived_version_id)||d.status!=='CLASSIFIED'||d.DE_members_evaluated!==true||d.created_by!==parent.created_by||d.source_result_id!==parent.result_id||d.source_manifest_sha256!==parent.manifest_sha256)return null;
  const key=canonicalJSON({full,parent,d,mapping:m.meta});return {key,document:m.document,full,p,m,context:{owner_scope:scope,project_id:projectId,derived_version_id:d.derived_version_id,mapping_version_id:m.meta.mapping_version_id}};
 }catch{return null;}}
 function same(a,b){return !!a&&!!b&&a.key===b.key&&a.document===b.document&&a.p.contextSignal===b.p.contextSignal&&a.p.parentPresentation.contextSignal===b.p.parentPresentation.contextSignal;}
 function revoke(e){const x=e?.download;if(!x)return;e.download=null;clearTimer(x.timer);try{urlImpl.revokeObjectURL(x.url);}catch{}try{x.anchor.removeAttribute?.('href');x.anchor.removeAttribute?.('download');x.anchor.remove();}catch{}x.detach?.();}
 function discard(){generation++;pending=null;const old=entry;entry=null;if(old){old.controller.abort();old.client?.dispose();revoke(old);for(const stop of old.stops)stop();}component.update({enabled:false,t});}
 function owns(e){return entry===e&&!e.controller.signal.aborted&&same(e.snapshot,capture());}
 function guard(e,signal){if(!owns(e)||signal?.aborted)throw Error('handoff_context_changed');}
 function exportBytes(e,{context,signal,name,media_type,sha256,bytes}){
  guard(e,signal);if(canonicalJSON(context)!==canonicalJSON(e.snapshot.context)||!(bytes instanceof Uint8Array)||!['queries.fasta','membership.tsv'].includes(name)||!/^[0-9a-f]{64}$/.test(sha256))throw Error('handoff_export_unconfirmed');
  // Approved HTTP client has already checked length, SHA and media. Copy before
  // it zeroes its owned bytes; never retain those source buffers in a timer.
  revoke(e);const copy=new Uint8Array(bytes);let url=null,anchor=null;
  try{guard(e,signal);const blob=new BlobImpl([copy],{type:media_type});copy.fill(0);guard(e,signal);url=urlImpl.createObjectURL(blob);guard(e,signal);anchor=documentImpl.createElement('a');anchor.style.display='none';anchor.href=url;anchor.download=name;documentImpl.body.append(anchor);guard(e,signal);
   const aborted=()=>revoke(e),x={url,anchor,timer:null,detach:()=>{signal.removeEventListener('abort',aborted);e.controller.signal.removeEventListener('abort',aborted);}};e.download=x;signal.addEventListener('abort',aborted,{once:true});e.controller.signal.addEventListener('abort',aborted,{once:true});anchor.click();guard(e,signal);x.timer=setTimer(()=>revoke(e),1000);return {status:'handed_off'};
  }catch(error){copy.fill(0);if(e.download)revoke(e);else{if(url!==null)try{urlImpl.revokeObjectURL(url);}catch{}try{anchor?.remove();}catch{}}throw error;}
 }
 function disabled(){note.textContent=ready?t('请先在现有“定量表／FASTA／ID 关联”中读取并核对本文件的完整关联结果。交接不会重新上传文件。','First read and verify this file’s complete mapping in Quantification / FASTA / ID mapping. No file is uploaded again.'):'';}
 function sync(){if(disposed)return;const s=capture();if(entry&&owns(entry))return;if(s&&pending&&same(s,pending.snapshot))return;discard();disabled();if(!s)return;
  const ticket={snapshot:s,generation};pending=ticket;note.textContent=t('正在核对当前定量文件与已验证 ID 关联；不会自动读取交接接口。','Checking the current table against its verified ID mapping; no handoff request runs automatically.');
  void quantBinding(s.full,cryptoImpl).then(q=>{
   if(disposed||pending!==ticket||generation!==ticket.generation||!same(s,capture()))return;
   if(canonicalJSON(q)!==canonicalJSON(s.m.meta.inputs.quantification)){pending=null;disabled();return;}
   pending=null;const e={snapshot:s,controller:new AbortController(),stops:[],client:null,download:null};entry=e;
   const abort=()=>{if(entry===e){discard();disabled();}};
   for(const signal of new Set([contextSignal,s.p.contextSignal,s.p.parentPresentation.contextSignal])){signal.addEventListener('abort',abort,{once:true});e.stops.push(()=>signal.removeEventListener('abort',abort));}
   try{e.client=clientFactory({context:s.context,contextSignal:e.controller.signal,isCurrent:()=>owns(e),exportBytes:args=>exportBytes(e,args),onDenied});
    guard(e);if(!e.client)throw Error();component.update({enabled:true,context:s.context,contextSignal:e.controller.signal,isCurrent:()=>owns(e),onPrepare:e.client.onPrepare,onRecheck:e.client.onRecheck,onDownload:e.client.onDownload,t});note.textContent=t('仅准备注释输入；完整来源和 FASTA 证据由后端核对，BLAST 尚未运行。','Prepare annotation input only. The backend checks complete source and FASTA evidence. BLAST has not run.');
   }catch{if(entry===e){discard();disabled();}}
  }).catch(()=>{if(pending===ticket){pending=null;disabled();}});
 }
 const stops=[mapping?.subscribe?.(sync)||(()=>{}),results?.subscribe?.(sync)||(()=>{}),subscribeSource(sync)||(()=>{})];
 const ownerAbort=()=>{discard();disabled();};contextSignal?.addEventListener('abort',ownerAbort,{once:true});
 return Object.freeze({sync,setReady(value){if(disposed)return;if(ready!== (value===true)){ready=value===true;sync();}else if(ready)sync();},status(){return Object.freeze({phase:disposed?'disposed':entry&&owns(entry)?'ready':pending?'checking':'waiting',context:entry?.snapshot.context||null});},dispose(){if(disposed)return;ready=false;discard();disposed=true;for(const stop of stops)stop();contextSignal?.removeEventListener('abort',ownerAbort);component.dispose();root.textContent='';}});
}
