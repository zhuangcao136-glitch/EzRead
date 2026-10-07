function renderLibrary() {
  if (draggingPaperIds.length) { paperDragDeferredRender = true; return; }
  if ($("#queue-dialog").open) { renderNav(); return; }
  paperLayoutCleanup?.(); paperLayoutCleanup = null;
  paperLayoutObserver?.disconnect(); paperLayoutObserver = null;
  renderNav();
  selectOptions("#filter-journal", [...new Set(state.papers.map(journalOf))].sort((a, b) => a.localeCompare(b, "en", { sensitivity: "base" })), "所有出版来源");
  $("#library-view").setAttribute("aria-label", state.collection || VIEW_NAMES[state.view] || VIEW_NAMES.all);
  const hasFilters = Boolean(state.query || $$(".filter-group select:not(#sort)").some(s => s.value));
  $("#clear-filters").classList.toggle("hidden", !hasFilters);
  syncFilterTriggers();
  syncSortControls();
  const grid = $("#paper-grid"), empty = $("#empty-state");
  grid.classList.remove("masonry", "is-laid-out"); grid.style.height = "";
  if (state.loading) { grid.replaceChildren(...Array.from({ length: 6 }, () => el("div", { class: "skeleton-card" }))); empty.classList.add("hidden"); return; }
  const papers = filteredPapers(); $("#result-count").textContent = `${papers.length} 篇论文${hasFilters ? ` / 共 ${state.papers.length} 篇` : ""}`;
  empty.classList.toggle("hidden", state.papers.length > 0); grid.classList.toggle("hidden", !state.papers.length);
  if (state.papers.length && !papers.length) grid.replaceChildren(el("div", { class: "no-results", style: { columnSpan: "all" } }, el("h3", {}, "暂时没有匹配的论文"), button("查看全部论文", () => { clearFilters(); setView("all"); }, "subtle", "", { style: { marginTop: "20px" } })));
  else if (papers.length) renderMasonry(grid, papers);
  else grid.replaceChildren();
  renderLegend(); renderBulkbar(papers);
}
function renderMasonry(grid, papers) {
  const cards = papers.map(renderCard);
  grid.classList.add("masonry");
  grid.replaceChildren(...cards);
  let scheduled = false;
  const layout = () => {
    scheduled = false;
    if (!grid.isConnected || !grid.classList.contains("masonry") || grid.firstElementChild !== cards[0]) return;
    const style = getComputedStyle(grid);
    // CSS resolves the available width into equal tracks, including on resize.
    const tracks = style.gridTemplateColumns.split(/\s+/).map(Number.parseFloat).filter(Number.isFinite);
    if (!tracks.length || !grid.clientWidth) return;
    const width = tracks[0], gap = Number.parseFloat(style.columnGap) || 0;
    const heights = Array(tracks.length).fill(0);
    // Set every width before measuring heights so wrapped text packs correctly.
    cards.forEach(card => { card.style.width = `${width}px`; });
    for (const card of cards) {
      const column = heights.indexOf(Math.min(...heights));
      card.style.left = `${column * (width + gap)}px`;
      card.style.top = `${heights[column]}px`;
      heights[column] += card.offsetHeight + gap;
    }
    grid.style.height = `${Math.max(...heights) - gap}px`;
    grid.classList.add("is-laid-out");
  };
  const schedule = () => { if (!scheduled) { scheduled = true; requestAnimationFrame(layout); } };
  if (window.ResizeObserver) {
    paperLayoutObserver = new ResizeObserver(schedule);
    paperLayoutObserver.observe(grid);
    cards.forEach(card => paperLayoutObserver.observe(card));
  } else {
    cards.forEach(card => card.querySelector("img")?.addEventListener("load", schedule));
    window.addEventListener("resize", schedule);
    paperLayoutCleanup = () => window.removeEventListener("resize", schedule);
  }
  schedule();
}
function renderCard(p) {
  const t = translationOf(p), rank = rankAppearance(p), cover = el("div", { class: "card-cover-inner" });
  const media = el("div", { class: "cover-media" });
  if (p.cover_url) { const img = imageNode(p.cover_url, { alt: `${titleOf(p)} · 封面`, onerror: event => event.target.replaceWith(placeholderImage()) }); media.append(img); } else media.append(placeholderImage());
  cover.append(media);
  const favorite = el("button", { class: `card-save ${p.favorite ? "saved" : ""}`, title: p.favorite ? "取消收藏" : "收藏论文", "aria-label": p.favorite ? "取消收藏" : "收藏论文", onclick: event => { event.stopPropagation(); act(() => patchPaper(p.id, { favorite: !p.favorite }), event.currentTarget); } }, icon("star"));
  const remove = iconButton("trash", `删除论文：${titleOf(p)}`, event => { event.stopPropagation(); if (confirm(`将《${titleOf(p)}》移入回收站？可在设置 → 文献与备份中恢复。`)) void act(() => deletePapers([p.id]), event.currentTarget); });
  remove.classList.add("card-delete");
  const cornerAction = state.selectionMode ? el("button", { class: `card-select ${state.selectedPapers.has(p.id) ? "selected" : ""}`, title: state.selectedPapers.has(p.id) ? "取消选择" : "选择论文", "aria-label": `${state.selectedPapers.has(p.id) ? "取消选择" : "选择论文"}：${titleOf(p)}`, "aria-pressed": String(state.selectedPapers.has(p.id)), onclick: event => { event.stopPropagation(); togglePaperSelection(p.id); } }, state.selectedPapers.has(p.id) ? icon("check") : null) : remove;
  const actions = el("div", { class: "card-actions" }, favorite, cornerAction);
  const translation = button(cardTranslationLabel(p), event => { event.stopPropagation(); openTranslationSetup([p]); }, "card-translate", "translate", { title: `${statusName(t.status)} · 当前译文 ${t.current_done || 0}/${t.total || 0} 段；点击确认翻译或整篇重译`, "aria-label": `${cardTranslationLabel(p)}：${titleOf(p)}` });
  const details = button("论文简介", event => { event.stopPropagation(); act(() => openDetail(p.id)); }, "card-detail", "arrow", { "aria-label": `查看论文简介：${titleOf(p)}` });
  const config = cardModelConfig(p);
  const configText = config.model && config.effort ? `${config.model} · ${CARD_EFFORT_NAMES[config.effort] || config.effort}` : config.model ? `${config.model} · 强度待确认` : "模型待确认";
  const model = button(configText, event => { event.stopPropagation(); openPaperModel(p); }, "card-ai-model", "", { title: `${config.legacyTranslation ? "历史译文模型未记录。" : ""}${config.locked ? "本篇已固定" : "首次 AI 任务时按当前默认配置固定"}：${configText}；点击更改` });
  const chat = button("对话", event => { event.stopPropagation(); act(async () => { await openReader(p.id); await openPaperChat(); }); }, "card-ai-chat", "chat");
  const body = el("div", { class: "card-body" }, el("div", { class: "card-header" }, el("div", { class: "card-journal" }, el("span", { class: "journal-badge", title: p.conference_name || journalNameOf(p) }, journalOf(p)), el("span", { class: "card-year" }, p.year || "年份待补充")), actions), el("h2", { class: "card-title", lang: "en" }, titleOf(p)));
  const content = el("div", { class: "card-content" }, el("div", { class: "card-cover" }, cover), el("div", { class: "card-controls" }, translation, details, model, chat));
  const activate = () => state.selectionMode ? togglePaperSelection(p.id) : act(() => openReader(p.id));
  let dragStarted = false;
  return el("article", { class: `paper-card paper-${rank.type} tier-${rank.tier} ${state.selectionMode && state.selectedPapers.has(p.id) ? "is-selected" : ""}`, dataset: { paperId: p.id }, draggable: "true", tabindex: "0", role: "group", "aria-label": `阅读论文：${titleOf(p)}`, ondragstart: event => {
    if (event.target.closest("button, a")) { event.preventDefault(); return; }
    draggingPaperIds = state.selectionMode && state.selectedPapers.has(p.id) ? [...state.selectedPapers] : [p.id];
    dragStarted = true; event.currentTarget.classList.add("is-dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("application/x-ezread-papers", JSON.stringify(draggingPaperIds));
    startPaperDragPreview(event, p);
  }, ondrag: positionPaperDragPreview, ondragend: () => { endPaperDrag(); setTimeout(() => { dragStarted = false; }, 0); },
  oncontextmenu: event => { event.preventDefault(); event.stopPropagation(); openCardMenu(p, event.clientX, event.clientY); },
  onclick: event => { if (!dragStarted && !event.target.closest("button, a")) activate(); }, onkeydown: event => {
    if (event.target !== event.currentTarget) return;
    if (event.key === "ContextMenu" || event.shiftKey && event.key === "F10") { event.preventDefault(); const rect = event.currentTarget.getBoundingClientRect(); openCardMenu(p, rect.left + 24, rect.top + 24); }
    else if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activate(); }
  } }, body, content);
}
function togglePaperSelection(id) { if (state.selectedPapers.has(id)) state.selectedPapers.delete(id); else state.selectedPapers.add(id); renderLibrary(); }
function renderBulkbar(papers) {
  const bar = $("#bulkbar"); bar.classList.toggle("hidden", !state.selectionMode);
  if (!state.selectionMode) return;
  const validIds = new Set(state.papers.map(p => p.id)); for (const id of state.selectedPapers) if (!validIds.has(id)) state.selectedPapers.delete(id);
  bar.replaceChildren(button("取消", cancelPaperSelection, "secondary", "close", { class: "button secondary bulkbar-cancel", title: "退出多选并清空选择（Esc）" }),
    el("div", { class: "bulkbar-actions" },
      el("span", { class: "bulkbar-count", role: "status" }, `已选 ${state.selectedPapers.size} 篇`),
      button("全选", () => { papers.forEach(p => state.selectedPapers.add(p.id)); renderLibrary(); }, "secondary", "", { title: "选中当前筛选结果中的全部论文" }),
      button("清空选择", () => { state.selectedPapers.clear(); renderLibrary(); }, "secondary"),
      button("移入合集", () => openMovePapers([...state.selectedPapers]), "secondary", "folder", { disabled: !state.selectedPapers.size }),
      button("全部翻译", () => openTranslationSetup(state.papers.filter(p => state.selectedPapers.has(p.id))), "primary", "translate", { disabled: !state.selectedPapers.size }),
      button("移入回收站", event => { const ids = [...state.selectedPapers]; if (ids.length && confirm(`将选中的 ${ids.length} 篇论文移入回收站？可在设置 → 文献与备份中恢复。`)) void act(() => deletePapers(ids), event.currentTarget); }, "danger", "trash", { disabled: !state.selectedPapers.size })));
}
function renderLegend() {
  $("#if-legend").replaceChildren(...["top", "important", "other", "conference", "preprint"].map(tier => el("span", { class: `legend-tile legend-${tier}` }, TIER_NAMES[tier])));
}
function clearFilters() { closeFilterMenu(); state.query = ""; $("#search").value = ""; $$(".filter-group select:not(#sort)").forEach(s => { s.value = ""; }); renderLibrary(); }
