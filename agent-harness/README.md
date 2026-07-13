# Agent Harness

`agent-harness` 是 General Agent Design 的本地 CLI 和参考 Runtime。它当前以代码目录为主要工作空间，但核心能力面向通用 Agent：持续多轮交互、模型与工具循环、权限审批、子 Agent、外部能力接入、本地状态恢复和执行审计。

一次模型请求不是一个完整 Thread。CLI 启动后会创建或恢复 Thread；每次用户输入形成一个 Turn；用户消息、模型输出、工具调用、审批和终态作为 Item 追加到 rollout。`.harness/runs/` 只用于一次性 `exec`，日常持续对话保存在 `.harness/threads/`。

## 环境要求

- Python 3.11+
- 可访问的 DeepSeek-compatible API
- 使用受保护命令执行时，需要 Linux + bubblewrap，或 Windows 上的 WSL2 Linux 发行版 + bubblewrap
- 开发环境建议安装 `uv`

## 安装

在本目录执行：

```powershell
python -m pip install -e .
```

`-e` 表示 editable install：当前源码目录被注册到正在使用的 Python 环境，并生成 `agent-harness` 命令。源码修改后通常不需要重新安装。若 PowerShell 找不到命令，应确认安装命令和运行命令使用的是同一个 Python 环境：

```powershell
python -m pip show agent-harness
Get-Command agent-harness
```

开发依赖使用锁定环境：

```powershell
uv sync --locked --extra test
```

## Provider 配置

运行交互式设置：

```powershell
agent-harness setup
```

它会保存 API URL、模型和 API Key 环境变量名称，但不会把 Key 明文写入配置。Windows 默认配置路径为：

```text
%APPDATA%\agent-harness\config.toml
```

当前 DeepSeek Provider 实际读取 `DEEPSEEK_API_KEY`，因此 setup 时应保留默认环境变量名：

```powershell
setx DEEPSEEK_API_KEY "你的 API Key"
```

`setx` 只影响之后新开的终端。当前 PowerShell 会话可使用：

```powershell
$env:DEEPSEEK_API_KEY = "你的 API Key"
```

API URL 也可以通过 `DEEPSEEK_API_URL` 覆盖。CLI 支持项目约定的两个模型别名：

```text
v4-flash -> deepseek-v4-flash
v4-pro   -> deepseek-v4-pro
```

这些名称会原样发送给配置的兼容 API，服务端是否提供对应模型需要由实际 API 验证。

## 开始使用

进入希望 Agent 操作的目录，再启动 CLI：

```powershell
cd "D:\APP_self\临时的测试目录"
agent-harness
```

也可以显式指定入口和模型：

```powershell
agent-harness code --model v4-flash
```

CLI 会连续接收输入，不需要每轮重新运行命令。恢复最近的 Thread 或指定 Thread：

```powershell
agent-harness resume
agent-harness resume thread_xxx
```

一次性非交互执行会单独创建 Run：

```powershell
agent-harness exec --workspace . --task "请分析这个目录并给出主要模块。"
```

## 交互命令

常用命令：

```text
/help                         查看命令
/status                       查看当前 Thread 状态
/usage                        查看 token 和成本摘要
/trace [full|raw]             查看本 Turn Trace
/statusline                   查看状态栏字段
/permissions <preset>         使用 plan/auto/manual/full-access 预设
/sandbox                      查看沙箱状态
/approvals                    查看审批记录
/mcp                          查看 MCP Server 状态
/guidance                     查看 Guidance Snapshot
/skills                       查看 Skill Catalog
/new                          新建 Thread
/exit                         退出
```

当前 CLI 只在 Turn 结束后读取下一次输入。在模型或工具正在执行时输入的新消息还不能 steer 当前 Turn，`/interrupt` 的完整交互链也尚未接入。

## CLI 命令

```text
agent-harness                  启动持续交互
agent-harness code             启动持续交互，可带首条任务
agent-harness exec             执行一次性任务
agent-harness resume           恢复 Thread
agent-harness threads          列出当前目录的 Thread
agent-harness inspect          查看 Thread 或 Run 摘要
agent-harness recover          查看非终态 Turn 的恢复计划
agent-harness tools            列出当前工作空间工具
agent-harness memory           管理项目 Memory
agent-harness mcp              管理用户级 MCP Server
agent-harness migrate-sessions 迁移旧 Session 数据
agent-harness setup            配置 Provider
```

各命令参数以 `agent-harness <command> --help` 为准。

## 工具与执行循环

内置工作空间工具包括：

- `list_files`：列出文件。
- `read_file`：读取文本文件。
- `search_text`：搜索文本。
- `write_file`：受控写入文件。
- `apply_patch`：按预期旧文本执行结构化精确替换，不是通用 Unified Diff Parser。
- `delete_path`：删除路径，默认需要审批。
- `run_command`：执行结构化 argv，不接受 shell 管道、重定向或复合命令。

模型可以在一个 Turn 中多次调用工具，直到生成最终结果或触发预算、审批、取消或错误终态。Tool allowlist、Agent capability 和 Permission 会在执行时再次求交集，模型输出不能绕过这些限制。

## 权限与沙箱

默认配置为 `workspace-write + on-request + network-off`。文件、命令和 MCP 工具统一进入权限决策链，执行主体由 `ToolExecutionPrincipal` 标识，规则优先级是 `DENY > ASK > ALLOW`。

权限判断不是 OS 沙箱。当前没有 Native Windows Restricted Token / ACL / Job Object 后端。Windows 的受保护命令路径是：

```text
agent-harness -> wsl.exe -> WSL2 -> bubblewrap -> command
```

安装示例：

