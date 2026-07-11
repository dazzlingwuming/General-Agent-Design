# General Agent Harness：Codex 式交互会话与 Runtime 修复完善方案

> 文档版本：v1.0  
> 文档日期：2026-07-11  
> 目标仓库：`https://github.com/dazzlingwuming/General-Agent-Design/tree/main/agent-harness`  
> 文档用途：直接交给 Codex，指导当前项目的重构、修复、测试和验收  
> 前置说明：本文件是独立文档，不依赖此前的“Session 化完善方案”  
> 当前目标：保留现有交互式 Coding Agent 产品形态，并将内部模型修正为更接近 Codex 的 `Thread → Turn → Item`

---

# 一、改造目标

当前项目的产品方向不是“一条任务执行一次、执行结束后销毁全部上下文”的一次性 Agent。

需要实现的是类似 Codex CLI 的持续交互：

```text
用户进入代码仓库
    ↓
创建或恢复一个 Thread
    ↓
连续输入多个请求
    ↓
每次请求在同一个 Thread 中形成新的 Turn
    ↓
Agent 在 Turn 内进行模型调用、工具调用和子 Agent 调度
    ↓
Turn 完成后 Thread 继续存在
    ↓
用户继续输入下一条请求
```

在一个 Turn 尚未结束时，用户还可以追加补充指令：

```text
当前 Turn 正在执行
    ↓
用户补充：“不要修改数据库”
    ↓
该输入追加到当前 Turn
    ↓
不创建新的 Turn
```

本轮改造的重点包括：

1. 将当前 Session / Run 混合模型修正为 `Thread → Turn → Item`；
2. 保留交互式 CLI 和持续上下文；
3. 使用 append-only canonical Item 保存历史；
4. 将 metadata 与完整历史分开保存；
5. 支持 Thread 创建、恢复、读取和关闭；
6. 支持 Turn 开始、追加指令、打断和完成；
7. 明确“持久化历史”和“模型本轮可见上下文”的区别；
8. 修复当前 Subagent Runtime 的生命周期、Follow-up、Trace、Budget 和授权问题；
9. 修复 DeepSeek 多轮 Tool Calling 协议；
10. 在完成这些修复后，再进入 Permission、Approval 和 Sandbox 阶段。

---

# 二、Codex 官方模型调研结论

本方案基于 Codex App Server 官方文档与 OpenAI Codex 开源仓库中的 Thread Store 和 Rollout Recorder 实现。

## 2.1 Thread

Thread 表示一个持续存在的 Codex conversation。

```text
Thread
    ├── Turn 1
    ├── Turn 2
    └── Turn 3
```

创建新会话使用：

```text
thread/start
```

恢复已有会话使用：

```text
thread/resume
```

恢复后，后续 `turn/start` 会继续向原 Thread 追加内容，而不是创建新的 Session。

Codex 还区分：

```text
thread.id
thread.sessionId
```

Root Thread 的：

```text
sessionId == thread.id
```

Fork 后的新 Thread 保留 Root Session ID，并具有新的 Thread ID。

### 本项目采用

第一版暂不实现 Fork，因此可以定义：

```text
session_id == thread_id
```

但代码和持久化协议中应保留：

```text
session_id
thread_id
parent_thread_id
forked_from_id
```

作为后续扩展字段。

---

## 2.2 Turn

Turn 表示：

> 一条用户请求，以及 Agent 为完成该请求所执行的全部工作。

例如：

```text
用户请求：
“分析登录接口为什么返回 500”
```

对应一个 Turn：

```text
Turn
├── userMessage
├── reasoning
├── search_text
├── read_file
├── commandExecution
├── fileChange
└── agentMessage
```

Turn 状态至少包括：

```text
inProgress
completed
failed
interrupted
```

当 Thread 当前没有活动 Turn 时：

```text
新用户输入
    ↓
创建新 Turn
```

当 Thread 已有活动 Turn 时：

```text
用户追加输入
    ↓
steer 当前 Turn
```

Codex 的 `turn/steer` 会向当前正在执行的 Turn 追加用户输入，不会产生新的 `turn/started`。

### 本项目采用

不再拆成：

```text
UserTurn
+
TurnExecution
```

两套对象。

统一使用：

```text
TurnState
```

TurnState 同时表示：

```text
用户本轮请求
+
本轮 Agent 执行状态
+
本轮预算
+
本轮最终结果
```

---

## 2.3 Item

Item 是 Turn 内的一项具体工作或记录。

第一版应支持：

```text
ThreadCreatedItem
TurnStartedItem
UserMessageItem
AgentMessageItem
ReasoningSummaryItem
ModelCallItem
ToolCallItem
ToolResultItem
SubagentSpawnItem
SubagentResultItem
TurnCompletedItem
TurnFailedItem
TurnInterruptedItem
ContextCompactionItem
```

