"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
let source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
source = source.slice(0, source.lastIndexOf("init().catch("));
function fixture(fail = false) {
  const calls = [], rendered = [], nodes = new Map();
  const context = vm.createContext({
    state: { settings: {}, loading: true }, DEFAULT_PREFERENCES: {},
    readBrowserSetting: () => null, applyPreferences() {}, loadUsage() {}, loadModels: async () => {},
    renderLibrary() { rendered.push(context.state.loading); }, list: value => value || [], toast() {},
    renderUsagePanels() {}, codexStatusPanel() {},
    $: selector => { if (!nodes.has(selector)) nodes.set(selector, { open: false, replaceChildren() {} }); return nodes.get(selector); },
    setInterval: () => 1,
    api: (url, options) => {
      calls.push({ url, options });
      if (url === "/api/selection-session") return fail ? Promise.reject(new Error("offline")) : new Promise(() => {});
      return Promise.resolve(url === "/api/papers" ? { papers: [], collections: [] } : {});
    }
  });
  vm.runInContext(source, context);
  vm.runInContext("installEvents = () => {}", context);
  return { context, calls, rendered };
}
(async () => {
  for (const fail of [false, true]) {
    const test = fixture(fail);
    const completed = await Promise.race([
      vm.runInContext("init()", test.context).then(() => true),
      new Promise(resolve => setTimeout(() => resolve(false), 100))
    ]);
    assert.equal(completed, true, "Pending or failed preparation must not block local page startup");
    assert.equal(test.context.state.loading, false);
    assert.ok(test.rendered.includes(false));
    void vm.runInContext("prepareSelectionSession()", test.context);
    const calls = test.calls.filter(call => call.url === "/api/selection-session");
    assert.equal(calls.length, 1, "Repeated calls within this page share the preparation request");
    assert.equal(calls[0].options.method, "POST");
    assert.deepEqual(Object.keys(calls[0].options.body), [], "Startup uses a server-owned fixed test without sending paper text");
  }
  console.log("Selection startup: silent background preparation starts once, while pending/failed connections leave the library usable.");
})().catch(error => { console.error(error); process.exitCode = 1; });
