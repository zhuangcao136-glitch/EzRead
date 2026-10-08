function titleOf(p) { return p.title || p.filename || "Untitled paper"; }
function translationOf(p) { return p.translation || { status: "idle", done: 0, total: 0 }; }
function statusName(value) { return STATUS_NAMES[value] || value || "尚未翻译"; }
function taskStatusName(value) { return ({ queued: "排队中", pending: "等待处理", running: "进行中", completed: "已完成", complete: "已完成", failed: "未完成", error: "未完成", paused: "已暂停" })[value] || value || "尚未开始"; }
function cardTranslationLabel(p) {
  const t = translationOf(p);
  return Number(t.total) > 0 && Number(t.current_done) >= Number(t.total) ? "已翻译" : "全文翻译";
}
function cardModelConfig(p) {
  const locked = p.paper_ai || {};
  const model = locked.model || state.settings.translation_model || modelCatalog?.default_model_id;
  const entry = modelCatalog?.models?.find(item => item.model === model);
  const effort = locked.model ? locked.reasoning_effort : state.settings.translation_reasoning_effort || entry?.default_reasoning_effort;
  return { model, effort, locked: Boolean(locked.model), legacyTranslation: !locked.model && Number(translationOf(p).current_done) > 0 };
}
function paperType(p) { return Object.hasOwn(PAPER_TYPES, p.paper_type) ? p.paper_type : "journal"; }
function journalNameOf(p) { return String(p.journal || p.journal_abbr || "").trim().replace(/\s+(?:19|20)\d{2}(?:[.,]\d+)?(?::\s*[\w.-]+)?\s*$/u, "").trim(); }
function journalOf(p) {
  if (paperType(p) === "conference") return String(p.conference_abbr || "").trim() || "会议简称待补充";
  if (paperType(p) === "preprint") return p.journal_abbr || p.journal || "预印本";
  const name = journalNameOf(p);
  const key = name.toLowerCase().replace(/[^a-z0-9]/g, "");
  if (JOURNAL_SHORT_NAMES[key]) return JOURNAL_SHORT_NAMES[key];
  const saved = String(p.journal_abbr || "").trim();
  if (saved && saved.length <= 26 && !/\b(?:19|20)\d{2}\b/u.test(saved)) return saved;
  if (!name) return "来源待补充";
  if (name.length <= 18) return name;
  const words = name.match(/[A-Za-z]+/g);
  if (!words) return name;
  const initials = words.filter(word => !/^(the|of|and|on|for|in|a|an)$/i.test(word)).map(word => word[0].toUpperCase()).join("");
  return initials.length > 1 ? initials.slice(0, 10) : name;
}
function dateMs(value) { return value ? new Date(typeof value === "number" && value < 1e12 ? value * 1000 : value).getTime() || 0 : 0; }
function importedAt(p) { return dateMs(p.created_at || p.imported_at); }
function safeUrl(url) { if (typeof url !== "string" || !url.trim()) return ""; try { const parsed = new URL(url, location.href); return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : ""; } catch { return ""; } }
function imageNode(src, attrs = {}) { const url = safeUrl(src); return url ? el("img", { src: url, loading: "lazy", alt: "论文插图", ...attrs }) : placeholderImage(); }
function placeholderImage() { return el("div", { class: "paper-no-image" }, icon("image"), "等待选择封面"); }
function ratio(p) { const t = translationOf(p); return Math.max(0, Math.min(100, Number(t.total) ? Number(t.done || 0) / Number(t.total) * 100 : 0)); }
function tierOf(p) { const tier = p.journal_tier; return Object.hasOwn(TIER_NAMES, tier) ? tier : paperType(p) === "conference" ? "conference" : paperType(p) === "preprint" ? "preprint" : "other"; }
function rankAppearance(p) {
  return { type: paperType(p), tier: tierOf(p) };
}
function rankBadges(p, { descriptive = false } = {}) {
  const tier = tierOf(p);
  const title = p.tier_needs_review ? "期刊全称、简称或 ISSN 冲突，请核对论文信息" : tier === "other" && paperType(p) === "journal" ? "未列入顶级或重要名单" : TIER_NAMES[tier];
  const label = descriptive ? ({ top: "顶级期刊", important: "重要期刊", other: "其他期刊", conference: "会议论文", preprint: "预印本" })[tier] : TIER_NAMES[tier];
  return [el("span", { class: `rank-badge rank-${tier}`, title }, label)];
}
