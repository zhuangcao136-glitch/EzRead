"use strict";

// Catalogue edits have their own state; papers still use the shared library state.
let journalCatalogueController = null;
const JOURNAL_CATALOGUE_TIERS = { top: "顶级", important: "重要", other: "其他" };

function journalSearchText(value) {
  return String(value || "").normalize("NFKC").toLowerCase().replace(/&/g, " and ").replace(/\s+/g, " ").trim();
}
function journalSearchKey(value) { return journalSearchText(value).replace(/[^\p{L}\p{N}]/gu, ""); }
function journalInitial(name) {
  const first = String(name || "").normalize("NFKC").trim().charAt(0).toUpperCase();
  return /^[A-Z]$/.test(first) ? first : "#";
}
function searchJournalCatalogue(rows, query) {
  const text = journalSearchText(query), key = journalSearchKey(query);
  if (!text) return [...rows];
  if (!key) return [];
  const tokens = text.split(/[^\p{L}\p{N}]+/u).filter(Boolean);
  const scored = rows.map(row => {
    const name = journalSearchKey(row.name), aliases = String(row.aliases || "").split(";").map(journalSearchKey).filter(Boolean);
    const issn = journalSearchKey(row.issn);
    let score = issn === key ? 11000 : name === key ? 10000 : aliases.includes(key) ? 9900 : 0;
    const fields = [name, ...aliases];
    for (const [index, value] of fields.entries()) {
      const at = value.indexOf(key);
      if (at < 0) continue;
      const base = at === 0 ? (index === 0 ? 9000 : 8900) : (index === 0 ? 8000 : 7900);
      score = Math.max(score, base - Math.min(at, 100) - Math.min(value.length - key.length, 100) / 100);
    }
    if (tokens.every(token => journalSearchText(row.name).includes(token))) score = Math.max(score, 7600);
    if (tokens.every(token => journalSearchText(`${row.name} ${row.aliases} ${row.issn}`).includes(token))) score = Math.max(score, 7300);
    if (issn.includes(key)) score = Math.max(score, 7000);
    return { row, score };
  }).filter(item => item.score > 0);
  scored.sort((a, b) => b.score - a.score || (journalSearchText(a.row.name) < journalSearchText(b.row.name) ? -1 : journalSearchText(a.row.name) > journalSearchText(b.row.name) ? 1 : a.row.issn.localeCompare(b.row.issn)));
  return scored.map(item => item.row);
}

