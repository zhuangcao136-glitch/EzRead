async function importFiles(files) {
  const pdfs = [...files].filter(file => file.name.toLowerCase().endsWith(".pdf"));
  if (!pdfs.length) { toast("请选择 PDF 文件", "warn"); return; }
  if (state.importing) { toast("正在导入，请等待当前批次完成", "warn"); return; }
  state.importing = true; $("#import-progress-title").textContent = `正在导入 ${pdfs.length} 篇论文`; $("#import-progress").classList.remove("hidden");
  try {
    const form = new FormData(); pdfs.forEach(file => form.append("files", file));
    const data = await api("/api/import", { method: "POST", body: form });
    await loadLibrary(); setView("all");
    const count = (data.papers || []).length;
    if (count) toast(`已导入：${count} 篇论文${data.papers.some(p => ACTIVE_STATUSES.has(p.collection_assignment?.status)) ? "，正在按研究主题归类" : ""}`);
    else if (!(data.errors || []).length) toast("未导入论文，请检查所选文件", "warn");
    for (const error of data.errors || []) toast(asText(error), "error", 9000);
  } finally { state.importing = false; $("#import-progress").classList.add("hidden"); $("#file-input").value = ""; }
}
