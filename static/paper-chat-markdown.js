"use strict";

// Model output becomes DOM text, never HTML.
function paperChatInline(text, depth = 0) {
  const fragment = document.createDocumentFragment();
  if (depth > 5) { fragment.append(document.createTextNode(text)); return fragment; }
  const pattern = /(`+)([^`]*?)\1|\*\*([^\n]+?)\*\*|__([^\n]+?)__|\*([^*\n]+?)\*|\[([^\]\n]+)\]\(([^\s)]+)\)/g;
  let start = 0, match;
  while ((match = pattern.exec(text))) {
    fragment.append(document.createTextNode(text.slice(start, match.index)));
    if (match[1]) fragment.append(el("code", {}, match[2]));
    else if (match[3] || match[4]) fragment.append(el("strong", {}, paperChatInline(match[3] || match[4], depth + 1)));
    else if (match[5]) fragment.append(el("em", {}, paperChatInline(match[5], depth + 1)));
    else {
      let safe = false;
      try { const url = new URL(match[7]); safe = ["http:", "https:"].includes(url.protocol) && !url.username && !url.password; } catch { /* Invalid links stay text. */ }
      fragment.append(safe ? el("a", { href: match[7], target: "_blank", rel: "noopener noreferrer" }, match[6]) : document.createTextNode(match[0]));
    }
    start = pattern.lastIndex;
  }
  fragment.append(document.createTextNode(text.slice(start)));
  return fragment;
}

function paperChatMarkdown(text) {
  const root = el("div", { class: "paper-chat-markdown" });
  const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
  const listPattern = /^(\s*)([-+*]|\d+[.)])\s+(.+)$/;
  const cells = line => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(s => s.trim());
  const startsBlock = (line, next) => /^\s*(?:```|~~~|#{1,6}\s|>\s?|[-*_]{3,}\s*$)/.test(line) || listPattern.test(line) ||
    (line.includes("|") && /^\s*\|?\s*:?-{3,}/.test(next || ""));
  for (let i = 0; i < lines.length;) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    const fence = /^\s*(`{3,}|~{3,})(.*)$/.exec(line);
    if (fence) {
      const content = []; i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence[1])) content.push(lines[i++]);
      root.append(el("pre", {}, el("code", {}, content.join("\n"))));
      if (i < lines.length) i++;
      continue;
    }
    const heading = /^\s*(#{1,6})\s+(.+?)\s*#*$/.exec(line);
    if (heading) { root.append(el(`h${heading[1].length}`, {}, paperChatInline(heading[2]))); i++; continue; }
    if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { root.append(el("hr")); i++; continue; }
    if (/^\s*>/.test(line)) {
      const quoted = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) quoted.push(lines[i++].replace(/^\s*>\s?/, ""));
      root.append(el("blockquote", {}, paperChatMarkdown(quoted.join("\n")))); continue;
    }
    if (line.includes("|") && cells(lines[i + 1] || "").every(s => /^:?-{3,}:?$/.test(s))) {
      const labels = cells(line), table = el("table", {}, el("thead", {}, el("tr", {}, labels.map(s => el("th", {}, paperChatInline(s))))));
      const body = el("tbody"); i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) body.append(el("tr", {}, cells(lines[i++]).map(s => el("td", {}, paperChatInline(s)))));
      table.append(body); root.append(el("div", { class: "paper-chat-table" }, table)); continue;
    }
    const first = listPattern.exec(line);
    if (first) {
      const ordered = /^\d/.test(first[2]), list = el(ordered ? "ol" : "ul", ordered ? { start: parseInt(first[2], 10) } : {});
      const indent = first[1].length;
      while (i < lines.length) {
        const entry = listPattern.exec(lines[i]);
        if (!entry || entry[1].length !== indent || /^\d/.test(entry[2]) !== ordered) break;
        const item = el("li", {}, paperChatInline(entry[3])); i++;
        const nested = [];
        while (i < lines.length && lines[i].trim() && /^\s+/.test(lines[i]) && lines[i].search(/\S/) > indent) nested.push(lines[i++].slice(indent + 2));
        if (nested.length) item.append(paperChatMarkdown(nested.join("\n")));
        list.append(item);
      }
      root.append(list); continue;
    }
    const paragraph = [line]; i++;
    while (i < lines.length && lines[i].trim() && !startsBlock(lines[i], lines[i + 1])) paragraph.push(lines[i++]);
    root.append(el("p", {}, paperChatInline(paragraph.join("\n"))));
  }
  return root;
}
