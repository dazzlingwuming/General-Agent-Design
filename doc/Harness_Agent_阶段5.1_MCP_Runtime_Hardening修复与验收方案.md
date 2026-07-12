# General Agent Harness：阶段 5.1 MCP Runtime Hardening 修复与验收方案

> 文档版本：v1.0  
> 文档日期：2026-07-12  
> 目标仓库：`https://github.com/dazzlingwuming/General-Agent-Design`  
> 当前基线提交：`d2ff7e29d04c52866c4d4bc3b787579dc9dad4b2`  
> 对应实现记录：`doc/Harness_Agent_阶段4.1.1与阶段5_实现差异及验收记录.md`  
> 文档用途：直接交给 Codex，修复阶段 5 MCP Client Runtime 的已知问题，并完成阶段 5 的正式验收。
>
> 当前状态：
>
> ```text
> 阶段 4.1.1 Trust：已完成
> 阶段 5 MCP 核心 Client Runtime：已完成
> 阶段 5 完整协议兼容与生命周期验收：未完成
> ```
>
> 本轮目标不是推倒重写 MCP，而是在现有实现上完成：
>
> ```text
> 协议兼容
> 审批语义
> Tool Disclosure 生命周期
> 错误分类
> 取消与刷新任务清理
> Pagination
> OAuth 凭据隔离
> JSON Schema 完整校验
> 测试补齐
> ```

---

# 一、当前实现结论

当前仓库已经具备可运行的 MCP 核心能力：

```text
官方 MCP Python SDK 1.28.x
stdio
Streamable HTTP
Thread 级连接复用
required server
失败隔离
reconnect
Tools
Resources
Prompts
Roots
Server Instructions
ToolRuntime 接入
Permission / Approval
Bearer Token
OAuth Provider
Windows Credential Manager
Project stdio config hash approval
真实 stdio Integration Test
真实 Streamable HTTP Integration Test
```

这些部分应当保留。

当前问题主要集中在：

```text
设计字段存在，但没有真正接入运行语义；
小型测试 Server 可以运行，但大型真实 Server 的协议特性未覆盖；
Tool Search、审批和连接生命周期仍有状态泄漏；
MCP 错误没有按协议类型正确分类；
OAuth、Schema 和 Tool Name 存在边缘安全问题。
```

因此本轮命名为：

> 阶段 5.1：MCP Runtime Hardening

阶段 5.1 完成后，才能把阶段 5 标记为正式完成。

---

# 二、参考的成熟实现原则

本轮修复继续参考：

```text
MCP Specification 2025-11-25
MCP Python SDK v1.x
OpenAI Codex MCP
Claude Code MCP
OpenAI Agents SDK MCP
Python asyncio
JSON Schema 2020-12
```

核心原则：

```text
1. 不自己重写 MCP JSON-RPC 和 Transport；
2. Tool Call Error 与 Protocol Error 必须分开；
3. Tool Catalog 必须支持分页和 list-changed；
4. Deferred Tool Schema 必须有明确 Turn 生命周期；
5. Approval Mode 必须进入真正的 Permission 决策链；
6. CancelledError 清理后必须继续传播；
7. 异步后台 Task 必须由连接对象持有并在关闭时清理；
8. MCP Tool Schema 应使用标准 JSON Schema Validator；
9. OAuth Token 必须绑定具体 Resource Server；
10. 可能产生副作用的 Tool Call 默认不能自动重试。
```

官方参考：

- MCP Specification  
  https://modelcontextprotocol.io/specification/2025-11-25
- MCP Lifecycle  
  https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle
- MCP Transports  
  https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- MCP Authorization  
  https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
- MCP Tools  
  https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- MCP Resources  
  https://modelcontextprotocol.io/specification/2025-11-25/server/resources
- MCP Prompts  
  https://modelcontextprotocol.io/specification/2025-11-25/server/prompts
- OpenAI Codex MCP  
  https://developers.openai.com/codex/mcp/
- Claude Code MCP  
  https://code.claude.com/docs/en/mcp
- OpenAI Agents SDK MCP  
  https://openai.github.io/openai-agents-python/mcp/
- MCP Python SDK v1.x  
  https://github.com/modelcontextprotocol/python-sdk/tree/v1.x

---

# 三、阶段 5.1 的范围

## 3.1 必须完成的阻塞项

```text
P0-1 HTTP 404 自动重新 Initialize
P0-2 MCP Catalog Pagination
P0-3 isError 与 Protocol Error 分离
P0-4 CancelledError 正确传播
P0-5 list-changed 后台 Task 生命周期
P0-6 Server / Tool Approval Mode 真正接入
P0-7 Tool Search Turn 生命周期
P0-8 auto Tool Disclosure 的 10% 预算算法
P0-9 完整 JSON Schema Validator
P0-10 OAuth Credential 按 Server URI 隔离
P0-11 Canonical Tool Name 冲突处理
```

## 3.2 应在本轮补齐的功能尾项

