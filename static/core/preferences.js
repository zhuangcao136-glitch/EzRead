function currentPreferences() { return Object.fromEntries(Object.keys(DEFAULT_PREFERENCES).map(key => [key, state.settings[key] ?? DEFAULT_PREFERENCES[key]])); }
function applyPreferences() {
  const p = currentPreferences(), root = document.documentElement;
  root.dataset.theme = ["paper", "cream", "sage", "graphite"].includes(p.theme) ? p.theme : "paper";
  root.dataset.pageHidden = String(document.hidden);
  root.style.setProperty("--ui-font-size", `${Math.max(14, Math.min(22, Number(p.ui_font_size) || 16))}px`);
  root.style.setProperty("--reader-font-size", `${Math.max(14, Math.min(28, Number(p.reader_font_size) || 18))}px`);
  writeBrowserSetting("preferences", JSON.stringify(p));
  syncPreferenceControls();
  if (typeof applyReaderPreferences === "function") applyReaderPreferences();
}
function syncPreferenceControls() {
  $$('[data-preference]').forEach(control => { const key = control.dataset.preference; if (control.type === "checkbox") control.checked = Boolean(state.settings[key]); else control.value = state.settings[key] ?? DEFAULT_PREFERENCES[key]; });
  $$('[data-preference-output]').forEach(node => { node.textContent = `${state.settings[node.dataset.preferenceOutput]} px`; });
  $$('[data-theme-choice]').forEach(node => { const chosen = node.dataset.themeChoice === state.settings.theme; node.classList.toggle("selected", chosen); node.setAttribute("aria-pressed", String(chosen)); });
  if (typeof syncSelectMenus === "function") syncSelectMenus();
}
function preferenceMessage(message, failed = false, keys = []) {
  for (const node of [$("#preference-save-status"), ...$$("[data-preference-save-status]")]) {
    if (!node || node.dataset.preferenceSaveStatus && !node.dataset.preferenceSaveStatus.split(" ").some(key => keys.includes(key))) continue;
    node.textContent = message; node.classList.toggle("error-text", failed);
    const retry = node.parentElement?.querySelector("[data-preference-retry]"); if (retry) retry.hidden = !failed;
  }
}
function preferenceSaveHint(keys) {
  const hint = el("p", { class: "save-hint", role: "status", dataset: { preferenceSaveStatus: keys.join(" ") } });
  return el("div", {}, hint, el("div", { hidden: true, dataset: { preferenceRetry: "" } }, button("重试保存", () => void flushPreferences(), "secondary")));
}
function changePreference(patch, immediate = false) {
  Object.assign(state.settings, patch); Object.assign(pendingPreferences, patch); applyPreferences(); preferenceMessage("正在保存…", false, Object.keys(patch)); clearTimeout(preferenceTimer);
  if (immediate) void flushPreferences(); else preferenceTimer = setTimeout(flushPreferences, 300);
}
async function flushPreferences() {
  clearTimeout(preferenceTimer); if (preferenceSaveBusy || !Object.keys(pendingPreferences).length) return;
  preferenceSaveBusy = true; let failed = false; const patch = pendingPreferences; pendingPreferences = {};
  preferenceMessage("正在保存…", false, Object.keys(patch));
  try {
    const saved = await api("/api/settings", { method: "PATCH", body: patch });
    const preview = { ...currentPreferences(), ...pendingPreferences }; state.settings = { ...state.settings, ...saved, ...preview };
    preferenceMessage("已自动保存", false, Object.keys(patch).filter(key => !Object.hasOwn(pendingPreferences, key)));
  } catch (error) { failed = true; pendingPreferences = { ...patch, ...pendingPreferences }; preferenceMessage(`保存失败：${error.message}`, true, Object.keys(patch)); }
  finally { preferenceSaveBusy = false; }
  if (!failed && Object.keys(pendingPreferences).length) preferenceTimer = setTimeout(flushPreferences, 100);
}
