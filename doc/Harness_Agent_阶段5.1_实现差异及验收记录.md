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

后续加固已将 ArtifactStore 提升为 Thread 级依赖，由配置的 `trace.thread_directory / thread_id / artifacts` 创建并注入 Root/Child ToolRuntime。文本、JSON、Image 和 Audio 均支持 host 文件名、SHA-256 去重、原子写、MIME allowlist/magic sniff、encoded/decoded/Turn/Thread quota 和显式 cleanup。

### 2.6 SDK 1.28.1 的真实 404 表达

真实故障注入测试证明，Streamable HTTP transport 收到 404 后，SDK 向 ClientSession 暴露的是精确 `McpError: Session terminated`，异常链没有 `httpx.Response`。Host 因此在 typed status 检查之后兼容该 SDK 精确错误，不使用宽泛 session 字符串匹配。

### 2.7 SDK Resource Template 分页注册

SDK low-level `list_resource_templates()` decorator 在 1.28.1 中只接受完整 list。真实分页 fixture 使用同一 SDK `request_handlers` 注册点返回标准 `ListResourceTemplatesResult(nextCursor=...)`；客户端仍经过官方 stdio/HTTP codec，未自行实现 JSON-RPC。

## 3. 后续加固已完成

1. ASGI middleware 通过真实 Streamable HTTP 注入 session 404；Resource/Prompt 只重试一次，Tool call 不重放并返回 outcome unknown。
2. Low-level MCP Server 通过真实 stdio/HTTP 为 Tools、Resources、Templates、Prompts 各返回 5 项、每页 2 项 opaque cursor。
3. 本地 OAuth Server 覆盖 PRM/OASM、dynamic registration、PKCE、authorization code、refresh、invalid_grant、logout 和 identity 隔离。
4. Resource/Prompt CLI 选择会形成 external/untrusted user context；支持 next Turn、active mailbox、hash 去重、resume 和 Artifact 回退。
5. Approval UI 显示 scope、identity hash、remote/canonical tool、mode/source、side effect、annotation trust、principal 和脱敏参数。
6. Binary Artifact、三层 quota、去重、原子写和 cleanup 已完成。
7. Scripted Child Provider 固定调用 MCP，验证显式子集、共享 Connection 和 child principal trace attribution；不以真实模型随机行为作为门禁。
8. Connection transport 改为 owner task，同一 task 进入/退出 SDK AnyIO context；并发 404 使用 generation single-flight。

## 4. 仍明确延期

- Native Windows Restricted Token/ACL/Job Object、WSL 沙箱完善；
- SSE、Sampling、Elicitation、Tasks、Apps、Resource Subscription；
- 跨进程恢复旧 HTTP MCP session id；
- BM25（当前规模继续使用稳定 substring，按指标触发升级）；
- 外部 OAuth provider 和真实 DeepSeek 的手动 smoke。它们不能代替本地确定性门禁。

## 5. 最新本地验收结果

```text
Ruff:       passed
Mypy:       passed (114 source files)
Pytest core: 119 passed, 1 skipped, 3 deselected
stdio:      real official-SDK lifecycle and pagination passed
HTTP:       real Streamable HTTP lifecycle, 404 recovery, no-replay and pagination passed
OAuth:      local loopback PKCE/refresh/invalid_grant/logout passed
diff check: passed
```

新增针对性覆盖包括：

- Catalog helper 与真实 stdio/HTTP 多页；
- typed HTTP 404 与真实 SDK Session 404；
- Tool isError 分类并保持 READY；
- Tool call cancellation 传播；
- Approval override 和 untrusted annotation；
- Turn-local disclosure 和 AUTO 预算；
- JSON Schema `$ref`、`oneOf`、`pattern`、`format`；
- OAuth PKCE、refresh、invalid_grant、logout 与 identity 隔离；
- Canonical name 标点、Unicode 碰撞；
- Admin winner、invalid fail-closed、domain deny 和 tool pattern deny；
- TurnController cancellation、ApprovalGrantStore、Rollout sticky failure；
- External Context pending/active/resume；
- Artifact MIME/quota/dedup/cleanup；
- deterministic Subagent MCP principal attribution。
