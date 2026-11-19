import {exact,uuid,freeze,canonicalJSON} from './sample-design-contract.mjs';

const MAX_FILE=27262976,MAX_TOTAL=134217728;
const MEDIA=freeze({'R-session.txt':'text/plain;charset=utf-8','volcano-audit.tsv':'text/tab-separated-values;charset=utf-8','volcano-points.tsv':'text/tab-separated-values;charset=utf-8','volcano.png':'image/png','volcano.svg':'image/svg+xml'});
const NAMES=Object.keys(MEDIA);
const CONTEXT=['project_id','actor_id','source_result_id','source_metadata_sha256','source_manifest_sha256','derived_version_id','derived_metadata_sha256','derived_manifest_sha256','source_binding_sha256','parameters_sha256'];
const DESCRIPTOR=['schemaVersion','contract','project_id','created_by','source_result_id','derived_version_id','source_metadata_sha256','source_manifest_sha256','derived_metadata_sha256','derived_manifest_sha256','source_binding_sha256','parameters_sha256','artifacts'];
const hash=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v);
const need=v=>{if(!v)throw Error('r_volcano_view_invalid');};
function context(value){
 need(exact(value,CONTEXT));for(const key of CONTEXT)need(key.endsWith('_sha256')?hash(value[key]):uuid(value[key]));
 return freeze(structuredClone(value));
}
function descriptor(value,ctx){
 need(exact(value,DESCRIPTOR)&&value.schemaVersion===1&&value.contract==='r_volcano_view_v0.1'&&value.created_by===ctx.actor_id);
 for(const key of CONTEXT.filter(k=>k!=='actor_id'))need(value[key]===ctx[key]);
 need(Array.isArray(value.artifacts)&&value.artifacts.length===5);let total=0;
 for(const [i,pin] of value.artifacts.entries()){
  need(exact(pin,['name','bytes','sha256','media_type'])&&pin.name===NAMES[i]&&pin.media_type===MEDIA[pin.name]&&Number.isSafeInteger(pin.bytes)&&pin.bytes>=1&&pin.bytes<=MAX_FILE&&hash(pin.sha256));total+=pin.bytes;
 }need(total<=MAX_TOTAL);return freeze(structuredClone(value));
}
function png(bytes){
 need(bytes.length>=33&&[137,80,78,71,13,10,26,10].every((n,i)=>bytes[i]===n));
 const data=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
 need(data.getUint32(8)===13&&[73,72,68,82].every((n,i)=>bytes[12+i]===n)&&data.getUint32(16)===2400&&data.getUint32(20)===1800);
}

