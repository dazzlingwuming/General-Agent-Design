# Harness Agent 阶段 2 实现差异与验收记录

> 文档日期：2026-07-11  
> 对照文档：`Harness_Agent_阶段2_Subagent_Runtime详细设计.md`  
> 实现目录：`agent-harness/`  
> 记录目的：说明阶段 2 当前实现、验收结果，以及与详细设计不完全一致的地方。

---

## 1. 当前实现概览

阶段 2 已在阶段 1 基础上实现中心化 Subagent Runtime。当前用户仍只面对 Main Agent，子 Agent 通过 root-only control tools 被创建、等待、追加指令、取消和关闭。

已实现的主要模块：

- `domain/subagents.py`：Agent Thread、Agent Turn、Delegation Request、Subagent Result；
- `agents/registry.py`：静态 Agent Registry、内置 root/child 角色、定义校验；
- `agents/loader.py`：从 `agents/*.toml` 加载 AgentDefinition；
- `agents/outputs.py`：结构化输出 schema registry；
- `runtime/completion.py`：Root 文本完成策略与 Child 结构化完成策略；
- `runtime/subagents/scheduler.py`：Run-scoped Subagent Scheduler；
- `runtime/subagents/control_tools.py`：Root Agent 可见的 Subagent 控制工具；
- `runtime/budgets.py`：Global / Local child budget 预留、释放和统计；
- `tools/internal/submit_result.py`：Child Agent 结构化终止工具和本地结构校验；
- `tracing/jsonl.py`：并发安全 JSONL trace，带全局单调 `sequence_number`；
- `providers/fake.py`：支持按 agent、turn、model call 分脚本，支持延迟和异常注入。

Root Agent 可见工具：

```text
list_files
read_file
search_text
spawn_subagent
wait_subagents
get_subagent_status
send_subagent_message
cancel_subagent
close_subagent
```

Child Agent 可见工具：

```text
list_files
read_file
search_text
submit_result
```

内置 Child Agent：

```text
explorer
reviewer
test_analyst
```

---

## 2. 已完成的阶段 2 验收项

### 2.1 功能验收

- Main Agent 能通过 `spawn_subagent` 创建 Child；
- Child 使用同一套 `AgentLoop`，没有复制第二套循环；
- Child 拥有独立消息历史，不复制父完整历史；
- Child 使用 AgentDefinition 工具白名单；
- Child 默认没有 Subagent Control Tools，不能继续创建下一层 Child；
- `spawn_subagent` 非阻塞返回 handle；
- 多个 Child 可以并行运行；
- 并发限制、总数量限制、深度限制生效；
- Main Agent 可以 `wait_subagents` wait all / wait any；
- wait timeout 不取消 Child；
- Main Agent 可以 `get_subagent_status`；
- Main Agent 可以向 running / idle Child 发送 follow-up；
- idle Child follow-up 会复用同一 thread 创建新 turn；
- Main Agent 可以 `cancel_subagent`；
- Main Agent 可以 `close_subagent`；
- Child 通过 `submit_result` 返回结构化结果；
- `submit_result` 会做本地 schema 和字段结构校验；
- Child 普通失败不会取消兄弟 Child，也不会自动让 Root 失败；
- Root 结束前会取消仍活动的 Child；
- Root Completion Guard 会阻止 Root 在活动 Child 存在时最终回答；
- Root Run 结束前不存在 orphan Child。

### 2.2 架构验收

- 只有一套 AgentLoop；
- Root 和 Child 通过 `CompletionPolicy` 区分完成语义；
- Agent Registry 不依赖 Provider SDK；
- Scheduler 不直接构建 Provider 请求，仍复用 AgentLoop / ContextBuilder；
- Tool Runtime 仍是工具执行入口；
- Control Tool 只能调用 Scheduler，不能绕过 Scheduler；
- Root Run / Session 是顶层边界，Child 不创建用户级 Run；
- Global / Local child budget 已分离；
- Child 不能通过请求扩大工具和预算；
- 未引入 A2A、Handoff 或第三方多 Agent 框架。

### 2.3 上下文与结果验收

- Child 默认 fresh context，只接收 Delegation Packet；
- Delegation Packet 明确区分 task、explicit context、expected focus；
- Child Result 返回 summary、evidence、unresolved_questions、confidence、structured_data、result_ref；
- Root 只看到精炼结果，不接收 Child 完整消息历史；
- Follow-up 只进入目标 thread；
- 两个 Child 不共享消息对象；
- Main Agent 上下文不会因为 Child 搜索日志直接膨胀。

### 2.4 并发、取消与 Trace 验收

- Scheduler 使用 run-scoped lifecycle 管理所有 child tasks；
- 普通 Child 异常被转换为 failed 状态；
- `CancelledError` 不吞掉，会在 child task 中继续传播；
- Root 结束时统一清理 active child；
- Semaphore 控制并发，并记录 `max_concurrent_observed`；
- Wait 使用 `asyncio.Condition`，不是高频轮询；
- JSONL trace 使用锁保护 sequence 分配和写入；
- Trace 事件带 `agent_id`、`thread_id`、`parent_agent_id`、`depth` 等顶层字段；
- `result.json` 增加 `agent_summary`，包含 agent_tree、成功/失败/取消统计、child usage 和预算摘要。

---

## 3. 与详细设计不完全一致的地方

### 3.1 目录拆分比设计文档更收敛

设计文档建议新增：

```text
runtime/subagents/supervisor.py
runtime/subagents/thread.py
runtime/subagents/mailbox.py
runtime/subagents/context_packet.py
context/delegation.py
```

实际实现：