```text
Admin Policy 最小实现
Subagent 显式 MCP 委派
Resource 用户显式引用
Prompt 用户显式命令
大结果 Artifact Store
连接并发配置真正生效
混合 Tool Result 完整保留
```

## 3.3 可以继续延期

```text
旧 SSE Transport
MCP Sampling
MCP Elicitation
MCP Tasks
MCP Apps
Resource Subscription
跨进程恢复 HTTP Session ID
远程 MCP Registry 自动安装
自动下载和运行第三方 MCP Server
MCP Server 自身沙箱
```

这些内容不得在阶段 5 验收文档中标记为已完成。

---

# 四、P0-1：Streamable HTTP 404 自动重新 Initialize

## 4.1 当前问题

当前实现依赖官方 SDK 管理当前 HTTP Session，但 Harness 只提供手动：

```text
reconnect(server)
```

没有明确处理：

```text
带 MCP-Session-Id 的请求返回 HTTP 404
```

MCP Streamable HTTP 规范要求：

```text
Client 必须丢弃旧 Session ID；
重新进行 Initialize；
重新协商 Capability；
重新加载 Catalog。
```

## 4.2 目标设计

在 `MCPServerConnection` 中增加：

```python
class MCPReconnectReason(StrEnum):
    MANUAL = "manual"
    CONNECTION_LOST = "connection_lost"
    SESSION_NOT_FOUND = "session_not_found"
    CATALOG_REFRESH_FAILED = "catalog_refresh_failed"
```

增加统一执行包装：

```python
async def execute_with_session_recovery(
    self,
    operation: Callable[[], Awaitable[T]],
    *,
    safe_to_retry: bool,
) -> T:
    ...
```

## 4.3 处理流程

### list/read 类请求

```text
请求
    ↓
HTTP 404 / Session Not Found
    ↓
关闭旧 ClientSession
    ↓
重新 Open Transport
    ↓
Initialize
    ↓
刷新 Capability 和 Catalog
    ↓
重试一次原请求
```

适用：

```text
tools/list
resources/list
resources/read
prompts/list
prompts/get
```

### tools/call

默认：

```text
不自动重试。
```

因为：

```text
Server 可能已经执行 Tool；
404 或连接断开不代表没有副作用。
```

流程：

```text
tools/call 出现 Session 失效
    ↓
重新 Initialize
    ↓
返回 MCP_TOOL_OUTCOME_UNKNOWN
    ↓
提示模型或用户：本次调用结果未知，不能自动重试
```

只有 Tool 明确配置：

```text
idempotent = true
```

且具有应用级幂等键时，才允许自动重试一次。

## 4.4 错误识别

优先使用 SDK 暴露的异常类型或 HTTP Response。

如果 SDK 没有直接暴露状态码，增加 Adapter 层：

```text
识别 404
识别 session not found
识别 invalid session
```

不得用字符串模糊匹配作为唯一实现；字符串匹配只能作为兼容 fallback。

## 4.5 测试

真实 HTTP Fixture：

```text
Initialize
→ 第一次 Tool 成功
→ Server 主动清除 Session
→ resources/read 返回 404
→ Harness 自动 Initialize
→ Resource Read 成功
```

Tool Call 测试：

```text
tools/call 后 Server 返回 Session Lost
→ Harness 不自动重复 Tool
→ 返回 Outcome Unknown
→ 新 Session 已建立
```

---

# 五、P0-2：MCP Catalog Pagination

## 5.1 当前问题

当前只调用一次：

```python
session.list_tools()
session.list_resources()
session.list_prompts()
```

大型 MCP Server 返回：

```text
nextCursor
```

时只会读取第一页。

## 5.2 统一分页方法

新增：

```python
async def collect_paginated(
    fetch_page: Callable[[str | None], Awaitable[PageT]],
    *,
    get_items: Callable[[PageT], Sequence[T]],
    get_next_cursor: Callable[[PageT], str | None],
    max_pages: int,
    max_items: int,
) -> tuple[T, ...]:
    ...
```

## 5.3 各 Catalog

```text
tools/list
resources/list
resources/templates/list
prompts/list
```

都使用统一分页器。

## 5.4 限制

配置：

```toml
[mcp]
max_catalog_pages = 100
max_tools_per_server = 2000
max_resources_per_server = 5000
max_prompts_per_server = 1000
```

达到限制：

```text
停止继续读取；
Server 标记 DEGRADED；
记录 Catalog Truncated；
保留已读取内容。
```

不能无限循环。

## 5.5 Cursor 安全

检测：

```text
重复 Cursor
空 Cursor 循环
超过 Page Limit
```

出现时返回：

```text
MCP_PROTOCOL_ERROR
```

## 5.6 测试

Fixture 每页只返回 2 条：

```text
5 Tools
5 Resources
5 Prompts
```

最终必须全部发现。

另外测试恶意 Server：

```text
nextCursor 永远相同
```

Harness 必须停止并记录协议错误。

---

# 六、P0-3：Tool Execution Error 与 Protocol Error 分离

## 6.1 当前问题

当前：

