"use strict";

// No browser, service, real library, or model calls: exercise persistence failures
// and reading anchors in a small VM with explicit geometry and storage doubles.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const source = fs.readFileSync(path.join(__dirname, "../static/reader.js"), "utf8");
const storage = new Map(), nodes = new Map();
const state = {
  settings: { reader_split_ratio: .6, reader_sync: true },
  reader: { id: "paper1", pages: [{ number: 1 }, { number: 2 }, { number: 3 }], blocks: [{ id: "b1", page: 1 }, { id: "b2", page: 2 }], read_page: 1 },
  readerPage: 2, zoom: 125,
};
let storageFail = false, apiHandler = async () => ({}), patchHandler = async () => ({}), closes = 0;
const dialog = { open: true, close() { this.open = false; closes++; } };
nodes.set("#reader-dialog", dialog);
nodes.set("#reader-content", { inert: false });
nodes.set("#reader-save-state", { textContent: "" });
nodes.set("#reader-notes-hint", { textContent: "" });
const context = {
  state, console, readSaveTimer: null, performance: { now: () => 1000 },
  setTimeout: () => 1, clearTimeout() {}, requestAnimationFrame: () => 1, cancelAnimationFrame() {},
  localStorage: { getItem: key => storage.get(key) || null, setItem(key, value) { if (storageFail) throw Error("quota"); storage.set(key, value); }, removeItem: key => storage.delete(key) },
  $: selector => nodes.get(selector) || null, $$: (selector, root) => root?.nodes?.[selector] || [],
  document: { activeElement: null }, window: { scrollY: 0, scrollX: 0, addEventListener() {}, scrollTo() {} },
  api: (...args) => apiHandler(...args),
  updatePaper: patch => { state.reader = { ...state.reader, ...patch }; }, toast() {}, renderLibrary() {}, paperSignature: () => "", safeUrl: value => value,
};
vm.createContext(context);
vm.runInContext(source + "\nglobalThis.R = readerRuntime;", context);
const R = context.R;
vm.runInContext("patchBlock = (...args) => testPatch(...args); refreshBlock = () => {}; findBlockNode = () => null;", context);
context.testPatch = (...args) => patchHandler(...args);
function box(top, height, dataset) {
  return { dataset, getBoundingClientRect: () => ({ top, bottom: top + height, height, left: 0, right: 500, width: 500 }) };
}
const original = {
  id: "original-scroll", scrollTop: 700, scrollLeft: 0, clientHeight: 600, clientWidth: 500,
  getBoundingClientRect: () => ({ top: 100, left: 0, right: 500 }),
  scrollTo(value) { this.last = value; this.scrollTop = value.top; this.scrollLeft = value.left; },
  nodes: { ".reader-page-section": [box(-200, 1000, { page: "2" })] },
};
const translated = {
  id: "translation-scroll", scrollTop: 300, scrollLeft: 0, clientHeight: 600,
  getBoundingClientRect: () => ({ top: 100, left: 0, right: 500 }),
  scrollTo(value) { this.last = value; this.scrollTop = value.top; this.scrollLeft = value.left; },
  nodes: { ".translation-block": [box(50, 200, { blockId: "b2", page: "2" })] },
};
nodes.set("#original-scroll", original); nodes.set("#translation-scroll", translated);

