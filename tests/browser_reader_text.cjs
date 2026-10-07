const {chromium} = require(process.env.EZREAD_PLAYWRIGHT || 'playwright');
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const root = process.env.EZREAD_TEST_ROOT, origin = process.env.EZREAD_TEST_URL, pid = '0123456789abcdef';
const edge = process.env.EZREAD_BROWSER || path.join(process.env['PROGRAMFILES(X86)'] || 'C:/Program Files (x86)', 'Microsoft/Edge/Application/msedge.exe');
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:edge});
  const context = await browser.newContext({viewport:{width:1440,height:1000}});
  const page = await context.newPage(), errors = [], checks = [];
  page.on('pageerror', error => errors.push(error.message));
  const source = id => page.locator(`.reader-source-text[data-source-id="${id}"]`);
  const apiState = async () => (await page.request.get(origin + '/__test/state')).json();
  async function open() {
    await page.goto(origin); await page.waitForSelector('.paper-card');
    await page.evaluate(id => openReader(id), pid); await source('one').waitFor();
    await page.evaluate(() => {
      window.__nativeCopy = null;
      document.addEventListener('copy', e => { window.__nativeCopy = window.getSelection().toString(); e.preventDefault(); }, {once:true});
      Object.defineProperty(navigator, 'clipboard', {configurable:true,value:{writeText:async text => {window.__menuCopy=text;}}});
    });
  }
  async function select(id, quote, drag=false) {
    await page.evaluate(() => { readerCloseTextPopup(true); window.getSelection().removeAllRanges(); });
    const point = await source(id).evaluate((span, quote) => {
      const at = span.textContent.indexOf(quote); if (at < 0) throw Error('Missing quote: ' + quote);
      const range = document.createRange(); range.setStart(span.firstChild,at); range.setEnd(span.firstChild,at+quote.length);
      const start=range.cloneRange(), end=range.cloneRange(); start.collapse(true); end.collapse(false);
      const a=start.getBoundingClientRect(),b=end.getBoundingClientRect();
      window.__selectionNode=span.firstChild;
      return {start:{x:a.left+.25,y:a.top+a.height/2},end:{x:b.left-.25,y:b.top+b.height/2}, x:(a.left+b.left)/2,y:a.top+a.height/2};
    }, quote);
    if(drag) { await page.mouse.move(point.start.x,point.start.y);await page.mouse.down();await page.mouse.move(point.end.x,point.end.y,{steps:8});await page.mouse.up(); }
    else await source(id).evaluate((span,quote)=>{const at=span.textContent.indexOf(quote),range=document.createRange();range.setStart(span.firstChild,at);range.setEnd(span.firstChild,at+quote.length);const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);},quote);
    return point;
  }
  async function menu(point) { await page.mouse.click(point.x,point.y,{button:'right'}); await page.getByRole('menu',{name:'所选文字操作'}).waitFor(); }
  async function command(name) { await page.getByRole('menuitem',{name,exact:true}).click(); }
  async function save(value) { await page.getByRole('textbox',{name:/所选文字批注|修订选中的文字/}).fill(value);await page.locator('.reader-text-editor').getByRole('button',{name:'保存',exact:true}).click();await page.waitForFunction(()=>readerTextUI.editor===null); }
  try {
    await open();
    const startup = await apiState(), initial = startup.doc;
    assert.equal(startup.preparations.length,1);
    assert.equal(startup.calls.length,0);
    await page.evaluate(() => prepareSelectionSession());
    assert.equal((await apiState()).preparations.length,1);
    assert.equal(await page.getByText(/后台连接测试|预热完成/).count(),0);
    checks.push('opening the main page starts silent background preparation once without showing test output');
    const point=await select('one','第一😀句',true);
    await page.getByRole('menu',{name:'所选文字操作'}).waitFor();
    assert.equal(await page.evaluate(()=>window.getSelection().toString()),'第一😀句');
    assert.equal(await page.evaluate(()=>document.querySelector('[data-source-id="one"]').firstChild===window.__selectionNode),true);
    await page.keyboard.press('Control+c');
    assert.equal(await page.evaluate(()=>window.__nativeCopy),'第一😀句');
    await menu(point); await command('复制');assert.equal(await page.evaluate(()=>window.__menuCopy),'第一😀句');
    assert.equal((await apiState()).calls.length,0);
    checks.push('native drag automatically opens the selection menu and Ctrl+C retains exact text');

    await select('one','第一');await page.getByRole('menu',{name:'所选文字操作'}).waitFor();
    await page.keyboard.press('Escape');await page.waitForTimeout(180);
    assert.equal(await page.getByRole('menu',{name:'所选文字操作'}).count(),0,'Escape must not reopen the same menu.');
    await page.evaluate(()=>window.getSelection().modify('extend','forward','character'));
    await page.getByRole('menu',{name:'所选文字操作'}).waitFor();
    await page.evaluate(()=>window.getSelection().removeAllRanges());
    await page.waitForFunction(()=>!document.querySelector('.reader-text-menu'));
    checks.push('keyboard-style selection extension opens the menu, Escape suppresses reopening and clearing selection dismisses it');
    await select('one','第一😀句');await page.getByRole('menu',{name:'所选文字操作'}).waitFor();

    await page.request.post(origin+'/__test/append');await page.evaluate(()=>refreshReader());
    assert.equal(await page.evaluate(()=>window.getSelection().toString()),'第一😀句');
    assert.equal(await page.evaluate(()=>document.querySelector('[data-source-id="one"]').firstChild===window.__selectionNode),true);
    await page.evaluate(()=>window.getSelection().removeAllRanges());await page.evaluate(()=>refreshReader());
    await source('one').filter({hasText:'后台更新'}).waitFor();
    checks.push('background update waits for selection to end');

    const cross = await page.evaluate(()=>{
      const one=document.querySelector('[data-source-id="one"]'),two=document.querySelector('[data-source-id="two"]');
      const range=document.createRange();range.setStart(one.firstChild,0);range.setEnd(two.firstChild,4);window.getSelection().removeAllRanges();window.getSelection().addRange(range);
      const rect=range.getClientRects()[0];return {x:rect.left+3,y:rect.top+rect.height/2};
    });
    assert.equal(await page.evaluate(()=>readerSelectionSnapshot().ranges.length),2);
    await menu(cross);await command('黄色高亮');
    await page.waitForFunction(()=>state.reader.text_annotations?.length===1);
    assert.equal((await apiState()).doc.text_annotations[0].ranges.length,2);
    assert.equal(await page.evaluate(()=>CSS.highlights.get('ezread-text-highlight').size),2);
    checks.push('cross-paragraph selection persists as one two-range highlight');

    const redPoint=await select('two','自然段');await menu(redPoint);await command('红色高亮');
    await page.waitForFunction(()=>state.reader.text_annotations?.some(a=>a.color==='red'));
    await open();
    assert.deepEqual((await apiState()).doc.text_annotations.map(a=>a.color),['yellow','red']);
    assert.equal(await page.evaluate(()=>CSS.highlights.get('ezread-text-highlight').size),2);
    assert.equal(await page.evaluate(()=>CSS.highlights.get('ezread-text-highlight-red').size),1);
    for (const theme of ['paper','sage','graphite']) {
      await page.evaluate(theme=>{state.settings.theme=theme;applyPreferences();},theme);
      assert.equal(await source('two').evaluate(span=>getComputedStyle(span,'::highlight(ezread-text-highlight-red)').backgroundColor),'rgb(246, 190, 190)');
      assert.equal(await source('one').evaluate(span=>getComputedStyle(span,'::highlight(ezread-text-highlight)').backgroundColor),'rgb(246, 231, 182)');
      assert.equal(await source('two').evaluate(span=>getComputedStyle(span,'::highlight(ezread-text-highlight-red)').color),'rgb(32, 43, 37)');
      assert.deepEqual((await apiState()).doc.text_annotations.map(a=>a.color),['yellow','red']);
    }
    await page.evaluate(()=>{state.settings.theme='paper';applyPreferences();});
    checks.push('annotation colors and legible text remain stable across all three themes without changing saved marks');
    await page.setViewportSize({width:1000,height:800});
    await menu(await select('two','自然段'));
    const highlightRow=page.getByRole('group',{name:'高亮',exact:true});
    assert.equal(await highlightRow.count(),1);
    assert.equal(await highlightRow.innerText(),'高亮');
    const swatches=highlightRow.getByRole('menuitem');
    assert.equal(await swatches.count(),2);
    const yellowBounds=await swatches.nth(0).boundingBox(),redBounds=await swatches.nth(1).boundingBox();
    assert.ok(Math.abs(yellowBounds.y-redBounds.y)<1&&redBounds.x>yellowBounds.x);
    assert.equal(await swatches.nth(0).innerText(),'');
    assert.equal(await swatches.nth(1).innerText(),'');
    const bounds=await page.getByRole('menu',{name:'所选文字操作'}).boundingBox();
    assert.ok(bounds.x>=0&&bounds.y>=0&&bounds.x+bounds.width<=1000&&bounds.y+bounds.height<=800);
    await page.screenshot({path:path.join(root,'work/reader-highlight-menu-preview.png')});
    await page.evaluate(()=>{readerCloseTextPopup();window.getSelection().removeAllRanges();});
    await page.screenshot({path:path.join(root,'work/reader-red-highlight-preview.png')});
    await page.setViewportSize({width:1440,height:1000});
    checks.push('one highlight row with clickable yellow/red swatches fits narrow menus and preserves colors on reopening');

    await page.evaluate(()=>window.getSelection().removeAllRanges());
    await page.getByRole('button',{name:'英文原文',exact:true}).click();
    const notePoint=await select('one','Another sentence');await menu(notePoint);await command('添加批注');await save('精准的文字批注');
    assert.equal((await apiState()).doc.text_annotations.filter(a=>a.kind==='note').length,1);
    await open();
    await page.locator('.translation-block[data-block-id="one"] .reader-annotation-rail').first().click();
    await page.getByText('精准的文字批注',{exact:true}).waitFor();
    await page.screenshot({path:path.join(root,'work/reader-text-note-preview.png')});
    assert.equal(await page.locator('.reader-annotation-markers').count(),0);
    assert.equal(await source('one').evaluate(span=>getComputedStyle(span,'::highlight(ezread-text-note)').textDecorationColor),'rgb(59, 130, 246)');
    checks.push('note survives reopening, has a blue underline and opens from its blue side rail');

    await select('one','Another sentence');await page.getByRole('menu',{name:'所选文字操作'}).waitFor();
    await command('添加批注');await save('重叠的另一条批注');
    await page.locator('.translation-block[data-block-id="one"] .reader-annotation-rail').click();
    await page.getByRole('button',{name:'重叠的另一条批注',exact:true}).click();
    await page.getByText('重叠的另一条批注',{exact:true}).waitFor();
    checks.push('overlapping notes share a rail and each remains accessible');

    await page.evaluate(()=>{readerCloseTextPopup();window.getSelection().removeAllRanges();});
    await source('four').scrollIntoViewIfNeeded();
    await page.evaluate(()=>{
      const one=document.querySelector('[data-source-id="four"]'),two=document.querySelector('[data-source-id="five"]');
      const range=document.createRange();range.setStart(one.firstChild,0);range.setEnd(two.firstChild,8);
      const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);window.__longNoteNode=one.firstChild;
    });
    await page.getByRole('menu',{name:'所选文字操作'}).waitFor();await command('添加批注');await save('跨段和多行批注');
    const crossNote=(await apiState()).doc.text_annotations.find(a=>a.note==='跨段和多行批注');
    assert.equal(crossNote.ranges.length,2);
    assert.equal(await page.locator(`.reader-annotation-rail[data-annotation-ids="${crossNote.id}"]`).count(),2);
    const geometryMatches=async ()=>{ try { await page.waitForFunction(id=>{
      for(const painted of readerTextUI.painted.filter(v=>v.item.id===id)) {
        const span=painted.range.startContainer.parentElement.closest('.reader-source-text');
        const rail=span.closest('.translation-block').querySelector('.reader-annotation-rail');
        const rects=[...painted.range.getClientRects()].filter(r=>r.width&&r.height),box=rail.getBoundingClientRect();
        const top=Math.min(...rects.map(r=>r.top)),bottom=Math.max(...rects.map(r=>r.bottom));
        if(Math.abs(box.top-top)>1||Math.abs(box.bottom-bottom)>1||box.left<Math.max(...rects.map(r=>r.right)))return false;
      }
      return true;
    },crossNote.id,{timeout:3000}); } catch(error) {
      console.log(JSON.stringify(await page.evaluate(id=>readerTextUI.painted.filter(v=>v.item.id===id).map(painted=>{
        const span=painted.range.startContainer.parentElement.closest('.reader-source-text'),paragraph=span.closest('.translation-block');
        const rail=paragraph.querySelector('.reader-annotation-rail'),box=rail.getBoundingClientRect(),rects=[...painted.range.getClientRects()].filter(r=>r.width&&r.height);
        return {source:span.dataset.sourceId,rail:{top:box.top,bottom:box.bottom,left:box.left},expected:{top:Math.min(...rects.map(r=>r.top)),bottom:Math.max(...rects.map(r=>r.bottom)),right:Math.max(...rects.map(r=>r.right))},style:rail.getAttribute('style'),paragraph:paragraph.getBoundingClientRect().toJSON()};
      }),crossNote.id)));
      await page.screenshot({path:path.join(root,'work/reader-annotation-geometry-failure.png')});throw error;
    } };
    await geometryMatches();
    await page.setViewportSize({width:850,height:1000});await geometryMatches();
    await source('four').evaluate(span=>{span.closest('.translated-text').style.fontSize='26px';});await geometryMatches();
    assert.equal(await source('four').evaluate(span=>span.firstChild===window.__longNoteNode),true);
    await source('four').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(root,'work/reader-annotation-rails-preview.png')});
    await page.locator(`.translation-block[data-block-id="five"] .reader-annotation-rail`).click();
    await page.getByText('跨段和多行批注',{exact:true}).waitFor();
    await page.setViewportSize({width:1440,height:1000});
    await open();
    checks.push('one cross-paragraph note has per-paragraph rails covering actual selected lines after resize and font changes without replacing text');

    const editPoint=await select('one','First');await menu(editPoint);await command('修订原文');await save('Corrected');
    let current=(await apiState()).doc;
    assert.equal(current.blocks[0].text,initial.blocks[0].text);
    assert.equal(current.blocks[0].translation,initial.blocks[0].translation+' 后台更新。');
    assert.ok(current.blocks[0].text_override.startsWith('Corrected'));
    await page.getByText('原文已修改，译文待核对',{exact:true}).waitFor();
    assert.equal((await apiState()).calls.length,0);
    checks.push('source correction is reversible and retains original text and translation');

    await page.getByRole('button',{name:'译文',exact:true}).click();
    const chinesePoint=await select('one','😀');await menu(chinesePoint);await command('修订译文');await save('修正');
    current=(await apiState()).doc;assert.ok(current.blocks[0].translation.includes('第一修正句'));
    assert.equal(await page.evaluate(()=>state.reader.text_annotations.find(a=>a.kind==='highlight').status),'needs_review');
    await page.getByRole('button',{name:/待核对标记 1/}).waitFor();
    checks.push('translation revision changes only selected characters and flags changed anchors');

    await page.getByRole('button',{name:'英文原文',exact:true}).click();
    const wordPoint=await select('one','Corrected');await menu(wordPoint);await command('翻译单词');
    await page.waitForFunction(()=>document.querySelector('.reader-selected-translation-result')?.textContent.includes('corrected'));
    assert.equal((await apiState()).calls.length,0,'Dictionary lookup must not call the model.');
    checks.push('offline dictionary finds selected words without model calls');
    const translatePoint=await select('one','Corrected');await menu(translatePoint);await command('翻译句子');
    await page.getByText('模拟划线译文：Corrected',{exact:true}).waitFor();
    assert.equal((await apiState()).calls.length,1);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#reader-selection-mode-hint').innerText(),'选中文字即可显示操作选项');
    const autoPoint=await select('one','Corrected',true);
    assert.equal(await page.evaluate(()=>window.getSelection().toString()),'Corrected');
    assert.equal((await apiState()).calls.length,1,'Left mouse selection must not infer automatically.');
    await menu(autoPoint);await command('复制');
    assert.equal((await apiState()).calls.length,1,'Copying must not infer.');
    checks.push('automatic selection menu still requires an explicit dictionary or model choice and retains native text selection');

    await page.request.post(origin+'/__test/model-delay',{data:{enabled:true}});
    const slowStart = async () => {
      const count=(await apiState()).calls.length;
      const point=await select('one','Another sentence.');await menu(point);await command('翻译句子');
      await page.waitForFunction(async count=>(await (await fetch('/__test/state')).json()).calls.length>count,count);
    };
    const cancelled = async () => page.waitForFunction(async ()=>(await (await fetch('/__test/state')).json()).calls.at(-1)?.cancelled===true);
    await slowStart();
    await page.keyboard.press('Escape');
    await cancelled();
    checks.push('closing translation card cancels the actual backend request');
    await slowStart();
    await source('one').evaluate(span=>{const range=document.createRange();range.setStart(span.firstChild,0);range.setEnd(span.firstChild,9);const s=window.getSelection();s.removeAllRanges();s.addRange(range);});
    await cancelled();
    assert.equal(await page.locator('.reader-selected-translation').count(),0);
    const replacement=await select('one','Corrected');await menu(replacement);await command('翻译单词');
    await page.waitForFunction(()=>document.querySelector('.reader-selected-translation-result')?.textContent.includes('corrected'));
    await page.waitForTimeout(150);
    assert.match(await page.locator('.reader-selected-translation-result').innerText(),/corrected/);
    checks.push('selection change cancels old inference and old result cannot overwrite dictionary card');
    await slowStart();await page.evaluate(()=>closeReader());await cancelled();
    await page.evaluate(id=>openReader(id),pid);await source('one').waitFor();
    checks.push('closing and reopening reader cancels pending model translation');

    await page.request.post(origin+'/__test/model-delay',{data:{enabled:false}});
    await page.request.post(origin+'/__test/model-progress',{data:{mode:'success'}});
    await page.setViewportSize({width:550,height:700});
    await menu(await select('one','Another sentence.'));await command('翻译句子');
    const latest=page.locator('.reader-translation-current-status');
    await latest.getByText('网络不可用. 正在重新连接... 1/5',{exact:true}).waitFor();
    await page.getByText('模拟正在输出的部分译文',{exact:true}).waitFor();
    const progressBounds=await page.locator('.reader-selected-translation').boundingBox();
    assert(progressBounds.x>=0&&progressBounds.y>=0&&progressBounds.x+progressBounds.width<=551&&progressBounds.y+progressBounds.height<=701);
    await page.screenshot({path:path.join(root,'work/reader-sentence-progress-preview.png')});
    await page.waitForFunction(()=>readerTextUI.request===null);
    assert.equal(await page.locator('.reader-selected-translation-result').innerText(),'模拟划线译文：Another sentence.');
    assert.equal(await latest.innerText(),'翻译完成。');
    assert.equal(await page.locator('.reader-translation-progress-log').count(),0);
    assert.match(await page.locator('.reader-translation-progress-hint').innerText(),/用时/);
    assert.equal(await page.locator('.reader-selected-translation button').count(),0);
    checks.push('latest Chinese status and the current partial translation overwrite old output in a narrow window');

    await page.request.post(origin+'/__test/model-progress',{data:{mode:'failure'}});
    await menu(await select('one','Another sentence.'));await command('翻译句子');
    await page.getByText('无法连接 Codex 服务，请检查网络。',{exact:true}).first().waitFor();
    await page.waitForFunction(()=>readerTextUI.request===null);
    assert.equal(await latest.innerText(),'无法连接 Codex 服务，请检查网络。');
    assert.equal(await latest.count(),1);
    await page.screenshot({path:path.join(root,'work/reader-sentence-failure-preview.png')});
    checks.push('failure displays only the latest error without accumulating a conversation history');
    await page.request.post(origin+'/__test/model-progress',{data:{mode:'success'}});
    await menu(await select('one','Another sentence.'));await command('翻译句子');
    await latest.getByText('网络不可用. 正在重新连接... 1/5',{exact:true}).waitFor();
    await page.getByRole('button',{name:'取消翻译',exact:true}).click();await cancelled();
    assert.equal(await page.locator('.reader-selected-translation-result').innerText(),'已取消本次翻译。');
    assert.match(await latest.innerText(),/对话保留/);
    assert.equal(await page.getByRole('button',{name:'取消翻译',exact:true}).count(),0);
    await menu(await select('one','Corrected'));await command('翻译单词');
    await page.waitForFunction(()=>document.querySelector('.reader-selected-translation-result')?.textContent.includes('corrected'));
    await page.waitForTimeout(650);
    assert.equal(await page.locator('.reader-translation-progress').count(),0);
    assert.match(await page.locator('.reader-selected-translation-result').innerText(),/corrected/);
    checks.push('manual cancel interrupts the request and polling, retains the paper context and cannot overwrite a new dictionary result');
    await page.request.post(origin+'/__test/model-progress',{data:{mode:''}});

    for(const width of [850,550]) {
      await page.setViewportSize({width,height:900});
      const point=await select('one','Corrected');await menu(point);
      await page.getByRole('menuitem',{name:'翻译单词',exact:true}).waitFor();
      await page.getByRole('menuitem',{name:'翻译句子',exact:true}).waitFor();
      const rect=await page.getByRole('menu',{name:'所选文字操作'}).boundingBox();
      assert(rect.x>=0&&rect.y>=0&&rect.x+rect.width<=width+1&&rect.y+rect.height<=901);
      await page.screenshot({path:path.join(root,`work/reader-translation-menu-${width}.png`)});
    }
    checks.push('translation choices fit narrow windows at 850 and 550 pixels');
    await page.setViewportSize({width:1440,height:1000});
    await page.screenshot({path:path.join(root,'work/reader-text-selection-preview.png')});
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(root,'work/reader-text-browser-verification.json'),JSON.stringify({checks,errors,realModelCalls:0,realLibraryWrites:0,mockedTranslationCalls:(await apiState()).calls.length},null,2));
    console.log(JSON.stringify({checks:checks.length,errors,realModelCalls:0,realLibraryWrites:0}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
