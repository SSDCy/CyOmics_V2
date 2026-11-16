import assert from 'node:assert/strict';
import { readFile, stat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { test } from 'node:test';

const { TXT_PREVIEW_INPUT: txtPath, FASTA_PREVIEW_INPUT: fastaPath,
  PLAYWRIGHT_MODULE: playwrightModule, CHROMIUM_EXECUTABLE: executablePath,
  PREVIEW_URL: baseURL } = process.env;
if (![txtPath,fastaPath,playwrightModule,executablePath,baseURL].every(Boolean)) throw Error('Explicit real inputs/browser/preview required');
if(baseURL!=='http://127.0.0.1:4180')throw Error('Only authorized loopback 4180 allowed');
const {chromium}=createRequire(import.meta.url)(playwrightModule);
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const same=(a,b,message)=>assert.ok(JSON.stringify(a)===JSON.stringify(b),message);
const inputs=[
  [txtPath,6186652,'301bc95ff440a4ea70912fb5ede26aa54cb15f2cb06a9efeed6e55fd749f95fb'],
  [fastaPath,17195782,'8cb4a7994a80b6fecef0310e288c4763261026989b73a4e467d3310aad5daba5'],
];
test('B2 actual browser dual-file association and existing real previews',async t=>{
  const before=await Promise.all(inputs.map(async([path,size,digest])=>{
    const metadata=await stat(path,{bigint:true}),bytes=await readFile(path);
    assert.equal(bytes.length,size);assert.equal(sha(bytes),digest);
    console.log(`INPUT ${path}; size=${size}; sha256=${digest}`);return {metadata,bytes};
  }));
  const txt=before[0].bytes,fasta=before[1].bytes;
  // Independent reference extraction; production parser/association code is not imported.
  const lines=txt.toString('utf8').replace(/^\uFEFF/,'').split(/\r\n|\n|\r/);
  if(lines.at(-1)==='')lines.pop();
  const headers=lines.shift().split('\t'),raw=lines.map(line=>line.split('\t'));
  assert.equal(raw.length,2252);assert.ok(raw.every(row=>row.length===182));
  const idIndex=headers.indexOf('Protein IDs'),referenceIndex=new Map();
  const fastaText=fasta.toString('utf8').replace(/^\uFEFF/,'');
  for(const record of fastaText.split(/^>/m).slice(1)){
    const newline=record.search(/[\r\n]/),id=record.slice(0,newline).trim().split(/\s+/)[0];
    referenceIndex.set(id,record.slice(newline).replace(/\s/g,'').length);
  }
  assert.equal(referenceIndex.size,38387);
  const reference=raw.map((row,i)=>{
    const matches=row[idIndex].split(';').map(id=>id.trim()).filter(Boolean).map(id=>({id,length:referenceIndex.get(id)??null}));
    const hits=matches.filter(item=>item.length!==null).length;
    return {number:i+1,ids:row[idIndex],matches,hits,status:matches.length&&hits===matches.length?'all':hits?'partial':'none'};
  });
  assert.equal(reference[1204].matches.length,23);
  let browser;
  try{
    browser=await chromium.launch({headless:true,executablePath});
    const context=await browser.newContext(),page=await context.newPage(),errors=[],requests=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>requests.push({url:request.url(),method:request.method()}));
    await page.route('**/*',route=>route.request().url().startsWith(baseURL+'/')?route.continue():route.fulfill({status:200,contentType:'text/css',body:''}));
    await page.addInitScript(()=>{
      window.__audit={streams:{table:0,fasta:0},cancels:0,releases:0,text:0,writes:[]};
      const originalStream=File.prototype.stream,originalText=File.prototype.text,setItem=Storage.prototype.setItem;
      Storage.prototype.setItem=function(key,value){window.__audit.writes.push(key);return setItem.call(this,key,value)};
      File.prototype.text=function(){window.__audit.text++;return originalText.call(this)};
      File.prototype.stream=function(){
        const kind=this.name.toLowerCase().endsWith('.txt')?'table':'fasta';
        window.__audit.streams[kind]++;
        const reader=originalStream.call(this).getReader();
        const mode=window.__hold?.kind===kind?window.__hold.mode:null;
        if(mode)window.__hold=null;
        let first=true;
        return {getReader:()=>({
          async read(){
            const next=await reader.read();
            if(first&&mode){first=false;await new Promise(resolve=>{window.__release=resolve;window.__held=true});window.__held=false;if(mode==='error')throw Error('delayed test error')}
            return next;
          },
          cancel(){window.__audit.cancels++;return reader.cancel()},
          releaseLock(){window.__audit.releases++;reader.releaseLock()},
        })};
      };
    });
    await page.goto(baseURL+'/#/demo/tool/import');
    const root=page.locator('#protein-association'),slot=kind=>root.locator(`[data-assoc-file="${kind}"]`);
    const state=kind=>root.locator(`[data-assoc-state="${kind}"]`),start=root.locator('[data-assoc-action="start"]');
    const result=root.locator('[data-assoc-result]'),detail=root.locator('[data-assoc-detail]');
    const waitState=async(kind,status)=>{
      await page.waitForFunction(({kind,status})=>document.querySelector(`[data-assoc-state="${kind}"]`)?.dataset.status===status,{kind,status});
      await page.waitForFunction(kind=>document.querySelector(`[data-assoc-file="${kind}"]`)?.value==='',kind);
    };
    const selectReal=async kind=>{await slot(kind).setInputFiles(kind==='table'?txtPath:fastaPath);await waitState(kind,'ready')};
    const derived=async(kind,buffer,name=kind==='table'?'derived.txt':'derived.fasta',status='error')=>{
      await slot(kind).setInputFiles({name,mimeType:'text/plain',buffer});await waitState(kind,status);
    };
    const noResult=async()=>{assert.equal(await result.locator('table').count(),0);assert.equal(await detail.locator('table').count(),0)};
    const empty=async()=>{
      await noResult();assert.equal(await start.isDisabled(),true);
      for(const kind of ['table','fasta']){assert.equal(await state(kind).getAttribute('data-status'),'empty');assert.equal(await root.locator(`[data-assoc-name="${kind}"]`).textContent(),'');assert.equal(await slot(kind).inputValue(),'')}
    };
    const clear=async()=>{await root.locator('[data-assoc-action="clear"]').click();await empty()};
    const checkSummary=async()=>{
      for(const[key,value]of Object.entries({rowCount:2252,allMatchRows:2223,partialMatchRows:0,zeroMatchRows:29,distinctTokenCount:4060,matchedDistinctTokenCount:3994,unmatchedDistinctTokenCount:66}))
        assert.equal(await root.locator(`[data-assoc-count="${key}"]`).textContent(),String(value));
    };
    const join=async()=>{assert.equal(await start.isDisabled(),false);await start.click();await checkSummary()};
    const prepare=async(order=['table','fasta'])=>{await clear();for(const kind of order)await selectReal(kind);await join()};
    const jump=async number=>{await root.locator('[data-assoc-page]').fill(String(number));await root.locator('[data-assoc-action="page"]').click();assert.equal(await root.locator('[data-assoc-current-page]').textContent(),String(number))};
    const checkDetail=async number=>{
      await jump(Math.floor((number-1)/50)+1);await root.locator(`[data-assoc-group="${number}"]`).click();
      const actual=await detail.locator('tbody tr').evaluateAll(rows=>rows.map(row=>[...row.querySelectorAll('td')].map(td=>td.textContent)));
      same(actual,reference[number-1].matches.map(item=>[item.id,item.length===null?'未匹配':String(item.length)]),'Complete group IDs/lengths differ from independent FASTA reference');
      assert.equal(await detail.locator('[data-assoc-detail-count]').textContent(),String(reference[number-1].matches.length));
      assert.equal(await detail.locator('a').count(),0);
    };
    const hold=async(kind,mode)=>{
      await page.evaluate(({kind,mode})=>{window.__held=false;window.__hold={kind,mode}},{kind,mode});
      await slot(kind).setInputFiles(kind==='table'?txtPath:fastaPath);await page.waitForFunction(()=>window.__held);
    };
    const release=async()=>{
      await page.evaluate(()=>window.__release());
      await page.waitForFunction(()=>window.__audit.releases===window.__audit.streams.table+window.__audit.streams.fasta);
    };

    await t.test('both validated inputs and explicit start are required; successful reader cleanup preserves snapshots',async()=>{
      await empty();await selectReal('table');assert.equal(await start.isDisabled(),true);
      await selectReal('fasta');await noResult();await join();
      const audit=await page.evaluate(()=>window.__audit);
      same(audit.streams,{table:1,fasta:1},'Unexpected association reads');assert.equal(audit.releases,2);assert.equal(audit.text,0);assert.equal(audit.writes.length,0);
      console.log('UI JOIN total=2252; all=2223; partial=0; unmatched=29; distinct=4060; matchedIDs=3994; unmatchedIDs=66; explicit start and successful snapshot lifetime verified');
    });
    await t.test('all 46 pages cover every original group; full details include group 1205 with 23 IDs',async()=>{
      await checkDetail(5);assert.ok(reference[4].matches.length>1);
      await checkDetail(1205);assert.equal(await detail.locator('tbody tr').count(),23);
      await jump(1);
      const seen=[];let unmatched=0;
      for(let number=1;number<=46;number++){
        assert.equal(await root.locator('[data-assoc-current-page]').textContent(),String(number));
        assert.equal(await root.locator('[data-assoc-pages]').textContent(),'46');
        const actual=await root.locator('[aria-label="Association groups"] tbody tr').evaluateAll(rows=>rows.map(row=>{
          const cells=row.querySelectorAll('td');return {number:Number(row.dataset.assocRow),ids:cells[1].textContent,status:cells[2].dataset.assocStatus,hits:cells[3].textContent};
        }));
        const expected=reference.slice((number-1)*50,number*50).map(row=>({number:row.number,ids:row.ids,status:row.status,hits:`${row.hits}/${row.matches.length}`}));
        same(actual,expected,'Pagination omitted/reordered/changed a group or its full ID string');
        assert.equal(actual.length,number===46?2:50);seen.push(...actual.map(row=>row.number));unmatched+=actual.filter(row=>row.status==='none').length;
        if(number<46)await root.locator('[data-assoc-action="next"]').click();
      }
      assert.equal(seen.length,2252);assert.equal(new Set(seen).size,2252);assert.equal(seen.at(-1),2252);assert.equal(unmatched,29);
      const missing=reference.find(row=>row.status==='none');await checkDetail(missing.number);
      assert.ok((await detail.textContent()).includes('未匹配'));
      console.log('UI PAGES=46; covered=2252; lastPage=2; lastRow=2252; unmatchedRetained=29; group5/allIDs and group1205/23IDs lengths exact');
    });
    await t.test('reverse selection order works; failed replacements clear join and preserve only the other valid slot',async()=>{
      await prepare(['fasta','table']);
      const beforeReads=await page.evaluate(()=>window.__audit.streams.fasta);
      const badTxt=Buffer.from(txt);Buffer.from('Missing IDs').copy(badTxt,0);
      await derived('table',badTxt);await noResult();assert.equal(await start.isDisabled(),true);assert.equal(await state('fasta').getAttribute('data-status'),'ready');
      await selectReal('table');await join();assert.equal(await page.evaluate(()=>window.__audit.streams.fasta),beforeReads);
      const firstEnd=fasta.indexOf('\n>');const duplicated=Buffer.concat([fasta,Buffer.from('\n'),fasta.subarray(0,firstEnd+1)]);
      await derived('fasta',duplicated);await noResult();assert.equal(await state('table').getAttribute('data-status'),'ready');assert.equal(await start.isDisabled(),true);
      const invalidUtf8=Buffer.from(fasta.subarray(0,firstEnd));invalidUtf8[1]=255;
      await derived('fasta',invalidUtf8);await noResult();await selectReal('fasta');await join();
      console.log('UI ORDER table-first/fasta-first passed; bad TXT/duplicate FASTA/UTF8 replacements remove old join; unaffected slot retained');
    });
    await t.test('actual byte gates accept exact 10/25 MiB derivatives and reject +1 before stream creation',async()=>{
      const paddedFasta=Buffer.alloc(25*1024*1024,10);fasta.copy(paddedFasta);
      await derived('fasta',paddedFasta,'boundary.fasta','ready');await join();
      const end=txt.at(-2)===13?2:1;
      const paddedTxt=Buffer.concat([txt.subarray(0,txt.length-end),Buffer.alloc(10*1024*1024-txt.length,32),txt.subarray(txt.length-end)]);
      await derived('table',paddedTxt,'boundary.txt','ready');await join();
      for(const[kind,buffer]of [['table',paddedTxt],['fasta',paddedFasta]]){
        const reads=await page.evaluate(kind=>window.__audit.streams[kind],kind);
        await derived(kind,Buffer.concat([buffer,Buffer.from('\n')]));await noResult();
        assert.equal(await page.evaluate(kind=>window.__audit.streams[kind],kind),reads);assert.equal(await start.isDisabled(),true);
      }
      console.log('UI LIMITS exact TXT=10485760 and FASTA=26214400 accepted; +1 rejected before any stream');
    });
    await t.test('replacement and explicit clear cancel readers; stale result/error cannot overwrite current slots or join',async()=>{
      await prepare();
      const bad=Buffer.from(txt);Buffer.from('Missing IDs').copy(bad,0);
      await hold('table','result');await noResult();assert.equal(await start.isDisabled(),true);
      const cancels=await page.evaluate(()=>window.__audit.cancels);
      await derived('table',bad);assert.ok(await page.evaluate(()=>window.__audit.cancels)>cancels);await release();
      assert.equal(await state('table').getAttribute('data-status'),'error');await noResult();
      await selectReal('table');await join();
      await hold('fasta','error');await derived('fasta',fasta,'replacement.fasta','ready');await join();await release();await checkSummary();
      assert.equal(await state('fasta').getAttribute('data-status'),'ready');
      await hold('table','result');await clear();await release();await empty();
      console.log('UI RACES old success/error suppressed; changed-slot metadata invalidated; clear cancels and drops both inputs/results/details/names');
    });
    await t.test('SPA navigation, redraw, pagehide/back and refresh cancel and clear metadata',async()=>{
      await selectReal('fasta');await hold('table','result');
      const cancels=await page.evaluate(()=>window.__audit.cancels);
      await page.evaluate(()=>{location.hash='/demo/hub'});await page.waitForFunction(()=>!document.querySelector('#protein-association'));
      assert.ok(await page.evaluate(()=>window.__audit.cancels)>cancels);await release();
      await page.evaluate(()=>{location.hash='/demo/tool/import'});await root.waitFor();await empty();
      await hold('fasta','error');await page.locator('[data-action="lang"]').click();await release();
      await page.locator('[data-action="lang"]').click();await empty();
      await prepare();await checkDetail(5);
      await page.evaluate(()=>window.dispatchEvent(new Event('pagehide')));assert.equal(await root.locator('input').count(),0);assert.equal(await root.textContent(),'');
      await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})));await empty();
      await prepare();await page.goto('about:blank');await page.goBack();await root.waitFor();await empty();
      await prepare();assert.ok((await page.evaluate(()=>window.__audit.writes)).every(key=>key==='pp-lang'));
      await page.reload();await root.waitFor();await empty();
      console.log('UI LIFECYCLE in-flight navigation/redraw canceled; pagehide/back/refresh clear both slots, names, details and results');
    });
    await t.test('existing TXT quantification and single FASTA previews stay independent',async()=>{
      const single=page.locator('#file-input');await single.setInputFiles(txtPath);
      await page.waitForFunction(()=>document.querySelector('[data-quant-select]'));
      for(const[value,kind]of [['intensity','Intensity'],['lfq','LFQ intensity']]){
        await page.locator('[data-quant-select]').selectOption(value);
        const cols=headers.flatMap((name,i)=>name.startsWith(kind+' ')?[i]:[]);
        assert.equal(cols.length,21);
        const actual=await page.locator('[aria-label="Selected quantification preview"] tbody tr').evaluateAll(rows=>rows.map(row=>[...row.querySelectorAll('td')].map(td=>td.textContent)));
        same(actual,raw.slice(0,10).map(row=>[row[idIndex],...cols.map(i=>row[i])]),'T009 quantification strings/IDs changed');
      }
      assert.equal(await page.locator('[data-preview-count="rows"]').textContent(),'2252');assert.equal(await page.locator('[data-preview-count="columns"]').textContent(),'182');
      await selectReal('fasta');assert.equal(await page.locator('[data-quant-select]').inputValue(),'lfq');
      assert.equal(await start.isDisabled(),true);assert.equal(await state('table').getAttribute('data-status'),'empty');
      await single.setInputFiles(fastaPath);await page.waitForFunction(()=>document.querySelector('[data-fasta-count="records"]')?.textContent==='38387');
      assert.equal(await page.locator('[data-fasta-count="length"]').textContent(),'16367314');assert.equal(await page.locator('[aria-label="FASTA ID and length preview"] tbody tr').count(),10);
      assert.equal(await state('fasta').getAttribute('data-status'),'ready');assert.equal(await start.isDisabled(),true);
      console.log('UI REGRESSION T009 Intensity/LFQ 21x10 raw strings exact; single FASTA=38387/16367314/10; independent association slots unchanged');
    });
    await t.test('all displayed strings escape markup; empty and partial groups remain visible with full details',async()=>{
      if(await state('fasta').getAttribute('data-status')!=='ready')await selectReal('fasta');
      const derivedRows=raw.slice(0,3).map(row=>[...row]),firstId=reference.find(row=>row.status==='all').matches[0].id;
      derivedRows[0][idIndex]=firstId+';<img/src=x>';derivedRows[1][idIndex]=' ; ';derivedRows[2][idIndex]=firstId;
      const buffer=Buffer.from([headers,...derivedRows].map(row=>row.join('\t')).join('\n'));
      await derived('table',buffer,'<img src=x>.txt','ready');await start.click();
      for(const[key,value]of Object.entries({rowCount:3,allMatchRows:1,partialMatchRows:1,zeroMatchRows:1}))assert.equal(await root.locator(`[data-assoc-count="${key}"]`).textContent(),String(value));
      assert.equal(await root.locator('img').count(),0);
      assert.ok((await root.locator('[data-assoc-name="table"]').textContent()).includes('<img src=x>'));
      await root.locator('[data-assoc-group="1"]').click();assert.equal(await detail.locator('tbody tr').count(),2);assert.equal(await detail.locator('img').count(),0);
      assert.ok((await detail.locator('tbody tr').last().textContent()).includes('<img/src=x>'));
      await root.locator('[data-assoc-group="2"]').click();assert.equal(await detail.locator('tbody tr').count(),0);assert.ok((await detail.textContent()).includes('空ID'));
      console.log('UI STRINGS filename/group ID/detail ID escaped; partial and empty-ID groups retained; no first-ID truncation');
    });
    assert.deepEqual(errors,[]);
    assert.ok(requests.every(request=>request.method==='GET'&&!new URL(request.url).pathname.startsWith('/api/')));
    assert.ok((await page.evaluate(()=>window.__audit.writes)).every(key=>key==='pp-lang'));
    console.log('UI AUDIT runtimeErrors=0; API/uploadRequests=0; data/metadataStorageWrites=0');
  }finally{
    if(browser)await browser.close();
    for(let i=0;i<inputs.length;i++){
      const[path,,digest]=inputs[i],metadata=await stat(path,{bigint:true});assert.equal(sha(await readFile(path)),digest);assert.equal(metadata.size,before[i].metadata.size);assert.equal(metadata.mtimeNs,before[i].metadata.mtimeNs);
      console.log(`UNCHANGED ${path}; size/mtime/SHA256 match before`);
    }
  }
});