后续阶段再增加：

```text
CommandExecutionItem
FileChangeItem
ApprovalRequestItem
ApprovalDecisionItem
MCPToolCallItem
SkillActivationItem
MemoryItem
```

所有 Item 使用统一生命周期：

```text
started
completed
failed
```

对流式输出，可以存在 Delta Event，但最终完成 Item 才是权威结果。

---

# 三、最重要的持久化修正

## 3.1 不要每轮保存完整 Session 快照

禁止使用下面的主要保存方式：

```json
{
  "session_id": "...",
  "all_messages": ["完整历史"],
  "all_tools": ["完整历史"],
  "all_turns": ["完整历史"],
  "all_agents": ["完整历史"]
}
```

并在用户每次输入后覆盖整个文件。

这样会造成：

- 历史越长，写入量越大；
- 每轮重复写入已有内容；
- 崩溃时容易产生半完整快照；
- 难以流式记录工具和模型事件；
- 难以审计某个状态如何产生；
- 难以支持 Turn 内追加输入和中断。

## 3.2 使用 append-only canonical history

正确方式：

```text
同一个 Thread
    ↓
持续追加 canonical Item
```

例如：

```json
{"type":"thread.created","thread_id":"thr_123", ...}
{"type":"turn.started","turn_id":"turn_001", ...}
{"type":"user_message","turn_id":"turn_001","text":"分析项目结构"}
{"type":"tool_call","turn_id":"turn_001","tool":"list_files", ...}
{"type":"tool_result","turn_id":"turn_001","tool_call_id":"call_1", ...}
{"type":"agent_message","turn_id":"turn_001","text":"项目分为……"}
{"type":"turn.completed","turn_id":"turn_001", ...}

{"type":"turn.started","turn_id":"turn_002", ...}
{"type":"user_message","turn_id":"turn_002","text":"继续看认证模块"}
...
```

记录一次用户输入是正确的。

但它表示：

```text
追加一条 UserMessageItem
```

而不是：

```text
重新保存整个 Thread
```

---

## 3.3 metadata 与 history 分离

推荐两类存储。

### Thread Metadata

保存便于查询的少量字段：

```text
thread_id
session_id
parent_thread_id
forked_from_id
workspace_root
name
preview
status
model_provider
model
created_at
updated_at
last_turn_id
turn_count
archived
```

### Canonical History

保存完整 Item 历史：

```text
user message
agent message
tool call
tool result
subagent event
compaction
错误
Turn 状态变化
```

本地第一版：

```text
metadata.json
rollout.jsonl
```

后续可增加 SQLite 作为 Metadata Index，但 JSONL 仍是 canonical history。

---

# 四、推荐目录结构

```text
.harness/
└── threads/
    └── <thread_id>/
        ├── metadata.json
        ├── rollout.jsonl
        ├── snapshots/
        │   └── latest.json
        └── agents/
            └── <child_thread_id>/
                ├── metadata.json
                └── rollout.jsonl
```

说明：

## metadata.json

保存可查询状态，不保存完整消息历史。

## rollout.jsonl

Thread 唯一 canonical history。

## snapshots/latest.json

可选派生缓存，用来加速恢复。

它不是事实来源，丢失后必须能根据 rollout 重建。

## agents/

如果 Child Agent 独立持久化为 Child Thread，可保存独立 Rollout。

第一版也可以先将 Child Item 追加到 Root Rollout，但必须保留：

```text
agent_id
child_thread_id
parent_thread_id
```

能够还原 Agent Tree。

---

# 五、目标领域模型

## 5.1 ThreadState

```python
@dataclass
class ThreadState:
    thread_id: str
    session_id: str
    workspace_root: Path
    status: ThreadStatus

    parent_thread_id: str | None
    forked_from_id: str | None

    main_agent_id: str
    active_turn_id: str | None
    child_thread_ids: list[str]

    created_at: datetime
    updated_at: datetime

    turn_count: int
    cumulative_usage: Usage
    metadata: dict
```

ThreadStatus：

```text
CREATED
IDLE
ACTIVE
CLOSING
CLOSED
FAILED
```

含义：

```text
IDLE：
Thread 已加载，但没有正在执行的 Turn。

ACTIVE：
存在正在执行的 Turn。

CLOSING：
正在取消和清理 Runtime 资源。

CLOSED：
当前运行实例已关闭，但持久化 Thread 仍可 resume。
```

注意：

```text
CLOSED 不等于删除历史。
```

---

## 5.2 TurnState

