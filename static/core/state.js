"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const DEFAULT_PREFERENCES = Object.freeze({ theme: "paper", ui_font: "system", reader_font: "system", ui_font_size: 16, reader_font_size: 18, reader_sync: true });
const FONT_FAMILIES = Object.freeze({ system: '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif', sans: '"Noto Sans SC", "Microsoft YaHei", "Segoe UI", sans-serif', serif: '"SimSun", "Songti SC", "Noto Serif SC", Georgia, serif' });
const PAPER_TYPES = Object.freeze({ journal: "期刊论文", conference: "会议论文", preprint: "预印本", other: "其他文献" });
const TIER_NAMES = Object.freeze({ top: "顶级", important: "重要", other: "其他", conference: "会议", preprint: "预印本" });
const state = { papers: [], collections: [], settings: { ...DEFAULT_PREFERENCES }, status: null, usage: null, view: "all", collection: "", detail: null, detailFigure: null, reader: null, readerPage: 1, selectedBlock: null, zoom: "fit", loading: true, importing: false, query: "", pollBusy: false, selectionMode: false, selectedPapers: new Set(), readerRenderedSignature: null, detailRenderedSignature: null };
const VIEW_NAMES = { all: "全部论文", favorites: "我的收藏", queue: "翻译任务", collection: "主题集合" };
const FILTER_TITLES = { "filter-journal": "出版来源", "filter-tier": "期刊等级" };
const CARD_EFFORT_NAMES = { none: "无", minimal: "最轻", low: "轻", medium: "中", high: "高", xhigh: "更高", max: "最高", ultra: "极高" };
const STATUS_NAMES = { idle: "尚未翻译", none: "尚未翻译", pending: "等待翻译", queued: "排队中", running: "翻译中", translating: "翻译中", paused: "已暂停", complete: "已译完", completed: "已译完", done: "已译完", failed: "翻译未完成", error: "翻译未完成", cancelled: "已暂停" };
const ACTIVE_STATUSES = new Set(["queued", "pending", "running", "translating"]);
const DONE_STATUSES = new Set(["complete", "completed", "done"]);
const JOURNAL_SHORT_NAMES = Object.freeze({
  internationaljournalofroboticsresearch: "IJRR",
  theinternationaljournalofroboticsresearch: "IJRR",
  ieeetransactionsonrobotics: "TRO",
  ieeeroboticsandautomationletters: "RA-L",
  ieeeasmetransactionsonmechatronics: "T-MECH",
  ieeetransactionsonautomationscienceandengineering: "T-ASE",
  ieeesensorsjournal: "IEEE Sens. J.",
  naturecommunications: "Nat. Commun.",
  sciencerobotics: "Sci. Robot.",
  advancedintelligentsystems: "Adv. Intell. Syst.",
  softrobotics: "Soft Robot.",
  advancedmaterials: "Adv. Mater.",
  advancedfunctionalmaterials: "Adv. Funct. Mater.",
  advancedscience: "Adv. Sci.",
  aerospacescienceandtechnology: "Aerosp. Sci. Technol.",
  actaastronautica: "Acta Astronaut.",
  aiaajournal: "AIAA J.",
  additivemanufacturing: "Addit. Manuf."
});
let pollTimer, readSaveTimer, searchTimer;
let preferenceTimer, preferenceSaveBusy = false, pendingPreferences = {}, usageBusy = false, usagePollTimer;
let activeFilterTrigger = null;
let queueLibraryScroll = 0;
let paperLayoutObserver = null;
let paperLayoutCleanup = null;
let draggingPaperIds = [];
let activeCardMenuId = null;
let paperDragPreview = null;
let paperDragDeferredRender = false;
