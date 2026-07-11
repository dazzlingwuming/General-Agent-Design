# Harness Agent 阶段 1 实现差异与验收记录

> 文档日期：2026-07-11  
> 对照文档：`Harness_Agent_阶段0总体设计.md`、`Harness_Agent_阶段1单Agent详细设计.md`  
> 实现目录：`agent-harness/`  
> 记录目的：说明当前实际实现与阶段 1 设计文档的符合情况、不一致点和原因。

---

## 1. 当前实现概览

已在当前工作区下新建独立项目：

```text
agent-harness/
├── pyproject.toml
├── README.md
├── .env.example
├── harness.example.toml
├── src/agent_harness/
├── tests/
└── .gitignore
```

原根目录的 `main.py` 未修改。

当前实现包含：

- 配置系统；
- Provider-independent canonical protocol；
- Fake / Scripted Provider；
- DeepSeek-compatible Chat Completions Adapter；
- Agent Definition；
- Agent Loop；
- Context Builder；
- Tool Definition；
- Tool Registry；
- Tool Runtime；
- 三个只读 Workspace 工具：`list_files`、`read_file`、`search_text`；
- 基础 Run State；
- 最大 iteration、model call、tool call、wall time 预算；
- 统一 RunError 模型；
- JSONL Trace；
- `result.json`；
- CLI：`run`、`tools`、`inspect`；
- Demo Repository；
- Unit / Integration / Live tests。

---

## 2. 与阶段 1 设计一致的部分

### 2.1 项目边界

符合设计：

- 新建独立项目 `agent-harness/`；
- 未修改或接入其他黑板模式项目；
- 未使用 LangGraph、OpenAI Agents SDK Runner、CrewAI、AutoGen 替代核心 Runtime；
- 阶段 1 只实现单 Root Agent；
- 未实现 Subagent、Memory、Skill、MCP、HITL、Checkpoint、数据库、Docker Sandbox、文件写入、通用 shell。

### 2.2 Runtime

符合设计：

- `AgentLoop` 自研；
- Provider 不执行工具；
- Tool 只能通过 `ToolRuntime` 执行；
- Context Builder 不执行副作用；
- CLI 不包含 Agent Loop；
- Tool Result 会以 `tool` message 写回上下文；
- 支持单轮多个 Tool Call，并按顺序串行执行；
- assistant 同时返回文本与 Tool Call 时，文本保留但不作为最终答案；
- 没有 Tool Call 且 assistant 文本非空时结束 Run。

### 2.3 Provider

符合设计：

- Runtime 使用内部 `ModelRequest` / `ModelResponse` / `CanonicalMessage` / `ToolCall`；
- DeepSeek 原始响应只在 `providers/deepseek.py` 内解析；
- Fake Provider 用于确定性测试；
- DeepSeek API Key 只从环境变量读取，未写入配置文件或 Trace。

### 2.4 Tool 与安全边界

符合设计：

- 内置 `list_files`、`read_file`、`search_text`；
- 不提供 `run_command`；
- 不提供写文件工具；
- 所有工具路径经过 Workspace Boundary 校验；
- 拒绝绝对路径和 `..` 逃逸；
- 符号链接解析后仍必须留在 Workspace 内；
- 基础 secret denylist：`.env`、常见私钥名和私钥后缀；
- Tool 参数在本地进行 schema 校验；
- Tool 错误转换为 `ToolResult(status="error")`，默认反馈给模型而不是直接导致 Run 崩溃。

### 2.5 Trace 与测试

符合设计：

- 每个 Run 写入 `.harness/runs/<run_id>/events.jsonl`；
- 每个 Run 写入 `.harness/runs/<run_id>/result.json`；
- Trace Event 包含 `run_id`、`sequence_number`、`iteration`、`payload`；
- 单元测试和集成测试不依赖真实 DeepSeek；
- 普通测试默认跳过 Live test；使用 `pytest -m live` 时才运行真实接口测试。

---

