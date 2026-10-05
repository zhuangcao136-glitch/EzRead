"use strict";

async function openSettings() {
  function quickTranslationPanel() {
    const select = el("select", { "aria-label": "划线翻译模型" });
    const hint = el("p", { class: "small muted", role: "status" }, "正在读取模型目录…");
    const render = () => {
      const models = modelCatalog?.models || [];
      select.replaceChildren(...models.map(item => el("option", { value: item.model }, item.display_name || item.model)));
      const saved = state.settings.selection_translation_model || "gpt-6-luna";
      if (!models.some(item => item.model === saved)) select.append(el("option", { value: saved, disabled: true }, `${saved}（当前目录不可用）`));
      select.value = saved; select.disabled = !models.length;
      hint.textContent = models.length ? "仅用于右侧英文正文的即时划线翻译；不进入论文对话历史。" : "模型目录暂不可用，请刷新后重试。";
    };
    void loadModels().then(render).catch(error => { hint.textContent = error.message; });
    const save = button("保存划线翻译模型", event => act(async () => {
      const saved = await api("/api/settings", { method: "PATCH", body: { selection_translation_model: select.value } });
      Object.assign(state.settings, saved); hint.textContent = "已保存。"; toast("划线翻译模型已更新");
    }, event.currentTarget), "secondary");
    return el("section", { class: "settings-section" }, el("h3", {}, "划线翻译"),
      field("快速模型", select), hint, save);
  }
  function preferenceSelect(key, label, options) {
    const select = el("select", { dataset: { preference: key }, "aria-label": label, onchange: event => changePreference({ [key]: event.target.value }) }, options.map(([value, name]) => el("option", { value }, name)));
    select.value = state.settings[key] ?? DEFAULT_PREFERENCES[key];
    return el("div", { class: "preference-control" }, el("label", {}, label), select);
  }
  function preferenceRange(key, label, min, max) {
    return el("div", { class: "preference-control" }, el("label", {}, label), el("input", { type: "range", min, max, step: 1, value: state.settings[key] ?? DEFAULT_PREFERENCES[key], dataset: { preference: key }, "aria-label": label, oninput: event => changePreference({ [key]: Number(event.target.value) }) }), el("output", { dataset: { preferenceOutput: key } }, `${state.settings[key]} px`));
  }
  const fonts = [["system", "系统字体"], ["sans", "无衬线字体"], ["serif", "衬线字体（宋体）"]];
  const themes = el("div", { class: "theme-grid" }, [["paper", "纸白"], ["cream", "暖米"], ["sage", "浅绿"], ["graphite", "石墨"]].map(([value, name]) => el("button", { type: "button", class: `theme-choice ${state.settings.theme === value ? "selected" : ""}`, dataset: { themeChoice: value }, "aria-pressed": state.settings.theme === value, onclick: () => changePreference({ theme: value }) }, el("span", { class: "theme-swatch", "aria-hidden": "true" }), name)));
  const sync = el("input", { type: "checkbox", checked: state.settings.reader_sync !== false, dataset: { preference: "reader_sync" }, "aria-label": "中英阅读同步滚动", onchange: event => changePreference({ reader_sync: event.target.checked }) });
  const panels = [
    ["appearance", "外观", el("div", {}, el("h3", {}, "阅读主题"), themes, el("section", { class: "settings-section" }, el("h3", {}, "期刊等级"), el("p", { class: "preference-section-note" }, "2025 年固定名单：顶级为金色，重要为柔和蓝色，未列入名单的期刊为森林绿；会议为暖灰色，预印本为白色。"), el("a", { href: "/static/journal_tiers_2025.csv", target: "_blank", rel: "noopener noreferrer" }, "查看固定期刊名单 ↗")))],
    ["reading", "阅读与字体", el("div", {}, el("h3", {}, "字体与字号"), preferenceSelect("ui_font", "界面字体", fonts), preferenceRange("ui_font_size", "界面字号", 14, 22), preferenceSelect("reader_font", "中文阅读字体", fonts), preferenceRange("reader_font_size", "中文阅读字号", 14, 28), el("div", { class: "preference-preview" }, "研究方法、实验条件与主要结果，应结合原文核对。"), el("section", { class: "settings-section" }, el("h3", {}, "对照阅读"), el("label", { class: "preference-control" }, "中英阅读同步滚动", sync), el("p", { class: "preference-section-note" }, "在阅读器中拖动中间分隔线调整宽度。PDF 缩放单独保存，不受中文字号影响。")))],
    ["translation", "翻译", el("div", {}, defaultTranslationPanel(), quickTranslationPanel(), el("details", { class: "settings-disclosure" }, el("summary", {}, "连接与账号共享用量"), el("div", { class: "status-panel", id: "settings-codex-status" }, codexStatusPanel()), button("刷新连接状态", event => act(async () => { await refreshStatus(); $("#settings-codex-status")?.replaceChildren(codexStatusPanel()); }, event.currentTarget)), el("div", { class: "usage-panel", id: "settings-usage" }, usagePanel())), el("p", { class: "external-content-note small muted" }, "主动开始问答、全文或划线翻译、速览与团队查询时，相关论文内容会发送给已登录的 Codex。原文和已保存内容可在本地阅读。"))],
    ["data", "文献与备份", el("div", {}, el("h3", {}, "本地文献库"), el("p", { class: "settings-path" }, state.status?.data_dir || "本机文献库"), el("p", { class: "preference-section-note" }, "完整备份包含原 PDF、插图、译文和笔记。"), el("div", { class: "settings-data-actions" }, el("a", { href: "/api/export", class: "button secondary", download: "EzRead-library-backup.zip" }, icon("download"), "导出完整备份"), button("查看回收站", event => act(showTrash, event.currentTarget), "secondary", "trash")), el("div", { id: "trash-list" }))],
    ["about", "关于", el("div", { class: "about-ezread" }, el("img", { src: "/static/ezread-icon.png", width: "84", height: "84", alt: "EzRead" }), el("h3", {}, "EzRead"), el("p", { class: "small muted" }, `版本 ${state.status?.version || "3.1.1"} · 本地论文阅读器`))]
  ];
  const nav = el("div", { class: "settings-tabs", role: "tablist", "aria-label": "设置分类" });
  const body = el("div", { class: "settings-panels" });
  function selectPanel(id, focus = false) {
    $$("[role=tab]", nav).forEach(tab => { const selected = tab.dataset.panel === id; tab.setAttribute("aria-selected", String(selected)); tab.tabIndex = selected ? 0 : -1; if (selected && focus) tab.focus(); });
    $$("[role=tabpanel]", body).forEach(panel => { panel.hidden = panel.id !== `settings-panel-${id}`; });
    $("#settings-dialog").scrollTop = 0;
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
  const footer = el("div", { class: "preferences-footer" }, el("span", { class: "save-hint", id: "preference-save-status", role: "status" }, "外观与阅读设置自动保存"), button("重试保存", () => act(flushPreferences)), button("恢复外观与阅读默认值", () => changePreference({ ...DEFAULT_PREFERENCES })));
  $("#settings-content").replaceChildren(el("div", { class: "dialog-heading" }, el("h2", {}, "设置"), iconButton("close", "关闭设置", () => { void flushPreferences(); closeDialog("#settings-dialog"); })), nav, body, footer);
  $("#settings-dialog").classList.add("settings-dialog-wide"); openDialog("#settings-dialog"); selectPanel("appearance"); syncPreferenceControls(); void loadUsage(false);
}
