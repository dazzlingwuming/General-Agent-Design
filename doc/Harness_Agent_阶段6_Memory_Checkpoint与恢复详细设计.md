# General Agent Harness：阶段 6 Memory、Checkpoint 与恢复详细设计

> 文档版本：v1.0  
> 文档日期：2026-07-12  
> 目标仓库：`https://github.com/dazzlingwuming/General-Agent-Design`  
> 设计基线提交：`d8e019f3a7cacd4882f25c28355acc92070172b5`  
> 当前前置状态：阶段 1～5 已完成并记录 CI 验收；阶段 6 尚未实现  
> 文档用途：直接交给 Codex，指导阶段 6 的设计复核、实现、测试、故障注入和验收  
> 核心原则：先建立可恢复执行，再建立长期记忆；Checkpoint 与 Memory 必须分离；任何副作用恢复都不得依赖模型猜测  

---

# 一、阶段 6 的目标

阶段 6 不是单纯增加一个“向量数据库”，也不是把历史对话重新塞回模型。

阶段 6 必须完成两套相互配合、但职责不同的系统：

```text
Durable Runtime
├── Checkpoint
├── Pending Approval 持久化
├── 进程重启恢复
├── Tool Action Journal
├── 幂等与副作用核对
├── Root / Child 执行恢复
└── Context Compaction 的恢复索引

Memory System
├── Thread 短期记忆
├── Turn / Task 工作记忆
├── Project 长期记忆
├── Agent 作用域记忆
├── 来源、置信度和验证状态
├── 检索、注入、失效和删除
└── Memory 污染与越权防护
```

阶段 6 完成后，系统应达到以下产品语义：

```text
用户创建或恢复 Thread
    ↓
Turn 执行到任意安全边界
    ↓
进程退出、崩溃或机器重启
    ↓
重新启动 Harness
    ↓
系统读取 Durable Checkpoint
    ↓
重新初始化 Provider、Tool、Guidance、Skill、MCP 等瞬态对象
    ↓
恢复原 Turn 的逻辑执行位置
    ↓
已提交结果不重复执行
结果未知的副作用不自动重放
待审批调用重新展示原审批
    ↓
继续原 Turn 或进入明确的人工核对状态
```

长期记忆应达到：

```text
历史 Thread 中经过允许且具有来源的有用信息
    ↓
形成 Memory Candidate
    ↓
经过范围、秘密、重复、冲突和可信度检查
    ↓
写入独立 Memory Store
    ↓
后续 Thread 按项目、Agent、可信度和上下文预算检索
    ↓
作为非权威辅助上下文注入模型
```

---

# 二、明确非目标

阶段 6 第一版不实现：

- 不引入 LangGraph、OpenAI Agents SDK Runner、Temporal、CrewAI 或 AutoGen 替代现有 Runtime；
- 不序列化 Python coroutine、`asyncio.Task`、Lock、Queue、文件句柄、HTTP Client 或 MCP ClientSession；
- 不恢复旧的 MCP Streamable HTTP Session ID；
- 不自动重放结果未知的外部副作用；
- 不把 Memory 当成 Permission、Approval、Guidance 或 Sandbox；
- 不允许模型自行声明某个 Tool“幂等”后获得自动重试资格；
- 不自动把所有对话写成长期记忆；
- 不在第一版引入独立向量数据库、知识图谱或分布式数据库；
- 不修改 Native Windows 沙箱延期边界；
- 不删除 `rollout.jsonl` 中的历史；
- 不声称提供 exactly-once 外部副作用语义；
- 不实现多机分布式调度；
- 不实现后台常驻 Memory Worker 服务；
- 不将阶段 7 的 Web UI、完整评测面板提前并入阶段 6。

阶段 6 的目标是：

```text
本地单机、单用户、可审计、可核对、故障安全的 Durable Agent Runtime
```

---

# 三、成熟系统调研结论与本项目借鉴

## 3.1 OpenAI Codex：Thread、Turn、Item 与恢复

Codex App Server 使用持续存在的 Thread，支持 `thread/start`、`thread/resume` 和 `thread/fork`。恢复 Thread 与启动新 Turn 是两个动作；仅恢复 Thread 不应虚假产生新的 Turn。Codex 将 Turn 内的工作表达为 Item，并将 `item/*` 通知作为 Turn Item 的事实来源。

Codex 的审批流程也绑定稳定的：

```text
threadId
turnId
itemId
```

审批请求解决后，原 Item 最终进入 completed、failed 或 declined 等明确状态。

本项目借鉴：

- 保留现有 `Thread -> Turn -> RolloutItem` 模型；
- Checkpoint 绑定稳定 `thread_id`、`turn_id`、`item_id` 和 `logical_action_id`；
- 恢复不创建新 Turn；
- Pending Approval 必须恢复原 Tool Call，而不是再次调用模型生成 Tool Call；
- Item 完成状态必须是恢复判定的重要依据；
- Thread Metadata 只能是可重建索引，不能覆盖 canonical history。

## 3.2 Codex Memory：辅助召回而不是权威规则

Codex 官方将 Memory 与 `AGENTS.md` 明确区分：

```text
AGENTS.md / checked-in documentation
    必须遵守的稳定项目指导

Memory
    从过去工作中提取的辅助召回层
```

Codex 本地 Memory 使用独立存储，包含 summary、durable entries、recent inputs 和 supporting evidence；Memory 生成会跳过活动或过短任务、清理秘密，并等待任务进入空闲状态后处理。

本项目借鉴：

- Memory 不能覆盖 Guidance；
- Memory 必须保留 supporting evidence；
- 活动 Turn、失败 Turn、取消 Turn 默认不自动生成长期 Memory；
- 自动提取只在 Thread/Turn 空闲安全边界运行；
- Memory 写入前必须执行 Secret Redaction；
- 每个 Thread/Turn 必须允许用户控制“是否使用 Memory”和“是否贡献 Memory”。

## 3.3 OpenAI Agents SDK：可序列化 HITL RunState

OpenAI Agents SDK 的 HITL 流程会：

```text
Tool 声明需要审批
    ↓
Run 暂停并返回 interruption
    ↓
RunState 序列化
    ↓
用户批准或拒绝
    ↓
从原 RunState 恢复
```

其 `RunState` 支持 JSON 序列化，并明确用于将长时间 Pending Approval 保存到数据库或队列后恢复。

本项目借鉴：

- Approval 不是 Console 输入函数中的临时等待；
- Pending Approval 是 Durable Runtime State；
- Approval Decision 必须写入持久状态后才能执行 Tool；
- 恢复时重建运行对象，但继续原逻辑状态；
- 持久化的是执行数据，不是 Python 调用栈。

## 3.4 OpenAI Agents SDK Session：完整历史与模型输入分离

OpenAI Agents SDK Session 会保存新 Turn 产生的用户消息、助手消息和工具 Item；在模型调用前，可以单独过滤、重排或裁剪历史，而不会把旧历史重新保存为新 Item。

本项目借鉴：

```text
Canonical Rollout
≠
Checkpoint
≠
Model-visible Context
≠
Long-term Memory
```

- `rollout.jsonl` 保存发生过什么；
- Checkpoint 保存当前应从哪里继续；
- ContextBuilder 决定本次模型能看到什么；
- MemoryStore 保存跨 Thread 的辅助信息；
- Context 裁剪与 Compaction 不能修改 canonical rollout。

## 3.5 LangGraph：Checkpointer 与 Store 分离

LangGraph 官方将持久化分成：

```text
Checkpointer
    单 Thread 状态快照
    Conversation Continuity
    HITL
    Fault Tolerance
    Time Travel

Store
    跨 Thread 的应用数据
    User Preferences
    Facts
    Shared Knowledge
```

本项目借鉴：

- `CheckpointStore` 与 `MemoryStore` 必须是两个接口；
- 不能用 Memory 检索代替执行恢复；
- 不能把 Pending Tool Call 写成普通 Memory；
- 不能把 Thread Message History 当成长期 Project Memory；
- Root 和 Child 应使用独立 Checkpoint Namespace。

## 3.6 Temporal：副作用拆分和幂等

Temporal 将外部副作用封装为小而明确的 Activity，建议 Activity 具备幂等性；大操作应拆成多个可核对的小步骤，以降低失败恢复和重复执行风险。

本项目借鉴：

- Tool 是 Durable Runtime 的副作用边界；
- Tool 必须声明 Effect Class 和 Recovery Policy；
- 已开始但没有结果的 Tool 不能一律重试；
- 文件写入应记录前置和后置状态；
- 外部写操作优先使用 Idempotency Key；
- 复杂副作用 Tool 应拆小，而不是在一个 Tool 内完成不可核对的长流程。

## 3.7 SQLite WAL

本项目是本地单机 CLI Harness，SQLite 适合作为第一版 Durable Store：

