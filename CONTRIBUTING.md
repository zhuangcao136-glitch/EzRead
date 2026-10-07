# 贡献与个人定制

先阅读 [AGENTS.md](AGENTS.md) 和 [架构说明](docs/ARCHITECTURE.md)。个人定制可以在自己的分支维护；适合大家的改动再提交 Pull Request。

## 开发环境

需要 Python 3.10+；开发依赖由 `requirements-dev.txt` 包含运行依赖与固定版本 Ruff。离线回归还需要 Node.js；前端本身无需 npm、CDN 或构建步骤。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python scripts/check.py
```

运行测试使用独立临时库，检查入口把测试子进程的临时文件放在 `work/` 下的独立目录并在结束后清理，不修改用户的全局环境变量。手动启动开发实例也应明确指定文献目录与空闲端口，例如：

```powershell
$env:EZREAD_DATA_DIR = Join-Path (Get-Location) 'work\dev-data'
python server.py --port 47832
```

随后通过该端口的 `/api/health` 核实目录，并在浏览器手动打开 `http://127.0.0.1:47832`。个人真实文献库不要用作测试库。桌面启动器使用默认端口；需要原生窗口时先运行 `powershell -File scripts/build-desktop.ps1` 构建桌面组件，再为开发命令添加 `--open`。

## 代码约定与检查

- `.editorconfig` 约定 UTF-8、空格缩进和文件末尾换行：Python、C# 与 PowerShell 使用 4 个空格，前端、YAML 与 VBS 使用 2 个空格。PowerShell/VBS 使用 CRLF；PowerShell 另保留 UTF-8 BOM 以兼容 Windows PowerShell。Git 换行策略见 `.gitattributes`。
- Python 模块与函数使用 `snake_case`，类使用 `PascalCase`，常量使用 `UPPER_CASE`；前端函数沿用 `camelCase`。名称说明职责，注释解释约束与原因。
- CSS 保持多行规则，每条声明单独一行，按职责分组并保留加载顺序；格式配置见 `.prettierrc.json`。需要批量格式化时可运行 `npx --yes prettier@3.6.2 --write "static/*.css"`；它只是开发格式工具，应用运行及常规离线检查无需 npm。
- 新增逻辑放入对应职责模块，减少重复分支、未使用变量和一次性调试代码。兼容入口通过显式别名导出，并保留调用证据；不能仅因为本文件未引用就删除对外接口。
- 修改相关代码时改善可读性；不把整库格式化混入功能修复。当前 Ruff 仅检查基础语法、未定义名称、未使用导入／变量等 `E9`、`F` 规则，不强制重排已有代码。
- 不用整文件忽略掩盖检查失败。确需单行例外时注明具体规则和原因；修复未使用变量前确认表达式是否承担验证、写入或其他副作用。

统一检查入口：

```powershell
python scripts/check.py --lint-only
python scripts/check.py
```

第一条运行 Ruff 和源码发布边界审查；第二条继续运行 Python 离线回归、全部前端脚本语法检查与 Node 回归。缺少依赖时明确失败，不静默跳过。Node 不在 PATH 时设置 `EZREAD_NODE` 为其可执行文件路径。

源码发布边界审查检查 Git 中已跟踪及待提交文件的路径与内容，防止个人数据、本机路径或疑似凭据进入公开源码；不会读取被 Git 忽略的文献库。无 Git 元数据的源码包会明确跳过此项，其他检查仍执行。它是提交前的基础筛查，仍需人工检查实际差异。

## 让自己的 Codex 修改功能

1. 为当前源码建立 Git 基线，并在自己的分支工作。先保存或提交已有改动，再开始新任务。
2. 给助手说明具体行为、作用位置、保存方式和验收例子。要求先读 `AGENTS.md`，根据定位表进入相关模块。
3. 修改后检查差异和相关测试。数据迁移先备份，普通验证模拟模型响应。
4. 前端改动刷新页面即可；后端改动关闭主窗口后重新打开，新的退出流程会连同后台和模型子进程一起结束。升级仍运行旧退出流程的实例时，先保存并正常关闭旧窗口，再核实、关闭旧后台；不直接终止窗口丢弃草稿。
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

GitHub Actions 在 Windows、Python 3.10 与 3.12 上使用相同检查入口，并构建桌面组件。项目源码与文档采用根目录 [MIT License](LICENSE)；贡献按该许可证提供，保留第三方资源原有授权。当前发布形式为源码，桌面组件按构建说明生成。
