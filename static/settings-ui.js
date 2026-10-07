"use strict";

async function openSettings(initialPanel = "appearance") {
  let currentPanel = null, refreshPromise = null, disposed = false;
  async function refreshTranslationSettings() {
    if (refreshPromise) return refreshPromise;
    refreshPromise = Promise.allSettled([
      (async () => { if (modelRequest) await modelRequest.catch(() => {}); if (!disposed) await loadModels(true); })(),
      refreshStatus(true).catch(error => {
        state.status = { ...(state.status || {}), codex: { available: null, authenticated: null, message: `连接状态读取失败：${error.message}` } };
        $("#settings-codex-status")?.replaceChildren(codexStatusPanel());
      }),
      (async () => { if (usageRequest) await usageRequest; if (!disposed) await loadUsage(true); })()
    ]).finally(() => { refreshPromise = null; });
    return refreshPromise;
  }
  function preferenceRange(key, label, min, max) {
    return el("div", { class: "preference-control" }, el("label", {}, label), el("input", { type: "range", min, max, step: 1, value: state.settings[key] ?? DEFAULT_PREFERENCES[key], dataset: { preference: key }, "aria-label": label, oninput: event => changePreference({ [key]: Number(event.target.value) }) }), el("output", { dataset: { preferenceOutput: key } }, `${state.settings[key]} px`));
  }
  const themes = el("div", { class: "theme-grid" }, [["paper", "纸白"], ["cream", "暖米"], ["sage", "浅绿"], ["graphite", "石墨"]].map(([value, name]) => el("button", { type: "button", class: `theme-choice ${state.settings.theme === value ? "selected" : ""}`, dataset: { themeChoice: value }, "aria-pressed": state.settings.theme === value, onclick: () => changePreference({ theme: value }) }, el("span", { class: "theme-swatch", "aria-hidden": "true" }), name)));
  const fullTranslation = defaultTranslationPanel(), selectionTranslation = defaultTranslationPanel(true);
  const panels = [
    ["appearance", "外观与字号", el("div", {},
      el("h3", {}, "阅读主题"), themes,
      el("section", { class: "settings-section" }, el("h3", {}, "字号"),
        preferenceRange("ui_font_size", "界面字号", 14, 22),
        preferenceRange("reader_font_size", "中文阅读字号", 14, 28),
        el("div", { class: "preference-preview" }, "研究方法、实验条件与主要结果。")))],
    ["journals", "期刊等级", journalCataloguePanel()],
    ["translation", "翻译", el("div", {}, fullTranslation, selectionTranslation, el("details", { class: "settings-disclosure" }, el("summary", {}, "连接与账号共享用量"), el("div", { class: "status-panel", id: "settings-codex-status" }, codexStatusPanel()), el("div", { class: "usage-panel", id: "settings-usage" }, usagePanel(false))))],
    ["data", "文献与备份", el("div", {}, el("h3", {}, "本地文献库"), el("p", { class: "settings-path" }, state.status?.data_dir || "本机文献库"), el("div", { class: "settings-data-actions" }, el("a", { href: "/api/export", class: "button secondary", download: "EzRead-library-backup.zip" }, icon("download"), "导出完整备份"), button("查看回收站", event => act(showTrash, event.currentTarget), "secondary", "trash")), el("div", { id: "trash-list" }))],
    ["about", "关于", el("div", { class: "about-ezread" }, el("img", { src: "/static/ezread-icon.png", width: "84", height: "84", alt: "EzRead" }), el("h3", {}, "EzRead"), el("p", { class: "small muted" }, state.status?.version ? `版本 ${state.status.version} · 本地论文阅读器` : "本地论文阅读器"))]
  ];
  const nav = el("div", { class: "settings-tabs", role: "tablist", "aria-label": "设置分类" });
  const body = el("div", { class: "settings-panels" });
  function selectPanel(id, focus = false) {
    const entered = currentPanel !== id; currentPanel = id;
    $$("[role=tab]", nav).forEach(tab => { const selected = tab.dataset.panel === id; tab.setAttribute("aria-selected", String(selected)); tab.tabIndex = selected ? 0 : -1; if (selected && focus) tab.focus(); });
    $$("[role=tabpanel]", body).forEach(panel => { panel.hidden = panel.id !== `settings-panel-${id}`; });
    footer.hidden = id !== "appearance";
    $("#settings-dialog").scrollTop = 0;
    if (id === "translation" && entered) void refreshTranslationSettings();
  }
  for (const [id, name, content] of panels) {
    const tab = el("button", { role: "tab", id: `settings-tab-${id}`, dataset: { panel: id }, "aria-controls": `settings-panel-${id}`, onclick: () => selectPanel(id) }, name);
    nav.append(tab); body.append(el("section", { role: "tabpanel", id: `settings-panel-${id}`, "aria-labelledby": tab.id }, content));
  }
  nav.addEventListener("keydown", event => {
    const tabs = $$("[role=tab]", nav), index = tabs.indexOf(document.activeElement);
    if (index < 0) return;
    const offset = ["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : ["ArrowLeft", "ArrowUp"].includes(event.key) ? -1 : 0;
    if (offset) { event.preventDefault(); selectPanel(tabs[(index + offset + tabs.length) % tabs.length].dataset.panel, true); }
  });
  const footer = el("div", { class: "preferences-footer" }, el("span", { class: "save-hint", id: "preference-save-status", role: "status" }), button("重试保存", () => act(flushPreferences)), button("恢复外观与字号默认值", () => {
    const defaults = { ...DEFAULT_PREFERENCES }; delete defaults.reader_sync;
    changePreference(defaults);
  }));
  $("#settings-content").replaceChildren(el("div", { class: "dialog-heading" }, el("h2", {}, "设置"), iconButton("close", "关闭设置", () => { void flushPreferences(); closeDialog("#settings-dialog"); })), nav, body, footer);
  $("#settings-dialog").addEventListener("close", () => { disposed = true; }, { once: true });
  $("#settings-dialog").classList.add("settings-dialog-wide"); openDialog("#settings-dialog"); selectPanel(initialPanel === "reading" ? "appearance" : initialPanel); syncPreferenceControls();
}
