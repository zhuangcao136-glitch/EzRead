"use strict";

// This script is loaded before app.js. Shared app state is only read inside functions.
const readerRuntime = {
  imageObserver: null, thumbObserver: null, resizeObserver: null,
  leftFrame: 0, rightFrame: 0, suppressLeftUntil: 0, suppressRightUntil: 0,
  opening: 0, request: 0, mountedId: null, boundDialog: null,
  originalSignature: "", blockSignatures: new Map(), structureSignature: "", entriesBySource: new Map(), resizeAnchor: null, translationAnchor: null,
  thumbsOpen: false, mode: "parallel", split: .5, position: null,
  savedSignature: "", savePromise: null, closePromise: null,
  notes: null, editor: null, libraryAnchor: null, lifecycleBound: false,
  layoutFrame: 0, restoring: false, splitSaveTimer: null,
  textLanguage: "auto"
};

const READER_ZOOMS = [75, 100, 125, 150, 200];
const READER_MODES = ["parallel", "original", "translation"];
function readerClamp(value, low, high, fallback = low) {
  const number = Number(value); return Number.isFinite(number) ? Math.max(low, Math.min(high, number)) : fallback;
}
function readerNormalizePosition(value, paper = state.reader) {
  const v = value && typeof value === "object" ? value : {};
  const zoom = Number(v.zoom), block = paper?.blocks.find(b => b.id === v.translation_block_id);
  return { version: 1, page: readerPageNumber(v.page ?? paper?.read_page, paper?.pages.length || 1),
    original_offset: readerClamp(v.original_offset, 0, 1), translation_block_id: block?.id || null,
    translation_offset: readerClamp(v.translation_offset, 0, 1),
    zoom: READER_ZOOMS.includes(zoom) ? zoom : "fit",
    split_ratio: readerClamp(v.split_ratio ?? state.settings.reader_split_ratio, .3, .7, .5),
    mode: READER_MODES.includes(v.mode) ? v.mode : "parallel" };
}
function readerStorageKey(kind, id) { return `ezread-reader-${kind}:${id}`; }
function readerReadLocal(kind, id) {
  try { return JSON.parse(localStorage.getItem(readerStorageKey(kind, id)) || "null"); } catch { return null; }
}
function readerWriteLocal(kind, id, value) {
  try { localStorage.setItem(readerStorageKey(kind, id), JSON.stringify(value)); return true; } catch { return false; }
}
function readerRemoveLocal(kind, id) {
  try { localStorage.removeItem(readerStorageKey(kind, id)); } catch {}
}
function readerSaveHint(message) { const node = $("#reader-save-state"); if (node) node.textContent = message; }
function readerCapturePosition() {
  if (!state.reader) return null;
  const previous = readerRuntime.position || readerNormalizePosition(state.reader.reader_state);
  const left = readerRuntime.mode !== "translation" ? readerCaptureAnchor($("#original-scroll"), ".reader-page-section") : null;
  const right = readerRuntime.mode !== "original" ? readerCaptureAnchor($("#translation-scroll"), ".translation-block") : null;
  const block = right?.key && state.reader.blocks.find(b => b.id === right.key);
  const page = left?.key ? Number(left.key) : block?.page || previous.page;
  const position = readerNormalizePosition({ ...previous, page,
    original_offset: left?.key ? left.ratio : page === previous.page ? previous.original_offset : 0,
    translation_block_id: right?.key || previous.translation_block_id,
    translation_offset: right?.key ? right.ratio : previous.translation_offset,
    zoom: state.zoom, split_ratio: readerRuntime.split, mode: readerRuntime.mode });
  readerRuntime.position = position;
  return position;
}
function readerCachePosition(position = readerCapturePosition(), pending = true) {
  return Boolean(position && state.reader && readerWriteLocal("position", state.reader.id, { position, pending }));
}
function readerQueuePositionSave() {
  if (readerRuntime.restoring || !readerScrollActive()) return;
  readerCachePosition(); clearTimeout(readSaveTimer); readSaveTimer = setTimeout(() => { saveReadPage().catch(error => readerSaveHint(`位置未保存：${error.message}`)); }, 650);
}
function readerRestorePosition(position) {
  const p = readerNormalizePosition(position); readerRuntime.restoring = true;
  const original = $("#original-scroll"), translated = $("#translation-scroll");
  const page = readerOriginalPage(p.page); readerLoadImage(page && $("img", page));
  if (readerRuntime.mode !== "translation") readerRestoreAnchor(original, ".reader-page-section", { key: String(p.page), ratio: p.original_offset, offset: 0, scrollTop: 0, scrollLeft: 0 });
  if (readerRuntime.mode !== "original") {
    if (p.translation_block_id) readerRestoreAnchor(translated, ".translation-block", { key: p.translation_block_id, ratio: p.translation_offset, offset: 0, scrollTop: 0, scrollLeft: 0 });
    else scrollTranslationToPage(p.page);
  }
  state.readerPage = p.page; readerUpdatePageUI(); readerRuntime.position = p;
  readerRuntime.resizeAnchor = readerCaptureAnchor(original, ".reader-page-section");
  readerRuntime.translationAnchor = readerCaptureAnchor(translated, ".translation-block");
  readerRuntime.restoring = false;
}
function readerRememberLibrary() {
  const cards = $$(".paper-card");
  const card = cards.find(node => node.getBoundingClientRect().bottom > 0);
  return { y: window.scrollY, x: window.scrollX, cardId: card?.dataset.id || card?.dataset.paperId || null, top: card?.getBoundingClientRect().top, focus: document.activeElement };
}
function readerRestoreLibrary() {
  const anchor = readerRuntime.libraryAnchor; if (!anchor) return;
  requestAnimationFrame(() => {
    const card = anchor.cardId && $$(".paper-card").find(node => (node.dataset.id || node.dataset.paperId) === anchor.cardId);
    window.scrollTo({ left: anchor.x, top: card ? window.scrollY + card.getBoundingClientRect().top - anchor.top : anchor.y, behavior: "instant" });
    if (anchor.focus?.isConnected) anchor.focus.focus({ preventScroll: true });
    else card?.focus({ preventScroll: true });
  });
}