```powershell
wsl --install -d Ubuntu
wsl -d Ubuntu -- sudo apt-get update
wsl -d Ubuntu -- sudo apt-get install -y bubblewrap
```

缺少 WSL2 发行版或 bubblewrap 时，要求沙箱的命令会 fail closed。`--danger-full-access` 会显式关闭 OS 沙箱并扩大主机访问，仅应在用户理解风险后使用。

## Project Guidance

Thread 启动、恢复或显式 reload 时会发现指导文件，并将内容冻结到 Thread Snapshot：

- 用户级：`%APPDATA%\agent-harness\AGENTS.override.md` 或 `AGENTS.md`。
- 项目级：从 Git/Workspace Root 到当前目录逐层读取 `AGENTS.override.md`、`AGENTS.md`，默认也兼容 `CLAUDE.md`。
- 路径规则：`.agents/rules/**/*.md`，支持 `paths` 和 `exclude` Frontmatter。
- Import：独立行 `@import relative/path.md`，受深度、文件数、字节数和真实路径边界限制。

项目 Guidance 受 Workspace Trust 控制。它影响模型决策，但不能代替 Permission 和 OS 隔离。交互命令为 `/trust`、`/guidance`、`/guidance inspect <id>` 和 `/guidance reload`。

## Agent Skills

Harness 发现标准 `SKILL.md`，启动时只向模型披露有限 Catalog，激活后才读取完整正文：

- 用户显式调用：`$skill-name 参数`。
- 模型激活：`activate_skill`。
- Skill 资源：`read_skill_resource`，当前仅支持 Manifest 内的 UTF-8 文本文件。
- `context: fork`：在受 Parent、AgentDefinition 和 Skill 三方限制的 Subagent Context 中执行。
- `allowed-tools`：只能收窄权限，不能预批准或扩大权限。

`scripts/` 当前只作为资源列出，不会自动执行。交互命令为 `/skills`、`/skills active`、`/skills inspect <name>` 和 `/skills reload`。

## MCP Client

用户级 MCP 配置保存在：

```text
%APPDATA%\agent-harness\mcp.json
```

添加 stdio Server：

```powershell
agent-harness mcp add local-server python path\to\server.py
```

添加 Streamable HTTP Server：

```powershell
agent-harness mcp add remote https://example.com/mcp --transport streamable_http
agent-harness mcp add oauth-server https://example.com/mcp --transport streamable_http --oauth
agent-harness mcp login oauth-server
```

管理命令：

```powershell
agent-harness mcp list
agent-harness mcp get <name>
agent-harness mcp remove <name>
agent-harness mcp logout <name>
```

OAuth token 使用系统 keyring，不写入 `mcp.json`。当前已实现 stdio、Streamable HTTP、404 reinitialize、分页、Tools、Resources、Prompts、OAuth 基础和 Artifact 结果；SSE、Sampling、Elicitation、Tasks、Apps、Resource Subscription 与跨进程 MCP Session 恢复未实现。

Thread 内可使用 `/mcp resources [server]`、`/mcp resource <server> <uri>`、`/mcp prompts [server]` 和 `/mcp prompt <server>/<name> key=value`，读取的内容会排队进入下一 Turn 上下文。

## Memory 与 Compaction

Project Memory 独立保存在 `.harness/memory.sqlite3`，按规范化项目 identity 隔离。当前支持显式新增、搜索、列出、失效和删除：

```powershell
agent-harness memory add "项目测试使用 uv run pytest"
agent-harness memory search "pytest"
agent-harness memory list
agent-harness memory invalidate memory_xxx --reason "配置已变化"
agent-harness memory delete memory_xxx
```

自动 Memory 提取默认关闭，冲突、替代和依赖失效尚未形成完整闭环。Context Compaction 已实现 idle-only 安全基础：保留 canonical rollout、保护 Tool 配对并验证 source hash；摘要目前仍是确定性文本压缩，不是完整的智能事实总结器。

## 数据与恢复

所有运行数据默认写入正在操作的项目目录：

```text
.harness/
  threads/<thread_id>/
    metadata.json
    rollout.jsonl
    events.jsonl
    result.json
    turns/<turn_id>-result.json
    snapshots/
    artifacts/
    agents/
  runs/<run_id>/
    events.jsonl
    result.json
  runtime.sqlite3
  memory.sqlite3
```

`rollout.jsonl` 使用单调 sequence 和 SHA-256 hash chain；末尾不完整记录可隔离，中间损坏会 fail closed。`runtime.sqlite3` 使用 SQLite WAL 保存 Checkpoint、审批和恢复状态。

检查 Thread 与恢复计划：

```powershell
agent-harness threads
agent-harness inspect thread_xxx --thread
agent-harness recover thread_xxx --status
```

当前恢复能力只覆盖已定义的安全边界。系统尚无跨进程 Thread execution lease、全量 Transactional Outbox、完整文件 pre/post hash reconciliation，以及人工 `mark-tool-*` 恢复命令，因此不能承诺所有外部副作用 exactly-once。

## 测试

```powershell
uv lock --check
uv run --no-sync python -m ruff check src tests
uv run --no-sync python -m mypy src
uv run --no-sync python -m pytest -m "unit or integration_local" -q
uv run --no-sync python -m pytest -m recovery_process -q
uv run --no-sync python -m pytest -m platform_linux -q
uv run --no-sync python -m pytest -m platform_windows -q
uv run --no-sync python -m pytest -m live_provider -q
uv run --no-sync python -m pytest -m live_oauth -q
```

默认核心测试使用 Fake Provider 或本地 fixture，不调用真实外部 API。平台测试和 live tests 需要对应环境；skip 不等于通过。最新未落实项与验收边界以仓库根目录的 `doc/General-Agent-Design_当前未落实问题总清单与后续实施基线.md` 为准。
