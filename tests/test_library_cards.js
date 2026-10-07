"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const app = require("./frontend_source").applicationSource();
function section(start, end) {
  const from = app.indexOf(start), to = app.indexOf(end, from);
  assert.ok(from >= 0 && to > from, `Missing source section: ${start}`);
  return app.slice(from, to);
}

const titleContext = {};
vm.createContext(titleContext);
vm.runInContext(section("function titleOf(", "function translationOf(") + "globalThis.titleOf = titleOf;", titleContext);
assert.equal(titleContext.titleOf({ title: "Original English title", title_zh: "中文译名" }), "Original English title");
assert.equal(titleContext.titleOf({ filename: "paper.pdf", title_zh: "中文译名" }), "paper.pdf");
assert.equal(titleContext.titleOf({}), "Untitled paper");

const journalContext = { paperType: paper => paper.paper_type || "journal" };
vm.createContext(journalContext);
vm.runInContext(
  section("const JOURNAL_SHORT_NAMES", "let pollTimer") + section("function journalNameOf(p)", "function dateMs(") +
  "globalThis.journalOf = journalOf;", journalContext
);
assert.equal(journalContext.journalOf({ journal: "The International Journal of Robotics Research", journal_abbr: "The International Journal of Robotics Research" }), "IJRR");
assert.equal(journalContext.journalOf({ journal: "Advanced Intelligent Systems 2024.6:2400022", journal_abbr: "Advanced Intelligent Systems 2024.6:2400022" }), "Adv. Intell. Syst.");
assert.equal(journalContext.journalOf({ journal: "Journal of Experimental Robotics" }), "JER");
assert.equal(journalContext.journalOf({ journal: "", paper_type: "preprint" }), "预印本");
assert.equal(journalContext.journalOf({ paper_type: "conference", conference_name: "Full conference name", conference_abbr: "CASE" }), "CASE");
assert.equal(journalContext.journalOf({ paper_type: "conference", conference_name: "Full conference name", conference_abbr: "RoboSoft" }), "RoboSoft");
assert.equal(journalContext.journalOf({ paper_type: "conference", conference_name: "Unrecognized Conference" }), "会议简称待补充");

const cardContext = {
  state: { settings: { translation_model: "", translation_reasoning_effort: "" } },
  modelCatalog: { default_model_id: "gpt-6-sol", models: [{ model: "gpt-6-sol", default_reasoning_effort: "medium" }] },
  translationOf: paper => paper.translation || {},
};
vm.createContext(cardContext);
vm.runInContext(section("function cardTranslationLabel(", "function paperType(") + "globalThis.cardTranslationLabel = cardTranslationLabel; globalThis.cardModelConfig = cardModelConfig;", cardContext);
assert.equal(cardContext.cardTranslationLabel({ translation: { total: 10, current_done: 9, status: "running" } }), "全文翻译");
assert.equal(cardContext.cardTranslationLabel({ translation: { total: 10, current_done: 10, status: "running" } }), "已翻译");
assert.equal(cardContext.cardTranslationLabel({ translation: { total: 10, current_done: 0, status: "complete" } }), "全文翻译");
assert.equal(cardContext.cardModelConfig({ paper_ai: {} }).model, "gpt-6-sol");
assert.equal(cardContext.cardModelConfig({ paper_ai: {} }).effort, "medium");
assert.equal(cardContext.cardModelConfig({ paper_ai: { model: "gpt-6-astra", reasoning_effort: "high" } }).effort, "high");
assert.equal(cardContext.cardModelConfig({ paper_ai: {}, translation: { current_done: 3 } }).legacyTranslation, true);