- Python 标准库内置；
- 支持事务；
- 支持 WAL；
- 支持索引、唯一约束和外键；
- 支持 FTS5 时可用于第一版 Memory 检索；
- 不需要提前引入数据库服务。

SQLite 只用于同一主机的本地状态，不将 WAL 数据库放在网络文件系统上。

---

# 四、当前仓库事实与阶段 6 前置缺口

当前主分支已经存在：

```text
Thread / Turn / Item
LocalThreadStore
LiveThread
RolloutRecorder
TurnController
ConversationSession
RunManager
AgentLoop
ToolRuntime
SubagentScheduler
Guidance Snapshot
Skill Catalog / Activation Snapshot
MCP Runtime / Catalog Snapshot
ArtifactStore
Permission / Approval / Sandbox
```

当前已经支持：

```text
.harness/threads/<thread_id>/
├── metadata.json
├── rollout.jsonl
├── events.jsonl
├── result.json
├── turns/
├── snapshots/
└── artifacts/
```

但当前 `resume` 主要执行：

```text
读取 metadata
读取 rollout
从 user_message / agent_message 重建消息
发现未完成 Turn
将其标记为 interrupted
恢复 Thread 到 IDLE
```

当前尚未具备：

- 从模型调用后恢复；
- 从 Tool Call 前恢复；
- 从 Pending Approval 恢复；
- 从 Tool Result 已产生但尚未进入 Context 的位置恢复；
- 对结果未知副作用进行核对；
- 恢复活动 Subagent；
- 持久化 Turn/Thread Approval Grant；
- 完整 Context Compaction；
- 长期 Memory Store。

当前 Rollout 和 Metadata 还存在双写窗口：

```text
metadata 已写 ACTIVE
但 turn.started 尚未 durable

或

metadata 已写 IDLE
但 turn.completed 尚未 durable
```

当前 JSONL 读取还会跳过损坏行。阶段 6 必须区分：

```text
末尾半行
    可视为进程崩溃造成的 truncated tail

中间损坏
    必须标记 CORRUPTED，禁止静默继续
```

---

# 五、阶段 6 总体架构

阶段 6 采用：

```text
Append-only Rollout
+
Durable Runtime Database
+
Checkpoint Store
+
Tool Action Journal
+
Memory Store
+
Artifact Store
```

推荐本地目录：

```text
.harness/
├── runtime.sqlite3
├── runtime.sqlite3-wal
├── runtime.sqlite3-shm
├── memory.sqlite3
└── threads/
    └── <thread_id>/
        ├── metadata.json
        ├── rollout.jsonl
        ├── events.jsonl
        ├── result.json
        ├── turns/
        ├── snapshots/
        ├── artifacts/
        └── agents/
            └── <child_thread_id>/
                ├── metadata.json
                ├── rollout.jsonl
                └── result.json
```

## 5.1 各存储职责

### `rollout.jsonl`

保存不可变的语义历史：

- 用户消息；
- 模型响应；
- Tool 生命周期；
- Approval 生命周期；
- Subagent 生命周期；
- Memory 读写事件；
- Compaction 事件；
- Recovery 事件；
- Turn Terminal Outcome。

它仍是用户检查和审计时的 canonical semantic history。

### `runtime.sqlite3`

保存尚未结束的执行状态：

- Checkpoint；
- Resume Point；
- Pending Approval；
- Prepared Tool Call；
- Tool Execution Journal；
- Durable Turn State；
- Child Execution State；
- Transactional Outbox；
- Migration Version。

它是未完成执行恢复时的事实来源。

### `memory.sqlite3`

保存长期 Memory：

- Memory Record；
- Source / Evidence；
- Dependency；
- Tag；
- Tombstone；
- FTS Index；
- Retrieval Audit。

### `metadata.json`

仅保存 Thread 列表和快速查询需要的派生字段：

- status；
- preview；
- turn_count；
- updated_at；
- active_turn_id；
- latest_checkpoint_id；
- recovery_status。

Metadata 损坏或不一致时必须能够从 `runtime.sqlite3 + rollout.jsonl` 重建。

### `artifacts/`

保存大型文本、二进制内容、完整 Tool Result、Compaction Summary 和 Memory Evidence。

## 5.2 Transactional Outbox

SQLite 状态更新和 JSONL 追加不能形成一个跨文件原子事务，因此使用 Transactional Outbox：

```text
SQLite Transaction
├── 更新 Checkpoint / Action / Approval
└── 插入 rollout_outbox
        ↓
COMMIT
        ↓
Outbox Projector
        ↓
按 sequence_number 追加 rollout.jsonl
        ↓
标记 outbox delivered
```

要求：

- 每个 Outbox Event 有唯一 `event_id`；
- 每个 Thread 的 `sequence_number` 单调递增；
- JSONL 追加前检查是否已经存在同一 `event_id`；
- 进程启动时先 drain 未投影 Outbox；
- Outbox 投影失败不能继续执行新的副作用；
- RolloutRecorder sticky failure 继续保留；
- Checkpoint/Approval/Tool Journal 事务不得依赖后台任务是否及时调度。

---

# 六、领域模型

## 6.1 ResumePoint

新增：

```python
class ResumePoint(StrEnum):
    BEFORE_MODEL = "before_model"
    MODEL_IN_FLIGHT = "model_in_flight"
    AFTER_MODEL = "after_model"

    WAITING_APPROVAL = "waiting_approval"

    BEFORE_TOOL = "before_tool"
    TOOL_IN_FLIGHT = "tool_in_flight"
    AFTER_TOOL = "after_tool"

    WAITING_SUBAGENT = "waiting_subagent"
    BEFORE_FINALIZE = "before_finalize"

    PAUSED = "paused"
    RECOVERY_REQUIRED = "recovery_required"
    TERMINAL = "terminal"
```

含义：

```text
BEFORE_MODEL
    下一步可以构建 Context 并请求模型。

MODEL_IN_FLIGHT
    模型请求已经发送，但响应未 durable。
    恢复后允许创建新 attempt。

AFTER_MODEL
    模型响应已经 durable。
    不得再次请求模型，应继续消费已保存 Tool Call 或 Final Text。

WAITING_APPROVAL
    原 Tool Call 已准备完成，正在等待用户决定。

BEFORE_TOOL
    审批和本地准备已完成，Tool 尚未开始。

TOOL_IN_FLIGHT
    Tool 已开始，但 Durable Result 不存在。
    必须按 Tool RecoveryPolicy 判断。

AFTER_TOOL
    Tool Result 已 durable，但可能尚未进入下一次模型 Context。

WAITING_SUBAGENT
    Root 正在等待一个或多个 Child。

BEFORE_FINALIZE
    最终输出已产生，但 Turn Terminal Item 尚未完整提交。

RECOVERY_REQUIRED
    系统无法安全自动继续，需要用户核对。

TERMINAL
    Turn 已有唯一 Terminal Outcome。
```

## 6.2 DurableTurnStatus

新增或扩展：

```python
class DurableTurnStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_SUBAGENT = "waiting_subagent"
    PAUSED = "paused"
    RECOVERING = "recovering"
    RECOVERY_REQUIRED = "recovery_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
```

注意：

- `interrupted` 表示用户或 Runtime 明确终止；
- `recovery_required` 表示不能判断副作用结果；
- 不能继续把所有进程崩溃都转换成 `turn.interrupted`。

## 6.3 CheckpointEnvelope

```python
@dataclass(frozen=True, slots=True)
class CheckpointEnvelope:
    schema_version: int
    checkpoint_id: str

    session_id: str
    thread_id: str
    turn_id: str
    agent_id: str
    parent_agent_id: str | None
    child_thread_id: str | None

    checkpoint_sequence: int
    rollout_sequence: int
    resume_point: ResumePoint
    turn_status: DurableTurnStatus

    runtime_version: str
    config_digest: str
    provider_name: str
    model_name: str

    agent_definition_hash: str
    tool_catalog_hash: str
    permission_policy_hash: str
    sandbox_policy_hash: str

    guidance_snapshot_id: str | None
    guidance_content_hash: str | None

    skill_catalog_snapshot_id: str | None
    active_skill_snapshot_ids: tuple[str, ...]

    mcp_catalog_snapshot_id: str | None
    mcp_catalog_hash: str | None

    serialized_state: dict[str, Any]
    pending_action_ids: tuple[str, ...]
    pending_approval_ids: tuple[str, ...]
    child_execution_ids: tuple[str, ...]

    created_at: str
    payload_hash: str
```

`serialized_state` 只保存纯数据：

```text
当前 messages / message references
当前 iteration
model_call_count
tool_call_count
usage
预算余额
Task / Goal
Working Set
Turn Mailbox 的 durable 部分
已加载 Tool Disclosure
Skill Turn-local 状态
已消费 External Context hash
最终输出或错误
```

禁止保存：

```text
Provider Client
MCP Connection
asyncio.Task
Lock
Queue
File Handle
ApprovalHandler 对象
Tool Executor 函数
Secret 明文
```