```python
if result.isError:
    raise MCPConnectionError(...)
```

这会把业务错误误判成连接错误。

MCP 语义：

```text
JSON-RPC Error / Transport Error：协议或连接错误。
CallToolResult.isError = true：Tool 已经正常返回，只是 Tool 执行失败。
```

## 6.2 新错误类型

```python
class MCPProtocolError(HarnessError):
    ...

class MCPTransportError(HarnessError):
    ...

class MCPToolExecutionError(HarnessError):
    ...

class MCPToolOutcomeUnknown(HarnessError):
    ...
```

## 6.3 Tool Adapter 返回结构

建议 Connection 不直接抛出 Tool Error，而是返回：

```python
@dataclass(frozen=True)
class MCPNormalizedToolResult:
    structured_content: dict | None
    text_content: tuple[str, ...]
    resource_links: tuple[str, ...]
    embedded_resources: tuple[dict, ...]
    image_content: tuple[dict, ...]
    audio_content: tuple[dict, ...]
    is_error: bool
```

Tool Adapter 处理：

```text
is_error = false
→ ToolResult.success

is_error = true
→ ToolResult.error
→ error_code = MCP_TOOL_EXECUTION_ERROR
→ 保留 Server 返回内容
→ Connection 仍保持 READY
```

## 6.4 模型自修复

例如：

```text
参数不合法
权限不足
Issue 不存在
API 限流
```

Tool Error 内容应进入模型上下文，让模型决定：

```text
修正参数
换 Tool
询问用户
停止
```

## 6.5 测试

Server Tool 返回：

```python
CallToolResult(
    isError=True,
    content=[TextContent(text="参数 project_id 不存在")]
)
```

断言：

```text
Connection status = READY
ToolResult.status = error
error_code = MCP_TOOL_EXECUTION_ERROR
模型可看到错误文本
```

---

# 七、P0-4：CancelledError 必须继续传播

## 7.1 当前问题

`connect()` 捕获：

```python
except BaseException
```

会吞掉：

```text
asyncio.CancelledError
KeyboardInterrupt
SystemExit
```

## 7.2 修复

```python
except asyncio.CancelledError:
    self.status = MCPServerStatus.STOPPING
    await self._cleanup_partial_connection()
    self._emit("mcp.server_connect_cancelled", ...)
    raise

except Exception as exc:
    ...
```

不得捕获 `BaseException` 作为普通失败。

## 7.3 call_tool / refresh / close

所有异步边界遵守：

```text
CancelledError：清理后继续传播。
普通 Exception：转换为 MCP Error。
```

## 7.4 测试

```text
连接过程中 cancel Task
→ 状态不是 FAILED
→ CancelledError 传播到上层
→ stdio 子进程被关闭
→ AsyncExitStack 清理
```

---

# 八、P0-5：list-changed 后台 Task 生命周期

## 8.1 当前问题

当前通知处理：

```python
asyncio.create_task(self._refresh_after_notification())
```

没有保存 Task。

问题：

```text
关闭连接时 Task 仍运行；
多通知并发刷新；
刷新使用已经关闭的 Session；
Task Exception 可能成为未处理异常。
```

## 8.2 推荐模型

优先采用：

```text
Notification 只标记 Catalog stale
下一次安全边界刷新
```

Connection 保存：

```python
catalog_stale: set[MCPCatalogKind]
catalog_refresh_lock: asyncio.Lock
```

通知：

```text
tools/list_changed
→ stale.add(TOOLS)
```

在以下时点刷新：

```text
下一次 Model Request 前
/mcp refresh
调用 Search 前
调用具体 Tool 发现不存在时
```

## 8.3 如果保留后台刷新

必须增加：

```python
_refresh_tasks: set[asyncio.Task]
_refresh_lock: asyncio.Lock
```

关闭流程：

```text
标记 closing
cancel refresh tasks
await gather(return_exceptions=True)
关闭 Session
```

## 8.4 Snapshot 一致性

Model Request 构建期间 Tool Catalog 必须稳定。

刷新后生成新：

```text
MCPToolCatalogSnapshot
```

下一次 Model Request 才使用。

## 8.5 测试

```text
短时间发送 20 次 list_changed
→ 最多一个刷新执行
→ 连接关闭没有悬挂 Task
→ Catalog 最终一致
```

---

# 九、P0-6：Server / Tool Approval Mode 真正接入

## 9.1 当前问题

当前配置已解析：

```text
default_approval_mode
tool_approval_overrides
```

但 Tool Adapter 只通过 RiskLevel 粗略映射。

问题：

```text
always 与 writes 没有真正区别；
tool_approval_overrides 没有生效；
never 不能准确表达；
Annotation 没有 Trust Gate。
```

## 9.2 MCPApprovalMode

```python
class MCPApprovalMode(StrEnum):
    INHERIT = "inherit"
    ALWAYS = "always"
    WRITES = "writes"
    NEVER = "never"
```

## 9.3 MCPApprovalResolver

