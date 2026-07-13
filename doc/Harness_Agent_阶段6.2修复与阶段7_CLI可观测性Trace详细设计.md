# General Agent Harness：阶段 6.2 修复与阶段 7 CLI 可观测性、Token 成本及 Trace 可视化详细设计

> 文档版本：v1.0
> 文档日期：2026-07-13
> 目标仓库：`https://github.com/dazzlingwuming/General-Agent-Design`
> 当前基线提交：`bd64aedd2d6999368de35c597543f76d664d76d9`
> 基线提交说明：`Harden stage 6 recovery semantics`
> 文档用途：直接交给 Codex，指导阶段 6.2 小型修复与阶段 7 CLI 实现
> 阶段 7 新定位：不建设 Web UI，不建设 Benchmark 成功率平台，优先完成 Codex CLI 风格的状态、Usage、成本和 Trace 展示

---

# 一、范围重新确定

此前阶段 7 曾考虑：

```text
评测平台
Web UI
成功率、Pass@1、回归率等统计
复杂结果页面
```

本轮明确不做这些内容。

当前项目是个人本地 Coding Agent，阶段 7 更重要的目标不是建设企业级评测平台，而是让用户在终端中清楚看到：

```text
Agent 当前处于哪个阶段
模型何时被调用
工具何时开始和结束
工具执行了什么
审批为什么出现
子 Agent 当前在做什么
本 Turn 与整个 Thread 消耗了多少 Token
当前上下文还剩多少
按照价格快照估算花费多少
失败发生在哪个阶段
恢复后从哪里继续
```

因此阶段 7 正式调整为：

```text
阶段 7：CLI 可观测性、Usage Accounting 与 Trace 可视化
```

其核心参考对象是 Codex CLI，而不是 Web Agent 控制台。

---

# 二、本阶段最终目标

完成后，用户进入交互模式：

```bash
agent-harness code
```

应看到类似 Codex CLI 的终端体验：

```text
> 修复阶段 6.2 的 Turn 状态重置问题，并运行测试。

• 正在检查当前 Thread 和工作区状态

• Ran git status --short
  └ M agent-harness/src/agent_harness/runtime/session.py

• 正在读取相关 Runtime 模块

• Ran read_file agent-harness/src/agent_harness/runtime/session.py
  └ ... 84 lines hidden (/trace full 查看完整输出)

• 正在调用 deepseek-v4-pro
  └ 8.7k input · 412 output · 1.9s

• 正在修改 Turn 初始化逻辑

• Ran apply_patch agent-harness/src/agent_harness/runtime/session.py
  └ Patch applied successfully

• 正在运行专项测试

• Ran uv run --no-sync python -m pytest tests/unit/test_turn_state.py -q
  └ 4 passed in 0.82s

任务已完成。

  修改：
  - 新 Turn 会重新初始化时间、Usage、取消状态和 Agent Summary。
  - Thread 历史消息仍然保留。
  - 增加了多 Turn 回归测试。
```

底部状态行可以显示：

```text
Working · deepseek-v4-pro · ctx 78% left · turn 9.1k · thread 31.4k · ¥0.04 est · workspace
```

输入：

```text
/status
```

应显示当前 Thread、Turn、运行阶段、模型、权限、Token、上下文和估算成本。

输入：

```text
/trace
```

应显示当前 Turn 的结构化执行时间线。

---

# 三、明确非目标

阶段 7 第一版不实现：

- 不实现 Web UI；
- 不启动 FastAPI、React 或浏览器页面；
- 不建设成功率、Pass@1、回归率等评测平台；
- 不建设大规模 Benchmark Runner；
- 不建设 LLM Judge；
- 不建设在线 A/B Test；
- 不建设 Prometheus、Grafana 或完整 OpenTelemetry 平台；
- 不建设分布式 Trace 服务；
- 不建设多租户、账号、权限后台；
- 不实现复杂全屏 IDE；
- 不实现图形化 DAG Trace；
- 不公开模型隐藏思维链；
- 不把 `reasoning_content` 原样显示给用户；
- 不为了界面重写现有 Agent Runtime；
- 不将 UI 组件直接耦合到 Provider、ToolRuntime 或 SQLite；
- 不要求所有 Provider 立刻支持 Token Streaming；
- 不要求第一版复制 Codex CLI 的所有快捷键和页面。

阶段 7 第一版仍然是：

```text
本地 Python Agent Runtime
+
本地交互式 CLI
+
结构化 Trace
+
可重放的终端 Transcript
```

---

# 四、成熟系统调研结论

## 4.1 Codex CLI 的 `/status`

Codex CLI 官方文档说明，`/status` 用于展示：

- 当前模型；
- 当前目录；
- Approval Policy；
- 可写目录；
- Token Usage；
- Context Capacity；
- Session/Thread 信息。

Codex 没有把“当前 Context 使用量”和“整个 Session 累计消耗”混成一个数字。

其源码中也明确分开：

```text
total_usage
    整个 Session 累计 Token

last_token_usage
    最近一次模型调用占用的上下文

model_context_window
    模型上下文窗口大小
```

本项目必须借鉴这个区分。

## 4.2 Codex CLI 的 Status Line

Codex CLI 当前支持可配置的 Status Line，可展示：

- Model；
- Reasoning Level；
- Current Directory；
- Project Root；
- Git Branch；
- Run State；
- Permission；
- Approval Mode；
- Context Remaining；
- Context Used；
- Context Window Size；
- Used Tokens；
- Total Input Tokens；
- Total Output Tokens；
- Thread ID；
- Task Progress；
- Version。

本项目不需要一次实现全部选项，但架构上不能把状态行写死成一个字符串。

## 4.3 Codex CLI 的事件驱动结构

Codex TUI 没有让界面直接读取 Agent 内部变量。

其核心模式是：

```text
Core / App Server
    ↓
Typed Server Notifications
    ↓
ChatWidget 处理通知
    ↓
HistoryCell / Active Cell
    ↓
终端 Transcript 与状态行
```

Codex 内部存在明确的应用事件总线。Widget 发出事件，由顶层 App 处理；Widget 不直接操作 Runtime 内部状态。

本项目应借鉴：

