# General Agent Harness：阶段 4.1.1 Trust 修复与阶段 5 MCP Client Runtime 详细设计

> 文档版本：v1.0  
> 文档日期：2026-07-11  
> 目标仓库：`https://github.com/dazzlingwuming/General-Agent-Design/tree/main/agent-harness`  
> 当前基线提交：`07270783f2ca415b50a9ce53761f9f51534c792c`  
> 文档用途：直接交给 Codex，先完成阶段 4.1.1 小修复，再实施阶段 5 MCP Client Runtime  
> 设计依据：
> - MCP Specification 2025-11-25；
> - OpenAI Codex MCP 配置与工具策略；
> - Anthropic Claude Code MCP 生命周期、Scope、资源和 Tool Search；
> - OpenAI Agents SDK MCP Server Manager、过滤、缓存、重连和审批；
> - MCP 官方 Python SDK 稳定 v1.x。
>
> 重要边界：
> - 不修改沙箱路线；
> - 不实现 MCP Server；
> - 不自己实现 JSON-RPC 2.0；
> - 不进入 Long-term Memory；
> - 不把 MCP Tool 绕过现有 Tool Runtime；
> - 项目级 MCP 配置必须经过 Workspace Trust；
> - 本阶段以 MCP Client/Host 能力为主。

---

# 一、总体结论

本次工作分成两个连续部分：

```text
阶段 4.1.1：
修复 Guidance、Skills 和后续 MCP 共用的 Trust 传播模型

阶段 5：
实现 MCP Client Runtime，将外部 MCP Server 的 Tools、Resources、
Prompts 和 Server Instructions 接入现有 Thread / Turn / Item Runtime
```

阶段 5 的核心不是简单写一个：

```python
await session.list_tools()
```

而是建立完整的：

```text
配置发现
    ↓
Trust Gate
    ↓
Server 生命周期
    ↓
MCP 初始化与能力协商
    ↓
Tool / Resource / Prompt Catalog
    ↓
上下文预算和渐进式披露
    ↓
Permission / Approval
    ↓
Tool 调用
    ↓
结果校验与错误转换
    ↓
重连、取消、关闭
    ↓
Rollout / Trace / Resume
```

---

# 二、官方调研结论

## 2.1 MCP 的基础模型

MCP 是 Host、Client 和 Server 之间的有状态 JSON-RPC 2.0 协议。

```text
Harness：
MCP Host

每个 Server Connection：
MCP Client

外部程序或服务：
MCP Server
```

Server 可以提供：

```text
Tools
Resources
Prompts
Server Instructions
```

Client 可以选择提供：

```text
Roots
Sampling
Elicitation
```

MCP 连接具有严格生命周期：

```text
Initialization
    ↓
Version Negotiation
    ↓
Capability Negotiation
    ↓
initialized Notification
    ↓
Operation
    ↓
Shutdown
```

客户端只能使用初始化过程中成功协商的 Capability。

官方资料：

- https://modelcontextprotocol.io/specification/2025-11-25
- https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle

---

## 2.2 标准 Transport

当前规范定义两个标准 Transport：

```text
stdio
Streamable HTTP
```

### stdio

Client 启动本地子进程，通过 stdin/stdout 交换 MCP JSON-RPC。

```text
stdout：
只能输出合法 MCP Message

stderr：
允许输出日志
```

### Streamable HTTP

使用一个同时支持 POST 和 GET 的 MCP Endpoint。

```text
HTTP POST：
发送 JSON-RPC

HTTP GET / SSE：
接收 Server 主动消息和 Notification
```

旧 HTTP+SSE Transport 已被 Streamable HTTP 取代。

阶段 5 不应优先实现旧 SSE。

官方资料：

- https://modelcontextprotocol.io/specification/2025-11-25/basic/transports

---

## 2.3 Codex 的 MCP 做法

Codex 支持：

```text
stdio
Streamable HTTP
Bearer Token
OAuth
Server Instructions
```

配置可以来自：

```text
User config.toml
Trusted Project .codex/config.toml
```

Codex 为每个 Server 提供：

```text
startup_timeout_sec
tool_timeout_sec
enabled
required
enabled_tools
disabled_tools
default_tools_approval_mode
per-tool approval override
```

Codex读取初始化返回的 `instructions`，作为该 Server 的跨 Tool 指导。官方建议前 512 字符能够自包含地说明最关键用法。

官方资料：

- https://developers.openai.com/codex/mcp/

---

## 2.4 Claude Code 的 MCP 做法

Claude Code 将 Server 配置分成：

```text
Local Scope
Project Scope
User Scope
Plugin / Connector Scope
```

同名 Server 出现在多个 Scope 时：

```text
使用最高优先级的完整 Server Entry
不跨 Scope 合并字段
```

Project `.mcp.json` 在启用前需要用户批准。

Claude Code推荐：

```text
Remote Server 使用 Streamable HTTP
SSE 已弃用
```

Claude Code 还实现了：

```text
MCP Resource @mention
MCP Prompts
OAuth
Elicitation UI
Tool Search
```

Tool Search 的关键思想是：

```text
启动时不把所有 MCP Tool Schema 塞入 Context；
只加载 Server Instructions 和 Tool Name；
任务需要时再搜索并加载相关 Tool Schema。
```

官方资料：

- https://code.claude.com/docs/en/mcp

---

## 2.5 OpenAI Agents SDK 的成熟做法

OpenAI Agents SDK 提供：

```text
MCPServerManager
active_servers
failed_servers
strict mode
parallel connect
reconnect
connect timeout
cleanup timeout
```

还支持：

```text
Tool allow/block filter
Context-aware Tool Filter
Tool List Cache
Cache Invalidating
Per-tool Approval
Retry Configuration
Structured Content
```

这说明成熟 Runtime 不应把所有 MCP Server 简单放进一个列表，而应有独立的 Server Manager 和失败隔离。

官方资料：

- https://openai.github.io/openai-agents-python/mcp/

---

## 2.6 官方 Python SDK 选择

截至 2026-07-11：

```text
MCP Python SDK v1.x：
稳定生产线

MCP Python SDK v2：
预发布版本，仍可能破坏兼容性
```

因此阶段 5 应固定：

```toml
mcp = ">=1.28,<2"
```

不要直接依赖 v2 预发布接口。

官方 SDK 已经处理：

```text
JSON-RPC
stdio
Streamable HTTP
SSE
ClientSession
协议消息
生命周期
OAuth 基础组件
```

Harness 不应重新实现这些内容。

官方资料：

- https://github.com/modelcontextprotocol/python-sdk/tree/v1.x

---

# 三、阶段 4.1.1：Trust 传播修复

## 3.1 当前问题

当前 Runtime 在非交互路径中可能使用：

```python
project_trusted = not guidance.require_workspace_trust
```

这会将：

```text
Workspace 是否真正受信任
Guidance 是否允许加载
Skills 是否允许加载
```

混成一个布尔值。

问题包括：