```python
@dataclass
class TurnState:
    turn_id: str
    thread_id: str
    status: TurnStatus

    initial_user_input: list[InputItem]
    steer_inputs: list[InputItem]

    started_at: datetime
    completed_at: datetime | None

    iteration: int
    model_call_count: int
    tool_call_count: int
    usage: Usage

    final_output: str | None
    error: RunError | None
```

TurnStatus：

```text
CREATED
IN_PROGRESS
COMPLETED
FAILED
INTERRUPTING
INTERRUPTED
```

每个新 Turn：

```text
iteration = 0
model_call_count = 0
tool_call_count = 0
usage = 0
```

Thread 的 cumulative usage 单独累计，不能用 Thread 累积计数作为 Turn Budget。

---

## 5.3 RolloutItem

统一基础结构：

```python
@dataclass
class RolloutItem:
    item_id: str
    item_type: str

    session_id: str
    thread_id: str
    turn_id: str | None

    agent_id: str | None
    parent_agent_id: str | None
    child_thread_id: str | None

    status: ItemStatus
    created_at: datetime
    completed_at: datetime | None

    payload: dict
```

ItemStatus：

```text
STARTED
COMPLETED
FAILED
INTERRUPTED
```

---

# 六、核心 Runtime 分层

推荐增加下列组件。

```text
ThreadManager
ThreadRuntime
TurnController
AgentLoop
ThreadStore
LiveThread
RolloutRecorder
MetadataStore
ContextBuilder
SubagentScheduler
```

---

## 6.1 ThreadManager

负责：

```text
start_thread
resume_thread
read_thread
list_threads
close_thread
archive_thread（后续）
fork_thread（后续）
```

不负责：

```text
Agent Loop
Tool 执行
Prompt 构建
```

---

## 6.2 ThreadRuntime

表示一个当前已加载的活动 Thread。

```python
@dataclass
class ThreadRuntime:
    state: ThreadState
    live_thread: LiveThread
    provider: ModelProvider
    main_agent_thread: AgentThreadState
    scheduler: SubagentScheduler
    tool_registry: ToolRegistry
    trace: TraceEmitter
    lock: asyncio.Lock
```

生命周期：

```text
thread/start 或 thread/resume
    ↓
创建 ThreadRuntime
    ↓
执行多个 Turn
    ↓
thread/close
    ↓
清理 Child Task
flush
shutdown writer
释放 Provider
```

---

## 6.3 TurnController

负责：

```text
start_turn
steer_turn
interrupt_turn
complete_turn
fail_turn
```

### start_turn

仅允许 Thread 当前无活动 Turn。

```text
Thread.IDLE
    ↓
创建 TurnState
    ↓
Thread.ACTIVE
    ↓
追加 turn.started
    ↓
追加 user_message
    ↓
启动 AgentLoop
```

### steer_turn

仅允许当前存在 `IN_PROGRESS` Turn。

```text
校验 expected_turn_id
    ↓
追加 user_message，input_kind=steer
    ↓
写入 Active Turn Mailbox
    ↓
不创建新 Turn
```

### interrupt_turn

```text
Turn.IN_PROGRESS
    ↓
Turn.INTERRUPTING
    ↓
取消 Model/Tool/Child Task
    ↓
等待清理
    ↓
Turn.INTERRUPTED
    ↓
Thread.IDLE
```

---

# 七、Turn 内追加输入的正确语义

当前项目需要区分两种输入。

## 7.1 Thread Idle

用户输入：

```text
“检查认证模块”
```

处理：

```text
创建新 Turn
```

## 7.2 Thread Active

用户输入：

```text
“先不要修改数据库”
```

处理：

```text
追加到当前 Turn
```

不能：

```text
创建第二个并发 Root Turn
```

也不应：

```text
等待当前 Turn 完成后默默变成下一 Turn
```

除非 UI 明确将其排队为“下一条请求”。

---

## 7.3 Main Agent Steer Mailbox

为活动 Root Turn 增加：

```python
class TurnInputMailbox:
    async def put(input_item)
    async def drain()
    async def has_pending()
```

AgentLoop 在安全边界消费：

```text
每次模型调用前
每批 Tool Result 写回后
接受最终结果前
```

若模型已经准备输出 Final，而 Mailbox 存在新输入：

```text
先消费 steer input
再继续模型调用
不能直接完成 Turn
```

这样可以避免用户补充要求丢失。

---

# 八、Thread Store 设计

## 8.1 抽象接口

```python
class ThreadStore(Protocol):
    async def create_thread(...)
    async def append_items(thread_id, items)
    async def update_metadata(thread_id, patch)
    async def load_history(thread_id)
    async def read_thread(thread_id, include_history)
    async def list_threads(...)
    async def persist_thread(thread_id)
    async def flush_thread(thread_id)
    async def shutdown_thread(thread_id)
```

Agent Runtime 不直接操作 JSONL 文件。

