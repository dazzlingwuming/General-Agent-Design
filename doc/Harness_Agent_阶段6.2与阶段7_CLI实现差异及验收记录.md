# Harness Agent 阶段 6.2 与阶段 7 CLI 实现差异及验收记录

> 实施日期：2026-07-13
> 设计依据：`doc/Harness_Agent_阶段6.2修复与阶段7_CLI可观测性Trace详细设计.md`
> 本文只记录仓库真实实现，不以设计文档代替验收事实。

## 1. 已完成内容

### 1.1 阶段 6.2 Turn Reset

- 新增统一 `_reset_state_for_new_turn()`。
- 每个新 Turn 重置 `started_at`、`updated_at`、`completed_at`、Usage、取消状态和 Agent Summary。
- 保留 Thread ID、Turn Count、历史 Messages、Session Summary、Workspace 和 Agent Name。
- `RunState.usage_total` 的正式语义改为当前 Turn 累计 Usage。
- `result.json` 增加 `usage_scope = "turn"`，第二个 Turn 的 duration 不再包含 Turn 间空闲时间。

### 1.2 Provider Usage 与价格快照

- `Usage` 新增缓存未命中和 reasoning token 字段。
- DeepSeek 映射 `prompt_cache_hit_tokens`、`prompt_cache_miss_tokens` 和 `completion_tokens_details.reasoning_tokens`。
- DeepSeek 上下文能力按当前官方 V4 文档调整为 1M。
- 新增 `PricingSnapshot`、`ModelUsageRecord`、`UsageSnapshot` 和 `UsageReducer`。
- V4 Flash/Pro 的人民币价格以 2026-07-13 官方页面快照写入代码；每个模型完成事件内嵌当时快照，历史 Replay 不使用未来价格重算。
- 金额使用 `Decimal`；reasoning tokens 只展示，不重复加入输出成本。
- Fake Provider 或未知模型没有价格时显示 `n/a`，不猜测为零。

官方依据：