### 情况一

```text
security.trusted_project = true
guidance.require_workspace_trust = true
```

项目已经被显式配置为 Trusted，但非交互 `run/exec` 仍可能把 Guidance 当成 Untrusted。

### 情况二

```text
guidance.require_workspace_trust = false
skills.require_workspace_trust = true
```

当前统一布尔值可能允许 Guidance 时，同时错误允许 Project Skill。

### 阶段 5 风险

MCP 加入后还会增加：

```text
mcp.require_workspace_trust
```

如果继续共用一个错误布尔值，可能出现：

```text
允许 Project Guidance
    ↓
错误自动启动 Project MCP stdio Server
```

而 Project MCP stdio Server 本质上是宿主机进程启动配置，风险远高于 Markdown Guidance。

---

## 3.2 新的 Trust 模型

新增：

```python
class WorkspaceTrustStatus(StrEnum):
    UNKNOWN = "unknown"
    TRUSTED_ONCE = "trusted_once"
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"
```

新增：

```python
class TrustDecisionSource(StrEnum):
    INTERACTIVE = "interactive"
    PERSISTED = "persisted"
    USER_CONFIG = "user_config"
    DEFAULT = "default"
```

新增：

```python
@dataclass(frozen=True, slots=True)
class ProjectTrustContext:
    workspace_status: WorkspaceTrustStatus
    source: TrustDecisionSource

    guidance_allowed: bool
    skills_allowed: bool
    mcp_allowed: bool

    project_config_allowed: bool
```

---

## 3.3 Trust 计算

```python
workspace_trusted = status in {
    WorkspaceTrustStatus.TRUSTED_ONCE,
    WorkspaceTrustStatus.TRUSTED,
}
```

然后分别计算：

```python
guidance_allowed = (
    workspace_trusted
    or not config.guidance.require_workspace_trust
)

skills_allowed = (
    workspace_trusted
    or not config.skills.require_workspace_trust
)

mcp_allowed = (
    workspace_trusted
    or not config.mcp.require_workspace_trust
)
```

但是对 Project stdio MCP Server 增加更严格限制：

```text
即使 mcp.require_workspace_trust = false，
Project stdio Server 仍不能在 Untrusted Workspace 自动启动。
```

推荐：

```python
project_stdio_allowed = workspace_trusted
```

---

## 3.4 Config 中的 trusted_project

`security.trusted_project = true` 表示显式用户配置，应转换为：

```text
WorkspaceTrustStatus.TRUSTED
source = USER_CONFIG
```

不能只影响 Permission Rule。

---

## 3.5 各子系统独立使用

```text
GuidanceManager：
guidance_allowed

SkillDiscovery：
skills_allowed

MCPConfigResolver：
mcp_allowed

Project stdio launch：
workspace_trusted + first-launch approval
```

不再传统一：

```python
project_trusted: bool
```

---

## 3.6 非交互模式

非交互模式没有 Trust Prompt。

规则：

```text
已持久 Trust：
使用持久 Trust

security.trusted_project=true：
视为 Trusted

否则：
UNKNOWN / UNTRUSTED
```

`run/exec` 不应自动把未知项目视作 Trusted。

---

## 3.7 Trust Rollout

新增：

```text
workspace.trust_resolved
workspace.trust_changed
project.capability_allowed
project.capability_blocked
```

Payload：

```text
workspace_status
source
guidance_allowed
skills_allowed
mcp_allowed
project_stdio_allowed
```

---

## 3.8 阶段 4.1.1 测试

必须覆盖：

```text
Guidance Require / Skills Require / MCP Require

true / true / true
false / true / true
true / false / true
true / true / false
false / false / false
```

并覆盖：

```text
Interactive Trust Once
Persistent Trust
Explicit security.trusted_project
Unknown Workspace
Untrusted Workspace
run
exec
ConversationSession
Resume
```

特别验证：

```text
Guidance 可以加载
但 Project Skills 仍被阻断

Skills 可以加载
但 Project MCP 仍被阻断

Project HTTP MCP 可显示但不连接
Project stdio MCP 不启动
```

---

# 四、阶段 5 的目标范围

阶段 5 建立 MCP Client Runtime。

## 4.1 必须实现

```text
官方 MCP Python SDK v1.x 集成
stdio Transport
Streamable HTTP Transport
MCP Server Config Scope
Workspace Trust Gate
Server Manager
Initialize / Capability Negotiation
Server Instructions
Tool Discovery
Tool Filtering
渐进式 Tool Schema 加载
MCP Tool → Internal ToolDefinition Adapter
Permission / Approval 集成
Tool Result Normalization
Output Schema Validation
Timeout / Cancellation
Server Failure Isolation
Reconnect
Tool List Cache 和 list_changed
Resource List / Read
Prompt List / Get
Root Capability
Bearer Token Env Auth
OAuth 基础流程
CLI
Rollout / Trace
Fake / In-memory / stdio / HTTP 测试
```

---

## 4.2 本阶段暂不实现

```text
MCP Server 开发
旧 SSE Transport 作为主要路径
Sampling
Elicitation
MCP Tasks
MCP Apps / UI Resources
Resource Subscription 自动更新
跨进程恢复 Remote MCP Session ID
远程 Hosted Connectors
自动安装第三方 MCP Server
MCP Registry 自动下载
MCP Server 沙箱
```

Sampling 和 Elicitation 不应在 Initialize 时声明支持。

---

# 五、总体架构

```text
ThreadRuntime
    └── MCPRuntime
         ├── MCPConfigResolver
         ├── MCPServerManager
         ├── MCPServerConnection A
         ├── MCPServerConnection B
         ├── MCPToolCatalog
         ├── MCPResourceCatalog
         ├── MCPPromptCatalog
         ├── MCPToolAdapter
         ├── MCPAuthManager
         └── MCPAuditBridge
```

调用路径：

```text
Model
    ↓
Internal Tool Call: mcp__github__search_issues
    ↓
ToolRuntime
    ↓
Principal / Tool Allowlist
    ↓
Permission Engine
    ↓
Approval Manager
    ↓
MCPToolAdapter
    ↓
MCPServerConnection.call_tool()
    ↓
MCP Server
    ↓
Result Validation
    ↓
Canonical ToolResult
    ↓
Model
```

MCP 不能绕过：

```text
ToolRuntime
Permission
Approval
Budget
Trace
Rollout
```

---

# 六、MCP Config Scope

## 6.1 Scope

定义：

```python
class MCPConfigScope(StrEnum):
    ADMIN = "admin"
    USER = "user"
    LOCAL = "local"
    PROJECT = "project"
    BUNDLED = "bundled"
```

含义：

### ADMIN

管理员或系统强制配置。

### USER

用户跨项目使用的 Server。

### LOCAL

用户在特定项目中的私有配置，不进入 Git。

### PROJECT

项目根目录中的共享配置。

### BUNDLED

Harness 内置 Server 定义，默认低优先级。

---

