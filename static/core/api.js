function toast(message, kind = "success", duration = 4500) {
  const node = el("div", { class: `toast ${kind}`, text: asText(message) });
  syncToastLayer(true); $("#toasts").append(node);
  setTimeout(() => { node.remove(); syncToastLayer(); }, duration);
}
function overlayHost(target = document.activeElement) {
  return target?.closest("dialog:modal") || document.elementFromPoint(0, 0)?.closest("dialog:modal") || document.body;
}
function syncToastLayer(showEmpty = false) {
  const region = $("#toasts"); if (!region) return;
  if (region.matches(":popover-open")) region.hidePopover();
  if (!region.childElementCount && !showEmpty) {
    if (region.parentElement !== document.body) document.body.append(region);
    return;
  }
  // Modal siblings are inert. Keep the live region inside the active modal,
  // then raise its non-modal popover above the latest dialog or popup.
  const host = overlayHost();
  if (region.parentElement !== host) host.append(region);
  region.showPopover();
}
function installToastLayer() {
  let frame;
  // Native toggle callbacks can run before showPopover has finished. Wait until
  // the next frame, and coalesce changes, before raising the notification layer.
  const schedule = () => {
    if (frame !== undefined) return;
    frame = requestAnimationFrame(() => { frame = undefined; syncToastLayer(); });
  };
  document.addEventListener("beforetoggle", event => {
    if (event.target.id !== "toasts" && event.target.matches("dialog,[popover]")) schedule();
  }, true);
  document.addEventListener("close", event => { if (event.target.matches("dialog")) schedule(); }, true);
}
async function api(path, options = {}) {
  const headers = { ...options.headers };
  if (options.body && !(options.body instanceof FormData)) { headers["Content-Type"] = "application/json"; options.body = JSON.stringify(options.body); }
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  let data; try { data = await response.json(); } catch { throw new Error(response.ok ? "服务返回了无法识别的内容" : `请求失败（${response.status}）`); }
  if (!response.ok) throw new Error(asText(data.error || data.detail || data.message) || `请求失败（${response.status}）`);
  return data;
}
async function apiStream(path, body, onEvent) {
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body), cache: "no-store" });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(asText(data.error) || `请求失败（${response.status}）`);
  }
  if (!response.body || !response.headers.get("Content-Type")?.includes("application/x-ndjson")) {
    throw new Error("问答服务版本不匹配，请重新启动 EzRead 后重试。");
  }
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let buffer = "", completed = false;
  const deliver = line => {
    if (!line.trim()) return;
    const event = JSON.parse(line);
    if (event.type === "error") throw new Error(event.error || "本次回答未完成");
    if (event.type === "completed") completed = true;
    onEvent(event);
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let end;
      while ((end = buffer.indexOf("\n")) >= 0) { deliver(buffer.slice(0, end)); buffer = buffer.slice(end + 1); }
      if (done) break;
    }
    if (buffer.trim()) deliver(buffer);
    if (!completed) throw new Error("回答连接已中断，请重新打开论文对话核对保存结果。");
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}
async function act(task, element) {
  if (element) element.disabled = true;
  try { return await task(); } catch (error) { toast(error.message || "操作未完成，请重试", "error", 6500); return undefined; }
  finally { if (element && element.isConnected) element.disabled = false; }
}
function normalizeDetail(data) {
  const p = data.paper ? { ...data.paper } : { ...data };
  for (const key of ["blocks", "pages", "figures"]) p[key] = data[key] || p[key] || [];
  return p;
}
function updatePaper(patch) {
  if (!patch || !patch.id) return;
  const index = state.papers.findIndex(p => p.id === patch.id);
  if (index >= 0) state.papers[index] = { ...state.papers[index], ...patch }; else state.papers.push(patch);
  if (state.detail?.id === patch.id) state.detail = { ...state.detail, ...patch };
  if (state.reader?.id === patch.id) { state.reader = { ...state.reader, ...patch }; updateReaderProgress(); }
}
async function patchPaper(id, patch) {
  const data = await api(`/api/papers/${encodeURIComponent(id)}`, { method: "PATCH", body: patch });
  updatePaper({ id, ...patch, ...(data.paper || (data.id ? data : {})) }); renderLibrary(); return data;
}
function openDialog(id) { const d = $(id); if (!d.open) { d.showModal(); syncToastLayer(); } }
function closeDialog(id) { if (id === "#reader-dialog") return closeReader(); if (id === "#detail-dialog") return act(async () => { await flushDetailNotes(); $(id).close(); }); $(id).close(); }