## 6.4 LogicalAction

```python
@dataclass(frozen=True, slots=True)
class LogicalAction:
    logical_action_id: str
    action_type: str
    thread_id: str
    turn_id: str
    agent_id: str

    attempt_number: int
    state: ActionState

    request_payload: dict[str, Any]
    request_hash: str

    result_payload: dict[str, Any] | None
    result_hash: str | None

    created_at: str
    started_at: str | None
    completed_at: str | None
```

一个逻辑 Tool Call 可以有多个 attempt，但只能形成一个逻辑结果。

## 6.5 ToolEffectClass

```python
class ToolEffectClass(StrEnum):
    PURE = "pure"
    READ_ONLY = "read_only"
    IDEMPOTENT_WRITE = "idempotent_write"
    RECONCILABLE_WRITE = "reconcilable_write"
    NON_IDEMPOTENT_WRITE = "non_idempotent_write"
```

## 6.6 ToolRecoveryPolicy

```python
class ToolRecoveryPolicy(StrEnum):
    RETRY_SAFE = "retry_safe"
    VERIFY_THEN_RETRY = "verify_then_retry"
    VERIFY_THEN_SYNTHESIZE = "verify_then_synthesize"
    MANUAL_RECONCILIATION = "manual_reconciliation"
    NEVER_RETRY = "never_retry"
```

## 6.7 PreparedToolCall

```python
@dataclass(frozen=True, slots=True)
class PreparedToolCall:
    logical_action_id: str
    tool_call_id: str
    tool_name: str

    principal: SerializedPrincipal
    canonical_arguments: dict[str, Any]
    arguments_hash: str

    effect_class: ToolEffectClass
    recovery_policy: ToolRecoveryPolicy

    permission_decision: str
    permission_evidence: dict[str, Any]

    approval_required: bool
    approval_id: str | None

    precondition: dict[str, Any] | None
    expected_postcondition: dict[str, Any] | None
    idempotency_key: str | None

    tool_definition_hash: str
    created_at: str
```

## 6.8 PersistentApproval

```python
@dataclass(frozen=True, slots=True)
class PersistentApproval:
    approval_id: str
    logical_action_id: str

    thread_id: str
    turn_id: str
    agent_id: str
    tool_call_id: str
    tool_name: str

    argument_preview: dict[str, Any]
    argument_hash: str
    reason: str
    risk_level: str
    requested_capabilities: tuple[str, ...]

    scope_options: tuple[str, ...]
    state: str
    decision: str | None

    requested_at: str
    decided_at: str | None
```

稳定 Approval ID：

```text
approval_id =
sha256(
    thread_id
    + NUL
    + turn_id
    + NUL
    + agent_id
    + NUL
    + tool_call_id
    + NUL
    + arguments_hash
)
```

不得在恢复时重新生成不同的审批对象。

---

# 七、RolloutItem v2

现有 `RolloutItem` 增加：

```python
schema_version: int
sequence_number: int

correlation_id: str | None
causation_id: str | None

logical_action_id: str | None
checkpoint_id: str | None
attempt_number: int | None

previous_item_hash: str | None
item_hash: str
```

## 7.1 Hash Chain

每个新 v2 Item：

```text
item_hash =
sha256(
    canonical_json(item_without_item_hash)
)
```

`previous_item_hash` 指向上一条 v2 Item。

用途：

- 检测中间行删除；
- 检测顺序改变；
- 检测内容修改；
- 检测 Metadata 与 Rollout 不一致。

兼容旧历史：

- v1 Item 继续读取；
- 第一个 v2 Item 写入 `migration.rollout_v2_started`；
- 其 `previous_item_hash` 可以为空；
- 不重新覆盖旧 JSONL。

## 7.2 必须补齐的 Item

```text
checkpoint.created
checkpoint.committed
checkpoint.invalidated

model.request.prepared
model.request.started
model.response.committed
model.request.failed

tool.prepared
tool.approval.requested
tool.approval.decided
tool.execution.started
tool.execution.succeeded
tool.execution.failed
tool.execution.outcome_unknown
tool.reconciled

recovery.started
recovery.auto_resumed
recovery.blocked
recovery.user_resolved
recovery.completed

memory.candidate.created
memory.candidate.rejected
memory.written
memory.retrieved
memory.injected
memory.invalidated
memory.deleted

context.compaction.started
context.compaction.completed
context.compaction.failed

subagent.checkpointed
subagent.recovered
subagent.restarted
```

Trace 可以保留更细粒度调试事件，但影响恢复的语义边界必须进入 Rollout/Outbox。

---

# 八、Durable Agent Loop

## 8.1 不恢复调用栈

当前 `AgentLoop.run()` 是一个长 `while` 协程。阶段 6 不尝试恢复协程位置。

应改造为：

```text
ResumableAgentLoop
    ↓
读取 DurableTurnState.resume_point
    ↓
执行一个 Step
    ↓
持久化状态和下一个 ResumePoint
    ↓
继续或返回 PAUSED / TERMINAL
```

推荐接口：

```python
class ResumableAgentLoop:
    async def run_until_pause_or_terminal(
        self,
        state: DurableTurnState,
    ) -> DurableTurnState:
        ...

    async def step(
        self,
        state: DurableTurnState,
    ) -> StepResult:
        ...
```

## 8.2 Model 调用协议

### 调用前

事务写入：

```text
LogicalAction(model)
state = PREPARED
Checkpoint.resume_point = BEFORE_MODEL
```

随后：

```text
state = STARTED
Checkpoint.resume_point = MODEL_IN_FLIGHT
COMMIT
```

才允许调用 Provider。

### 响应后

Provider 返回后，必须先在同一事务中保存：

- 原始可序列化 ModelResponse；
- assistant message；
- tool calls；
- finish reason；
- usage；
- provider response id；
- attempt number；
- `Checkpoint.resume_point = AFTER_MODEL`；
- 对应 Outbox Event。

Commit 成功后才允许继续消费 Tool Call。

### 崩溃恢复

```text
BEFORE_MODEL
    可正常请求模型。

MODEL_IN_FLIGHT
    模型响应未知。
    允许创建 attempt + 1 后重新请求。
    记录可能产生重复模型费用。
    不将其当作外部副作用 exactly-once。

AFTER_MODEL
    直接使用已保存 ModelResponse。
    不得再次请求模型。
```

## 8.3 多 Tool Call

同一 ModelResponse 包含多个 Tool Call 时：

```text
保存完整 ModelResponse
    ↓
为每个 Tool Call 创建稳定 logical_action_id
    ↓
按模型返回顺序执行
    ↓
每个 Tool Result 独立 durable
    ↓
所有 Tool Result 完成后进入 BEFORE_MODEL
```

一个 Tool 进入 `RECOVERY_REQUIRED` 时：

- 后续 Tool 不执行；
- Turn 暂停；
- 已完成 Tool Result 保留；
- 用户核对完成后继续剩余 Tool。

## 8.4 Tool Result 进入 Context

Tool 成功后：

```text
执行 Tool
    ↓
持久化 ToolResult
    ↓
Checkpoint = AFTER_TOOL
    ↓
将 ToolResult 构造成 CanonicalMessage
    ↓
持久化 message_consumed 标记
    ↓
Checkpoint = BEFORE_MODEL 或 BEFORE_NEXT_TOOL
```

如果崩溃发生在 Tool Result 已提交但 Message 尚未加入内存：

```text
恢复时从 Tool Journal 重建 Tool Message
```

不得重新执行 Tool。

## 8.5 Finalization

最终输出产生后：

```text
持久化 final_output
Checkpoint = BEFORE_FINALIZE
    ↓
写 agent_message
    ↓
写唯一 Turn Terminal Item
    ↓
Checkpoint = TERMINAL
    ↓
更新 metadata 为 IDLE
```

唯一约束：

```text
UNIQUE(thread_id, turn_id, terminal_outcome)
```

恢复时如果发现 Terminal Item 已存在：

- 只修复 Metadata；
- 不再次写 `turn.completed`；
- 不再次运行 Memory Extraction；
- 不再次执行 Completion Hook。

---

# 九、ToolRuntime 改造

## 9.1 拆分 Prepare 与 Execute

当前 `ToolRuntime.execute()` 同时完成：

- 参数解析；
- Schema 校验；
- Permission；
- Pre Hook；
- Approval；
- Executor；
- Post Hook；
- Result 格式化。

阶段 6 改为：

```python
prepared = await tool_runtime.prepare(call, principal)

if prepared.approval_required:
    await approval_service.pause(prepared)
    return WAITING_APPROVAL

result = await tool_runtime.execute_prepared(prepared)
```

推荐接口：

```python
class ToolRuntime:
    async def prepare(
        self,
        call: ToolCall,
        principal: ToolExecutionPrincipal,
    ) -> PreparedToolCall:
        ...

    async def execute_prepared(
        self,
        prepared: PreparedToolCall,
    ) -> ToolResult:
        ...

    async def reconcile(
        self,
        prepared: PreparedToolCall,
        journal: ToolExecutionJournal,
    ) -> ReconciliationResult:
        ...
```