## 6.2 推荐存储位置

### User

```text
~/.agent-harness/config.toml
```

```toml
[mcp.servers.context7]
transport = "stdio"
command = "npx"
args = ["-y", "@upstash/context7-mcp"]
```

### Local Project Private

```text
~/.agent-harness/projects/<project_identity>/mcp.json
```

### Project Shared

```text
<project_root>/.mcp.json
```

兼容 Claude Code 的标准项目配置。

### Admin

```text
/etc/agent-harness/mcp.json
```

Windows：

```text
%PROGRAMDATA%\AgentHarness\mcp.json
```

---

## 6.3 Scope 优先级

推荐：

```text
ADMIN Policy
    ↓
LOCAL
    ↓
PROJECT
    ↓
USER
    ↓
BUNDLED
```

需要区分：

```text
Admin Deny Policy
与
Server Definition Winner
```

Server Definition Winner：

```text
LOCAL > PROJECT > USER > BUNDLED
```

Admin 可以：

```text
强制禁用
限制 Transport
限制 URL Domain
限制 Command
限制 Tool
```

---

## 6.4 同名 Server

借鉴 Claude Code：

```text
同名 Server 使用最高优先级 Scope 的完整 Entry。
不合并字段。
```

例如：

```text
User 定义 URL
Project 定义 command
```

不能合并成：

```text
URL + command
```

必须选择完整 Project Entry 或 User Entry。

---

## 6.5 Project Config Trust

Project `.mcp.json`：

```text
Untrusted：
发现但不连接；
不向模型暴露；
不启动进程；
CLI 显示 Blocked。

Trusted：
进入 Server Catalog；
仍受 Server Enabled 和首次启动审批。
```

---

# 七、Server Config Model

```python
class MCPTransport(StrEnum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"
```

```python
@dataclass(frozen=True, slots=True)
class MCPServerConfig:
    name: str
    scope: MCPConfigScope
    enabled: bool
    required: bool

    transport: MCPTransport

    command: str | None
    args: tuple[str, ...]
    cwd: Path | None
    env: tuple[tuple[str, str], ...]
    env_vars: tuple[str, ...]

    url: str | None
    bearer_token_env_var: str | None
    headers: tuple[tuple[str, str], ...]

    auth_mode: str
    oauth_scopes: tuple[str, ...]

    startup_timeout_seconds: float
    tool_timeout_seconds: float
    cleanup_timeout_seconds: float

    enabled_tools: tuple[str, ...]
    disabled_tools: tuple[str, ...]

    default_approval_mode: str
    tool_approval_overrides: tuple[tuple[str, str], ...]

    always_load_tools: bool
    trusted: bool

    config_hash: str
```

---

## 7.1 验证规则

### stdio 必须有

```text
command
```

不能同时配置：

```text
url
```

### HTTP 必须有

```text
url
```

不能同时配置：

```text
command
```

### URL

默认要求：

```text
https://
```

允许：

```text
http://127.0.0.1
http://localhost
```

其他明文 HTTP：

```text
拒绝或要求显式危险配置。
```

### Command

不使用：

```text
shell=True
```

必须结构化：

```text
command + args
```

---

# 八、Server 生命周期

## 8.1 状态

```python
class MCPServerStatus(StrEnum):
    DISABLED = "disabled"
    BLOCKED_UNTRUSTED = "blocked_untrusted"
    NOT_CONNECTED = "not_connected"
    CONNECTING = "connecting"
    AUTH_REQUIRED = "auth_required"
    READY = "ready"
    DEGRADED = "degraded"
    RECONNECTING = "reconnecting"
    FAILED = "failed"
    STOPPING = "stopping"
    STOPPED = "stopped"
```

---

## 8.2 Thread 级生命周期

MCPRuntime 属于：

```text
ThreadRuntime
```

不是每个 Tool Call 临时创建。

```text
Thread Start / Resume
    ↓
Resolve Config
    ↓
Apply Trust
    ↓
并行连接 Server
    ↓
多个 Turn 复用连接
    ↓
Thread Close
    ↓
Graceful Shutdown
```

这样保留：

```text
MCP Session
Server Cache
OAuth 状态
Tool List
Resource List
```

---

## 8.3 Server Manager

```python
class MCPServerManager:
    async def start()
    async def connect(name)
    async def connect_all()
    async def reconnect(name)
    async def reconnect_failed()
    async def stop(name)
    async def shutdown()
```

状态集合：

```text
active_servers
failed_servers
blocked_servers
auth_required_servers
```

---

## 8.4 Required Server

借鉴 Codex：

```text
required = true
```

含义：

```text
Server 初始化失败
→ Thread Start 失败
```

普通 Server：

```text
失败记录
→ Thread 继续运行
→ /mcp 显示失败
```

---

## 8.5 并行连接

不同 MCP Server 可以并行 Initialize。

配置：

```text
connect_in_parallel = true
max_parallel_connections = 4
```

每个 Server 独立 Timeout。

---

# 九、stdio Server 安全

沙箱当前延期，因此 stdio Server 会直接运行在 Host。

这比普通 MCP HTTP Tool 更危险。

## 9.1 Project stdio Server

必须同时满足：

```text
Workspace Trusted
Server Enabled
首次启动批准
Command Policy 允许
```

首次启动 Approval 显示：

```text
Server Name
Config Source
Command
Args
CWD
传入的环境变量名称
是否包含 package manager
```

---

## 9.2 禁止静默执行

Project `.mcp.json` 中出现：

```text
npx -y package
uvx package
pipx run package
```

不能直接自动下载并执行。

第一版：

```text
默认 ASK
```

非交互模式：

```text
拒绝，除非 User/Admin 配置明确 allow。
```

---

## 9.3 Environment

默认只传：

```text
PATH
LANG
LC_ALL
TERM
SYSTEMROOT
WINDIR
```

Secret 必须通过：

```text
env_vars = ["GITHUB_TOKEN"]
```

按名称显式转发。

Config 中不能持久化 Secret Value。

---

## 9.4 stderr

捕获 Server stderr：

```text
写入 Trace
限制大小
不当作 MCP Protocol Message
不直接加入模型上下文
```

---

## 9.5 Shutdown

遵循 MCP Lifecycle：

```text
关闭 stdin
等待退出
超时后 terminate
再次超时后 kill
等待 Process Tree 清理
```

---

# 十、Streamable HTTP

## 10.1 Session

如果 Server 在 Initialize 返回：

```text
MCP-Session-Id
```

连接对象在当前 Thread Runtime 中保存。

后续请求带：

```text
MCP-Session-Id
MCP-Protocol-Version
Authorization
```

---

## 10.2 Session 失效

收到带 Session ID 请求的：

```text
HTTP 404
```

按照规范：

```text
清除旧 Session ID
重新 Initialize
刷新 Capability 和 Catalog
```

不应将旧 Session ID长期持久化到 Thread Metadata。

Resume Thread：

```text
重新连接并创建新 MCP Session。
```

