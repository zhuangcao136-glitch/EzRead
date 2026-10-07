"use strict";

// Exercise real model-picker source with an asynchronous API and controlled
// timers. A refresh from the settings page must finish visibly in both pickers.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
class Node {
  constructor(tag, attrs = {}, children = []) {
    this.tag = tag; this.children = children.flat(); this.textContent = attrs.text || "";
    this.value = attrs.value || ""; this.isConnected = true; this.disabled = false;
    this.classList = { toggle() {}, add() {} };
  }
  replaceChildren(...items) { this.children = items.flat(); }
  append(...items) { this.children.push(...items.flat()); }
  addEventListener() {}
}
const catalog = { status: "ready", refreshing: false, models: [{ model: "chosen-model", display_name: "Chosen model",
  default_reasoning_effort: "low", supported_reasoning_efforts: ["low", "high"] }], default_model_id: "chosen-model", error: "" };
const timers = new Map(), calls = [];
let nextTimer = 0;
const context = vm.createContext({
  el: (tag, attrs, ...children) => new Node(tag, attrs, children),
  field: (label, input) => new Node("field", {}, [input]),
  button: label => new Node("button", { text: label }),
  api: async url => { calls.push(url); return structuredClone({ ...catalog, refreshing: url.includes("refresh=1") }); },
  setTimeout: callback => { const id = ++nextTimer; timers.set(id, callback); return id; },
  clearTimeout: id => timers.delete(id),
  $: () => new Node("dialog"), console
});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../static/translation-ui.js"), "utf8"), context);
async function settle() { for (let i = 0; i < 12; i++) await Promise.resolve(); }
function runTimers() { const pending = [...timers.entries()]; timers.clear(); for (const [, callback] of pending) callback(); }
const message = picker => picker.root.children[1].children[0].textContent;
(async () => {
  const full = vm.runInContext("modelPicker({model:'chosen-model',reasoning_effort:'high'})", context);
  const selection = vm.runInContext("modelPicker({model:'chosen-model'})", context);
  await settle();
  assert.equal(calls.length, 1, "Initial reads should share the same request");
  assert.equal(timers.size, 0);
  await vm.runInContext("loadModels(true)", context); await settle();
  assert.equal(timers.size, 2, "Settings-triggered refresh must start completion polling in both existing pickers");
  assert.equal(full.ready(), true, "Existing catalog remains usable while refreshing");
  assert.match(message(full), /后台更新|正在刷新/);
  runTimers(); await settle();
  assert.equal(calls.length, 3, "Both completion polls share a single request");
  assert.equal(timers.size, 0, "Polling stops after the backend finishes");
  assert.equal(message(full), ""); assert.equal(message(selection), "");
  assert.equal(full.value().reasoning_effort, "high", "Refresh must preserve user's reasoning choice");
  await vm.runInContext("loadModels(true)", context); await settle();
  full.dispose(); selection.dispose();
  assert.equal(timers.size, 0, "Closing the settings panel must cancel completion polling");
  const count = calls.length; runTimers(); await settle(); assert.equal(calls.length, count);
  console.log("Model picker: external refresh completion, cached choices, shared polling and disposal passed.");
})().catch(error => { console.error(error); process.exitCode = 1; });