`execute_prepared()` 必须验证：

- Prepared ToolDefinition Hash 仍匹配；
- Principal 没有扩大；
- Approval ID 和 Decision 匹配；
- arguments hash 没有变化；
- Logical Action 尚未有结果；
- Recovery 状态允许执行。

## 9.2 ToolDefinition 新字段

```python
@dataclass(slots=True)
class ToolDefinition:
    ...
    effect_class: ToolEffectClass
    recovery_policy: ToolRecoveryPolicy

    supports_idempotency_key: bool = False
    idempotency_key_field: str | None = None

    precondition_builder: Callable | None = None
    postcondition_verifier: Callable | None = None
    reconciler: Callable | None = None
```

这些值由 Tool 注册代码定义，不能由模型参数覆盖。

## 9.3 Approval 执行顺序

```text
Tool Prepare
    ↓
持久化 PreparedToolCall
    ↓
创建 PendingApproval
    ↓
Checkpoint = WAITING_APPROVAL
    ↓
返回 CLI / UI
    ↓
用户 Decision
    ↓
持久化 Decision
    ↓
Checkpoint = BEFORE_TOOL
    ↓
执行 Tool
```

禁止：

```text
先执行 Tool
再写 Approval Decision
```

禁止：

```text
恢复后重新请求模型生成 Tool Call
```

## 9.4 Approval Grant

现有 once、turn、thread Grant 扩展为：

```text
Once Grant
    绑定 logical_action_id

Turn Grant
    绑定 thread_id + turn_id + tool + canonical argument matcher

Thread Grant
    绑定 thread_id + tool + canonical argument matcher
```

持久化要求：

- Turn Grant 随 Turn Terminal 清理；
- Thread Grant 随 Thread 关闭或显式撤销清理；
- `ALLOW_THREAD` 不跨 Thread；
- 不将 Approval Grant 写成长期 Memory；
- 不恢复已经失效的 Project Trust；
- Approval Grant 必须保留来源和创建时间。

---

# 十、内置 Tool 恢复策略

## 10.1 `list_files`、`read_file`、`search_text`

```text
EffectClass = READ_ONLY
RecoveryPolicy = RETRY_SAFE
```

如果状态为 `TOOL_IN_FLIGHT` 且没有 Result：

- 可以创建新 attempt；
- 重新执行；
- 旧 attempt 标记为 abandoned；
- 返回新结果。

## 10.2 `write_file`

准备阶段记录：

```text
resolved_path
before_exists
before_sha256
desired_content_sha256
expected_after_sha256
```

恢复判定：

```text
当前 hash == expected_after_sha256
    → 合成 SUCCEEDED_RECONCILED
    → 不重新写入

当前 hash == before_sha256
    → 可以执行原写入

当前状态与 before / after 均不匹配
    → RECOVERY_REQUIRED
    → 禁止覆盖并发修改
```

写入仍使用原子临时文件替换。

## 10.3 `apply_patch`

准备阶段记录：

```text
resolved_path
before_sha256
old_text_hash
new_text_hash
expected_after_sha256
```

恢复判定：

```text
当前 hash == expected_after_sha256
    → 已成功，合成结果

当前 hash == before_sha256
且 old_text 仍精确出现一次
    → 可执行

new_text 已存在
且 old_text 不存在
且文件整体符合 expected_after_sha256
    → 已成功，合成结果

其他情况
    → RECOVERY_REQUIRED
```

不借阶段 6 扩大为通用 Unified Diff Parser。

## 10.4 `delete_path`

默认：

```text
EffectClass = NON_IDEMPOTENT_WRITE
RecoveryPolicy = MANUAL_RECONCILIATION
```

原因：

- 路径不存在不能证明一定是 Harness 删除；
- 崩溃期间可能有外部进程删除或替换；
- 目录内容可能变化。

阶段 6 第一版不自动重放 `delete_path`。

可以记录：

```text
path type
before hash / manifest hash
resolved path
```

用于用户核对。

## 10.5 `run_command`

默认：

```text
EffectClass = NON_IDEMPOTENT_WRITE
RecoveryPolicy = NEVER_RETRY
```

即使某些命令看似只读，也不能由模型自行判断后自动重放。

可选后续增加 Host 定义的严格白名单：

```text
pytest --collect-only
git status --short
git diff --check
```

第一版不要求自动分类。

当命令进入 `TOOL_IN_FLIGHT` 后进程崩溃：

```text
tool.execution.outcome_unknown
Turn = RECOVERY_REQUIRED
```

用户可以：

- 标记为失败并让 Agent 继续；
- 标记为已成功并提供结果；
- 放弃原 Turn；
- 显式创建新的 Tool Attempt。

## 10.6 MCP Tool

沿用阶段 5.1 已建立的原则。

### Trusted Read-only MCP Tool

只有同时满足：

```text
Server Scope 可信
Annotation 可信
readOnlyHint = true
destructiveHint != true
阶段 5.1 Policy 判定为只读
```

才可使用：

```text
RecoveryPolicy = RETRY_SAFE
```

### 写入或副作用 MCP Tool

```text
RecoveryPolicy = NEVER_RETRY
```

Session 失效或进程崩溃后结果未知：

```text
MCP_TOOL_OUTCOME_UNKNOWN
```

不得自动重新调用。

恢复时重新 Initialize MCP Server，刷新 Catalog，并比较：

```text
server identity
credential identity
remote tool name
canonical tool name
input schema hash
approval mode
```

不恢复旧网络 Session ID。

## 10.7 Subagent Control Tool

`spawn_subagent` 应使用稳定 `delegation_id`。

恢复时：

```text
Child 已存在
    → 返回原 Child Handle

Child 已完成
    → 返回原结构化结果

Child 创建记录存在但尚未开始
    → 启动原 Child

状态未知
    → 根据 Child Checkpoint 判断
```

不得因为 Root 恢复重复创建同一逻辑 Child。

---

# 十一、恢复协调器

新增：

```python
class RecoveryCoordinator:
    async def inspect_thread(thread_id: str) -> RecoveryPlan:
        ...

    async def auto_recover(plan: RecoveryPlan) -> RecoveryResult:
        ...

    async def apply_user_resolution(
        thread_id: str,
        resolution: UserRecoveryResolution,
    ) -> RecoveryResult:
        ...
```

## 11.1 启动恢复顺序

```text
打开 SQLite
    ↓
运行 Migration
    ↓
校验 WAL / Foreign Key
    ↓
Drain Transactional Outbox
    ↓
校验 rollout tail 和 hash chain
    ↓
读取最新 committed Checkpoint
    ↓
比较 Runtime / Config / Catalog Compatibility
    ↓
重新初始化 Project Paths 和 Trust
    ↓
恢复 Guidance / Skill Snapshot
    ↓
重新 Initialize MCP
    ↓
重建 Provider / Tool Registry / Permission Engine
    ↓
按 ResumePoint 生成 RecoveryPlan
    ↓
安全自动恢复或进入 RECOVERY_REQUIRED
```

## 11.2 Compatibility Check

恢复前比较：

```text
checkpoint schema version
runtime schema version
agent definition hash
tool catalog hash
tool definition hash
permission policy hash
sandbox policy hash
guidance snapshot hash
skill snapshot hash
MCP catalog / identity hash
provider / model
```

处理规则：

### 安全兼容

例如：

- UI 文案变化；
- 非当前 Tool 的 Catalog 增加；
- Trace 配置变化。

可继续，写 warning。

### 需要重新准备

例如：

- Pending Tool 尚未执行；
- ToolDefinition Hash 变化；
- Permission Policy 变严格。

处理：

```text
重新执行本地 Prepare
但不得扩大权限
如果新结果为 DENY，则拒绝
如果新结果为 ASK，则重新显示审批
```

### 不兼容

例如：

- `TOOL_IN_FLIGHT` 的 ToolDefinition 已不存在；
- Side Effect 分类发生变化；
- Workspace Root 与原 Checkpoint 不匹配；
- MCP Credential Identity 变化；
- Checkpoint Schema 无迁移路径。

进入：

```text
RECOVERY_REQUIRED
```

## 11.3 自动恢复矩阵

```text
BEFORE_MODEL
    自动继续。

MODEL_IN_FLIGHT
    创建新 Model Attempt，允许继续。

AFTER_MODEL
    复用已提交 ModelResponse。

WAITING_APPROVAL
    重新显示原 Approval。

BEFORE_TOOL
    若 ToolDefinition 和 Approval 兼容，可继续。

TOOL_IN_FLIGHT + RETRY_SAFE
    新 attempt 重试。

TOOL_IN_FLIGHT + VERIFY_THEN_RETRY
    先核对，再决定。

TOOL_IN_FLIGHT + NEVER_RETRY
    RECOVERY_REQUIRED。

AFTER_TOOL
    重建 Tool Message，继续。

WAITING_SUBAGENT
    恢复或重启对应 Child。

BEFORE_FINALIZE
    幂等完成 Turn。

TERMINAL
    只修复索引，不执行业务动作。
```