// Presentation only. The caller supplies Owner-authorized, manifest-verified
// metadata and callbacks. This module has no HTTP, computation or task API.
export function mountRVolcanoView(root,initial={}){
 const doc=root.ownerDocument;let active=null,panelNode=null,disposed=false,phase='waiting',ready=Promise.resolve(false);
 const make=(tag,text='',cls='')=>{const n=doc.createElement(tag);n.textContent=text;n.className=cls;return n;};
 function revoke(w){if(w.image){if(typeof w.image.removeAttribute==='function')w.image.removeAttribute('src');else w.image.src='';w.image.remove();w.image=null;}if(w.url!==null){try{w.options.urlImpl.revokeObjectURL(w.url);}catch{}w.url=null;}}
 function clear(){const w=active;active=null;panelNode?.remove();panelNode=null;if(!w)return;w.controller.abort();w.options.contextSignal?.removeEventListener('abort',dispose);w.stops.splice(0).forEach(stop=>stop());revoke(w);w.panel.textContent='';w.panel.remove();}
 function dispose(){if(disposed)return;disposed=true;clear();phase='disposed';}
 function current(w){try{return !disposed&&active===w&&!w.controller.signal.aborted&&!w.options.contextSignal.aborted&&w.options.isCurrent(w.context)===true&&canonicalJSON(w.options.verifiedContext)===w.contextKey&&canonicalJSON(w.options.descriptor)===w.descriptorKey;}catch{return false;}}
 function check(w){if(!current(w)){if(active===w)dispose();throw Error('r_volcano_context_changed');}}
 const wait=async(w,promise)=>{const value=await Promise.race([promise,w.aborted]);check(w);return value;};
 const bind=(w,n,event,fn)=>{n.addEventListener(event,fn);w.stops.push(()=>n.removeEventListener(event,fn));};
 function failed(w){if(!current(w)){if(active===w)dispose();return;}revoke(w);w.downloads?.remove();w.downloadNotice?.remove();phase='unavailable';w.note.textContent=w.t('R 火山图读取或校验未通过，未显示图像。','The R volcano image could not be read or verified. No image is displayed.');}
 function downloads(w){
  const section=make('div','','rvv-downloads'),notice=make('p',w.t('下载原始 SVG、点数据、绘图审计与 R 环境信息。','Download the original SVG, point data, plot audit and R session information.'));
  notice.setAttribute('role','status');notice.setAttribute('aria-live','polite');notice.setAttribute('data-rvv-download-state','status');const buttons=[];
  const labels={'R-session.txt':['R 环境信息','R session'],'volcano-audit.tsv':['绘图审计','Plot audit'],'volcano-points.tsv':['点数据','Point data'],'volcano.svg':['SVG 图','SVG figure']};
  for(const pin of w.descriptor.artifacts.filter(p=>p.name!=='volcano.png')){
   const button=make('button',w.t(...labels[pin.name]));button.type='button';button.setAttribute('data-rvv-download',pin.name);button.disabled=typeof w.options.onDownload!=='function';section.append(button);buttons.push(button);
   bind(w,button,'click',async()=>{if(!current(w)){dispose();return;}if(phase!=='ready'||w.downloading||typeof w.options.onDownload!=='function')return;w.downloading=true;buttons.forEach(b=>{b.disabled=true;});notice.textContent=w.t('正在请求授权下载…','Requesting an authorized download…');
    try{const result=await wait(w,Promise.resolve(w.options.onDownload({name:pin.name,pin,descriptor:w.descriptor,verifiedContext:w.context,signal:w.controller.signal})));notice.textContent=exact(result,['status'])&&result.status==='handed_off'?w.t('已交给浏览器，请查看浏览器下载状态。','Handed to the browser; check its download status.'):w.t('下载尚未确认。','Download is unconfirmed.');}
    catch{if(current(w))notice.textContent=w.t('下载尚未确认。','Download is unconfirmed.');else if(active===w)dispose();}
    finally{if(current(w)){w.downloading=false;buttons.forEach(b=>{b.disabled=false;});}}
   });
  }
  const repro=make('button',w.t('R脚本与数据下载','Download R scripts and data'));repro.type='button';repro.setAttribute('data-rvv-reproducibility','download');repro.disabled=typeof w.options.onReproducibility!=='function';section.append(repro);buttons.push(repro);
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
  const t=options.t||((zh)=>zh),panel=make('section','','rvv'),note=make('p','','rvv-note');panelNode=panel;panel.setAttribute('role','region');panel.setAttribute('aria-label',t('R 火山图','R volcano plot'));note.setAttribute('role','status');note.setAttribute('aria-live','polite');note.setAttribute('data-rvv-state','status');panel.append(make('h3',t('R 火山图','R volcano plot')),note);root.append(panel);
  let w;
  try{
   const ctx=context(options.verifiedContext);need(typeof options.isCurrent==='function'&&options.isCurrent(ctx)===true&&options.contextSignal&&typeof options.contextSignal.aborted==='boolean'&&!options.contextSignal.aborted&&typeof options.contextSignal.addEventListener==='function'&&typeof options.contextSignal.removeEventListener==='function');
   const desc=options.descriptor===null?null:descriptor(options.descriptor,ctx),controller=new AbortController();
   w={panel,note,t,options:{...options,cryptoImpl:options.cryptoImpl||globalThis.crypto,urlImpl:options.urlImpl||globalThis.URL,BlobImpl:options.BlobImpl||globalThis.Blob},context:ctx,contextKey:canonicalJSON(ctx),descriptor:desc,descriptorKey:canonicalJSON(desc),controller,stops:[],url:null,image:null,downloads:null,downloading:false};
   w.aborted=new Promise((_,reject)=>controller.signal.addEventListener('abort',()=>reject(Error('r_volcano_cancelled')),{once:true}));void w.aborted.catch(()=>{});active=w;options.contextSignal.addEventListener('abort',dispose,{once:true});check(w);
   if(desc===null){phase='empty';note.textContent=t('暂无已保存的 R 火山图。','No saved R volcano plot is available.');return ready=Promise.resolve(false);}
   need(typeof options.readPNG==='function'&&typeof w.options.cryptoImpl?.subtle?.digest==='function'&&typeof w.options.urlImpl?.createObjectURL==='function'&&typeof w.options.urlImpl?.revokeObjectURL==='function'&&typeof w.options.BlobImpl==='function');
  }catch{phase='unavailable';note.textContent=t('火山图来源信息或读取通道未通过校验，未显示图像。','The plot source or read channel could not be verified. No image is displayed.');return ready=Promise.resolve(false);}
  phase='loading';note.textContent=t('正在读取并校验 R 原始火山图…','Reading and verifying the original R volcano image…');
  ready=(async()=>{try{
   const value=await wait(w,Promise.resolve(w.options.readPNG({descriptor:w.descriptor,verifiedContext:w.context,signal:w.controller.signal}))),pin=w.descriptor.artifacts.find(p=>p.name==='volcano.png');
   need(exact(value,['body'])&&value.body instanceof Uint8Array&&value.body.byteLength===pin.bytes);const bytes=new Uint8Array(value.body);png(bytes);
   const digest=[...new Uint8Array(await wait(w,w.options.cryptoImpl.subtle.digest('SHA-256',bytes)))].map(n=>n.toString(16).padStart(2,'0')).join('');need(digest===pin.sha256);check(w);
   const blob=new w.options.BlobImpl([bytes],{type:'image/png'});check(w);w.url=w.options.urlImpl.createObjectURL(blob);need(typeof w.url==='string'&&w.url.startsWith('blob:'));check(w);
   const image=make('img');w.image=image;image.alt=t('R 生成的原始火山图','Original volcano plot generated by R');image.width=2400;image.height=1800;image.setAttribute('data-rvv-image','png');
   const decoded=new Promise(resolve=>{bind(w,image,'load',()=>{try{check(w);resolve(true);}catch{resolve(false);}});bind(w,image,'error',()=>{failed(w);resolve(false);});});
   phase='rendering';w.panel.append(image);image.src=w.url;const loaded=await wait(w,decoded);if(!loaded||!w.image||w.url===null)return false;check(w);phase='ready';note.textContent=t('显示 R 原始图像；浏览器未重绘、重分类或重新计算。','Original R image; the browser has not redrawn, reclassified or recomputed the data.');downloads(w);return true;
  }catch{if(active===w)failed(w);else revoke(w);return false;}})();return ready;
 }
 update(initial);return Object.freeze({update,dispose,get ready(){return ready;},view(){if(active&&!current(active))dispose();return freeze({phase,has_image:phase==='ready'&&!!active?.image});}});
}