---

## 8.2 LocalThreadStore

负责：

```text
rollout.jsonl
metadata.json
可选 SQLite metadata index
```

## 8.3 LiveThread

LiveThread 是已加载 Thread 的持久化句柄。

```python
class LiveThread:
    async def append_items(items)
    async def update_metadata(patch)
    async def persist()
    async def flush()
    async def shutdown()
    async def load_history()
```

业务代码只依赖 LiveThread，不依赖本地文件实现。

---

# 九、Rollout Recorder

## 9.1 后台单 Writer

不要让 Root Agent、Child Agent 和 Tool Runtime 同时直接打开 JSONL 写文件。

使用：

```text
asyncio.Queue
+
单一 writer task
```

命令：

```text
AddItems
Persist
Flush
Shutdown
```

示意：

```python
class RolloutRecorder:
    queue: asyncio.Queue
    writer_task: asyncio.Task

    async def record(items):
        await queue.put(AddItems(items))

    async def persist():
        await queue.put(Persist(ack))

    async def flush():
        await queue.put(Flush(ack))

    async def shutdown():
        await queue.put(Shutdown(ack))
```

保证：

- Item 写入顺序稳定；
- 不同 Agent 不会写坏 JSONL；
- Agent Loop 不被同步文件 I/O 阻塞；
- Flush 可以等待全部已有 Item 落盘；
- Shutdown 可以安全退出。

---

## 9.2 延迟物化

创建 Thread 时可以：

```text
先创建内存状态
先计算持久化路径
暂不创建 rollout 文件
```

第一次需要写入有效 Item 时：

```text
persist()
```

再创建文件并写入 Thread Metadata 和 Pending Items。

空 Thread 不必产生无意义的文件。

---

## 9.3 Flush 与 Shutdown

### Flush

```text
保证调用 Flush 之前进入队列的所有 Item 已落盘
Writer 继续运行
```

### Shutdown

```text
Drain Queue
Flush
关闭文件
结束 Writer Task
```

关闭 ThreadRuntime 的顺序必须是：

```text
停止新 Turn
    ↓
interrupt / cancel active Turn
    ↓
shutdown Subagent Scheduler
    ↓
等待所有 Child Task
    ↓
append 最终 Item
    ↓
flush LiveThread
    ↓
shutdown LiveThread
    ↓
关闭 Provider
```

---

# 十、恢复 Thread

## 10.1 Resume 流程

```text
thread/resume(thread_id)
    ↓
读取 metadata
    ↓
读取 rollout.jsonl
    ↓
跳过损坏行并记录 parse error
    ↓
重建 ThreadState
    ↓
重建 Turn 历史
    ↓
恢复 Main Agent 历史
    ↓
加载 Context Compaction 结果
    ↓
Thread.IDLE
```

第一版只恢复已经完成或中断的 Turn。

进程崩溃时存在未完成 Turn：

```text
恢复时将其标记为 INTERRUPTED
追加 recovery interruption marker
```

不要假装继续运行原 Tool Call。

精确 Checkpoint Resume 留到后续阶段。

---

## 10.2 Read 与 Resume 分开

### read_thread

```text
读取历史和 metadata
不创建活动 Runtime
不启动 Provider
```

### resume_thread

```text
加载 ThreadRuntime
允许继续 start_turn
```

---

# 十一、Context Builder 与持久化历史的区别

必须明确：

```text
rollout history
≠
每次全部发送给模型的 context
```

Rollout 保存较完整历史。

模型上下文根据 Context Policy 构建：

```text
System Prompt
Project Guidance（后续）
最近有效 Compaction
最近 Turn
当前 Turn Items
相关 Tool Result
当前 Steer Input
```

不能为了节省 Token 修改或删除 canonical rollout。

---

## 11.1 Context Compaction

当前上下文接近限制时：

```text
创建 contextCompaction Item
    ↓
对旧历史生成摘要
    ↓
记录替代上下文
    ↓
下一轮模型使用摘要 + 最近 Item
```

Compaction 不能覆盖或删除原 Rollout。

它本质上是一个新 Item：

```text
replacement_history
summary
compacted_through_item
```

第一轮修复可以只设计接口，不必立即实现完整压缩算法。

但不得继续把“删除旧消息”作为上下文控制方法。

---

# 十二、Main Agent Loop 改造

现有 AgentLoop 应继续保留，但输入从单个复用 RunState 改为：

```python
AgentLoop.run_turn(
    thread_runtime,
    turn_state,
    agent_thread,
    completion_policy,
)
```

流程：