---

# 十二、CLI 与用户恢复交互

## 12.1 `resume`

保留：

```bash
agent-harness resume [thread_id]
```

新行为：

```text
Thread IDLE
    → 恢复普通会话。

Thread WAITING_APPROVAL
    → 显示原 Approval 并等待决定。

Thread SAFE_RECOVERABLE
    → 自动恢复同一 Turn。

Thread RECOVERY_REQUIRED
    → 显示 Recovery Summary，不自动继续。
```

## 12.2 新命令

```bash
agent-harness recover <thread_id> --status
agent-harness recover <thread_id> --continue
agent-harness recover <thread_id> --abandon-turn
agent-harness recover <thread_id> --mark-tool-succeeded <logical_action_id>
agent-harness recover <thread_id> --mark-tool-failed <logical_action_id>
agent-harness recover <thread_id> --retry-tool <logical_action_id>
```

`--retry-tool` 只有在：

- Tool Policy 允许；
- 或用户收到明确风险提示并进行二次确认；

才能创建新 attempt。

交互命令：

```text
/checkpoint
/recovery
/recovery continue
/recovery abandon
/approvals
```

## 12.3 用户提供核对结果

当用户标记 Tool 已成功时，必须要求提供：

```text
状态说明
可选 Tool Result
可选 Artifact 引用
```

系统写入：

```text
recovery.user_resolved
tool.reconciled
```

不能伪装成原 Executor 自动返回的结果。

---

# 十三、Memory System

## 13.1 Memory 类型

阶段 6 使用统一 Memory Store，不为每种 Memory 建四套数据库。

### Thread Memory

由以下内容构成：

```text
rollout history
recent messages
compaction summary
thread goal
```

只作用于一个 Thread。

### Turn / Task Working Memory

保存在 Checkpoint：

```text
当前目标
当前计划
约束
已验证事实
未解决问题
Working Set
Pending Actions
```

默认不跨 Thread。

### Project Memory

跨 Thread、同一 Project Identity 可检索：

```text
经过验证的项目结构
测试入口
构建命令
稳定架构事实
常见失败及解决方式
用户明确要求长期保留的项目偏好
```

### Agent Memory

绑定：

```text
project_identity
agent_name
```

用于保存角色范围内的稳定经验，例如 reviewer 的已验证审查重点。

第一版 Agent Memory 不跨项目共享。

## 13.2 Project Identity

第一版默认使用本地 Workspace Scope：

```text
project_identity_v1 =
sha256(
    normalized_resolved_project_root
)
```

额外保存：

```text
git remote canonical URL
git repository root
current branch
HEAD commit
```

但这些不是第一版主键，避免同一 Remote 的不同本地 Checkout 互相污染。

未来可增加显式“共享同一远程仓库 Memory”的用户配置，不在本阶段默认开启。

## 13.3 MemoryRecord

```python
@dataclass(frozen=True, slots=True)
class MemoryRecord:
    schema_version: int
    memory_id: str

    namespace: str
    scope: MemoryScope
    memory_type: str

    project_identity: str | None
    thread_id: str | None
    agent_name: str | None

    content: str
    structured_data: dict[str, Any]

    source_kind: MemorySourceKind
    verification_status: VerificationStatus
    confidence: float
    trust_label: str

    source_thread_id: str
    source_turn_id: str | None
    source_item_ids: tuple[str, ...]
    source_artifact_ids: tuple[str, ...]

    content_hash: str
    supersedes_id: str | None

    created_by: str
    created_at: str
    updated_at: str

    expires_at: str | None
    invalidated_at: str | None
    invalidation_reason: str | None

    sensitivity: str
    tags: tuple[str, ...]
```

## 13.4 MemorySourceKind

```python
class MemorySourceKind(StrEnum):
    USER_EXPLICIT = "user_explicit"
    TOOL_VERIFIED = "tool_verified"
    TEST_VERIFIED = "test_verified"
    MODEL_INFERRED = "model_inferred"
    MCP_EXTERNAL = "mcp_external"
    COMPACTION_SUMMARY = "compaction_summary"
    IMPORTED = "imported"
```

## 13.5 VerificationStatus

```python
class VerificationStatus(StrEnum):
    USER_ASSERTED = "user_asserted"
    VERIFIED = "verified"
    INFERRED = "inferred"
    UNTRUSTED_EXTERNAL = "untrusted_external"
    STALE = "stale"
    CONFLICTED = "conflicted"
```

置信度不能代替验证状态。

例如：

```text
confidence = 0.98
verification = MODEL_INFERRED
```

仍不能作为已验证项目事实。

## 13.6 Memory 权限边界

Memory 永远不能：

- 扩大 Tool allowlist；
- 增加 Capability；
- 绕过 PermissionEngine；
- 自动批准 Tool；
- 修改 SandboxMode；
- 修改 Workspace Trust；
- 自动启动 Project MCP Server；
- 覆盖 Admin/User/Project Guidance；
- 将 MCP 外部内容升级为可信系统指令。

Context 优先级：

```text
System Policy
>
Permission / Sandbox / Approval
>
Admin Guidance
>
User Guidance
>
Project Guidance
>
Current User Input
>
Verified Project Memory
>
User-asserted Memory
>
Inferred Memory
>
Untrusted External Memory
```

Memory 注入区必须标记：

```text
辅助信息
非权威
可能过期
带来源和验证状态
```

---

# 十四、Memory 写入流程

## 14.1 Task 级控制

每个 Thread/Turn 配置：

```python
memory_read_enabled: bool
memory_write_enabled: bool
memory_auto_extract_enabled: bool
```

默认建议：

```text
Memory Read：开启
User Explicit Write：开启
Automatic Extraction：关闭或 conservative
```

CLI：

```text
/memories
/memories use on|off
/memories contribute on|off
/memories auto-extract on|off
```

## 14.2 Candidate 流程

```text
Turn 进入 Terminal + Thread IDLE
    ↓
检查 memory_write_enabled
    ↓
只处理 COMPLETED Turn
    ↓
生成 MemoryCandidate
    ↓
Secret Redaction
    ↓
Scope 检查
    ↓
Source / Evidence 检查
    ↓
重复检查
    ↓
冲突检查
    ↓
Policy 决定：
    ├── 自动写入
    ├── 等待用户确认
    └── 拒绝
```

第一版自动写入仅允许：

```text
用户显式要求记住
或
具有明确 Tool/Test Evidence 的低风险项目事实
```

以下默认不自动写入：

- 模型纯推断；
- 活动 Turn；
- Failed / Cancelled / Interrupted Turn；
- 未完成计划；
- 临时日志；
- Tool 原始大输出；
- Secret；
- Access Token；
- API Key；
- OAuth Code；
- `.env` 内容；
- 未信任 MCP 内容；
- Approval Decision；
- Permission Grant；
- Sandbox 临时状态。

## 14.3 Memory Candidate

```python
@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    candidate_id: str
    proposed_scope: MemoryScope
    proposed_type: str
    content: str
    structured_data: dict[str, Any]

    source_kind: MemorySourceKind
    source_item_ids: tuple[str, ...]
    evidence_artifact_ids: tuple[str, ...]

    proposed_confidence: float
    verification_status: VerificationStatus

    secret_scan_status: str
    duplicate_of: str | None
    conflicts_with: tuple[str, ...]

    decision: str
    rejection_reason: str | None
```

## 14.4 去重

第一版使用：

```text
Project / Agent / Scope Metadata Filter
    ↓
normalized content hash
    ↓
FTS5 lexical similarity
    ↓
结构字段精确比较
```

不要求向量数据库。

处理：

```text
完全相同
    → 更新 last_seen_at 和 evidence，不创建新记录。

新事实取代旧事实
    → 新记录 supersedes 旧记录。

发生冲突
    → 两条都保留，标记 conflicted，禁止自动高置信注入。
```

## 14.5 Secret Redaction

新增统一 `MemoryRedactor`，复用或扩展现有 Secret Policy：

- 常见 API Key Pattern；
- Bearer Token；
- OAuth Token；
- Private Key；
- `.env` 键值；
- Keyring Secret；
- MCP credential；
- 高熵字符串；
- 用户配置的 deny pattern。

Redaction 后保存：

```text
redaction_count
redaction_categories
redacted_content_hash
```

不保存被删除的 Secret 原文。

---

# 十五、Memory 检索与 Context 注入

## 15.1 第一版检索

```text
Scope Filter
├── project_identity
├── agent_name
├── active status
├── verification status
└── trust label
    ↓
SQLite FTS5
    ↓
稳定排序
    ↓
去重
    ↓
预算裁剪
```