```python
class MCPApprovalResolver:
    def resolve(
        self,
        server: MCPServerConfig,
        tool: MCPToolRecord,
    ) -> MCPApprovalDecision:
        ...
```

顺序：

```text
1. tool_approval_overrides
2. server.default_approval_mode
3. global Permission Rule
4. Tool Annotation（只在 Trusted Server 上作为提示）
```

## 9.4 语义

### ALWAYS

```text
MCP 层强制 ASK
```

### NEVER

```text
MCP 层不额外 ASK
```

但仍不能绕过：

```text
Permission DENY
Agent Tool Allowlist
Skill Tool Scope
ApprovalPolicy.UNTRUSTED
系统硬规则
```

### INHERIT

使用现有 Permission Engine。

### WRITES

只有同时满足：

```text
Server Trusted
Annotation 可信启用
readOnlyHint = true
destructiveHint != true
```

才不额外 ASK。

否则：

```text
ASK
```

## 9.5 不要只修改 RiskLevel

Approval Mode 应进入：

```text
PermissionEvaluation
```

可以增加 ToolDefinition Metadata：

```python
metadata={
    "mcp_server": ...,
    "mcp_remote_tool": ...,
    "mcp_approval_mode": ...,
}
```

在 Permission Engine 中增加 MCP Policy Layer。

## 9.6 Approval UI

显示：

```text
Server Scope
Server URL / Command
Tool Name
Description
Arguments
Annotation
Annotation Trust
Effective Approval Mode
```

## 9.7 测试矩阵

```text
server always
server never
server writes + readonly
server writes + destructive
tool override always
tool override never
untrusted annotation
Permission Deny
ApprovalPolicy.NEVER
```

---

# 十、P0-7：Tool Search 必须是 Turn-local

## 10.1 当前问题

当前：

```python
loaded_tool_names: set[str]
```

属于整个 Thread。

搜索后 Tool 永久进入所有后续 Turn。

## 10.2 新状态

```python
@dataclass
class MCPToolDisclosureState:
    always_loaded: set[str]
    turn_loaded: dict[str, set[str]]
```

## 10.3 生命周期

Thread Start：

```text
建立 always_loaded
```

Turn Start：

```text
创建空 turn_loaded[turn_id]
```

`mcp_search_tools`：

```text
把 Tool 加入当前 turn_id
```

Turn End：

```text
删除 turn_loaded[turn_id]
```

## 10.4 Context Builder

```python
effective_tool_names(
    turn_id,
    names,
)
```

只允许：

```text
非 MCP Tool
always_loaded
当前 Turn loaded
```

## 10.5 Search Tool 输入

增加：

```python
class SearchMCPToolsInput(BaseModel):
    query: str
    server: str | None = None
    limit: int = 8
```

当前 Tool 必须能从 Principal 或 Runtime Context 获取：

```text
thread_id
turn_id
agent_id
```

不能依赖全局变量。

## 10.6 测试

```text
Turn 1 Search github tool
→ Tool 可见

Turn 2
→ Tool 不可见

always_load_tools
→ 每个 Turn 均可见
```

---

# 十一、P0-8：真正实现 auto Tool Disclosure

## 11.1 当前问题

现在：

```text
eager：全部加载
auto：实际等于 deferred
```

没有 Context Budget 判断。

## 11.2 模式

```python
class MCPToolDisclosureMode(StrEnum):
    EAGER = "eager"
    AUTO = "auto"
    SEARCH = "search"
```

## 11.3 Token 估算

计算：

```text
所有 Enabled MCP Tool：
name
description
inputSchema
outputSchema
```

序列化后的字符数，通过现有：

```text
char_to_token_ratio
```

估算 Token。

阈值：

```text
max_estimated_input_tokens * max_tool_context_ratio
```

默认：

```text
0.10
```

## 11.4 AUTO 语义

```text
预计 Tool Tokens <= 10% Context
→ EAGER

超过 10%
→ SEARCH
```

`always_load_tools` 始终加载。

## 11.5 Snapshot

记录：

```text
configured_mode
effective_mode
estimated_tool_tokens
tool_budget_tokens
loaded_count
deferred_count
```

## 11.6 测试

```text
2 个小 Tool → AUTO = EAGER
100 个大 Tool → AUTO = SEARCH
always_load Tool 在 SEARCH 中仍可见
```

---

# 十二、P0-9：使用标准 JSON Schema Validator

## 12.1 当前问题

当前手写 Validator 只支持部分 Schema。

MCP Tool 可能使用：

```text
$ref
oneOf
anyOf
allOf
const
pattern
format
exclusiveMinimum
union type
dependentRequired
```

## 12.2 依赖

增加：

```toml
jsonschema>=4.23,<5
```

## 12.3 Validator

```python
from jsonschema import validators

def validate_json_schema(instance, schema):
    validator_cls = validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator = validator_cls(schema)
    errors = sorted(
        validator.iter_errors(instance),
        key=lambda item: list(item.path),
    )
    ...
```

## 12.4 Draft

如果 Schema 有：

```text
$schema
```

按其 Draft 选择。