```text
Agent Runtime
    ↓
Typed TraceEvent
    ↓
RuntimeEventBus
    ├── JsonlTraceSink
    └── CliTraceRenderer
```

## 4.4 Codex 的 HistoryCell

Codex 把终端对话历史中的一个可展示单元抽象为 `HistoryCell`。

它同时支持：

```text
Committed Cell
    已经结束，写入终端历史，不再变化

Active Cell
    当前正在执行，可动态更新，例如 Spinner、耗时、流式输出
```

这正是用户截图中的体验来源：

```text
• Ran <command>
  └ <output>

• 正在执行下一阶段
```

工具完成后，显示块固定在 Scrollback 中；新的活动状态显示在下面。

本项目应采用同样的“Transcript First”设计，而不是每次刷新整块屏幕。

## 4.5 DeepSeek Usage

DeepSeek Chat Completion 响应中的 `usage` 可以提供：

- `prompt_tokens`
- `completion_tokens`
- `total_tokens`
- `prompt_cache_hit_tokens`
- `prompt_cache_miss_tokens`
- `completion_tokens_details.reasoning_tokens`

Token 数量应优先使用 Provider 返回值。

字符数除以比例的估算方式只能用于：

```text
模型调用前预测上下文是否超限
```

不能作为最终 Token 账单。

## 4.6 Python 终端技术选型

Codex 使用 Rust 与 Ratatui。本项目是 Python，不应直接模仿其技术栈，而应借鉴其架构。

阶段 7 建议使用：

```text
prompt_toolkit
    输入框
    PromptSession
    异步输入
    Slash Command 补全
    Key Binding
    Dynamic Bottom Toolbar
    输入历史

Rich
    彩色文本
    Tree
    Panel
    Syntax
    Spinner
    Live Active Cell
    自动检测 TTY
    Plain Text 回退
```

不使用 Textual，原因是第一版不需要完整全屏应用框架。

不使用 Curses，原因是跨平台、Unicode、输入法和 Windows 支持成本较高。

---

# 五、阶段 6.2：进入阶段 7 前的最后修复

## 5.1 当前问题

当前 `ConversationSession` 会在同一个 Thread 内复用同一个 `RunState`。

开始新 Turn 时已经重置：

```text
turn_id
task
final_output
error
iteration
model_call_count
tool_call_count
status
```

但尚未重置：

```text
started_at
updated_at
completed_at
cancellation_requested
agent_summary
usage_total
```

这会导致：

### Wall-time Budget 错误

第二个 Turn 可能继续使用第一个 Turn 的 `started_at`。

结果是：

```text
用户在两个 Turn 之间停留了 10 分钟
    ↓
第二个 Turn 一开始
    ↓
wall-time budget 认为已经运行了 10 分钟
```

### Turn Duration 错误

`result.json` 使用：

```text
started_at → completed_at
```

计算 Duration。

如果 `started_at` 未重置，第二个 Turn 的耗时会包含两个 Turn 之间的空闲时间。

### Usage 污染

`usage_total` 当前保存在复用的 `RunState` 中。

如果不重置：

```text
第二个 Turn 的 result.json
```

会包含此前 Turn 的累计 Usage，无法区分：

```text
当前 Turn Token
整个 Thread Token
```

### Cancellation 污染

若上一 Turn 留下：

```python
cancellation_requested = True
```

新 Turn 可能立即进入取消逻辑。

### Agent Summary 污染

上一 Turn 的 Subagent Summary 可能进入下一 Turn 的结果。

---

## 5.2 修复要求

在 `ConversationSession.run_turn()` 创建新 Turn 时，统一调用：

```python
_reset_state_for_new_turn(state, turn_id, user_input)
```

不要继续分散手动赋值。

建议：

```python
from agent_harness.domain.model import Usage
from agent_harness.utils.time import utc_now


def _reset_state_for_new_turn(
    state: RunState,
    *,
    turn_id: str,
    task: str,
) -> None:
    now = utc_now()

    state.turn_id = turn_id
    state.task = task

    state.status = RunStatus.CREATED
    state.iteration = 0
    state.model_call_count = 0
    state.tool_call_count = 0

    state.usage_total = Usage()

    state.started_at = now
    state.updated_at = now
    state.completed_at = None

    state.final_output = None
    state.error = None
    state.cancellation_requested = False
    state.agent_summary = None
```

必须保留：

```text
run_id / thread_id
turn_count
messages
session_summary
workspace_root
agent_name
```

因此不能简单替换成全新的 `RunState`。

---

## 5.3 Usage 语义重新确定

从阶段 6.2 开始：

```text
RunState.usage_total
    只表示当前 Turn 的累计 Usage
```

整个 Thread 的 Usage 不再通过复用 `RunState.usage_total` 保存。

Thread Usage 由阶段 7 的 `UsageReducer` 从持久 Trace 中重建：

```text
Thread Usage
=
该 Thread 所有 model.response.completed 事件中的 Usage 之和
```

这样可以同时得到：

```text
Current Model Call Usage
Current Turn Usage
Thread Lifetime Usage
```

三者不会混淆。

---

## 5.4 阶段 6.2 测试

新增：

```text
tests/unit/test_turn_state_reset.py
```

必须覆盖：

### 时间重置

```text
Turn 1 完成
修改或等待一段时间
Turn 2 开始
Turn 2 started_at > Turn 1 started_at
Turn 2 completed_at 在执行前为 None
```

### Usage 重置

```text
Turn 1 usage_total = 非零
Turn 2 开始
Turn 2 usage_total 全部归零或 None
历史 Trace 中的 Turn 1 Usage 不受影响
```

### Cancellation 重置

```text
Turn 1 cancellation_requested = True
Turn 2 开始
cancellation_requested = False
```

### Summary 重置

```text
Turn 1 agent_summary 非空
Turn 2 开始
agent_summary = None
```

### 历史不丢失

```text
Turn 2 开始后
Turn 1 的 user / assistant / tool messages 仍保留
```

### Duration 正确

第二个 Turn 的 `result.json.duration_ms` 只计算第二个 Turn。

---

# 六、阶段 7 总体架构

## 6.1 设计原则

