# General Agent Harness：阶段 5.1 实现差异及验收记录

> 记录日期：2026-07-12  
> 设计依据：`doc/Harness_Agent_阶段5.1_MCP_Runtime_Hardening修复与验收方案.md`  
> 原则：本记录以仓库当前代码和真实测试结果为准，不以设计文档中的目标描述代替完成事实。

## 1. 本轮已完成

### 1.1 协议与生命周期

- Streamable HTTP 读类操作遇到带类型信息的 HTTP 404 时，关闭旧 Session、重新 Initialize、刷新 Catalog，并仅重试一次。
- `tools/call` 遇到 Session 失效时不自动重试，重建 Session 后返回 `MCP_TOOL_OUTCOME_UNKNOWN`。
- Tools、Resources、Resource Templates、Prompts 使用统一 cursor 分页器。
- 分页器限制页数和条目数，并检测重复 cursor；截断时记录 degraded/truncated 状态。
- `CancelledError` 在 connect、tool call 和 refresh 边界清理后继续传播，不再由 `BaseException` 包装。
- list-changed 通知只标记 Catalog stale；在下一次安全边界刷新，不再创建无所有者后台 Task。
- `connect_in_parallel` 和 `max_parallel_connections` 已传入 Manager；可选择稳定串行或有界并行连接。

### 1.2 Tool、审批与上下文边界

- `CallToolResult.isError=true` 转换为 `MCP_TOOL_EXECUTION_ERROR`，保留 Server 文本和混合结果，Connection 保持 READY。
- Tool 结果统一保留 structuredContent、Text、Resource Link、Embedded Resource、Image 和 Audio 通道。
- MCP 输入/输出 Schema 使用 `jsonschema`，默认 Draft 2020-12，支持 `$ref`、`oneOf`、`format` 等标准关键字。
- Approval override 优先于 Server 默认模式；`always/writes/never/inherit` 已进入 PermissionEngine metadata policy 层。
- `writes` 仅在 Server trusted 且 annotation 明确只读、非 destructive 时免除 MCP 额外 ASK。
- Tool Search 状态改为 `always_loaded + turn_loaded[turn_id]`，Turn 结束删除临时激活项。
- AUTO disclosure 按 Tool Schema 估算 token，并以 Context 预算的 10% 决定 eager 或 search。
- Search 支持 Server 过滤并返回有效 approval mode；排序稳定。

### 1.3 凭据、命名与管理策略

- OAuth Keyring identity 绑定 Server name、canonical resource URI、auth mode 和排序后的 scope。
- URL 规范化覆盖 scheme/host 大小写、默认端口、路径、fragment 和 query。
- Canonical Tool Name 使用可读前缀和 `sha256(server_name + NUL + remote_name)` 后缀，截断时保留 hash。
- Catalog 构建检查 canonical name 冲突，不再静默覆盖。
- Admin `mcp.json` 最小策略支持：Admin Server winner、disabled server、stdio command allow、HTTP domain allow/deny、tool pattern deny。
- Admin 同名 Server 不允许被 User、Project 或 Local scope 覆盖。

### 1.4 用户交互、Artifact 与 Subagent

- 增加 `/mcp resources [server]`、`/mcp resource <server> <uri>`。
- 增加 `/mcp prompts [server]`、`/mcp prompt <server>/<name> key=value`。
- `mcp_get_prompt` 不再默认暴露给模型；Prompt 仅允许用户显式命令调用。
- 超过 ToolRuntime 文本上限的 MCP 文本结果写入 `.harness/threads/<thread_id>/artifacts/mcp/`，返回 host 生成的 ID、路径、大小、MIME 和 SHA-256。
- `DelegationRequest.allowed_mcp_tools` 支持显式 MCP 子集；Child 复用 Root 的 MCPRuntime Connection，并只注册请求中存在于当前 Catalog 的 Tool。
- Snapshot 增加 Catalog page count/truncated/hash、instructions hash/字符数、credential identity hash 和 canonical mapping。

## 2. 与设计文档不同的实现

### 2.1 404 识别

设计建议使用 SDK 公开异常。MCP Python SDK 1.28.1 没有为 Streamable HTTP Session 404 提供稳定、单一的公开异常类型，因此当前实现按以下顺序识别：

1. 异常或异常链上的 `httpx.Response.status_code == 404`；
2. SDK transport 异常公开的 `status_code == 404` 属性；
3. 仅作为兼容 fallback 的 `404 + session/not found/invalid` 文本。