## 3. 与阶段 1 设计不一致或未完全达到的地方

### 3.1 未创建 `docs/phase-0-overview.md` 和 `docs/phase-1-single-agent-design.md`

设计文档推荐在新项目中包含：

```text
docs/
├── phase-0-overview.md
└── phase-1-single-agent-design.md
```

实际没有复制这两个文档到 `agent-harness/docs/`。

原因：

- 当前工作区已有权威设计文档位于带前导空格的 ` doc/` 目录；
- 为避免产生重复文档和版本漂移，阶段 1 实现只在根 `README.md` 写运行说明；
- 本文件作为单独差异记录保存在原文档目录中。

影响：

- 不影响 Runtime 功能；
- 若后续希望 `agent-harness/` 可单独发布，应补充 `docs/` 或复制设计基线。

### 3.2 没有使用 Pydantic

设计文档建议使用 Pydantic / JSON Schema。

实际实现：

- 使用 Python dataclass 表达内部协议；
- Tool input schema 使用普通 JSON Schema 字典；
- `ToolRuntime` 实现了阶段 1 所需的基础本地校验。

原因：

- 当前阶段工具输入简单；
- 减少依赖，优先跑通核心 Loop；
- 仍保留统一 schema 出口，未来可替换为 Pydantic。

影响：

- 当前只校验基础类型、required 和未知字段；
- 尚未实现完整 JSON Schema 语义，例如 enum、array item、minimum、pattern。

### 3.3 `search_text` 未优先调用 `rg`

设计建议：

```text
优先使用受控 subprocess 调用 rg；
不可用时使用 Python fallback。
```

实际实现：

- 当前直接使用 Python fallback 遍历文本文件；
- 未调用 `rg`。

原因：

- 保持跨平台稳定；
- 避免在阶段 1 引入 subprocess 行为差异；
- 只读搜索能力已经满足 Fake Provider 和集成测试。

影响：

- 大仓库搜索性能不如 `rg`；
- 后续可在 `search_text` 内增加受控 `rg` 分支。

### 3.4 Provider retry 未写入 `model.retrying` Trace Event

设计要求 Provider retry 每次重试产生 Trace Event。

实际实现：

- DeepSeek Adapter 有有限重试；
- 但 retry 发生在 Provider 内部，当前没有把 `model.retrying` 事件回传给 Trace。

原因：

- 当前 Provider 接口未注入 Trace Sink；
- 为保持 Provider 和 Runtime 解耦，先没有让 Provider 直接依赖 tracing。

影响：

- 普通成功、失败、Run Trace 完整；
- Provider retry 的每次尝试不可在 JSONL 中逐条复盘。

建议：

- 后续在 Provider 接口增加 callback 或 Runtime 级 retry wrapper，而不是让 Provider 直接写 Trace。

### 3.5 Ctrl+C 取消只实现了基础路径

设计要求 CLI Ctrl+C 设置取消状态并保存 `run.cancelled`。

实际实现：

- `AgentLoop` 支持 `CancellationError` 和 `asyncio.CancelledError`；
- CLI 当前捕获 `KeyboardInterrupt` 的路径较基础；
- 没有专门的跨任务 signal handler 来主动设置 `RunState.cancellation_requested`。

影响：

- Runtime 内部取消语义存在；
- CLI 层的 Ctrl+C 行为还没有完整验收。

建议：

- 后续补充 signal handler，并增加取消集成测试。

### 3.6 测试覆盖数量少于设计文档建议

设计文档列出了更完整的测试矩阵，例如 Provider 重试耗尽、空响应、取消、Trace 全事件顺序等。

初始测试结果：

```text
17 passed, 2 skipped
```

覆盖了：

- 直接 final；
- tool 后 final；
- 单轮多个 tool call；
- 未知 tool error 反馈；
- tool call 预算；
- context limit；
- path boundary；
- secret denylist；
- tool timeout；
- output truncation；
- CLI tools；
- demo repository integration；
- DeepSeek live skip。