---

## 10.3 Origin 与本地 Server

这是 Server 端责任，但 Client 仍应：

```text
只允许 localhost 本地 HTTP 配置；
清晰展示远程目标 Host；
默认拒绝可疑 URL；
不跟随跨 Host Redirect 发送 Authorization。
```

---

## 10.4 Retry

可以自动重试：

```text
initialize
tools/list
resources/list
prompts/list
resources/read
```

对于：

```text
tools/call
```

默认不能自动重试。

原因：

```text
Tool 可能已经产生副作用；
网络断开不代表 Server 未执行。
```

只有在以下条件同时满足时才可重试：

```text
User Config 明确标记 idempotent
Server / Tool 被 Trusted
调用带有应用级幂等键
```

MCP 规范本身没有通用 Tool Idempotency 保证。

---

# 十一、Capability Negotiation

Client Initialize 时只声明已实现能力。

阶段 5 声明：

```text
roots
```

阶段 5 不声明：

```text
sampling
elicitation
tasks
experimental
```

Server Capability Snapshot 保存：

```text
tools
resources
prompts
logging
completions
listChanged
resource subscribe
server instructions
```

Runtime 只能调用 Server 声明支持的能力。

---

# 十二、Roots

MCP Server 可以请求：

```text
roots/list
```

阶段 5 提供：

```text
workspace_root
```

可选提供：

```text
project_root
```

但默认不提供：

```text
User Home
父目录
其他 Repository
Secret Directory
```

---

## 12.1 Root Trust

只有 Trusted Server 可以获得 Root。

HTTP Remote Server第一次请求 Root 时，可以记录：

```text
mcp.roots.exposed
```

用户配置可以控制：

```toml
expose_roots = false
```

---

## 12.2 Root 变化

当 Thread CWD 不变时 Root 保持稳定。

后续支持 Workspace 切换时再发送：

```text
notifications/roots/list_changed
```

---

# 十三、Server Instructions

Server Initialize 可以返回：

```text
instructions
```

Codex 会将其作为 Server-wide Guidance。

本项目采用：

```text
读取
限制长度
标记来源
低于 Core / Admin / Project Guidance
```

Context 结构：

```xml
<mcp_servers>
  <server name="github" trusted="true">
    <instructions>
      ...
    </instructions>
  </server>
</mcp_servers>
```

---

## 13.1 安全语义

Server Instructions 是外部内容。

即使 Server Trusted，也不能覆盖：

```text
System Prompt
Permission
Approval
Workspace Trust
Project Guidance 的强制范围
```

不可信 Server Instructions：

```text
不注入模型 Context。
```

---

## 13.2 预算

每个 Server：

```text
前 512 字符作为关键摘要预算
完整 Instructions 上限 4 KiB
```

全部 Server 总预算：

```text
max_mcp_instruction_chars
```

超出时完整 Server Entry 省略，不从中间截断关键字段。

---

# 十四、MCP Tool Catalog

## 14.1 为什么不能全部加载

MCP Server 可能暴露几十或几百个 Tool。

将所有 JSON Schema 在每次模型请求中加载会造成：

```text
Context 膨胀
Tool 选择质量下降
Prompt Cache 失效
延迟增加
```

借鉴 Claude Code，阶段 5实现渐进式 Tool Disclosure。

---

## 14.2 Tool Record

```python
@dataclass(frozen=True, slots=True)
class MCPToolRecord:
    server_name: str
    remote_name: str
    canonical_name: str

    title: str | None
    description: str
    input_schema: dict
    output_schema: dict | None
    annotations: dict
    execution: dict | None

    trusted_server: bool
    metadata_hash: str
```

---

## 14.3 Canonical Tool Name

模型可见名称：

```text
mcp__<server>__<tool>
```

例如：

```text
mcp__github__search_issues
```

内部仍保存：

```text
server_name = github
remote_name = search_issues
```

对非法字符进行稳定编码。

不能仅使用远程 Tool Name，避免不同 Server 冲突。

---

## 14.4 Tool Filter 顺序

借鉴 Codex 和 Agents SDK：

```text
1. Server enabled_tools Allowlist
2. Server disabled_tools Denylist
3. Workspace Trust
4. Agent Tool Allowlist
5. SkillExecution Tool Scope
6. Permission Engine
7. Approval Policy
```

Denylist 在 Allowlist 后应用。

---

# 十五、Tool Search 与 Context Budget

## 15.1 两种模式

```python
class MCPToolLoadingMode(StrEnum):
    EAGER = "eager"
    AUTO = "auto"
    SEARCH = "search"
```

### EAGER

加载所有 Enabled MCP Tool Schema。

只适用于很小的 Tool 集合。

### AUTO

计算全部 MCP Tool Schema 的预计 Token。

如果小于：

```text
Context Window 的 10%
```

则 Eager。

否则进入 Search。

### SEARCH

启动时只注入：

```text
Server Name
Server Instructions
Tool Name
短 Description
```

完整 Schema 按需加载。

---

## 15.2 Search Tool

注册内部 Tool：

```text
search_mcp_tools
```

输入：

```python
class SearchMCPToolsInput(BaseModel):
    query: str
    server: str | None = None
    limit: int = 8
```

输出：

```text
Tool Canonical Name
Description
Server
Risk / Approval Summary
Input Schema
```

被 Search 返回并选中的 Tool：

```text
加入当前 Turn 的 MCP Tool Activation Set
下一次 Model Request 暴露完整 ToolDefinition
```

---

## 15.3 生命周期

MCP Tool Activation 默认：

```text
当前 Turn 持续有效
```

下一 Turn重新由 Search 或 Eager Policy决定。

不能让 Thread 访问过的所有 MCP Tool 永久堆积。

---

## 15.4 Search 实现

第一版不需要 Embedding。

使用：

```text
名称 Token
Description Token
Server Instructions
BM25 / 简单文本相关度
```

后续可以替换为语义搜索。

---

# 十六、Tool List Cache

## 16.1 缓存范围

Cache 属于：

```text
MCPServerConnection
```

不是永久全局缓存。

保存：

```text
tools
catalog_hash
loaded_at
stale
```

---

## 16.2 listChanged

Server 声明：

```text
tools.listChanged = true
```

并发送：

```text
notifications/tools/list_changed
```

Client：

```text
标记 Cache stale
```

在安全边界刷新：

```text
下一次 Model Request 前
或显式 /mcp refresh
```

不在模型正在执行 Tool Call 时替换当前 Tool Snapshot。

---

## 16.3 无 listChanged

配置：

```text
cache_tools_list = true
```

则 Thread 内缓存。

用户可以：

```text
/mcp refresh <server>
```

---

# 十七、MCP Tool Adapter

每个已暴露 MCP Tool 转换为现有：

```text
ToolDefinition
```

```python
class MCPToolAdapter:
    def to_tool_definition(
        self,
        server: MCPServerConnection,
        record: MCPToolRecord,
    ) -> ToolDefinition:
        ...
```

