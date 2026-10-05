// Current keys live here; previous brands are read only while migrating a value.
const BROWSER_SETTINGS = Object.freeze({
  preferences: { key: "ezread-preferences", previous: ["readx-preferences"], valid: value => {
    try { const parsed = JSON.parse(value); return parsed !== null && typeof parsed === "object" && !Array.isArray(parsed); } catch { return false; }
  } },
  sort: { key: "ezread-sort", previous: ["readx-sort", "tudu-sort"], valid: value => ["recent", "imported", "year"].includes(value) },
  direction: { key: "ezread-sort-direction", previous: [], valid: value => ["asc", "desc"].includes(value) },
});

function removePreviousBrowserSettings(storage, spec) {
  for (const key of spec.previous) { try { storage.removeItem(key); } catch {} }
}

function readBrowserSetting(kind, storage) {
  const spec = BROWSER_SETTINGS[kind];
  let value = null;
  try {
    storage = storage || localStorage;
    value = storage.getItem(spec.key);
    if (spec.valid(value)) { removePreviousBrowserSettings(storage, spec); return value; }
    value = null;
    for (const key of spec.previous) {
      const previous = storage.getItem(key);
      if (spec.valid(previous)) { value = previous; break; }
    }
    if (value !== null) {
      storage.setItem(spec.key, value);
      // Keep old values if the browser refuses or does not retain the write.
      if (storage.getItem(spec.key) === value) removePreviousBrowserSettings(storage, spec);
    }
  } catch {}
  return value;
}

function writeBrowserSetting(kind, value, storage) {
  const spec = BROWSER_SETTINGS[kind];
  if (!spec.valid(value)) return false;
  try {
    storage = storage || localStorage;
    storage.setItem(spec.key, value);
    if (storage.getItem(spec.key) !== value) return false;
    removePreviousBrowserSettings(storage, spec);
    return true;
  } catch { return false; }
}
