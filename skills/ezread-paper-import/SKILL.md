---
name: ezread-paper-import
description: Organize imported scientific PDF text in EzRead into source-preserving title, authors, affiliations, article information, abstract, sections and natural paragraphs, and automatically assign papers to research-topic collections; use for PDF ingestion, topic grouping, structured English reading and extraction quality review.
---

# EzRead 论文导入与英文原文整理

目标是可逐段精读、可定位回 PDF 的英文全文。按论文内容组织，不能按页码罗列，也不能用摘要、改写或翻译代替原文。

## 执行流程

1. 保留原 PDF。提取文字、字体、坐标、栏顺序、图表和公式，每个源文本块保留稳定 ID、页码和 bbox。先恢复栏顺序和行，再恢复自然段；PDF 换行、换栏和换页本身不是段落边界。
2. 执行 `scripts/organize.py` 的 `organize(document)`，建立独立结构层。固定首页信息顺序为 Title → Authors → Affiliations → Article Info → Abstract；正文按原论文的章节顺序，保留小节、自然段、参考文献、附录和声明。Introduction 通常含多个自然段，不强制压成一段。没有对应信息就省略该组，不编造作者、机构、实验室、日期或章节。
3. 导入后自动在后台进行一次 AI 语义校对和研究主题归类。复用设置中的快速模型，导入时保存所选模型，按官方模型目录验证配置；不改变论文全文翻译/问答的模型。读 [references/semantic.md](references/semantic.md)，识别首页栏目和真实章节标题；只返回源 ID 的分类，不生成替换英文。发送首页与有歧义的标题候选、相邻上下文，避免反复发送全文。完整原文始终由程序从源块装配。
4. 用 `validate_roles` 校验 AI 结果：候选 ID 全部且唯一覆盖，拒绝未知 ID、正文伪装成页眉页脚、公式误分类。最终用 `audit_structure` 检查每个源块恰好归入一个阅读条目或带理由的版面噪声清单。校验失败保留本地结果，显示待核对，不以 AI 错误阻断导入。
5. 保存结构版本、源指纹、实际处理方式、模型配置、状态和提示。同一版本和源指纹直接复用；失败不自动循环重试，用户可手动重试。打开已有文献只建立本地结构，不批量发起模型请求。
6. 右侧按结构连续显示，不显示页码分隔标题；源页码用于对照定位。合并段落仍保留每个源 ID，让已译片段、手工修订、批注、高亮和阅读位置继续有效。不要替换原 `blocks` 或重新编号。
7. 新导入论文先暂存于“待分类”，同次语义调用按 [references/collections.md](references/collections.md) 返回主题归属。结合标题、摘要、已有合集名称及最多三篇示例论文，优先复用能容纳主要贡献的已有主题；没有对应主题才新建简短中文合集。归类结果与合集列表一起持久保存，Unicode/空格/标点/大小写近似的名称复用既有名称。手动移入或移出后标记 manual，后台结果不能覆盖；重复导入保持原归属，不给旧库批量归类。分类和源块结构分别校验，某一项失败不能丢弃另一项的有效结果。信息不足或调用失败保留“待分类”、显示原因并提供重试/手动选择，不反复自动请求。

## 划分和罗列规则

- Title 合并多行标题；Authors 合并作者列表，保留原有机构序号与通讯标记。Affiliations 保留原文给出的大学、院系、研究所和实验室名称及序号；作者与单位的对应只采用明确标记，不按常识推断。
- Article Info 汇集论文实际给出的期刊、卷期、DOI、Received/Revised/Accepted/Available online、Keywords、通讯信息、许可等。首次出现的有意义出版信息保留；重复页眉、页脚、网址、页码可移出阅读正文，但须记录源 ID 和原因。
- Abstract 保留完整英文和原有论证顺序。识别带空格的 A B S T R A C T 及无标签摘要；不能把 Introduction 的大段文字当成摘要。结构式摘要允许多个自然段或小标题。
- 正文依据章节语义、字体、编号、缩进和间距共同判断。不能把作者名单、加粗摘要正文、孤立上标或句尾短行当作章节。修复断行连字时保留有意义复合词，不把不同自然段拼成一段。
- 图注、表格、公式、参考文献和脚注独立处理，不混入正文。无法可靠恢复的数学符号与复杂表格保留原图和提示。扫描件无文字时明确需要 OCR，不伪造全文。

## 应用接入和验收

应用从本 Skill 的 `scripts/organize.py` 加载处理逻辑，并把本文件及语义参考送入受限的 Codex 文本调用，因此 Markdown 约定和实际执行同源。使用用户订阅内的现有 Codex 桥接；不读取凭据、不新增收费 API、不进行联网作者归属猜测。

测试至少覆盖单栏、双栏、多行标题、作者上标、首页脚注机构、Article Info 与摘要并排、跨栏/跨页段落、无标签摘要、公式表格、漏块/重复块拒绝和已译批注保留，以及已有主题复用、新主题创建、重复导入、连续导入避免重名、手动修改优先、失败保留/可重试。使用隔离数据目录和模拟模型结果；真实文献可只读验收，不为了测试反复消耗模型额度。
