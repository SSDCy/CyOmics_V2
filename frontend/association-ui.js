import { parseProteinGroupsForAssociation, createFastaAssociationParser,
  associateProteinGroups, releaseAssociationInputs } from './protein-association.js';

const escape = value => String(value).replace(/[&<>"']/g,
  char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));

export function mountProteinAssociation(root, t = zh => zh) {
  root.innerHTML = `<section class="mini" aria-label="Local protein association"><h3>${t('表格与FASTA关联（本地只读）','Table and FASTA association (local read-only)')}</h3><p class="note">${t('未上传、未保存、未分析。依据 Protein IDs，按分号拆分并仅去首尾空白，区分大小写精确匹配；保留全部原始组与未匹配项。','Not uploaded, saved or analyzed. Exact case-sensitive Protein IDs matching; split semicolons and trim token ends only. All groups and unmatched IDs are retained.')}</p><div class="form-grid">${['table','fasta'].map(kind => `<label class="field">${kind==='table'?'proteinGroups TXT ≤ 10 MiB':'FASTA / FA ≤ 25 MiB'}<input type="file" data-assoc-file="${kind}" accept="${kind==='table'?'.txt':'.fasta,.fa'}"><span class="note" data-assoc-name="${kind}"></span><span class="note" role="status" data-assoc-state="${kind}" data-status="empty"></span></label>`).join('')}</div><div class="actions"><button class="primary" data-assoc-action="start" disabled>${t('开始关联','Start association')}</button><button data-assoc-action="clear">${t('清除关联输入与结果','Clear association inputs and results')}</button></div><p class="note" role="status" data-assoc-ready></p><div data-assoc-result aria-live="polite"></div><div data-assoc-detail></div></section>`;
  const q = selector => root.querySelector(selector);
  const slots = Object.fromEntries(['table','fasta'].map(kind => [kind,
    { version: 0, reader: null, parser: null, snapshot: null }]));
  const resultNode=q('[data-assoc-result]'), detailNode=q('[data-assoc-detail]');
  const startButton=q('[data-assoc-action="start"]');
  let disposed=false, joined=null, page=1;
  const state = (kind, status, text) => {
    const node=q(`[data-assoc-state="${kind}"]`);
    node.dataset.status=status; node.textContent=text;
  };
  function clearResult() {
    joined=null; page=1; resultNode.replaceChildren(); detailNode.replaceChildren();
  }
  function updateReady() {
    const ready=Boolean(slots.table.snapshot&&slots.fasta.snapshot);
    startButton.disabled=!ready;
    q('[data-assoc-ready]').textContent=ready
      ?t('两个文件已完整校验，请点击开始关联。','Both files validated; click Start association.')
      :t('请选择并完成校验两个文件。','Select and validate both files.');
  }
  function invalidate(kind) {
    const slot=slots[kind]; slot.version++;
    slot.reader?.cancel().catch(()=>{}); slot.reader=null;
    slot.parser?.abort(); slot.parser=null;
    if(slot.snapshot)releaseAssociationInputs(slot.snapshot);
    slot.snapshot=null;
  }
  function reset() {
    clearResult();
    for(const kind of ['table','fasta']) {
      invalidate(kind); q(`[data-assoc-file="${kind}"]`).value='';
      q(`[data-assoc-name="${kind}"]`).textContent=''; state(kind,'empty','');
    }
    updateReady();
  }
  async function load(kind, file) {
    invalidate(kind); clearResult(); updateReady();
    const slot=slots[kind], version=slot.version;
    const input=q(`[data-assoc-file="${kind}"]`);
    q(`[data-assoc-name="${kind}"]`).textContent=file?.name||'';
    const active=()=>!disposed&&root.isConnected&&slot.version===version;
    let reader, parser, published=false, text='';
    try {
      state(kind,'empty','');
      if(!file)return;
      const ext=file.name.split('.').pop().toLowerCase();
      const limit=(kind==='table'?10:25)*1024*1024;
      if(!(kind==='table'?ext==='txt':['fasta','fa'].includes(ext)))throw new Error('FILE_TYPE');
      if(file.size>limit)throw new Error('FILE_SIZE_LIMIT');
      state(kind,'loading',t('正在本地读取并校验…','Reading and validating locally…'));
      const decoder=new TextDecoder('utf-8',{fatal:true});
      if(kind==='fasta') { parser=createFastaAssociationParser(); slot.parser=parser; }
      reader=file.stream().getReader(); slot.reader=reader;
      let bytes=0;
      while(active()) {
        const next=await reader.read();
        if(!active())return;
        if(next.done)break;
        bytes+=next.value.byteLength;
        if(bytes>limit)throw new Error('FILE_SIZE_LIMIT');
        const chunk=decoder.decode(next.value,{stream:true});
        if(parser)parser.push(chunk); else text+=chunk;
      }
      if(!active())return;
      const tail=decoder.decode();
      if(parser)parser.push(tail); else text+=tail;
      const snapshot=parser?parser.finish():parseProteinGroupsForAssociation(text);
      text='';
      if(!active()) { releaseAssociationInputs(snapshot); return; }
      slot.snapshot=snapshot; slot.parser=null; published=true;
      state(kind,'ready',t('完整校验通过，仅保留ID元数据。','Fully validated; ID metadata only.'));
      updateReady();
    } catch(failure) {
      if(active()) {
        const code=failure.code||failure.message;
        const message=code==='FILE_SIZE_LIMIT'?t('文件超过此槽大小限制。','File exceeds this slot size limit.')
          :code==='FILE_TYPE'?t('文件扩展名不符合此槽要求。','File extension does not match this slot.')
          :t('校验失败，请检查文件结构、资源上限及UTF-8编码。','Validation failed; check structure, resource limits and UTF-8.');
        state(kind,'error',message+(/^[A-Z_]+$/.test(code)?` (${code})`:''));
        updateReady();
      }
    } finally {
      text='';
      if(!published)parser?.abort();
      if(slot.parser===parser)slot.parser=null;
      if(slot.reader===reader)slot.reader=null;
      if(reader) { try { await reader.cancel(); } catch {} reader.releaseLock(); }
      if(active())input.value='';
    }
  }
  const statusText = row => row.emptyIds?t('空ID（未匹配）','Empty ID (unmatched)')
    :row.status==='all'?t('全部匹配','All matched'):row.status==='partial'?t('部分匹配','Partially matched'):t('未匹配','Unmatched');
  function renderPage() {
    if(!joined)return;
    const s=joined.summary, pages=Math.ceil(s.rowCount/50);
    const labels={rowCount:t('总组数','Groups'),allMatchRows:t('全部匹配','All matched'),
      partialMatchRows:t('部分匹配','Partial'),zeroMatchRows:t('未匹配','Unmatched'),
      distinctTokenCount:t('不同ID','Distinct IDs'),matchedDistinctTokenCount:t('命中ID','Matched IDs'),
      unmatchedDistinctTokenCount:t('未命中ID','Unmatched IDs')};
    resultNode.innerHTML=`<dl class="facts">${Object.entries(labels).map(([key,label])=>`<dt>${escape(label)}</dt><dd data-assoc-count="${key}">${s[key]}</dd>`).join('')}</dl><p class="note">${t('依据 Protein IDs；每页50组，所有原始组均可访问。','Based on Protein IDs; 50 groups per page, all original groups accessible.')}</p><div style="overflow:auto"><table aria-label="Association groups"><thead><tr><th>#</th><th>Protein IDs</th><th>${t('状态','Status')}</th><th>${t('命中/组内ID','Matched/group IDs')}</th><th>${t('详情','Details')}</th></tr></thead><tbody>${joined.rows.slice((page-1)*50,page*50).map(row=>`<tr data-assoc-row="${row.recordNumber}"><td>${row.recordNumber}</td><td style="white-space:pre-wrap;overflow-wrap:anywhere">${escape(row.proteinIds)}</td><td data-assoc-status="${row.status}">${escape(statusText(row))}</td><td>${row.matches.filter(item=>item.length!==null).length}/${row.matches.length}</td><td><button data-assoc-group="${row.recordNumber}">${t('查看全组','View full group')}</button></td></tr>`).join('')}</tbody></table></div><div class="actions"><button data-assoc-action="prev" ${page===1?'disabled':''}>${t('上一页','Previous')}</button><span><span data-assoc-current-page>${page}</span> / <span data-assoc-pages>${pages}</span></span><button data-assoc-action="next" ${page===pages?'disabled':''}>${t('下一页','Next')}</button><input type="number" data-assoc-page min="1" max="${pages}" value="${page}" aria-label="Association page"><button data-assoc-action="page">${t('跳转','Go')}</button></div>`;
  }
  function showGroup(number) {
    const row=joined?.rows[number-1]; if(!row)return;
    detailNode.innerHTML=`<section class="mini" aria-label="Association group details"><h4>${t('原始记录','Original record')} ${row.recordNumber}</h4><p>${escape(statusText(row))} · <span data-assoc-detail-count>${row.matches.length}</span> ID</p><p class="note">${t('长度为结构字符计数；未匹配项不删除，不显示原始序列。','Lengths are structural character counts; unmatched IDs are retained, sequences are not displayed.')}</p>${row.emptyIds?`<p>${t('原始组没有非空ID，归入未匹配。','No nonempty ID in this group; counted as unmatched.')}</p>`:''}<div style="overflow:auto"><table aria-label="All group IDs and lengths"><thead><tr><th>ID</th><th>${t('长度/未匹配','Length/unmatched')}</th></tr></thead><tbody>${row.matches.map(item=>`<tr><td>${escape(item.id)}</td><td>${item.length===null?t('未匹配','Unmatched'):item.length}</td></tr>`).join('')}</tbody></table></div><button data-assoc-action="close">${t('关闭详情','Close details')}</button></section>`;
  }
  root.onchange=event=>{
    const kind=event.target.dataset.assocFile;
    if(kind==='table'||kind==='fasta')void load(kind,event.target.files[0]);
  };
  root.onclick=event=>{
    const button=event.target.closest?.('[data-assoc-action],[data-assoc-group]');
    if(!button||!root.contains(button))return;
    if(button.dataset.assocGroup) { if(joined)showGroup(Number(button.dataset.assocGroup)); return; }
    const action=button.dataset.assocAction;
    if(action==='clear') { reset(); return; }
    if(action==='close') { detailNode.replaceChildren(); return; }
    if(action==='start') {
      clearResult();
      if(!slots.table.snapshot||!slots.fasta.snapshot)return;
      try { joined=associateProteinGroups(slots.table.snapshot,slots.fasta.snapshot); renderPage(); }
      catch { resultNode.textContent=t('关联失败，请重新选择文件。','Association failed; reselect files.'); }
      return;
    }
    if(!joined)return;
    const pages=Math.ceil(joined.rows.length/50);
    const next=action==='next'?page+1:action==='prev'?page-1:action==='page'?Number(q('[data-assoc-page]').value):page;
    if(Number.isInteger(next)&&next>=1&&next<=pages) { page=next; detailNode.replaceChildren(); renderPage(); }
  };
  updateReady();
  return ()=>{
    disposed=true; reset(); root.onchange=null; root.onclick=null; root.replaceChildren();
  };
}