```text
检查 Turn 状态
    ↓
消费 Turn Steer Mailbox
    ↓
ContextBuilder 构建本轮上下文
    ↓
追加 model_call.started Item
    ↓
Provider 调用
    ↓
追加 model_call.completed Item
    ↓
如果 Tool Call：
    追加 tool_call.started
    执行
    追加 tool_result.completed
    回到循环
    ↓
如果 Final：
    再次检查 Mailbox
    无新输入才完成 Turn
```

---

# 十三、DeepSeek Provider 必须修复的问题

当前默认使用 DeepSeek V4 模型。

多轮 Thinking Tool Calling 需要保存并回传上一轮 assistant 的：

```text
reasoning_content
```

## 13.1 CanonicalMessage 增加字段

```python
reasoning_content: str | None = None
```

或者：

```python
provider_extensions: dict[str, Any]
```

推荐显式字段，因为它参与下一轮协议。

## 13.2 Parse

```text
Provider Response
    ↓
content
reasoning_content
tool_calls
```

全部保存。

## 13.3 Serialize

下一轮发送 assistant Tool Call Message 时：

```text
content
reasoning_content
tool_calls
```

一起回传。

## 13.4 测试

必须新增：

```text
DeepSeek Adapter reasoning_content round-trip
真实两轮 Tool Call Live Test
```

---

# 十四、Subagent 应如何接入 Thread 模型

## 14.1 Root 与 Child 形成 Thread Tree

```text
Root Thread
├── Child Thread A
├── Child Thread B
└── Child Thread C
```

Child Thread 保存：

```text
thread_id
session_id
parent_thread_id
agent_definition
history
turns
status
```

Root 与 Child 共享：

```text
session_id
workspace
Root Permission Upper Bound
Trace Root
```

但 Child 拥有：

```text
独立上下文
独立 Agent Definition
独立 Tool 白名单
独立 Local Budget
独立 Rollout Item 归属
```

---

## 14.2 Child 是否跨 Root Turn 保留

本项目可以选择保留 Idle Child Thread。

推荐第一版：

```text
Root Turn 完成：
取消所有 active Child Turn
保留 IDLE Child Thread Metadata 和历史

下一 Root Turn：
Main Agent 可以继续 send_followup 到原 Child Thread
```

若实现成本过高，可以暂时关闭 Child Thread，但必须明确这是产品策略，而不是 Session 模型限制。

---

# 十五、Subagent Runtime 现有问题修复

## 15.1 Cancel 必须等待 Task 真实退出

错误：

```text
task.cancel()
立即标记 CANCELLED
立即返回
```

正确：

```text
RUNNING
→ CANCELLING
→ task.cancel()
→ await task
→ 清理完成
→ CANCELLED
```

---

## 15.2 Close 状态机

允许：

```text
IDLE → CLOSED
CANCELLED → CLOSED
FAILED → CLOSED
RUNNING → CANCELLING → CANCELLED → CLOSED
```

禁止：

```text
CLOSED → CANCELLED
CLOSED → RUNNING
```

所有状态修改通过：

```python
transition_to()
```

不能在多个 finally 块中直接赋值。

---

## 15.3 Scheduler Shutdown

必须提供：

```python
async def shutdown():
    cancel every unfinished task
    await every task
    verify no orphan
```

不能只看 Agent 状态；必须检查实际 asyncio Task。

---

## 15.4 Follow-up

### Running Child

```text
只放入 Child Mailbox
```

### Idle Child

```text
不放 Mailbox
直接创建新 Child Turn
```

不能同时注入。

接受 Child `submit_result` 前检查 Mailbox：

```text
有 Follow-up
→ 消费并继续
无 Follow-up
→ 完成 Child Turn
```

---

## 15.5 Spawn 幂等

幂等键由 Runtime 自动生成：

```text
session_id
+
parent_thread_id
+
parent_turn_id
+
tool_call_id
```

从模型 Tool Schema 中删除：

```text
idempotency_key
```

模型不能决定幂等行为。

---

# 十六、Trace 与 Item 统一

当前 Trace 和持久化 Rollout 不应完全重复实现两套不一致的协议。

推荐：

```text
RolloutItem：
业务 canonical history

TraceEvent：
诊断和性能事件
```

两者关系：

```text
重要用户可恢复行为
→ RolloutItem

内部耗时、重试、锁等待、队列信息
→ TraceEvent
```

例如：

```text
Tool Call / Result：
Rollout + Trace

Provider Retry：
只进入 Trace

User Message：
Rollout

Budget Snapshot：
通常 Trace，Turn 完成时汇总进入 Rollout
```

每个模型、工具和 Child 事件都必须带：

```text
session_id
thread_id
turn_id
agent_id
parent_agent_id
item_id
```

---

# 十七、Tool Runtime 执行授权

当前 AgentDefinition 的 Tool 白名单不能只用于隐藏 Schema。

ToolRuntime 必须在执行时检查：

