// Composition: polling, event wiring and application startup.
function paperSignature(p) {
  const t = translationOf(p); return JSON.stringify([p.updated_at, t.status, t.done, t.total, t.error, p.summarize_status, p.team_status, p.summary, p.team, p.structure_status, p.structure_error, p.collection, p.collection_assignment]);
}
async function poll() {
  if (state.pollBusy || state.importing || document.hidden) return;
  state.pollBusy = true;
  try {
    const before = new Map(state.papers.map(p => [p.id, paperSignature(p)]));
    const data = await api("/api/papers"); const changed = (data.papers || []).some(p => before.get(p.id) !== paperSignature(p)) || (data.papers || []).length !== state.papers.length;
    state.papers = data.papers || []; state.collections = list(data.collections).map(c => typeof c === "object" ? c.name : c).filter(Boolean);
    if (changed) { renderLibrary(); if ($("#queue-dialog").open) renderQueue(); }
    const detailId = state.detail?.id, readerId = state.reader?.id;
    const focusedInput = document.activeElement && ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
    const readerSummary = state.papers.find(p => p.id === readerId), detailSummary = state.papers.find(p => p.id === detailId);
    if ($("#reader-dialog").open && readerSummary) {
      state.reader.translation = readerSummary.translation; updateReaderProgress();
      if (state.readerRenderedSignature !== paperSignature(readerSummary)) await refreshReader();
    }
    if ($("#detail-dialog").open && detailSummary && !$("#reader-dialog").open && !focusedInput && !$("#edit-dialog").open && !$("#crop-dialog").open && state.detailRenderedSignature !== paperSignature(detailSummary)) {
      const scrollTop = $("#detail-dialog").scrollTop;
      const p = normalizeDetail(await api(`/api/papers/${encodeURIComponent(detailId)}`));
      if (state.detail?.id === detailId && $("#detail-dialog").open) { state.detail = p; renderDetail(); $("#detail-dialog").scrollTop = scrollTop; }
    }
  } catch { /* Keep saved views usable during a temporary local-server interruption. */ }
  finally { state.pollBusy = false; }
}
function installEvents() {
  $$(".filter-trigger[data-select]").forEach(trigger => trigger.addEventListener("click", () => openFilterMenu(trigger)));
  window.addEventListener("resize", positionFilterMenu);
  window.addEventListener("scroll", positionFilterMenu, true);
  $("#filter-backdrop").addEventListener("click", () => closeFilterMenu(true));
  $("#filter-menu-close").addEventListener("click", () => closeFilterMenu(true));
  document.addEventListener("keydown", event => {
    if (!activeFilterTrigger) return;
    if (event.key === "Escape") { event.preventDefault(); closeFilterMenu(true); }
    if (!["ArrowDown", "ArrowUp"].includes(event.key) || !$("#filter-menu-options").contains(document.activeElement)) return;
    const options = $$(".filter-menu-option");
    const index = options.indexOf(document.activeElement);
    if (index < 0) return;
    event.preventDefault();
    options[(index + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length].focus();
  });
  $("#search").addEventListener("focus", () => closeFilterMenu());
  $("#import-top").addEventListener("click", () => { closeFilterMenu(); $("#file-input").click(); });
  $("#file-input").addEventListener("change", event => act(() => importFiles(event.target.files)));
  $$(".nav-item[data-view]").forEach(node => node.addEventListener("click", () => { if (node.dataset.view === "all") clearFilters(); toggleView(node.dataset.view); }));
  $$("[data-filter-type]").forEach(node => node.addEventListener("click", () => {
    $("#filter-type").value = node.dataset.filterType;
    if (node.dataset.filterType !== "journal") { $("#filter-journal").value = ""; $("#filter-tier").value = ""; }
    renderLibrary();
  }));
  $("#search").addEventListener("input", event => { state.query = event.target.value; clearTimeout(searchTimer); searchTimer = setTimeout(renderLibrary, 120); });
  ["#filter-journal", "#filter-tier"].forEach(selector => $(selector).addEventListener("change", () => {
    if ($(selector).value) $("#filter-type").value = "journal";
    renderLibrary();
  }));
  $$("[data-sort-key]").forEach(node => node.addEventListener("click", () => {
    $("#sort").value = node.dataset.sortKey; $("#sort-direction").value = node.dataset.sortDirection;
    try { localStorage.setItem("readx-sort", $("#sort").value); localStorage.setItem("ezread-sort-direction", $("#sort-direction").value); } catch {}
    renderLibrary();
  }));
  $("#clear-filters").addEventListener("click", clearFilters);
  $("#new-collection").addEventListener("click", () => { openDialog("#collection-dialog"); $("#collection-name").focus(); });
  $("#collection-form").addEventListener("submit", event => { event.preventDefault(); act(() => createCollection($("#collection-name").value), $("#collection-form button[type=submit]")); });
  $("#settings-button").addEventListener("click", () => act(openSettings));
  document.addEventListener("pointerdown", event => { if (activeCardMenuId && !$("#card-context-menu").contains(event.target)) closeCardMenu(); });
  window.addEventListener("resize", () => closeCardMenu());
  document.addEventListener("wheel", () => closeCardMenu(), { passive: true, capture: true });
  $("#card-context-menu").addEventListener("keydown", event => {
    const items = $$("[role=menuitem]", event.currentTarget), index = items.indexOf(document.activeElement);
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault(); items[event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length].focus();
    }
    if (event.key === "Tab") closeCardMenu();
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape" || event.defaultPrevented) return;
    if (activeCardMenuId) { event.preventDefault(); event.stopPropagation(); closeCardMenu(true); }
    else if (state.selectionMode && !$("dialog[open]") && !activeFilterTrigger && !draggingPaperIds.length) { event.preventDefault(); cancelPaperSelection(); }
  });
  $("#refresh-status").addEventListener("click", event => act(async () => { await refreshStatus(); await loadLibrary(); toast("已更新连接与任务状态"); }, event.currentTarget));
  $$('[data-close]').forEach(node => node.addEventListener("click", () => closeDialog(`#${node.dataset.close}`)));
  $("#queue-close").addEventListener("click", () => closeDialog("#queue-dialog"));
  $("#queue-dialog").addEventListener("close", () => { renderLibrary(); requestAnimationFrame(() => { if (!$("#queue-dialog").open) window.scrollTo({ top: queueLibraryScroll }); }); });
  document.addEventListener("keydown", event => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); if ($("#queue-dialog").open) closeDialog("#queue-dialog"); $("#search").focus(); $("#search").select(); } });
  $("#detail-dialog").addEventListener("cancel", event => { event.preventDefault(); closeDialog("#detail-dialog"); });
  $("#reader-dialog").addEventListener("cancel", event => { event.preventDefault(); void closeReader(); });
  let dragDepth = 0;
  document.addEventListener("dragenter", event => { if (![...(event.dataTransfer?.types || [])].includes("Files")) return; event.preventDefault(); dragDepth++; $("#drop-overlay").classList.remove("hidden"); });
  document.addEventListener("dragover", event => { if ([...(event.dataTransfer?.types || [])].includes("Files")) { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; } });
  document.addEventListener("dragleave", event => { if (![...(event.dataTransfer?.types || [])].includes("Files")) return; dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) $("#drop-overlay").classList.add("hidden"); });
  document.addEventListener("drop", event => { if (!event.dataTransfer?.files.length) return; event.preventDefault(); dragDepth = 0; $("#drop-overlay").classList.add("hidden"); act(() => importFiles(event.dataTransfer.files)); });
  document.addEventListener("dragover", positionPaperDragPreview);
  document.addEventListener("dragend", endPaperDrag);
  window.addEventListener("blur", () => { dragDepth = 0; $("#drop-overlay").classList.add("hidden"); closeCardMenu(); endPaperDrag(); });
  document.addEventListener("visibilitychange", () => { document.documentElement.dataset.pageHidden = String(document.hidden); if (!document.hidden) poll(); });
}
async function init() {
  try { const cached = JSON.parse(localStorage.getItem("readx-preferences") || "null"); if (cached && typeof cached === "object") for (const key of Object.keys(DEFAULT_PREFERENCES)) if (Object.hasOwn(cached, key)) state.settings[key] = cached[key]; } catch {}
  applyPreferences(); installEvents(); try { const sort = localStorage.getItem("readx-sort") || localStorage.getItem("tudu-sort"); if (["recent", "imported", "year"].includes(sort)) $("#sort").value = sort; const direction = localStorage.getItem("ezread-sort-direction"); if (["asc", "desc"].includes(direction)) $("#sort-direction").value = direction; } catch {}
  renderLibrary();
  api("/api/status").then(value => { state.status = value; if (value.usage) state.usage = value.usage; if ($("#queue-dialog").open) renderQueue(); $("#settings-codex-status")?.replaceChildren(codexStatusPanel()); renderUsagePanels(); }).catch(error => { state.status = { ...(state.status || {}), codex: { available: null, authenticated: null, message: `连接状态查询失败：${error.message}` } }; if ($("#queue-dialog").open) renderQueue(); $("#settings-codex-status")?.replaceChildren(codexStatusPanel()); });
  loadUsage(false);
  const results = await Promise.allSettled([api("/api/settings"), api("/api/papers")]);
  if (results[0].status === "fulfilled") state.settings = { ...state.settings, ...results[0].value };
  applyPreferences();
  if (results[1].status === "fulfilled") { state.papers = results[1].value.papers || []; state.collections = list(results[1].value.collections); state.loading = false; renderLibrary(); }
  else {
    state.loading = false; $("#result-count").textContent = "连接未完成"; $("#paper-grid").replaceChildren(el("div", { class: "no-results", style: { columnSpan: "all" } }, el("h3", {}, "暂时无法打开本地文献库"), el("p", { class: "small" }, results[1].reason.message), button("重新连接", () => act(() => loadLibrary(true)), "primary", "", { style: { marginTop: "17px" } }))); toast("请确认 EzRead 本地服务正在运行", "error", 7000);
  }
  void loadModels().then(renderLibrary).catch(() => {});
  pollTimer = setInterval(poll, 6000);
  usagePollTimer = setInterval(() => { if (!document.hidden && ($("#queue-dialog").open || $("#settings-dialog").open)) loadUsage(false); }, 60000);
}
init().catch(error => toast(error.message, "error", 9000));