ToolDefinition：

```text
name：
canonical_name

input_schema：
MCP inputSchema

output model：
MCP outputSchema，可选

required capabilities：
MCP_TOOL_CALL
必要时 NETWORK_ACCESS / EXTERNAL_SIDE_EFFECT

executor：
connection.call_tool(remote_name, arguments)
```

---

## 17.1 Annotations 不可信

MCP 规范明确要求：

```text
Tool annotations 默认不可信。
```

因此不能仅根据：

```text
readOnlyHint
destructiveHint
```

自动免审批。

只有：

```text
Trusted Server
+
用户配置允许使用 Annotation
```

时，Annotation 才能帮助选择默认 Approval。

---

# 十八、Permission 与 Approval

## 18.1 MCP Tool 必须经过 Tool Runtime

禁止：

```text
Model → MCP Client 直接 call_tool
```

必须：

```text
Model
→ Internal ToolRuntime
→ Permission Engine
→ Approval
→ MCP Client
```

---

## 18.2 Capability

已有：

```text
MCP_TOOL_CALL
```

建议增加：

```text
MCP_RESOURCE_READ
MCP_PROMPT_USE
MCP_SERVER_START
MCP_REMOTE_CONNECT
```

如果不增加新 Capability，至少使用：

```text
MCP_TOOL_CALL
FILE_READ
NETWORK_ACCESS
EXTERNAL_SIDE_EFFECT
```

---

## 18.3 Approval Mode

每个 Server：

```text
inherit
always
writes
never
```

每个 Tool可以覆盖。

### inherit

使用全局 Permission Policy。

### always

每次调用都询问。

### writes

只对可信 Server 且可信 Annotation 标记的只读 Tool 自动执行。

Annotation 不可信时：

```text
等同 always。
```

### never

只表示 MCP 层不追加额外 Approval。

仍不能绕过：

```text
Permission Rule
Agent Tool Allowlist
Skill Tool Scope
系统硬 Deny
```

---

## 18.4 默认策略

### Project Server

```text
default approval = always
```

### User Trusted Read Server

```text
default approval = writes
```

### Remote Unknown Server

```text
default approval = always
```

### Explicit per-tool allow

用户可以在 User Config 中设置。

---

## 18.5 Approval UI

显示：

```text
MCP Server
Config Scope
Remote Tool Name
Tool Description
Arguments
Target Host
可能传出的路径或数据
Server Trust
Approval Mode
```

MCP 规范要求用户在调用前看到 Tool Input，防止数据外泄。

---

# 十九、Tool 调用结果

## 19.1 内容类型

MCP Tool Result可能包含：

```text
Text
Image
Audio
Resource Link
Embedded Resource
structuredContent
isError
```

---

## 19.2 Normalization

转换为内部：

```python
@dataclass
class MCPToolExecutionResult:
    text_content: tuple[str, ...]
    structured_content: dict | None
    artifacts: tuple[ArtifactRef, ...]
    resource_links: tuple[str, ...]
    is_error: bool
```

---

## 19.3 Structured Content

如果 Server提供：

```text
outputSchema
```

Client：

```text
校验 structuredContent
```

失败：

```text
ServerProtocolViolation
```

不把无效结构伪装成成功。

---

## 19.4 大内容

超过：

```text
max_mcp_tool_result_chars
```

不能直接截断后丢失数据。

推荐：

```text
完整内容写 Artifact Store
模型获得摘要 + ArtifactRef
```

Binary Content：

```text
保存 Artifact
不直接塞入文本 Context
```

---

## 19.5 Error

MCP 区分：

```text
Protocol Error
Tool Execution Error (isError=true)
```

Tool Execution Error：

```text
作为可操作 ToolResult 返回模型
允许模型修正参数
```

Protocol Error：

```text
转换为 MCPProtocolError
记录 Trace
只向模型返回必要摘要
```

---

# 二十、Timeout、Cancel 与 Retry

## 20.1 Timeout

配置：

```text
startup_timeout_seconds
tool_timeout_seconds
cleanup_timeout_seconds
```

所有请求均有 Timeout。

规范建议 Timeout 后发送：

```text
notifications/cancelled
```

然后停止等待。

---

## 20.2 Turn Cancel

用户中断 Turn：

```text
取消当前 MCP Request
发送 cancellation notification
等待短暂清理
不自动关闭整个 Server Connection
```

如果 Server无法恢复：

```text
Connection 标记 DEGRADED
下一次调用前 Reconnect。
```

---

## 20.3 Tool Retry

默认：

```text
MCP Tool Call不自动重试。
```

`tools/list` 等读取操作可重试。

---

# 二十一、Resources

## 21.1 MCP Resource 定位

Resource 是：

```text
Application-driven Context
```

不是模型自动副作用 Tool。

阶段 5 支持：

```text
resources/list
resources/templates/list
resources/read
listChanged
```

暂不实现自动订阅。

---

## 21.2 Resource Record

```python
@dataclass(frozen=True, slots=True)
class MCPResourceRecord:
    server_name: str
    uri: str
    name: str
    title: str | None
    description: str | None
    mime_type: str | None
    size: int | None
```

---

## 21.3 用户调用

支持：

```text
@mcp:github:issue://123
```

或：

```text
/mcp resource github issue://123
```

CLI解析后：

```text
读取 Resource
    ↓
创建 ResourceAttachment Item
    ↓
加入当前 User Turn Context
```

---

## 21.4 模型调用

可注册受控 Tool：

```text
list_mcp_resources
read_mcp_resource
```

但必须受：

```text
MCP_RESOURCE_READ
Permission
Result Size
MIME
```

控制。

---

## 21.5 Resource Security

读取前显示或记录：

```text
Server
URI
可能的数据范围
```

不能自动将 Resource发送到其他 Server。

Resource 内容是外部数据：

```text
按 Prompt Injection 内容处理
不作为 System Instruction
```

---

# 二十二、Prompts

MCP 规范将 Prompt 定义为：

```text
User-controlled
```

因此阶段 5 不让模型自动调用 MCP Prompt。

支持：

```text
/mcp prompts
/mcp prompt <server>/<prompt> key=value
```

流程：

```text
用户显式选择
    ↓
prompts/get
    ↓
显示来源
    ↓
将 Prompt Message 转换为当前 Turn Input
```

Rollout 保存：

```text
mcp.prompt_selected
mcp.prompt_loaded
```

不能将 MCP Prompt当成 System Prompt。

---

# 二十三、OAuth 与凭据

## 23.1 第一阶段 Auth

必须支持：

```text
Bearer Token from Env
Static Non-secret Headers
Unauthenticated HTTP
```

Config：

```toml
bearer_token_env_var = "GITHUB_MCP_TOKEN"
```

不保存：

```text
Token Value
Client Secret
Refresh Token
```

---

## 23.2 OAuth

成熟 MCP Client需要 OAuth 2.1。

阶段 5 OAuth要求：

