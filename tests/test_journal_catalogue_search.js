"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../static/journal-catalogue-ui.js"), "utf8");
const context = {};
vm.createContext(context);
vm.runInContext(source.slice(0, source.indexOf("function journalCataloguePanel(")) +
  "globalThis.search = searchJournalCatalogue; globalThis.initial = journalInitial;", context);
const rows = [
  { name: "Advanced Nature Methods", aliases: "ANM", issn: "1000-0001", tier: "important" },
  { name: "Nature Communications", aliases: "Nat. Commun.;Nat Commun", issn: "2041-1723", tier: "top" },
  { name: "Nature", aliases: "", issn: "0028-0836", tier: "top" },
  { name: "IEEE Robotics and Automation Letters", aliases: "RA-L", issn: "2377-3766", tier: "important" },
  { name: "IEEE Transactions on Robotics", aliases: "T-RO;TRO", issn: "1552-3098", tier: "top" },
  { name: "机器人", aliases: "Robot", issn: "1002-0446", tier: "important" },
];
function names(query) { return Array.from(context.search(rows, query), row => row.name); }
assert.deepEqual(names("NATURE"), names("nature"));
assert.deepEqual(names("nature"), ["Nature", "Nature Communications", "Advanced Nature Methods"]);
assert.deepEqual(names(" nAt. CoMmUn. "), ["Nature Communications"]);
assert.deepEqual(names("RA-L"), names("ra-l"));
assert.equal(names("robot")[0], "机器人", "An exact alias must rank above longer title matches across grades");
assert.equal(names("tro")[0], "IEEE Transactions on Robotics");
assert.deepEqual(names("robotics letters"), ["IEEE Robotics and Automation Letters"]);
assert.deepEqual(names("20411723"), ["Nature Communications"]);
assert.deepEqual(names("2377-3766"), ["IEEE Robotics and Automation Letters"]);
assert.deepEqual(names("机 器 人"), ["机器人"]);
assert.deepEqual(names("definitely absent"), []);
assert.deepEqual(names("..."), []);
assert.equal(names("   ").length, rows.length);
assert.equal(context.initial("nature"), "N");
assert.equal(context.initial("Ｎature"), "N");
assert.equal(context.initial("机器人"), "#");
const copy = JSON.stringify(rows); context.search(rows, "nature"); assert.equal(JSON.stringify(rows), copy);
console.log("Journal catalogue search: case, exact identities, aliases, prefixes, keywords, ranking and alphabet groups passed.");