function readerPageNumber(value, count) {
  return Math.max(1, Math.min(Math.max(1, count), Math.round(Number(value) || 1)));
}
function readerSyncEnabled() { return state.settings.reader_sync !== false; }
function readerIsEditing() { return Boolean(readerRuntime.editor || document.activeElement?.closest(".reader-notes, .block-editor") || typeof readerTextProtected === "function" && readerTextProtected()); }
function readerScrollActive() { return Boolean(state.reader && $("#reader-dialog")?.open); }
function readerAtPage(container, nodes) {
  if (!nodes.length) return null;
  const line = container.getBoundingClientRect().top + Math.min(100, container.clientHeight * 0.2);
  // Use a fixed reading line; a tall page must not lose to a short adjacent page.
  let chosen = nodes[0];
  for (const node of nodes) {
    if (node.getBoundingClientRect().top <= line) chosen = node;
    else break;
  }
  return chosen;
}
function readerCaptureAnchor(container, selector) {
  if (!container) return null;
  const nodes = $$(selector, container), top = container.getBoundingClientRect().top;
  const node = nodes.find(n => n.getBoundingClientRect().bottom > top + 1) || nodes.at(-1);
  if (!node) return { scrollTop: container.scrollTop, scrollLeft: container.scrollLeft };
  const rect = node.getBoundingClientRect();
  return { key: node.dataset.blockId || node.dataset.page, ratio: Math.max(0, Math.min(1, (top - rect.top) / Math.max(1, rect.height))), offset: Math.max(0, rect.top - top), scrollTop: container.scrollTop, scrollLeft: container.scrollLeft };
}
function readerRestoreAnchor(container, selector, anchor) {
  if (!container || !anchor) return;
  const node = $$(selector, container).find(n => (n.dataset.blockId || n.dataset.page) === anchor.key || n.dataset.sourceIds?.split(" ").includes(anchor.key));
  let top = anchor.scrollTop;
  if (node) {
    const rect = node.getBoundingClientRect();
    top = container.scrollTop + rect.top - container.getBoundingClientRect().top + rect.height * anchor.ratio - anchor.offset;
  }
  readerMoveScroll(container, top, anchor.scrollLeft);
}
function readerMoveScroll(container, top, left) {
  if (!container) return;
  if (left === undefined) left = container.scrollLeft;
  const side = container.id === "original-scroll" ? "suppressLeftUntil" : "suppressRightUntil";
  readerRuntime[side] = performance.now() + 220;
  container.scrollTo({ top: Math.max(0, top || 0), left: Math.max(0, left || 0), behavior: "instant" });
  if (container.id === "original-scroll") readerRuntime.resizeAnchor = readerCaptureAnchor(container, ".reader-page-section");
  else if (container.id === "translation-scroll") readerRuntime.translationAnchor = readerCaptureAnchor(container, ".translation-block");
}
function readerScrollToNode(container, node, align = "start") {
  if (!container || !node) return;
  const r = node.getBoundingClientRect(), pane = container.getBoundingClientRect();
  const offset = align === "center" ? Math.max(16, (container.clientHeight - Math.min(r.height, container.clientHeight * 0.7)) / 2) : 18;
  let left = container.scrollLeft;
  if (r.left < pane.left + 12) left += r.left - pane.left - 12;
  else if (r.right > pane.right - 12 && r.width < container.clientWidth) left += r.right - pane.right + 12;
  readerMoveScroll(container, container.scrollTop + r.top - pane.top - offset, left);
}
function readerOriginalPage(page) { return $$(".reader-page-section", $("#original-scroll")).find(n => Number(n.dataset.page) === Number(page)); }
function readerTranslationPage(page) {
  const root = $("#translation-scroll");
  return $$(".translation-block", root).find(n => n.dataset.sourcePages?.split(" ").includes(String(page)) || Number(n.dataset.page) === Number(page)) ||
    $$(".translation-page", root).find(n => Number(n.dataset.page) === Number(page));
}

async function openReader(id) {
  if (readerScrollActive() && !(await closeReader())) return;
  if (typeof flushDetailNotes === "function") await flushDetailNotes(id);
  const opening = ++readerRuntime.opening;
  const p = normalizeDetail(await api(`/api/papers/${encodeURIComponent(id)}`));
  if (opening !== readerRuntime.opening) return;
  readerRuntime.libraryAnchor = readerRememberLibrary();
  const detail = $("#detail-dialog"); if (detail?.open) detail.close();
  state.reader = p;
  const cached = readerReadLocal("position", id);
  const position = readerNormalizePosition(cached?.pending ? cached.position : p.reader_state || cached?.position, p);
  readerRuntime.position = position; readerRuntime.mode = position.mode; readerRuntime.split = position.split_ratio;
  state.readerPage = position.page; state.selectedBlock = null; state.zoom = position.zoom;
  readerRuntime.thumbsOpen = false; readerRuntime.savedSignature = ""; readerRuntime.editor = null;
  readerRuntime.textLanguage = readerReadLocal("language", id)?.value === "original" ? "original" : "auto";
  if (typeof readerTextUI !== "undefined") { readerTextUI.editor = null; readerCloseTextPopup(true); }
  const notesDraft = readerReadLocal("notes", id);
  readerRuntime.notes = { id, value: typeof notesDraft?.value === "string" ? notesDraft.value : p.notes || "", savedValue: p.notes || "", timer: null, saving: null, storageOk: true };
  renderReader(); openDialog("#reader-dialog");
  requestAnimationFrame(() => {
    if (state.reader?.id !== id || !readerScrollActive()) return;
    readerRestorePosition(position);
    const blockDraft = readerReadLocal("block", id);
    if (blockDraft && ["note", "translation"].includes(blockDraft.mode) && p.blocks.some(b => b.id === blockDraft.blockId)) {
      if (readerRuntime.mode === "original") readerSetMode("parallel");
      state.selectedBlock = blockDraft.blockId; refreshBlock(blockDraft.blockId); openBlockEditor(blockDraft.blockId, blockDraft.mode);
      toast("已恢复上次未保存的段落草稿", "success");
    }
    if (notesDraft?.value !== undefined && notesDraft.value !== p.notes) toggleReaderNotes(true);
    readerQueuePositionSave();
  });
}

