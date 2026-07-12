# Agent Harness

这是一个本地 CLI Coding Agent Harness。当前产品形态是 Codex 式持续交互：进入一个代码目录后创建或恢复一个 Thread，用户连续输入，每次输入形成一个 Turn，消息、模型输出、工具结果和终态以 append-only Item 写入 rollout。

当前还包含权限执行链、Project Guidance、Agent Skills 和 Stage 5 MCP Client Runtime。各阶段的真实完成差异见仓库根目录 `doc/`，沙箱和明确延期能力不能描述为已完成。

## 权限与沙箱

默认安全配置为 `workspace-write + on-request + network-off`。所有 Tool 执行必须携带 `ToolExecutionPrincipal`，规则采用 `DENY > ASK > ALLOW`，文件写入、删除和命令执行统一经过 `ToolRuntime`。

Windows 主机不会直接把普通 PowerShell 子进程当作沙箱。受保护的命令执行路径是：

```text
agent-harness -> wsl.exe -> WSL2 -> bubblewrap -> command
```

如果没有安装 WSL2 Linux 发行版或发行版中没有 `bubblewrap`，`run_command` 会失败关闭，不会静默在 Windows Host 上执行。安装示例：

```powershell
wsl --install -d Ubuntu
wsl -d Ubuntu -- sudo apt-get update
wsl -d Ubuntu -- sudo apt-get install -y bubblewrap
```

交互模式可使用 `/permissions`、`/sandbox`、`/approvals`。权限预设包括 `plan`、`auto`、`manual` 和需要二次确认的 `full-access`。`--danger-full-access` 会显式关闭 OS 沙箱，不能由模型或未确认的项目配置启用。

阶段 3 新增 `write_file`、结构化精确替换版 `apply_patch`、默认需要审批的 `delete_path`，以及不支持 shell、管道和重定向的 `run_command`。

## 已实现

- CLI 交互模式：`agent-harness` / `agent-harness code`
- 一次性任务模式：`agent-harness exec`
- Thread 持久化：`.harness/threads/<thread_id>/metadata.json` + `rollout.jsonl`
- Thread 恢复：`agent-harness resume [thread_id]`
- Thread 查看：`agent-harness threads`、`agent-harness inspect --thread <thread_id>`
- 旧 Session 迁移：`agent-harness migrate-sessions`
- `list_files`, `read_file`, `search_text`
- DeepSeek-compatible adapter，包含 `reasoning_content` round-trip
- 执行时 Tool allowlist / capability 检查
- 文件工具使用 `asyncio.to_thread()` 避免阻塞 event loop
- 阶段 2 Subagent Runtime：spawn、wait、follow-up、cancel、close、structured result

## 仍未完成

- CLI 在模型执行中并发读取用户输入并调用 `turn/steer`
- 完整 ThreadRuntime / TurnController 分层
- Child Thread 独立 rollout
- Pydantic Tool Input Model 替代当前内置 JSON Schema 子集校验
- Native Windows Restricted Token / ACL / Job Object 沙箱后端
- 审批后自动持久化用户规则
- 通用 Unified Diff Patch Parser
- Context Compaction 算法
- 精确 checkpoint resume

## 安装

```bash
python -m pip install -e .[test]
```

开发和 CI 使用锁定环境：

```bash
uv sync --locked --extra test
```

## 交互式使用

先进入你要分析的目录：

```powershell
cd "D:\APP_self\临时的测试目录"
```

然后运行：

```powershell
agent-harness
```

或指定模型：

```powershell
agent-harness code --model v4-flash
```

常用交互命令：

```text
/status
/new
/exit
```

恢复最近 Thread：

```powershell
agent-harness resume
```

恢复指定 Thread：

```powershell
agent-harness resume thread_xxx
```

一次性执行：

```powershell
agent-harness exec --workspace tests/fixtures/demo_repo --task "请分析这个目录。"
```

## 配置 DeepSeek

先把 API Key 放进环境变量，例如 PowerShell：

```powershell
setx DEEPSEEK_API_KEY "你的 API Key"
```

然后运行：

```powershell
agent-harness setup
```

配置文件只保存 API URL、模型和 `api_key_env`，不会写入 API Key 明文。

可选模型：

```text
v4-flash -> deepseek-v4-flash
v4-pro   -> deepseek-v4-pro
```

## 查看数据

```bash
agent-harness threads
agent-harness inspect --thread <thread_id>
agent-harness tools --workspace tests/fixtures/demo_repo
```

交互式 Thread：

```text
.harness/threads/<thread_id>/
  metadata.json
  rollout.jsonl
  events.jsonl
  result.json
  turns/turn_0001-result.json
```

一次性 `exec` 任务：

```text
.harness/runs/<run_id>/
  events.jsonl
  result.json
```

## 测试

```bash
uv run --no-sync python -m ruff check src tests
uv run --no-sync python -m mypy src
uv run --no-sync python -m pytest -m "unit or integration_local" -q
uv run --no-sync python -m pytest -m platform_linux -q
uv run --no-sync python -m pytest -m live_provider -q
```

默认核心测试使用 Fake Provider 或本地 fixture，不调用真实 API。平台测试和 live tests 单独选择；缺少对应平台能力或 `DEEPSEEK_API_KEY` 时会明确跳过，skip 不等于平台验收通过。

## Project Guidance

Harness 在 Thread 启动、恢复或显式 Reload 时发现指导文件，并将完整内容冻结到 `.harness/threads/<thread-id>/snapshots/`：

- 用户级：`%APPDATA%/agent-harness/AGENTS.override.md` 或 `AGENTS.md`。
- 项目级：从 Git/Workspace Root 到当前目录逐层读取 `AGENTS.override.md`、`AGENTS.md` 或配置的 fallback。
- 路径规则：`.agents/rules/**/*.md`，可使用 `paths` 和 `exclude` YAML Frontmatter。
- Import：只识别独立行 `@import relative/path.md`，并限制深度、数量、字节和真实路径边界。

项目 Guidance 受 Workspace Trust 控制。Guidance 影响模型决策，但不是强制安全机制；Permission 和 Hook 才是执行边界。

CLI 命令：`/guidance`、`/guidance inspect <id>`、`/guidance reload`、`/trust`。

## Agent Skills

Harness 支持 Agent Skills 标准的 `SKILL.md`。启动时只把 `name`、`description` 和路径放入有限 Catalog，激活后才加载完整正文。

- 用户显式调用：`$code-review 参数`。
- 模型调用：`activate_skill`。
- 资源读取：`read_skill_resource`，只能读取已激活 Skill Manifest 内的 UTF-8 文件。
- `scripts/` 只作为资源列出，阶段 4 不会自动执行。
- `context: fork` 使用独立 Subagent Context，Child 工具是 Parent、AgentDefinition 和 Skill 限制的交集。
- Harness 将 `allowed-tools` 解释为权限收窄，不能预批准或扩大权限。

CLI 命令：`/skills`、`/skills active`、`/skills inspect <name>`、`/skills reload`。

Guidance 用于每个 Thread 都需要的项目约定；Skill 用于按需加载的工作流；Tool 是原子操作；Permission 是强制边界；Memory 不属于阶段 4。
