function currentPreferences() { return Object.fromEntries(Object.keys(DEFAULT_PREFERENCES).map(key => [key, state.settings[key] ?? DEFAULT_PREFERENCES[key]])); }
function applyPreferences() {
  const p = currentPreferences(), root = document.documentElement;
  root.dataset.theme = ["paper", "cream", "sage", "graphite"].includes(p.theme) ? p.theme : "paper";
  root.dataset.pageHidden = String(document.hidden);
  root.style.setProperty("--ui-font", FONT_FAMILIES[p.ui_font] || FONT_FAMILIES.system);
  root.style.setProperty("--reader-font", FONT_FAMILIES[p.reader_font] || FONT_FAMILIES.system);
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
}
function preferenceMessage(message, failed = false) { const node = $("#preference-save-status"); if (node) { node.textContent = message; node.classList.toggle("error-text", failed); } }
function changePreference(patch) {
  Object.assign(state.settings, patch); Object.assign(pendingPreferences, patch); applyPreferences(); preferenceMessage("正在保存…"); clearTimeout(preferenceTimer); preferenceTimer = setTimeout(flushPreferences, 300);
}
async function flushPreferences() {
  clearTimeout(preferenceTimer); if (preferenceSaveBusy || !Object.keys(pendingPreferences).length) return;
  preferenceSaveBusy = true; let failed = false; const patch = pendingPreferences; pendingPreferences = {};
  try {
    const saved = await api("/api/settings", { method: "PATCH", body: patch });
    const preview = currentPreferences(); state.settings = { ...state.settings, ...saved, ...preview };
    preferenceMessage("已自动保存");
  } catch (error) { failed = true; pendingPreferences = { ...patch, ...pendingPreferences }; preferenceMessage(`保存失败：${error.message}。请点击重试。`, true); }
  finally { preferenceSaveBusy = false; }
  if (!failed && Object.keys(pendingPreferences).length) preferenceTimer = setTimeout(flushPreferences, 100);
}