没有 `$schema` 时，默认：

```text
Draft 2020-12
```

## 12.5 本地 Tool

现有手写 Validator 可以保留作为 Legacy / Fast Path，但 MCP Tool 必须使用标准 Validator。

更推荐所有 Tool 统一迁移到 `jsonschema`。

## 12.6 错误格式

输出：

```text
path
schema_path
validator
message
```

不得把整个 Secret 参数写入 Trace。

## 12.7 测试

覆盖：

```text
oneOf
$ref
pattern
format
const
nullable
nested array
outputSchema
invalid server schema
```

---

# 十三、P0-10：OAuth Credential 必须绑定 Server URI

## 13.1 当前问题

当前 Keyring 使用：

```text
agent-harness-mcp:<server_name>
```

同名 Server URL 改变后可能读取旧 Token。

## 13.2 Credential Identity

```python
@dataclass(frozen=True)
class MCPCredentialIdentity:
    server_name: str
    canonical_resource_uri: str
    auth_mode: str
    scopes: tuple[str, ...]
```

Key：

```text
sha256(
    server_name
    + canonical_resource_uri
    + auth_mode
    + normalized scopes
)
```

Service：

```text
agent-harness-mcp:<short_hash>
```

## 13.3 URL Canonicalization

至少规范化：

```text
scheme lowercase
host lowercase
default port removal
path normalization
fragment removal
```

Query 是否参与 Identity：

```text
默认参与
```

因为不同 Query 可能代表不同 Resource。

## 13.4 Config 变化

以下变化必须重新授权：

```text
URL
Scope
Auth Mode
Client Metadata
Resource Parameter
```

## 13.5 Legacy Migration

检测旧：

```text
agent-harness-mcp:<name>
```

不要自动复制 Token 到新 Server。

可以提示：

```text
发现旧凭据，需要重新登录。
```

## 13.6 测试

```text
同名不同 URL → 不共享 Token
同 URL 不同 Scope → 不共享 Token
相同 Identity → 可复用 Token
logout 只删除当前 Identity
```

---

# 十四、P0-11：Canonical Tool Name 冲突处理

## 14.1 当前问题

当前：

```text
非法字符 → _
截断 64 字符
```

可能碰撞。

## 14.2 新命名

格式：

```text
mcp__<readable_server>__<readable_tool>__<hash8>
```

Hash 输入：

```text
server_name + "\0" + remote_tool_name
```

Provider 有长度限制时：

```text
保留 Hash 后缀
截断 readable 部分
```

## 14.3 Collision Check

Catalog 构建时：

```text
canonical_name → source identity
```

如果仍碰撞：

```text
扩大 Hash
或
拒绝 Catalog
```

不能静默覆盖。

## 14.4 Snapshot

保存：

```text
canonical_name
remote_name
server_name
name_hash
```

## 14.5 测试

```text
foo-bar
foo.bar
foo_bar
两个超长 Tool Name
Unicode Tool Name
```

必须生成不同 Canonical Name。

---

# 十五、P1：Admin Policy 最小实现

## 15.1 当前状态

已经存在：

```text
MCPConfigScope.ADMIN
```

但没有真正读取 Admin Server 定义和 Admin Deny Policy。

## 15.2 最小范围

Admin 配置：

```text
Admin MCP Server Definition
Disabled Server Names
Allowed stdio Command Prefix
Allowed HTTP Domain
Denied HTTP Domain
Denied Tool Pattern
```

## 15.3 示例

```json
{
  "policy": {
    "disabledServers": ["unknown-cloud"],
    "allowedStdioCommands": ["python", "node", "uvx"],
    "allowedHttpDomains": ["mcp.company.com"],
    "deniedHttpDomains": ["example-malicious.com"],
    "deniedTools": ["*delete*", "*publish*"]
  },
  "mcpServers": {}
}
```

## 15.4 优先级

```text
Admin Deny
> Workspace Trust
> Scope Winner
> User Allow
```

Admin Policy 不能被 Project/User 配置覆盖。

---

# 十六、P1：连接并发配置真正接入

当前配置有：

```text
connect_in_parallel
max_parallel_connections
```

Runtime 必须传入 Manager。

语义：

```text
connect_in_parallel = false
→ 按稳定顺序串行连接

true
→ Semaphore(max_parallel_connections)
```

测试：

```text
max_parallel=2
→ 同时最多两个 Server Connecting
```

---

# 十七、P1：混合 Tool Result 完整归一化

## 17.1 当前问题

有 `structuredContent` 时会丢弃 Text 和 Resource Link。

## 17.2 正确模型

统一保留：

```python
@dataclass
class MCPNormalizedToolResult:
    structured_content: dict | None
    text_content: tuple[str, ...]
    resource_links: tuple[dict, ...]
    embedded_resources: tuple[dict, ...]
    images: tuple[ArtifactRef, ...]
    audio: tuple[ArtifactRef, ...]
    is_error: bool
```

## 17.3 Model Context

模型得到：

```text
简短文本摘要
structured JSON
Artifact/Resource References
```