```python
@dataclass(frozen=True)
class ToolExecutionPrincipal:
    session_id: str
    thread_id: str
    turn_id: str
    agent_id: str
    allowed_tools: frozenset[str]
    capabilities: frozenset[str]
```

执行流程：

```text
查找 Tool
    ↓
检查 Tool Name 是否在 allowed_tools
    ↓
检查 required_capabilities
    ↓
校验参数
    ↓
执行
```

即使模型知道隐藏 Tool 的名字，也不能绕过授权。

这也是后续 Permission Engine 的接入口。

---

# 十八、Tool 参数校验

停止继续维护手写的浅层 JSON Schema 校验器。

改为：

```text
Pydantic Input Model
    ↓
model_validate
    ↓
validated arguments
    ↓
executor
```

导出 Provider Schema：

```text
Pydantic.model_json_schema()
```

需要覆盖：

```text
Literal / enum
minimum / maximum
字符串长度
数组 item
嵌套对象
额外字段拒绝
```

---

# 十九、Budget 模型

## 19.1 Turn Global Budget

每个 Root Turn 拥有：

```text
max_model_calls
max_tool_calls
max_tokens
max_wall_time
max_subagents
```

Root 和 Child 都从同一个 Turn Global Ledger 消费。

## 19.2 Agent Local Budget

Child 还有 Local Limit：

```text
local_model_calls
local_tool_calls
local_tokens
local_wall_time
```

有效限制：

```text
min(Local Limit, Turn Remaining Budget)
```

## 19.3 Thread Cumulative Usage

Thread 只累计展示：

```text
total_tokens
total_model_calls
total_tool_calls
total_duration
```

Thread 累计 Usage 不作为下一 Turn 的默认执行上限。

---

# 二十、文件工具异步修复

当前同步磁盘扫描放在 async 函数中会阻塞 Event Loop。

## read_file / list_files

使用：

```python
await asyncio.to_thread(...)
```

## search_text

优先：

```text
asyncio.create_subprocess_exec("rg", ...)
```

要求：

```text
不使用 shell=True
固定 cwd
参数数组
超时
输出上限
```

Python fallback 放入：

```python
asyncio.to_thread()
```

增加限制：

```text
max_scanned_files
max_scanned_bytes
max_file_size
max_results
```

---

# 二十一、Secret 与配置

不要在用户配置文件中明文保存 API Key。

配置文件只保存：

```toml
[provider]
api_key_env = "DEEPSEEK_API_KEY"
```

CLI setup 可以输出设置环境变量的方法，但不写入 Key。

后续再接系统 Keychain。

---

# 二十二、CLI 目标行为

## 新建 Thread

```bash
agent-harness code
```

无可恢复 Thread 时创建新 Thread。

## 恢复

```bash
agent-harness resume
agent-harness resume <thread_id>
```

## 查看

```bash
agent-harness threads
agent-harness inspect <thread_id>
```

## 交互命令

```text
/new
/resume
/status
/interrupt
/compact
/exit
```

### 用户在 Turn 执行时输入

如果 CLI 支持并发输入：

```text
发送 steer
```

如果当前 CLI 暂不支持边执行边输入，可以先保留内部 `steer_turn()` API，并在后续 UI 阶段接入。

---

# 二十三、推荐代码目录

```text
src/agent_harness/
├── threads/
│   ├── manager.py
│   ├── runtime.py
│   ├── live_thread.py
│   ├── store.py
│   ├── local_store.py
│   ├── metadata.py
│   ├── recorder.py
│   └── recovery.py
│
├── turns/
│   ├── controller.py
│   ├── state.py
│   ├── mailbox.py
│   └── budget.py
│
├── rollout/
│   ├── items.py
│   ├── codec.py
│   ├── replay.py
│   └── compaction.py
│
├── runtime/
│   ├── agent_loop.py
│   └── completion.py
│
├── runtime/subagents/
│   ├── scheduler.py
│   ├── thread.py
│   └── control_tools.py
│
├── tools/
├── providers/
├── tracing/
└── cli.py
```

不要求为了目录而机械拆分。

如果现有文件较小，可以先合并，但对象职责必须保持清晰。

---

# 二十四、迁移当前数据

当前已有：

```text
.harness/sessions/<session_id>/
    session.json
    transcript.jsonl
    events.jsonl
    turns/*-result.json
```

迁移策略：

## 24.1 旧 Session 映射为 Thread

```text
thread_id = old session_id
session_id = old session_id
```

## 24.2 transcript 转换

```text
user transcript
→ UserMessageItem

assistant transcript
→ AgentMessageItem
```

## 24.3 turns result

转换为：

```text
TurnCompletedItem
```

## 24.4 events

旧 events.jsonl 作为 legacy trace 保留，不强制全部转换为 canonical history。

