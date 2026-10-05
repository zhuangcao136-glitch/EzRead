# 贡献与个人定制

先阅读 [AGENTS.md](AGENTS.md) 和 [架构说明](docs/ARCHITECTURE.md)。个人定制可以在自己的分支维护；适合大家的改动再提交 Pull Request。

## 开发环境

需要 Python 3.10+ 和 `requirements.txt` 中的包。离线回归还需要 Node.js；前端本身无需 npm、CDN 或构建步骤。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts/check.py
```

运行测试使用独立临时库。手动启动开发实例也应明确指定文献目录与空闲端口，例如：

```powershell
$env:EZREAD_DATA_DIR = Join-Path (Get-Location) 'work\dev-data'
python server.py --port 47832 --open
```

随后通过该端口的 `/api/health` 核实目录。个人真实文献库不要用作测试库。桌面启动器使用默认端口，开发实例的 `--open` 打开普通浏览器页。

## 让自己的 Codex 修改功能

1. 为当前源码建立 Git 基线，并在自己的分支工作。先保存或提交已有改动，再开始新任务。
2. 给助手说明具体行为、作用位置、保存方式和验收例子。要求先读 `AGENTS.md`，根据定位表进入相关模块。
3. 修改后检查差异和相关测试。数据迁移先备份，普通验证模拟模型响应。
4. 前端改动刷新页面即可；后端改动须确认并关闭对应后台进程，再重新启动。关闭应用窗口通常不会结束后台服务。
5. 验收后提交改动。需要撤回时使用 Git 回退代码，并先检查未提交编辑；代码回退不会自动恢复数据迁移。

示例请求：

> 请阅读 AGENTS.md，给文献卡片增加“稍后阅读”操作。状态要持久保存，允许按它筛选；沿用现有卡片与 HTTP 契约，保护已有数据，并验证刷新后状态仍保留。

本机路径、个人风格和运行环境可以写入不提交的 `AGENTS.local.md`；通用架构、数据规则和测试入口写入公开的 `AGENTS.md`。

## 提交要求

- 只修改需求涉及的职责模块，入口仅做必要组装。
- 保留导入去重、软删除、阅读锚点、草稿、手动合集归属及译文版本。
- Bug 修复补充能覆盖故障的测试；简单样式或文档调整无需编写重复实现的测试。
- 提交前运行与改动相称的验证；涉及接口和模块加载时运行 `python scripts/check.py`。
- PR 描述说明问题、最终行为、验证和实际限制。截图使用合成文献或已获授权的样本。
- 检查 `git status` 和拟提交差异，排除文献、账号资料、本机绝对路径、缓存和备份。

GitHub Actions 使用相同检查入口。该项目尚未确定开源许可证与发布形式；公开发布前由维护者明确这些事项。