const daysAgo = days => new Date(Date.now() - days * 86400000).toISOString();
const filterValues = { "filter-journal": "", "filter-tier": "", "filter-type": "", sort: "imported", "sort-direction": "desc" };
const filterContext = {
  state: { view: "all", collection: "", query: "", papers: [
    { id: "new", paper_type: "journal", tier: "top", journal: "Journal A", year: 2023, collection: "robotics", created_at: daysAgo(3), last_read: daysAgo(1), updated_at: daysAgo(100) },
    { id: "middle", paper_type: "journal", tier: "top", journal: "Journal A", year: 2024, collection: "robotics", created_at: daysAgo(10), last_read: daysAgo(12), updated_at: daysAgo(0) },
    { id: "older", paper_type: "journal", tier: "other", journal: "Journal B", year: 2022, created_at: daysAgo(45), last_read: "" },
    { id: "oldest", paper_type: "conference", year: 2021, created_at: daysAgo(210), last_read: daysAgo(200) }
  ] },
  $: selector => ({ value: filterValues[selector.slice(1)] }),
  paperType: paper => paper.paper_type, journalOf: paper => paper.journal || "", tierOf: paper => paper.tier || "conference",
  titleOf: paper => paper.id, asText: value => String(value || ""), list: value => value || []
};
vm.createContext(filterContext);
vm.runInContext(section("function dateMs(", "function safeUrl(") + section("function filteredPapers()", "function renderLibrary()") + "globalThis.filteredPapers = filteredPapers;", filterContext);
const filteredIds = () => Array.from(filterContext.filteredPapers(), paper => paper.id);
assert.deepEqual(filteredIds(), ["new", "middle", "older", "oldest"]);
filterValues["filter-type"] = "journal"; filterValues["filter-tier"] = "top";
assert.deepEqual(filteredIds(), ["new", "middle"]);
filterValues.sort = "year"; filterValues["sort-direction"] = "asc";
assert.deepEqual(filteredIds(), ["new", "middle"]);
filterValues["sort-direction"] = "desc";
assert.deepEqual(filteredIds(), ["middle", "new"]);
filterContext.state.view = "collection"; filterContext.state.collection = "robotics";
filterValues["filter-journal"] = "Journal A";
assert.deepEqual(filteredIds(), ["middle", "new"]);
filterValues.sort = "recent"; filterValues["sort-direction"] = "asc";
assert.deepEqual(filteredIds(), ["middle", "new"]);
filterValues["sort-direction"] = "desc";
assert.deepEqual(filteredIds(), ["new", "middle"]);

const heights = [500, 250, 400, 450, 350, 300, 200, 300];
const cards = heights.map((height, index) => ({ id: index, offsetHeight: height, style: {}, querySelector: () => null }));
const frames = [];
let observeCallback;
let trackWidths = Array(6).fill(265);
const observed = [];
const grid = {
  isConnected: true, clientWidth: 1670, firstElementChild: null, style: {},
  classList: { values: new Set(), add(value) { this.values.add(value); }, contains(value) { return this.values.has(value); } },
  replaceChildren(...nodes) { this.firstElementChild = nodes[0]; this.children = nodes; }
};
const layoutContext = {
  renderCard: paper => cards[paper],
  getComputedStyle: () => ({ gridTemplateColumns: trackWidths.map(width => `${width}px`).join(" "), columnGap: "16px" }),
  requestAnimationFrame: callback => { frames.push(callback); },
  window: { ResizeObserver: true },
  ResizeObserver: class { constructor(callback) { observeCallback = callback; } observe(node) { observed.push(node); } disconnect() {} }
};
vm.createContext(layoutContext);
vm.runInContext("let paperLayoutObserver = null;" + section("function renderMasonry(", "function renderCard(") + "globalThis.renderMasonry = renderMasonry;", layoutContext);
layoutContext.renderMasonry(grid, cards.map((_, index) => index));
frames.shift()();
assert.deepEqual(cards.slice(0, 6).map(card => card.style.top), Array(6).fill("0px"));
assert.deepEqual(cards.slice(0, 6).map(card => card.style.left), ["0px", "281px", "562px", "843px", "1124px", "1405px"]);
assert.equal(cards[6].style.left, "281px");
assert.equal(cards[6].style.top, "266px");
assert.equal(cards[7].style.left, "1405px");
assert.equal(cards[7].style.top, "316px");
assert.equal(grid.style.height, "616px");
assert.ok(observed.includes(grid), "The available width must be observed, even if card heights stay unchanged");
assert.ok(cards.every(card => card.style.width === "265px"));

// Resize the same grid: every card must remain inside its available width.
trackWidths = Array(3).fill(320); grid.clientWidth = 992;
observeCallback(); frames.shift()();
assert.deepEqual(cards.slice(0, 3).map(card => card.style.left), ["0px", "336px", "672px"]);
assert.equal(cards[3].style.left, "336px");
assert.equal(cards[3].style.top, "266px");
assert.ok(cards.every(card => parseFloat(card.style.left) + parseFloat(card.style.width) <= grid.clientWidth));

trackWidths = [240]; grid.clientWidth = 240;
observeCallback(); frames.shift()();
assert.ok(cards.every(card => card.style.left === "0px" && card.style.width === "240px"));
assert.equal(cards[1].style.top, "516px");

trackWidths = Array(6).fill(265); grid.clientWidth = 1670;

cards[0].offsetHeight = 100;
observeCallback(); frames.shift()();
assert.equal(cards[6].style.left, "0px");
assert.equal(cards[6].style.top, "116px");
console.log("library cards: English titles, status, model, filters, sorting, abbreviations, and responsive masonry passed");
