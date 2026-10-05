"use strict";

// Extract only the detail-note writer, with manual timers and deferred requests.
// These tests never open a browser or access a service, library, or real storage.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const appSource = require("./frontend_source").applicationSource();
const start = appSource.indexOf("const detailNoteSaves = new Map();");
const end = appSource.indexOf("async function changeCover(", start);
assert.ok(start >= 0 && end > start, "The detail-note function boundary must exist.");
const source = appSource.slice(start, end);

function fixture() {
  const storage = new Map(), timers = new Map(), requests = [], toasts = [];
  const state = { papers: [{ id: "paper1", notes: "saved original" }, { id: "paper2", notes: "other paper" }] };
  let timerId = 0, active = 0, maxActive = 0, mode = "defer", storageAvailable = true;
  const key = (kind, id) => `ezread-reader-${kind}:${id}`;
  const context = {
    state, console,
    setTimeout(fn) { const id = ++timerId; timers.set(id, fn); return id; },
    clearTimeout(id) { timers.delete(id); },
    readerReadLocal(kind, id) { const value = storage.get(key(kind, id)); return value ? JSON.parse(value) : null; },
    readerWriteLocal(kind, id, value) { if (!storageAvailable) return false; storage.set(key(kind, id), JSON.stringify(value)); return true; },
    readerRemoveLocal(kind, id) { storage.delete(key(kind, id)); },
    toast(...args) { toasts.push(args); },
    patchPaper(id, patch) {
      active++; maxActive = Math.max(maxActive, active);
      let resolve, reject, settled = false;
      const promise = new Promise((ok, bad) => { resolve = ok; reject = bad; });
      const request = {
        id, value: patch.notes,
        succeed() { if (settled) return; settled = true; active--; state.papers.find(paper => paper.id === id).notes = patch.notes; resolve({}); },
        fail() { if (settled) return; settled = true; active--; reject(Error("offline")); },
      };
      requests.push(request);
      if (mode === "success") request.succeed();
      if (mode === "failure") request.fail();
      return promise;
    },
  };
  vm.createContext(context); vm.runInContext(source, context);
  function editor(id = "paper1") {
    const listeners = new Map(), hint = { textContent: "" };
    const textarea = { value: state.papers.find(paper => paper.id === id).notes, addEventListener(name, fn) { listeners.set(name, fn); } };
    context.bindAutosave(textarea, id, hint);
    return { textarea, hint, input(value) { textarea.value = value; listeners.get("input")(); }, blur() { listeners.get("blur")(); } };
  }
  return {
    context, requests, state, storage, toasts, editor,
    setMode(value) { mode = value; }, setStorage(value) { storageAvailable = value; },
    runTimers() { const ready = [...timers.values()]; timers.clear(); for (const fn of ready) fn(); },
    get maxActive() { return maxActive; },
    readDraft(id = "paper1") { return context.readerReadLocal("notes", id); },
  };
}
async function settle() { for (let i = 0; i < 8; i++) await Promise.resolve(); }

const cases = [
  ["continuous edits send serial requests and finish with the latest value", async () => {
    const f = fixture(), e = f.editor();
    e.input("version 1"); const flushed = f.context.flushDetailNotes("paper1");
    assert.equal(f.requests.length, 1);
    e.input("version 2"); f.runTimers();
    assert.equal(f.requests.length, 1, "A second input must wait for the in-flight request.");
    f.requests[0].succeed(); await settle();
    assert.equal(f.requests.length, 2); assert.equal(f.requests[1].value, "version 2");
    f.requests[1].succeed(); await flushed; await settle();
    assert.equal(f.maxActive, 1); assert.equal(f.state.papers[0].notes, "version 2"); assert.equal(f.readDraft(), null);
    await f.context.flushDetailNotes("paper1"); assert.equal(f.requests.length, 2, "An already-saved value must not be sent again.");
  }],
  ["offline flush retains a draft and permits the reader to take ownership", async () => {
    const f = fixture(), e = f.editor(); f.setMode("failure"); e.input("detail draft");
    await f.context.flushDetailNotes("paper1");
    assert.equal(f.readDraft().value, "detail draft"); assert.equal(f.state.papers[0].notes, "saved original");
    // The reader recovers this draft, edits it further and saves successfully.
    f.state.papers[0].notes = "new reader notes"; f.context.readerRemoveLocal("notes", "paper1"); f.setMode("success");
    await f.context.flushDetailNotes("paper1");
    assert.equal(f.state.papers[0].notes, "new reader notes", "A retired detail writer must not overwrite the reader's newer saved notes.");
  }],
  ["network and local-storage failure rejects flush without losing the editor value", async () => {
    const f = fixture(), e = f.editor(); f.setMode("failure"); f.setStorage(false); e.input("keep this text");
    await assert.rejects(f.context.flushDetailNotes("paper1"), /offline/);
    assert.equal(e.textarea.value, "keep this text"); assert.equal(f.state.papers[0].notes, "saved original"); assert.equal(f.readDraft(), null);
  }],
  ["an old success response cannot delete a newer shared draft", async () => {
    const f = fixture(), e = f.editor(); e.input("request snapshot"); const flushed = f.context.flushDetailNotes("paper1");
    f.context.readerWriteLocal("notes", "paper1", { value: "newer shared draft" });
    f.requests[0].succeed(); await flushed;
    assert.equal(f.readDraft().value, "newer shared draft");
  }],
  ["rebinding a detail editor cannot leave an independent delayed writer", async () => {
    const f = fixture(), first = f.editor(); first.input("old pending value");
    const replacement = f.editor(); replacement.input("latest replacement value");
    const flushed = f.context.flushDetailNotes("paper1"); f.runTimers();
    assert.equal(f.maxActive, 1, "A discarded textarea's timer must not run a competing save.");
    for (let turn = 0; turn < 10; turn++) {
      const outstanding = f.requests.splice(0);
      if (!outstanding.length) break;
      for (const request of outstanding) request.succeed();
      await settle();
    }
    await flushed; assert.equal(f.state.papers[0].notes, "latest replacement value");
  }],
  ["restored drafts are submitted, while flushing another paper stays isolated", async () => {
    const f = fixture(); f.context.readerWriteLocal("notes", "paper1", { value: "recovered draft" });
    const e = f.editor(); assert.equal(e.textarea.value, "recovered draft"); f.setMode("success");
    await f.context.flushDetailNotes("paper2"); assert.equal(f.requests.length, 0);
    await f.context.flushDetailNotes("paper1"); assert.equal(f.state.papers[0].notes, "recovered draft"); assert.equal(f.readDraft(), null);
  }],
];
(async () => {
  let failed = 0;
  for (const [name, run] of cases) {
    let timeout;
    try {
      await Promise.race([run(), new Promise((_, reject) => { timeout = setTimeout(() => reject(Error("Test exceeded its one-second deadline; a save promise did not settle.")), 1000); })]);
      console.log(`PASS ${name}`);
    }
    catch (error) { failed++; console.error(`FAIL ${name}\n${error.stack}`); }
    finally { clearTimeout(timeout); }
  }
  if (failed) process.exitCode = 1;
  else console.log(`Detail notes: ${cases.length} behavior checks passed.`);
})();