提供一次性脚本：

```bash
agent-harness migrate-sessions
```

迁移失败不得删除旧目录。

---

# 二十五、实施顺序

## Step 1：建立 Thread / Turn / Item 领域模型

完成：

```text
ThreadState
TurnState
RolloutItem
状态转换
序列化
```

## Step 2：ThreadStore 与 RolloutRecorder

完成：

```text
LocalThreadStore
LiveThread
Queue Writer
persist
flush
shutdown
```

## Step 3：重构现有 ConversationSession

将其改为：

```text
ThreadRuntime
+
TurnController
```

删除跨 Turn 复用执行计数的问题。

## Step 4：AgentLoop 接收 TurnState

完成：

```text
Turn Budget
Turn Mailbox
Final 前检查 Steer
Rollout Item 追加
```

## Step 5：Thread Resume

完成：

```text
load history
replay state
read vs resume
```

## Step 6：DeepSeek reasoning_content

修复并增加 Live Test。

## Step 7：Subagent 生命周期

完成：

```text
cancel await
shutdown
close state machine
follow-up
idempotency
```

## Step 8：Trace 绑定

所有 Model / Tool / Child 事件关联：

```text
thread_id
turn_id
agent_id
item_id
```

## Step 9：Tool 授权与 Pydantic

完成 Runtime allowlist 和完整校验。

## Step 10：Budget 与异步 I/O

统一 Turn Global Budget，并修复文件工具阻塞。

## Step 11：CLI Resume 和迁移

完成：

```text
resume
threads
inspect
migrate-sessions
```

## Step 12：CI 和验收

完成测试、lint、类型检查。

---

# 二十六、必须新增的测试

## Thread

- [ ] 新建 Thread；
- [ ] Thread ID 稳定；
- [ ] 同一 Thread 连续执行三个 Turn；
- [ ] 每个 Turn 独立计数；
- [ ] Thread Cumulative Usage 累计；
- [ ] Thread Close 后可 Resume；
- [ ] Resume 本身不创建 Turn；
- [ ] Resume 后新 Turn 追加到原 Rollout。

## Turn

- [ ] Idle Thread 输入创建新 Turn；
- [ ] Active Thread 输入 Steer 当前 Turn；
- [ ] Steer 不产生新的 TurnStarted；
- [ ] expected_turn_id 错误时拒绝；
- [ ] Final 前有 Steer 时不能完成；
- [ ] Interrupt 最终为 INTERRUPTED；
- [ ] 一个 Turn 失败后 Thread 回到 IDLE；
- [ ] 下一 Turn 可继续运行。

## Persistence

- [ ] 每次只追加新 Item；
- [ ] 不覆盖已有 Rollout；
- [ ] 并发 Agent 写入顺序不损坏；
- [ ] Flush 等待已排队 Item；
- [ ] Shutdown 后 Writer Task 结束；
- [ ] 损坏一行 JSONL 时其余历史仍可读取；
- [ ] Snapshot 删除后仍可从 Rollout 恢复。

## Context

- [ ] Rollout 完整历史不等于 Provider Context；
- [ ] 最近 Turn 正确进入模型；
- [ ] Tool Call / Tool Result 配对不丢失；
- [ ] Compaction Item 可被 Context Builder 识别。

## DeepSeek

- [ ] reasoning_content 解析；
- [ ] reasoning_content 回传；
- [ ] 两轮 Tool Calling Live Smoke；
- [ ] Tool Call ID 保持一致。

## Subagent

- [ ] 两个带延迟 Child 真正并发；
- [ ] cancel 返回时 Task 已 done；
- [ ] shutdown 后无 orphan；
- [ ] close 状态不被 finally 覆盖；
- [ ] Running Follow-up 只注入一次；
- [ ] Idle Follow-up 只创建一次 Child Turn；
- [ ] 同一 Tool Call 重放不重复 Spawn；
- [ ] Child Model / Tool Item 具有 Child Thread ID。

## Tool Runtime

- [ ] 未授权 Tool 拒绝执行；
- [ ] 隐藏 Tool 名称不能绕过；
- [ ] Literal / enum 校验；
- [ ] 嵌套 Schema 校验；
- [ ] 异步 timeout 能实际终止受控子进程。

## Secret

- [ ] API Key 不写入 config；
- [ ] API Key 不进入 Rollout；
- [ ] API Key 不进入 Trace；
- [ ] Secret 文件读取继续阻断。

---

# 二十七、验收标准

完成以下条件后，才能进入阶段 3。

## 数据模型

- [ ] 核心模型为 `Thread → Turn → Item`；
- [ ] 不再混用 Session 与执行计数；
- [ ] 每个 Turn 独立 Budget；
- [ ] 同一 Thread 支持多个 Turn。