```text
Runtime 产生事实
Trace 记录事实
Reducer 解释事实
Renderer 展示事实
CLI 不创造事实
```

禁止：

```text
CLI 根据字符串猜 Agent 状态
CLI 直接访问 ToolRuntime 私有字段
CLI 直接修改 Checkpoint
CLI 读取模型隐藏思维链并展示
CLI 通过 tail -f JSONL 获取实时状态
```

## 6.2 数据流

```text
AgentLoop / ToolRuntime / Approval / Subagent / MCP
    ↓
TraceEmitter.emit(...)
    ↓
创建唯一 TraceEvent
    ↓
CompositeTraceSink
    ├── JsonlTraceSink
    │       └── events.jsonl
    │
    └── RuntimeEventBus
            ↓
        TraceReducer
            ↓
        CliViewState
            ↓
        Rich / prompt_toolkit Renderer
```

恢复 Thread 时：

```text
读取 events.jsonl
    ↓
TraceReader
    ↓
按 sequence_number Replay
    ↓
TraceReducer
    ↓
重建 Usage、阶段和 Transcript
    ↓
切换到 Live EventBus
```

---

# 七、TraceEvent v2

当前 `events.jsonl` 已经有：

```text
event_id
event_type
timestamp
run_id
sequence_number
iteration
parent_event_id
agent_id
thread_id
turn_id
parent_agent_id
delegation_request_id
depth
payload
```

阶段 7 不推翻它，而是补充稳定语义。

建议：

```python
@dataclass(frozen=True, slots=True)
class TraceEvent:
    schema_version: int

    event_id: str
    event_type: str
    timestamp: str
    sequence_number: int

    thread_id: str
    turn_id: str | None
    agent_id: str | None

    iteration: int
    parent_event_id: str | None
    correlation_id: str | None
    logical_action_id: str | None

    payload: dict[str, Any]
```

其中：

```text
parent_event_id
    表示树形父子关系

correlation_id
    表示同一模型调用、Tool Call、Approval 或 Subagent 生命周期

logical_action_id
    与阶段 6 Durable Action 对齐
```

---

# 八、阶段状态模型

## 8.1 不显示模型隐藏思维

用户截图中的：

```text
我会先检查……
当前变更全部属于……
```

属于对用户可见的工作进度说明。

本项目不能把：

```text
reasoning_content
隐藏 Chain of Thought
Provider 原始内部推理
```

直接显示为 Trace。

阶段显示来源应优先是确定性的 Runtime Event。

## 8.2 RuntimePhase

新增：

```python
class RuntimePhase(StrEnum):
    READY = "ready"
    PREPARING = "preparing"
    BUILDING_CONTEXT = "building_context"
    CALLING_MODEL = "calling_model"
    PROCESSING_RESPONSE = "processing_response"

    WAITING_APPROVAL = "waiting_approval"
    RUNNING_TOOL = "running_tool"
    WAITING_SUBAGENT = "waiting_subagent"

    COMPACTING = "compacting"
    RECOVERING = "recovering"
    FINALIZING = "finalizing"

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

## 8.3 Event 到 Phase 的映射

```text
turn.started
    → PREPARING

context.build.started
    → BUILDING_CONTEXT

model.request.started
    → CALLING_MODEL

model.response.completed
    → PROCESSING_RESPONSE

approval.requested
    → WAITING_APPROVAL

tool.execution.started
    → RUNNING_TOOL

subagent.started / subagent.waiting
    → WAITING_SUBAGENT

context.compaction.started
    → COMPACTING

recovery.started
    → RECOVERING

turn.finalizing
    → FINALIZING

turn.completed
    → COMPLETED

turn.failed
    → FAILED

turn.cancelled
    → CANCELLED
```

界面显示文本由本地映射产生：

```text
BUILDING_CONTEXT
    正在构建上下文

CALLING_MODEL
    正在调用 deepseek-v4-pro

RUNNING_TOOL
    正在执行 apply_patch

WAITING_APPROVAL
    等待审批：write_file

RECOVERING
    正在恢复未完成 Turn
```

不让模型自由生成阶段名称。

---

# 九、需要规范化的 Trace 事件

## 9.1 Turn

```text
turn.started
turn.finalizing
turn.completed
turn.failed
turn.cancelled
turn.recovered
```

## 9.2 Context

```text
context.build.started
context.build.completed
context.compaction.started
context.compaction.completed
context.compaction.failed
```

`context.build.completed` 建议包含：

```json
{
  "message_count": 24,
  "tool_schema_count": 13,
  "estimated_input_tokens": 10820,
  "estimate_method": "chars_ratio",
  "memory_count": 3,
  "active_skill_count": 1
}
```

这里的 Token 明确标记：

```text
estimated
```

## 9.3 Model

```text
model.request.started
model.response.completed
model.request.failed
model.response.reused
```

`model.response.completed` 必须包含 Usage：

```json
{
  "provider": "deepseek",
  "model": "deepseek-v4-pro",
  "response_id": "...",
  "finish_reason": "tool_calls",
  "duration_ms": 1832,
  "usage": {
    "input_tokens": 10820,
    "cached_input_tokens": 6420,
    "cache_miss_input_tokens": 4400,
    "output_tokens": 386,
    "reasoning_tokens": 120,
    "total_tokens": 11206
  }
}
```

## 9.4 Tool

```text
tool.requested
tool.execution.started
tool.output.delta
tool.execution.completed
tool.execution.failed
tool.execution.timed_out
tool.execution.outcome_unknown
```

第一版 Tool 不支持流式输出时，可以不产生 `tool.output.delta`。

但最终完成事件必须包含：

```json
{
  "tool_name": "run_command",
  "tool_call_id": "...",
  "logical_action_id": "...",
  "status": "success",
  "duration_ms": 812,
  "output_chars": 2150,
  "output_truncated": false,
  "artifact_id": null
}
```

## 9.5 Approval

```text
approval.requested
approval.decided
approval.reused
```

## 9.6 Subagent

```text
subagent.spawned
subagent.started
subagent.completed
subagent.failed
subagent.cancelled
```

## 9.7 Recovery

```text
recovery.started
recovery.resume_point.selected
recovery.completed
recovery.blocked
```

## 9.8 Memory 与 Checkpoint

```text
memory.retrieved
memory.injected
memory.written
checkpoint.saved
```

这些默认不在主 Transcript 中展开。

它们在：

```text
/trace full
```

中显示。

---

# 十、EventBus 与 Trace Sink

## 10.1 接口

```python
class TraceSink(Protocol):
    def write(self, event: TraceEvent) -> None:
        ...