排序特征：

```text
Verification
Scope Match
Lexical Relevance
Confidence
Recency
Evidence Strength
Staleness
```

推荐稳定评分，不把最终排序完全交给模型。

如果 Python/SQLite 构建不支持 FTS5：

```text
使用稳定 substring + token overlap fallback
```

不得因此引入新外部搜索服务。

## 15.2 Retrieval Query

查询由 Runtime 构造：

```text
当前用户输入
当前 Thread Goal
当前 Task
当前 Agent Role
当前 Working Set 路径
```

模型可以建议检索关键词，但不能修改 Scope 和 Trust Filter。

## 15.3 注入格式

ContextBuilder 增加独立区：

```text
[Retrieved Memory — non-authoritative]

Memory ID: mem_xxx
Scope: project
Verification: verified
Confidence: 0.92
Source: thread_x / turn_3 / item_x
Last verified: 2026-07-12
Content:
项目测试入口为 `uv run --no-sync python -m pytest ...`
```

限制：

- 不伪装成 System Message；
- 不伪装成用户新输入；
- 不写入 canonical conversation history；
- 每次注入记录 `memory.retrieved` 和 `memory.injected`；
- 达到预算后停止，不截断单条关键 Memory；
- Untrusted External Memory 单独分区并降低优先级。

## 15.4 Context 预算

新增：

```text
memory catalog / retrieval budget
```

建议第一版：

- 不超过总输入预算的 5%；
- 最大 10 条；
- 单条最大字符数；
- 超大 Evidence 只注入 Artifact 引用；
- Verified Memory 优先于 Inferred Memory；
- 最近原始 Turn 优先于普通长期 Memory。

---

# 十六、Memory 失效与删除

## 16.1 失效原因

```text
TTL 到期
用户手动失效
被新 Memory supersede
Project Identity 不匹配
依赖文件删除
依赖文件 hash 变化
与新 Tool Result 冲突
测试失败推翻旧结论
Git 状态发生关键变化
安全策略要求删除
```

## 16.2 Dependency

```python
@dataclass(frozen=True, slots=True)
class MemoryDependency:
    memory_id: str
    dependency_type: str
    dependency_key: str
    expected_hash: str | None
    last_checked_at: str
```

例如：

```text
memory:
    “认证逻辑位于 src/auth/service.py”

dependency:
    file path = src/auth/service.py
    expected hash = abc...
```

检索前可以轻量检查重要依赖。

第一版不对所有 Memory 每次全量扫描。

## 16.3 删除语义

支持：

```text
invalidate
    保留审计记录，不再检索。

delete
    删除 Memory 正文和索引，保留最小 Tombstone。

purge
    用户明确要求时删除正文、Evidence 引用和 Tombstone。
```

CLI：

```bash
agent-harness memory list
agent-harness memory search "pytest"
agent-harness memory show <memory_id>
agent-harness memory invalidate <memory_id>
agent-harness memory delete <memory_id>
```

---

# 十七、Context Compaction

## 17.1 设计原则

```text
Compaction 是 Context 派生缓存
不是 Rollout 删除
不是长期 Memory 自动等价物
```

Canonical Rollout 永远保留。

## 17.2 触发条件

只在：

```text
Turn 完成
Thread IDLE
没有 Pending Approval
没有 RECOVERY_REQUIRED Action
没有活动 Child
```

时执行。

触发可基于：

- estimated input token 达到阈值；
- Turn 数达到阈值；
- message 数达到阈值；
- 用户显式 `/compact`；
- 启动恢复发现 Context 过大。

默认不在流式输出结束前同步运行，避免阻塞用户继续输入。

## 17.3 Protected Content

不得普通压缩或删除：

- System Prompt；
- Admin/User/Project Guidance；
- 当前 Thread Goal；
- 用户明确长期约束；
- Active Skill durable guidance；
- Pending Approval；
- Pending Tool；
- RECOVERY_REQUIRED Action；
- 未完成计划；
- 最近若干 Turn；
- Tool Result 的关键错误代码；
- Memory Source/Evidence ID；
- Trust 和 Permission 状态。

## 17.4 CompactionRecord

```python
@dataclass(frozen=True, slots=True)
class CompactionRecord:
    compaction_id: str
    thread_id: str

    source_sequence_start: int
    source_sequence_end: int
    source_hash: str

    summary_text: str
    summary_artifact_id: str | None
    summary_hash: str

    protected_item_ids: tuple[str, ...]
    summarizer_provider: str
    summarizer_model: str
    prompt_version: str

    created_at: str
```

## 17.5 Context 重建

```text
最近有效 Compaction Summary
+
Compaction 之后的原始 Item
+
Protected Item
+
当前 Turn
+
Retrieved Memory
```

Compaction 失败：

- 不损坏 Thread；
- 保留原始 Context；
- 写 `context.compaction.failed`；
- 不影响 Turn Terminal。

---

# 十八、Subagent Checkpoint 与恢复

当前 Child 主要由进程内 `SubagentScheduler` 管理。阶段 6 后期将 Child 映射为可恢复 Child Thread。

## 18.1 Child 目录

```text
.harness/threads/<root_thread_id>/agents/<child_thread_id>/
├── metadata.json
├── rollout.jsonl
├── result.json
└── artifacts/
```

Child Checkpoint 存在 `runtime.sqlite3` 的独立 namespace。

## 18.2 Parent 保存内容

```text
delegation_id
child_thread_id
child_agent_id
parent_thread_id
parent_turn_id
delegation request hash
allowed tools
allowed MCP tools
budget reservation
status
latest checkpoint id
result ref
```

## 18.3 恢复规则

```text
Child Result 已 durable
    → Root 复用原结果。

Child 已创建但未开始
    → 启动原 Child。

Child 位于 BEFORE_MODEL / AFTER_MODEL / AFTER_TOOL
    → 从 Child Checkpoint 恢复。

Child 位于 TOOL_IN_FLIGHT + 不安全 Tool
    → Child 进入 RECOVERY_REQUIRED
    → Root 同时等待用户核对。

Child 没有 Checkpoint
    → 标记 interrupted
    → Root 可以显式重新委派。
```

## 18.4 Budget

恢复不能重复扣除 Child Budget。

Budget Reservation 使用稳定 `delegation_id` 唯一约束：

```text
同一 delegation_id 只预留一次
完成、取消或关闭后只释放一次
```

## 18.5 Root Final Guard

Root 不能在以下情况最终回答：

- Child Running；
- Child Waiting Approval；
- Child Recovery Required；
- Child Result 尚未由 Root 消费。

---

# 十九、SQLite Schema 建议

## 19.1 Runtime Database

至少包含：

```sql
schema_migrations

thread_runtime
turn_runtime
checkpoints

logical_actions
model_attempts
prepared_tool_calls
tool_attempts
tool_results

pending_approvals
approval_decisions
approval_grants

child_executions
budget_reservations

rollout_outbox
recovery_records
```

关键唯一约束：

```text
UNIQUE(thread_id, turn_id, checkpoint_sequence)
UNIQUE(thread_id, sequence_number)
UNIQUE(logical_action_id, attempt_number)
UNIQUE(tool_call_id, logical_action_id)
UNIQUE(approval_id)
UNIQUE(delegation_id)
UNIQUE(thread_id, turn_id, terminal_outcome)
UNIQUE(event_id)
```

## 19.2 Memory Database

至少包含：

```sql
memory_records
memory_sources
memory_artifacts
memory_dependencies
memory_tags
memory_tombstones
memory_retrieval_events
memory_fts
```

## 19.3 SQLite 设置

启动时：

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = FULL;
PRAGMA busy_timeout = 5000;
```

说明：

- Durable transition 使用短事务；
- 不在事务内执行模型、Tool 或网络调用；
- 外部调用前后分别提交状态；
- 不长期持有读事务；
- 正常关闭时可执行受控 checkpoint；
- 不把数据库放在网络共享盘。

---

# 二十、Rollout 和 Metadata 完整性

## 20.1 Durable Flush

以下边界必须执行 durable flush / transaction commit：

- Turn Start；
- ModelResponse Committed；
- Approval Requested；
- Approval Decided；
- Tool Execution Started；
- Tool Result Committed；
- Recovery Resolution；
- Turn Terminal；
- Memory Written / Deleted。

普通 Trace Delta 不要求每条 `fsync`。

## 20.2 JSONL 损坏

恢复算法：

```text
逐行读取
    ↓
验证 JSON
    ↓
验证 sequence
    ↓
验证 hash chain
```

### 最后一行损坏

- 移动到 `rollout.corrupt-tail.<timestamp>`；
- 截断到最后一条有效换行；
- 检查 Outbox 是否可重新投影；
- 写 recovery audit。

### 中间行损坏

- Thread 标记 `CORRUPTED`；
- 禁止自动继续；
- 保留原文件；
- 提供 inspect/repair 命令；
- 不静默跳过。

## 20.3 Metadata 修复

```text
Terminal Item 存在 + metadata ACTIVE
    → 修复为 IDLE。

