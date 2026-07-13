# General Agent Harness：阶段 6 进入阶段 7 前加固审计

> 审计日期：2026-07-12
> 审计提交：`60c397d4a9a96ff99d5b38232b0ae6c52adc34e7`
> 对照文档：`doc/Harness_Agent_阶段6_Memory_Checkpoint与恢复详细设计.md`
> 结论：阶段 6 第一轮实现已经建立了正确基础，但当前不能直接把“精确恢复”视为完整完成。进入阶段 7 前应先关闭本文件列出的 P0/P1 问题。

---

## 一、总体判断

第一轮实现方向正确，已经具备：

- Checkpoint 与 Memory 分离；
- SQLite WAL Runtime Store；
- Rollout v2 sequence/hash chain；
- stable Approval ID 和决定持久化；
- Tool EffectClass / RecoveryPolicy；
- Project Memory；
- Idle-only Compaction 基础；
- hard process exit 后 Checkpoint 存活测试。

这些能力足以作为阶段 6 基础，但还存在恢复状态机未闭环的问题。当前最主要的风险不是“缺少更多 Memory 功能”，而是部分 Checkpoint 虽然被写入，恢复后却没有按照对应逻辑边界继续。

---

## 二、进入阶段 7 前必须关闭的问题

### P0：多个 ResumePoint 会错误地重新调用模型

当前 `AgentLoop` 只有 `AFTER_MODEL` 被特殊处理为复用已经提交的 assistant message。以下边界仍会进入普通模型请求路径：

- `WAITING_APPROVAL`
- `BEFORE_TOOL`
- `AFTER_TOOL`
- `BEFORE_FINALIZE`

影响：

1. Pending Approval 恢复时，可能重新请求模型，而不是重新展示原 Tool Call；
2. Approval 已决定、Tool 尚未执行时，可能重新生成不同 Tool Call；
3. 同一 ModelResponse 有多个 Tool Call，首个 Tool 完成后崩溃，恢复可能跳过剩余 Tool；
4. 最终输出已经决定但 TurnController 尚未落盘时，恢复可能再次调用 Provider。

必须改为：

```text
AFTER_MODEL / WAITING_APPROVAL / BEFORE_TOOL / AFTER_TOOL
    → 复用最后一条 durable assistant message
    → 根据已经存在的 tool message 跳过已完成 Tool
    → 继续剩余 Tool

BEFORE_FINALIZE
    → 直接恢复终态
    → 不调用 Provider
    → 不调用 Tool
```

### P0：WAITING_APPROVAL 在 Session 层被错误转成 RECOVERY_REQUIRED

`RecoveryCoordinator` 会为 `WAITING_APPROVAL` 返回 `WAIT_APPROVAL`，但 `ConversationSession.resume()` 只接受 `CONTINUE` 和 `RETRY`。

结果：

```text
审批请求已经持久化
    ↓
进程重启
    ↓
Session 不会重新展示原审批
    ↓
Thread 被标记为 RECOVERY_REQUIRED
```

这与“审批状态持久化并可恢复”的产品描述不一致。

第一版无需引入复杂 UI，只要让 `WAITING_APPROVAL` 恢复原 assistant Tool Call，并由现有 `ToolRuntime` 使用 stable approval identity 重新请求或复用已经提交的 Decision。

### P0：TERMINAL Checkpoint 与 Rollout Terminal Item 之间存在崩溃窗口

当前顺序是：

```text
AgentLoop 写 TERMINAL Checkpoint
    ↓
返回 ConversationSession
    ↓
TurnController 写 agent_message + turn.completed/failed/cancelled
    ↓
更新 metadata
```

如果进程在第一步和第二步之间退出：

- Runtime DB 表示 Turn 已终态；
- Rollout 中没有 Turn Terminal Item；
- Metadata 可能仍为 ACTIVE；
- 当前 resume 会忽略 TERMINAL Checkpoint，不进行修复。

必须增加：

```text
TERMINAL Checkpoint + Rollout 无 terminal item
    → 将其按 BEFORE_FINALIZE 修复
    → 幂等写入唯一 terminal item

TERMINAL Checkpoint + Rollout 已有 terminal item
    → 只修复 metadata
```

### P1：Checkpoint 反序列化没有恢复完整 RunState

当前恢复遗漏：

- `agent_name`
- `started_at`
- `updated_at`
- `completed_at`
- `error`
- `agent_summary`

最严重的是 `started_at`。Wall-time budget 使用 RunState 的开始时间；重启后重新创建时间会让执行预算被重置。

必须恢复原始时间戳和结构化错误，避免恢复改变预算和终态语义。

### P1：Compaction 记录没有在进程重启后重新应用

当前 Compaction：

- 将 summary 写入数据库；
- 只修改当前进程中的 `state.session_summary` 和 `state.messages`。

但 `resume()` 没有读取最新 CompactionRecord。重启后会恢复完整 Checkpoint 消息，Compaction 实际失效。

必须增加：

```text
CompactionService.latest(thread_id)
CompactionService.apply_latest(state)
```

并验证：

- source hash；
- protected message IDs；
- summary hash。

### P1：Memory soft delete 仍在 payload_json 中保留明文

当前 `delete()` 只更新：

```text
memory_records.content = "[DELETED]"
```

但 `payload_json` 仍包含：

- 原始 content；
- structured_data；
- source references；
- tags。

因此数据库中仍能恢复用户要求删除的内容，且与“hash-only tombstone”描述不符。

必须在同一事务中：