## 持久化

- [ ] canonical history append-only；
- [ ] metadata 与 history 分离；
- [ ] 后台单 Writer；
- [ ] Flush / Shutdown 语义可靠；
- [ ] Thread 可以恢复。

## 交互

- [ ] Idle 输入创建 Turn；
- [ ] Active 输入可以 Steer；
- [ ] Interrupt 正确；
- [ ] CLI Resume 正确。

## Agent Runtime

- [ ] DeepSeek 多轮 Tool Calling 真实通过；
- [ ] Agent Loop 不绑定本地 Store；
- [ ] Model / Tool / Child 全部生成可恢复 Item；
- [ ] Context Builder 不直接等于完整 Rollout。

## Subagent

- [ ] 无 orphan task；
- [ ] Cancel / Close 无竞态；
- [ ] Follow-up 不重复、不丢失；
- [ ] Spawn 系统级幂等；
- [ ] Child Trace / Item 可准确归属。

## 执行边界

- [ ] Tool allowlist 执行时强制检查；
- [ ] Pydantic 完整校验；
- [ ] Turn Global Budget 包含 Root 和 Child；
- [ ] 文件工具不阻塞 Event Loop。

## 工程质量

- [ ] 所有旧测试通过；
- [ ] 新增测试通过；
- [ ] GitHub Actions 通过；
- [ ] Ruff 通过；
- [ ] 类型检查通过；
- [ ] README 更新为 Codex 式 Thread 模型。

---

# 二十八、明确禁止的改造

Codex 实施时不得：

```text
1. 删除交互式会话，退回一次性任务程序；
2. 每次用户输入创建一个新 Thread；
3. 每次用户输入覆盖保存完整 Thread 快照；
4. 将 rollout history 与模型 context 混为一体；
5. 用 LangGraph、Agents SDK Runner、CrewAI 或 AutoGen 替代现有 Runtime；
6. 复制新的 Child Agent Loop；
7. 改成 A2A 或 Handoff；
8. 只修改文档而不增加测试；
9. 提前加入完整 Memory、Skill、MCP 和 Sandbox；
10. 为了兼容旧代码而继续复用跨 Turn 的 iteration / model_call_count。
```

---

# 二十九、给 Codex 的执行要求

Codex 正式修改前，先输出：

```text
1. 当前实现与目标 Thread/Turn/Item 模型的差异；
2. 需要修改和新增的文件列表；
3. 状态迁移图；
4. Rollout Item Schema；
5. ThreadStore 接口；
6. Recorder 的并发与 shutdown 设计；
7. Session 旧数据迁移方案；
8. 测试清单；
9. 预计保留、重构和删除的现有代码；
10. 与本文档不同的设计及理由。
```

得到确认后再编码。

实施过程中：

```text
每完成一个 Step：
运行该 Step 的单元测试
运行阶段 1 / 2 回归测试
记录实现差异
```

不能一次性进行大范围不可审查重写。

---

# 三十、官方参考资料

## Codex App Server

- Thread、Turn、Item、`thread/start`、`thread/resume`、`turn/start`、`turn/steer`、`turn/interrupt`、`thread/compact/start`  
  https://developers.openai.com/codex/app-server

## Codex CLI

- CLI 会话恢复与开发命令  
  https://developers.openai.com/codex/cli/reference

## OpenAI Codex 开源仓库

- Thread Store  
  https://github.com/openai/codex/blob/main/codex-rs/thread-store/README.md

- LiveThread  
  https://github.com/openai/codex/blob/main/codex-rs/thread-store/src/live_thread.rs

- Rollout Recorder  
  https://github.com/openai/codex/blob/main/codex-rs/rollout/src/recorder.rs

---

# 三十一、最终结论

项目最终应采用：

```text
Thread
    ├── Turn
    │    └── Item
    ├── Turn
    │    └── Item
    └── Child Thread
         └── Child Turn
              └── Item
```

其中：

```text
Thread：
持续交互会话。

Turn：
一条用户请求及 Agent 的完整工作。

Item：
消息、模型调用、工具、子 Agent、错误和 Compaction 等具体工作单元。
```

持久化采用：

```text
metadata
+
append-only rollout
+
可重建 snapshot
```

用户每次输入应该被记录，但含义是：

```text
向当前 Thread 追加 UserMessageItem
```

不是：

```text
重新保存完整 Session
```

当没有活动 Turn 时：

```text
创建新 Turn
```

当存在活动 Turn 时：

```text
Steer 当前 Turn
```

这套模型既符合 Codex 的交互体验，也适合当前项目后续继续扩展：

```text
Permission
Approval
Sandbox
Skill
MCP
Memory
Checkpoint
Web UI
```
