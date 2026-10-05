function toast(message, kind = "success", duration = 4500) {
  const node = el("div", { class: `toast ${kind}`, text: asText(message) }); $("#toasts").append(node); setTimeout(() => node.remove(), duration);
}
async function api(path, options = {}) {
  const headers = { ...options.headers };
  if (options.body && !(options.body instanceof FormData)) { headers["Content-Type"] = "application/json"; options.body = JSON.stringify(options.body); }
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  let data; try { data = await response.json(); } catch { throw new Error(response.ok ? "服务返回了无法识别的内容" : `请求失败（${response.status}）`); }
  if (!response.ok) throw new Error(asText(data.error || data.detail || data.message) || `请求失败（${response.status}）`);
  return data;
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
function openDialog(id) { const d = $(id); if (!d.open) d.showModal(); }
function closeDialog(id) { if (id === "#reader-dialog") return closeReader(); if (id === "#detail-dialog") return act(async () => { await flushDetailNotes(); $(id).close(); }); $(id).close(); }