尚未完整覆盖：

- Provider retry 成功/耗尽；
- Provider 空响应；
- CLI Ctrl+C 取消；
- Trace 事件完整顺序断言；
- DeepSeek Adapter mock response 的全部字段映射。

影响：

- 核心 Happy Path 与关键工具边界已验证；
- 若进入长期维护，仍需补足设计文档中更完整的测试矩阵。

### 3.7 DeepSeek Live Test 已补充执行

设计要求真实 DeepSeek 基础运行成功作为进入后续阶段的重要条件之一。该项已在后续补充验证中完成。

实际情况：

- 工作区根目录 `.env` 中存在 `DEEPSEEK_API_KEY` 和 `DEEPSEEK_API_URL`；
- 已通过模型列表接口确认可用模型：
  - `deepseek-v4-flash`
  - `deepseek-v4-pro`
- 已执行真实 live test：

```bash
python -m pytest -m live
```

结果：

```text
1 passed, 18 deselected
```

同时已通过 CLI 对两个模型进行真实运行验证：

```text
v4-flash: run_92ad4334c103468c8bf928d49a7c9b67, COMPLETED
v4-pro:   run_b9a230ded2474cf3acacc1cbd44b8e8f, COMPLETED
```

影响：

- Fake Provider 仍用于确定性测试；
- DeepSeek-compatible Provider 的真实 API 路径已验证；
- CLI 的 `--model v4-flash` 与 `--model v4-pro` 已验证；
- Live test 仍不混入普通测试，避免成本和稳定性影响。

### 3.8 没有生成覆盖率报告

设计建议核心覆盖率达到较高水平。

实际：

- 执行了 `python -m pytest`；
- 未执行 coverage；
- 无覆盖率百分比。

影响：

- 只能确认测试通过，不能确认覆盖率指标。

### 3.9 当前工作区不是 Git 仓库

实际执行 `git status --short` 时返回：

```text
fatal: not a git repository
```

影响：

- 无法用 Git 状态区分用户原有改动和本次实现改动；
- 无法提交 commit；
- 已通过不修改原 `main.py` 降低影响范围。

---

## 4. 本次验证记录

### 4.1 单元和集成测试

执行目录：

```text
agent-harness/
```

执行命令：

```bash
python -m pytest
```

结果：

```text
17 passed, 2 skipped
```

最初两个 skipped：

- DeepSeek live test：未设置 `DEEPSEEK_API_KEY`；
- symlink escape test：当前 Windows 环境未允许创建 symlink。

后续补充 `.env` 自动加载、模型别名和配置测试后，最新普通测试结果：

```text
20 passed, 2 skipped
```

真实接口测试单独使用 `pytest -m live`，最新结果：

```text
1 passed, 21 deselected
```

### 4.2 CLI Fake Provider 演示

执行命令：

```bash
$env:PYTHONPATH='src'
python -m agent_harness.cli run --provider fake --workspace tests/fixtures/demo_repo --task "Find calculate_total and explain discounts."
```

结果：

```text
Status: COMPLETED
iteration_count: 4
model_call_count: 4
tool_call_count: 3
```

生成：

```text
.harness/runs/<run_id>/events.jsonl
.harness/runs/<run_id>/result.json
```

### 4.3 DeepSeek 真实环境验证

已确认 `.env` 中存在以下变量，但不记录具体值：

```text
DEEPSEEK_API_KEY
DEEPSEEK_API_URL
```

模型列表接口返回：

```text
deepseek-v4-flash
deepseek-v4-pro
```

真实 live test：

```bash
python -m pytest -m live
```

结果：

```text
1 passed, 18 deselected
```

真实 CLI 验证：

```bash
python -m agent_harness.cli run --provider deepseek --model v4-flash --workspace tests/fixtures/demo_repo --task "请用工具查看这个仓库，简要说明主要模块和价格计算入口。" --max-iterations 8
```

结果：