```text
Protected Resource Metadata Discovery
Authorization Server Metadata Discovery
PKCE S256
State Verification
Exact Redirect URI
Resource Parameter
Scope Restriction
Secure Credential Store
Refresh Token
Logout
```

---

## 23.3 Token 规则

必须遵守：

```text
Token 不放 URL Query
每个 HTTP Request 都携带 Authorization
只向目标 MCP Server 发送为它签发的 Token
禁止 Token Passthrough
验证 Resource Audience
```

---

## 23.4 Credential Store

优先：

```text
OS Keyring
```

Fallback：

```text
用户目录 Credential File
严格文件权限
```

Config 只保存：

```text
credential reference
OAuth metadata
scope
```

---

## 23.5 CLI

```text
agent-harness mcp login <server>
agent-harness mcp logout <server>
agent-harness mcp auth-status <server>
```

---

## 23.6 OAuth 实施分段

### 5A

```text
Bearer Env Auth
```

### 5B

```text
OAuth Browser Flow
Credential Store
Refresh
Logout
```

阶段 5 完整验收要求 5B 完成；如果先提交 5A，必须标记为阶段 5 Beta。

---

# 二十四、MCP Server Instructions 与 Skill 的区别

```text
Skill：
Harness 本地可复用工作流，按需激活。

MCP Server Instructions：
远程 Server 对其全部 Tool 的使用说明。

MCP Tool：
远程可执行能力。

MCP Resource：
远程可读取上下文。

MCP Prompt：
用户选择的远程模板。
```

MCP Server Instructions不能注册新 Tool，也不能扩大权限。

---

# 二十五、Subagent 与 MCP

Child Agent是否可以使用 MCP，必须显式委派。

有效 MCP Tool：

```text
Parent Delegatable MCP Tools
∩ Child AgentDefinition Tools
∩ DelegationRequest.allowed_tools
∩ SkillExecution allowed-tools
∩ MCP Server Filter
∩ Permission Engine
```

默认：

```text
Child 不继承全部 MCP Server。
```

只传与任务相关的 Tool。

---

## 25.1 Server Connection 是否共享

同一个 Thread Runtime 中：

```text
Root 和 Child 共享 MCPServerConnection
```

但具有不同：

```text
ToolExecutionPrincipal
Tool Allowlist
Approval Context
Trace agent_id
```

不要为每个 Child 重新启动同一个 stdio Server。

---

# 二十六、MCP Snapshot 与 Thread Resume

## 26.1 保存内容

Thread Metadata / Snapshot 保存：

```text
Server Config Hash
Scope
Trust Result
Server Info
Protocol Version
Capability Snapshot
Server Instructions Hash
Tool Catalog Hash
Resource Catalog Hash
Prompt Catalog Hash
```

不保存：

```text
Bearer Token
Refresh Token
MCP Session ID
stdio PID
完整 HTTP Connection
```

---

## 26.2 Resume

```text
读取旧 MCP Snapshot
    ↓
重新 Resolve 当前 Config
    ↓
比较 Config Hash
    ↓
重新连接 Server
    ↓
重新 Initialize
    ↓
刷新 Catalog
    ↓
记录 mcp.snapshot_changed
```

旧 Turn继续引用旧 Snapshot。

新 Turn使用新连接和新 Snapshot。

---

# 二十七、Notifications

支持：

```text
notifications/tools/list_changed
notifications/resources/list_changed
notifications/prompts/list_changed
notifications/message
notifications/progress
```

行为：

```text
Catalog Notification：
标记 Stale

Logging：
写 Trace，按 Level 过滤

Progress：
更新 Tool Execution Progress

Unknown Notification：
记录 Debug，不崩溃
```

---

# 二十八、CLI

## 28.1 管理命令

```text
agent-harness mcp add
agent-harness mcp remove
agent-harness mcp list
agent-harness mcp get
agent-harness mcp enable
agent-harness mcp disable
agent-harness mcp login
agent-harness mcp logout
agent-harness mcp reconnect
agent-harness mcp refresh
agent-harness mcp test
```

---

## 28.2 Thread 内命令

```text
/mcp
/mcp servers
/mcp tools [server]
/mcp resources [server]
/mcp prompts [server]
/mcp status <server>
/mcp reconnect <server>
/mcp refresh <server>
```

---

## 28.3 /mcp 输出

显示：

```text
Name
Scope
Transport
Status
Trusted
Required
Auth
Protocol Version
Tools Count
Resources Count
Prompts Count
Last Error
```

---

# 二十九、Rollout 与 Trace

## 29.1 Rollout Item

```text
mcp.config_resolved
mcp.server_blocked
mcp.server_connecting
mcp.server_initialized
mcp.server_failed
mcp.server_reconnecting
mcp.server_stopped

mcp.catalog_created
mcp.catalog_changed
mcp.tool_activated
mcp.tool_call_started
mcp.tool_call_completed
mcp.tool_call_failed

mcp.resource_selected
mcp.resource_read

mcp.prompt_selected
mcp.prompt_loaded

mcp.auth_required
mcp.auth_completed
mcp.auth_failed

mcp.roots_exposed
```

---

## 29.2 Trace

Trace 增加：

```text
connect duration
initialize duration
protocol version
request id
tool latency
result size
retry count
cache hit
catalog refresh
notification count
stderr line
```

Secret 一律 Redact。

---

# 三十、Error Model

```python
class MCPErrorCode(StrEnum):
    CONFIG_INVALID = "config_invalid"
    BLOCKED_UNTRUSTED = "blocked_untrusted"
    STARTUP_TIMEOUT = "startup_timeout"
    AUTH_REQUIRED = "auth_required"
    AUTH_FAILED = "auth_failed"
    VERSION_MISMATCH = "version_mismatch"
    CAPABILITY_MISSING = "capability_missing"
    CONNECTION_LOST = "connection_lost"
    REQUEST_TIMEOUT = "request_timeout"
    TOOL_NOT_FOUND = "tool_not_found"
    TOOL_EXECUTION_ERROR = "tool_execution_error"
    PROTOCOL_ERROR = "protocol_error"
    RESULT_SCHEMA_INVALID = "result_schema_invalid"
    OUTPUT_TOO_LARGE = "output_too_large"
```

错误返回模型时应具有：

```text
server
tool
recoverable
suggested_action
```

---

# 三十一、推荐代码结构

```text
src/agent_harness/
├── mcp/
│   ├── config.py
│   ├── config_resolver.py
│   ├── models.py
│   ├── runtime.py
│   ├── manager.py
│   ├── connection.py
│   ├── transports.py
│   ├── lifecycle.py
│   ├── auth.py
│   ├── credentials.py
│   ├── tools.py
│   ├── tool_catalog.py
│   ├── tool_search.py
│   ├── resources.py
│   ├── prompts.py
│   ├── roots.py
│   ├── results.py
│   ├── errors.py
│   └── audit.py
│
├── trust/
│   └── context.py
│
├── runtime/
│   ├── thread_runtime.py
│   └── run_manager.py
│
└── cli/
    └── mcp_commands.py
```

