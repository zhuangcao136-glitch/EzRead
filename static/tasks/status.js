let usageRequest = null;
async function refreshStatus(force = false) {
  state.status = await api(`/api/status${force ? "?refresh=1" : ""}`); if (state.status.usage) state.usage = state.status.usage;
  $("#settings-codex-status")?.replaceChildren(codexStatusPanel()); renderQueue(); renderUsagePanels(); return state.status;
}
function windowLabel(window) {
  const minutes = Number(window.window_minutes);
  if (Number.isFinite(minutes) && minutes > 0) return minutes % 1440 === 0 ? `${minutes / 1440} 天窗口` : minutes % 60 === 0 ? `${minutes / 60} 小时窗口` : `${minutes} 分钟窗口`;
  return window.kind === "primary" ? "主要用量窗口" : window.kind === "secondary" ? "附加用量窗口" : "用量窗口";
}
function timeLabel(value, seconds = false) { if (value === null || value === undefined || value === "") return "未知"; const date = new Date(seconds ? Number(value) * 1000 : value); return Number.isNaN(date.getTime()) ? "未知" : date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }); }
function percentage(value) { return typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : null; }
function usagePanel(showRefresh = true) {
  const usage = state.usage, status = usage?.status || "loading";
  const panel = el("div", { class: status === "stale" ? "usage-stale" : "" }, el("div", { class: "usage-heading" }, el("h3", {}, "账户共享 Codex 用量"), showRefresh ? button(usageBusy || usage?.refreshing ? "正在刷新…" : "刷新用量", event => act(() => loadUsage(true), event.currentTarget), "secondary", "clock", { disabled: usageBusy || usage?.refreshing }) : null));
  if (!usage || status === "loading" && !usage.buckets?.length) panel.append(el("p", { class: "usage-note" }, "正在读取用量…"));
  if (status === "stale") panel.append(el("p", { class: "usage-note" }, "当前为上次成功读取的用量记录"));
  if (status === "unavailable") panel.append(el("p", { class: "usage-note" }, "暂时无法读取用量"));
  if (usage?.error) panel.append(el("p", { class: "error-text" }, usage.error));
  for (const bucket of usage?.buckets || []) {
    const windows = (bucket.windows || []).map(window => {
      const used = percentage(window.used_percent), providedRemaining = percentage(window.remaining_percent), remaining = providedRemaining !== null ? providedRemaining : used !== null ? 100 - used : null;
      const label = value => value === null ? "—" : `${Number(value.toFixed(1))}%`;
      return el("div", { class: "usage-window" }, el("div", { class: "usage-window-title" }, windowLabel(window)), el("div", { class: "usage-numbers" }, el("span", {}, `已用 ${label(used)}`), el("strong", {}, `剩余 ${label(remaining)}`)), used !== null ? el("div", { class: "progress-track", role: "progressbar", "aria-label": `${windowLabel(window)}已用`, "aria-valuenow": used, "aria-valuemin": 0, "aria-valuemax": 100 }, el("div", { class: "progress-fill", style: { width: `${used}%` } })) : null, el("div", { class: "usage-reset" }, window.resets_at ? `重置：${timeLabel(window.resets_at, true)}` : "未提供重置时间"));
    });
    const bucketName = bucket.name || bucket.id || "Codex";
    panel.append(el("section", { class: "usage-bucket" }, el("h4", {}, bucketName.toLowerCase() === "codex" ? "Codex" : bucketName), el("div", { class: "usage-windows" }, windows.length ? windows : el("p", { class: "usage-note" }, "此用量类别未返回可用窗口。"))));
  }
  if (usage?.ordinary_usage_allowed === false) panel.append(el("p", { class: "usage-note" }, "账户当前报告普通用量受限。"));
  if (usage) panel.append(el("p", { class: "usage-stamp" }, `${status === "stale" ? "上次成功更新" : "更新"}：${timeLabel(usage.updated_at)}${usage.checked_at ? ` · 最近检查：${timeLabel(usage.checked_at)}` : ""}`));
  return panel;
}
function renderUsagePanels() { ["#usage-status", "#settings-usage"].forEach(selector => { const node = $(selector); if (node) node.replaceChildren(usagePanel(selector !== "#settings-usage")); }); }
function loadUsage(force = false) {
  if (usageRequest) return usageRequest;
  usageBusy = true; renderUsagePanels();
  usageRequest = (async () => {
    try { state.usage = await api(`/api/usage${force ? "?refresh=1" : ""}`); }
    catch (error) { state.usage = { ...(state.usage || {}), status: state.usage?.buckets?.length ? "stale" : "unavailable", error: `读取用量失败：${error.message}` }; }
    finally { usageBusy = false; renderUsagePanels(); }
  })().finally(() => { usageRequest = null; });
  return usageRequest;
}
function codexStatusPanel() {
  const s = state.status?.codex;
  if (!s) return el("div", {}, el("h3", {}, "正在检查 Codex 登录状态…"));
  const ready = Boolean(s.available && s.authenticated);
  return el("div", {}, el("div", { style: { display: "flex", alignItems: "center", justifyContent: "space-between", gap: "15px", marginBottom: "8px" } }, el("h3", {}, "ChatGPT 订阅 · Codex"), el("span", { class: `connection-state ${ready ? "" : "offline"}` }, el("i", { class: "status-dot" }), ready ? "已连接" : s.available ? "需要登录" : "尚未就绪")), el("p", {}, ready ? "" : s.message || (s.available ? "Codex 尚未登录" : "Codex 不可用")), s.login_command ? el("pre", {}, s.login_command) : null);
}
function renderQueue() {
  const panel = $("#codex-status"); if (!panel) return; panel.replaceChildren(codexStatusPanel());
  const jobs = state.papers.filter(p => { const t = translationOf(p); return t.status && !["idle", "none"].includes(t.status) || t.done > 0 || p.summarize_status || p.team_status; });
  jobs.sort((a, b) => Number(ACTIVE_STATUSES.has(translationOf(b).status)) - Number(ACTIVE_STATUSES.has(translationOf(a).status)) || dateMs(b.updated_at) - dateMs(a.updated_at));
  $("#queue-list").replaceChildren(...(jobs.length ? jobs.map(p => {
    const t = translationOf(p), active = ACTIVE_STATUSES.has(t.status);
    const extras = [p.summarize_status ? `研究速览：${taskStatusName(p.summarize_status)}` : "", p.team_status ? `团队背景：${taskStatusName(p.team_status)}` : ""].filter(Boolean).join(" · ");
    return el("article", { class: "queue-item" }, imageNode(p.cover_url, { class: "queue-image", alt: "论文封面" }), el("div", { class: "queue-item-body" }, el("h3", {}, titleOf(p)), el("p", {}, `${t.mode === "retranslate" ? "重译 · " : ""}${statusName(t.status)} · 已保存 ${t.done || 0} / ${t.total || 0} 段 · ${Math.round(ratio(p))}%`), el("p", { class: "queue-model" }, `本次任务：${configLabel(t.config)}`), t.current_mixed ? el("p", { class: "small muted" }, "当前译文含历史或不同配置的内容。") : null, t.has_staging ? el("p", {}, "重译中 · 当前显示原译文") : null, el("div", { class: "progress-track" }, el("div", { class: "progress-fill", style: { width: `${ratio(p)}%` } })), extras ? el("p", { style: { marginTop: "5px" } }, extras) : null, t.error || p.summarize_error || p.team_error ? el("p", { class: "error-text", style: { marginTop: "6px" } }, asText(t.error || p.summarize_error || p.team_error)) : null), el("div", { class: "queue-actions" }, button("阅读", () => act(() => openReader(p.id)), "secondary", "book"), button(active ? "暂停" : DONE_STATUSES.has(t.status) ? "重新翻译" : "继续 / 配置", event => act(() => translateAction(p, active), event.currentTarget), active ? "secondary" : "subtle", active ? "pause" : "translate"), button("译文版本", event => act(() => openTranslationVersions(p), event.currentTarget), "secondary", "clock")));
  }) : [el("div", { class: "no-results" }, icon("translate"), el("h3", { style: { marginTop: "15px" } }, "还没有翻译任务"), button("浏览文献库", () => setView("all"), "subtle", "", { style: { marginTop: "18px" } }))]));
  renderUsagePanels();
}

async function showTrash() {
  const data = await api("/api/trash"), papers = data.papers || []; const container = $("#trash-list"); if (!container) return;
  container.replaceChildren(...(papers.length ? papers.map(p => el("div", { style: { borderTop: "1px solid var(--line)", padding: "10px 0", display: "flex", gap: "12px", alignItems: "center" } }, el("span", { class: "small", style: { flex: "1", minWidth: "0", overflowWrap: "anywhere" } }, titleOf(p)), button("恢复", event => act(async () => { await api(`/api/papers/${encodeURIComponent(p.id)}/restore`, { method: "POST", body: {} }); await loadLibrary(); await showTrash(); toast("论文已恢复"); }, event.currentTarget), "secondary"))) : [el("p", { class: "small muted" }, "回收站是空的。") ]));
}
async function createCollection(name) {
  if (!name.trim()) return;
  const collections = [...new Set([...allCollections(), name.trim()])];
  state.settings = await api("/api/settings", { method: "PATCH", body: { collections } }); state.collections = collections;
  $("#collection-name").value = ""; closeDialog("#collection-dialog"); setView("collection", name.trim()); toast("合集已创建");
}
