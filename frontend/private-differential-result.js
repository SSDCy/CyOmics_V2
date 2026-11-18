import {mountLimmaResultTable} from './limma-result-table.js';
export function mountPrivateDifferentialResult(root,{client=null,t=zh=>zh}={}){
 if(!client)return()=>{};const doc=root.ownerDocument,make=(tag,text='',cls='')=>{const n=doc.createElement(tag);n.textContent=text;n.className=cls;return n;};
 const region=make('section','','ppo-differential'),state=make('p','','ppo-state'),info=make('p','','ppo-summary'),tableRoot=make('div');state.setAttribute('role','status');state.setAttribute('aria-live','polite');state.setAttribute('data-ppo-diff-result-state','status');region.append(state,info,tableRoot);root.append(region);
 let disposed=false,mounted=null,epoch=null,rendered=false;
 function clear(){mounted?.dispose();mounted=null;epoch=null;rendered=false;tableRoot.textContent='';}
 function paint(){if(disposed)return;const value=client.view(),labels={loading:['正在核对已保存任务的执行与结果…','Checking execution and results for the saved task…'],not_started:['暂无可访问的执行记录，可能尚未开始。原排队记录保持不变。','No accessible execution record is available; execution may not have started. Admission history is unchanged.'],running:['执行记录为运行中。可稍后再次查看；不会自动刷新或重跑。','The execution record is running. View again later; no automatic refresh or rerun occurs.'],reconciliation:['执行状态仍待核对，尚无可展示的确认结果。','Execution requires reconciliation; no confirmed result is displayed.'],failed:['执行未完成，尚无可用结果。','Execution did not complete; no result is available.'],no_result:['执行已记录为完成，但暂无可访问结果；尚未展示结果表。','Execution is recorded as completed, but no accessible result is available; no table is displayed.'],unavailable:['本次读取未能确认，旧结果已清除。请稍后手动查看。','This read is unconfirmed; previous results were cleared. View again manually later.']};
  region.hidden=['idle','disposed'].includes(value.phase);if(value.phase!=='ready'){clear();state.textContent=labels[value.phase]?t(...labels[value.phase]):'';info.textContent='';return;}
  const m=value.meta;state.textContent=m.scientific_status==='NO_TESTABLE_ROWS'?t('执行结果已保存：没有可检验行，不构成差异统计结论。','Execution results were saved: no testable rows; no differential statistical conclusion.')
   :m.scientific_status==='REVIEW_REQUIRED_LIMMA_WARNING'?t('执行结果已保存：存在 limma 警告，需要审查。','Execution results were saved with limma warnings requiring review.')
   :t('已核对保存的计算结果；请结合实验设计与方法审计解释。','Saved computed results were checked; interpret with the experimental design and method audit.');
  info.textContent=t('结果：','Result: ')+m.result_id+' · '+t('执行尝试：','Attempt: ')+m.attempt_id+' · '+t('文件清单：','Manifest: ')+m.manifest_sha256;
  if(!rendered)state.textContent=t('正在核对结果表中的原始行关联…','Checking original row identity in the result table…');
  if(epoch===value.epoch)return;clear();epoch=value.epoch;const own=epoch,props=client.presentation();if(!props)return;mounted=mountLimmaResultTable(tableRoot,{...props,t});void mounted.ready.then(ok=>{if(disposed||epoch!==own)return;if(!ok)client.rejectPresentation();else{rendered=true;paint();}});
 }
 const stop=client.subscribe(paint);paint();return()=>{if(disposed)return;disposed=true;stop();clear();client.clear();region.textContent='';region.remove();};
}
