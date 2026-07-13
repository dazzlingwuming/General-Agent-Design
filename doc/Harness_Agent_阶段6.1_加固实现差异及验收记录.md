# Harness Agent 阶段 6.1 加固实现差异及验收记录

> 实施基线：`60c397d4a9a96ff99d5b38232b0ae6c52adc34e7`
> 实施日期：2026-07-12
> 对照文档：`Harness_Agent_阶段6.1_进入阶段7前加固审计.md`

## 1. 架构依据

本次没有引入新的工作流框架，而是把成熟实现的恢复原则映射到现有 `AgentLoop + ConversationSession + CheckpointStore + RolloutRecorder`：

- OpenAI Agents SDK 将待审批 Tool Call 暴露为可序列化 interruption，决定写回原 RunState 后继续原顶层 Run，而不是创建新 Turn：<https://openai.github.io/openai-agents-python/human_in_the_loop/>。
- LangGraph durable execution 在恢复时复用已完成 Task 的持久结果；未完成副作用必须具备幂等性或使用幂等键：<https://langchain-ai.github.io/langgraph/how-tos/review-tool-calls-functional/>。
- SQLite WAL 只解决并发与日志持久化基础，不自动解决跨 SQLite/JSONL 两个存储的原子提交，因此本项目仍需要显式终态 reconciliation：<https://www.sqlite.org/wal.html>。

## 2. 已关闭的 P0 问题

### ResumePoint 恢复闭环

- `AFTER_MODEL`、`WAITING_APPROVAL`、`BEFORE_TOOL`、`AFTER_TOOL` 统一复用最后一条 durable assistant message。
- 已存在 Tool message 的 call ID 会被跳过；同一 ModelResponse 的剩余 Tool Call 按原顺序继续。
- 恢复当前 iteration 时不重复增加 iteration 计数。
- `BEFORE_FINALIZE` 直接恢复终态并写 `TERMINAL` Checkpoint，不调用 Provider 或 Tool。

### Pending Approval

- `WAITING_APPROVAL` 不再被 Session 转成 `RECOVERY_REQUIRED`。
- 恢复时重新进入原 Tool Call；stable approval ID 保持不变。
- 已提交决定由 `CheckpointStore.approval_decision()` 复用；仍 pending 时由原 ApprovalHandler 再次展示。
- `WAITING_SUBAGENT`、`PAUSED`、`RECOVERY_REQUIRED` 仍明确要求人工处理，不会自动继续。

### Terminal 崩溃窗口

- `TERMINAL` Checkpoint 存在而 rollout 缺少 terminal item 时，Session 会从 Checkpoint 恢复 RunState，并幂等补写唯一 terminal item。
- rollout 已有 terminal item 时只修复 metadata 为 `IDLE` 且清空 active turn。
- 恢复取消态时保留原结构化错误，不重新生成取消原因。

上述唯一性保证适用于当前单 CLI、单 Session 的顺序恢复模型。项目尚未实现同一 Thread 的跨进程 durable lease；若两个进程同时执行 resume，检查与 rollout append 之间仍可能竞争。该并发 exactly-once 问题需要与 Transactional Outbox/执行租约一起解决，不能描述为本次已完成。

## 3. 已关闭的 P1 问题

### 完整 RunState

Checkpoint 反序列化现恢复：

- `agent_name`；
- `started_at`、`updated_at`、`completed_at`；
- 结构化 `RunError`；
- `agent_summary`。

因此 wall-time budget 不会因进程重启而重置。

### Compaction 跨进程应用

- 新增 `CompactionService.latest(thread_id)` 与 `apply_latest(state)`。
- 应用前验证 source hash、summary hash 和 protected message IDs。
- Checkpoint 已包含同一 summary 时幂等返回，不重复压缩。
- 任一校验不一致时 fail closed，不把无法证明来源的摘要注入模型上下文。

### Memory 删除

删除操作现在在同一 SQLite 事务内：

- 将列和 `payload_json.content` 都替换为 `[DELETED]`；
- 清空 `structured_data`、source item/artifact IDs 和 tags；
- 删除 `memory_sources` 与 FTS 行；
- 保留 content hash tombstone、删除时间和删除原因。

### Rollout 完整性

- v1 item 只允许出现在首个 v2 item 之前；v2 chain 开始后出现 v1 会 fail closed。
- corrupt-tail 使用时间戳加 UUID 的排他文件名，不覆盖旧审计证据。
- quarantine 和 canonical truncate 后均执行 flush + fsync。

## 4. 与审计文档建议不完全相同之处

1. 审计文档把 `WAITING_APPROVAL` 的 disposition 描述为 `WAIT_APPROVAL`。本项目 Session 原本只执行 `CONTINUE/RETRY`，因此实际实现将它规划为 `CONTINUE`，但语义仍是恢复原 durable Tool Call 后等待或复用审批，不代表绕过审批。
2. 审计建议将缺失 rollout terminal 的 `TERMINAL` Checkpoint “按 BEFORE_FINALIZE 修复”。实际实现直接在 Session projection 层补 terminal item，不再次进入 AgentLoop。这样避免生成第二个 `TERMINAL` Checkpoint，且更贴合问题属于 DB 到 rollout projection 的事实。
3. 没有采用文档所称的“补丁包完整替换文件”。当前仓库中未发现独立补丁文件，本次按现有代码逐处修改，保留阶段 1-5 的 Permission、MCP、Skill 和 Thread 行为。
4. Compaction 仍是确定性文本摘要，不是模型摘要；本次只修复其跨进程一致性与完整性验证，没有扩大阶段 6 范围。

## 5. 仍未完成且继续延期

以下事项没有因本次加固而变成已完成：

- Subagent durable child execution 与跨进程恢复；
- 文件 Tool pre/post hash 自动 reconciliation；
- `recover --mark-tool-*`、retry、abandon 等人工核对命令；
- Transactional Outbox 全面接管 rollout projection；
- 同一 Thread 的跨进程 durable execution lease 与并发 resume 排他；
- Checkpoint retention 自动清理；
- Memory 自动提取、冲突、supersede 和 dependency invalidation；
- 完整 agent/tool/permission/sandbox/guidance/skill/MCP compatibility hash；
- 所有故障点的真实 `os._exit` 矩阵；
- DeepSeek Live、真实 MCP/OAuth、Linux/WSL sandbox 的本轮回归。
- 审批前真实进程终止、重启后由真人确认同一 Approval ID 的交互演示；自动测试已覆盖恢复规划和 stable identity 的底层路径，但本轮未模拟真人终端输入。

这些能力不得在阶段 7 UI 或验收说明中展示为已经完成。

## 6. 新增专项验收

新增 `tests/unit/test_phase6_recovery_hardening.py`，覆盖：

1. RunState 时间戳、错误和 summary 完整恢复；
2. `AFTER_TOOL` 多 Tool Call 只继续剩余调用；
3. `BEFORE_FINALIZE` 的 Provider/Tool 调用次数均为 0；
4. `TERMINAL` Checkpoint 缺 terminal item 时自动补写且 metadata 修复；
5. Compaction 重启后验证并重新应用；
6. Memory 删除后 SQL payload 不存在原文和 tags；
7. v2 chain 后插入 v1 item 必须失败。

最终门禁结果应以本次实际命令输出为准，不以设计文档中的预期数字代替。
