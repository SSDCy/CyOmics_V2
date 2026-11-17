import {createRawQCClient} from './raw-qc-client.js';
import {FAMILIES,canonicalJSON,freeze} from './raw-qc-contract.mjs';
export function createPrivateRawQCHost({reportModel,projectId,fileId,transportFactory=()=>null,isCurrent=()=>true,cryptoImpl=globalThis.crypto,onDenied=()=>{},clientOptions={}}={}){
  let disposed=false,fingerprint=null,selected='',expanded=false,mounts=0,active=null,stopActive=()=>{};
  const clients=new Map(),listeners=new Set();let stops=[];
  const emit=()=>{if(!disposed)for(const fn of listeners)fn();};
  const activate=()=>{const next=!disposed&&expanded&&mounts>0?clients.get(selected)||null:null;if(active===next)return;stopActive();active=next;stopActive=next?.attach()||(()=>{});};
  const clear=()=>{stopActive();stopActive=()=>{};active=null;stops.forEach(fn=>fn());stops=[];clients.forEach(c=>c.dispose());clients.clear();selected='';};
  function sync(){
    if(disposed)return;let full=null,next=null;
    try{if(isCurrent())full=reportModel?.validatedSource?.()||null;if(full?.file?.project_id!==projectId||full?.file?.file_id!==fileId)full=null;if(full)next=canonicalJSON(full);}catch{full=null;}
    if(next===fingerprint)return;fingerprint=next;clear();
    if(full){for(const [family,label]of Object.entries(FAMILIES))if(full.importDTO?.importReport?.sample_columns?.some(c=>c.quant_family===label)){
      let transport=null;try{transport=transportFactory({projectId,fileId,datasetVersionId:full.importDTO.datasetVersion.dataset_version_id});}catch{}
      const c=createRawQCClient({...clientOptions,fullSource:full,family,transport,cryptoImpl,isCurrent,onDenied});clients.set(family,c);
      stops.push(c.subscribe(()=>{if(!selected&&c.reference())selected=family;activate();emit();}));
    }}activate();emit();
  }
  const stopSource=reportModel?.subscribe?.(sync)||(()=>{});sync();
  return{
    view(){sync();const c=clients.get(selected),off=clients.size>0&&[...clients.values()].every(v=>v.view().phase==='not_connected');return freeze({disposed,expanded,family:selected,families:[...clients.keys()],phase:!fingerprint?'waiting':!selected?(off?'not_connected':'choose'):c?.view().phase||'waiting',task:c?.view()||null,analysis_ready:false});},
    selectFamily(value){sync();if(disposed||!clients.has(value)||selected===value)return false;selected=value;activate();emit();return true;},
    setExpanded(value){if(disposed||typeof value!=='boolean')return;expanded=value;activate();emit();},
    start(){sync();return clients.get(selected)?.start()||Promise.resolve(false);},check(){sync();return clients.get(selected)?.check()||Promise.resolve(false);},
    attach(){if(disposed)return()=>{};mounts++;activate();let stopped=false;return()=>{if(stopped)return;stopped=true;mounts=Math.max(0,mounts-1);activate();};},
    subscribe(fn){if(disposed)return()=>{};listeners.add(fn);return()=>listeners.delete(fn);},
    dispose(){if(disposed)return;disposed=true;stopSource();clear();fingerprint=null;expanded=false;for(const fn of listeners)fn();listeners.clear();}
  };
}
const labels={waiting:['请先完成并保存导入报告。','Complete and save the import report first.'],choose:['请选择质控数据类型。','Choose the quantification type for QC.'],not_connected:['原始质控服务待接入。','Raw QC service is not connected.'],preparing:['正在核对已保存的来源…','Checking the saved source…'],idle:['尚未运行。','Not run.'],submitting:['正在提交任务…','Submitting the task…'],checking:['正在核对任务状态…','Checking task status…'],queued:['排队中。','Queued.'],running:['运行中。','Running.'],succeeded:['基础质控已完成。','Basic QC completed.'],failed:['基础质控未完成。','Basic QC did not complete.'],unknown:['提交结果暂未确认，请检查状态，勿重复提交。','Submission is unconfirmed. Check status; do not submit again.'],unavailable:['暂时无法确认任务信息，请检查状态或重新确认身份。','Task information cannot be confirmed. Check status or confirm your session.'],conflict:['已有请求信息冲突，暂不可继续提交。','The existing request conflicts; submission is blocked.'],rejected:['本次任务未能提交，请检查状态。','The task was not accepted. Check status.']};
const errors={raw_qc_recovery_unavailable:['任务恢复信息无法保存，请检查状态，勿重复提交。','Recovery information could not be saved. Check status; do not submit again.'],raw_qc_capacity:['当前任务额度已满。','The current task capacity is full.'],raw_qc_family_unavailable:['该数据类型没有可用样本列。','No sample columns are available for this type.'],raw_qc_unsupported_source:['当前来源暂不支持基础质控。','This source is not supported for basic QC.'],resource_limit:['任务达到资源限制。','The task reached a resource limit.'],input_integrity_failed:['输入校验未通过。','Input validation failed.'],execution_unavailable:['计算服务暂不可用。','The compute service is unavailable.'],runner_failed:['计算未能完成。','The computation did not complete.'],output_invalid:['结果校验未通过。','Result validation failed.'],worker_interrupted:['任务执行已中断。','Task execution was interrupted.'],publish_integrity_failed:['结果发布校验未通过。','Result publication validation failed.']};
export function mountPrivateRawQC(root,host,{t=zh=>zh}={}){
  const doc=root.ownerDocument,make=(tag,text='',cls='')=>{const n=doc.createElement(tag);n.textContent=text;n.className=cls;return n;};
  const box=make('details','','prqc'),summary=make('summary',t('原始矩阵与基础质控','Raw matrix & basic QC')),body=make('div'),label=make('label',t('质控数据类型','Quantification type'),'prqc-field'),select=make('select');select.setAttribute('data-prqc-field','family');select.setAttribute('aria-label',t('质控数据类型','Quantification type'));label.append(select);
  const actions=make('div','','prqc-actions'),start=make('button',t('启动基础质控','Start basic QC')),check=make('button',t('检查状态','Check status'));start.type=check.type='button';start.setAttribute('data-prqc-action','start');check.setAttribute('data-prqc-action','check');actions.append(start,check);
  const state=make('p','','prqc-state'),error=make('p','','prqc-error'),task=make('p','','prqc-note');state.setAttribute('role','status');state.setAttribute('aria-live','polite');
  const note=make('p',t('仅提取原始矩阵并分别统计正值、零值与空值；不做标准化、PCA 或差异分析。','Extracts the raw matrix and counts positive, zero and blank values separately. No normalization, PCA or differential analysis.'),'prqc-note');
  body.append(label,actions,state,error,task,note);box.append(summary,body);root.replaceChildren(box);let disposed=false,stopHost=()=>{},stopView=()=>{},choices='';
  const change=()=>{if(!disposed)host.selectFamily(select.value);},toggle=()=>{if(!disposed&&box.open!==host.view().expanded)host.setExpanded(box.open);},run=()=>{if(!disposed)void host.start();},read=()=>{if(!disposed)void host.check();};
  function stop(){if(disposed)return;disposed=true;stopHost();stopView();select.removeEventListener('change',change);box.removeEventListener('toggle',toggle);start.removeEventListener('click',run);check.removeEventListener('click',read);select.value='';select.textContent='';state.textContent=error.textContent=task.textContent='';box.textContent='';root.replaceChildren();}
  function paint(){if(disposed)return;const v=host.view();if(v.disposed){stop();return;}box.open=v.expanded;const key=v.families.join(':');if(key!==choices||!select.children.length){choices=key;select.replaceChildren();const blank=make('option',t('请选择','Choose'));blank.value='';select.append(blank);for(const family of v.families){const option=make('option',FAMILIES[family]);option.value=family;select.append(option);}}
    select.value=v.family;select.disabled=!v.families.length;start.hidden=!v.task?.can_start;start.disabled=!v.task?.can_start;check.hidden=!v.task?.can_check;check.disabled=!v.task?.can_check;
    state.textContent=v.task?.observation==='reconciliation_pending'?t('任务状态核对中，请勿重复提交。','Task state is being reconciled. Do not submit again.'):t(...(labels[v.phase]||labels.unavailable));
    error.textContent=errors[v.task?.error||v.task?.code]?t(...errors[v.task.error||v.task.code]):'';
    task.textContent=v.task?.task?t('任务','Task')+' '+v.task.task+(v.task.finished_at?' · '+v.task.finished_at:''):'';
  }
  select.addEventListener('change',change);box.addEventListener('toggle',toggle);start.addEventListener('click',run);check.addEventListener('click',read);stopHost=host.subscribe(paint);stopView=host.attach();paint();return stop;
}