function readerDispose() {
  readerRuntime.imageObserver?.disconnect(); readerRuntime.thumbObserver?.disconnect(); readerRuntime.resizeObserver?.disconnect();
  readerRuntime.imageObserver = null; readerRuntime.thumbObserver = null; readerRuntime.resizeObserver = null;
  cancelAnimationFrame(readerRuntime.leftFrame); cancelAnimationFrame(readerRuntime.rightFrame);
  readerRuntime.leftFrame = 0; readerRuntime.rightFrame = 0;
  cancelAnimationFrame(readerRuntime.layoutFrame); clearTimeout(readSaveTimer);
}
function renderReader() {
  const p = state.reader; if (!p) return;
  readerDispose(); readerRuntime.mountedId = p.id; readerRuntime.originalSignature = "";
  readerRuntime.blockSignatures.clear();
  readerRuntime.structureSignature = ""; readerRuntime.entriesBySource.clear();
  const pageInput = el("input", { type: "number", min: "1", max: p.pages.length || 1, value: state.readerPage, id: "reader-page-input", "aria-label": "当前页码", onchange: event => setReaderPage(Number(event.target.value), true) });
  const zoom = el("select", { class: "zoom-select", id: "reader-zoom", "aria-label": "原文页面缩放", onchange: event => readerSetZoom(event.target.value) }, el("option", { value: "fit" }, "适合宽度"), READER_ZOOMS.map(value => el("option", { value: String(value) }, `${value}%`))); zoom.value = state.zoom;
  const original = el("section", { class: "original-pane", "aria-label": "原版 PDF 连续阅读" },
    el("div", { class: "pane-bar" }, el("span", { class: "pane-label" }, icon("file"), "原版 PDF"), zoom,
      el("div", { class: "page-control" }, iconButton("chevron", "上一页", () => setReaderPage(state.readerPage - 1, true)), pageInput, ` / ${p.pages.length}`, iconButton("chevron", "下一页", () => setReaderPage(state.readerPage + 1, true))),
      el("button", { class: "reader-thumbs-toggle", id: "reader-thumbs-toggle", "aria-expanded": "false", "aria-controls": "reader-thumbs", onclick: toggleReaderThumbnails }, "缩略图")),
    el("div", { class: "original-scroll", id: "original-scroll", tabindex: "0", "aria-label": "连续 PDF 页面，可上下滚动" }),
    el("div", { class: "reader-thumbs", id: "reader-thumbs", hidden: true }));
  $(".page-control .icon-button", original).style.transform = "rotate(180deg)";
  const sync = el("input", { type: "checkbox", id: "reader-sync", checked: readerSyncEnabled(), onchange: event => act(async () => {
    const previous = readerSyncEnabled(), next = event.target.checked;
    state.settings.reader_sync = next;
    try { const data = await api("/api/settings", { method: "PATCH", body: { reader_sync: next } }); state.settings = { ...state.settings, ...(data.settings || data) }; }
    catch (error) { state.settings.reader_sync = previous; event.target.checked = previous; throw error; }
    if (typeof syncPreferenceControls === "function") syncPreferenceControls();
    if (next && !readerIsEditing()) scrollTranslationToPage(state.readerPage);
  }, event.target) });
  const translationLabel = readerRuntime.textLanguage === "original" || translationOf(p).done === 0 ? "英文原文" :
    translationOf(p).done < translationOf(p).total ? "译文与原文" : "中文译文";
  const trans = el("section", { class: "translation-pane", "aria-label": "论文文字与笔记" },
    el("div", { class: "pane-bar" }, el("span", { class: "pane-label", id: "reader-text-pane-label" }, icon("translate"), translationLabel),
      el("label", { class: "reader-sync-control", title: "滚动时按页联动，点击段落可精确定位" }, sync, "同步滚动"),
      el("span", { class: "selection-status", id: "reader-translation-status" }, `第 ${state.readerPage} 页`)),
    el("div", { class: "translation-scroll", id: "translation-scroll", tabindex: "0", "aria-label": "译文或英文原文，可自由选择与复制" }));
  const tools = el("div", { class: "reader-tools" },
    button("论文简介", () => act(async () => { const id = state.reader.id; if (await closeReader()) await openDetail(id); }), "secondary", "file", { title: "论文信息、作者与来源" }),
    button("论文对话", () => togglePaperChat(), "secondary", "", { id: "reader-chat-toggle", "aria-expanded": "false", "aria-controls": "reader-paper-chat" }),
    button("笔记", () => toggleReaderNotes(), "secondary", "edit", { id: "reader-notes-toggle", "aria-expanded": "false", "aria-controls": "reader-notes" }),
    button("全文翻译", event => act(() => translateAction(state.reader, ACTIVE_STATUSES.has(translationOf(state.reader).status)), event.currentTarget), "primary", "translate", { id: "reader-translate-button" }));
  const originalLink = safeUrl(p.original_url || `/media/${p.id}/original.pdf`);
  const modes = el("div", { class: "reader-mode-switch", role: "group", "aria-label": "阅读视图" }, [["parallel", "中英对照"], ["original", "只看原文"], ["translation", "只看译文"]].map(([mode, label]) => button(label, () => readerSetMode(mode), "", "", { "data-reader-mode": mode, "aria-pressed": String(readerRuntime.mode === mode) })));
  const divider = el("div", { class: "reader-divider", id: "reader-divider", role: "separator", tabindex: "0", "aria-label": "调整原文与译文比例", "aria-orientation": "vertical", "aria-valuemin": "30", "aria-valuemax": "70", "aria-controls": "original-scroll translation-scroll", title: "拖动调整比例；方向键微调，Enter 恢复均分" }, el("span", { "aria-hidden": "true" }));
  $("#reader-content").replaceChildren(
    el("div", { class: "reader-topbar" }, el("button", { class: "reader-back", onclick: () => void closeReader(), title: "返回文献库" }, icon("chevron"), "文献库"), el("div", { class: "reader-title", title: titleOf(p) }, titleOf(p)), tools),
    el("div", { class: "reader-viewbar" }, modes,
      el("div", { class: "reader-text-view-switch", role: "group", "aria-label": "右侧文字语言" },
        button("英文原文", () => readerSetTextLanguage("original"), "secondary", "", { "data-text-language": "original", "aria-pressed": String(readerRuntime.textLanguage === "original") }),
        button("译文", () => readerSetTextLanguage("auto"), "secondary", "", { "data-text-language": "auto", "aria-pressed": String(readerRuntime.textLanguage !== "original") })),
      button("修订历史", () => readerTextList("history"), "secondary"),
      button("待核对标记", () => readerTextList("annotations"), "secondary", "", { id: "reader-review-annotations", hidden: true }),
      el("span", { id: "reader-selection-mode-hint", class: "reader-selection-mode-hint" }, "选中文字即可显示操作选项")),
    el("div", { class: "reader-workspace" }, el("div", { class: "reader-layout", id: "reader-layout" }, original, divider, trans), el("aside", { class: "reader-notes", id: "reader-notes", hidden: true, "aria-label": "论文笔记" }), el("aside", { class: "reader-paper-chat", id: "reader-paper-chat", hidden: true, "aria-label": "本篇论文对话" })),
    el("div", { class: "reader-bottom" }, el("span", { id: "reader-save-state", role: "status", "aria-live": "polite" }, "阅读位置自动保存"), el("a", { href: originalLink, target: "_blank", rel: "noopener noreferrer" }, "打开原 PDF ↗")));
  // Layout must be visible before computing page widths and scroll anchors.
  openDialog("#reader-dialog");
  readerApplyLayout(); drawOriginalPage(); renderTranslations(); updateReaderProgress(); readerBindScrolling(); readerBindDivider(divider); readerBindLifecycle();
  if (typeof readerBindTextSelection === "function") readerBindTextSelection();
  const dialog = $("#reader-dialog");
  if (readerRuntime.boundDialog !== dialog) {
    dialog.addEventListener("close", () => { readerPersistDrafts(); readerDispose(); readerRestoreLibrary(); }); readerRuntime.boundDialog = dialog;
  }
}
async function closeReader() {
  if (readerRuntime.closePromise) return readerRuntime.closePromise;
  if (!readerScrollActive()) return true;
  readerCachePosition(); readerPersistDrafts();
  const content = $("#reader-content"); content.inert = true;
  readerRuntime.closePromise = (async () => {
    const results = await Promise.allSettled([readerSaveNotes(), readerSaveEditor(), typeof readerFlushTextEditor === "function" ? readerFlushTextEditor() : Promise.resolve()]);
    results.push(...await Promise.allSettled([saveReadPage()]));
    const failed = results.some(result => result.status === "rejected");
    const preserved = readerPersistDrafts();
    if (failed && !preserved) { toast("保存失败，草稿也未能写入本机；请保留此窗口并重试。", "error", 8000); return false; }
    if (failed) toast("未保存内容已保留为本机草稿，请重新打开后核对。", "warn", 6500);
    if (typeof readerReleaseTextUI === "function") readerReleaseTextUI();
    $("#reader-dialog").close(); return true;
  })().finally(() => { content.inert = false; readerRuntime.closePromise = null; });
  return readerRuntime.closePromise;
}

