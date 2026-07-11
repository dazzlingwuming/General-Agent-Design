# Harness Agent Session 与 Run 设计修正记录

> 文档日期：2026-07-11  
> 修正范围：CLI 交互模式、Run/Session/Turn 边界、Trace 保存策略  
> 参考对象：Codex CLI / Codex App Server 的 session、thread、turn 思路  

---

## 1. 问题背景

原阶段 1 / 阶段 2 实现把一次用户输入当作一个 `Run`：

```text
用户输入一句
→ RunManager.run()
→ 新建 run_id
→ 写入 .harness/runs/<run_id>/
→ 输出后退出 CLI
```

这导致两个问题：

1. CLI 只能对话一次，无法像 Codex 一样持续交互；
2. 每输入一次就生成一个顶层 run 目录，`.harness/runs` 很快堆积大量记录。

用户实际想要的产品形态不是“一次性任务执行器”，而是：

```text
进入一个目录
→ 启动 agent-harness
→ 在同一个终端会话里连续对话
→ 必要时再开启新会话或恢复旧会话
```

---

## 2. 借鉴 Codex 后的边界修正

Codex 相关资料显示，其交互形态更接近：

```text
Session / Thread
  ├── Turn 1
  ├── Turn 2
  ├── Turn 3
  └── Subagent Threads
```

而不是：

```text
Run 1
Run 2
Run 3
```

本项目因此把交互模式修正为：

```text
Session = 一次持续对话的顶层边界
Turn    = 用户在 Session 中输入的一轮
RunState = 当前仍复用的运行状态对象，后续应重命名或拆分
Trace   = Session 级 events.jsonl
```

---

## 3. 当前实现变更

新增：

- `runtime/session.py`
  - `ConversationSession`
  - session metadata
  - transcript append
  - per-turn result 文件

修改：

- `RunManager`
  - 新增 `run_existing(state, trace_root)`
  - 支持在同一个 `RunState` 上执行多轮 turn

- `ContextBuilder`
  - 增加临时 `recent_turns` 策略
  - 默认只取最近 3 个用户 turn 及其后续消息
  - 支持 `session_summary` 占位，但尚未实现自动 compact

- `JsonlTraceSink`
  - 追加同一个 trace 文件时读取最后的 `sequence_number`
  - 避免多 turn session 中 sequence 从 1 重新开始

- `CLI`
  - 裸 `agent-harness` 进入交互 session
  - `agent-harness code` 进入交互 session
  - `agent-harness code --task "..."` 保留一次性执行
  - `agent-harness run --task "..."` 保留一次性执行
  - 交互命令支持：
    - `/exit`
    - `/quit`
    - `/status`
    - `/new`

---

## 4. 新的目录策略

一次性执行仍写入：

```text
.harness/runs/<run_id>/
```

交互式对话写入：

```text
.harness/sessions/<session_id>/
├── session.json
├── transcript.jsonl
├── events.jsonl
├── result.json
├── turns/
│   ├── turn_0001-result.json
│   └── turn_0002-result.json
└── agents/
    └── <agent_id>/
        └── turn-0001-result.json
```

因此，正常交互时不会再出现“每说一句就生成一个顶层 run 目录”的问题。

---

## 5. 测试污染修正

之前部分测试直接使用默认 `HarnessConfig()`，会把测试 run 写到项目目录：

```text
agent-harness/.harness/runs/
```

现在测试层新增自动 fixture，把测试工作目录隔离到 `tmp_path`，避免继续污染真实项目目录。

---

## 6. 当前仍未完成的地方

本次只解决两个核心问题：

1. runs 顶层目录过多；
2. CLI 只能对话一次。

上下文管理只是临时方案，仍未完成：

- 没有真正的自动 `/compact`；
- 没有模型生成的 session summary；
- 没有 transcript 恢复到内存的完整 `resume`；
- 没有 session list / delete / archive；
- 没有把 `RunState` 正式拆分为 `SessionState` 和 `TurnState`；
- Trace 仍未完整达到阶段 2 要求的顶层 `agent_id/thread_id/turn_id/depth` 字段；
- Trace 仍未实现文档要求的 async lock。

这些需要在后续阶段继续完善。

---

## 7. 验证记录

执行：

```bash
python -m pytest
```

结果：

```text
36 passed, 2 skipped
```

新增关键测试：

```text
tests/integration/test_interactive_session.py
```

该测试确认：

- 两轮用户输入复用同一个 `session_id`；
- 只生成一个 session 目录；
- 同一个 session 下生成多个 turn result；
- `events.jsonl` 的 `sequence_number` 连续且不重复。

