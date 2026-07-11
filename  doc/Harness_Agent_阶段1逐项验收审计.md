# Harness Agent 阶段 1 逐项验收审计

> 文档日期：2026-07-11  
> 对照文档：`Harness_Agent_阶段1单Agent详细设计.md` 第 21 节  
> 当前实现目录：`agent-harness/`  
> 审计目的：逐条核对阶段 1 验收标准，明确实现证据、测试证据和剩余边界。

---

## 1. 本次补充后的验证结果

普通测试：

```text
python -m pytest
35 passed, 2 skipped
```

真实接口测试：

```text
python -m pytest -m live
1 passed, 36 deselected
```

本次为阶段 1 验收新增的直接测试：

```text
tests/unit/test_phase1_acceptance.py
tests/integration/test_cli.py::test_cli_run_accepts_task_and_workspace
```

这些测试补上了之前没有明确证明的部分：

- 多轮模型循环；
- Tool Result 进入下一轮模型上下文；
- Tool Schema 暴露给模型；
- Provider Error；
- Provider 空响应；
- Run ID 唯一；
- 模型调用预算；
- iteration 预算；
- Runtime cancellation；
- Trace sequence；
- result.json；
- CLI `run --workspace --task`。

---

## 2. 21.1 功能验收

| 验收项 | 状态 | 证据 |
|---|---|---|
| 可通过 CLI 提交任务 | 已满足 | `test_cli_run_accepts_task_and_workspace`，`agent-harness run --workspace ... --task ...` |
| 可指定 Workspace | 已满足 | `test_cli_run_accepts_task_and_workspace` |
| 能创建唯一 Run ID | 已满足 | `test_phase1_unique_run_ids` |
| 能调用 DeepSeek | 已满足 | `python -m pytest -m live` 通过；之前 CLI `v4-flash` / `v4-pro` 均真实运行完成 |
| 能使用 Fake Provider | 已满足 | 多个 unit/integration tests 使用 `FakeModelProvider` |
| 能向模型暴露 Tool Schema | 已满足 | `InspectingProvider` 在 `test_phase1_multi_turn_loop_and_tool_results_enter_context` 中断言 schema 包含 `list_files`、`read_file`、`search_text` |
| 能解析单个 Tool Call | 已满足 | `test_agent_loop_tool_then_final` |
| 能解析多个 Tool Call | 已满足 | `test_agent_loop_multiple_tool_calls_same_turn` |
| 能执行至少三个只读工具 | 已满足 | `list_files`、`read_file`、`search_text` 已实现；demo 和验收测试覆盖 |
| 能将 Tool Result 正确写回模型 | 已满足 | `test_phase1_multi_turn_loop_and_tool_results_enter_context` 断言下一轮 request 中存在 `tool_call_id` |
| 能进行多轮循环 | 已满足 | `test_phase1_multi_turn_loop_and_tool_results_enter_context` 覆盖 3 次 model call、2 次 tool call |
| 能获得最终答案 | 已满足 | 多个测试断言 `final_output` |
| 能正确结束 Run | 已满足 | 多个测试断言 `RunStatus.COMPLETED` |
| 能处理 Tool Error | 已满足 | `test_agent_loop_unknown_tool_is_feedback_not_crash` |
| 能处理 Provider Error | 已满足 | `test_phase1_provider_error_fails_run` |
| 能处理取消 | 已满足（Runtime 层） | `test_phase1_cancellation_state` 断言 `cancellation_requested` 转成 `CANCELLED`；CLI Ctrl+C 是基础捕获，未做自动化端到端测试 |
| 能限制模型和工具调用次数 | 已满足 | `test_phase1_model_call_budget`、`test_agent_loop_max_tool_calls` |
| 能输出 JSONL Trace | 已满足 | `test_phase1_trace_sequence_and_result_json` |
| 能输出 result.json | 已满足 | `test_phase1_trace_sequence_and_result_json` |

结论：21.1 功能验收在当前代码中已满足。唯一需要说明的是“取消”目前有 Runtime 层验证，CLI Ctrl+C 端到端自动化测试尚未补。

---

## 3. 21.2 架构验收