function readerApplyLayout() {
  const layout = $("#reader-layout"); if (!layout) return;
  layout.dataset.mode = readerRuntime.mode;
  layout.style.setProperty("--reader-left", `${readerRuntime.split}fr`);
  layout.style.setProperty("--reader-right", `${1 - readerRuntime.split}fr`);
  $(".original-pane", layout).hidden = readerRuntime.mode === "translation";
  $(".translation-pane", layout).hidden = readerRuntime.mode === "original";
  const divider = $("#reader-divider"); divider.hidden = readerRuntime.mode !== "parallel";
  divider.setAttribute("aria-valuenow", String(Math.round(readerRuntime.split * 100)));
  divider.setAttribute("aria-valuetext", `原文 ${Math.round(readerRuntime.split * 100)}%，译文 ${Math.round((1 - readerRuntime.split) * 100)}%`);
  divider.setAttribute("aria-orientation", typeof matchMedia === "function" && matchMedia("(max-width: 700px)").matches ? "horizontal" : "vertical");
  $$("[data-reader-mode]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.readerMode === readerRuntime.mode)));
  const sync = $(".reader-sync-control"); if (sync) sync.hidden = readerRuntime.mode !== "parallel";
}
function readerPreserveLayout(change) {
  const position = readerCapturePosition();
  const originalAnchor = readerCaptureAnchor($("#original-scroll"), ".reader-page-section");
  const translationAnchor = readerCaptureAnchor($("#translation-scroll"), ".translation-block");
  change(); readerApplyLayout(); cancelAnimationFrame(readerRuntime.layoutFrame);
  readerRuntime.layoutFrame = requestAnimationFrame(() => {
    if (!readerScrollActive()) return;
    drawOriginalPage();
    if (readerRuntime.mode !== "translation") readerRestoreAnchor($("#original-scroll"), ".reader-page-section", originalAnchor);
    if (readerRuntime.mode !== "original") readerRestoreAnchor($("#translation-scroll"), ".translation-block", translationAnchor);
    if (position) readerRuntime.position = { ...position, mode: readerRuntime.mode, split_ratio: readerRuntime.split, zoom: state.zoom };
    readerQueuePositionSave();
  });
}
function readerSetMode(mode) {
  if (!READER_MODES.includes(mode) || mode === readerRuntime.mode) return;
  const position = readerCapturePosition(); readerRuntime.mode = mode; readerApplyLayout();
  cancelAnimationFrame(readerRuntime.layoutFrame);
  readerRuntime.layoutFrame = requestAnimationFrame(() => { if (!readerScrollActive()) return; drawOriginalPage(); readerRestorePosition({ ...position, mode }); readerQueuePositionSave(); });
}
function readerSetSplit(ratio, persist = true) {
  readerPreserveLayout(() => { readerRuntime.split = Math.round(readerClamp(ratio, .3, .7, .5) * 1000) / 1000; });
  if (persist) {
    state.settings.reader_split_ratio = readerRuntime.split;
    clearTimeout(readerRuntime.splitSaveTimer);
    const split = readerRuntime.split;
    readerRuntime.splitSaveTimer = setTimeout(() => {
      api("/api/settings", { method: "PATCH", body: { reader_split_ratio: split } }).catch(() => readerSaveHint("默认分栏暂未同步，当前比例仍可继续使用"));
    }, 500);
  }
}
function readerBindDivider(divider) {
  let dragging = false;
  const move = event => {
    if (!dragging) return;
    const box = $("#reader-layout").getBoundingClientRect();
    const vertical = divider.getAttribute("aria-orientation") === "vertical";
    readerSetSplit(vertical ? (event.clientX - box.left) / box.width : (event.clientY - box.top) / box.height, false);
  };
  const finish = event => {
    if (!dragging) return;
    dragging = false; divider.classList.remove("dragging");
    if (divider.hasPointerCapture?.(event.pointerId)) divider.releasePointerCapture(event.pointerId);
    readerSetSplit(readerRuntime.split);
  };
  divider.addEventListener("pointerdown", event => { if (event.button !== 0) return; event.preventDefault(); dragging = true; divider.setPointerCapture(event.pointerId); divider.classList.add("dragging"); divider.focus({ preventScroll: true }); });
  divider.addEventListener("pointermove", move); divider.addEventListener("pointerup", finish); divider.addEventListener("pointercancel", finish); divider.addEventListener("lostpointercapture", finish);
  divider.addEventListener("keydown", event => {
    const step = event.shiftKey ? .05 : .02;
    const next = { ArrowLeft: readerRuntime.split - step, ArrowUp: readerRuntime.split - step, ArrowRight: readerRuntime.split + step, ArrowDown: readerRuntime.split + step, Home: .3, End: .7, Enter: .5 }[event.key];
    if (next === undefined) return; event.preventDefault(); readerSetSplit(next);
  });
  divider.addEventListener("dblclick", () => readerSetSplit(.5));
}
function readerPersistDrafts() {
  let safe = true;
  if (typeof readerTextDraft === "function") safe = readerTextDraft() && safe;
  const notes = readerRuntime.notes, editor = readerRuntime.editor;
  if (notes && notes.value !== notes.savedValue) safe = readerWriteLocal("notes", notes.id, { value: notes.value }) && safe;
  if (editor && editor.text.value !== editor.savedValue) safe = readerWriteLocal("block", editor.paperId, { blockId: editor.blockId, mode: editor.mode, value: editor.text.value }) && safe;
  if (state.reader && readerScrollActive()) {
    const position = readerCapturePosition();
    safe = readerCachePosition(position, JSON.stringify(position) !== readerRuntime.savedSignature) && safe;
  }
  return safe;
}
function readerBindLifecycle() {
  if (readerRuntime.lifecycleBound) return; readerRuntime.lifecycleBound = true;
  window.addEventListener("beforeunload", event => {
    if (readerScrollActive() && !readerPersistDrafts()) { event.preventDefault(); event.returnValue = ""; }
  });
  window.addEventListener("pagehide", () => { if (readerScrollActive()) readerPersistDrafts(); });
}

function readerLoadImage(image) {
  if (!image || image.getAttribute("src") || !image.dataset.src) return;
  const url = safeUrl(image.dataset.src); if (!url) return;
  image.src = url;
}
function readerMakePageImage(page, thumbnail = false) {
  const image = el("img", { alt: `第 ${page.number} 页${thumbnail ? "缩略图" : "原版 PDF"}`, decoding: "async", dataset: { src: page.image_url }, width: Math.round(Number(page.width) || 595), height: Math.round(Number(page.height) || 842), onload: event => event.target.closest(".paper-page")?.classList.add("page-loaded"), onerror: event => {
    const paper = event.target.closest(".paper-page"); if (!paper) return;
    paper.classList.add("page-failed");
    if (!$(".page-retry", paper)) paper.append(button("页面载入失败，点击重试", () => { paper.classList.remove("page-failed"); event.target.removeAttribute("src"); readerLoadImage(event.target); $(".page-retry", paper)?.remove(); }, "secondary", "", { class: "button secondary page-retry" }));
  } });
  return image;
}
function readerObserveImages() {
  readerRuntime.imageObserver?.disconnect();
  const root = $("#original-scroll"); if (!root) return;
  if (typeof IntersectionObserver !== "function") {
    $$(".reader-page-section", root).filter(n => Math.abs(Number(n.dataset.page) - state.readerPage) <= 1).forEach(n => readerLoadImage($("img", n)));
    return;
  }
  readerRuntime.imageObserver = new IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) { readerLoadImage($("img", entry.target)); readerRuntime.imageObserver?.unobserve(entry.target); }
  }, { root, rootMargin: "80% 0px", threshold: 0 });
  $$(".reader-page-section", root).forEach(n => readerRuntime.imageObserver.observe(n));
}
function readerPageOverlay(block) {
  const box = block.bbox?.map(Number); if (!box || box.length !== 4 || box.some(v => !Number.isFinite(v)) || box[2] <= box[0] || box[3] <= box[1]) return null;
  return el("button", { class: `block-overlay ${block.highlight ? "highlighted" : ""} ${state.selectedBlock === block.id ? "selected" : ""}`, "aria-label": `对照段落：${asText(block.text).slice(0, 90)}`, title: asText(block.text).slice(0, 170), dataset: { blockId: block.id }, style: { left: `${box[0] * 100}%`, top: `${box[1] * 100}%`, width: `${(box[2] - box[0]) * 100}%`, height: `${(box[3] - box[1]) * 100}%` }, onclick: () => selectBlock(block.id, true) });
}
function drawOriginalPage() {
  const p = state.reader, scroll = $("#original-scroll"); if (!p || !scroll) return;
  const anchor = readerCaptureAnchor(scroll, ".reader-page-section");
  const signature = JSON.stringify([p.id, p.pages.map(x => [x.number, x.width, x.height, x.image_url]), p.blocks.map(b => [b.id, b.page, b.bbox])]);
  if (readerRuntime.originalSignature !== signature) {
    const grouped = new Map(); for (const b of p.blocks) { if (!grouped.has(Number(b.page))) grouped.set(Number(b.page), []); grouped.get(Number(b.page)).push(b); }
    scroll.replaceChildren(...p.pages.map(page => {
      const overlays = el("div", { class: "page-overlays" }, (grouped.get(Number(page.number)) || []).map(readerPageOverlay));
      return el("section", { class: "reader-page-section", dataset: { page: page.number }, "aria-label": `原文第 ${page.number} 页` },
        el("div", { class: "paper-page", style: { aspectRatio: `${Number(page.width) || 595}/${Number(page.height) || 842}` } }, readerMakePageImage(page), overlays),
        el("div", { class: "page-caption" }, `第 ${page.number} 页`));
    }));
    if (!p.pages.length) scroll.append(el("p", { class: "reader-error" }, "此 PDF 没有可显示的页面"));
    readerRuntime.originalSignature = signature; readerObserveImages();
  }
  const scale = state.zoom === "fit" ? 1 : Math.max(0.5, Math.min(3, Number(state.zoom) / 100 || 1));
  scroll.style.setProperty("--pdf-page-width", `${Math.max(220, scroll.clientWidth - 40) * scale}px`);
  const highlights = new Set(p.blocks.filter(b => b.highlight).map(b => b.id));
  $$(".block-overlay", scroll).forEach(n => { n.classList.toggle("highlighted", highlights.has(n.dataset.blockId)); n.classList.toggle("selected", n.dataset.blockId === state.selectedBlock); });
  readerRestoreAnchor(scroll, ".reader-page-section", anchor);
  readerRuntime.resizeAnchor = readerCaptureAnchor(scroll, ".reader-page-section");
}
function toggleReaderThumbnails() {
  readerRuntime.thumbsOpen = !readerRuntime.thumbsOpen;
  $("#reader-thumbs").hidden = !readerRuntime.thumbsOpen;
  $("#reader-thumbs-toggle").setAttribute("aria-expanded", String(readerRuntime.thumbsOpen));
  if (readerRuntime.thumbsOpen) renderThumbnails(); else readerRuntime.thumbObserver?.disconnect();
}
function renderThumbnails() {
  const p = state.reader, root = $("#reader-thumbs"); if (!p || !root || root.hidden) return;
  readerRuntime.thumbObserver?.disconnect();
  root.replaceChildren(...p.pages.map(page => el("button", { class: `page-thumb ${Number(page.number) === state.readerPage ? "active" : ""}`, title: `第 ${page.number} 页`, "aria-label": `跳转第 ${page.number} 页`, dataset: { page: page.number }, onclick: () => setReaderPage(Number(page.number), true) }, readerMakePageImage(page, true), el("span", {}, page.number))));
  if (typeof IntersectionObserver === "function") {
    readerRuntime.thumbObserver = new IntersectionObserver(entries => { for (const entry of entries) if (entry.isIntersecting) { readerLoadImage($("img", entry.target)); readerRuntime.thumbObserver?.unobserve(entry.target); } }, { root, rootMargin: "0px 40px", threshold: 0 });
    $$(".page-thumb", root).forEach(n => readerRuntime.thumbObserver.observe(n));
  } else $$(".page-thumb", root).slice(0, 10).forEach(n => readerLoadImage($("img", n)));
  readerUpdatePageUI();
}
function readerUpdatePageUI() {
  const input = $("#reader-page-input"); if (input && input !== document.activeElement) input.value = state.readerPage;
  $$(".page-thumb", $("#reader-thumbs")).forEach(n => { n.classList.toggle("active", Number(n.dataset.page) === state.readerPage); n.setAttribute("aria-current", Number(n.dataset.page) === state.readerPage ? "page" : "false"); });
  const thumb = $(".page-thumb.active", $("#reader-thumbs")), row = $("#reader-thumbs");
  if (thumb && row && !row.hidden) {
    const left = thumb.offsetLeft - row.offsetLeft;
    if (left < row.scrollLeft) row.scrollLeft = left;
    else if (left + thumb.offsetWidth > row.scrollLeft + row.clientWidth) row.scrollLeft = left + thumb.offsetWidth - row.clientWidth;
  }
  if ($("#reader-translation-status")) $("#reader-translation-status").textContent = `第 ${state.readerPage} 页`;
}
function readerSetActivePage(page) {
  const next = readerPageNumber(page, state.reader?.pages.length || 1), changed = next !== state.readerPage;
  state.readerPage = next; readerUpdatePageUI();
  if (changed) readerQueuePositionSave();
  return changed;
}
function setReaderPage(page, scrollChinese = false) {
  if (!state.reader) return;
  readerSetActivePage(page);
  const node = readerOriginalPage(state.readerPage); readerLoadImage(node && $("img", node));
  readerScrollToNode($("#original-scroll"), node);
  if (scrollChinese) scrollTranslationToPage(state.readerPage);
  readerQueuePositionSave();
}
async function saveReadPage() {
  clearTimeout(readSaveTimer); if (!state.reader) return;
  if (readerRuntime.savePromise) { await readerRuntime.savePromise; return saveReadPage(); }
  const id = state.reader.id, position = readerCapturePosition(), signature = JSON.stringify(position);
  if (readerRuntime.savedSignature === signature) return;
  readerCachePosition(position);
  readerRuntime.savePromise = (async () => {
    const data = await api(`/api/papers/${encodeURIComponent(id)}`, { method: "PATCH", body: { reader_state: position } });
    if (state.reader?.id !== id) return;
    updatePaper({ id, reader_state: position, read_page: position.page, ...(data.paper || (data.id ? data : {})) });
    readerRuntime.savedSignature = signature;
    if (JSON.stringify(readerCapturePosition()) === signature) readerCachePosition(position, false);
    readerSaveHint(`已保存阅读位置 · 第 ${position.page} 页`);
  })();
  try {
    await readerRuntime.savePromise;
  } catch (error) {
    readerSaveHint("位置暂存本机；继续阅读时重试同步");
    if (!readerCachePosition()) throw error;
  } finally { readerRuntime.savePromise = null; }
}
function scrollTranslationToPage(page) { readerScrollToNode($("#translation-scroll"), readerTranslationPage(page)); }
function readerSetZoom(value) {
  const zoom = value === "fit" ? "fit" : Number(value);
  if (!readerScrollActive() || zoom !== "fit" && !READER_ZOOMS.includes(zoom) || zoom === state.zoom) return;
  state.zoom = zoom;
  const select = $("#reader-zoom"); if (select) select.value = String(zoom);
  if (typeof syncSelectMenus === "function") syncSelectMenus();
  drawOriginalPage(); readerQueuePositionSave();
}
function readerHandleZoomWheel(event, container) {
  if (!event.ctrlKey || !event.cancelable || !Number.isFinite(event.deltaY) || !event.deltaY || !readerScrollActive()) return false;
  // Cancel browser zoom, including at either limit, before changing reader content.
  event.preventDefault();
  const enlarge = event.deltaY < 0;
  if (container.id === "original-scroll") {
    const current = state.zoom === "fit" ? 100 : Number(state.zoom);
    const next = enlarge ? READER_ZOOMS.find(value => value > current) : [...READER_ZOOMS].reverse().find(value => value < current);
    if (next !== undefined) readerSetZoom(next);
  } else {
    const current = readerClamp(state.settings.reader_font_size, 14, 28, 18);
    const next = readerClamp(current + (enlarge ? 1 : -1), 14, 28, 18);
    if (next !== current) {
      readerRuntime.resizeAnchor = readerCaptureAnchor($("#original-scroll"), ".reader-page-section");
      readerRuntime.translationAnchor = readerCaptureAnchor(container, ".translation-block");
      changePreference({ reader_font_size: next });
    }
  }
  return true;
}
function readerBindScrolling() {
  const original = $("#original-scroll"), translated = $("#translation-scroll");
  for (const [container, side, frame] of [[original, "suppressLeftUntil", "leftFrame"], [translated, "suppressRightUntil", "rightFrame"]]) {
    const release = () => { readerRuntime[side] = 0; };
    container.addEventListener("wheel", event => { if (!readerHandleZoomWheel(event, container)) release(); }, { passive: false });
    container.addEventListener("touchstart", release, { passive: true }); container.addEventListener("pointerdown", release);
    container.addEventListener("keydown", event => { if (["ArrowDown", "ArrowUp", "PageDown", "PageUp", "Home", "End", " "].includes(event.key)) release(); });
    container.addEventListener("scroll", () => {
        if (readerRuntime[frame]) return;
      readerRuntime[frame] = requestAnimationFrame(() => {
        readerRuntime[frame] = 0; if (!readerScrollActive() || performance.now() < readerRuntime[side]) return;
        const left = container === original;
        const node = readerAtPage(container, $$(left ? ".reader-page-section" : ".translation-block", container)); if (!node) return;
        const page = Number(node.dataset.page), changed = readerSetActivePage(page);
        if (left) readerRuntime.resizeAnchor = readerCaptureAnchor(original, ".reader-page-section");
        else readerRuntime.translationAnchor = readerCaptureAnchor(translated, ".translation-block");
        if (changed && readerRuntime.mode === "parallel" && readerSyncEnabled() && !readerIsEditing()) {
          if (left) scrollTranslationToPage(page); else { const target = readerOriginalPage(page); readerLoadImage(target && $("img", target)); readerScrollToNode(original, target); }
        }
        if (typeof IntersectionObserver !== "function" && left) readerObserveImages();
        readerQueuePositionSave();
      });
    }, { passive: true });
  }
  if (typeof ResizeObserver === "function") {
    let width = original.clientWidth;
    readerRuntime.resizeObserver = new ResizeObserver(() => {
      if (Math.abs(original.clientWidth - width) < 1 || !readerScrollActive()) return;
      if (!original.clientWidth) return;
      const anchor = readerRuntime.resizeAnchor, translationAnchor = readerRuntime.translationAnchor; width = original.clientWidth;
      drawOriginalPage(); readerRestoreAnchor(original, ".reader-page-section", anchor);
      if (readerRuntime.mode !== "original") readerRestoreAnchor(translated, ".translation-block", translationAnchor);
      readerApplyLayout();
    });
    readerRuntime.resizeObserver.observe(original);
  }
}