```text
Run ID: run_92ad4334c103468c8bf928d49a7c9b67
Status: COMPLETED
```

```bash
python -m agent_harness.cli run --provider deepseek --model v4-pro --workspace tests/fixtures/demo_repo --task "请用工具确认 calculate_total 定义在哪个文件和哪一行，只需简短回答。" --max-iterations 8
```

结果：

```text
Run ID: run_b9a230ded2474cf3acacc1cbd44b8e8f
Status: COMPLETED
```

---

## 5. 阶段 1 验收状态

### 5.1 已满足

- 可通过 CLI 提交任务；
- 可指定 Workspace；
- 能创建唯一 Run ID；
- 能使用 Fake Provider；
- 能向模型暴露 Tool Schema；
- 能解析单个 Tool Call；
- 能解析多个 Tool Call；
- 能执行三个只读工具；
- 能将 Tool Result 写回模型上下文；
- 能多轮循环；
- 能获得最终答案；
- 能正确结束 Run；
- 能处理 Tool Error；
- 能限制模型和工具调用次数；
- 能输出 JSONL Trace；
- 能输出 `result.json`；
- Agent Loop 不依赖 DeepSeek SDK 对象；
- Provider Adapter 不执行 Tool；
- Tool 不直接修改 Run State；
- Context Builder 不执行副作用；
- CLI 不包含 Agent Loop；
- Tool Schema 来自统一定义；
- Tool Call ID 全程保持关联；
- 未使用 Agent 框架 Runner；
- Tool 只能访问 Workspace；
- 阻止 `..` 路径逃逸；
- 阻止常见 Secret 文件；
- 没有通用 shell；
- 没有文件写入；
- Trace 不记录 API Key；
- README 明确阶段 1 安全限制。

### 5.2 部分满足

- DeepSeek Provider 已实现并完成 live 验证；
- Provider Error 基础转换已实现，但测试未覆盖全部分支；
- 取消语义 Runtime 已有，CLI Ctrl+C 未完整验收；
- 符号链接逃逸代码已实现，当前环境测试因权限跳过；
- Context Limit 已实现估算检查，但不是精确 tokenizer；
- Trace 基础事件已实现，但 Provider retry event 未实现。

### 5.3 未满足或未执行

- 未生成 coverage 报告；
- 未复制阶段 0/阶段 1 文档到 `agent-harness/docs/`；
- 未完整覆盖设计文档列出的全部测试矩阵。

---

## 6. 当前结论

当前 `agent-harness/` 已完成阶段 1 的核心工程闭环：

```text
CLI task
→ RunState
→ ContextBuilder
→ ModelProvider
→ ToolCall
→ ToolRuntime
→ ToolResult message
→ next model turn
→ final output
→ JSONL trace + result summary
```

实际实现没有提前引入阶段 2 及之后的 Subagent、Memory、Skill、MCP、Approval、Sandbox 或持久恢复。

进入阶段 2 前，建议优先补齐：

1. Provider retry trace；
2. CLI Ctrl+C 取消验收；
3. Provider Adapter mock tests；
4. coverage 报告；
5. 可选的 `rg` 搜索加速。

---

## 7. 追加更新记录

### 2026-07-11：真实 DeepSeek 与中文化补充

本次补充：

- 自动加载当前目录或父目录中的 `.env`；
- 支持 `DEEPSEEK_API_URL`；
- 支持 CLI 模型短别名：
  - `v4-flash` → `deepseek-v4-flash`
  - `v4-pro` → `deepseek-v4-pro`
- 系统提示词改为中文；
- README 改为中文优先说明；
- Fake Provider 示例最终回答改为中文；
- 工具描述改为中文；
- 源码函数补充函数级说明；
- 修复 `DeepSeekProvider` 在 `slots=True` 下缺少 `_client` 字段的问题；
- 避免 `DeepSeekProvider` 的 `repr` 输出 API Key；
- 普通 `pytest` 默认跳过 live test，`pytest -m live` 才执行真实接口。
