# Harness Agent：Codex 式 Thread / Turn / Item 修复实现差异与验收记录

> 日期：2026-07-11  
> 对照文档：`Harness_Agent_Codex式Thread_Turn_Item修复完善方案.md`  
> 本记录用途：说明本轮实际实现、验收结果，以及与方案不同或尚未完成的地方。

## 1. 外部参考核对

已通过 OpenAI 官方开发者页面核对核心概念：

- Codex App Server 的核心原语是 `Thread -> Turn -> Item`；
- Thread 表示一个 conversation；
- Turn 表示一条用户请求及 Agent 后续工作；
- Item 表示用户消息、Agent 消息、工具调用、文件变化等输入输出单元；
- Codex 非交互 JSONL 事件包含 `thread.started`、`turn.started`、`turn.completed`、`turn.failed`、`item.*` 等类型。

本地 `openai-docs` helper 尝试获取 Codex manual 时，当前机器 DNS 无法解析 `developers.openai.com`，因此最终使用浏览器搜索到的官方 OpenAI 页面核对，不使用第三方资料作为设计依据。

## 2. 本轮已完成

### 2.1 Thread / Turn / Item 领域模型

新增：

- `src/agent_harness/rollout/items.py`
  - `RolloutItem`
  - `ItemStatus`
  - `item_from_dict`
- `src/agent_harness/turns/state.py`
  - `ThreadState`
  - `TurnState`
  - `InputItem`
  - `ThreadStatus`
  - `TurnStatus`
- `src/agent_harness/turns/mailbox.py`
  - `TurnInputMailbox`

说明：

- 当前主交互路径已经使用 Thread ID 作为稳定 conversation ID；
- 第一版仍保持 `session_id == thread_id`，预留 `parent_thread_id`、`forked_from_id`；
- 每个 Turn 返回前会重置 turn-local `iteration`、`model_call_count`、`tool_call_count`，不再跨 Turn 复用执行计数。

### 2.2 Thread Store 与 append-only rollout

新增：

- `src/agent_harness/threads/local_store.py`
- `src/agent_harness/threads/live_thread.py`
- `src/agent_harness/threads/recorder.py`
- `src/agent_harness/threads/store.py`

当前目录结构：

```text
.harness/
  threads/
    <thread_id>/
      metadata.json
      rollout.jsonl
      events.jsonl
      result.json
      turns/
        turn_0001-result.json
```

实现情况：

- `metadata.json` 只保存索引字段，不保存完整历史；
- `rollout.jsonl` 是 canonical history；
- 每轮只追加新增 `RolloutItem`，不覆盖历史；
- `RolloutRecorder` 使用 `asyncio.Queue + 单 writer task`；
- `flush()` 会等待已排队 Item 落盘；
- `shutdown()` 会 drain 并结束 writer task；
- resume 时会跳过损坏 JSONL 行；
- resume 本身不会创建新 Turn。

### 2.3 CLI 行为

新增/调整：

- `agent-harness`：默认进入交互式 Thread；
- `agent-harness code`：无 task 时进入交互式 Thread；
- `agent-harness resume [thread_id]`：恢复最近或指定 Thread；
- `agent-harness threads`：列出 Thread；
- `agent-harness inspect --thread <thread_id>`：查看 Thread metadata 和 rollout item 数量；
- `agent-harness migrate-sessions`：将旧 `.harness/sessions` 转为 `.harness/threads`；
- `agent-harness sessions`：隐藏兼容别名，内部转到 Thread 列表。

### 2.4 DeepSeek reasoning_content

已完成：

- `CanonicalMessage.reasoning_content`
- DeepSeek response parse 时保存 `reasoning_content`
- 下一轮 assistant tool-call message serialize 时回传 `reasoning_content`
- 新增单元测试验证 round-trip。

### 2.5 Tool Runtime 执行边界

已完成：

- 新增 `ToolExecutionPrincipal`
- ToolRuntime 执行时检查：
  - `allowed_tools`
  - `required_capabilities`
- AgentLoop 调用工具时传入 principal；
- 隐藏工具名不能绕过执行层授权；
- 当前默认能力为 `FILE_READ`。

### 2.6 Tool 参数校验增强

已完成内置 JSON Schema 子集增强：

- `enum`
- `minimum`
- `maximum`
- `minLength`
- `maxLength`
- array `items`
- nested object
- `additionalProperties = false`

### 2.7 文件工具异步修复

已完成：

- `read_file` 使用 `asyncio.to_thread`
- `list_files` 使用 `asyncio.to_thread`
- `search_text` 的 Python fallback 使用 `asyncio.to_thread`

### 2.8 Secret 配置修复

已完成：

- `agent-harness setup` 不再把 API Key 写入 config；
- config 只保存 `api_key_env = "DEEPSEEK_API_KEY"`；
- 旧 config 中如果已有 `api_key` 字段，仍兼容读取。

## 3. 与方案不一致或暂未完成

### 3.1 没有完全拆出 ThreadRuntime / TurnController

方案要求：

```text
ThreadManager
ThreadRuntime
TurnController
LiveThread
ThreadStore
```

当前实际：

