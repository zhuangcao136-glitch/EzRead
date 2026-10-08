"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const context = {};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, "../static/core/storage.js"), "utf8"), context);

function fixture(values = {}) {
  const entries = new Map(Object.entries(values));
  return { entries, failWrite: false, failRead: false, dropWrite: false,
    getItem(key) { if (this.failRead) throw Error("blocked"); return entries.get(key) ?? null; },
    setItem(key, value) { if (this.failWrite) throw Error("quota"); if (!this.dropWrite) entries.set(key, value); },
    removeItem(key) { entries.delete(key); },
  };
}

const preferences = JSON.stringify({ theme: "sage", ui_font_size: 20 });
let storage = fixture({ "readx-preferences": preferences, "readx-sort": "year", "tudu-sort": "recent" });
assert.equal(context.readBrowserSetting("preferences", storage), preferences);
assert.equal(context.readBrowserSetting("sort", storage), "year");
assert.equal(storage.entries.get("ezread-preferences"), preferences);
assert.equal(storage.entries.get("ezread-sort"), "year");
assert.ok(!storage.entries.has("readx-preferences") && !storage.entries.has("readx-sort") && !storage.entries.has("tudu-sort"));

storage = fixture({ "ezread-sort": "imported", "readx-sort": "year" });
assert.equal(context.readBrowserSetting("sort", storage), "imported");
assert.ok(!storage.entries.has("readx-sort"));

storage = fixture({ "readx-sort": "year" }); storage.failWrite = true;
assert.equal(context.readBrowserSetting("sort", storage), "year");
assert.equal(storage.entries.get("readx-sort"), "year");
assert.ok(!storage.entries.has("ezread-sort"));

storage = fixture({ "readx-preferences": preferences }); storage.dropWrite = true;
assert.equal(context.readBrowserSetting("preferences", storage), preferences);
assert.equal(storage.entries.get("readx-preferences"), preferences);

storage = fixture({ "readx-sort": "year" }); storage.failRead = true;
assert.equal(context.readBrowserSetting("sort", storage), null);
assert.equal(storage.entries.get("readx-sort"), "year");

storage = fixture({ "readx-preferences": "[]", "readx-sort": "invalid", "tudu-sort": "recent" });
assert.equal(context.readBrowserSetting("preferences", storage), null);
assert.equal(context.readBrowserSetting("sort", storage), "recent");
assert.equal(storage.entries.get("ezread-sort"), "recent");

storage = fixture({ "readx-preferences": preferences }); storage.failWrite = true;
assert.equal(context.writeBrowserSetting("preferences", preferences, storage), false);
assert.equal(storage.entries.get("readx-preferences"), preferences);
storage.failWrite = false;
assert.equal(context.writeBrowserSetting("preferences", preferences, storage), true);
assert.ok(!storage.entries.has("readx-preferences"));

storage = fixture({ "ezread-sort-direction": "asc" });
assert.equal(context.readBrowserSetting("direction", storage), "asc");
assert.equal(context.writeBrowserSetting("sort", "invalid", storage), false);
assert.equal(storage.entries.get("ezread-sort-direction"), "asc");
storage = fixture({ "ezread-paper-chat-width": "616" });
assert.equal(context.readBrowserSetting("chatWidth", storage), "616");
assert.equal(context.writeBrowserSetting("chatWidth", "720.5", storage), true);
assert.equal(context.readBrowserSetting("chatWidth", storage), "720.5");
for (const width of ["", "NaN", "Infinity", "-100", "0", "99999", "616px"]) {
  assert.equal(context.writeBrowserSetting("chatWidth", width, storage), false);
}
storage.failWrite = true;
assert.equal(context.writeBrowserSetting("chatWidth", "800", storage), false);
assert.equal(context.readBrowserSetting("chatWidth", storage), "720.5");
context.document = { documentElement: { dataset: {}, style: { setProperty() {} } }, hidden: false, querySelectorAll: () => [] };
for (const file of ["state.js", "preferences.js"]) {
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../static/core", file), "utf8"), context);
}
for (const theme of ["paper", "sage", "graphite", "cream", "light", "unknown", null]) {
  context.localStorage = fixture(); context.themeUnderTest = theme;
  vm.runInContext("state.settings.theme = themeUnderTest; state.settings.ui_font_size = 20; applyPreferences();", context);
  const expected = ["paper", "sage", "graphite"].includes(theme) ? theme : "paper";
  assert.equal(context.document.documentElement.dataset.theme, expected);
  assert.equal(vm.runInContext("state.settings.theme", context), expected);
  const saved = JSON.parse(context.localStorage.entries.get("ezread-preferences"));
  assert.equal(saved.theme, expected);
  assert.equal(saved.ui_font_size, 20);
}
console.log("Browser storage: migration, write/read failures, supported themes and retired-theme fallback passed.");