Checkpoint WAITING_APPROVAL + metadata IDLE
    → 修复为 WAITING_APPROVAL。

Metadata latest_checkpoint_id 不存在
    → 从数据库选择最新 committed checkpoint。
```

---

# 二十一、配置

新增建议：

```toml
[persistence]
enabled = true
runtime_db = ".harness/runtime.sqlite3"
journal_mode = "wal"
synchronous = "full"
auto_recover_safe = true
outbox_batch_size = 100
checkpoint_retention_per_turn = 20
fail_on_integrity_error = true

[recovery]
allow_model_retry_after_unknown_response = true
auto_retry_read_only_tools = true
auto_retry_writes = false
require_confirmation_for_manual_retry = true

[memory]
enabled = true
read_enabled = true
write_enabled = true
auto_extract = false
max_results = 10
max_context_fraction = 0.05
min_auto_write_confidence = 0.90
require_evidence_for_auto_write = true
fts_enabled = true

[compaction]
enabled = true
auto_compact = true
idle_only = true
estimated_token_threshold = 0.75
retain_recent_turns = 6
max_summary_chars = 12000
```

Admin/User/Project Scope：

- Project Config 不得在 Untrusted Workspace 开启自动 Memory 写入；
- Project Config 不得降低 Recovery 安全策略；
- Admin 可以强制关闭自动副作用重试；
- Memory Config 不影响 Permission；
- Project Memory 读取继续受 Workspace Trust Gate 约束。

---

# 二十二、代码目录与改造点

推荐新增：

```text
src/agent_harness/
├── persistence/
│   ├── database.py
│   ├── migrations.py
│   ├── transaction.py
│   ├── outbox.py
│   └── integrity.py
│
├── checkpoints/
│   ├── models.py
│   ├── store.py
│   ├── manager.py
│   ├── serializer.py
│   └── compatibility.py
│
├── recovery/
│   ├── coordinator.py
│   ├── plans.py
│   ├── policies.py
│   └── reconciliation.py
│
├── memory/
│   ├── models.py
│   ├── store.py
│   ├── candidates.py
│   ├── policies.py
│   ├── retrieval.py
│   ├── redaction.py
│   └── lifecycle.py
│
├── compaction/
│   ├── models.py
│   ├── planner.py
│   └── service.py
│
└── runtime/
    ├── thread_runtime.py
    └── resumable_loop.py
```

必须修改：

```text
config.py
cli.py
domain/run.py
domain/tools.py
rollout/items.py
threads/local_store.py
threads/live_thread.py
threads/recorder.py
turns/controller.py
runtime/session.py
runtime/run_manager.py
runtime/agent_loop.py
runtime/subagents/scheduler.py
tools/runtime.py
context/builder.py
security/approval.py
security/approval_grants.py
mcp/runtime.py
mcp/connection.py
tracing/summary.py
```

## 22.1 `ConversationSession`

最终职责收敛为：

```text
CLI compatibility adapter
```

核心 Thread/Turn 恢复迁移到：

```text
ThreadRuntime
RecoveryCoordinator
```

## 22.2 `RunManager`

负责重新组合瞬态依赖：

- Provider；
- ToolRegistry；
- PermissionEngine；
- Sandbox；
- Guidance；
- Skills；
- MCP；
- SubagentScheduler。

不直接实现数据库细节。

## 22.3 `ContextBuilder`

增加：

- Compaction Summary；
- Retrieved Memory；
- Pending Goal/Plan；
- Durable Working Set；
- Context Source Metadata；
- Memory Budget。

不直接写 Memory。

## 22.4 `RolloutRecorder`

扩展：

- sequence；
- v2 hash；
- Outbox Projector；
- durable boundary flush；
- tail repair；
- duplicate event detection。

---

# 二十三、实施批次

## 批次一：Durable Persistence Foundation

完成：

- SQLite Runtime Database；
- Migration；
- Transactional Outbox；
- RolloutItem v2；
- sequence 和 hash chain；
- JSONL integrity；
- Metadata repair；
- Repository 基线回归。

暂不改 Tool 恢复。

## 批次二：Checkpoint 与 Resumable Agent Loop

完成：

- CheckpointEnvelope；
- DurableTurnState；
- ResumePoint；
- Model Attempt Journal；
- AFTER_MODEL 恢复；
- AFTER_TOOL 恢复；
- 幂等 Turn Finalization；
- 进程重启继续同一 Turn。

## 批次三：Persistent Approval 与 Tool Recovery

完成：

- Tool prepare/execute 分离；
- PersistentApproval；
- Approval Grant 持久化；
- Tool Effect / Recovery Policy；
- Builtin 文件 Tool reconciliation；
- `run_command` outcome unknown；
- MCP no replay；
- Recovery CLI。

## 批次四：Memory Store

完成：

- Memory Schema；
- Project Identity；
- Candidate；
- Secret Redaction；
- 用户显式写入；
- Verified Candidate 自动写入；
- FTS5 / fallback；
- Context 注入；
- 失效、删除和 Tombstone；
- Memory CLI。

## 批次五：Context Compaction

完成：

- Idle-only Compaction；
- Protected Item；
- Compaction Record；
- Context 重建；
- Compaction Failure Isolation；
- Artifact 回退。

## 批次六：Subagent Recovery 与加固

完成：

- Child Thread Mapping；
- Child Checkpoint；
- Stable Delegation；
- Budget 幂等；
- Root/Child Recovery；
- Retention；
- 全量故障注入；
- 文档和 CI 验收。

---

# 二十四、故障注入测试

阶段 6 不能只测试“保存后能读取”，必须真实模拟进程中止。

新增 Fault Injector：

```python
class FaultPoint(StrEnum):
    AFTER_TURN_START_COMMIT = ...
    AFTER_MODEL_REQUEST_COMMIT = ...
    AFTER_MODEL_RESPONSE_RECEIVED = ...
    AFTER_MODEL_RESPONSE_COMMIT = ...

    AFTER_APPROVAL_REQUEST_COMMIT = ...
    AFTER_APPROVAL_DECISION_COMMIT = ...

    AFTER_TOOL_STARTED_COMMIT = ...
    AFTER_TOOL_EXECUTOR_RETURNED = ...
    AFTER_TOOL_RESULT_COMMIT = ...

    AFTER_TERMINAL_ITEM_COMMIT = ...
    AFTER_METADATA_UPDATE = ...

    DURING_OUTBOX_PROJECTION = ...
    DURING_COMPACTION = ...
    DURING_MEMORY_WRITE = ...
```

故障触发不能只抛普通 Exception，还要使用子进程：

```text
os._exit()
terminate()
kill()
```

验证重新启动后的真实状态。

## 24.1 必测场景

### Turn

- `turn.started` durable 后崩溃；
- Metadata ACTIVE 但 Rollout 尚未投影；
- Terminal 已提交但 Metadata 未更新；
- Terminal 唯一性。

### Model

- 请求前崩溃；
- 请求发送后、响应前崩溃；
- 响应返回后、commit 前崩溃；
- 响应 commit 后崩溃；
- AFTER_MODEL 恢复不得再次调用 Provider。

### Approval

- Approval Requested 后崩溃；
- 用户 Allow 后、Decision commit 前崩溃；
- Decision commit 后、Tool 前崩溃；
- Deny / Cancel 恢复；
- Stable Approval ID；
- Turn/Thread Grant 恢复和清理。

### Tool

- Read-only Tool 中断后重试；
- `write_file` 写入成功后 Result 前崩溃；
- `apply_patch` 成功后 Result 前崩溃；
- 文件被外部修改后禁止自动覆盖；
- `delete_path` 进入 manual reconciliation；
- `run_command` 进入 outcome unknown；
- MCP write Tool 不重放；
- Tool Result commit 后不得重复执行。

### Rollout

- 最后一行半写；
- 中间 JSON 损坏；
- sequence 缺失；
- hash chain 错误；
- Outbox 重复投影；
- Recorder sticky failure。

### Memory

- Failed Turn 不自动写 Memory；
- Active Turn 不自动写 Memory；
- Secret 不进入 Memory；
- MCP External 不自动升级为 Verified；
- Project Scope 不串库；
- duplicate / supersede / conflict；
- invalidate / delete；
- FTS5 fallback；
- 注入预算。

### Compaction

- Compaction 期间崩溃；
- Summary commit 前后恢复；
- 原 Rollout 不丢失；
- Pending Approval 不被压缩；
- Active Skill durable guidance 保留。

### Subagent

- Child 创建后崩溃；
- Child ModelResponse 后崩溃；
- Child Result commit 后 Root 崩溃；
- Stable Delegation 不重复 spawn；
- Budget 不重复扣除；
- Child outcome unknown 冒泡到 Root。

---

# 二十五、核心不变量

测试必须明确断言：

```text
一个 Thread 同一 sequence_number 只有一条 Item。