async function main() {
  let value = context.readerNormalizePosition({ page: 99, original_offset: 2, translation_offset: -1, translation_block_id: "foreign", zoom: 999, split_ratio: .1, mode: "bad" });
  assert.equal(value.page, 3); assert.equal(value.original_offset, 1); assert.equal(value.translation_offset, 0);
  assert.equal(value.translation_block_id, null); assert.equal(value.zoom, "fit"); assert.equal(value.split_ratio, .3); assert.equal(value.mode, "parallel");
  value = context.readerNormalizePosition({}); assert.equal(value.split_ratio, .6); assert.equal(value.zoom, "fit");

  R.mode = "parallel"; R.split = .64;
  let position = context.readerCapturePosition();
  assert.equal(position.page, 2); assert.equal(position.original_offset, .3); assert.equal(position.translation_block_id, "b2");
  assert.equal(position.translation_offset, .25); assert.equal(position.zoom, 125); assert.equal(position.split_ratio, .64);
  context.readerRestoreAnchor(original, ".reader-page-section", { key: "2", ratio: .3, offset: 0, scrollTop: 0, scrollLeft: 0 });
  assert.equal(original.last.top, 700);
  R.mode = "original"; translated.nodes[".translation-block"][0] = box(-30, 200, { blockId: "b1", page: "1" });
  position = context.readerCapturePosition(); assert.equal(position.translation_block_id, "b2"); assert.equal(position.translation_offset, .25);
  R.mode = "parallel"; translated.nodes[".translation-block"][0] = box(50, 200, { blockId: "b2", page: "2" });

  const writes = [];
  apiHandler = async (url, options) => { writes.push({ url, body: options.body }); return {}; };
  await context.saveReadPage(); assert.equal(writes.length, 1); assert.equal(writes[0].body.reader_state.original_offset, .3);
  assert.equal(Object.hasOwn(writes[0].body, "read_state"), false, "Saving reading position must not classify a paper as read.");
  await context.saveReadPage(); assert.equal(writes.length, 1, "An unchanged reading anchor must not produce another write.");
  original.nodes[".reader-page-section"][0] = box(-300, 1000, { page: "2" });
  await context.saveReadPage(); assert.equal(writes.length, 2, "A new offset on the same page must be saved."); assert.equal(writes[1].body.reader_state.original_offset, .4);

  let fail = true; apiHandler = async () => { if (fail) throw Error("offline"); return {}; };
  R.notes = { id: "paper1", value: "new notes", savedValue: "old notes", saving: null, timer: null };
  await assert.rejects(context.readerSaveNotes(), /offline/);
  assert.equal(JSON.parse(storage.get("ezread-reader-notes:paper1")).value, "new notes"); assert.equal(R.notes.savedValue, "old notes");
  fail = false; await context.readerSaveNotes(); assert.equal(R.notes.savedValue, "new notes"); assert.equal(storage.has("ezread-reader-notes:paper1"), false);

  let resolveSave; apiHandler = async () => new Promise(resolve => { resolveSave = resolve; });
  R.notes = { id: "paper1", value: "version1", savedValue: "old", saving: null, timer: null };
  const firstSave = context.readerSaveNotes(); R.notes.value = "version2"; context.readerWriteLocal("notes", "paper1", { value: "version2" }); resolveSave({}); await firstSave;
  assert.equal(R.notes.savedValue, "version1");
  assert.equal(JSON.parse(storage.get("ezread-reader-notes:paper1")).value, "version2", "A stale response must not clear the latest note draft.");
  apiHandler = async () => ({}); await context.readerSaveNotes(); assert.equal(R.notes.savedValue, "version2");

  const editor = { paperId: "paper1", blockId: "b2", mode: "translation", text: { value: "revision" }, node: { remove() { this.removed = true; } }, hint: { textContent: "" }, savedValue: "old", saving: null };
  R.editor = editor; patchHandler = async () => { throw Error("offline"); };
  await assert.rejects(context.readerSaveEditor(), /offline/);
  assert.equal(JSON.parse(storage.get("ezread-reader-block:paper1")).value, "revision"); assert.equal(R.editor, editor);
  patchHandler = async () => ({}); await context.readerSaveEditor(); assert.equal(R.editor, null); assert.equal(storage.has("ezread-reader-block:paper1"), false);

  R.notes = { id: "paper1", value: "unsaved", savedValue: "old", saving: null, timer: null };
  storageFail = true; apiHandler = async () => { throw Error("offline"); }; R.savedSignature = "";
  const closed = await context.closeReader(); assert.equal(closed, false); assert.equal(closes, 0); assert.equal(dialog.open, true); assert.equal(nodes.get("#reader-content").inert, false);
  storageFail = false; const safeClosed = await context.closeReader();
  assert.equal(safeClosed, true); assert.equal(closes, 1); assert.equal(JSON.parse(storage.get("ezread-reader-notes:paper1")).value, "unsaved");

  const handlers = {}, divider = { addEventListener: (name, handler) => { handlers[name] = handler; } };
  vm.runInContext("readerSetSplit = value => { globalThis.testSplitValue = value; };", context);
  R.split = .5; context.readerBindDivider(divider);
  handlers.keydown({ key: "ArrowRight", shiftKey: false, preventDefault() {} }); assert.equal(context.testSplitValue, .52);
  handlers.keydown({ key: "Home", shiftKey: false, preventDefault() {} }); assert.equal(context.testSplitValue, .3);
  handlers.keydown({ key: "Enter", shiftKey: false, preventDefault() {} }); assert.equal(context.testSplitValue, .5);
  console.log("Reader behavior checks passed: anchors, offset persistence, offline drafts, concurrent notes, safe close, keyboard split.");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
