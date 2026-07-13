# General Agent Design

General Agent Design 是一个面向本地环境的通用 AI Agent Runtime 项目。它的目标不是只封装一次模型请求，而是为可持续工作的 Agent 提供统一运行基础：多轮会话、工具循环、权限审批、子 Agent、项目指导、按需 Skill、MCP 外部能力、本地持久化、恢复和可观测性。

当前最完整的产品入口是 `agent-harness` CLI，主要服务于代码仓库分析和修改。因此它现在可以作为 Coding Agent 使用，但底层的 Thread / Turn / Item、Tool、Permission、Memory 和 MCP 设计并不限定于编程任务。

> 项目仍在开发中。现有实现不能描述为完整复刻 Codex 或 Claude Code，也不能宣称已经完成 Windows 原生沙箱和任意副作用的 exactly-once 恢复。

## 核心模型

- **Thread**：一段可恢复的长期交互，包含多个 Turn。
- **Turn**：用户的一次输入及其触发的模型、工具和审批循环。
- **Item**：追加写入的消息、模型调用、工具结果、审批和终态记录。
- **Tool Runtime**：统一执行内置工具、Skill 控制工具和 MCP 工具。
- **Permission**：独立于模型输出的强制执行边界，规则优先级为 `DENY > ASK > ALLOW`。
- **Checkpoint / Rollout**：分别保存可恢复运行状态和可审计的 canonical history。

## 当前能力

- 持续交互、Thread 创建与恢复、一次性 `exec` 执行。
- DeepSeek-compatible 模型适配和多轮 Tool Calling。
- 文件读取、搜索、受控写入、精确替换、删除和命令执行工具。
- 进程内 Subagent 委派、跟进、等待、取消和结构化结果。
- Workspace Trust、权限审批、Project Guidance 和 Agent Skills。
- MCP stdio / Streamable HTTP Client，支持 Tools、Resources、Prompts 和 OAuth 基础流程。
- SQLite Checkpoint、项目 Memory、Rollout 完整性链和 idle-only Context Compaction。
- CLI Trace、Usage、成本估算、状态栏和 JSONL 事件记录。

## 快速开始

要求 Python 3.11 或更高版本。在仓库中安装 CLI：

```powershell
cd agent-harness
python -m pip install -e .
agent-harness setup
```

设置 `DEEPSEEK_API_KEY` 后，进入需要处理的目录直接启动：

```powershell
cd "D:\path\to\your-project"
agent-harness
```

安装命令会把当前源码包以 editable 方式注册到当前 Python 环境，因此后续可以在任意目录调用 `agent-harness`。完整的配置、命令、MCP 和数据目录说明见 [agent-harness/README.md](agent-harness/README.md)。

## 仓库结构

- `agent-harness/src/agent_harness/`：Agent Runtime 和 CLI 实现。
- `agent-harness/tests/`：单元、本地集成、平台和 live 测试。
- `doc/`：各阶段设计、审计、实施差异及当前问题基线。
- `AGENTS.md`：本仓库开发和安全约定。
- `.github/workflows/agent-harness.yml`：自动化质量门禁。

## 开发验证

依赖由 `uv.lock` 锁定。以下命令从 `agent-harness/` 执行：

```powershell
uv sync --locked --extra test
uv lock --check
uv run --no-sync python -m ruff check src tests
uv run --no-sync python -m mypy src
uv run --no-sync python -m pytest -m "unit or integration_local" -q
```

`platform_linux`、`platform_windows`、`live_provider`、`live_oauth` 和 `recovery_process` 需要单独环境。测试被 skip 只表示前置条件不满足，不代表对应能力已验收。

## 已知边界

- CLI 在一个 Turn 执行期间尚不能并发接收 steer / interrupt 输入。
- 同一 Thread 没有跨进程执行租约，Subagent 也不能跨进程恢复。
- Outbox 和副作用 reconciliation 尚未覆盖全部 durable transition。
- Windows 没有 Restricted Token / ACL / Job Object 原生沙箱；当前受保护命令路径依赖 WSL2 与 bubblewrap。
- Context Compaction 和 Project Memory 已有保守基础实现，但尚无完整的智能摘要、自动记忆提取和冲突管理。
- DeepSeek Adapter 尚不支持 token streaming。
- MCP 的 SSE、Sampling、Elicitation、Tasks、Apps、Resource Subscription 和跨进程 Session 恢复仍延期。

最新、逐项且带代码证据的问题清单见 [当前未落实问题总清单与后续实施基线](doc/General-Agent-Design_当前未落实问题总清单与后续实施基线.md)。历史阶段文档用于保留设计演进，不能替代当前实现状态。
