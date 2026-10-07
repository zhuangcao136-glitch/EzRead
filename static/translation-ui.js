"use strict";

// The model catalog is returned by the authenticated official CLI, never hard-coded.
let modelCatalog = null, modelRequest = null;
const modelCatalogListeners = new Set();
const EFFORT_NAMES = { none: "不启用", minimal: "最低", low: "低", medium: "中", high: "高", xhigh: "更高", max: "最高", ultra: "极高" };
function effortLabel(value) { return value ? `${EFFORT_NAMES[value] || value} · ${value}` : "模型默认"; }
function configLabel(config) { return config?.model ? `${config.model} · 推理 ${effortLabel(config.reasoning_effort)}` : "历史模型未知"; }
async function loadModels(refresh = false) {
  if (modelRequest) return modelRequest;
  modelRequest = api(`/api/models${refresh ? "?refresh=1" : ""}`).then(value => { modelCatalog = value; for (const render of modelCatalogListeners) render(); return value; }).finally(() => { modelRequest = null; });
  return modelRequest;
}
function modelPicker(initial = {}, options = {}) {
  const model = el("select", { "aria-label": options.modelLabel || "翻译模型" }), effort = el("select", { "aria-label": options.effortLabel || "推理强度" });
  const message = el("p", { class: "model-catalog-status small muted", role: "status" }, "正在读取模型目录…");
  const refresh = button(options.refreshLabel || "刷新模型目录", () => void update(true), "secondary");
  const root = el("div", { class: "model-picker" }, el("div", { class: "inline-fields" }, field("模型", model), field("推理强度", effort)), el("div", { class: "model-catalog-footer" }, message, options.showRefresh === false ? null : refresh));
  let desiredModel = initial.model || "", desiredEffort = initial.reasoning_effort || "", timer = null, disposed = false, disabled = false;
  function updateEfforts() {
    const entry = (modelCatalog?.models || []).find(x => x.model === (model.value || modelCatalog.default_model_id));
    effort.replaceChildren(el("option", { value: "" }, entry?.default_reasoning_effort ? `模型默认 · ${effortLabel(entry.default_reasoning_effort)}` : "模型默认"), ...(entry?.supported_reasoning_efforts || []).map(value => el("option", { value }, effortLabel(value))));
    if (desiredEffort && !entry?.supported_reasoning_efforts?.includes(desiredEffort)) effort.append(el("option", { value: desiredEffort, disabled: true }, `${desiredEffort}（当前模型不支持）`));
    effort.value = desiredEffort;
    effort.disabled = disabled || !entry;
  }
  function render() {
    const entries = modelCatalog?.models || [];
    model.replaceChildren(el("option", { value: "" }, modelCatalog?.default_model_id ? `Codex 默认 · ${modelCatalog.default_model_id}` : "Codex 默认"), ...entries.map(entry => el("option", { value: entry.model }, entry.display_name || entry.model)));
    if (desiredModel && !entries.some(x => x.model === desiredModel)) model.append(el("option", { value: desiredModel, disabled: true }, `${desiredModel}（目录中暂不可用）`));
    model.value = desiredModel; model.disabled = disabled || !entries.length; updateEfforts();
    message.textContent = modelCatalog?.error || (modelCatalog?.refreshing ? "正在刷新模型目录…" : modelCatalog?.status === "stale" ? "当前为缓存目录" : entries.length ? "" : "模型目录暂不可用，请刷新后重试。" );
    message.classList.toggle("error-text", !entries.length && !modelCatalog?.refreshing);
  }
  model.addEventListener("change", () => { desiredModel = model.value; desiredEffort = ""; updateEfforts(); options.onChange?.({ model: desiredModel, reasoning_effort: desiredEffort }); });
  effort.addEventListener("change", () => { desiredEffort = effort.value; options.onChange?.({ model: desiredModel, reasoning_effort: desiredEffort }); });
  async function update(force = false) {
    if (disposed) return;
    refresh.disabled = true;
    try { await loadModels(force); if (disposed) return; render(); if (modelCatalog?.refreshing) { clearTimeout(timer); timer = setTimeout(() => { if (root.isConnected) void update(); }, 1200); } }
    catch (error) { message.textContent = `模型目录读取失败：${error.message}`; message.classList.add("error-text"); }
    finally { refresh.disabled = false; }
  }
  if (modelCatalog) render();
  modelCatalogListeners.add(render);
  void update();
  return { root, value: () => ({ model: model.value, reasoning_effort: effort.value }), setConfig(value) { desiredModel = value.model || ""; desiredEffort = value.reasoning_effort || ""; render(); }, disable(value) { disabled = value; render(); }, ready: () => Boolean(modelCatalog?.models?.length), dispose() { disposed = true; clearTimeout(timer); modelCatalogListeners.delete(render); } };
}