不要求机械拆文件，但：

```text
Config
Connection
Lifecycle
Catalog
Adapter
Auth
```

必须分离。

---

# 三十二、配置示例

## 32.1 User stdio

```toml
[mcp]
enabled = true
require_workspace_trust = true
tool_loading_mode = "auto"
max_tool_context_ratio = 0.10
max_instruction_chars = 16000

[mcp.servers.context7]
transport = "stdio"
command = "npx"
args = ["-y", "@upstash/context7-mcp"]
enabled = true
required = false
startup_timeout_seconds = 15
tool_timeout_seconds = 60
enabled_tools = ["resolve-library-id", "get-library-docs"]
default_approval_mode = "writes"
env_vars = ["CONTEXT7_API_KEY"]
```

---

## 32.2 Remote HTTP

```toml
[mcp.servers.github]
transport = "streamable_http"
url = "https://mcp.github.example/mcp"
enabled = true
required = false
bearer_token_env_var = "GITHUB_MCP_TOKEN"
default_approval_mode = "always"
disabled_tools = ["delete_repository"]

[mcp.servers.github.tool_approval]
search_issues = "never"
create_issue = "always"
```

---

## 32.3 Project .mcp.json

```json
{
  "mcpServers": {
    "project-docs": {
      "type": "stdio",
      "command": "python",
      "args": ["tools/project_docs_mcp.py"],
      "env": {},
      "enabled": true,
      "required": false
    }
  }
}
```

该 Server：

```text
只有 Workspace Trusted 后可见；
首次启动仍需确认。
```

---

# 三十三、实施顺序

## Step 0：阶段 4.1.1 Trust

完成：

```text
ProjectTrustContext
独立 Guidance / Skills / MCP Allow
非交互 Trust
测试
```

---

## Step 1：SDK 与 Domain Model

增加：

```toml
mcp>=1.28,<2
```

实现：

```text
MCPServerConfig
MCPServerState
Capability Snapshot
Error Model
```

---

## Step 2：Config Resolver

实现：

```text
Admin
User
Local
Project
Bundled
Whole-entry precedence
Trust Gate
Diagnostics
```

---

## Step 3：Server Manager 和 stdio

实现：

```text
Lifecycle
Parallel Connect
Required
Timeout
Graceful Shutdown
stderr
```

先使用官方 SDK，不手写 JSON-RPC。

---

## Step 4：Streamable HTTP

实现：

```text
Session ID
Protocol Header
Bearer Env
Reconnect
404 Reinitialize
```

---

## Step 5：Tool Discovery 与 Adapter

实现：

```text
tools/list pagination
Tool Record
Namespace
Filter
ToolDefinition Adapter
```

---

## Step 6：Permission / Approval

所有 MCP Tool 经过现有 ToolRuntime。

---

## Step 7：Tool Result

实现：

```text
structuredContent
outputSchema
isError
Artifact
size limit
```

---

## Step 8：Tool Search

实现：

```text
AUTO 10%
search_mcp_tools
Turn-local Activation
Catalog Cache
listChanged
```

---

## Step 9：Resources

实现：

```text
list
read
user @mention
controlled model tools
```

---

## Step 10：Prompts

实现：

```text
list
get
user explicit invocation
```

---

## Step 11：Roots

只提供 Workspace Root。

---

## Step 12：OAuth

完成：

```text
PKCE
State
Discovery
Credential Store
Refresh
Logout
```

---

## Step 13：Subagent

实现 MCP Tool 委派和权限交集。

---

## Step 14：CLI、Rollout 与 Resume

---

## Step 15：完整验收

---

# 三十四、测试矩阵

## 34.1 Trust

- [ ] Guidance/Skills/MCP Trust 独立；
- [ ] Explicit trusted_project；
- [ ] Unknown Project MCP Blocked；
- [ ] Project stdio 不自动启动；
- [ ] User Server 不受 Project Trust 阻断；
- [ ] Non-interactive 安全默认。

---

## 34.2 Config

- [ ] User；
- [ ] Local；
- [ ] Project；
- [ ] Admin；
- [ ] Whole-entry precedence；
- [ ] 不跨 Scope 合并；
- [ ] Invalid stdio；
- [ ] Invalid HTTP；
- [ ] Duplicate Name；
- [ ] Secret 不持久化。

---

## 34.3 Lifecycle

- [ ] Initialize 首个请求；
- [ ] Version Negotiation；
- [ ] Capability Negotiation；
- [ ] initialized Notification；
- [ ] Required Failure；
- [ ] Optional Failure；
- [ ] Parallel Connect；
- [ ] Graceful stdio Shutdown；
- [ ] Thread 多 Turn复用；
- [ ] Resume 重新连接。

---

## 34.4 stdio

- [ ] command + args；
- [ ] no shell；
- [ ] env allowlist；
- [ ] stderr capture；
- [ ] stdout 非协议内容失败；
- [ ] startup timeout；
- [ ] process exit；
- [ ] first launch approval；
- [ ] package manager command approval。

---

## 34.5 HTTP

- [ ] Streamable HTTP；
- [ ] Protocol Version Header；
- [ ] Session ID；
- [ ] 404 Reinitialize；
- [ ] Bearer Env；
- [ ] HTTPS；
- [ ] localhost HTTP；
- [ ] Redirect Credential Protection；
- [ ] reconnect；
- [ ] connection timeout。

---

## 34.6 Tools

- [ ] Pagination；
- [ ] Name Namespace；
- [ ] Collision；
- [ ] Allowlist；
- [ ] Denylist；
- [ ] listChanged；
- [ ] Cache；
- [ ] outputSchema；
- [ ] structuredContent；
- [ ] isError；
- [ ] Protocol Error；
- [ ] no automatic side-effect retry；
- [ ] cancellation。

---

## 34.7 Tool Search

- [ ] Eager；
- [ ] Auto below 10%；
- [ ] Auto overflow；
- [ ] Search；
- [ ] Turn-local activation；
- [ ] next Turn reset；
- [ ] Server filter；
- [ ] Catalog update；
- [ ] stable ordering。

---

## 34.8 Permission

- [ ] MCP Tool经过 ToolRuntime；
- [ ] default always；
- [ ] writes trusted annotation；
- [ ] untrusted annotation ignored；
- [ ] per-tool override；
- [ ] Child intersection；
- [ ] Skill intersection；
- [ ] Permission Deny wins；
- [ ] Approval shows arguments。

---

## 34.9 Resources

- [ ] list；
- [ ] read text；
- [ ] read binary；
- [ ] size limit；
- [ ] @mention；
- [ ] URI validation；
- [ ] external data not system instruction；
- [ ] no cross-server forwarding。

---

## 34.10 Prompts

- [ ] list；
- [ ] get；
- [ ] arguments；
- [ ] user-only；
- [ ] source retained；
- [ ] Prompt not system message。