不能丢数据，也不能把大型二进制编码进 Prompt。

---

# 十八、P1：大结果 Artifact Store

## 18.1 当前问题

当前只做字符截断。

## 18.2 Artifact

超过：

```text
max_mcp_tool_result_chars
```

流程：

```text
完整结果写入 Thread Artifact Store
    ↓
生成 Artifact ID
    ↓
ToolResult 返回：摘要、Artifact Path/ID、原始大小、MIME、Hash
```

## 18.3 路径

```text
.harness/threads/<thread_id>/artifacts/mcp/<artifact_id>
```

## 18.4 安全

```text
不在文件名中包含 Token
不使用远程 Server 提供的原始路径
限制大小
二进制不作为文本打开
```

---

# 十九、P1：Resource 与 Prompt 的用户交互

## 19.1 Resource

增加：

```text
/mcp resources [server]
/mcp resource <server> <uri>
```

可选：

```text
@mcp:<server>:<uri>
```

Resource 内容作为：

```text
User-selected external context
```

进入当前 Turn。

不能作为 System Message。

## 19.2 Prompt

增加：

```text
/mcp prompts [server]
/mcp prompt <server>/<name> key=value
```

默认：

```text
Prompt 只允许用户显式调用。
```

模型可见 `mcp_get_prompt`：

```text
默认关闭
```

如果保留，必须经过 Approval。

---

# 二十、P1：Subagent 显式 MCP 委派

## 20.1 当前状态

Child 默认没有 MCP Tool，这是安全的。

但需要支持 Parent 显式委派：

```text
server/tool 子集
```

## 20.2 DelegationRequest

增加：

```python
allowed_mcp_tools: tuple[str, ...] = ()
```

## 20.3 有效 Tool

```text
Parent Effective MCP Tools
∩ Delegation allowed_mcp_tools
∩ Child AgentDefinition
∩ SkillExecution allowed-tools
∩ MCP Server Filter
∩ Permission Engine
```

## 20.4 连接共享

Root 与 Child 共享 Thread 级：

```text
MCPServerConnection
```

但 Tool Principal 不同。

不能为每个 Child 重启 stdio Server。

## 20.5 测试

```text
Parent 未委派 → Child 无 MCP Tool
Parent 委派一个 Tool → Child 只有该 Tool
Child 不能搜索到其他 MCP Tool
Trace agent_id 正确
```

---

# 二十一、Server Instructions 预算与信任

保持当前：

```text
Server Instructions 明确标记为外部不可信
```

补充：

```text
每个 Server 4 KiB
全部 Server 总预算
完整 Entry 省略
不从中间截断结构标签
```

Snapshot 记录：

```text
instructions_hash
instructions_chars
truncated
```

---

# 二十二、Tool Search 搜索质量

当前简单 substring 可以保留作为最小实现，但建议改为：

```text
Tokenized Name
Description
Server Instructions
BM25
```

不需要 Embedding。

排序必须稳定：

```text
score desc
server name
tool name
```

Search Result 返回：

```text
canonical name
server
description
input schema
approval mode
```

---

# 二十三、Snapshot 与 Resume

阶段 5.1 Snapshot 增加：

```text
Effective Tool Disclosure Mode
Catalog Page Count
Catalog Truncated
Catalog Hash
Approval Policy Hash
Credential Identity Hash
Instructions Hash
Canonical Tool Mapping
```

Resume：

```text
不恢复旧 HTTP Session ID
不恢复 stdio PID
重新 Initialize
比较 Config/Catalog Hash
记录 snapshot_changed
```

---

# 二十四、Rollout 与 Trace

新增或补全：

```text
mcp.session_expired
mcp.session_reinitialized
mcp.tool_outcome_unknown
mcp.catalog_page_loaded
mcp.catalog_truncated
mcp.catalog_stale
mcp.catalog_refresh_started
mcp.catalog_refresh_completed
mcp.catalog_refresh_cancelled
mcp.tool_disclosure_resolved
mcp.tool_activated
mcp.tool_deactivated
mcp.approval_resolved
mcp.tool_execution_error
mcp.protocol_error
mcp.transport_error
mcp.artifact_created
mcp.credential_identity_changed
```

关键字段：

```text
server
remote_tool
canonical_tool
turn_id
agent_id
request_id
cursor
page
approval_mode
config_hash
catalog_hash
```

Secret 必须 Redact。

---

# 二十五、推荐代码结构

```text
src/agent_harness/mcp/
├── approval_policy.py
├── pagination.py
├── disclosure.py
├── schema_validation.py
├── results.py
├── artifacts.py
├── naming.py
├── admin_policy.py
├── delegation.py
├── auth.py
├── connection.py
├── manager.py
├── runtime.py
├── config.py
└── models.py
```

现有文件可以继续使用，不要求机械迁移，但以下职责必须分离：

```text
Approval Resolution
Catalog Pagination
Disclosure State
Result Normalization
Credential Identity
Canonical Naming
Connection Lifecycle
```

---

