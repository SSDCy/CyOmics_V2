import {mountPreprocessParameters} from './preprocess-parameters.js';
import {createPreprocessOwnerClient} from './preprocess-owner-client.js';
import {canonicalJSON} from './raw-qc-contract.mjs';

// One saved-report view owns this host. The parent must dispose/invalidate on
// authority changes; an injected contextSignal provides immediate cancellation.
export function mountPrivatePreprocessOwner(root,{featureEnabled=false,reportModel=null,binding=null,transportFactory=null,isCurrent=()=>true,onDenied=()=>{},contextSignal=null,clientOptions={},t=zh=>zh}={}){
  if(featureEnabled!==true)return()=>{};
  const doc=root.ownerDocument,make=(tag,text='',cls='')=>{const node=doc.createElement(tag);node.textContent=text;node.className=cls;return node;};
  const panel=make('section','','ppo'),heading=make('h3',t('数据预处理','Data preprocessing')),state=make('p','','ppo-state'),summary=make('p','','ppo-summary'),cardRoot=make('div'),actions=make('div','','ppo-actions'),check=make('button',t('按原请求核对（只读）','Check original request (read only)')),note=make('p',t('当前只支持提交排队及恢复核对；排队不表示已经开始计算，也不会产生已处理版本。','This interface supports queue admission and recovery only. Queuing does not mean computation has started and does not produce a processed version.'),'ppo-note');
  panel.setAttribute('role','region');panel.setAttribute('aria-label',t('已保存数据的预处理','Preprocessing for saved data'));state.setAttribute('role','status');state.setAttribute('aria-live','polite');check.type='button';check.setAttribute('data-ppo-action','check');actions.append(check);panel.append(heading,state,summary,cardRoot,actions,note);root.append(panel);
  let disposed=false,denied=false,fingerprint=null,client=null,stopClient=()=>{},stopModel=()=>{},card=null,confirmHandler=null,syncing=false;
  const wipe=node=>{for(const child of [...node.children])wipe(child);node.textContent='';};
  function clearCard(){if(card){card.dispose();card=null;}wipe(cardRoot);}
  function clearClient(){stopClient();stopClient=()=>{};const old=client;client=null;confirmHandler=null;clearCard();old?.dispose();summary.textContent='';}
  function source(){
    try{if(!isCurrent()||contextSignal?.aborted||!reportModel?.validatedSource||!reportModel?.status)return null;const status=reportModel.status();if(status.phase!=='ready'||status.report_saved!==true||status.report_verified!==true||status.source_verified!==true)return null;const full=reportModel.validatedSource();if(!binding||full?.file?.project_id!==binding.project_id||full.file.file_id!==binding.raw_file_id||full.file.sha256!==binding.expected_raw_sha256)return null;return full;}catch{return null;}
  }
  function sync(){
    if(disposed||syncing)return;syncing=true;
    try{const full=source(),next=full?canonicalJSON(full):null;if(next===fingerprint)return;fingerprint=next;denied=false;clearClient();
      if(full){let transport=null;try{transport=typeof transportFactory==='function'?transportFactory({projectId:full.file.project_id,fileId:full.file.file_id,datasetVersionId:full.importDTO.datasetVersion.dataset_version_id}):null;}catch{}
        const captured=next;
        const created=createPreprocessOwnerClient({...clientOptions,featureEnabled:true,fullSource:full,transport,contextSignal,isCurrent:()=>{const current=source();return !disposed&&!denied&&fingerprint===captured&&isCurrent()&&current!==null&&canonicalJSON(current)===captured;},onDenied(response){denied=true;clearCard();summary.textContent='';try{onDenied(response);}finally{paint();}}});
        client=created;confirmHandler=request=>created.submit(request).then(accepted=>{if(!accepted)throw Error('preprocessing_not_confirmed');});stopClient=created.subscribe(()=>{sync();paint();});
      }
    }finally{syncing=false;}
  }
  function paint(){
    if(disposed)return;
    const v=client?.view(),phase=denied?'unauthenticated':!fingerprint?'waiting':v?.phase||'unavailable';
    const labels={waiting:['请先保存并确认导入报告。','Save and verify the import report first.'],preparing:['正在核对已保存的数据来源…','Verifying the saved source…'],not_connected:['预处理排队接口尚未接入。','The preprocessing queue interface is not connected.'],idle:['请明确选择定量类型和过滤标记，确认后提交排队。','Choose the quantification type and filter flags explicitly. Confirmation submits the request to the queue.'],submitting:['正在提交排队请求，结果尚未确认。','Submitting a queue request; its outcome is unconfirmed.'],checking:['正在按原请求编号只读核对…','Checking the original request ID with a read-only query…'],queued:['已排队；尚未开始计算。','Queued; computation has not started.'],unknown:['提交结果未知，请按原请求只读核对，勿重复提交。','Submission outcome is unknown. Check the original request; do not submit again.'],unavailable:['暂时无法确认预处理请求，请保留原请求并只读核对。','The preprocessing request cannot be confirmed. Retain the original request and check it read-only.'],not_found:['尚未查到该请求；不会自动重新提交。','The request was not found; it will not be submitted again automatically.'],conflict:['请求绑定冲突，不能确认任务；请核对来源。','Request binding conflict; the task cannot be confirmed. Check the source.'],rejected:['请求被拒绝；未确认排队，不会自动重发。','Request rejected; queuing is not confirmed and no automatic retry occurs.'],unauthenticated:['身份或授权已失效，已清除可用任务状态，请重新登录。','Identity or authorization is no longer valid. Usable task state was cleared; sign in again.'],disposed:['当前来源或账户已变化，旧请求不再展示。','The source or account changed; the old request is no longer displayed.']};
    state.textContent=phase==='unavailable'&&!v?.request_id?t('无法核对当前来源或恢复引用；未确认排队。','The current source or recovery reference cannot be verified; queuing is not confirmed.'):t(...(labels[phase]||labels.unavailable));panel.setAttribute('aria-busy',String(['preparing','submitting','checking'].includes(phase)));
    check.hidden=!v?.can_check||denied;check.disabled=check.hidden;
    if(!denied&&v?.request_id){const names={contaminant:'Potential contaminant',reverse:'Reverse',site_only:'Only identified by site'};const flags=Object.entries(v.filter_flags||{}).map(([key,value])=>names[key]+': '+(value?t('剔除','remove'):t('保留','retain'))).join(' · ');summary.textContent=v.quant_family+' · '+flags+' · '+t('请求','Request')+' '+v.request_id+(v.job_id?' · '+t('排队任务','Queued job')+' '+v.job_id:'');}else summary.textContent='';
    if(phase==='idle'){
      const parameterSource=client?.parameterSource();
      if(parameterSource&&!card)card=mountPreprocessParameters(cardRoot,{featureEnabled:true,source:parameterSource,onConfirm:confirmHandler,lang:t('zh','en')});
    }else clearCard();
  }
  const read=()=>{if(!disposed&&!denied)void client?.check();};check.addEventListener('click',read);
  const invalidate=()=>{if(!disposed){sync();paint();}};
  try{stopModel=reportModel?.subscribe?.(invalidate)||(()=>{});}catch{}
  contextSignal?.addEventListener('abort',invalidate,{once:true});sync();paint();
  return()=>{if(disposed)return;disposed=true;stopModel();contextSignal?.removeEventListener('abort',invalidate);check.removeEventListener('click',read);clearClient();fingerprint=null;reportModel=null;binding=null;wipe(panel);panel.remove();};
}
