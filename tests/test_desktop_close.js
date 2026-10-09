const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/desktop.js'), 'utf8');

async function run() {
  let flushed = 0, cached = 0, readerSaved = 0;
  const entry = { id: 'test-paper', value: 'unsaved note', flush: async () => { flushed++; throw Error('offline'); } };
  const context = vm.createContext({ readerScrollActive: () => true, readerPersistDrafts: () => true,
    detailNoteSaves: new Map([[entry.id, entry]]), readerWriteLocal: () => { cached++; return true; },
    saveReadPage: async () => { throw Error('offline'); }, readerSaveNotes: async () => { readerSaved++; throw Error('offline'); } });
  vm.runInContext(source, context);
  assert.equal(context.ezreadPersistDesktopDrafts(), true);
  assert.equal(cached, 1);
  assert.equal(flushed, 0, "The synchronous checkpoint preserves drafts before any asynchronous save");
  cached = 0;
  assert.equal(await context.ezreadPrepareDesktopClose(), true);
  assert.equal(flushed, 1); assert.equal(cached, 1);
  assert.equal(readerSaved, 1);
  context.readerPersistDrafts = () => false;
  assert.equal(context.ezreadPersistDesktopDrafts(), false);
  assert.equal(await context.ezreadPrepareDesktopClose(), false);
  assert.equal(flushed, 1);
  context.readerPersistDrafts = () => true; context.readerWriteLocal = () => false;
  assert.equal(context.ezreadPersistDesktopDrafts(), false);
  assert.equal(await context.ezreadPrepareDesktopClose(), false);
  assert.equal(flushed, 1);
  console.log('Desktop close preserves drafts offline and blocks close when local persistence fails.');
  context.readerWriteLocal = () => true;
  context.flushPreferences = async () => {};
  context.preferenceSaveBusy = false;
  context.pendingPreferences = { theme: 'sage' };
  assert.equal(await context.ezreadPrepareDesktopClose(), false);
  context.pendingPreferences = {};
  assert.equal(await context.ezreadPrepareDesktopClose(), true);
  context.localStorage = { length: 2, key: index => ["ezread-reader-notes:test-paper", "unrelated-setting"][index],
    getItem: key => key.startsWith("ezread-reader-") ? '{"value":"recoverable note"}' : "private value" };
  assert.deepEqual(JSON.parse(JSON.stringify(context.ezreadDesktopDraftSnapshot())),
    { "ezread-reader-notes:test-paper": '{"value":"recoverable note"}' });
  context.preferenceSaveBusy = true;
  assert.equal(context.ezreadDesktopDraftSnapshot(), null, "An in-flight preference save prevents crash-close confirmation");
  context.preferenceSaveBusy = false;
  context.pendingPreferences = { theme: "sage" };
  assert.equal(context.ezreadDesktopDraftSnapshot(), null);
}
run().catch(error => { console.error(error); process.exitCode = 1; });