- 已有 `LocalThreadStore`、`LiveThread`、`RolloutRecorder`；
- 旧 `ConversationSession` 被改造成兼容包装层；
- 还没有独立 `ThreadRuntime` 和 `TurnController` 文件。

理由：

- 为了避免一次性重写 AgentLoop、SubagentScheduler、CLI 全链路，本轮选择先让主交互路径落到正确持久化模型；
- 后续可以把 `ConversationSession.run_turn()` 中的 Turn 状态迁移逻辑抽到 `TurnController`。

### 3.2 CLI 暂不支持执行中并发输入 steer

方案要求：

```text
Turn Active 时用户输入 -> steer 当前 Turn
```

当前实际：

- 已有 `TurnInputMailbox`；
- Subagent follow-up 已有 mailbox 风格；
- CLI 当前仍是 `await run_turn()` 完成后再读取下一条输入；
- 因此终端用户无法在模型执行中即时输入 steer。

原因：

- 需要改造 CLI 输入循环为并发任务，同时处理 Windows PowerShell 输入阻塞、Ctrl+C、provider/tool cancellation；
- 本轮未引入该交互复杂度。

### 3.3 Rollout Item 尚未覆盖模型/工具每个细节事件

方案要求：

```text
ModelCallItem
ToolCallItem
ToolResultItem
SubagentSpawnItem
SubagentResultItem
```

当前实际：

- Rollout 已记录：
  - `thread.created`
  - `turn.started`
  - `user_message`
  - `agent_message`
  - `turn.completed`
  - `turn.failed`
  - recovery `turn.interrupted`
- 模型调用、工具调用、Subagent 详细事件仍主要在 `events.jsonl` trace 中。

理由：

- AgentLoop 和 Trace 目前耦合较深；
- 要避免同时大改 AgentLoop、SubagentScheduler 和 ToolRuntime；
- 下一步应把 `RolloutRecorder` 注入 AgentLoop，在模型/工具边界写 canonical Item。

### 3.4 Child Thread 尚未独立持久化

方案要求：

```text
Root Thread
  Child Thread A
  Child Thread B
```

当前实际：

- Subagent 仍以 Phase 2 的 Scheduler 状态和 trace/result 文件保存；
- 尚未为每个 child 建立 `.harness/threads/<child_thread_id>`。

### 3.5 Pydantic Tool Input Model 尚未完成

方案要求：

```text
Pydantic Input Model -> model_validate
```

当前实际：

- 未新增 Pydantic 依赖；
- 使用项目内置 JSON Schema 子集校验器；
- 已覆盖方案要求的大部分参数约束，但不是 Pydantic。

理由：

- 当前 `pyproject.toml` 生产依赖只有 `httpx`；
- 直接引入 Pydantic 会扩大依赖面，需要单独设计 ToolDefinition 与 schema 导出方式。

### 3.6 Budget 仍未完整升级为 Turn Global Ledger

当前实际：

- Root Turn 本轮计数已重置；
- Phase 2 child budget manager 仍存在；
- 尚未把 Root + Child 统一成完整 Turn Global Ledger。

### 3.7 Context Compaction 仅保留设计入口

当前实际：

- 没有删除 canonical rollout；
- ContextBuilder 仍按 recent turns/messages 构建模型上下文；
- 尚未实现 `ContextCompactionItem` 和压缩算法。

### 3.8 真实 DeepSeek 两轮 Tool Calling Live Test 未默认执行

当前实际：

- 已新增 reasoning_content 单元测试；
- 真实 DeepSeek smoke 仍放在 live 测试/人工验证路径；
- 默认 CI 不会调用真实 API。

原因：

- 默认测试不能依赖用户 API Key 和外部网络。

## 4. 新增/更新测试

新增或更新：

- `tests/integration/test_interactive_session.py`
  - 同一 Thread 连续两个 Turn；
  - rollout append-only；
  - metadata/history 分离；
  - resume 不创建 Turn；
  - 损坏 JSONL 行可跳过；
- `tests/unit/test_deepseek_provider.py`
  - `reasoning_content` parse；
  - `reasoning_content` serialize；
- `tests/unit/test_tool_runtime.py`
  - 未授权工具拒绝；
  - 缺能力拒绝；
  - enum/range/array/nested object 校验；
- `tests/integration/test_cli.py`
  - setup 不写 API Key 明文。

## 5. 当前验收状态

已通过：

```text
python -m pytest tests\unit\test_tool_runtime.py tests\unit\test_deepseek_provider.py tests\integration\test_interactive_session.py tests\integration\test_demo_repository_task.py
```

结果：

```text
12 passed
```

全量测试已运行：

```text
python -m pytest
```

结果：

```text
52 passed, 2 skipped
```

## 6. 下一步建议

优先顺序：

1. 抽出 `ThreadRuntime` 和 `TurnController`，让 `ConversationSession` 只做 CLI 适配；
2. AgentLoop 注入 `LiveThread`，补齐 `model_call`、`tool_call`、`tool_result` RolloutItem；
3. CLI 输入循环改为并发读取，支持 active Turn steer；
4. Child Agent 建立独立 child thread rollout；
5. 引入 Pydantic Tool Input Model；
6. 实现 Turn Global Budget Ledger；
7. 增加 Context Compaction Item 和恢复逻辑。
