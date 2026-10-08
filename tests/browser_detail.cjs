const { chromium } = require(process.env.EZREAD_PLAYWRIGHT || 'playwright');
const assert = require('node:assert/strict'), path = require('node:path'), fs = require('node:fs');
const root = process.env.EZREAD_TEST_ROOT, origin = process.env.EZREAD_TEST_URL, pid = '0123456789abcdef';
const edge = process.env.EZREAD_BROWSER || path.join(process.env['PROGRAMFILES(X86)'] || 'C:/Program Files (x86)', 'Microsoft/Edge/Application/msedge.exe');
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: edge });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' }), errors = [], layouts = [], metadataLayouts = [], themes = [], metadataRequests = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (request.url().endsWith('/metadata-enrich')) metadataRequests.push(request); });
  try {
    await page.goto(origin); await page.waitForSelector('.paper-card');
    await page.evaluate(id => openDetail(id), pid);
    const dialog = page.locator('#detail-dialog');
    assert.equal(await dialog.getByText(/作者与团队|整理论文|主题合集|主题集合/).count(), 0);
    assert.equal(await dialog.locator('details').count(), 0);
    assert.equal(await dialog.locator('.translation-status-line').count(), 0);
    assert.equal(await dialog.getByRole('button', { name: '核对出版信息', exact: true }).count(), 0);
    assert.equal(await dialog.locator('.publication-verified').innerText(), '出版信息已核对');
    assert.equal(await dialog.locator('.detail-topbar #detail-collection-trigger').count(), 0);
    assert.equal(await dialog.locator('.detail-tags .tag').count(), 6);
    assert.equal(await dialog.getByText('关键词', { exact: true }).count(), 0);
    assert.equal(await dialog.locator('.detail-keywords-empty').count(), 0);
    assert.equal(await dialog.getByText(/按当前期刊名单匹配|查看与编辑名单|原版 PDF/).count(), 0);
    assert.equal(await dialog.getByRole('link', { name: '发表信息来源', exact: true }).count(), 0);
    await page.evaluate(() => { void openPublicationLookup(state.detail); });
    const publication = page.locator('#publication-dialog');
    assert.equal(await publication.locator('h2').innerText(), '出版信息已核对');
    assert.equal(await publication.getByText(/未能可靠识别/).count(), 0);
    assert.equal(await publication.getByRole('link', { name: '查看出版信息来源', exact: true }).count(), 0);
    await publication.getByRole('button', { name: '完成', exact: true }).click();
    assert.equal(metadataRequests.length, 0, 'Reopening/closing a verified record must not change consent or search again');
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 780, height: 850 }, { width: 390, height: 780 }, { width: 1440, height: 1000, fontSize: 22 }]) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.evaluate(size => { document.documentElement.style.setProperty('--ui-font-size', `${size}px`); }, viewport.fontSize || 16);
      await page.evaluate(() => { document.querySelector('#detail-scroll').scrollTop = 0; });
      const measure = await page.evaluate(() => {
        const box = selector => { const r = document.querySelector(selector).getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width }; };
        const frame = document.querySelector('#detail-dialog'), scroll = document.querySelector('#detail-scroll'), main = document.querySelector('.detail-main');
        const gaps = [...document.querySelectorAll('.insight')].map(node => {
          const heading = node.querySelector('h4').getBoundingClientRect(), body = node.querySelector('p').getBoundingClientRect();
          return body.top - heading.bottom;
        });
        return { frame: box('#detail-dialog'), scroll: box('#detail-scroll'), picker: box('#detail-collection-trigger'), hero: box('.detail-hero'),
          publicationControl: box('.detail-publication-actions > :first-child'), keywords: box('.detail-keywords'),
          tags: [...document.querySelectorAll('.detail-tags .tag')].map(node => { const r = node.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom }; }),
          actions: [...document.querySelectorAll('.detail-start .button')].map(node => { const r = node.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width }; }),
          overview: box('.detail-overview'), notes: box('.detail-notes'), gaps,
          badge: box('.detail-tier .rank-badge'), doi: box('.metadata-line a'), metadata: box('.metadata-line'),
          badgeColor: getComputedStyle(document.querySelector('.detail-tier .rank-badge')).backgroundColor,
          contentWidth: main.clientWidth - parseFloat(getComputedStyle(main).paddingLeft) - parseFloat(getComputedStyle(main).paddingRight),
          overflow: scroll.scrollWidth > scroll.clientWidth + 1, frameOverflow: getComputedStyle(frame).overflow,
          innerOutline: getComputedStyle(scroll).outlineStyle,
          scrollHeight: scroll.scrollHeight, clientHeight: scroll.clientHeight, outerScrollTop: frame.scrollTop };
      });
      if (viewport.width > 600) {
        assert.ok(measure.picker.left >= measure.publicationControl.right + 8, 'Collection box belongs to the right of the publication control');
        assert.ok(Math.abs((measure.picker.top + measure.picker.bottom) - (measure.publicationControl.top + measure.publicationControl.bottom)) <= 2, 'Publication control and collection box align vertically');
      } else assert.ok(measure.picker.top >= measure.publicationControl.bottom + 8, 'Narrow windows stack controls without squeezing their text');
      assert.ok(measure.keywords.top >= measure.picker.bottom + 8, 'Keywords remain a separate group below publication and collection controls');
      assert.ok(measure.tags.every(tag => tag.left >= measure.keywords.left - 1 && tag.right <= measure.keywords.right + 1), 'Keywords wrap within their own region');
      assert.ok(measure.tags.every((tag, index, tags) => !index || (Math.abs(tag.top - tags[index - 1].top) <= 1 ? tag.left >= tags[index - 1].right + 6 : tag.top >= tags[index - 1].bottom + 6)), 'Keyword pills have comfortable spacing across rows');
      assert.ok(measure.actions.every(action => action.width > 90 && action.top >= measure.keywords.bottom + 8), 'Reading actions have enough width and clear keyword spacing');
      if (viewport.width > 480) {
        assert.ok(measure.actions.every(action => Math.abs(action.top - measure.actions[0].top) <= 1 && Math.abs(action.bottom - measure.actions[0].bottom) <= 1), 'Reading actions align in one even row');
      } else assert.ok(measure.actions[0].bottom + 8 <= measure.actions[1].top && Math.abs(measure.actions[1].top - measure.actions[2].top) <= 1, 'Narrow windows give reading its own row');
      assert.ok(Math.abs(measure.overview.width - measure.contentWidth) <= 1, 'Overview uses the full content width');
      assert.ok(measure.notes.top - measure.overview.bottom >= 24, 'Notes follow overview with clear spacing');
      assert.ok(measure.badge.left >= measure.doi.right && Math.abs(measure.badge.right - measure.metadata.right) <= 1, 'Tier badge belongs to the right of DOI and aligns to the metadata edge');
      assert.equal(measure.badgeColor, 'rgb(231, 216, 174)', 'Top-tier color matches the library legend');
      assert.ok(measure.scroll.right <= measure.frame.right - 10 && measure.scroll.left >= measure.frame.left + 10, 'Scrollbar is inset within the rounded frame');
      assert.ok(measure.scroll.top >= measure.frame.top + 10 && measure.scroll.bottom <= measure.frame.bottom - 10);
      assert.equal(measure.frameOverflow, 'hidden'); assert.equal(measure.overflow, false);
      assert.equal(measure.innerOutline, 'none', 'The inner scrolling region must not add a second visible frame');
      assert.ok(measure.scrollHeight > measure.clientHeight, 'Long detail content uses the inner scroller');
      assert.ok(measure.gaps.every(gap => gap >= 8));
      assert.ok(Math.max(...measure.gaps) - Math.min(...measure.gaps) <= 1, 'Insight heading/body gaps are uniform');
      await page.evaluate(() => { document.querySelector('#detail-scroll').scrollTop = 300; });
      assert.equal(await page.locator('#detail-dialog').evaluate(node => node.scrollTop), 0);
      layouts.push({ viewport, ...measure });
      if (viewport.width === 1440 && !viewport.fontSize) {
        await page.evaluate(() => { document.querySelector('#detail-scroll').scrollTop = 0; });
        await dialog.screenshot({ path: path.join(root, 'work/detail-layout-top.png') });
        await page.locator('.detail-overview').scrollIntoViewIfNeeded();
        await dialog.screenshot({ path: path.join(root, 'work/detail-layout-overview.png') });
        await page.evaluate(() => { const scroll = document.querySelector('#detail-scroll'); scroll.scrollTop = scroll.scrollHeight; });
        await dialog.screenshot({ path: path.join(root, 'work/detail-layout-notes.png') });
      } else if (viewport.width === 390) {
        await page.locator('.detail-publication-actions').scrollIntoViewIfNeeded();
        await dialog.screenshot({ path: path.join(root, 'work/detail-layout-narrow.png') });
      }
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.evaluate(() => { document.documentElement.style.setProperty('--ui-font-size', '16px'); });
    await page.evaluate(() => { document.querySelector('#detail-scroll').scrollTop = 0; });
    for (const theme of ['paper', 'sage', 'graphite']) {
      await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
      assert.equal(await dialog.getByText(/按当前期刊名单匹配|查看与编辑名单|原版 PDF/).count(), 0);
      assert.equal(await dialog.locator('.detail-tags .tag').count(), 6);
      assert.equal(await page.locator('#detail-scroll').evaluate(node => node.scrollWidth > node.clientWidth + 1), false);
      await page.locator('.detail-hero').screenshot({ path: path.join(root, `work/detail-keywords-${theme}.png`) });
      themes.push(theme);
    }
    await page.evaluate(() => { document.documentElement.dataset.theme = 'paper'; });
    // The same row must also work before the user has verified publication data.
    await page.evaluate(() => { state.detail.metadata_enrichment = {}; renderDetail(); });
    const uncheckedControl = dialog.getByRole('button', { name: '核对出版信息', exact: true });
    const uncheckedBounds = await uncheckedControl.boundingBox(), uncheckedPicker = await page.locator('#detail-collection-trigger').boundingBox();
    assert.ok(uncheckedPicker.x >= uncheckedBounds.x + uncheckedBounds.width + 8);
    await dialog.screenshot({ path: path.join(root, 'work/detail-layout-publication-pending.png') });
    await page.evaluate(() => { state.detail.tags = []; renderDetail(); });
    const keywordRegion = dialog.locator('.detail-keywords');
    assert.equal(await keywordRegion.isVisible(), true);
    assert.equal(await keywordRegion.locator('p').innerText(), '生成速览以显示论文关键词');
    assert.equal(await dialog.getByText('关键词', { exact: true }).count(), 0);
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      assert.equal(await page.locator('#detail-scroll').evaluate(node => node.scrollWidth > node.clientWidth + 1), false);
      await keywordRegion.screenshot({ path: path.join(root, `work/detail-keywords-empty-${width}.png`) });
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    // Exercise the user action without starting a backend task or real model call.
    await page.evaluate(() => {
      window.__savedKeywordTask = runPaperTask;
      window.__keywordTaskCalls = [];
      runPaperTask = async task => {
        window.__keywordTaskCalls.push(task);
        state.detail.summarize_status = 'running'; renderDetail();
      };
    });
    assert.deepEqual(await page.evaluate(() => window.__keywordTaskCalls), []);
    await keywordRegion.getByRole('button', { name: '生成速览', exact: true }).click();
    assert.deepEqual(await page.evaluate(() => window.__keywordTaskCalls), ['summarize']);
    assert.equal(await keywordRegion.getByRole('button', { name: '正在生成…', exact: true }).isDisabled(), true);
    assert.equal(await keywordRegion.locator('p').innerText(), '正在生成速览，完成后显示论文关键词。');
    await page.evaluate(() => { state.detail.tags = ['示例论文主题']; state.detail.summarize_status = 'completed'; renderDetail(); });
    assert.equal(await dialog.locator('.detail-keywords-empty').count(), 0);
    assert.equal(await keywordRegion.locator('.tag').innerText(), '示例论文主题');
    await page.evaluate(() => { runPaperTask = window.__savedKeywordTask; delete window.__savedKeywordTask; delete window.__keywordTaskCalls; });
    await page.evaluate(id => openDetail(id), pid);
    await page.locator('#detail-collection-trigger').click();
    await page.locator('#detail-collection-menu').press('Escape');
    assert.equal(await page.locator('#detail-collection-trigger').getAttribute('aria-expanded'), 'false');
    assert.equal(await dialog.evaluate(node => node.open), true, 'Escape closes the moved collection menu without closing the detail dialog');
    await page.locator('#detail-collection-trigger').focus();
    await page.locator('#detail-collection-trigger').press('ArrowDown');
    await page.getByRole('option', { name: '触觉传感', exact: true }).click();
    await page.waitForFunction(() => state.detail.collection === '触觉传感');
    assert.equal(await page.locator('#detail-collection-trigger').innerText(), '触觉传感');
    const notes = page.getByRole('textbox', { name: '个人论文笔记' });
    await notes.fill('新的笔记保留在研究速览下方。');
    await page.evaluate(() => flushDetailNotes());
    const stored = await (await page.request.get(origin + '/__test/detail-state')).json();
    assert.equal(stored.notes, '新的笔记保留在研究速览下方。');
    assert.equal(stored.team, '保留的历史团队资料');
    const publicPaper = await (await page.request.get(`${origin}/api/papers/${pid}`)).json();
    assert.equal(Object.hasOwn(publicPaper.paper || publicPaper, 'team'), false);
    const retired = await page.request.post(`${origin}/api/papers/${pid}/team`, { data: {} });
    assert.equal(retired.status(), 410);
    await page.getByRole('button', { name: '关闭论文简介', exact: true }).click();
    await page.evaluate(id => openDetail(id), pid);
    assert.equal(await page.getByRole('textbox', { name: '个人论文笔记' }).inputValue(), stored.notes);
    assert.equal(await dialog.locator('.publication-verified').innerText(), '出版信息已核对');
    const edit = page.locator('#edit-dialog');
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 780, height: 850 }, { width: 390, height: 780 }, { width: 390, height: 480 }, { width: 1440, height: 1000, fontSize: 22 }]) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.evaluate(size => { document.documentElement.style.setProperty('--ui-font-size', `${size}px`); }, viewport.fontSize || 16);
      await page.evaluate(() => openMetadata(state.detail));
      const measure = () => page.evaluate(() => {
        const frame = document.querySelector('#edit-dialog'), scroll = frame.querySelector('.metadata-scroll');
        const box = node => { const r = node.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, left: r.left, right: r.right }; };
        return { frame: box(frame), heading: box(frame.querySelector('.metadata-heading')), close: box(frame.querySelector('[aria-label="关闭编辑"]')),
          scroll: box(scroll), scrollTop: scroll.scrollTop, scrollHeight: scroll.scrollHeight, clientHeight: scroll.clientHeight,
          overflow: getComputedStyle(frame).overflow, frameScrollTop: frame.scrollTop, horizontalOverflow: scroll.scrollWidth > scroll.clientWidth + 1 };
      });
      const before = await measure();
      await edit.locator('.metadata-scroll').evaluate(node => { node.scrollTop = node.scrollHeight; });
      const after = await measure();
      assert.deepEqual(after.heading, before.heading, 'The metadata title stays fixed while the form scrolls');
      assert.deepEqual(after.close, before.close, 'The close button stays fixed while the form scrolls');
      assert.ok(after.scrollTop > 0 && after.scrollHeight > after.clientHeight);
      assert.equal(after.frameScrollTop, 0); assert.equal(after.overflow, 'hidden');
      assert.equal(after.horizontalOverflow, false);
      assert.ok(after.scroll.left >= after.frame.left + 8 && after.scroll.right <= after.frame.right - 8);
      assert.ok(after.scroll.bottom <= after.frame.bottom - 8 && after.scroll.top >= after.heading.bottom - 1);
      assert.ok(after.frame.top >= 19 && after.frame.bottom <= viewport.height - 19);
      assert.equal(await edit.getByRole('button', { name: '关闭编辑', exact: true }).isVisible(), true);
      metadataLayouts.push({ viewport, before, after });
      if (viewport.width === 1440 && !viewport.fontSize) await edit.screenshot({ path: path.join(root, 'work/metadata-fixed-header-bottom.png') });
      if (viewport.width === 390 && viewport.height === 480) await edit.screenshot({ path: path.join(root, 'work/metadata-fixed-header-narrow.png') });
      await edit.getByRole('button', { name: '关闭编辑', exact: true }).click();
      assert.equal(await edit.evaluate(node => node.open), false);
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.evaluate(() => { document.documentElement.style.setProperty('--ui-font-size', '16px'); openMetadata(state.detail); });
    await edit.getByRole('textbox', { name: '中文标题', exact: true }).fill('编辑窗口保存回归');
    await edit.getByRole('button', { name: '保存信息', exact: true }).click();
    await page.waitForFunction(() => !document.querySelector('#edit-dialog').open);
    const savedMetadata = await (await page.request.get(origin + '/__test/detail-state')).json();
    assert.equal(savedMetadata.title_zh, '编辑窗口保存回归');
    assert.equal(savedMetadata.metadata_source, 'https://example.org/publication/detail-fixture');
    await page.evaluate(() => openMetadata(state.detail));
    assert.equal(await edit.getByRole('textbox', { name: '中文标题', exact: true }).inputValue(), savedMetadata.title_zh);
    await edit.getByRole('textbox', { name: '中文标题', exact: true }).fill('关闭按钮不应提交这份修改');
    await edit.getByRole('button', { name: '关闭编辑', exact: true }).click();
    assert.equal((await (await page.request.get(origin + '/__test/detail-state')).json()).title_zh, savedMetadata.title_zh);
    assert.equal(await dialog.getByRole('link', { name: '发表信息来源', exact: true }).count(), 0);
    assert.equal(errors.length, 0, errors.join('\n'));
    const report = { layouts, metadataLayouts, themes, errors, collectionSaved: true, notesSavedAndReopened: true, metadataSavedAndReopened: true, metadataCloseDoesNotSubmit: true, publicationSourceRemoved: true, retiredTeamStatus: 410, realModelCalls: 0, realLibraryWrites: 0 };
    fs.writeFileSync(path.join(root, 'work/detail-layout-verification.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ viewportChecks: layouts.length, metadataViewportChecks: metadataLayouts.length, themes, errors, collectionSaved: true, notesSavedAndReopened: true, metadataSavedAndReopened: true, metadataCloseDoesNotSubmit: true, publicationSourceRemoved: true, retiredTeamStatus: 410 }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