- 将 `payload_json.content` 改为 `[DELETED]`；
- 清空 `structured_data`；
- 清空 source item/artifact IDs；
- 清空 tags；
- 删除 `memory_sources`；
- 删除 FTS 行；
- 只保留 hash tombstone。

### P1：Rollout 允许在 v2 Hash Chain 后插入 v1 Item

当前完整性读取器遇到 `schema_version == 1` 会直接接受，不检查 v2 chain 是否已经开始。

攻击或损坏场景：

```text
v2 item
v2 item
插入一个 v1 item
v2 item
```

插入的 v1 Item 不属于 hash chain，却可能被读取为 canonical history。

必须规定：

```text
v1 只能出现在第一个 v2 Item 之前
v2 chain 开始后再出现 v1 → fail closed
```

同时建议：

- corrupt-tail 文件名防覆盖；
- 修复截断后执行 flush + fsync。

---

## 三、可以继续延期到阶段 7 之后的问题

以下能力重要，但不阻塞阶段 7 的“评测与界面”设计：

### 可延期 A：Subagent 完整跨进程恢复

阶段 7 可以先把它作为明确的未完成指标，评测：

- Child 中断率；
- Root 重新委派次数；
- orphan cleanup；
- recovery-required 比例。

但不能在 UI 中展示为已完成能力。

### 可延期 B：文件 Tool 自动 pre/post hash reconciliation

在人工核对命令完成前，继续保持：

```text
TOOL_IN_FLIGHT + write/delete/command
    → fail closed / manual
```

这比错误自动重放更安全。

### 可延期 C：Memory 自动提取、冲突和 supersede

阶段 7 反而可以先通过评测数据决定：

- 哪类事实值得自动提取；
- 污染率；
- 错误召回率；
- 过期率；
- 用户删除率。

不应为了“功能完整”提前开放宽松自动写入。

### 可延期 D：高质量模型 Compaction

当前确定性截取质量有限，但只要恢复可重放且不删除 canonical rollout，就可以在阶段 7 中建立压缩质量评测后再升级 summarizer。

### 可延期 E：Outbox 全面接管所有 Rollout

这是架构完整性工作。阶段 7 开始前至少应明确 UI 中哪些事件来自 Runtime DB、哪些来自 Rollout，并避免将二者误认为已经强一致。

---

## 四、建议新增的专项测试

至少增加：

1. `WAITING_APPROVAL` 重启后重新展示同一 Approval ID；
2. Approval 已决定、Tool 前退出，不再次调用 Provider；
3. `AFTER_TOOL` 多 Tool Call 恢复，剩余 Tool 继续；
4. `BEFORE_FINALIZE` 恢复 Provider 调用次数为 0；
5. TERMINAL Checkpoint 缺少 Rollout terminal item 时自动修复；
6. `started_at` 跨 Checkpoint 保持不变；
7. Compaction 重启后重新应用；
8. Memory delete 后数据库中不存在原明文；
9. v2 chain 后插入 v1 Item 必须失败；
10. `WAITING_SUBAGENT`、`PAUSED`、`RECOVERY_REQUIRED` 不得自动继续。

当前已有的 `os._exit` 测试只证明 SQLite Checkpoint 存活，不能证明 AgentLoop 按正确边界继续。

---

## 五、补丁包内容

本补丁包基于提交：

```text
60c397d4a9a96ff99d5b38232b0ae6c52adc34e7
```

包含以下完整替换文件：

```text
agent-harness/src/agent_harness/recovery/coordinator.py
agent-harness/src/agent_harness/checkpoints/serializer.py
agent-harness/src/agent_harness/compaction/service.py
agent-harness/src/agent_harness/rollout/integrity.py
agent-harness/src/agent_harness/memory/store.py
agent-harness/src/agent_harness/runtime/agent_loop.py
agent-harness/src/agent_harness/runtime/session.py
```

新增：

```text
agent-harness/tests/unit/test_phase6_recovery_hardening.py
```

补丁目标：

- 收紧恢复状态机；
- 修复 Pending Approval 恢复；
- 修复 Terminal 崩溃窗口；
- 恢复完整 RunState；
- 让 Compaction 跨进程生效；
- 真正清理 Memory plaintext；
- 收紧 Rollout v1/v2 完整性规则。

---

## 六、应用后门禁

```bash
cd agent-harness

uv sync --locked --extra test

uv run --no-sync python -m ruff check src tests
uv run --no-sync python -m mypy src
uv run --no-sync python -m pytest -m "unit or integration_local" -q
uv run --no-sync python -m pytest -m recovery_process -q
git diff --check
```

还应重新执行至少一个真实交互演示：

```text
模型产生需要审批的 Tool Call
    ↓
审批前终止进程
    ↓
agent-harness resume <thread_id>
    ↓
确认显示同一 Tool Call 和同一 Approval ID
```

---

## 七、阶段 7 准入建议

关闭上述 P0/P1 后，可以开始阶段 7。

阶段 7 应优先评测：

- Task success rate；
- Tool success/error/timeout；
- Approval request/allow/deny/cancel；
- Recovery automatic/manual/blocked；
- Provider replay count；
- Tool duplicate execution count；
- Memory hit/usefulness/pollution/staleness；
- Compaction compression ratio and answer regression；
- Subagent benefit and failure isolation；
- Token、延迟和成本；
- Safety block 和 false positive；
- Rollout integrity failures。

界面第一版应围绕：

```text
Thread List
Turn Timeline
Item / Trace Detail
Approval Queue
Recovery Status
Memory Inspector
Artifact Viewer
Evaluation Runs
```

而不是先做普通聊天网页。