- 当前仍集中在 `runtime/subagents/scheduler.py` 和 `domain/subagents.py`；
- mailbox 使用 `AgentThreadState.mailbox` 字段实现；
- delegation packet 在 scheduler 构造 child RunState 时生成；
- 没有拆出单独 Supervisor 类。

原因：

- 当前阶段只有进程内 root-scoped scheduler；
- 拆更多文件不会改变运行语义，反而增加过早抽象；
- 后续如果需要持久恢复、UI 面板或跨进程调度，再拆 Supervisor / Mailbox 更合适。

影响：

- 功能语义满足阶段 2；
- 文件结构与推荐目录不完全一致。

### 3.2 使用 `threading.Lock` 而不是 async lock 保护 trace

设计文档写的是“异步锁”。

实际实现：

- `JsonlTraceSink.emit()` 是同步方法；
- 因此使用 `threading.Lock` 包住 sequence 分配和文件写入。

原因：

- 现有 trace API 是同步调用；
- 在同步方法里引入 `asyncio.Lock` 会强迫所有调用方改成 await；
- 当前所有并发 child task 仍运行在同一进程，`threading.Lock` 能满足“全局单调 sequence + JSONL 不损坏”的验收目标。

影响：

- 并发安全目标满足；
- 锁类型与文档措辞不同。

### 3.3 Agent TOML Loader 已实现，但默认仍使用内置 AgentDefinition

设计文档推荐默认目录：

```text
agents/main.toml
agents/explorer.toml
agents/reviewer.toml
agents/test-analyst.toml
```

实际实现：

- 已提供 `load_registry_from_toml()` 和 `load_agent_definitions()`；
- 当前 CLI 默认仍使用 `create_default_agent_registry()` 内置角色；
- 没有强制要求用户项目必须存在 `agents/*.toml`。

原因：

- 保持开箱即用；
- 避免用户没有 agent 配置文件时 CLI 无法运行。

影响：

- TOML 能测试和扩展；
- 默认体验仍是内置角色。

### 3.4 Budget 以调用次数为主，还没有真实 token 预留

设计文档提到 child token、usage 和 local budget。

实际实现：

- 当前 Global / Local 预算基于 `RunLimits.max_model_calls` 和 `max_tool_calls`；
- Provider 返回 usage 时仍会在 RunState 中累计；
- 没有在 spawn 阶段按 token 做预留。

原因：

- DeepSeek / OpenAI 兼容接口的 token usage 只有请求完成后才可靠；
- 阶段 2 的调度风险主要是模型调用数、工具调用数和并发数量失控；
- token 预算可以在后续 Context/Cost 阶段完善。

影响：

- 阶段 2 的预算隔离已可核对；
- 成本级 token 预算仍是后续增强项。

### 3.5 真实 DeepSeek Demo 不作为默认测试执行

当前验证：

- Fake Provider 多 Agent 测试覆盖了三个 child demo、失败隔离、timeout、follow-up、trace sequence；
- 额外手动执行过一次真实 DeepSeek child smoke；
- DeepSeek live test 仍默认 skipped。

原因：

- live test 需要真实 API key 和网络；
- 真实模型是否主动 spawn child 受提示词和任务影响，不适合作为默认 CI 断言；
- 默认测试应可重复、无成本、无网络依赖。

影响：

- 阶段 2 runtime 语义已通过 Fake Provider 验证；
- 真实 Provider 的 child AgentLoop / tool call / submit_result 路径已做 smoke；
- 真实 Root 自动三子 Agent 委派仍建议作为人工 demo，而不是默认自动测试。

---

## 4. 验证记录

执行目录：

```text
D:\APP_self\General Agent Design\agent-harness
```

执行命令：

```bash
python -m pytest
```

结果：

```text
45 passed, 2 skipped
```

新增或强化的关键测试：

- `tests/unit/test_agent_registry_phase2.py`
- `tests/unit/test_subagent_scheduler.py`
- `tests/integration/test_subagent_runtime.py`
- `tests/unit/test_config.py`

覆盖内容：

- Agent Registry 正常加载和非法配置拒绝；
- TOML AgentDefinition loader；
- Root spawn / wait / submit_result；
- 三个 Child 的阶段 2 demo；
- Child 结构化输出修正；
- Idle thread follow-up 复用；
- Wait timeout 不取消 child；
- Child failure isolation；
- Trace sequence 全局单调且不重复；
- Subagent config 从 TOML 读取；
- 阶段 1 全量回归。

两个 skipped：

- DeepSeek live test 默认跳过；
- Windows symlink 权限相关测试跳过。

真实 DeepSeek smoke：

```text
run_id: live_phase2_smoke
provider: deepseek
model: deepseek-v4-flash
workspace: tests/fixtures/demo_repo
child: explorer
status: succeeded
summary: 真实模型确认 calculator/pricing.py 中存在 calculate_total
trace: .harness/live/live_phase2_smoke/events.jsonl
result_ref: .harness/live/live_phase2_smoke/agents/<agent_id>/turn-0001-result.json
```

---

## 5. 当前结论

阶段 2 的核心目标已经完成：

```text
Root Agent
  -> spawn_subagent
  -> managed Child Agent Thread
  -> isolated context + allowed tools
  -> submit_result
  -> wait / follow-up / cancel / close
  -> parent-child trace + result summary
```

仍未进入阶段 2 明确排除的范围：

- 跨进程恢复；
- 长期 Memory；
- Skill / MCP；
- HITL Approval；
- Docker Sandbox；
- 写文件 / shell / apply patch 工具；
- 多层子 Agent；
- A2A 或 Handoff；
- 后台 Agent 脱离 Root Run。

因此，当前实现可以作为阶段 2 完成版继续进入后续阶段；后续如果要提升真实 Codex-like 体验，优先做 session resume、上下文压缩、真实 provider live demo 和更完整的 token/cost 预算。
