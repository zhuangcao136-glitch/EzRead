async function openDetail(id) {
  const samePaper = state.detail?.id === id;
  const data = await api(`/api/papers/${encodeURIComponent(id)}`); state.detail = normalizeDetail(data);
  state.detailFigure = (state.detail.figures || []).find(f => f.path === state.detail.cover)?.id ?? null;
  renderDetail(); openDialog("#detail-dialog");
  if (!samePaper) $("#detail-scroll").scrollTop = 0;
}
function detailFigure() {
  const figures = state.detail?.figures || [];
  return state.detailFigure === null ? null : figures.find(f => String(f.id) === String(state.detailFigure));
}
function detailCollectionPicker(p) {
  const current = p.collection || "", values = ["", ...allCollections()];
  let listeners;
  // Keep the native invoker relationship so a second click does not light-dismiss and reopen.
  const trigger = el("button", {
    id: "detail-collection-trigger", type: "button", class: "filter-trigger detail-collection-trigger",
    "aria-label": `论文所属集合：${current || "尚未加入集合"}`, "aria-haspopup": "listbox",
    "aria-controls": "detail-collection-menu", "aria-expanded": "false", popovertarget: "detail-collection-menu",
    onclick: event => { event.preventDefault(); menu.matches(":popover-open") ? close(true) : open(); },
    onkeydown: event => {
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      event.preventDefault(); open(event.key === "Home" ? 0 : event.key === "End" ? values.length - 1 : undefined);
    }
  }, el("span", {}, current || "尚未加入集合"), el("span", { class: "filter-chevron", "aria-hidden": "true" }));
  const choices = values.map((value, index) => el("button", {
    type: "button", class: "filter-menu-option", role: "option", tabindex: "-1",
    "aria-selected": String(value === current), "aria-current": String(value === current),
    style: { "--option-order": Math.min(index, 8) }, onclick: () => {
      close(true);
      if (value === current) return;
      void act(async () => {
        await patchPaper(p.id, { collection: value });
        renderDetail(); $("#detail-collection-trigger")?.focus({ preventScroll: true });
      }, trigger);
    }
  }, el("span", {}, value || "尚未加入集合"), value === current ? icon("check") : null));
  // A non-modal popover sits above the detail dialog without adding a blur layer.
  const menu = el("div", {
    id: "detail-collection-menu", class: "filter-menu detail-collection-menu", popover: "auto",
    role: "listbox", "aria-label": "论文所属集合", onbeforetoggle: event => {
      trigger.setAttribute("aria-expanded", String(event.newState === "open"));
      if (event.newState === "closed") { listeners?.abort(); listeners = null; }
    }, onkeydown: event => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(true); }
      else if (event.key === "Tab") close(true);
      else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        const index = choices.indexOf(document.activeElement);
        focusChoice(event.key === "Home" ? 0 : event.key === "End" ? choices.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + choices.length) % choices.length);
      }
    }
  }, el("div", { class: "filter-menu-options" }, choices));
  function close(restoreFocus = false) {
    if (menu.matches(":popover-open")) menu.hidePopover();
    if (restoreFocus && trigger.isConnected) trigger.focus({ preventScroll: true });
  }
  function position() {
    const rect = trigger.getBoundingClientRect(), dialog = $("#detail-dialog").getBoundingClientRect();
    if (rect.bottom <= Math.max(0, dialog.top) || rect.top >= Math.min(window.innerHeight, dialog.bottom)) { close(); return; }
    const gap = 8, margin = 12, below = window.innerHeight - rect.bottom - gap - margin, above = rect.top - gap - margin;
    menu.style.width = `${Math.min(rect.width, window.innerWidth - margin * 2)}px`;
    const openAbove = below < Math.min(menu.scrollHeight, 240) && above > below;
    menu.style.maxHeight = `${Math.max(80, Math.min(320, openAbove ? above : below))}px`;
    menu.style.left = `${Math.max(margin, Math.min(rect.left, window.innerWidth - menu.offsetWidth - margin))}px`;
    menu.style.top = `${openAbove ? Math.max(margin, rect.top - gap - menu.offsetHeight) : Math.min(rect.bottom + gap, window.innerHeight - menu.offsetHeight - margin)}px`;
  }
  function focusChoice(index) {
    const choice = choices[index];
    choice.focus({ preventScroll: true });
    if (choice.offsetTop < menu.scrollTop || choice.offsetTop + choice.offsetHeight > menu.scrollTop + menu.clientHeight) menu.scrollTop = choice.offsetTop - (menu.clientHeight - choice.offsetHeight) / 2;
  }
  function open(index = values.indexOf(current)) {
    if (!menu.matches(":popover-open")) {
      menu.showPopover(); listeners = new AbortController();
      window.addEventListener("resize", position, { signal: listeners.signal });
      document.addEventListener("scroll", event => { if (!menu.contains(event.target)) position(); }, { capture: true, signal: listeners.signal });
      $("#detail-dialog").addEventListener("close", () => close(), { signal: listeners.signal });
    }
    position(); if (menu.matches(":popover-open")) focusChoice(Math.max(0, index));
  }
  return el("div", { class: "detail-collection-picker" }, trigger, menu);
}
function renderDetail({ centerSelectedFigure = false } = {}) {
  const p = state.detail; if (!p) return;
  const previousMenu = $("#detail-collection-menu");
  if (previousMenu?.matches(":popover-open")) previousMenu.hidePopover();
  const chosen = detailFigure(), coverUrl = chosen?.url || p.cover_url, content = $("#detail-content");
  const previousGallery = $(".gallery", content);
  const galleryScrollLeft = previousGallery?.dataset.paperId === String(p.id) ? previousGallery.scrollLeft : 0;
  const cover = el("div", { class: "detail-cover" }, coverUrl ? imageNode(coverUrl, { alt: "论文代表性插图" }) : placeholderImage());
  const gallery = el("div", { class: "gallery", "aria-label": "论文插图" }, (p.figures || []).map((f, i) => el("button", { class: `gallery-item ${String(state.detailFigure) === String(f.id) ? "active" : ""}`, "aria-label": `查看第 ${i + 1} 张插图`, onclick: () => { state.detailFigure = f.id; renderDetail({ centerSelectedFigure: true }); } }, imageNode(f.url), el("span", {}, f.page ? `P${f.page}` : `${i + 1}`))));
  gallery.dataset.paperId = String(p.id);
  const visual = el("div", { class: "detail-visual" }, cover, el("div", { class: "cover-actions" }, button("设为封面", event => act(() => changeCover({ figure_id: chosen.id }), event.currentTarget), "secondary", "image", { disabled: !chosen }), button("裁剪所选图片", () => openCrop(chosen), "secondary", "edit", { disabled: !chosen })), gallery);
  const t = translationOf(p);
  const translateButton = button(ACTIVE_STATUSES.has(t.status) ? "暂停翻译" : DONE_STATUSES.has(t.status) ? "翻译设置 / 重译" : t.done ? "继续翻译" : "全文翻译", event => act(() => translateAction(p, ACTIVE_STATUSES.has(t.status)), event.currentTarget), "secondary", ACTIVE_STATUSES.has(t.status) ? "pause" : "translate");
  const keywords = list(p.tags);
  const summarizeRunning = ACTIVE_STATUSES.has(p.summarize_status);
  const keywordContent = keywords.length ? el("div", { class: "detail-tags", role: "list" }, keywords.map(tag => el("span", { class: "tag", role: "listitem" }, tag))) : el("div", { class: "detail-keywords-empty" }, el("p", {}, summarizeRunning ? "正在生成速览，完成后显示论文关键词。" : "生成速览以显示论文关键词"), button(summarizeRunning ? "正在生成…" : "生成速览", event => act(() => runPaperTask("summarize"), event.currentTarget), "secondary", "", { disabled: summarizeRunning }));
  const keywordSection = el("div", { class: "detail-keywords", role: "group", "aria-label": "论文关键词" }, keywordContent);
  const metadata = el("div", { class: "detail-meta" }, el("div", { class: "metadata-line" }, el("span", { class: "journal-badge", title: p.conference_name || journalNameOf(p) }, journalOf(p)), p.year || "年份待补充", p.doi ? el("a", { href: safeUrl(`https://doi.org/${p.doi}`), target: "_blank", rel: "noopener noreferrer", title: "打开 DOI" }, "DOI ↗") : null, el("span", { class: "detail-tier" }, rankBadges(p, { descriptive: true }))), el("h2", {}, titleOf(p)), el("p", { class: "authors-line" }, asText(p.authors) || "作者信息待提取"), keywordSection, el("div", { class: "detail-start" }, button(p.last_read ? "继续阅读" : "开始阅读", () => act(() => openReader(p.id)), "primary", "book"), translateButton, button("译文版本", event => act(() => openTranslationVersions(p), event.currentTarget), "secondary", "clock")));
  const publicationInfoAnchor = keywordSection;
  const publication = [p.publication_date, p.volume ? `卷 ${p.volume}` : "", p.issue ? `期 ${p.issue}` : "", p.page_range ? `发表页码 ${p.page_range}` : p.article_number ? `文章编号 ${p.article_number}` : ""].filter(Boolean);
  if (publication.length) metadata.insertBefore(el("p", { class: "info-inline" }, publication.join(" · ")), publicationInfoAnchor);
  const publicationControl = publicationVerified(p) ? el("p", { class: "publication-verified", role: "status" }, icon("check"), "出版信息已核对") : button(publicationMissing(p).length ? "补全出版信息" : "核对出版信息", () => void openPublicationLookup(p), "secondary", "");
  metadata.insertBefore(el("div", { class: "detail-publication-actions", "aria-label": "出版信息与合集" }, publicationControl, detailCollectionPicker(p)), publicationInfoAnchor);
  if (p.metadata_source) metadata.insertBefore(sourceList([{ title: "发表信息来源", url: p.metadata_source }]), publicationInfoAnchor);
  if (p.paper_type === "journal" && /^\d{4}\.\d{4,5}v\d+/i.test(p.filename || "")) metadata.insertBefore(el("p", { class: "info-inline" }, "当前原文为导入时的 arXiv 预印本；期刊和年份对应后续正式发表版本。"), publicationInfoAnchor);
  const overview = el("section", { class: "detail-overview", "aria-label": "研究速览" }, el("div", { class: "section-heading" }, el("h3", {}, "研究速览"), button(ACTIVE_STATUSES.has(p.summarize_status) ? "正在生成…" : "生成 / 更新速览", event => act(() => runPaperTask("summarize"), event.currentTarget), "secondary", "translate", { disabled: ACTIVE_STATUSES.has(p.summarize_status) })), p.summarize_status && !DONE_STATUSES.has(p.summarize_status) ? el("p", { class: "info-inline" }, `速览任务：${taskStatusName(p.summarize_status)}`) : null, p.summarize_error ? el("p", { class: "error-text" }, asText(p.summarize_error)) : null, ...[["一句话理解", p.summary], ["研究问题", p.problem], ["方法与装置", p.method], ["主要结果", p.results], ["局限与边界", p.limitations]].map(([label, value]) => el("section", { class: "insight" }, el("h4", {}, label), el("p", { class: value ? "" : "muted" }, asText(value) || "尚未生成"))));
  const notesArea = el("textarea", { class: "notes-area", placeholder: "论文笔记", "aria-label": "个人论文笔记", value: p.notes || "" });
  const noteHint = el("div", { class: "save-hint", role: "status" }); bindAutosave(notesArea, p.id, noteHint);
  const notes = el("section", { class: "detail-notes", "aria-label": "我的研究笔记" }, el("div", { class: "section-heading" }, el("h3", {}, "我的研究笔记"), icon("edit")), notesArea, noteHint);
  content.replaceChildren(el("div", { class: "detail-topbar" }, el("span", {}, "EzRead / 论文简介"), el("div", { class: "detail-toolbar" }, button(p.favorite ? "已收藏" : "收藏", event => act(async () => { await patchPaper(p.id, { favorite: !state.detail.favorite }); renderDetail(); }, event.currentTarget), "secondary", "star"), button("编辑信息", () => openMetadata(p), "secondary", "edit"), iconButton("close", "关闭论文简介", () => closeDialog("#detail-dialog")))), el("div", { class: "detail-main" }, el("div", { class: "detail-hero" }, visual, metadata), overview, notes));
  gallery.scrollLeft = galleryScrollLeft;
  if (centerSelectedFigure) {
    const selected = $(".gallery-item.active", gallery);
    if (selected) {
      const galleryRect = gallery.getBoundingClientRect(), selectedRect = selected.getBoundingClientRect();
      const centeredLeft = gallery.scrollLeft + selectedRect.left - galleryRect.left - (gallery.clientWidth - selectedRect.width) / 2;
      const target = Math.max(0, Math.min(gallery.scrollWidth - gallery.clientWidth, centeredLeft));
      gallery.scrollTo({ left: target, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    }
  }
  state.detailRenderedSignature = paperSignature(p);
}
function sourceList(sources) {
  return el("ul", { class: "source-list" }, list(sources).map((source, i) => { const url = externalUrl(typeof source === "string" ? source : source?.url || source?.href); return url ? el("li", {}, el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, typeof source === "object" ? source.title || source.name || `来源 ${i + 1}` : `来源 ${i + 1} ↗`)) : null; }));
}
const detailNoteSaves = new Map();
async function flushDetailNotes(id = null) {
  const entries = [...detailNoteSaves.values()].filter(entry => !id || entry.id === id);
  for (const entry of entries) {
    try { await entry.flush(); }
    catch (error) {
      if (!readerWriteLocal("notes", entry.id, { value: entry.value })) throw error;
      toast("笔记暂未保存到文献库，已保留本机草稿。", "warn");
    }
    // Transfer ownership to the reader/new detail view. Old blur handlers must
    // never retry an earlier draft after the new view has saved newer notes.
    entry.released = true; clearTimeout(entry.timer);
    if (detailNoteSaves.get(entry.id) === entry) detailNoteSaves.delete(entry.id);
  }
}
function bindAutosave(textarea, id, hint) {
  const draft = readerReadLocal("notes", id);
  if (typeof draft?.value === "string") textarea.value = draft.value;
  let entry = detailNoteSaves.get(id);
  if (!entry || entry.released) {
    entry = { id, value: textarea.value, savedValue: state.papers.find(p => p.id === id)?.notes || "", timer: null, saving: null };
    detailNoteSaves.set(id, entry);
  } else textarea.value = entry.value;
  entry.flush = async () => {
    clearTimeout(entry.timer);
    if (entry.released) return;
    if (entry.saving) { await entry.saving; if (entry.value !== entry.savedValue) return entry.flush(); return; }
    if (entry.value === entry.savedValue) return;
    entry.saving = (async () => {
      while (entry.value !== entry.savedValue) {
        const value = entry.value; hint.textContent = "正在保存…";
        try { await patchPaper(id, { notes: value }); entry.savedValue = value; }
        catch (error) { hint.textContent = `保存失败，草稿已保留：${error.message}`; throw error; }
      }
      if (readerReadLocal("notes", id)?.value === entry.savedValue) readerRemoveLocal("notes", id);
      hint.textContent = "已保存在本机";
    })().finally(() => { entry.saving = null; });
    return entry.saving;
  };
  textarea.addEventListener("input", () => {
    entry.value = textarea.value;
    const stored = readerWriteLocal("notes", id, { value: entry.value });
    hint.textContent = stored ? "草稿已保留，正在保存…" : "草稿存储不可用，请保持窗口打开";
    clearTimeout(entry.timer); entry.timer = setTimeout(() => { entry.flush().catch(() => {}); }, 600);
  });
  textarea.addEventListener("blur", () => { entry.flush().catch(() => {}); });
}