function readerBlockSignature(b) { return JSON.stringify([b.text, b.text_override, b.translation, b.translation_needs_review, b.note, b.highlight, b.kind, b.is_formula, b.image_url, b.has_unparsed_math]); }
function renderTranslations() {
  const p = state.reader, container = $("#translation-scroll"); if (!p || !container) return;
  if (typeof readerTextProtected === "function" && readerTextProtected()) return;
  const anchorSelector = ".translation-block";
  const anchor = readerCaptureAnchor(container, anchorSelector);
  const structure = p.reading_structure || { sections: [{ id: "body", role: "body", label: "Main text", entries: p.blocks.map(b => ({ id: b.id, role: b.kind === "heading" ? "section_heading" : b.kind, source_ids: [b.id] })) }] };
  const signature = JSON.stringify([structure.sections, structure.status, structure.error, structure.warnings]);
  const sameStructure = readerRuntime.structureSignature === signature;
  if (sameStructure) {
    for (const block of p.blocks) if (readerRuntime.blockSignatures.get(block.id) !== readerBlockSignature(block)) refreshBlock(block.id);
  } else {
    if (readerRuntime.editor) return;
    readerRuntime.entriesBySource.clear();
    for (const section of structure.sections) for (const entry of section.entries) for (const id of entry.source_ids) readerRuntime.entriesBySource.set(id, entry);
    const fragment = document.createDocumentFragment();
    const busy = ["queued", "running"].includes(structure.status);
    if (structure.status !== "ready" && p.blocks.length) {
      const message = busy ? "正在整理论文结构…" : structure.status === "needs_review" ? "语义校对未完成，当前按本地结构显示" : "已按论文结构整理";
      fragment.append(el("div", { class: "reader-structure-message", title: structure.error || "语义校对只识别结构，保留英文原句；使用 Codex 订阅额度。" },
        el("span", {}, message), !busy ? button(structure.status === "needs_review" ? "重试整理" : "校对结构", event => act(async () => {
          const data = await api(`/api/papers/${encodeURIComponent(p.id)}/structure`, { method: "POST", body: {} });
          if (state.reader?.id === p.id) { updatePaper(normalizeDetail(data)); renderTranslations(); }
        }, event.currentTarget), "secondary", "", { title: "使用 Codex 校对作者机构、摘要和章节；结果保存后重复打开不再消耗额度" }) : null));
    }
    for (const section of structure.sections) {
      const first = p.blocks.find(b => b.id === section.entries[0]?.id);
      fragment.append(el("section", { class: `translation-page translation-section role-${section.role}`, dataset: { page: first?.page || 1, sectionId: section.id }, "aria-label": section.label },
        section.role !== "body" && section.role !== "title" && asText(first?.text).replace(/\s/g, "").toLowerCase() !== section.label.replace(/\s/g, "").toLowerCase() ? el("h2", { class: "reader-section-heading" }, section.label) : null,
        section.entries.map(renderStructuredParagraph)));
    }
    if (!p.blocks.length) fragment.append(el("p", { class: "reader-error" }, "未能提取文字，扫描件需要 OCR；请查看原 PDF。"));
    container.replaceChildren(fragment);
    readerRuntime.structureSignature = signature;
  }
  readerRestoreAnchor(container, anchorSelector, anchor);
  readerRuntime.translationAnchor = readerCaptureAnchor(container, ".translation-block");
  state.readerRenderedSignature = paperSignature(p);
  if (typeof readerPaintAnnotations === "function") readerPaintAnnotations();
}
function renderStructuredParagraph(entry) {
  const sources = entry.source_ids.map(id => state.reader.blocks.find(b => b.id === id)).filter(Boolean);
  if (!sources.length) return el("span");
  for (const b of sources) readerRuntime.blockSignatures.set(b.id, readerBlockSignature(b));
  const active = sources.find(b => b.id === state.selectedBlock) || sources[0];
  const node = renderBlock(active);
  node.dataset.blockId = sources[0].id;
  node.dataset.page = sources[0].page;
  node.dataset.sourceIds = entry.source_ids.join(" ");
  node.dataset.sourcePages = [...new Set(sources.map(b => b.page))].join(" ");
  node.classList.toggle("is-heading", entry.role === "section_heading");
  node.classList.toggle("is-title", entry.role === "title");
  node.classList.toggle("is-caption", entry.role === "caption");
  if (["abstract", "article_info"].includes(entry.role) && /^(abstract|articleinfo)$/i.test(asText(sources[0].text).replace(/\s/g, ""))) node.classList.add("is-heading");
  const text = $(".translated-text", node);
  if (text) {
    text.replaceChildren(...sources.flatMap((b, i) => [i ? document.createTextNode(" ") : null,
      readerTextSpan(b, { class: ["authors", "affiliations"].includes(entry.role) && /^[\d,;*†‡\s]+$/.test(asText(b.text)) ? "is-marker" : "" })]).filter(Boolean));
  }
  if (sources.some(b => b.translation_needs_review) && !$(".reader-source-review", node)) node.append(el("small", { class: "reader-source-review" }, "原文已修改，译文待核对"));
  return node;
}
function renderBlock(b) {
  readerRuntime.blockSignatures.set(b.id, readerBlockSignature(b));
  const isFormula = Boolean(b.is_formula), isReference = b.kind === "reference";
  const node = el("article", { class: `translation-block ${readerTextLanguage(b) === "original" && !isFormula ? "untranslated" : ""} ${b.kind === "heading" ? "is-heading" : ""} ${b.kind === "caption" ? "is-caption" : ""}`, dataset: { blockId: b.id, page: b.page } });
  if (isFormula && b.image_url) node.append(el("div", { class: "block-label" }, "公式 · 原文"), imageNode(b.image_url, { alt: "原文公式", class: "reader-formula-image" }));
  else {
    if (isReference || isFormula && !b.translation) node.append(el("div", { class: "block-label" }, isReference ? "参考文献 · 原文" : "公式 · 请对照原文"));
    node.append(el("p", { class: "translated-text" }, readerTextSpan(b)));
  }
  if (b.is_table && b.image_url && !isFormula) node.append(imageNode(b.image_url, { alt: "原文表格", class: "reader-table-image" }));
  if (b.has_unparsed_math && !isFormula) node.append(el("div", { class: "block-label", title: "本段含无法可靠提取的数学字符，请查看左侧原 PDF。" }, "含公式 · 请核对原文"));
  if (b.translation_needs_review) node.append(el("small", { class: "reader-source-review" }, "原文已修改，译文待核对"));
  return node;
}
function selectBlock(id, scrollChinese) {
  const b = state.reader?.blocks.find(x => x.id === id); if (!b) return;
  if ($(".block-editor") && state.selectedBlock !== id) { toast("请先保存或取消当前编辑", "warn"); return; }
  const previous = state.selectedBlock; state.selectedBlock = id;
  const translated = $("#translation-scroll"), anchor = readerCaptureAnchor(translated, ".translation-block");
  if (previous && previous !== id) refreshBlock(previous); refreshBlock(id);
  readerRestoreAnchor(translated, ".translation-block", anchor);
  readerSetActivePage(Number(b.page));
  const originals = $$(".block-overlay", $("#original-scroll")); originals.forEach(n => n.classList.toggle("selected", n.dataset.blockId === id));
  const page = readerOriginalPage(b.page); readerLoadImage(page && $("img", page));
  readerScrollToNode($("#original-scroll"), originals.find(n => n.dataset.blockId === id) || page, "center");
  if (scrollChinese) readerScrollToNode(translated, findBlockNode(id), "center");
  $("#reader-translation-status").textContent = `第 ${b.page} 页 · 段落已定位`;
  readerQueuePositionSave();
}
function findBlockNode(id) { return $$(".translation-block", $("#translation-scroll")).find(n => n.dataset.blockId === String(id) || n.dataset.sourceIds?.split(" ").includes(String(id))); }
function refreshBlock(id) {
  if (typeof readerTextProtected === "function" && readerTextProtected()) return;
  const b = state.reader?.blocks.find(x => x.id === id), node = findBlockNode(id); if (!b || !node) return;
  const container = $("#translation-scroll"), anchor = readerCaptureAnchor(container, ".translation-block");
  // Background updates must never replace an editor or the currently focused control.
  if ($(".block-editor", node)) return;
  const focused = node.contains(document.activeElement), focusLabel = focused ? document.activeElement.getAttribute("aria-label") : null;
  const entry = readerRuntime.entriesBySource.get(id);
  const replacement = entry ? renderStructuredParagraph(entry) : renderBlock(b); node.replaceWith(replacement); readerRestoreAnchor(container, ".translation-block", anchor);
  if (focused) {
    const target = focusLabel ? $$("[aria-label]", replacement).find(n => n.getAttribute("aria-label") === focusLabel) : replacement;
    (target || replacement).focus({ preventScroll: true });
  }
}
async function patchBlock(id, patch) {
  // Invalidate any background fetch started before this user edit.
  readerRuntime.request++;
  const p = state.reader, data = await api(`/api/papers/${encodeURIComponent(p.id)}/blocks/${encodeURIComponent(id)}`, { method: "PATCH", body: patch });
  readerRuntime.request++;
  if (state.reader?.id !== p.id) return;
  if (data.paper) updatePaper(normalizeDetail(data));
  else { const b = state.reader.blocks.find(x => x.id === id); if (b) Object.assign(b, patch); }
  state.readerRenderedSignature = paperSignature(state.reader); renderLibrary();
}
// Persisted paragraph drafts still restore through this editor; new selections
// are edited by reader-text.js.
function openBlockEditor(id, mode) {
  const b = state.reader?.blocks.find(x => x.id === id), node = findBlockNode(id); if (!b || !node) return;
  if (readerRuntime.editor) { readerRuntime.editor.text.focus({ preventScroll: true }); toast("请先保存或放弃当前段落编辑", "warn"); return; }
  const draft = readerReadLocal("block", state.reader.id), restored = draft?.blockId === id && draft?.mode === mode && typeof draft.value === "string";
  const text = el("textarea", { value: restored ? draft.value : b[mode] || "", rows: mode === "translation" ? "7" : "4", placeholder: mode === "translation" ? "填写或修订本段中文翻译" : "填写段落批注", "aria-label": mode === "translation" ? "修订译文" : "段落批注" });
  const editor = el("form", { class: "block-editor" }, field(mode === "translation" ? "修订译文" : "段落批注", text)), save = el("button", { type: "submit", class: "button primary" }, "保存");
  const hint = el("div", { class: "save-hint", role: "status" }, restored ? "已恢复本机草稿" : "");
  const entry = { paperId: state.reader.id, blockId: id, mode, text, node: editor, hint, savedValue: b[mode] || "", saving: null };
  readerRuntime.editor = entry;
  text.addEventListener("input", () => {
    const ok = readerWriteLocal("block", entry.paperId, { blockId: id, mode, value: text.value });
    hint.textContent = ok ? "草稿已保存在本机" : "草稿暂未保存，请保持窗口打开";
  });
  editor.append(hint, el("div", { class: "reader-editor-actions" }, save, button("放弃修改", () => {
    readerRemoveLocal("block", entry.paperId); readerRuntime.editor = null; editor.remove(); refreshBlock(id); findBlockNode(id)?.focus({ preventScroll: true });
  }, "secondary")));
  editor.addEventListener("submit", event => { event.preventDefault(); act(async () => { await readerSaveEditor(); toast(mode === "translation" ? "译文已保存" : "批注已保存"); }, save); });
  node.append(editor); text.focus({ preventScroll: true }); readerScrollToNode($("#translation-scroll"), editor, "center");
}
async function readerSaveEditor() {
  const entry = readerRuntime.editor; if (!entry) return;
  if (entry.saving) { await entry.saving; if (readerRuntime.editor === entry) return readerSaveEditor(); return; }
  const value = entry.text.value;
  entry.hint.textContent = "正在保存…";
  entry.saving = (async () => {
    await patchBlock(entry.blockId, { [entry.mode]: value.trim() }); entry.savedValue = value;
    if (readerRuntime.editor !== entry) return;
    if (entry.text.value === value) {
      readerRemoveLocal("block", entry.paperId); readerRuntime.editor = null; entry.node.remove(); refreshBlock(entry.blockId); findBlockNode(entry.blockId)?.focus({ preventScroll: true });
    } else entry.hint.textContent = "上次修改已保存，新内容仍在草稿中";
  })();
  try { await entry.saving; }
  catch (error) {
    const local = readerWriteLocal("block", entry.paperId, { blockId: entry.blockId, mode: entry.mode, value: entry.text.value });
    entry.hint.textContent = local ? "文献库保存失败，草稿已保存在本机" : "保存失败，请保持窗口打开"; throw error;
  } finally { entry.saving = null; }
}
function readerNotesHint(message) { const node = $("#reader-notes-hint"); if (node) node.textContent = message; }
async function readerSaveNotes() {
  const entry = readerRuntime.notes; if (!entry) return;
  clearTimeout(entry.timer);
  if (entry.saving) { await entry.saving; if (entry.value !== entry.savedValue) return readerSaveNotes(); return; }
  if (entry.value === entry.savedValue) return;
  const value = entry.value; readerNotesHint("正在保存…");
  entry.saving = (async () => {
    const data = await api(`/api/papers/${encodeURIComponent(entry.id)}`, { method: "PATCH", body: { notes: value } });
    entry.savedValue = value; updatePaper({ id: entry.id, notes: value, ...(data.paper || (data.id ? data : {})) });
    if (entry.value === value) { readerRemoveLocal("notes", entry.id); readerNotesHint("笔记已保存在文献库"); }
    else readerNotesHint("最新修改保存在本机草稿中");
  })();
  try { await entry.saving; }
  catch (error) {
    entry.storageOk = readerWriteLocal("notes", entry.id, { value: entry.value });
    readerNotesHint(entry.storageOk ? "同步失败，笔记草稿已保存在本机" : "保存失败，请保持窗口打开"); throw error;
  } finally { entry.saving = null; }
}
function toggleReaderNotes(force) {
  const pane = $("#reader-notes"), entry = readerRuntime.notes; if (!pane || !entry) return;
  const show = typeof force === "boolean" ? force : pane.hidden;
  if (show && typeof closePaperChat === "function") closePaperChat();
  readerPreserveLayout(() => {
    pane.hidden = !show; $("#reader-notes-toggle").setAttribute("aria-expanded", String(show));
    if (!show) { readerSaveNotes().catch(() => {}); return; }
    if (pane.childElementCount) return;
    const textarea = el("textarea", { class: "notes-area", value: entry.value, placeholder: "论文笔记", "aria-label": "论文笔记", id: "reader-notes-text" });
    textarea.addEventListener("input", () => {
      entry.value = textarea.value; entry.storageOk = readerWriteLocal("notes", entry.id, { value: entry.value });
      readerNotesHint(entry.storageOk ? "草稿已保存在本机，正在等待同步…" : "草稿暂未保存，请保持窗口打开");
      clearTimeout(entry.timer); entry.timer = setTimeout(() => { readerSaveNotes().catch(() => {}); }, 650);
    });
    textarea.addEventListener("blur", () => { readerSaveNotes().catch(() => {}); });
    pane.append(el("div", { class: "section-heading" }, el("h3", {}, "论文笔记"), iconButton("close", "收起笔记", () => toggleReaderNotes(false))), textarea,
      el("div", { class: "save-hint", id: "reader-notes-hint", role: "status" }, entry.value !== entry.savedValue ? "已恢复本机草稿" : ""),
      button("立即保存", event => act(readerSaveNotes, event.currentTarget), "secondary", "check"));
  });
  if (show) $("#reader-notes-text")?.focus({ preventScroll: true });
}
function updateReaderProgress() {
  if (!state.reader) return;
  const t = translationOf(state.reader), node = $("#reader-translate-button");
  const label = $("#reader-text-pane-label");
  if (label) label.replaceChildren(icon("translate"), readerRuntime.textLanguage === "original" || t.done === 0 ? "英文原文" : t.done < t.total ? "译文与原文" : "中文译文");
  if (node) node.replaceChildren(icon(ACTIVE_STATUSES.has(t.status) ? "pause" : "translate"), ACTIVE_STATUSES.has(t.status) ? `暂停 · ${Math.round(ratio(state.reader))}%` : DONE_STATUSES.has(t.status) ? "已译完" : t.done ? "继续翻译" : "全文翻译");
}
async function refreshReader() {
  if (!readerScrollActive() || readerIsEditing()) return;
  const id = state.reader.id, request = ++readerRuntime.request;
  const p = normalizeDetail(await api(`/api/papers/${encodeURIComponent(id)}`));
  if (state.reader?.id !== id || !readerScrollActive() || request !== readerRuntime.request || readerIsEditing()) return;
  updatePaper(p); renderTranslations(); drawOriginalPage(); updateReaderProgress();
}
function applyReaderPreferences() {
  const toggle = $("#reader-sync"); if (toggle) toggle.checked = readerSyncEnabled();
  if (!readerScrollActive()) return;
  const originalAnchor = readerRuntime.resizeAnchor, translationAnchor = readerRuntime.translationAnchor;
  drawOriginalPage(); readerRestoreAnchor($("#original-scroll"), ".reader-page-section", originalAnchor);
  if (readerRuntime.mode !== "original") readerRestoreAnchor($("#translation-scroll"), ".translation-block", translationAnchor);
  readerQueuePositionSave();
}
