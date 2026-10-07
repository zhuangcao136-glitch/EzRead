"use strict";

const paperChatRuntime = { paperId: null, data: null, busyPapers: new Set(), request: 0 };

function paperChatMessage(message) {
  const node = el("div", { class: `paper-chat-message role-${message.role}` },
    el("div", { class: "paper-chat-role" }, message.kind === "summary" ? "研究速览" : message.role === "user" ? "我" : "论文助手"),
    el("div", { class: "paper-chat-text" }, message.text));
  if (message.citations?.length) node.append(el("div", { class: "paper-chat-citations" },
    message.citations.map(cite => button(`第 ${cite.page} 页 · ${cite.block_id}`, () => {
      if (state.reader?.id !== paperChatRuntime.paperId) return;
      setReaderPage(cite.page, true);
      selectBlock(cite.block_id, true);
    }, "secondary"))));
  return node;
}

function renderPaperChat() {
  const pane = $("#reader-paper-chat"), data = paperChatRuntime.data;
  if (!pane || !data) return;
  const current = data.generation === data.current.generation;
  const busy = paperChatRuntime.busyPapers.has(paperChatRuntime.paperId);
  const version = el("select", { "aria-label": "论文对话版本", onchange: event => void loadPaperChat(Number(event.target.value)) },
    data.generations.map(item => el("option", { value: item.generation },
      `第 ${item.generation} 版 · ${item.model || "模型未记录"}`)));
  version.value = String(data.generation);
  const history = el("div", { class: "paper-chat-history", id: "paper-chat-history" },
    data.messages.length ? data.messages.map(paperChatMessage) :
      el("p", { class: "paper-chat-empty" }, "暂无对话"));
  const input = el("textarea", { id: "paper-chat-input", rows: "3", maxlength: "4000",
    placeholder: current ? "向论文提问" : "历史对话",
    disabled: !current || busy,
    "aria-label": "向论文提问" });
  input.addEventListener("keydown", event => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault(); void sendPaperChat();
    }
  });
  const threadLink = data.thread_id && /^[0-9a-f-]{20,}$/i.test(data.thread_id)
    ? el("a", { class: "paper-chat-native", href: `codex://threads/${data.thread_id}`,
        title: "在 Codex 桌面端打开此对话" }, "在 Codex 中打开 ↗") : null;
  pane.replaceChildren(el("div", { class: "paper-chat-heading" },
      el("strong", {}, "论文对话"), threadLink,
      iconButton("close", "收起论文对话", () => closePaperChat())),
    version, history,
    current ? el("div", { class: "paper-chat-compose" }, input,
      button(busy ? "正在回答…" : "发送", () => void sendPaperChat(), "primary", "", {
        disabled: busy })) : null);
  history.scrollTop = history.scrollHeight;
}

async function loadPaperChat(generation = null) {
  const id = state.reader?.id;
  if (!id) return;
  const request = ++paperChatRuntime.request;
  const data = await api(`/api/papers/${encodeURIComponent(id)}/ai${generation ? `?generation=${generation}` : ""}`);
  if (state.reader?.id !== id || request !== paperChatRuntime.request) return;
  paperChatRuntime.paperId = id; paperChatRuntime.data = data;
  state.reader.paper_ai = data.current;
  const listed = state.papers.find(item => item.id === id);
  if (listed) listed.paper_ai = data.current;
  renderPaperChat();
}

async function openPaperChat() {
  const pane = $("#reader-paper-chat");
  if (!pane || !state.reader) return;
  if (paperChatRuntime.paperId !== state.reader.id) paperChatRuntime.data = null;
  const notes = $("#reader-notes");
  if (notes && !notes.hidden) { notes.hidden = true; $("#reader-notes-toggle")?.setAttribute("aria-expanded", "false"); }
  pane.hidden = false;
  $("#reader-chat-toggle")?.setAttribute("aria-expanded", "true");
  await loadPaperChat();
  $("#paper-chat-input")?.focus({ preventScroll: true });
}

function closePaperChat() {
  const pane = $("#reader-paper-chat");
  if (pane) pane.hidden = true;
  $("#reader-chat-toggle")?.setAttribute("aria-expanded", "false");
}

function togglePaperChat() {
  if ($("#reader-paper-chat")?.hidden) void openPaperChat().catch(error => toast(error.message, "error"));
  else closePaperChat();
}

async function sendPaperChat() {
  const input = $("#paper-chat-input"), id = state.reader?.id;
  const question = input?.value.trim();
  if (!id || !question || paperChatRuntime.busyPapers.has(id)) return;
  paperChatRuntime.busyPapers.add(id);
  const remembered = question;
  let failed = false;
  renderPaperChat();
  try {
    await api(`/api/papers/${encodeURIComponent(id)}/ai/chat`, {
      method: "POST", body: { question } });
    if (state.reader?.id === id) {
      await loadPaperChat();
      const target = state.papers.find(p => p.id === id);
      if (target) { target.paper_ai = paperChatRuntime.data.current; renderLibrary(); }
    }
  } catch (error) {
    failed = true;
    toast(`论文对话失败：${error.message}`, "error", 8000);
  } finally {
    paperChatRuntime.busyPapers.delete(id);
    if (state.reader?.id === id) {
      renderPaperChat();
      if (failed) { const target = $("#paper-chat-input"); if (target) target.value = remembered; }
    }
  }
}