| 验收项 | 状态 | 证据 |
|---|---|---|
| Agent Loop 不依赖 DeepSeek SDK 对象 | 已满足 | `runtime/agent_loop.py` 只依赖 `ModelProvider` 协议和 canonical model |
| Provider Adapter 不执行 Tool | 已满足 | `providers/deepseek.py` 只转换请求/响应，不调用 `ToolRuntime` |
| Tool 不直接修改 Run State | 已满足 | built-in tools 只返回数据；RunState 只在 AgentLoop / RunManager 更新 |
| Context Builder 不执行副作用 | 已满足 | `context/builder.py` 只构造 `ModelRequest` |
| CLI 不包含 Agent Loop | 已满足 | `cli.py` 调用 `RunManager` |
| Tool Schema 来自统一定义 | 已满足 | `ToolDefinition.to_model_schema()` 和 `ToolRegistry.export_schemas()` |
| Tool Call ID 全程保持关联 | 已满足 | `CanonicalMessage(tool_call_id=...)`；`test_phase1_multi_turn_loop_and_tool_results_enter_context` 断言 |
| Provider 可替换 | 已满足 | Fake / DeepSeek / 测试自定义 Provider 均可插入 RunManager |
| 后续 Subagent 可作为 Tool 接入 | 已满足 | 阶段 2 已实际通过 control tools 接入，证明扩展点成立 |
| 未使用 LangGraph、Agents SDK Runner、CrewAI 或 AutoGen | 已满足 | 项目依赖和源码未引入这些框架 |

结论：21.2 架构验收已满足。

---

## 4. 21.3 安全验收

| 验收项 | 状态 | 证据 |
|---|---|---|
| Tool 只能访问 Workspace | 已满足 | `utils/paths.py::resolve_workspace_path`；`test_path_policy.py` |
| 阻止 `..` 路径逃逸 | 已满足 | `test_rejects_parent_escape` |
| 阻止符号链接逃逸 | 已满足（代码实现，环境测试可能跳过） | `resolve(strict=True)` 后校验父目录；`test_rejects_symlink_escape` 在无 symlink 权限时跳过 |
| 默认阻止常见 Secret 文件 | 已满足 | `ensure_not_secret`；`test_read_file_blocks_secret_file` |
| 没有通用 shell | 已满足 | 无 `run_command`；`search_text` 当前使用 Python fallback，不调用 shell |
| 没有文件写入 | 已满足（工具层） | 对模型暴露的工具均只读；运行产物 trace/result 属于 Harness 自身输出 |
| Tool 参数均执行本地 Schema 校验 | 已满足 | `ToolRuntime._validate_json_schema`；`test_tool_runtime_validates_required_argument` |
| Trace 不记录 API Key | 已满足 | Provider key 不进入 request metadata；`test_deepseek_provider_repr_does_not_expose_api_key`；密钥模式扫描未命中真实 key |
| README 明确阶段 1 的安全限制 | 已满足 | README “安全限制”章节 |

结论：21.3 安全验收已满足。注意：阶段 1 仍不是 Sandbox，只是应用层只读边界。

---

## 5. 21.4 测试验收

| 验收项 | 状态 | 证据 |
|---|---|---|
| 所有 Unit Tests 通过 | 已满足 | `python -m pytest` 中 unit 全通过 |
| Integration Tests 通过 | 已满足 | `python -m pytest` 中 integration 全通过 |
| Live Test 默认跳过 | 已满足 | 普通 pytest 中 live test skipped |
| Fake Provider 能覆盖完整 Agent Loop | 已满足 | `test_phase1_multi_turn_loop_and_tool_results_enter_context` 和 demo integration |
| 关键错误分支有测试 | 已满足 | Tool error、Provider error、empty response、budget、cancellation、timeout 等已覆盖 |
| CI 或本地命令可一键运行测试 | 已满足 | `python -m pytest` |
| Demo Run 有可审计 Trace | 已满足 | integration run 和 CLI run 生成 `.harness/runs/<run_id>/events.jsonl` 和 `result.json` |

结论：21.4 测试验收已满足。

---

## 6. 需要诚实说明的边界

1. 当前代码已经叠加了阶段 2 Subagent Runtime，所以它不是“纯阶段 1”代码状态；但阶段 1 的验收项均已有测试或实现证据。
2. CLI Ctrl+C 的端到端自动化测试还没有做；当前取消测试验证的是 Runtime cancellation 状态机。
3. 没有覆盖率百分比报告；阶段 1 文档第 21 节没有把 coverage 作为硬性验收项，但第 19 节有建议覆盖率目标。
4. `search_text` 当前没有优先用 `rg`，而是 Python fallback；这和阶段 1 详细设计里的建议不同，已在差异文档记录。
5. 当前工具层没有写文件能力；trace/result 写文件是 Harness 运行产物，不是模型可调用的文件写入工具。

---

## 7. 当前结论

对照 `Harness_Agent_阶段1单Agent详细设计.md` 第 21 节，阶段 1 验收标准当前已经满足。

本次补充前，你质疑的点是合理的：之前确实缺少逐项验收证据，尤其是多轮循环、Tool Result 反馈、Provider Error、取消、Trace 顺序等。现在这些都有了可执行测试。