一个 Turn 最多一个 Terminal Outcome。

一个 logical_action_id 最多一个逻辑成功结果。

已 durable 的 ModelResponse 不重新请求。

已 durable 的 ToolResult 不重新执行 Tool。

非幂等 Tool 的未知结果不自动重放。

Approval Decision 不丢失、不重复消费。

Tool 参数、Principal 和 Approval 在恢复前后保持一致或更严格。

Memory 没有 Source/Evidence 不得自动写入。

Memory 不能扩大 Permission、Trust 或 Sandbox。

Untrusted MCP 内容不能自动成为 Verified Memory。

Compaction 不删除 Canonical Rollout。

Metadata 可以重建。

Child Delegation 不重复创建。

SQLite Transaction 内不执行网络或长时间 Tool。
```

---

# 二十六、验收标准

阶段 6 只有满足以下条件才能标记完成。

## 26.1 Durable Runtime

- [ ] 同一 Thread 可以在进程重启后继续原 Turn；
- [ ] BEFORE_MODEL、AFTER_MODEL、WAITING_APPROVAL、BEFORE_TOOL、AFTER_TOOL、BEFORE_FINALIZE 均有测试；
- [ ] 模型响应 durable 后不会重复请求；
- [ ] Tool Result durable 后不会重复执行；
- [ ] Pending Approval 可以跨进程恢复；
- [ ] Approval ID 稳定；
- [ ] Turn Terminal Exactly-once；
- [ ] Metadata 不作为唯一事实来源；
- [ ] Rollout 中间损坏不会静默跳过；
- [ ] Transactional Outbox 可以在重启后补投影；
- [ ] Runtime Schema 支持 Migration；
- [ ] 不序列化瞬态对象。

## 26.2 Tool Recovery

- [ ] 每个内置 Tool 声明 EffectClass；
- [ ] 每个内置 Tool 声明 RecoveryPolicy；
- [ ] `write_file` 支持 hash reconciliation；
- [ ] `apply_patch` 支持 pre/post reconciliation；
- [ ] `delete_path` 未知结果不自动重试；
- [ ] `run_command` 未知结果不自动重试；
- [ ] MCP write Tool 未知结果不自动重试；
- [ ] Read-only Tool 可以安全创建新 attempt；
- [ ] Tool Recovery 进入 Rollout 和 Trace。

## 26.3 Memory

- [ ] CheckpointStore 与 MemoryStore 独立；
- [ ] Memory 带 Scope、Source、Evidence、Verification、Trust；
- [ ] 支持显式 Memory Write；
- [ ] 自动写入仅限有 Evidence 的保守规则；
- [ ] Secret Redaction 生效；
- [ ] Project Memory 不跨 Workspace；
- [ ] 支持检索、注入、失效、删除；
- [ ] Memory 不进入 System 权威区；
- [ ] Memory 不修改 Permission；
- [ ] Memory Retrieval 有预算；
- [ ] Memory 事件可观测。

## 26.4 Compaction

- [ ] 只在安全 IDLE 边界自动执行；
- [ ] Canonical Rollout 不删除；
- [ ] 支持 Compaction Summary + Recent Raw Item 重建；
- [ ] Pending Action 和 Protected Item 不丢失；
- [ ] Compaction 崩溃不损坏 Thread。

## 26.5 Subagent

- [ ] Stable Delegation；
- [ ] Child Checkpoint；
- [ ] Child Result 复用；
- [ ] Child Budget 幂等；
- [ ] Child Unknown Outcome 不被 Root 隐藏；
- [ ] Root Terminal Guard 覆盖 Recovery Required Child。

## 26.6 工程门禁

必须执行：

```bash
uv sync --locked --extra test

uv run --no-sync python -m ruff check src tests
uv run --no-sync python -m mypy src
uv run --no-sync python -m pytest -m "unit or integration_local" -q
uv run --no-sync python -m pytest -m recovery_process -q
uv run --no-sync python -m pytest -m platform_linux -q
git diff --check
```

真实外部验证单独执行：

- DeepSeek Live；
- MCP stdio；
- MCP Streamable HTTP；
- OAuth；
- WSL2/bubblewrap。

Skip 不能被描述为通过。

---

# 二十七、不能声称的能力

即使阶段 6 完成，也不能声称：

- 任意外部副作用 exactly-once；
- 结果未知的邮件、支付、命令或 MCP 写入能够自动判断成功；
- Native Windows 沙箱已经完成；
- 跨机器恢复；
- 跨进程恢复旧 MCP HTTP Session；
- Memory 永远正确；
- Memory 不会过期；
- 向量语义检索已经完成；
- 任意 Runtime 版本都能无损恢复旧 Checkpoint；
- 中间损坏的 Rollout 可以自动无风险修复；
- 所有 Child 都能从任意 Python 执行点继续。

正确表述：

```text
系统提供安全边界内的 Durable Resume、
持久审批、Tool 幂等和人工核对机制，
并对不能安全判断的副作用显式返回 outcome unknown。
```

---

# 二十八、给 Codex 的执行要求

Codex 开始实现前必须：

1. checkout 并记录当前基线 SHA；
2. 阅读阶段 0、Thread/Turn/Item 修复、阶段 3、阶段 4、阶段 5 和 5.1 的设计与差异记录；
3. 阅读当前 `RunManager`、`ConversationSession`、`AgentLoop`、`ToolRuntime`、`TurnController`、`LocalThreadStore`、`RolloutRecorder`、`SubagentScheduler`；
4. 运行现有 Ruff、Mypy、Pytest，保存基线；
5. 先提交批次一的详细文件改动清单；
6. 不一次性重写整个项目；
7. 每个批次单独增加实现差异与验收记录；
8. 不将第三方 Agent Framework 引入核心 Runtime；
9. 不修改沙箱路线；
10. 不绕过现有 ToolRuntime、PermissionEngine、Approval、ArtifactStore 和 MCPRuntime；
11. 不用“重新执行整个 Turn”代替精确恢复；
12. 不用“模型判断是否成功”代替 Tool Reconciliation；
13. 不用普通 Memory 代替 Guidance；
14. 不把外部 MCP 内容直接写成可信 Memory；
15. 不在数据库事务中调用模型、Tool 或网络；
16. 所有持久结构必须有 schema version；
17. 所有不兼容恢复必须 fail closed；
18. 所有副作用恢复策略必须有确定性测试；
19. 所有进程恢复必须包含真实子进程 kill 测试；
20. 最终更新 README 的已完成和延期边界；
21. 最终新增《阶段 6 实现差异及验收记录》；
22. 只有对应提交 GitHub Actions 绿色后，才能声明阶段 6 核心门禁通过。

---

# 二十九、官方参考资料

## R1 OpenAI Codex App Server

Thread、Turn、Item、恢复和审批协议：

https://developers.openai.com/codex/app-server/

## R2 OpenAI Codex Memories

Memory 与 Guidance 分离、本地 Memory、Supporting Evidence、Secret Redaction、Idle Extraction：

https://learn.chatgpt.com/docs/customization/memories

## R3 OpenAI Agents SDK Human-in-the-loop

可序列化 RunState、Pending Approval、跨进程恢复：

https://openai.github.io/openai-agents-python/human_in_the_loop/

## R4 OpenAI Agents SDK Sessions

Session History、Tool Item 持久化、模型输入过滤、Compaction：

https://openai.github.io/openai-agents-python/sessions/

## R5 LangGraph Persistence

Checkpointer 与 Store 分离：

https://docs.langchain.com/oss/python/langgraph/persistence

## R6 Temporal Activities

副作用拆分、幂等 Activity、失败恢复：

https://docs.temporal.io/activities

## R7 SQLite WAL

WAL、Commit 和 Checkpoint：

https://www.sqlite.org/wal.html

## R8 MCP Specification

Lifecycle、Transport 和协议边界：

https://modelcontextprotocol.io/specification/2025-11-25

## R9 当前项目

https://github.com/dazzlingwuming/General-Agent-Design

基线：

```text
d8e019f3a7cacd4882f25c28355acc92070172b5
Record stages 1-5 CI acceptance
```

---

# 三十、最终结论

阶段 6 的实施顺序必须是：

```text
Durable Persistence
    ↓
Checkpoint / Resume Point
    ↓
Persistent Approval
    ↓
Tool Idempotency / Reconciliation
    ↓
Memory Store
    ↓
Context Compaction
    ↓
Subagent Recovery
```

不能先写一个“Memory Tool”就宣称阶段 6 已完成。

阶段 6 的核心价值不是让 Agent“记得更多”，而是保证：

```text
已经完成的动作不因恢复而盲目重复；
尚未完成的动作可以从明确位置继续；
无法判断结果的副作用会被明确阻断；
长期记忆有来源、范围、可信度、失效和删除机制；
完整历史、执行状态、模型上下文和长期记忆互不混淆。
```