function defaultTranslationPanel(selection = false) {
  const modelKey = selection ? "selection_translation_model" : "translation_model";
  const effortKey = selection ? "selection_translation_reasoning_effort" : "translation_reasoning_effort";
  const picker = modelPicker({ model: state.settings[modelKey], reasoning_effort: state.settings[effortKey] }, {
    modelLabel: selection ? "划线翻译模型" : "翻译模型", effortLabel: selection ? "划线翻译推理强度" : "推理强度",
    showRefresh: false,
    onChange: value => { changePreference({ [modelKey]: value.model, [effortKey]: value.reasoning_effort }, true); renderLibrary(); }
  });
  $("#settings-dialog").addEventListener("close", () => picker.dispose(), { once: true });
  return el("section", { class: `settings-section ${selection ? "selection-translation-defaults" : "translation-defaults"}` },
    el("h3", {}, selection ? "划线翻译" : "全文翻译默认配置"), picker.root, preferenceSaveHint([modelKey, effortKey]));
}

function openPaperModel(p) {
  const locked = p.paper_ai || {};
  const picker = modelPicker({ model: locked.model || state.settings.translation_model,
                              reasoning_effort: locked.reasoning_effort || state.settings.translation_reasoning_effort });
  const note = el("p", { class: "small muted" }, locked.model
    ? `当前：${configLabel(locked)}` : "模型尚未固定");
  const save = button("应用于本篇论文", event => act(async () => {
    if (!picker.ready()) throw new Error("模型目录尚不可用，请刷新后重试。");
    const result = await api(`/api/papers/${encodeURIComponent(p.id)}/ai/model`,
      { method: "POST", body: picker.value() });
    const target = state.papers.find(item => item.id === p.id);
    if (target) target.paper_ai = result.paper_ai;
    if (state.reader?.id === p.id) state.reader.paper_ai = result.paper_ai;
    renderLibrary(); closeDialog("#translation-dialog");
    toast(result.paper_ai.generation > (locked.generation || 1) ? "已新建论文对话" : "论文模型已固定");
  }, event.currentTarget), "primary");
  $("#translation-content").replaceChildren(el("div", { class: "dialog-heading" },
    el("h2", {}, "本篇论文的模型"), iconButton("close", "关闭", () => closeDialog("#translation-dialog"))),
    el("p", { class: "translation-paper-title" }, titleOf(p)), note, picker.root,
    el("div", { class: "form-actions" }, button("取消", () => closeDialog("#translation-dialog")), save));
  openDialog("#translation-dialog");
  $("#translation-dialog").addEventListener("close", () => picker.dispose(), { once: true });
}

