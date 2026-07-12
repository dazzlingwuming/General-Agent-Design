# Harness Agent 阶段 4.1.1 与阶段 5 实现差异及验收记录

> 记录日期：2026-07-11  
> 对照文档：`Harness_Agent_阶段4.1.1_Trust修复与阶段5_MCP_Client_Runtime详细设计.md`  
> 原则：以当前仓库真实实现和真实测试结果为准，不以设计稿勾选项代替验收。

## 一、外部调研与采用结论

本次没有自行实现 JSON-RPC 或 MCP transport，采用官方 `mcp==1.28.1` Python SDK。

- MCP Python SDK v1.x 当前仍是稳定线，v2 处于预发布阶段，因此项目固定为 `mcp>=1.28,<2`。
- stdio 使用 `StdioServerParameters`、`stdio_client` 和 `ClientSession`。
- Streamable HTTP 使用 `streamable_http_client` 和显式 `httpx.AsyncClient`。
- OAuth 使用官方 `OAuthClientProvider`；PKCE、metadata discovery、动态客户端注册和 token refresh 由 SDK 负责。
- 借鉴 OpenAI Agents SDK 的失败隔离方式：manager 只把 READY server 放入 active 集合，普通 server 失败不终止 Thread，required server 失败才终止初始化。
- 借鉴 Claude Code/Codex 的完整 scope winner 语义：同名 server 选择高优先级完整 entry，不跨 scope 合并字段。

参考：

- https://modelcontextprotocol.io/specification/2025-11-25
- https://github.com/modelcontextprotocol/python-sdk/tree/v1.x
- https://developers.openai.com/codex/mcp/
- https://code.claude.com/docs/en/mcp
- https://openai.github.io/openai-agents-python/mcp/

## 二、阶段 4.1.1 实际完成情况

已新增显式 `ProjectTrustContext`，分别计算：

- `guidance_allowed`
- `skills_allowed`
- `mcp_allowed`
- `project_config_allowed`
- `project_stdio_allowed`

`security.trusted_project=true` 在非交互模式中转换为显式 Trusted；否则 UNKNOWN 不再被 Guidance 配置反向推导为整个项目 Trusted。

项目 stdio MCP 还增加了第二层控制：

1. Workspace 必须 Trusted。
2. 用户必须确认准确的 server config hash。
3. config hash 变化后旧批准失效。
4. 非交互模式没有批准记录时直接阻断，不启动进程。

与设计稿不同：代码保留了部分 `project_trusted: bool` 兼容入口，供旧测试和旧调用方使用；入口内部立即转换为 `ProjectTrustContext`，新逻辑不再共享该布尔值。

## 三、阶段 5 实际完成情况

已实现：

- User、Local、Project MCP JSON 配置发现与完整 entry 覆盖。
- `.mcp.json` 项目配置 Trust Gate。
- stdio 与 Streamable HTTP 两种真实 transport。
- Thread 级连接复用、并行连接、required server、失败隔离、关闭和单 server reconnect API。
- initialize 与 capability gate。
- Server Instructions 有界注入，并明确标记为外部不可信内容。
- Tools、Resources、Prompts catalog。
- Tool allowlist 后 denylist 过滤。
- 监听 Tool、Resource、Prompt list-changed notification，并在 SDK 分发栈外刷新 catalog；刷新失败进入 DEGRADED。
- `mcp_search_tools` 渐进加载 schema；未加载的 MCP tool 不进入模型工具列表。
- MCP Tool 适配为内部 `ToolDefinition`，经过 ToolRuntime、capability、permission、approval、timeout、trace 和 output schema validation。
- structured content、文本和混合 content 归一化。
- Roots callback，仅暴露当前 Thread workspace root。
- Bearer token 环境变量认证。
- OAuth 2.1 官方 provider、PKCE 流程和 Windows Credential Manager 凭据存储。
- `agent-harness mcp add/list/get/remove/login/logout`。
- Thread 内 `/mcp` 状态。
- 无 token、无 session id 的 MCP server snapshot。

## 四、与设计稿不完全一致的地方

以下内容必须明确标记，不能视为完全按原文实现：

1. **Admin Policy 未实现。** 当前识别了 Admin scope 域模型，但尚未实现管理员 deny policy、command allowlist 和 URL domain policy。User、Local、Project winner 已实现。
2. **HTTP 404 session 自动重建未单独实现。** 当前 SDK 管理当前连接的 session id；Harness 提供 reconnect，但没有在 404 后自动判定并重建。
3. **OAuth 没有外部真实授权服务器验收。** OAuth 使用官方 SDK 实现且凭据落入系统 keyring，但本次没有可用的第三方 OAuth MCP 测试账号，所以只完成静态、类型和连接路径验证，不能宣称真实第三方登录已验收。
4. **Subagent 显式 MCP 委派未开放。** 当前 child agent 默认完全不能使用 MCP，符合最小权限；尚未增加父 Agent 按 server/tool 子集显式委派的配置面。
5. **Resource 用户 `@server:uri` 语法未实现。** 模型可通过 `mcp_read_resource` 调用；CLI 尚未解析 `@` mention。
6. **Prompt `/mcp prompt` Thread 命令未实现。** 模型可通过 `mcp_get_prompt` 获取，用户管理 CLI 目前只提供 server 管理命令。
7. **大结果只使用现有字符截断。** 尚未把超大 MCP 结果自动落盘并返回 artifact 引用。
8. **旧 SSE 没有实现。** 这是有意差异，设计稿也规定不把 SSE 作为阶段 5 主路径。
9. **首次启动批准按 config hash 保存。** 设计稿没有规定准确存储键；当前实现比仅按 server name 保存更严格，任何命令、参数或环境转发配置变化都会重新询问。

因此，当前状态应描述为：**阶段 4.1.1 已完成；阶段 5 核心 Client Runtime 已完成并通过双 transport 真实协议测试，但上述 1-7 项仍属于阶段 5 加固尾项，不能标记为原设计稿全部勾选完成。**

## 五、真实验收结果

执行环境：Windows PowerShell，Python 3.12，`mcp 1.28.1`。

- Ruff：通过。
- Mypy：通过，104 个 source files。
- Pytest：87 passed，4 skipped。
- stdio integration：真实启动官方 FastMCP 子进程，完成 initialize、tools/list、tools/call、resources/read、prompts/get。
- Streamable HTTP integration：真实启动官方 FastMCP HTTP 进程，完成 initialize、tools/list 和 tools/call。
- 测试提示词、工具输入、资源内容和 Prompt 内容均使用中文。

4 个 skipped 是仓库原有的环境相关测试，不是本阶段新增失败。

## 六、主要代码位置

- Trust：`src/agent_harness/guidance/trust.py`
- MCP 配置：`src/agent_harness/mcp/config.py`
- MCP 连接：`src/agent_harness/mcp/connection.py`
- MCP manager：`src/agent_harness/mcp/manager.py`
- MCP runtime/adapter：`src/agent_harness/mcp/runtime.py`
- MCP OAuth：`src/agent_harness/mcp/auth.py`
- stdio 首次启动批准：`src/agent_harness/mcp/approval.py`
- Thread 集成：`src/agent_harness/runtime/session.py`、`src/agent_harness/runtime/run_manager.py`
- CLI：`src/agent_harness/cli.py`
- 真实协议测试：`tests/unit/test_phase5_mcp.py`、`tests/integration/test_mcp_streamable_http.py`