---

## 34.11 OAuth

- [ ] Metadata Discovery；
- [ ] PKCE S256；
- [ ] State；
- [ ] exact redirect URI；
- [ ] Resource Parameter；
- [ ] Scope Restriction；
- [ ] Token not in Query；
- [ ] Credential Store；
- [ ] Refresh；
- [ ] Logout；
- [ ] no token passthrough。

---

## 34.12 Rollout / Trace

- [ ] Config Source；
- [ ] Trust Result；
- [ ] Connect；
- [ ] Capability；
- [ ] Tool Call；
- [ ] Resource；
- [ ] Prompt；
- [ ] Auth；
- [ ] Reconnect；
- [ ] Secret Redaction；
- [ ] Agent / Turn Attribution。

---

# 三十五、阶段 5 验收标准

## Trust

- [ ] ProjectTrustContext 已落地；
- [ ] Guidance、Skills、MCP 独立；
- [ ] Project stdio 必须 Trusted + 首次确认；
- [ ] 非交互模式安全默认。

## Protocol

- [ ] 使用官方 SDK v1.x；
- [ ] stdio；
- [ ] Streamable HTTP；
- [ ] Initialization；
- [ ] Capability Negotiation；
- [ ] Graceful Shutdown；
- [ ] Timeout / Cancel。

## Lifecycle

- [ ] MCPRuntime 属于 Thread；
- [ ] 多 Turn 复用；
- [ ] Required / Optional；
- [ ] Failure Isolation；
- [ ] Reconnect；
- [ ] Resume 重建。

## Tools

- [ ] Tool Adapter；
- [ ] Namespace；
- [ ] Filter；
- [ ] Tool Search；
- [ ] Permission / Approval；
- [ ] Result Validation；
- [ ] listChanged。

## Context

- [ ] Server Instructions 有预算；
- [ ] MCP Tool Schema 不无限加载；
- [ ] Tool Activation 当前 Turn；
- [ ] External Content 不提升为 System。

## Resources / Prompts

- [ ] Resource List / Read；
- [ ] 用户 Resource Reference；
- [ ] Prompt User-controlled；
- [ ] 大内容进入 Artifact。

## Auth

- [ ] Bearer Env；
- [ ] OAuth PKCE；
- [ ] Credential Store；
- [ ] No Token Passthrough；
- [ ] Secret 不进入 Config / Trace / Rollout。

## Subagent

- [ ] MCP Tool 按显式委派；
- [ ] Root/Child 共用 Connection但权限隔离；
- [ ] Agent Attribution 正确。

## 工程

- [ ] Ruff；
- [ ] Mypy；
- [ ] Pytest；
- [ ] compileall；
- [ ] diff check；
- [ ] GitHub Actions 真实通过；
- [ ] README；
- [ ] 差异与验收记录。

---

# 三十六、明确禁止的实现

```text
1. 不自己手写 MCP JSON-RPC Client；
2. 不依赖 MCP SDK v2 预发布作为生产基线；
3. 不让 Project .mcp.json 在 Untrusted Workspace 启动；
4. 不使用 shell=True 启动 stdio Server；
5. 不把所有 Host 环境变量传给 Server；
6. 不将所有 MCP Tool Schema 永久加载到 Context；
7. 不让 MCP Tool 绕过 ToolRuntime；
8. 不信任 Tool Annotation；
9. 不自动重试可能产生副作用的 Tool Call；
10. 不把 MCP Prompt 作为 System Message；
11. 不把 Resource 内容当作可信指令；
12. 不声明未实现的 Sampling / Elicitation；
13. 不把 Token 写入 Config、Trace 或 Rollout；
14. 不保存 MCP Session ID 作为跨进程恢复依据；
15. 不自动下载并运行 Project 提供的 MCP Package。
```

---

# 三十七、给 Codex 的执行要求

编码前先输出：

```text
1. 当前 Trust 传播问题的复现；
2. ProjectTrustContext 设计；
3. MCP Config Scope 和优先级；
4. MCP Server 状态机；
5. SDK v1.x 接入方式；
6. stdio 安全启动流程；
7. Streamable HTTP Session 和 Reconnect；
8. Tool Catalog 和 Tool Search；
9. Tool Runtime / Approval 接入；
10. Resource / Prompt UI；
11. OAuth 状态机；
12. Rollout / Trace；
13. 文件改动列表；
14. 测试计划；
15. 与本文不同的设计及理由。
```

实施要求：

```text
每一步先写 Fixture 和失败测试；
每完成一步运行阶段 1-4 回归；
MCP 外部测试优先使用官方 SDK In-memory / Test Server；
stdio 和 HTTP 都必须有真实 Integration Test；
不以 Mock 全部替代协议测试；
不扩大到 MCP Server 或 Memory。
```

---

# 三十八、官方参考资料

## MCP Specification

- Specification  
  https://modelcontextprotocol.io/specification/2025-11-25

- Lifecycle  
  https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle

- Transports  
  https://modelcontextprotocol.io/specification/2025-11-25/basic/transports

- Authorization  
  https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization

- Tools  
  https://modelcontextprotocol.io/specification/2025-11-25/server/tools

- Resources  
  https://modelcontextprotocol.io/specification/2025-11-25/server/resources

- Prompts  
  https://modelcontextprotocol.io/specification/2025-11-25/server/prompts

- Roots  
  https://modelcontextprotocol.io/specification/2025-11-25/client/roots

## OpenAI Codex

- MCP  
  https://developers.openai.com/codex/mcp/

## Anthropic Claude Code

- MCP  
  https://code.claude.com/docs/en/mcp

## OpenAI Agents SDK

- MCP  
  https://openai.github.io/openai-agents-python/mcp/

## MCP Python SDK

- Stable v1.x  
  https://github.com/modelcontextprotocol/python-sdk/tree/v1.x

---

# 三十九、最终结论

阶段 5 的正确实现不是：

```text
读取 .mcp.json
→ 启动进程
→ 把所有 Tool 塞给模型
```

而是：

```text
Trust
    ↓
配置 Scope
    ↓
Server Manager
    ↓
协议初始化
    ↓
能力协商
    ↓
有限 Catalog
    ↓
按需 Tool Schema
    ↓
现有 Permission / Approval
    ↓
Tool 调用和结果校验
    ↓
资源、Prompt、Auth、重连和审计
```

完成阶段 5 后，Harness 将具备成熟 Coding Agent 的外部能力扩展层：

```text
本地 MCP Server
远程 MCP Server
第三方 Tool
外部 Resource
用户 Prompt Workflow
OAuth 授权
多 Server 生命周期
```

同时仍保持现有架构原则：

```text
Main Agent 持有控制权；
Child 只能获得委派能力；
Skill 不能扩大权限；
MCP 不能绕过 Tool Runtime；
Project Config 必须经过 Trust；
Thread 负责持续连接；
Turn 负责有限执行作用域；
Item 负责可恢复审计。
```