# 二十六、实施顺序

## Step 1：先补失败测试

必须先增加当前会失败的测试：

```text
isError 被当连接错误
Tool Search 跨 Turn 泄漏
Pagination 只读第一页
CancelledError 被包装
tool_approval_overrides 不生效
auto 模式永远 Search
同名 Tool 碰撞
同名不同 URL 共享 OAuth Token
```

## Step 2：错误模型和 Result Normalization

先修：

```text
isError
Protocol Error
Transport Error
Outcome Unknown
Mixed Content
```

## Step 3：Connection 生命周期

修：

```text
CancelledError
404 Reinitialize
Refresh Task
Close
Reconnect
```

## Step 4：Pagination

统一 Tools、Resources、Prompts、Templates。

## Step 5：Approval Resolver

让配置真正生效。

## Step 6：Disclosure State

完成：

```text
Turn-local Search
AUTO 10%
always loaded
```

## Step 7：JSON Schema

接入标准 Validator。

## Step 8：OAuth 与 Naming

修复凭据隔离和 Tool Name 冲突。

## Step 9：Admin Policy 和并发配置

## Step 10：Resource、Prompt、Artifact

## Step 11：Subagent MCP 委派

## Step 12：完整回归和真实协议测试

---

# 二十七、完整测试矩阵

## 27.1 HTTP Session

- [ ] Resource Read 404 自动 Reinitialize；
- [ ] Tool List 404 自动 Reinitialize；
- [ ] Prompt Get 404 自动 Reinitialize；
- [ ] Tool Call 404 不自动重试；
- [ ] Outcome Unknown；
- [ ] 手动 Reconnect；
- [ ] 新 Capability Snapshot。

## 27.2 Pagination

- [ ] Tools 多页；
- [ ] Resources 多页；
- [ ] Templates 多页；
- [ ] Prompts 多页；
- [ ] Cursor Loop；
- [ ] Page Limit；
- [ ] Item Limit。

## 27.3 Error

- [ ] isError=true；
- [ ] JSON-RPC Error；
- [ ] Transport Error；
- [ ] Output Schema Error；
- [ ] Connection 保持 READY；
- [ ] Model 可见 Tool Error。

## 27.4 Cancellation

- [ ] Connect Cancel；
- [ ] Tool Call Cancel；
- [ ] Refresh Cancel；
- [ ] Close During Refresh；
- [ ] No leaked Task；
- [ ] No leaked stdio process。

## 27.5 Approval

- [ ] Server always；
- [ ] Server writes；
- [ ] Server never；
- [ ] Tool Override；
- [ ] Untrusted Annotation；
- [ ] Permission Deny；
- [ ] ApprovalPolicy.NEVER；
- [ ] Approval UI Arguments。

## 27.6 Disclosure

- [ ] Eager；
- [ ] Search；
- [ ] Auto small；
- [ ] Auto large；
- [ ] Always Load；
- [ ] Turn 1 Search；
- [ ] Turn 2 Reset；
- [ ] list_changed 后 Catalog 更新。

## 27.7 Schema

- [ ] `$ref`；
- [ ] oneOf；
- [ ] anyOf；
- [ ] pattern；
- [ ] format；
- [ ] outputSchema；
- [ ] invalid Schema；
- [ ] Secret 不进入错误日志。

## 27.8 OAuth

- [ ] 同名不同 URL；
- [ ] 同 URL 不同 Scope；
- [ ] Refresh；
- [ ] Logout；
- [ ] Legacy Credential；
- [ ] Token 不进入 Trace。

## 27.9 Naming

- [ ] 标点碰撞；
- [ ] 长名称碰撞；
- [ ] Unicode；
- [ ] Provider Length；
- [ ] Snapshot Mapping。

## 27.10 Subagent

- [ ] 无委派无 MCP；
- [ ] 单 Tool 委派；
- [ ] 多 Tool 委派；
- [ ] Parent Permission Ceiling；
- [ ] Skill Tool Intersection；
- [ ] Agent Attribution。

## 27.11 Resource / Prompt / Artifact

- [ ] Resource CLI；
- [ ] Prompt CLI；
- [ ] Prompt 默认用户控制；
- [ ] Mixed Content；
- [ ] 大结果 Artifact；
- [ ] Binary Artifact；
- [ ] External Content 不作为 System。

---

# 二十八、阶段 5.1 验收标准

只有全部满足，阶段 5 才能标记为完成。

## Protocol

- [ ] stdio；
- [ ] Streamable HTTP；
- [ ] HTTP 404 Reinitialize；
- [ ] Pagination；
- [ ] Capability Gate；
- [ ] Timeout；
- [ ] Cancellation；
- [ ] Graceful Shutdown。

## Lifecycle

- [ ] Thread 级连接复用；
- [ ] Required / Optional；
- [ ] Failure Isolation；
- [ ] Refresh Task 可控；
- [ ] No leaked Task；
- [ ] No leaked Process；
- [ ] Resume 重新 Initialize。

## Tools

