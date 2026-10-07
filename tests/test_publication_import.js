"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
class Node {
  constructor(tag = "div", attrs = {}, children = []) { this.tag = tag; this.attrs = attrs; this.children = children.flat(Infinity).filter(Boolean); this.handlers = {}; this.open = false; this.disabled = !!attrs.disabled; }
  addEventListener(type, fn, options) { (this.handlers[type] ||= []).push({ fn, once: options?.once }); }
  removeEventListener(type, fn) { this.handlers[type] = (this.handlers[type] || []).filter(item => item.fn !== fn); }
  fire(type, event = {}) { for (const item of [...this.handlers[type] || []]) { item.fn(event); if (item.once) this.removeEventListener(type, item.fn); } }
  replaceChildren(...children) { this.children = children; }
  focus() {}
  close() { this.open = false; this.fire("close"); }
}
const dialog = new Node(), content = new Node(), close = new Node("button");
let calls = [], rendered = 0, updated, networkResolve;
const context = {
  list: v => Array.isArray(v) ? v : [], asText: v => Array.isArray(v) ? v.join("；") : String(v || ""),
  $: selector => selector === "#publication-dialog" ? dialog : selector === "#publication-content" ? content : selector === "#detail-dialog" ? { open: false } : close,
  el: (tag, attrs = {}, ...children) => new Node(tag, attrs, children), icon: name => new Node(name),
  button: (label, action, kind, icon, attrs = {}) => new Node("button", { ...attrs, label, onclick: action }),
  iconButton: (name, label, action) => new Node("button", { label, onclick: action }),
  titleOf: p => p.title, sourceList: () => new Node(), toast: () => {}, state: { detail: null },
  openDialog: () => { dialog.open = true; }, renderLibrary: () => rendered++, normalizeDetail: data => data.paper,
  updatePaper: paper => { updated = paper; },
  api: async (url, options) => { calls.push({ url, options }); if (options.body.consent) return new Promise(resolve => { networkResolve = resolve; }); return { paper: { id: "a" } }; },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, "../static/publication-ui.js"), "utf8") + "\nglobalThis.lookup = openPublicationLookup; globalThis.missing = publicationMissing;", context);
function find(label, node = content) { if (node?.attrs?.label === label) return node; for (const child of node?.children || []) { const match = find(label, child); if (match) return match; } }
function textOf(node) { return typeof node === "string" ? node : (node?.children || []).map(textOf).join(" "); }
const tick = async () => { await Promise.resolve(); await Promise.resolve(); };
const paper = { id: "0123456789abcdef", title: "Fixture paper" };
(async () => {
  const first = context.lookup(paper); assert.equal(calls.length, 0, "Opening the consent dialog must not make a request");
  find("暂不搜索").attrs.onclick(); await first;
  assert.equal(calls.at(-1).options.body.consent, false); assert.equal(dialog.open, false);
  calls = []; const escaped = context.lookup(paper); dialog.fire("cancel", { preventDefault() {} }); await escaped;
  assert.equal(calls.length, 1); assert.equal(calls[0].options.body.consent, false);
  calls = []; const yes = context.lookup(paper); find("联网补全").attrs.onclick(); await tick();
  assert.equal(calls.length, 1); assert.equal(calls[0].options.body.consent, true);
  assert.equal(find("暂不搜索").disabled, true);
  networkResolve({ paper: { ...paper, journal: "Verified Journal", year: 2024, doi: "10.1234/example", authors: ["A Author"], page_range: "10-20", metadata_enrichment: { status: "complete", missing: [] } } });
  await tick(); assert.equal(updated.journal, "Verified Journal"); assert.ok(rendered); find("完成").attrs.onclick(); await yes;
  calls = []; const failure = context.lookup(paper); find("联网补全").attrs.onclick(); await tick();
  networkResolve({ paper: { ...paper, metadata_enrichment: { status: "error", missing: [{ key: "doi", label: "DOI" }], error: "Offline" } } }); await tick();
  assert.ok(find("重试联网补全")); find("重试联网补全").attrs.onclick(); await tick(); assert.equal(calls.length, 2);
  networkResolve({ paper: { ...paper, metadata_enrichment: { status: "partial", missing: [{ key: "doi", label: "DOI" }] } } }); await tick();
  find("完成").attrs.onclick(); await failure;
  assert.equal(context.missing({ journal: "J", year: 2024, doi: "10.1234/a", page_range: "1-10", authors: ["A Author"] }).length, 0);
  const complete = { ...paper, journal: "J", year: 2024, doi: "10.1234/a", page_range: "1-10", authors: ["A Author"],
    metadata_enrichment: { status: "complete", missing: [], conflicts: [], error: "" } };
  calls = []; const optional = context.lookup(complete);
  assert.ok(textOf(content).includes("当前出版信息已完整"));
  assert.equal(textOf(content).includes("未能可靠识别"), false);
  assert.ok(find("联网核对")); find("暂不搜索").attrs.onclick(); await optional;
  assert.equal(calls.length, 0, "Declining an optional recheck must not downgrade complete metadata");
  const verified = { ...complete, metadata_enrichment: { ...complete.metadata_enrichment, checked_at: "2026-01-02T00:00:00Z" } };
  calls = []; const reopened = context.lookup(verified);
  assert.equal(context.publicationVerified(verified), true);
  assert.ok(textOf(content).includes("出版信息已核对"));
  assert.equal(find("联网核对"), undefined); assert.equal(find("联网补全"), undefined);
  find("完成").attrs.onclick(); await reopened;
  assert.equal(calls.length, 0, "Closing a successful check must preserve its saved status without another request");
  const prompted = [];
  close.classList = { add() {}, remove() {} };
  context.FormData = class { append() {} };
  context.ACTIVE_STATUSES = new Set(); context.loadLibrary = async () => {}; context.setView = () => {};
  context.openPublicationLookup = async p => { prompted.push(p.id); };
  context.api = async () => ({ papers: [
    { id: "complete", metadata_enrichment: { status: "complete", missing: [] } },
    { id: "first", metadata_enrichment: { status: "needs_consent", missing: [{ key: "doi" }] } },
    { id: "declined", metadata_enrichment: { status: "declined", missing: [{ key: "doi" }] } },
    { id: "second", metadata_enrichment: { status: "needs_consent", missing: [{ key: "publication_date" }] } },
  ] });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../static/library/import.js"), "utf8"), context);
  await context.importFiles([{ name: "first.pdf" }, { name: "second.pdf" }]);
  assert.deepEqual(prompted, ["first", "second"], "Batch imports prompt each incomplete new paper and skip complete or already declined papers");
  assert.equal(context.state.importing, false);
  console.log("Publication import: consent, decline/Esc, completed-check preservation, accurate optional recheck text, failure and retry passed.");
})().catch(error => { console.error(error); process.exitCode = 1; });