function openTranslationSetup(papers) {
  const ids = papers.map(p => p.id), single = papers.length === 1 ? papers[0] : null;
  const existing = papers.some(p => translationOf(p).config || translationOf(p).done || DONE_STATUSES.has(translationOf(p).status) || cardTranslationLabel(p) === "已翻译");
  const completed = single && cardTranslationLabel(single) === "已翻译";
  const picker = modelPicker({ model: single?.paper_ai?.model || state.settings.translation_model,
    reasoning_effort: single?.paper_ai?.reasoning_effort || state.settings.translation_reasoning_effort });
  const retranslate = el("input", { type: "checkbox", checked: Boolean(completed), "aria-label": "使用新配置整篇重新翻译" });
  const hint = el("p", { class: "translation-choice-hint small muted" });
  const start = button("加入翻译队列", event => act(async () => {
    const value = picker.value();
    if ((!existing || retranslate.checked) && !picker.ready()) throw new Error("模型目录尚不可用，请先刷新。");
    const errors = []; let added = 0, skipped = 0;
    for (const id of ids) {
      const p = state.papers.find(item => item.id === id), t = translationOf(p || {});
      if (ACTIVE_STATUSES.has(t.status)) { errors.push("正在运行的任务需先暂停"); continue; }
      if (cardTranslationLabel(p) === "已翻译" && !retranslate.checked) { skipped++; continue; }
      const preserve = Boolean(t.config) && !retranslate.checked;
      try {
        const selected = p?.paper_ai?.model ? { model: p.paper_ai.model,
          reasoning_effort: p.paper_ai.reasoning_effort } : value;
        const payload = preserve ? {} : { ...selected, ...(retranslate.checked ? { retranslate: true } : {}) };
        await api(`/api/papers/${encodeURIComponent(id)}/translate`, { method: "POST", body: payload }); added++;
      } catch (error) { errors.push(error.message); }
    }
    await loadLibrary();
    if (single && state.detail?.id === single.id && $("#detail-dialog").open) { state.detail = normalizeDetail(await api(`/api/papers/${encodeURIComponent(single.id)}`)); renderDetail(); }
    if (state.reader && ids.includes(state.reader.id)) await refreshReader();
    if (!errors.length && added) closeDialog("#translation-dialog");
    else if (!errors.length && skipped) hint.textContent = "所选论文已译完";
    else hint.textContent = `${errors.length} 篇未加入：${errors[0]}`;
    if (added) toast(`已加入翻译队列：${added} 篇${skipped ? `，跳过已译完 ${skipped} 篇` : ""}`);
    if (errors.length) toast(errors[0], "error", 7000);
  }, event.currentTarget), "primary", "translate");
  let previousResumeMode = null;
  function updateMode() {
    const resumeOnly = single && translationOf(single).config && !retranslate.checked;
    if (previousResumeMode !== Boolean(resumeOnly)) picker.setConfig(resumeOnly ? translationOf(single).config : single?.paper_ai?.model
      ? { model: single.paper_ai.model, reasoning_effort: single.paper_ai.reasoning_effort }
      : { model: state.settings.translation_model, reasoning_effort: state.settings.translation_reasoning_effort });
    previousResumeMode = Boolean(resumeOnly);
    picker.disable(Boolean(resumeOnly || single?.paper_ai?.model));
    start.textContent = retranslate.checked ? "确认整篇重译" : existing ? "确认继续翻译" : "确认开始全文翻译";
    hint.textContent = "";
  }
  retranslate.addEventListener("change", updateMode);
  const content = el("div", {}, el("div", { class: "dialog-heading" }, el("h2", {}, single ? "确认全文翻译" : `确认批量翻译 · ${papers.length} 篇`), iconButton("close", "关闭翻译设置", () => closeDialog("#translation-dialog"))), single ? el("p", { class: "translation-paper-title" }, titleOf(single)) : null, single && existing ? el("p", { class: "small muted" }, `原任务配置：${configLabel(translationOf(single).config)}`) : null, existing ? el("label", { class: "translation-mode-choice" }, retranslate, "使用新配置整篇重新翻译") : null, hint, picker.root, el("div", { class: "form-actions" }, button("取消", () => closeDialog("#translation-dialog")), start));
  $("#translation-content").replaceChildren(content); updateMode(); openDialog("#translation-dialog");
  $("#translation-dialog").addEventListener("close", () => picker.dispose(), { once: true });
}

async function openTranslationVersions(p) {
  const data = await api(`/api/papers/${encodeURIComponent(p.id)}/translation-versions`);
  const versions = data.versions || [];
  const body = el("div", {}, el("div", { class: "dialog-heading" }, el("h2", {}, "译文版本"), iconButton("close", "关闭译文版本", () => closeDialog("#translation-dialog"))));
  if (!versions.length) body.append(el("p", { class: "no-results" }, "尚无历史译文"));
  for (const version of versions) body.append(el("div", { class: "translation-version" }, el("div", {}, el("strong", {}, configLabel(version.config)), el("p", { class: "small muted" }, `${version.created_at ? new Date(version.created_at).toLocaleString("zh-CN") : "历史版本"} · ${version.block_count || 0} 段${version.current ? " · 当前" : ""}`)), !version.current ? button("恢复此版本", event => act(async () => {
    await api(`/api/papers/${encodeURIComponent(p.id)}/translation-restore`, { method: "POST", body: { version_id: version.id } });
    await loadLibrary(); if (state.reader?.id === p.id) await refreshReader();
    if (state.detail?.id === p.id && $("#detail-dialog").open) { state.detail = normalizeDetail(await api(`/api/papers/${encodeURIComponent(p.id)}`)); renderDetail(); }
    closeDialog("#translation-dialog"); toast("已恢复译文版本");
  }, event.currentTarget), "secondary") : null));
  if (data.drafts?.length) body.append(el("h3", { class: "settings-section" }, "未完成的重译草稿"));
  for (const draft of data.drafts || []) body.append(el("div", { class: "translation-version" }, el("div", {}, el("strong", {}, configLabel(draft.config)), el("p", { class: "small muted" }, `${draft.block_count || 0} 段已保存 · 尚未完成`)), button("恢复为待续译任务", event => act(async () => {
    await api(`/api/papers/${encodeURIComponent(p.id)}/translation-resume-draft`, { method: "POST", body: { draft_id: draft.id } });
    await loadLibrary();
    if (state.reader?.id === p.id) await refreshReader();
    if (state.detail?.id === p.id && $("#detail-dialog").open) { state.detail = normalizeDetail(await api(`/api/papers/${encodeURIComponent(p.id)}`)); renderDetail(); }
    closeDialog("#translation-dialog"); toast("重译草稿已恢复");
  }, event.currentTarget))));
  $("#translation-content").replaceChildren(body); openDialog("#translation-dialog");
}
