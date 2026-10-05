"use strict";

// Read the actual application scripts in the order declared by index.html.
// Source-based regression tests must follow the running app after a file moves.
const fs = require("node:fs");
const path = require("node:path");

function applicationSource() {
  const root = path.join(__dirname, "../static");
  const index = fs.readFileSync(path.join(root, "index.html"), "utf8");
  const files = [...index.matchAll(/<script\s+src="\/static\/([^"]+)"\s+defer><\/script>/g)]
    .map(match => match[1]);
  if (!files.length || files.at(-1) !== "app.js") throw new Error("Missing application entry point");
  return files.map(file => fs.readFileSync(path.join(root, file), "utf8")).join("\n");
}

module.exports = { applicationSource };