- [ ] ToolRuntime；
- [ ] Permission；
- [ ] Approval Mode；
- [ ] Tool Override；
- [ ] Turn-local Disclosure；
- [ ] Auto 10%；
- [ ] Tool Error 分类；
- [ ] JSON Schema；
- [ ] Canonical Name 无碰撞。

## Auth

- [ ] Bearer Env；
- [ ] OAuth Provider；
- [ ] Credential 按 URI 隔离；
- [ ] Token Refresh；
- [ ] Logout；
- [ ] No Token Passthrough；
- [ ] Secret Redaction。

## Resources / Prompts

- [ ] Resource List / Read；
- [ ] User Resource Command；
- [ ] Prompt User Command；
- [ ] Prompt 不作为 System；
- [ ] External Content 明确不可信。

## Results

- [ ] structuredContent；
- [ ] Text；
- [ ] Resource Link；
- [ ] Embedded Resource；
- [ ] Binary Artifact；
- [ ] 大结果 Artifact；
- [ ] Output Schema。

## Subagent

- [ ] 默认无 MCP；
- [ ] 显式子集委派；
- [ ] 共用 Connection；
- [ ] Principal 隔离；
- [ ] Trace Attribution。

## Policy

- [ ] Workspace Trust；
- [ ] Project stdio Hash Approval；
- [ ] Admin Deny Policy；
- [ ] Scope Winner；
- [ ] 配置并发参数生效。

## Engineering

- [ ] Ruff；
- [ ] Mypy；
- [ ] Pytest；
- [ ] compileall；
- [ ] git diff --check；
- [ ] GitHub Actions 真实成功；
- [ ] stdio Integration；
- [ ] Streamable HTTP Integration；
- [ ] OAuth 可用真实 Server 或官方兼容 Fixture 验收；
- [ ] 差异记录更新。

---

# 二十九、验收后的准确描述

阶段 5.1 完成前：

> 阶段 5 MCP 核心 Client Runtime 已完成，支持 stdio、Streamable HTTP、Tools、Resources、Prompts、Roots、Bearer 和 OAuth 基础流程，但完整协议兼容和生命周期加固尚未完成。

阶段 5.1 完成后：

> 已完成 Thread 级 MCP Client Runtime，支持官方 SDK stdio 与 Streamable HTTP、Session 恢复、分页 Catalog、渐进式 Tool Disclosure、Permission/Approval、Resources、Prompts、OAuth、Subagent 显式委派、完整结果归一化和可审计生命周期。

---

# 三十、给 Codex 的执行要求

Codex 正式编码前必须先输出：

```text
1. 本文每个 P0/P1 问题在当前代码中的复现路径；
2. MCP Connection 新状态机；
3. HTTP 404 Reinitialize 流程；
4. Pagination Helper 接口；
5. Tool Error / Protocol Error 类型图；
6. Approval Resolver 决策表；
7. Turn-local Disclosure 状态模型；
8. JSON Schema Validator 方案；
9. OAuth Credential Identity；
10. Canonical Tool Naming；
11. Refresh Task 和 Close 顺序；
12. 修改文件列表；
13. 新增测试列表；
14. 与本文不同的设计及理由。
```

实施约束：

```text
1. 先写失败测试再修；
2. 不自己重写 MCP JSON-RPC；
3. 不升级到 MCP SDK v2；
4. 不自动重试未知副作用 Tool；
5. 不吞 CancelledError；
6. 不用 BaseException 处理普通连接错误；
7. 不让 Tool Search 跨 Turn 永久堆积；
8. 不信任 MCP Annotation；
9. 不继续扩展手写 JSON Schema Validator；
10. 不按 Server Name 单独复用 OAuth Token；
11. 不静默截断并丢弃大型结果；
12. 不进入 Memory 或 Sandbox 新阶段。
```

---

# 三十一、最终结论

当前 MCP 实现已经具备正确骨架，不需要重写。

需要完成的是：

```text
协议边界
状态边界
审批边界
错误边界
上下文边界
凭据边界
```

修复后的运行链应为：

```text
Thread Start
    ↓
Trust + Config Scope
    ↓
Connect / Initialize
    ↓
分页 Catalog
    ↓
AUTO/EAGER/SEARCH Disclosure
    ↓
当前 Turn Tool Activation
    ↓
ToolRuntime
    ↓
Permission + MCP Approval Resolver
    ↓
MCP Tool Call
    ↓
Tool Error / Protocol Error 正确分类
    ↓
完整 Result Normalization
    ↓
Artifact / Rollout / Trace
    ↓
Turn End 清理 Tool Activation
    ↓
Thread Close 清理 Refresh Task、Session 和 Process
```

只有完成阶段 5.1，才能避免以下问题进入后续阶段：

```text
大型 MCP Server 只发现第一页；
Tool Search 永久污染 Thread；
Tool Error 被当成连接失败；
取消操作变成普通失败；
OAuth Token 发给错误的同名 Server；
审批配置存在但不生效；
后台 Catalog Refresh 在 Thread 关闭后继续运行。
```
