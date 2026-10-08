"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../static/desktop.js"), "utf8");
const messages = [], bindings = [];
let dialogOpen = false;
const sidebar = { getBoundingClientRect: () => ({ left: 0, right: 184, top: 0, bottom: 1000 }) };
const context = vm.createContext({
  window: { ezreadDesktop: true, innerHeight: 1000, devicePixelRatio: 1.5,
    chrome: { webview: { postMessage: value => messages.push(value) } } },
  document: { querySelector: selector => selector === ".sidebar" ? sidebar : dialogOpen ? {} : null,
    addEventListener: (...args) => bindings.push(args) },
  getComputedStyle: () => ({ lineHeight: "25.6px" })
});
vm.runInContext(source, context);
assert.equal(bindings.length, 1);
assert.equal(bindings[0][0], "wheel");
assert.equal(bindings[0][2].passive, false);

function wheel(options = {}) {
  let prevented = false;
  const event = { clientX: 50, clientY: 80, deltaY: 100, deltaMode: 0, cancelable: true,
    target: { closest: () => null }, preventDefault: () => { prevented = true; }, ...options };
  context.ezreadDesktopSidebarWheel(event);
  return prevented;
}
assert.equal(wheel(), true);
assert.equal(messages[0].type, "ezread-sidebar-wheel");
assert.equal(messages[0].deltaY, 100);
assert.equal(messages[0].pixelRatio, 1.5);
assert.equal(wheel({ deltaMode: 1, deltaY: -3 }), true);
assert.ok(Math.abs(messages[1].deltaY + 76.8) < 1e-9);
assert.equal(wheel({ deltaMode: 2, deltaY: 1 }), true);
assert.equal(messages[2].deltaY, 1000);

const before = messages.length;
for (const options of [{ clientX: 240 }, { clientY: 1100 }, { ctrlKey: true },
  { defaultPrevented: true }, { cancelable: false }, { deltaY: 0 }, { deltaY: NaN }]) {
  assert.equal(wheel(options), false);
}
assert.equal(messages.length, before, "Card scrolling, zoom and invalid wheel events must stay independent");

dialogOpen = true;
assert.equal(wheel(), false, "Open dialogs keep their own scrolling");
dialogOpen = false;
const popover = { getBoundingClientRect: () => ({ left: 10, right: 170, top: 20, bottom: 300 }) };
assert.equal(wheel({ target: { closest: () => popover } }), false, "Popover lists retain their own scrolling");
console.log("Desktop sidebar wheel: region routing, units, zoom, dialog and popover scrolling passed.");
