import {exact,uuid,freeze,canonicalJSON} from './sample-design-contract.mjs';

const MAX_FILE=27262976,MAX_TOTAL=134217728;
const MEDIA=freeze({"R-session.txt":"text/plain;charset=utf-8","heatmap-audit.tsv":"text/tab-separated-values;charset=utf-8","heatmap-matrix.tsv":"text/tab-separated-values;charset=utf-8","heatmap-row-map.tsv":"text/tab-separated-values;charset=utf-8","heatmap-samples.tsv":"text/tab-separated-values;charset=utf-8","heatmap-zscore.tsv":"text/tab-separated-values;charset=utf-8","heatmap.png":"image/png","heatmap.svg":"image/svg+xml"});
const NAMES=Object.keys(MEDIA);
const CONTEXT=['project_id','actor_id','source_result_id','source_metadata_sha256','source_manifest_sha256','derived_version_id','derived_metadata_sha256','derived_manifest_sha256','source_binding_sha256','parameters_sha256'];
const DESCRIPTOR=['schemaVersion','contract','project_id','created_by','source_result_id','derived_version_id','source_metadata_sha256','source_manifest_sha256','derived_metadata_sha256','derived_manifest_sha256','source_binding_sha256','parameters_sha256','artifacts'];
const hash=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v);
const need=v=>{if(!v)throw Error('r_heatmap_view_invalid');};
function context(value){
 need(exact(value,CONTEXT));for(const key of CONTEXT)need(key.endsWith('_sha256')?hash(value[key]):uuid(value[key]));
 return freeze(structuredClone(value));
}
function descriptor(value,ctx){
 need(exact(value,DESCRIPTOR)&&value.schemaVersion===1&&value.contract==='r_heatmap_view_v0.1'&&value.created_by===ctx.actor_id);
 for(const key of CONTEXT.filter(k=>k!=='actor_id'))need(value[key]===ctx[key]);
 need(Array.isArray(value.artifacts)&&value.artifacts.length===8);let total=0;
 for(const [i,pin] of value.artifacts.entries()){
  need(exact(pin,['name','bytes','sha256','media_type'])&&pin.name===NAMES[i]&&pin.media_type===MEDIA[pin.name]&&Number.isSafeInteger(pin.bytes)&&pin.bytes>=1&&pin.bytes<=MAX_FILE&&hash(pin.sha256));total+=pin.bytes;
 }need(total<=MAX_TOTAL);return freeze(structuredClone(value));
}
function png(bytes){
 need(bytes.length>=33&&[137,80,78,71,13,10,26,10].every((n,i)=>bytes[i]===n));
 const data=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
 need(data.getUint32(8)===13&&[73,72,68,82].every((n,i)=>bytes[12+i]===n)&&data.getUint32(16)===2400&&data.getUint32(20)===1350);
}