没有修改 SDK，也没有自行实现 JSON-RPC。

### 2.2 list-changed

设计允许持有后台 Task 或 stale 模型。项目采用 stale 模型：通知处理不做网络 I/O，也不创建 Task；下一次 Tool call 或显式刷新边界执行受锁保护的刷新。这比维护后台 Task 集合更符合当前单进程 Thread 生命周期。

### 2.3 Tool outputSchema

outputSchema 在 Connection 对原始 `structuredContent` 校验。ToolDefinition 不再让通用 ToolRuntime 对归一化 envelope 重复校验，否则 Server Schema 会错误地校验 `{structured_content, text, ...}` 外层对象。

### 2.4 Prompt 默认能力

阶段 5 原实现向模型暴露 `mcp_get_prompt`。阶段 5.1 按用户控制原则将其默认关闭，只保留用户 CLI 显式命令。

### 2.5 Artifact 范围

当前 Artifact 接在 ToolRuntime 的 MCP adapter 输出边界，文本/JSON 大结果不会静默丢失。Image/Audio 仍仅以协议归一化引用保留，尚未将 base64 二进制拆分成独立二进制 Artifact。

## 3. 尚未完成或尚未达到正式验收

以下项目不得标记为阶段 5.1 已正式完成：

1. **真实 HTTP Session 404 Integration Test**：已覆盖 typed `httpx` 404 的恢复单元测试，但真实 FastMCP fixture 尚不能主动清除 SDK Session 后返回 404，因此没有完成“真实 Server 清 Session”的端到端验收。
2. **真实多页 MCP Server Integration Test**：分页器覆盖多页、重复 cursor；尚未用真实协议进程返回 Tools/Resources/Templates/Prompts 多页数据。
3. **OAuth 外部联调**：Identity、Keyring 隔离、login/logout 路径已实现；未使用真实 OAuth MCP 账户完成浏览器授权、refresh 和 logout 联调。
4. **Resource/Prompt 进入当前 Turn**：CLI 可显式读取并显示，但内容尚未自动封装为“User-selected external context”提交到下一模型 Turn；当前实现不会错误注入 System。
5. **Approval UI 完整字段**：Permission metadata 已包含 server、remote tool、mode、annotations 和 trust；终端审批界面尚未完整显示 arguments、annotation trust 和 effective mode。
6. **二进制 Artifact**：混合结果不丢失，但 Image/Audio 的独立文件落盘、MIME 扩展名和单项大小限制尚未实现。
7. **Artifact 总量限制与清理策略**：当前有 host 文件名和 thread 路径隔离，但没有 thread 总容量配额及淘汰策略。
8. **Subagent 端到端模型测试**：显式子集注册和共享 Connection 已实现，尚未用真实模型验证 Child 实际调用、Principal trace attribution 和 Parent permission ceiling 的完整矩阵。
9. **BM25 Tool Search**：仍使用稳定排序的 name/description substring；没有实现文档建议的 BM25。
10. **GitHub Actions**：本地质量门通过，但本轮尚未 push，不能声称 GitHub Actions 已真实成功。
11. **旧 SSE、Sampling、Elicitation、Tasks、Apps、Resource Subscription、跨进程 HTTP Session 恢复**：按设计明确延期。

因此本轮准确状态是：

> 阶段 5.1 的核心运行时加固和主要 P1 功能已落地；本地单元、真实 stdio、真实 Streamable HTTP 基础回归通过。由于真实 404/多页/OAuth、二进制 Artifact 和部分交互验收仍缺失，阶段 5 尚不能按原文的“全部验收项完成”标准标记为正式完成。

## 4. 本地验收结果

```text
Ruff:       passed
Mypy:       passed (109 source files)
Pytest:     96 passed, 4 skipped
stdio:      real official-SDK integration passed
HTTP:       real Streamable HTTP basic integration passed
compileall: passed
diff check: passed（未暂存实现改动）
```

新增针对性覆盖包括：

- Catalog 多页收集和 cursor loop；
- typed HTTP 404 读操作恢复；
- Tool isError 分类并保持 READY；
- Tool call cancellation 传播；
- Approval override 和 untrusted annotation；
- Turn-local disclosure 和 AUTO 预算；
- JSON Schema `$ref`、`oneOf`、`pattern`、`format`；
- OAuth 同名不同 URI 隔离；
- Canonical name 标点、Unicode 碰撞；
- Admin winner、domain deny 和 tool pattern deny。
