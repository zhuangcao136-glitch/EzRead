function selectOptions(selector, options, first) {
  const select = $(selector), previous = select.value;
  select.replaceChildren(el("option", { value: "" }, first), ...options.map(value => el("option", { value }, value)));
  if (options.includes(previous)) select.value = previous;
}
function syncFilterTriggers() {
  $$(".filter-trigger[data-select]").forEach(trigger => {
    const select = $(`#${trigger.dataset.select}`), label = select.value ? select.selectedOptions[0]?.textContent : FILTER_TITLES[select.id];
    trigger.querySelector("span").textContent = label;
    trigger.setAttribute("aria-label", `${FILTER_TITLES[select.id]}：${label}`);
    trigger.classList.toggle("has-filter", Boolean(select.value));
  });
}
function syncSortControls() {
  const key = $("#sort").value, direction = $("#sort-direction").value;
  $$("[data-sort-key]").forEach(button => {
    const selected = button.dataset.sortKey === key && button.dataset.sortDirection === direction;
    button.setAttribute("aria-pressed", String(selected));
    button.classList.toggle("active", selected);
  });
}
function closeFilterMenu(restoreFocus = false) {
  if (!activeFilterTrigger) return;
  const trigger = activeFilterTrigger;
  activeFilterTrigger = null;
  trigger.setAttribute("aria-expanded", "false");
  $("#filter-menu").classList.add("hidden");
  $("#filter-backdrop").classList.add("hidden");
  document.documentElement.classList.remove("filter-menu-open");
  if (restoreFocus) trigger.focus();
}
function positionFilterMenu() {
  if (!activeFilterTrigger) return;
  const rect = activeFilterTrigger.getBoundingClientRect(), menu = $("#filter-menu");
  if (rect.bottom < 0 || rect.top > window.innerHeight || rect.right < 0 || rect.left > window.innerWidth) { closeFilterMenu(); return; }
  const gap = 8, margin = 12;
  menu.style.left = `${Math.max(margin, Math.min(rect.left, window.innerWidth - menu.offsetWidth - margin))}px`;
  const below = window.innerHeight - rect.bottom - gap - margin, above = rect.top - gap - margin;
  const openAbove = below < Math.min(menu.scrollHeight, 240) && above > below;
  menu.style.maxHeight = `${Math.max(80, openAbove ? above : below)}px`;
  menu.style.top = `${openAbove ? Math.max(margin, rect.top - gap - menu.offsetHeight) : Math.min(rect.bottom + gap, window.innerHeight - menu.offsetHeight - margin)}px`;
}
function openFilterMenu(trigger) {
  if (activeFilterTrigger === trigger) { closeFilterMenu(true); return; }
  closeFilterMenu();
  activeFilterTrigger = trigger;
  const select = $(`#${trigger.dataset.select}`), options = [...select.options];
  $("#filter-menu-title").textContent = FILTER_TITLES[select.id];
  const choices = options.map((option, index) => el("button", {
    type: "button", class: "filter-menu-option", "aria-current": String(option.value === select.value), style: { "--option-order": Math.min(index, 8) },
    onclick: () => { select.value = option.value; select.dispatchEvent(new Event("change", { bubbles: true })); closeFilterMenu(true); }
  }, el("span", {}, option.textContent), option.value === select.value ? icon("check") : null));
  $("#filter-menu-options").replaceChildren(...choices);
  trigger.setAttribute("aria-expanded", "true");
  $("#filter-menu").classList.remove("hidden");
  $("#filter-backdrop").classList.remove("hidden");
  document.documentElement.classList.add("filter-menu-open");
  positionFilterMenu();
  const selected = choices.find(choice => choice.getAttribute("aria-current") === "true") || choices[0];
  selected?.focus({ preventScroll: true });
  selected?.scrollIntoView({ block: "nearest" });
}
function renderNav() {
  $("#nav-count").textContent = state.papers.length;
  const collections = allCollections();
  $("#collections-nav").replaceChildren(...(collections.length ? collections.map(name => makeDropTarget(el("button", { class: `nav-item collection-nav ${state.view === "collection" && state.collection === name ? "active" : ""}`, "aria-pressed": String(state.view === "collection" && state.collection === name), onclick: () => toggleView("collection", name), title: `查看 ${name}；再次点击退出；可将论文拖入` }, el("span", { class: "collection-dot" }), el("span", { class: "collection-label", text: name }), el("span", { class: "nav-count", text: state.papers.filter(p => p.collection === name).length })), "collection", name)) : [el("p", { class: "collection-empty" }, "暂无主要合集") ]));
  $$(".nav-item[data-view]").forEach(n => { if (n.dataset.view === "queue") { const open = $("#queue-dialog").open; n.classList.toggle("active", open); n.setAttribute("aria-expanded", String(open)); n.removeAttribute("aria-current"); return; } const active = n.dataset.view === state.view && (n.dataset.view !== "all" || !$("#filter-type").value); n.classList.toggle("active", active); if (active) n.setAttribute("aria-current", "page"); else n.removeAttribute("aria-current"); });
  $$("[data-filter-type]").forEach(n => { const active = n.dataset.filterType === $("#filter-type").value; n.classList.toggle("active", active); n.setAttribute("aria-pressed", String(active)); });
  $(".nav-item[data-view='favorites']")?.setAttribute("aria-pressed", String(state.view === "favorites"));
  const active = state.papers.filter(p => ACTIVE_STATUSES.has(translationOf(p).status)).length;
  $("#queue-count").textContent = active; $("#queue-count").classList.toggle("hidden", !active);
}
function setView(view, collection = "") {
  closeFilterMenu();
  if (view === "queue") { openQueue(); return; }
  if ($("#queue-dialog").open) { queueLibraryScroll = 0; closeDialog("#queue-dialog"); }
  state.view = view; state.collection = collection;
  $("#search").placeholder = view === "all" ? "搜索论文…" : `搜索${collection || VIEW_NAMES[view]}…`;
  renderLibrary(); window.scrollTo({ top: 0 });
}
function toggleView(view, collection = "") {
  const leave = (view === "favorites" || view === "collection") && state.view === view && state.collection === collection;
  setView(leave ? "all" : view, leave ? "" : collection);
}
function openQueue() {
  if (!$("#queue-dialog").open) queueLibraryScroll = window.scrollY;
  renderQueue(); renderUsagePanels(); openDialog("#queue-dialog"); renderNav();
  void act(refreshStatus); void loadUsage(false);
}
function filteredPapers() {
  const q = state.query.toLowerCase().trim(), journal = $("#filter-journal").value, tier = $("#filter-tier").value, type = $("#filter-type").value;
  let papers = state.papers.filter(p => {
    if (state.view === "favorites" && !p.favorite) return false;
    if (state.view === "collection" && p.collection !== state.collection) return false;
    if (type && paperType(p) !== type) return false;
    if (journal && journalOf(p) !== journal) return false;
    if (tier && tierOf(p) !== tier) return false;
    if (q) {
      const searchable = [p.title, p.title_zh, p.summary, p.authors, p.journal, p.journal_abbr, p.conference_name, p.conference_abbr, p.tags, p.doi, p.collection].map(asText).join(" ").toLowerCase();
      const journalAlias = `${p.journal_abbr || ""} ${p.journal || ""} ${p.conference_abbr || ""} ${p.conference_name || ""}`.toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff]/g, "");
      const aliasQuery = q.replace(/[^a-z0-9\u4e00-\u9fff]/g, "");
      if (!searchable.includes(q) && !(aliasQuery && journalAlias.includes(aliasQuery))) return false;
    }
    return true;
  });
  sortPapers(papers, $("#sort").value, $("#sort-direction").value);
  return papers;
}
function sortPapers(papers, key, direction) {
  const value = p => key === "year" ? Number(p.year) || 0 : key === "imported" ? importedAt(p) : dateMs(p.last_read);
  papers.sort((a, b) => {
    const left = value(a), right = value(b);
    if (!left || !right) return !left && !right ? titleOf(a).localeCompare(titleOf(b), "zh-CN") : !left ? 1 : -1;
    return (left - right) * (direction === "asc" ? 1 : -1) || titleOf(a).localeCompare(titleOf(b), "zh-CN");
  });
  return papers;
}