// Presentation only. The caller supplies Owner-authorized, manifest-verified
// metadata and callbacks. This module has no HTTP, computation or task API.
export function mountRHeatmapView(root,initial={}){
 const doc=root.ownerDocument;let active=null,panelNode=null,disposed=false,phase='waiting',ready=Promise.resolve(false);
 const make=(tag,text='',cls='')=>{const n=doc.createElement(tag);n.textContent=text;n.className=cls;return n;};
 function revoke(w){if(w.image){if(typeof w.image.removeAttribute==='function')w.image.removeAttribute('src');else w.image.src='';w.image.remove();w.image=null;}if(w.url!==null){try{w.options.urlImpl.revokeObjectURL(w.url);}catch{}w.url=null;}}
 function clear(){const w=active;active=null;panelNode?.remove();panelNode=null;if(!w)return;w.controller.abort();w.options.contextSignal?.removeEventListener('abort',dispose);w.stops.splice(0).forEach(stop=>stop());revoke(w);w.panel.textContent='';w.panel.remove();}
 function dispose(){if(disposed)return;disposed=true;clear();phase='disposed';}
 function current(w){try{return !disposed&&active===w&&!w.controller.signal.aborted&&!w.options.contextSignal.aborted&&w.options.isCurrent(w.context)===true&&canonicalJSON(w.options.verifiedContext)===w.contextKey&&canonicalJSON(w.options.descriptor)===w.descriptorKey;}catch{return false;}}
 function check(w){if(!current(w)){if(active===w)dispose();throw Error('r_heatmap_context_changed');}}
 const wait=async(w,promise)=>{const value=await Promise.race([promise,w.aborted]);check(w);return value;};
 const bind=(w,n,event,fn)=>{n.addEventListener(event,fn);w.stops.push(()=>n.removeEventListener(event,fn));};
 function failed(w){if(!current(w)){if(active===w)dispose();return;}revoke(w);w.downloads?.remove();w.downloadNotice?.remove();phase='unavailable';w.note.textContent=w.t('R 热图读取或校验未通过，未显示图像。','The R heatmap image could not be read or verified. No image is displayed.');}
 function downloads(w){
  const section=make('div','','rhv-downloads'),notice=make('p',w.t('下载原始 SVG、定量矩阵、显示用 Z-score、行关联、样本信息、绘图审计与 R 环境信息。','Download the original SVG, quantitative matrix, display Z-scores, row mapping, samples, plot audit and R session information.'));
  notice.setAttribute('role','status');notice.setAttribute('aria-live','polite');notice.setAttribute('data-rhv-download-state','status');const buttons=[];
  const labels={'R-session.txt':['R 环境信息','R session'],'heatmap-audit.tsv':['绘图审计','Plot audit'],'heatmap-matrix.tsv':['原始 log2 定量矩阵','Original log2 matrix'],'heatmap-row-map.tsv':['原始行关联','Source row mapping'],'heatmap-samples.tsv':['样本与分组信息','Samples and groups'],'heatmap-zscore.tsv':['显示用行 Z-score','Row Z-scores for display'],'heatmap.svg':['SVG 图','SVG figure']};
  for(const pin of w.descriptor.artifacts.filter(p=>p.name!=='heatmap.png')){
   const button=make('button',w.t(...labels[pin.name]));button.type='button';button.setAttribute('data-rhv-download',pin.name);button.disabled=typeof w.options.onDownload!=='function';section.append(button);buttons.push(button);
   bind(w,button,'click',async()=>{if(!current(w)){dispose();return;}if(phase!=='ready'||w.downloading||typeof w.options.onDownload!=='function')return;w.downloading=true;buttons.forEach(b=>{b.disabled=true;});notice.textContent=w.t('正在请求授权下载…','Requesting an authorized download…');
    try{const result=await wait(w,Promise.resolve(w.options.onDownload({name:pin.name,pin,descriptor:w.descriptor,verifiedContext:w.context,signal:w.controller.signal})));notice.textContent=exact(result,['status'])&&result.status==='handed_off'?w.t('已交给浏览器，请查看浏览器下载状态。','Handed to the browser; check its download status.'):w.t('下载尚未确认。','Download is unconfirmed.');}
    catch{if(current(w))notice.textContent=w.t('下载尚未确认。','Download is unconfirmed.');else if(active===w)dispose();}
    finally{if(current(w)){w.downloading=false;buttons.forEach(b=>{b.disabled=false;});}}
   });
  }
  const repro=make('button',w.t('R脚本与数据下载','Download R scripts and data'));repro.type='button';repro.setAttribute('data-rhv-reproducibility','download');repro.disabled=typeof w.options.onReproducibility!=='function';section.append(repro);buttons.push(repro);
  bind(w,repro,'click',async()=>{if(!current(w)){dispose();return;}if(phase!=='ready'||w.downloading||typeof w.options.onReproducibility!=='function')return;w.downloading=true;buttons.forEach(b=>{b.disabled=true;});notice.textContent=w.t('正在请求授权下载…','Requesting an authorized download…');
   try{const result=await wait(w,Promise.resolve(w.options.onReproducibility({descriptor:w.descriptor,verifiedContext:w.context,signal:w.controller.signal})));notice.textContent=exact(result,['status'])&&result.status==='handed_off'?w.t('已交给浏览器，请查看浏览器下载状态。','Handed to the browser; check its download status.'):w.t('下载尚未确认。','Download is unconfirmed.');}
   catch{if(current(w))notice.textContent=w.t('下载尚未确认。','Download is unconfirmed.');else if(active===w)dispose();}
   finally{if(current(w)){w.downloading=false;buttons.forEach(b=>{b.disabled=false;});}}
  });
  if(typeof w.options.onDownload!=='function')notice.textContent=w.t('下载尚未接入授权通道。','Authorized downloads are not connected.');
  w.panel.append(section,notice);w.downloads=section;w.downloadNotice=notice;
 }
 function update(options={}){
  if(disposed)return Promise.resolve(false);clear();phase='waiting';
  const t=options.t||((zh)=>zh),panel=make('section','','rhv'),note=make('p','','rhv-note');panelNode=panel;panel.setAttribute('role','region');panel.setAttribute('aria-label',t('R 热图','R heatmap plot'));note.setAttribute('role','status');note.setAttribute('aria-live','polite');note.setAttribute('data-rhv-state','status');panel.append(make('h3',t('R 热图','R heatmap plot')),note);root.append(panel);
  let w;
  try{
   const ctx=context(options.verifiedContext);need(typeof options.isCurrent==='function'&&options.isCurrent(ctx)===true&&options.contextSignal&&typeof options.contextSignal.aborted==='boolean'&&!options.contextSignal.aborted&&typeof options.contextSignal.addEventListener==='function'&&typeof options.contextSignal.removeEventListener==='function');
   const desc=options.descriptor===null?null:descriptor(options.descriptor,ctx),controller=new AbortController();
   w={panel,note,t,options:{...options,cryptoImpl:options.cryptoImpl||globalThis.crypto,urlImpl:options.urlImpl||globalThis.URL,BlobImpl:options.BlobImpl||globalThis.Blob},context:ctx,contextKey:canonicalJSON(ctx),descriptor:desc,descriptorKey:canonicalJSON(desc),controller,stops:[],url:null,image:null,downloads:null,downloading:false};
   w.aborted=new Promise((_,reject)=>controller.signal.addEventListener('abort',()=>reject(Error('r_heatmap_cancelled')),{once:true}));void w.aborted.catch(()=>{});active=w;options.contextSignal.addEventListener('abort',dispose,{once:true});check(w);
   if(desc===null){phase='empty';note.textContent=t('暂无已保存的 R 热图。','No saved R heatmap plot is available.');return ready=Promise.resolve(false);}
   need(typeof options.readPNG==='function'&&typeof w.options.cryptoImpl?.subtle?.digest==='function'&&typeof w.options.urlImpl?.createObjectURL==='function'&&typeof w.options.urlImpl?.revokeObjectURL==='function'&&typeof w.options.BlobImpl==='function');
  }catch{phase='unavailable';note.textContent=t('热图来源信息或读取通道未通过校验，未显示图像。','The plot source or read channel could not be verified. No image is displayed.');return ready=Promise.resolve(false);}
  phase='loading';note.textContent=t('正在读取并校验 R 原始热图…','Reading and verifying the original R heatmap image…');
  ready=(async()=>{try{
   const value=await wait(w,Promise.resolve(w.options.readPNG({descriptor:w.descriptor,verifiedContext:w.context,signal:w.controller.signal}))),pin=w.descriptor.artifacts.find(p=>p.name==='heatmap.png');
   need(exact(value,['body'])&&value.body instanceof Uint8Array&&value.body.byteLength===pin.bytes);const bytes=new Uint8Array(value.body);png(bytes);
   const digest=[...new Uint8Array(await wait(w,w.options.cryptoImpl.subtle.digest('SHA-256',bytes)))].map(n=>n.toString(16).padStart(2,'0')).join('');need(digest===pin.sha256);check(w);
   const blob=new w.options.BlobImpl([bytes],{type:'image/png'});check(w);w.url=w.options.urlImpl.createObjectURL(blob);need(typeof w.url==='string'&&w.url.startsWith('blob:'));check(w);
   const image=make('img');w.image=image;image.alt=t('R pheatmap 生成的原始热图','Original heatmap generated by R pheatmap');image.width=2400;image.height=1350;image.setAttribute('data-rhv-image','png');
   const decoded=new Promise(resolve=>{bind(w,image,'load',()=>{try{check(w);resolve(true);}catch{resolve(false);}});bind(w,image,'error',()=>{failed(w);resolve(false);});});
   phase='rendering';w.panel.append(image);image.src=w.url;const loaded=await wait(w,decoded);if(!loaded||!w.image||w.url===null)return false;check(w);phase='ready';note.textContent=t('R pheatmap 原图；行 Z-score 只作显示，原 log2 定量与差异统计不变；缺失灰色。','Original R pheatmap image. Row Z-scores are for display only; original log2 quantities and differential statistics are unchanged. Missing values are grey.');downloads(w);return true;
  }catch{if(active===w)failed(w);else revoke(w);return false;}})();return ready;
 }
 update(initial);return Object.freeze({update,dispose,get ready(){return ready;},view(){if(active&&!current(active))dispose();return freeze({phase,has_image:phase==='ready'&&!!active?.image});}});
}