- [DeepSeek 模型与价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)
- [DeepSeek Chat Completion Usage](https://api-docs.deepseek.com/api/create-chat-completion)
- [DeepSeek Context Caching](https://api-docs.deepseek.com/guides/kv_cache)

### 1.3 Typed Trace Pipeline

- 新增版本化 `TraceEvent` v2，保留 v1 Replay 兼容。
- 新增 `TraceSink`、`CompositeTraceSink` 和进程内 `RuntimeEventBus`。
- 同一个 Event 实例先持久化 JSONL，再发布给 Live Subscriber；UI 失败不会触发 Tool 重放。
- Trace Replay 检查 sequence 严格递增。
- 新增 `RuntimePhase`、`TraceReducer` 和三层 Usage Reducer；Replay 与 Live 使用同一套归约逻辑。
- `model.response.reused` 不增加 Token 和成本。
- 模型事件包含 provider、model、response ID、duration、Usage、context window 和价格快照。
- 工具事件包含 correlation ID、logical action ID、duration、输出长度和有界预览。

### 1.4 CLI

- TTY 下使用 `prompt_toolkit.PromptSession.prompt_async()`、Slash Command 补全和动态 Bottom Toolbar。
- 使用 Rich 输出 TTY Transcript；非 TTY 自动走无 ANSI、编码安全的 Plain Output。
- Windows PowerShell 管道输入会在 UTF-8 持久化前替换非法 surrogate。
- 新增 `/status`、`/usage`、`/usage raw`、`/trace`、`/trace full`、`/trace raw`、`/statusline`、`/statusline reset`、`/statusline set` 和 `/help`。
- `/status` 分开显示 Last Call、Current Turn、Thread Lifetime 和 Current Context。
- Tool 输出默认折叠为八行；`/trace full` 展开持久化预览。
- API Key、Authorization、access/refresh token、Secret、Password 和 Cookie 在所有 CLI raw 视图中递归脱敏。
- `agent-harness exec` 保持脚本化纯文本行为，不依赖 Web Server 或浏览器。

架构参考：

- [Codex CLI developer commands](https://developers.openai.com/codex/cli/slash-commands)
- [Codex CLI features](https://developers.openai.com/codex/cli/features)
- [prompt_toolkit asyncio](https://python-prompt-toolkit.readthedocs.io/en/master/pages/advanced_topics/asyncio.html)
- [Rich Live display](https://rich.readthedocs.io/en/stable/live.html)

## 2. 与设计文档不同的地方

### 2.1 保留现有 `JsonlTraceSink.emit()` 外观

设计示例把 `TraceEmitter` 独立成新类。当前项目大量 Runtime、Subagent 和安全审计代码依赖 `JsonlTraceSink.emit()`，因此本次保留该兼容外观，在内部创建一次 `TraceEvent` 后交给 `CompositeTraceSink`。语义符合“一个事件、多个 Sink”，但类拆分没有照抄示例。

### 2.2 Subagent 事件不强制改名

阶段 2 已持久化 `agent.spawned`、`agent.completed` 等事件，而设计建议 `subagent.*`。本次 Reducer 和 Renderer 同时识别两套名称，没有迁移历史事件，也没有为界面修改 Durable Subagent 契约。

### 2.3 第一版是 Transcript First，不是完整全屏 TUI

已实现 committed model/tool/approval/subagent/recovery/error cells 和 Bottom Toolbar，但没有实现 Ratatui 风格全屏布局，也没有持续刷新的 Spinner Active Cell。原因是文档明确将完整全屏 IDE 和完整 Codex TUI 排除在本阶段目标外。

### 2.4 `/trace full` 展开的是持久化有界预览

ToolRuntime 原有 Artifact 机制仍负责超长结果，Trace 只持久化最多 4000 字符的输出预览。因此 `/trace full` 不保证打印整个任意大工具结果；完整大对象应从 Artifact 读取。这避免 Trace 与 Artifact 重复保存大内容。

### 2.5 状态行配置是进程内配置

`/statusline set/reset` 当前修改本进程的 `TuiConfig`，没有回写用户 TOML。设计文档只要求第一版基础配置，完整持久化 Picker 延后。

### 2.6 Trace v1 兼容采用读取升级

旧事件读取为内存中的 schema v1 Event，再由当前 Reducer 兼容旧事件名；不会重写已有 `events.jsonl`，避免破坏阶段 6 的追加写和完整性假设。

## 3. 本轮明确未完成或继续延期

- 没有 Token-by-token Streaming。
- 没有全屏 Active Spinner、交互式 Status Line Picker 或图形 DAG Trace。
- 没有 Web UI、Benchmark、Prometheus、OpenTelemetry 后端或分布式 Trace。
- 没有 Provider 最终账单核对；CLI 成本始终标记 `est`。
- 没有扩大阶段 6.1 已记录的 Subagent 跨进程恢复、多进程 Lease、Transactional Outbox 和文件 pre/post hash 能力。
- 本轮没有调用真实 DeepSeek API；Provider Usage 映射使用官方响应结构和单元测试验证，真实账号费用仍需 live test 单独验收。

以上项目不属于本阶段第一版已声明能力，不能据此宣称完整复制 Codex CLI。

## 4. 验收结果

实施前基线：

```text
Ruff: passed
Mypy: passed, 130 source files
Pytest unit/integration_local: 134 passed, 1 skipped, 3 deselected
```

实施后最终结果：

```text
Ruff: passed
Mypy: passed, 137 source files
Pytest unit/integration_local: 141 passed, 1 skipped, 3 deselected
Pytest recovery_process: 1 passed, 144 deselected
git diff --check: passed
```

另外完成 Windows 非 TTY 冒烟：

```text
PowerShell pipeline -> agent-harness code --provider fake
/status、普通 Turn、/usage、/trace、/exit 可连续执行
agent-harness exec 保持纯文本输出
```