class TraceEmitter:
    def emit(
        self,
        event_type: str,
        *,
        payload: dict[str, Any] | None = None,
        ...
    ) -> TraceEvent:
        ...
```

`TraceEmitter` 只创建一次 Event。

然后：

```python
class CompositeTraceSink:
    def __init__(self, sinks: list[TraceSink]) -> None:
        self.sinks = sinks

    def write(self, event: TraceEvent) -> None:
        for sink in self.sinks:
            sink.write(event)
```

## 10.2 持久 Sink

```text
JsonlTraceSink
```

继续负责：

- 顺序写入；
- Flush；
- Write Error；
- 恢复 sequence number。

## 10.3 Live Sink

```text
RuntimeEventBus
```

负责当前进程内订阅。

建议：

```python
class RuntimeEventBus:
    def subscribe(self, subscriber: TraceSubscriber) -> Subscription:
        ...

    def publish(self, event: TraceEvent) -> None:
        ...
```

第一版不需要网络，不需要 Redis，不需要消息队列。

## 10.4 慢 UI 不得阻塞 Runtime

Live EventBus 使用有界队列。

规则：

- Terminal Event、Approval、Error 不得丢弃；
- 高频 Delta 可以合并；
- UI 队列满时优先丢弃重复 Spinner Tick；
- JsonlTraceSink 写入失败仍按原 Fail-closed 配置处理；
- UI Renderer 失败不能让 Tool 副作用重复执行；
- UI 崩溃后 Runtime 可以转为 Plain Output 或停止交互层。

---

# 十一、Usage 数据模型

## 11.1 扩展 `Usage`

当前字段：

```python
input_tokens
output_tokens
total_tokens
cached_input_tokens
provider_details
```

建议扩展：

```python
@dataclass(slots=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    cached_input_tokens: int | None = None
    cache_miss_input_tokens: int | None = None
    reasoning_tokens: int | None = None

    provider_details: dict[str, Any] = field(default_factory=dict)
```

语义：

```text
input_tokens
    Provider 的全部输入 Token

cached_input_tokens
    命中缓存的输入 Token

cache_miss_input_tokens
    未命中缓存的输入 Token

output_tokens
    全部输出 Token

reasoning_tokens
    Provider 报告的推理 Token
    通常是 output_tokens 的组成部分
    不得再次加入 total_tokens

total_tokens
    Provider 返回的总 Token
```

## 11.2 DeepSeek 映射

```text
prompt_tokens
    → input_tokens

completion_tokens
    → output_tokens

total_tokens
    → total_tokens

prompt_cache_hit_tokens
    → cached_input_tokens

prompt_cache_miss_tokens
    → cache_miss_input_tokens

completion_tokens_details.reasoning_tokens
    → reasoning_tokens
```

必须保留原始：

```text
provider_details
```

便于 Provider 新增字段后追查。

---

# 十二、ModelUsageRecord

每次模型调用都形成一条记录。

```python
@dataclass(frozen=True, slots=True)
class ModelUsageRecord:
    usage_record_id: str

    thread_id: str
    turn_id: str
    iteration: int

    provider: str
    model: str
    request_id: str | None
    response_id: str | None

    started_at: str
    completed_at: str
    duration_ms: int

    usage: Usage

    context_window_tokens: int | None
    pricing_snapshot_id: str | None
    estimated_cost: Decimal | None
    currency: str | None
```

第一版不一定单独建数据库表。

它可以作为：

```text
model.response.completed.payload
```

持久化在 `events.jsonl` 中。

`UsageReducer` 从 Trace Replay 中恢复。

---

# 十三、Token 统计的三层语义

## 13.1 当前模型调用

```text
Last Model Call
```

显示最近一次 Provider Response 的 Usage。

## 13.2 当前 Turn

```text
Turn Usage
=
当前 Turn 中所有 Model Call Usage 之和
```

## 13.3 当前 Thread

```text
Thread Usage
=
该 Thread 所有 Turn 中所有 Model Call Usage 之和
```

## 13.4 Current Context

当前上下文占用不能使用 Thread 累计 Token。

建议：

```text
最近一次模型调用的 input_tokens
    作为 Current Context 的精确近似
```

显示：

```text
Context: 18,420 / 64,000
```

说明：

- 它表示最近一次请求发送给模型的输入规模；
- 不包含未来尚未发送的新输入；
- 在第一个模型响应前，只能显示 ContextBuilder 的估算；
- 估算值必须标记 `est`；
- Provider 返回后替换为 exact。

## 13.5 Context Remaining

```text
remaining_tokens
=
context_window_tokens - latest_input_tokens
```

```text
remaining_percent
=
remaining_tokens / context_window_tokens
```

不得用：

```text
Thread Total Tokens
```

计算 Context Remaining。

---

# 十四、成本估算

## 14.1 必须显示为估算

终端中统一显示：

```text
¥0.042 est
```

不显示：

```text
实际扣费 ¥0.042
```

原因：

- Provider 价格可能变化；
- 不同账号可能存在优惠；
- 缓存价格不同；
- Provider Usage 字段可能缺失；
- 网络重试可能产生额外计费；
- 本地无法确认最终账单。

## 14.2 价格快照

不得把价格散落硬编码在 Renderer 中。

新增：

```python
@dataclass(frozen=True, slots=True)
class PricingSnapshot:
    snapshot_id: str
    provider: str
    model: str

    currency: str
    unit_tokens: int

    cache_hit_input_per_unit: Decimal | None
    cache_miss_input_per_unit: Decimal | None
    input_per_unit: Decimal | None
    output_per_unit: Decimal | None

    effective_from: str
    source_url: str
```

示例配置只展示结构，不在设计文档中写死价格：

```toml
[pricing.deepseek.deepseek-v4-pro]
snapshot_id = "deepseek-v4-pro-YYYY-MM-DD"
currency = "CNY"
unit_tokens = 1000000

cache_hit_input_per_unit = "..."
cache_miss_input_per_unit = "..."
output_per_unit = "..."

effective_from = "YYYY-MM-DD"
source_url = "DeepSeek 官方定价页面"
```

实现时由 Codex重新访问 Provider 官方定价页面，记录当时价格与日期。

## 14.3 计算公式

Provider 返回缓存拆分时：

```text
cost =
cached_input_tokens
    × cache_hit_input_price / unit
+
cache_miss_input_tokens
    × cache_miss_input_price / unit
+
output_tokens
    × output_price / unit
```

只有总输入量时：

```text
cost =
input_tokens
    × generic_input_price / unit
+
output_tokens
    × output_price / unit
```

`reasoning_tokens` 若已经包含在 `output_tokens` 中：

```text
只展示
不重复计费
```

## 14.4 缺失价格

缺少 Price Snapshot 时：

```text
Cost: n/a
```

不得：

- 猜价格；
- 使用别的模型价格；
- 将价格默认为 0；
- 在线状态命令中临时抓取网页。

## 14.5 历史稳定性

每条 ModelUsageRecord 保存：

```text
pricing_snapshot_id
```

以后价格变化时，历史成本仍按原 Snapshot 计算。

---

# 十五、UsageReducer

```python
@dataclass(slots=True)
class UsageSnapshot:
    last_call: Usage
    current_turn: Usage
    current_thread: Usage

    current_context_tokens: int | None
    context_window_tokens: int | None
    context_estimated: bool

    turn_estimated_cost: Decimal | None
    thread_estimated_cost: Decimal | None
    currency: str | None
```

Reducer：

```python
class UsageReducer:
    def apply(self, event: TraceEvent) -> None:
        ...

    def snapshot(self) -> UsageSnapshot:
        ...
```

只消费：

```text
model.response.completed
model.response.reused
```

其中 `model.response.reused`：

- 不增加新的 Token；
- 不增加新的成本；
- 只记录恢复行为。

这样恢复 `AFTER_MODEL` 时不会重复计费。

---

# 十六、`/status` 设计

## 16.1 显示目标

`/status` 是一个静态快照，不是 Trace。

建议输出：

```text
Agent Harness Status

  Thread       thread_01J...
  Turn         turn_0004 · running
  Phase        Running tool: apply_patch

  Provider     deepseek
  Model        deepseek-v4-pro
  Workspace    D:\APP_self\General-Agent-Design
  Git branch   main

  Sandbox      workspace-write
  Approval     on-request

Usage

  Context      18.4k / 64.0k · 71% left
  Last call    18.4k input + 412 output
  This turn    26.8k total
  Thread       74.2k total

  Cache        11.2k hit + 7.2k miss
  Reasoning    120 · included in output
  Cost         ¥0.083 est · this turn
               ¥0.221 est · thread

Activity

  Model calls  3
  Tool calls   7
  Elapsed      21.8s
  Checkpoint   after_tool
  Pending      none

Files

  Trace        .harness/threads/<id>/events.jsonl
  Rollout      .harness/threads/<id>/rollout.jsonl
```

## 16.2 未知值

缺少值时：

```text
Context      unavailable
Cost         n/a
Reasoning    not reported
```

不要展示 `None`。

## 16.3 当前项目现有 `/status`

当前实现只显示：

```text
Thread
Turns
Rollout
```

阶段 7 将其替换为独立：

```python
StatusPresenter
```

CLI 不直接拼接 RunState 字段。

---

# 十七、`/usage` 设计

`/status` 提供摘要。

`/usage` 提供详细账目：

```text
Token Usage

Current turn: turn_0004

  Call  Model                 Input   Hit    Miss   Output  Reason  Total   Cost
  1     deepseek-v4-pro       8.2k    4.1k   4.1k    311      80    8.5k   ¥...
  2     deepseek-v4-pro       9.4k    7.2k   2.2k    412     120    9.8k   ¥...
  3     deepseek-v4-pro       9.2k    8.0k   1.2k    203      40    9.4k   ¥...

Turn total
  Input: 26.8k
  Output: 926
  Total: 27.7k
  Estimated cost: ¥...

Thread total
  Input: 72.1k
  Output: 2.1k
  Total: 74.2k
  Estimated cost: ¥...
```

窄终端时不使用宽表格，改成逐调用块。

命令：

```text
/usage
/usage turn
/usage thread
/usage raw
```

`/usage raw` 可以展示 Provider Details，但必须过滤 Secret。

---

# 十八、Status Line

## 18.1 默认字段

```text
phase
model
context-remaining
turn-tokens
thread-tokens
estimated-cost
permissions
```

示例：

```text
Running tool · deepseek-v4-pro · ctx 71% · turn 27.7k · thread 74.2k · ¥0.08 est · workspace
```

## 18.2 窄终端优先级

当宽度不足，按顺序保留：

```text
Phase
Context Remaining
Turn Tokens
Model
Estimated Cost
Permissions
Thread Tokens
Thread ID
```

最后退化为：

```text
Running tool · ctx 71% · 27.7k
```

## 18.3 配置

```toml
[tui]
enabled = true
plain_output = false

show_progress = true
show_tool_output_lines = 8
show_model_calls = true
show_checkpoints = false
show_memory_events = false

status_line = [
  "phase",
  "model",
  "context-remaining",
  "turn-tokens",
  "thread-tokens",
  "estimated-cost",
  "permissions"
]

color = "auto"
unicode = true
raw_reasoning = false
```

`raw_reasoning` 第一版固定为 `false`，不开放启用。

## 18.4 `/statusline`

第一版只需要：

```text
/statusline
/statusline reset
/statusline set phase,model,context-remaining,turn-tokens
```

Codex 那种完整交互式多选和排序 Picker 可以后续补。

---

# 十九、Trace Transcript

## 19.1 主视图不是日志倾倒

默认 Transcript 只展示用户真正关心的事件：

```text
模型调用
工具调用
审批
子 Agent
错误
最终结果
少量安全进度
```

默认隐藏：

```text
每个 Checkpoint
Memory 检索内部细节
完整 Tool Schema
完整 Context
所有 Provider Metadata
重复 Retry 细节
```

## 19.2 Cell 类型

```python
class TraceCell(Protocol):
    def render(self, width: int, mode: RenderMode) -> list[str]:
        ...
```

建议：

```text
ProgressCell
ModelCallCell
ToolCallCell
ApprovalCell
SubagentCell
RecoveryCell
CompactionCell
ErrorCell
TurnSummaryCell
StatusCell
```

## 19.3 Active Cell

当前活动事件只保留一个 Active Cell，例如：

```text
⠋ 正在执行测试 · 4.2s
```

完成后提交为：

```text
• Ran uv run --no-sync python -m pytest -q
  └ 134 passed, 1 skipped in 8.21s
```

随后创建下一个 Active Cell。

## 19.4 Tool 展示

### Command

```text
• Ran git status --short
  └ M src/agent_harness/runtime/session.py
    M tests/unit/test_turn_state_reset.py
```

### Read

```text
• Read src/agent_harness/runtime/session.py
  └ 262 lines
```

默认不把整个文件内容打印到 Transcript。

### Search

```text
• Searched "_reset_state_for_new_turn"
  └ 4 matches in 3 files
```

### Patch

```text
• Updated src/agent_harness/runtime/session.py
  └ +18 -7
```

### MCP

```text
• Called MCP github.search
  └ 12 results
```

### Subagent

```text
• Started reviewer subagent
  └ agent_01J...

• reviewer completed
  └ 3 findings · 1 high priority
```

## 19.5 长输出折叠

配置：

```text
show_tool_output_lines = 8
```

超出后：

```text
  └ ... +101 lines hidden (/trace full 查看)
```

完整内容优先写 Artifact。

不得仅存在 UI 内存中。

## 19.6 错误展示

```text
× run_command failed · exit 1 · 2.1s
  └ AssertionError: expected 3, got 2
```

Recovery Required：

```text
! Tool result unknown after process interruption
  └ run_command cannot be replayed automatically
  └ Run `/recovery` to inspect the pending action
```

---

# 二十、Trace 命令

```text
/trace
```

显示当前 Turn 默认摘要。

```text
/trace full
```

显示当前 Turn 全部可展示事件，包括 Checkpoint、Memory、Retry。

```text
/trace raw
```

格式化展示原始 TraceEvent，不直接打印 Secret 字段。

```text
/trace last 20
```

显示最后 20 个 Cell。

```text
/trace turn turn_0003
```

查看指定 Turn。

```text
/trace export path.txt
```

导出 Copy-friendly Plain Transcript。

第一版不需要图形化 Trace Graph。

---

# 二十一、CLI 技术架构

## 21.1 不使用 Web UI

继续保留：

```bash
agent-harness code
agent-harness exec
agent-harness resume
```

`exec` 仍是非交互模式。

`code` 无 Task 时进入增强交互 CLI。

## 21.2 prompt_toolkit

使用：

```python
PromptSession.prompt_async()
```

能力：

- 异步输入；
- Slash Command Completion；
- FileHistory；
- Bottom Toolbar；
- Ctrl+C；
- Ctrl+T；
- 光标与多行输入；
- Windows Terminal 支持。

建议：

```text
Ctrl+C
    当前 Turn 运行时请求 Cancel
    空闲时清空当前输入
    连续第二次再退出可后续实现

Ctrl+T
    展开或关闭当前 Turn Trace

Ctrl+L
    清屏但不删除 Thread History
```

## 21.3 Rich

使用 Rich 展示：

- Tool Cell；
- Status Card；
- Usage；
- Error；
- Tree；
- Spinner；
- Syntax；
- Diff Summary。

但不使用一个长期 `Live` 区域不断重绘全部历史。

只对：

```text
Active Cell
Bottom Status
```

进行动态刷新。

## 21.4 Plain Mode

以下情况自动进入 Plain Mode：

- stdout 不是 TTY；
- `--plain`；
- CI；
- 不支持 Unicode；
- Rich 初始化失败。

Plain Mode 输出必须：

- 顺序稳定；
- 无 ANSI；
- 可重定向；
- 可复制；
- 适合 Snapshot Test。

---

# 二十二、目录设计

新增：

```text
src/agent_harness/
├── ui/
│   ├── app.py
│   ├── commands.py
│   ├── event_bus.py
│   ├── events.py
│   ├── projector.py
│   ├── state.py
│   ├── transcript.py
│   ├── status.py
│   ├── plain.py
│   └── cells/
│       ├── base.py
│       ├── progress.py
│       ├── model.py
│       ├── tool.py
│       ├── approval.py
│       ├── subagent.py
│       ├── recovery.py
│       ├── error.py
│       └── summary.py
│
├── usage/
│   ├── models.py
│   ├── reducer.py
│   ├── pricing.py
│   └── formatter.py
│
└── tracing/
    ├── emitter.py
    ├── sinks.py
    ├── reader.py
    └── reducer.py
```

修改：

```text
cli.py
config.py
domain/model.py
runtime/session.py
runtime/run_manager.py
runtime/agent_loop.py
tools/runtime.py
security/approval.py
runtime/subagents/scheduler.py
providers/deepseek.py
tracing/jsonl.py
tracing/summary.py
checkpoints/serializer.py
pyproject.toml
```

---

# 二十三、核心类职责

## `CliApp`

负责：

- Prompt Session；
- Slash Command；
- 输入循环；
- Renderer 生命周期；
- Cancel；
- Thread 切换。

不直接执行 Tool。

## `RuntimeEventBus`

负责：

- Runtime 到 UI 的本地事件；
- Subscriber 生命周期；
- 有界队列；
- 关闭。

## `TraceReader`

负责：

- 读取历史 `events.jsonl`；
- sequence 校验；
- Turn Filter；
- Replay。

## `TraceReducer`

负责：

- TraceEvent → TraceCell；
- Active Cell；
- Completed Cell；
- Phase；
- Error。

## `UsageReducer`

负责：

- Last Call；
- Turn Usage；
- Thread Usage；
- Context；
- Cost。

## `StatusPresenter`

负责：

- `/status`；
- Bottom Toolbar；
- 宽度降级；
- 未知值格式化。

## `PricingRegistry`

负责：

- 加载 Price Snapshot；
- 按 Provider / Model / Date 查找；
- 估算成本；
- 返回 n/a。

---

# 二十四、当前 Trace 需要补充的数据

## 24.1 Model Duration

当前 `model.requested` 与 `model.completed` 可以通过时间差推算，但阶段 7 应直接记录：

```text
duration_ms
```

避免 Reducer 在 Retry 和并发场景中配错事件。

## 24.2 Usage

当前 `model.completed` 尚未写入 Usage。

必须补充完整 Normalize Usage。

## 24.3 Tool Display Summary

Tool 完成事件应包含：

```text
display_name
summary
duration_ms
status
output_lines
output_chars
artifact_id
```

`summary` 由 Tool Adapter 本地生成，不让 Renderer 解析任意 ToolResult 文本。

## 24.4 Approval Preview

Approval Requested 事件应包含经过清理的：

```text
tool_name
argument_preview
risk
reason
scope_options
```

不得包含 Secret。

## 24.5 Subagent Summary

Child 完成事件应包含：

```text
agent_name
duration_ms
model_calls
tool_calls
status
summary
```

---

# 二十五、`result.json` 的调整

保留现有字段。

新增：

```json
{
  "thread_id": "...",
  "turn_id": "...",

  "usage": {
    "input_tokens": 0,
    "cached_input_tokens": 0,
    "cache_miss_input_tokens": 0,
    "output_tokens": 0,
    "reasoning_tokens": 0,
    "total_tokens": 0
  },

  "estimated_cost": {
    "amount": "0.000000",
    "currency": "CNY",
    "pricing_snapshot_id": "...",
    "estimated": true
  },

  "context": {
    "last_input_tokens": 0,
    "window_tokens": 64000,
    "remaining_percent": 0,
    "estimated": false
  }
}
```

注意：

```text
result.json
    当前 Turn Summary

events.jsonl
    可重建 Thread Lifetime Summary
```

不要把 Thread 累计数据覆盖进每个 Turn 的当前结果。

---

# 二十六、Slash Command

阶段 7 核心：

```text
/status
/usage
/trace
/trace full
/trace raw
/statusline
```

保留现有：

```text
/permissions
/sandbox
/approvals
/memories
/compact
/new
/exit
```

建议补充：

```text
/help
```

自动补全应展示：

```text
命令
一句说明
```

---

# 二十七、实现批次

## 批次 1：阶段 6.2 Turn Reset

完成：

- `_reset_state_for_new_turn()`；
- Usage 变成 Turn-local；
- 时间、取消、Summary 重置；
- 多 Turn 专项测试；
- 原阶段 6.1 Recovery 测试全量回归。

## 批次 2：Usage Accounting

完成：

- Usage 新字段；
- DeepSeek Usage 映射；
- ModelUsageRecord；
- PricingSnapshot；
- UsageReducer；
- `result.json` 增强；
- `/usage` Plain 输出。

## 批次 3：Typed Trace Pipeline

完成：

- TraceEvent；
- TraceEmitter；
- Composite Sink；
- RuntimeEventBus；
- TraceReader；
- TraceReducer；
- 历史 Replay；
- 不改变 Durable Rollout 语义。

## 批次 4：CLI Shell

完成：

- prompt_toolkit；
- Rich；
- PromptSession；
- Slash Command Completion；
- Bottom Toolbar；
- Plain Mode；
- 现有交互命令迁移。

## 批次 5：Trace Cells

完成：

- Progress；
- Model；
- Tool；
- Approval；
- Subagent；
- Error；
- Recovery；
- 长输出折叠；
- `/trace`。

## 批次 6：Status 与加固

完成：

- `/status`；
- `/statusline` 基础配置；
- Context Remaining；
- Cost；
- 宽度降级；
- Snapshot Tests；
- Windows Terminal 回归；
- 文档和验收记录。

---

# 二十八、测试设计

## 28.1 阶段 6.2

- 多 Turn started_at 重置；
- completed_at 重置；
- Usage 重置；
- Cancellation 重置；
- Agent Summary 重置；
- 历史 Message 保留；
- Duration 正确。

## 28.2 Provider Usage

- DeepSeek prompt Token；
- Cache Hit；
- Cache Miss；
- Output；
- Reasoning；
- Total；
- 字段缺失；
- 原始 provider_details 保留。

## 28.3 成本

- Cache Hit / Miss 分别计价；
- Generic Input 回退；
- Reasoning 不重复计费；
- Price Snapshot 不存在返回 n/a；
- 历史 Snapshot 价格不随新配置变化；
- Decimal 计算，不使用 Float 金额。

## 28.4 Reducer

- 单模型调用；
- 同 Turn 多调用；
- 多 Turn Thread；
- AFTER_MODEL Reuse 不增加 Token；
- Retry 只计算实际完成响应；
- Trace Replay 与 Live 结果一致；
- sequence 顺序错误 fail closed。

## 28.5 Trace

- Model Cell；
- Tool Cell；
- Approval Cell；
- Subagent Cell；
- Error Cell；
- Recovery Cell；
- 长输出折叠；
- Artifact 引用；
- Checkpoint 默认隐藏；
- `/trace full` 显示；
- Secret 清理。

## 28.6 CLI

- Slash Command Completion；
- `/status` Snapshot；
- `/usage` Snapshot；
- `/trace` Snapshot；
- 窄终端；
- 宽终端；
- Unicode；
- ASCII 回退；
- stdout 重定向；
- Windows CRLF；
- Ctrl+C Cancel；
- Resume 后 Transcript Replay。

## 28.7 原有门禁

```bash
uv sync --locked --extra test

uv run --no-sync python -m ruff check src tests
uv run --no-sync python -m mypy src
uv run --no-sync python -m pytest -m "unit or integration_local" -q
uv run --no-sync python -m pytest -m recovery_process -q
git diff --check
```

---

# 二十九、验收标准

## 阶段 6.2

- [ ] 新 Turn 有独立 started_at；
- [ ] 新 Turn Usage 从零开始；
- [ ] 新 Turn 不继承 Cancellation；
- [ ] 新 Turn 不继承 Agent Summary；
- [ ] Thread History 不丢失；
- [ ] 多 Turn Duration 正确；
- [ ] 阶段 6.1 Recovery 无回归。

## Usage

- [ ] `/status` 显示 Last Call、Turn、Thread 三层 Token；
- [ ] Current Context 与 Thread Total 分离；
- [ ] Token 优先使用 Provider Usage；
- [ ] Context 估算明确标记 est；
- [ ] Cache Hit/Miss 可展示；
- [ ] Reasoning Token 不重复计费；
- [ ] Cost 明确标记 est；
- [ ] 缺少价格时显示 n/a；
- [ ] Price Snapshot 可追溯。

## Trace

- [ ] 模型、工具、审批、子 Agent、错误可在 CLI 中看到；
- [ ] 默认视图不倾倒所有内部日志；
- [ ] 长输出可折叠；
- [ ] `/trace full` 可查看详细事件；
- [ ] 历史 Trace Replay 与 Live 展示一致；
- [ ] Resume 后不重复生成已提交 Cell；
- [ ] 不显示原始隐藏思维链；
- [ ] Secret 不进入 Transcript。

## CLI

- [ ] 不依赖 Web Server；
- [ ] 不打开浏览器；
- [ ] `agent-harness exec` 保持可脚本化；
- [ ] `agent-harness code` 提供增强交互；
- [ ] TTY 使用 Rich/prompt_toolkit；
- [ ] 非 TTY 自动 Plain Mode；
- [ ] `/status`、`/usage`、`/trace` 可用；
- [ ] Bottom Status 能根据宽度降级；
- [ ] Windows Terminal 可运行。

---

# 三十、继续延期的内容

本阶段不因 CLI 完成而宣称以下能力已完成：

- Subagent 跨进程 Durable Recovery；
- 同一 Thread 的多进程 Lease；
- 全面 Transactional Outbox；
- 文件 Tool 完整 pre/post hash reconciliation；
- Memory 自动提取；
- Benchmark 成功率平台；
- Web UI；
- 图形 Trace；
- Provider 最终账单核对；
- Token-by-token Streaming；
- 完整 Codex TUI 功能对齐；
- 企业级遥测平台。

---

# 三十一、给 Codex 的执行要求

1. 基于提交 `bd64aedd2d6999368de35c597543f76d664d76d9` 开始；
2. 先实现阶段 6.2，不得直接跳到 TUI；
3. 先运行并保存现有 Ruff、Mypy、Pytest 基线；
4. 不把 `RunState.usage_total` 同时定义为 Turn 和 Thread Usage；
5. 不使用字符估算冒充 Provider Token；
6. 不在 Renderer 内计算业务状态；
7. 不让 UI 直接访问 Provider 或 ToolRuntime；
8. 不通过实时 tail JSONL 代替 EventBus；
9. 不公开 `reasoning_content`；
10. 不把价格写死在 Status Renderer；
11. 缺少价格必须显示 n/a；
12. 历史成本必须绑定 Pricing Snapshot；
13. 不为了 UI 修改 Permission、Approval 或 Recovery 语义；
14. `exec` 模式必须继续支持无 ANSI 输出；
15. TraceEvent 必须可序列化和版本化；
16. Replay 与 Live 必须使用同一 Reducer；
17. 所有终端布局必须有 Snapshot Test；
18. 超长 Tool Output 必须引用 Artifact；
19. 完成后新增：
    `doc/Harness_Agent_阶段6.2与阶段7_CLI实现差异及验收记录.md`；
20. 只有完整门禁通过后才能声明阶段 7 完成。

---

# 三十二、参考资料

## OpenAI Codex CLI：Slash Commands、Status 与 Status Line

https://developers.openai.com/codex/cli/slash-commands

https://developers.openai.com/codex/cli/features

## OpenAI Codex 源码：Status

https://github.com/openai/codex/blob/main/codex-rs/tui/src/status/mod.rs

https://github.com/openai/codex/blob/main/codex-rs/tui/src/status/card.rs

https://github.com/openai/codex/blob/main/codex-rs/tui/src/bottom_pane/status_line_setup.rs

## OpenAI Codex 源码：事件与 Transcript

https://github.com/openai/codex/blob/main/codex-rs/tui/src/app_event.rs

https://github.com/openai/codex/blob/main/codex-rs/tui/src/chatwidget/protocol.rs

https://github.com/openai/codex/blob/main/codex-rs/tui/src/history_cell/mod.rs

## DeepSeek API

https://api-docs.deepseek.com/api/create-chat-completion

https://api-docs.deepseek.com/guides/kv_cache

## prompt_toolkit

https://python-prompt-toolkit.readthedocs.io/en/master/pages/asking_for_input.html

https://python-prompt-toolkit.readthedocs.io/en/master/pages/advanced_topics/asyncio.html

## Rich

https://rich.readthedocs.io/en/stable/live.html

https://rich.readthedocs.io/en/stable/console.html

## 当前项目

https://github.com/dazzlingwuming/General-Agent-Design

基线提交：

```text
bd64aedd2d6999368de35c597543f76d664d76d9
Harden stage 6 recovery semantics
```

---

# 三十三、最终结论

阶段 7 不再围绕：

```text
成功率
Benchmark
Web 页面
复杂评测平台
```

而是围绕：

```text
我现在能否看懂 Agent 正在做什么
我能否知道它调用了哪些模型和工具
我能否看到审批、恢复和子 Agent 状态
我能否知道 Token 消耗和估算成本
我能否在终端中重放并检查完整 Trace
```

正确实施顺序为：

```text
阶段 6.2 Turn 状态修复
    ↓
Usage 与价格快照
    ↓
Typed TraceEvent 与 EventBus
    ↓
Trace Reducer
    ↓
Codex 风格 CLI Transcript
    ↓
/status、/usage、/trace
    ↓
Status Line 与终端加固
```

最终产品形态仍然是一个本地 Coding Agent CLI。

它不需要先成为一个 Web 平台，但必须像成熟 Coding Agent 一样，让执行过程、资源消耗和安全边界对用户可见。
