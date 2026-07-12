# Harness Agent 阶段 6 实现差异及验收记录

> 实施基线：`d8e019f3a7cacd4882f25c28355acc92070172b5`  
> 实施日期：2026-07-12  
> 对照文档：`Harness_Agent_阶段6_Memory_Checkpoint与恢复详细设计.md`

## 1. 本次实际完成

### Durable Persistence

- 新增独立 `CheckpointStore`，使用 SQLite WAL、`foreign_keys=ON`、`synchronous=FULL`、`busy_timeout=5000` 和短事务。
- 新增 schema migration、checkpoint、logical action、pending approval、recovery record 和 transactional outbox 基础表。
- `AgentLoop` 在 `BEFORE_MODEL`、`MODEL_IN_FLIGHT`、`AFTER_MODEL`、`BEFORE_TOOL`、`TOOL_IN_FLIGHT`、`AFTER_TOOL`、`BEFORE_FINALIZE`、`TERMINAL` 写 Checkpoint。
- Checkpoint 只保存 JSON 数据，不保存 Provider、MCP Session、Task、Lock、Queue、函数或文件句柄。
- `AFTER_MODEL` 恢复会复用已提交 assistant message/tool calls，不重复请求 Provider。
- 恢复不再自动把未完成 Turn 改写为 `turn.interrupted`。

### Rollout 完整性

- RolloutItem v2 新增 `schema_version`、`sequence_number`、`previous_hash` 和 `item_hash`。
- Recorder 每批写入后执行 flush 和 `fsync`。
- 最后一条损坏记录会隔离到 `rollout.jsonl.corrupt-tail` 并截断；中间损坏、序号错误和 hash chain 错误会 fail closed。
- 兼容读取旧 v1 rollout；新写入从 v2 chain 开始。

### Approval 与 Tool Policy

- Approval ID 改为由 thread、turn、agent、tool call 和 argument hash 稳定派生。
- Approval 请求和决定写入 runtime DB；恢复后同一审批对象复用已提交决定。
- 每个内置 Tool 均有 `ToolEffectClass` 与 `ToolRecoveryPolicy`。
- 读取 Tool 为 `READ_ONLY + RETRY_SAFE`；`write_file/apply_patch` 为 `RECONCILABLE_WRITE + VERIFY_THEN_SYNTHESIZE`；`delete_path` 为 `NON_IDEMPOTENT_WRITE + MANUAL_RECONCILIATION`；`run_command` 为 `NON_IDEMPOTENT_WRITE + NEVER_RETRY`。

### Memory

- `memory.sqlite3` 与 `runtime.sqlite3` 完全分离。
- Memory 包含 Scope、Source、Evidence、Verification、Confidence、Trust、失效和删除字段。
- Project Identity v1 使用本地 resolved workspace path hash，不跨 checkout 串库。
- 支持显式写入、FTS5/LIKE fallback 检索、上下文预算注入、失效、软删除和 tombstone。
- Memory 注入明确标为 non-authoritative，不能影响 Permission、Approval、Sandbox、Trust 或 Guidance。
- Secret-like 内容拒绝写入；MCP External 不能直接升级为 Verified。

### Compaction

- 仅在 completed/idle 边界和阈值达到时执行。
- Compaction 只改变 model-visible state，保留 canonical rollout。
- Tool messages 和带 tool calls 的 assistant message作为 protected content 保留。
- Compaction record 单独写入 runtime DB。

### CLI

- 新增 `agent-harness recover <thread_id> --status`。
- 新增 `agent-harness memory add|list|search|invalidate|delete`。
- `agent-harness resume` 检测到安全的非终态 Checkpoint 时，会先继续原 Turn，再接收新 Turn。

## 2. 与设计文档不一致或尚未完成

以下内容没有完成，不能按设计文档描述为已经具备：

1. **Subagent 跨进程恢复未完成。** 当前 Child 仍由进程内 `SubagentScheduler` 管理，尚无 durable child execution、stable budget reservation 和 child result reuse。Root/Child 未覆盖完整故障注入矩阵。
2. **文件 Tool 自动 reconciliation 未完成。** Tool 已声明恢复策略，但 `write_file` 和 `apply_patch` 尚未把 pre/post hash、expected postcondition 持久化到 logical action journal，因此 `TOOL_IN_FLIGHT` 仍会保守进入人工核对，不能自动合成成功结果。
3. **人工核对 CLI 不完整。** 尚未实现 `--mark-tool-succeeded`、`--mark-tool-failed`、`--retry-tool` 和 `--abandon-turn`；当前 `recover --status` 只提供只读恢复计划。
4. **Transactional Outbox 尚未接管全部 Rollout 写入。** 表、唯一约束和 projection API 已实现，但现有 `TurnController` 事件仍主要直接写 Recorder；尚未做到所有 durable transition 与 rollout intent 同事务提交。
5. **Checkpoint retention 未执行。** 配置项已解析，但尚未自动清理每 Turn 的旧 checkpoint。
6. **Compaction 首版使用确定性文本截取。** 没有调用独立 summarizer provider，也没有 summary artifact fallback；它满足不删除历史和失败隔离边界，但摘要质量低于设计目标。
7. **Memory 自动提取未实现。** `auto_extract` 默认关闭；当前只支持用户显式写入，未实现 Tool/Test Evidence candidate 自动写入、conflict/supersede 和 dependency invalidation。
8. **完整 compatibility hash 未实现。** Checkpoint 当前保存 config/provider/model 和 payload hash，但尚未保存 agent definition、tool catalog、permission、sandbox、guidance、skill 和 MCP catalog 的全部兼容性 hash。
9. **故障注入覆盖不完整。** 已有真实子进程 `os._exit` 后读取 Checkpoint 的测试，但尚未覆盖文档列出的每个 model/tool/approval/compaction/subagent fault point。

## 3. 设计调整说明

- 文档建议一次性新增完整 Durable Runtime；实际项目的 `ConversationSession`、`RunManager` 和 `AgentLoop` 耦合较深。本次采用增量接入，保留既有 ToolRuntime、Permission、Skill、MCP 和 Recorder，不引入 LangGraph、Temporal 或 Agents SDK。
- 文档要求任何中间损坏不得静默跳过，因此旧测试“损坏行不阻止恢复”已改为：仅末尾损坏可隔离修复，中间损坏必须抛出完整性错误。
- 文档要求崩溃不等于 interrupted，因此旧测试“resume 自动写一个 turn.interrupted”已改为保持原 Turn 非终态。

## 4. 已执行验收

```text
ruff check src tests: passed
mypy src: passed
pytest -m "unit or integration_local": 127 passed, 1 skipped, 3 deselected
pytest -m recovery_process: 1 passed, 130 deselected
```

`1 skipped` 未计为通过。Linux/WSL sandbox、DeepSeek Live、真实 MCP/OAuth 本次没有重新执行，不能描述为本次阶段 6 验收通过。

## 5. 当前准确能力边界

当前系统提供本地单机 Checkpoint、部分安全边界续跑、持久审批决定、Rollout 完整性校验、显式 Project Memory 和 idle-only compaction。它不提供任意外部副作用 exactly-once，不会自动重放未知命令/MCP 写入，也尚未提供完整 Child 恢复和文件副作用自动核对。