function journalCataloguePanel() {
  let catalogue = null, editingRevision = null;
  let matches = [], suggestionIndex = -1, navigationTier = "top", scrollFrame = null;
  const hint = el("p", { class: "small muted", role: "status", hidden: true });
  const suggestions = el("div", { id: "journal-catalogue-suggestions", class: "journal-catalogue-suggestions", role: "listbox", "aria-label": "可能的期刊", hidden: true });
  const searchStatus = el("span", { class: "small muted journal-catalogue-search-status", role: "status" });
  const search = el("input", { type: "search", placeholder: "搜索刊名、别名或 ISSN", "aria-label": "搜索期刊名单", role: "combobox", autocomplete: "off", "aria-autocomplete": "list", "aria-expanded": "false", "aria-controls": suggestions.id,
    oninput: () => { renderList(true); renderSuggestions(); }, onfocus: () => renderSuggestions(), onblur: () => hideSuggestions(), onkeydown: event => searchKeydown(event) });
  const searchBox = el("div", { class: "journal-catalogue-search-box" }, search, suggestions);
  const editor = el("div", { class: "journal-catalogue-editor" });
  const rowsNode = el("div", { class: "journal-catalogue-rows" });
  const listNode = el("div", { class: "journal-catalogue-list", tabindex: 0, "aria-label": "期刊等级查看列表", onscroll: () => {
    if (scrollFrame !== null) return;
    scrollFrame = requestAnimationFrame(() => { scrollFrame = null; syncNavigation(); });
  } }, editor, rowsNode);
  const tierLabel = el("span");
  const tierCount = el("span", { class: "small muted journal-catalogue-tier-count" });
  const tierMenuOptions = new Map();
  const tierMenu = el("div", { id: "journal-catalogue-tier-menu", class: "journal-catalogue-tier-menu", popover: "auto", role: "menu", "aria-label": "选择期刊等级", ontoggle: event => {
    tierNavigation.setAttribute("aria-expanded", String(event.newState === "open"));
  }, onkeydown: event => {
    const options = [...tierMenuOptions.values()].map(item => item.node);
    const index = options.indexOf(document.activeElement);
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); tierMenu.hidePopover(); tierNavigation.focus(); }
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      options[event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length].focus();
    }
  } }, Object.entries(JOURNAL_CATALOGUE_TIERS).map(([tier, name]) => {
    const count = el("span", { class: "small muted journal-catalogue-tier-count" });
    const node = button("", () => {
      navigationTier = tier; tierMenu.hidePopover(); hideSuggestions(); renderList(true); tierNavigation.focus();
    }, "", "", { class: "journal-catalogue-tier-option", role: "menuitemradio", "aria-checked": "false", dataset: { tier } });
    node.append(grade({ tier }), count, el("span", { class: "journal-catalogue-tier-check", "aria-hidden": "true" }, "✓"));
    tierMenuOptions.set(tier, { node, count, name }); return node;
  }));
  const tierNavigation = button("", () => toggleTierMenu(), "", "", { class: "journal-catalogue-tier-trigger", "aria-haspopup": "menu", "aria-controls": tierMenu.id, "aria-expanded": "false", disabled: true, onkeydown: event => {
    if (["ArrowDown", "ArrowUp"].includes(event.key)) { event.preventDefault(); showTierMenu(event.key === "ArrowUp" ? "last" : "first"); }
  } });
  tierNavigation.append(tierLabel, tierCount, el("span", { class: "journal-catalogue-tier-chevron", "aria-hidden": "true" }));
  const letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ#".split("");
  const indexButtons = new Map();
  const alphabet = el("nav", { class: "journal-catalogue-alphabet", "aria-label": "期刊首字母快捷访问" }, letters.map(letter => {
    const node = button(letter, () => jumpToLetter(letter), "", "", { class: "journal-catalogue-letter", "aria-label": `定位首字母 ${letter === "#" ? "非英文字母" : letter}`, title: letter === "#" ? "中文及其他非英文字母刊名" : letter });
    indexButtons.set(letter, node); return node;
  }));
  const navigation = el("div", { class: "journal-catalogue-navigation" }, el("div", { class: "journal-catalogue-tier-filter" }, el("span", { class: "small muted" }, "期刊等级"), tierNavigation, tierMenu), searchStatus);
  const catalogueBrowser = el("div", { class: "journal-catalogue-browser" }, listNode, alphabet);

  async function refresh() {
    const saved = await api("/api/journal-catalogue");
    if (!root.isConnected) return;
    catalogue = saved; renderList();
    if (document.activeElement === search) renderSuggestions();
    hint.textContent = saved.error || ""; hint.hidden = !saved.error;
  }
  async function save(changes, revision) {
    const saved = await api("/api/journal-catalogue", { method: "PATCH", body: { changes, revision } });
    catalogue = saved; editor.replaceChildren(); editingRevision = null;
    hideSuggestions(); renderList();
    hint.textContent = ""; hint.hidden = true;
    await loadLibrary(); await poll();
    toast("期刊名单已保存，论文评级已刷新");
  }
  function edit(row = null) {
    if (!catalogue || catalogue.error) return;
    editingRevision = catalogue.revision;
    const controls = {};
    for (const key of ["name", "issn", "aliases", "basis", "source_url"]) {
      controls[key] = el("input", { value: row?.[key] || "", maxlength: 2000,
        "aria-label": { name: "期刊全称", issn: "期刊 ISSN", aliases: "期刊别名", basis: "评级依据", source_url: "来源链接" }[key] });
    }
    // ISSN is the stable identity; changing it is a remove-and-add operation.
    controls.issn.disabled = Boolean(row);
    controls.tier = el("select", { "aria-label": "名单期刊等级" }, Object.entries(JOURNAL_CATALOGUE_TIERS).map(([key, name]) => el("option", { value: key }, name)));
    controls.tier.value = row?.tier || navigationTier;
    const form = el("form", { class: "journal-catalogue-form", onsubmit: event => {
      event.preventDefault();
      const changed = Object.fromEntries(Object.entries(controls).map(([key, node]) => [key, node.value]));
      if (row && changed.tier !== row.tier && changed.basis === row.basis) changed.basis = "用户自定义等级";
      if (!changed.basis) changed.basis = "用户自定义名单";
      void act(() => save([{ action: "upsert", ...changed }], editingRevision), form.querySelector("button[type=submit]"));
    } }, el("h4", {}, row ? "编辑期刊" : "添加期刊"),
    el("div", { class: "journal-catalogue-fields" },
      field("期刊全称", controls.name), field("ISSN", controls.issn, "格式：1234-567X；须通过校验"),
      field("等级", controls.tier), field("简称 / 别名", controls.aliases, "多个别名以英文分号 ; 分隔"),
      field("评级依据", controls.basis), field("来源链接（可选）", controls.source_url)),
    el("div", { class: "journal-catalogue-actions" }, el("button", { type: "submit", class: "button primary" }, "保存期刊"),
      button("取消编辑", () => { editor.replaceChildren(); editingRevision = null; })));
    editor.replaceChildren(form); listNode.scrollTop = 0; controls.name.focus();
  }
  function remove(row) {
    const revision = catalogue.revision;
    editor.replaceChildren(el("p", {}, `从名单移除「${row.name}」后，匹配该期刊的论文将归为“其他”。`),
      el("div", { class: "journal-catalogue-actions" }, button("确认移除期刊", event => act(() => save([{ action: "delete", issn: row.issn }], revision), event.currentTarget)),
        button("取消", () => editor.replaceChildren())));
    listNode.scrollTop = 0;
  }
  function renderList(resetScroll = false) {
    if (!catalogue) return;
    const query = search.value.trim();
    const scrollTop = resetScroll ? 0 : listNode.scrollTop;
    matches = searchJournalCatalogue(query ? catalogue.rows : catalogue.rows.filter(row => row.tier === navigationTier), query);
    searchStatus.textContent = query ? `全名单搜索 · ${matches.length} 本匹配期刊 · 按匹配程度排序` : "A–Z；# 为中文等其他刊名";
    const empty = query ? "全部期刊中没有匹配结果。可尝试简称、别名或 ISSN。" : navigationTier === "other" ? "暂无明确列为其他等级的期刊。未列入名单的期刊也默认归为其他。" : `暂无${JOURNAL_CATALOGUE_TIERS[navigationTier]}期刊，可通过 Codex 调整名单或修改现有期刊等级。`;
    rowsNode.replaceChildren(...(matches.length ? matches.map(row => catalogueRow(row)) : [el("p", { class: "small muted journal-catalogue-empty" }, empty)]));
    listNode.scrollTop = scrollTop;
    renderTierFilter(); updateIndex();
  }
  function grade(row) { return el("span", { class: `journal-catalogue-grade grade-${row.tier}` }, JOURNAL_CATALOGUE_TIERS[row.tier]); }
  function renderTierFilter() {
    tierLabel.replaceChildren(grade({ tier: navigationTier }));
    tierCount.textContent = `${catalogue.counts[navigationTier]} 本`;
    tierNavigation.disabled = false;
    tierNavigation.setAttribute("aria-label", `查看期刊等级：${JOURNAL_CATALOGUE_TIERS[navigationTier]}`);
    for (const [tier, item] of tierMenuOptions) {
      item.count.textContent = `${catalogue.counts[tier]} 本`;
      item.node.setAttribute("aria-checked", String(tier === navigationTier));
      item.node.setAttribute("aria-label", `${item.name}期刊，${catalogue.counts[tier]} 本`);
    }
  }
  function showTierMenu(focus = "selected") {
    if (!catalogue) return;
    tierMenu.showPopover();
    const rect = tierNavigation.getBoundingClientRect(), width = Math.min(208, window.innerWidth - 24);
    tierMenu.style.width = `${width}px`;
    tierMenu.style.left = `${Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))}px`;
    const height = tierMenu.getBoundingClientRect().height;
    tierMenu.style.top = `${rect.bottom + height + 8 > window.innerHeight ? Math.max(12, rect.top - height - 6) : rect.bottom + 6}px`;
    const options = [...tierMenuOptions.values()];
    (focus === "first" ? options[0] : focus === "last" ? options.at(-1) : tierMenuOptions.get(navigationTier)).node.focus();
  }
  function toggleTierMenu() { if (tierMenu.matches(":popover-open")) tierMenu.hidePopover(); else showTierMenu(); }
  function catalogueRow(row) {
    return el("article", { class: "journal-catalogue-row", dataset: { issn: row.issn, tier: row.tier, initial: journalInitial(row.name) } },
      el("div", { class: "journal-catalogue-identity" }, el("strong", {}, row.name),
        row.aliases ? el("span", { class: "small muted journal-catalogue-aliases", title: row.aliases.replaceAll(";", " / ") }, row.aliases.replaceAll(";", " / ")) : null,
        el("div", { class: "small muted journal-catalogue-metadata" }, el("span", { class: "journal-catalogue-issn" }, row.issn), el("span", { "aria-hidden": "true" }, "·"),
          el("span", { class: "journal-catalogue-basis", title: row.basis || "用户自定义名单" }, row.basis || "用户自定义名单"),
          row.source_url ? [el("span", { "aria-hidden": "true" }, "·"), el("a", { href: row.source_url, target: "_blank", rel: "noopener noreferrer" }, "来源 ↗")] : null)),
      el("div", { class: "journal-catalogue-row-actions" },
        search.value.trim() ? grade(row) : null,
        button("编辑", () => edit(row), "secondary", "", { disabled: Boolean(catalogue.error), "aria-label": `编辑 ${row.name}` }),
        button("移除", () => remove(row), "secondary", "", { disabled: Boolean(catalogue.error), "aria-label": `移除 ${row.name}` })));
  }
  function scrollToRow(node) {
    const top = node.getBoundingClientRect().top - listNode.getBoundingClientRect().top + listNode.scrollTop - listNode.clientTop - 8;
    listNode.scrollTo({ top: Math.max(0, top), behavior: "auto" });
  }
  function jumpToLetter(letter) {
    const row = listNode.querySelector(`[data-initial="${letter}"]`);
    if (!row) return;
    listNode.querySelectorAll(".journal-catalogue-jump-target").forEach(node => node.classList.remove("journal-catalogue-jump-target"));
    row.classList.add("journal-catalogue-jump-target"); scrollToRow(row); syncNavigation();
  }
  function updateIndex(active = null) {
    const query = Boolean(search.value.trim());
    alphabet.hidden = query || !matches.length;
    for (const [letter, node] of indexButtons) {
      node.disabled = query || !listNode.querySelector(`[data-initial="${letter}"]`);
      node.classList.toggle("active", letter === active);
      node.setAttribute("aria-pressed", String(letter === active));
    }
  }
  function syncNavigation() {
    if (search.value.trim()) return;
    const top = listNode.getBoundingClientRect().top + listNode.clientTop + 10;
    const row = [...listNode.querySelectorAll("[data-initial]")].find(node => node.getBoundingClientRect().bottom > top);
    updateIndex(row?.dataset.initial || null);
  }
  function hideSuggestions() {
    suggestions.hidden = true; suggestionIndex = -1;
    search.setAttribute("aria-expanded", "false"); search.removeAttribute("aria-activedescendant");
  }
  function chooseSuggestion(row) {
    search.value = row.name; hideSuggestions(); renderList(true);
    const node = listNode.querySelector(`[data-issn="${row.issn}"]`);
    if (node) { node.classList.add("journal-catalogue-jump-target"); scrollToRow(node); }
  }
  function renderSuggestions() {
    if (!catalogue || !search.value.trim()) { hideSuggestions(); return; }
    const rows = matches.slice(0, 8);
    suggestionIndex = -1; search.removeAttribute("aria-activedescendant");
    suggestions.replaceChildren(...rows.map((row, index) => el("button", { type: "button", role: "option", tabindex: -1, class: "journal-catalogue-suggestion", id: `journal-suggestion-${index}`, title: row.name, "aria-selected": "false", onpointerdown: event => event.preventDefault(), onclick: () => chooseSuggestion(row) },
      el("span", { class: "journal-catalogue-suggestion-name" }, el("strong", {}, row.name), el("small", { class: "muted" }, row.issn)), grade(row))));
    suggestions.hidden = !rows.length; search.setAttribute("aria-expanded", String(Boolean(rows.length)));
  }
  function searchKeydown(event) {
    if (event.isComposing) return;
    if (event.key === "Escape" && !suggestions.hidden) { event.preventDefault(); event.stopPropagation(); hideSuggestions(); return; }
    if (!["ArrowDown", "ArrowUp", "Enter"].includes(event.key) || !search.value.trim()) return;
    if (event.key === "Enter") {
      if (!matches.length) return;
      event.preventDefault(); chooseSuggestion(matches[Math.max(0, suggestionIndex)]); return;
    }
    if (suggestions.hidden) renderSuggestions();
    const options = [...suggestions.children]; if (!options.length) return;
    event.preventDefault();
    suggestionIndex = (suggestionIndex + (event.key === "ArrowDown" ? 1 : suggestionIndex < 0 ? 0 : -1) + options.length) % options.length;
    options.forEach((node, index) => { node.classList.toggle("selected", index === suggestionIndex); node.setAttribute("aria-selected", String(index === suggestionIndex)); });
    search.setAttribute("aria-activedescendant", options[suggestionIndex].id);
    options[suggestionIndex].scrollIntoView({ block: "nearest" });
  }
  const root = el("section", { class: "journal-catalogue-panel" },
    el("div", { class: "journal-catalogue-heading" }, el("h3", {}, "期刊等级名单"), searchBox), hint,
    navigation, catalogueBrowser);
  journalCatalogueController?.dispose?.();
  journalCatalogueController = { root, refresh, revision: () => catalogue?.revision, dispose: () => { if (scrollFrame !== null) cancelAnimationFrame(scrollFrame); } };
  void act(refresh);
  return root;
}

async function syncJournalCataloguePanel(data) {
  const controller = journalCatalogueController;
  if (controller?.root.isConnected && data.journal_catalogue_revision && (controller.revision() !== data.journal_catalogue_revision || data.journal_catalogue_error)) {
    await controller.refresh();
  }
}
