"use strict";

// Invoked only by the native window's close handshake. Preserve local drafts
// first; a network failure may leave a recoverable draft but must not discard it.
async function ezreadPrepareDesktopClose() {
  if (typeof readerScrollActive === "function" && readerScrollActive() && !readerPersistDrafts()) return false;
  if (typeof detailNoteSaves !== "undefined") {
    for (const entry of detailNoteSaves.values()) {
      if (entry.released || entry.value === entry.savedValue) continue;
      if (!readerWriteLocal("notes", entry.id, { value: entry.value })) return false;
    }
  }
  const saves = [];
  if (typeof readerFlushTextEditor === "function") saves.push(readerFlushTextEditor());
  if (typeof readerScrollActive === "function" && readerScrollActive()) {
    if (typeof saveReadPage === "function") saves.push(saveReadPage());
    if (typeof readerSaveNotes === "function") saves.push(readerSaveNotes());
  }
  if (typeof detailNoteSaves !== "undefined") {
    for (const entry of detailNoteSaves.values()) if (!entry.released) saves.push(entry.flush());
  }
  await Promise.allSettled(saves);
  if (typeof flushPreferences === "function") {
    while (preferenceSaveBusy) await new Promise(resolve => setTimeout(resolve, 50));
    await flushPreferences();
    if (Object.keys(pendingPreferences).length) return false;
  }
  if (typeof readerScrollActive === "function" && readerScrollActive() && !readerPersistDrafts()) return false;
  // Failed requests retain their local copies through the existing save code.
  return true;
}
