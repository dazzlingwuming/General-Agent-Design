# General Agent Harness：阶段 3 权限、审批与 OS 原生沙箱详细设计

> 文档版本：v1.0  
> 文档日期：2026-07-11  
> 目标仓库：`https://github.com/dazzlingwuming/General-Agent-Design/tree/main/agent-harness`  
> 前置阶段：
> - 阶段 1：单 Agent Harness
> - 阶段 2：Subagent Runtime
> - Codex 式 `Thread → Turn → Item` 会话与持久化修复
>
> 当前阶段：阶段 3——Permission、Approval、Hooks 与 Sandbox  
> 文档用途：直接交给 Codex，作为阶段 3 的设计、实现、测试和验收依据  
> 核心结论：本阶段不强制使用 Docker；本地 Agent 优先采用操作系统级沙箱

---

# 一、阶段 3 的目标

阶段 3 要解决的问题不是“给 Tool 增加一个 `dangerous=true` 字段”，而是建立一套真正可执行的安全边界：

```text
模型提出动作
    ↓
Tool Runtime 接收 Tool Call
    ↓
Permission Engine 判断是否具有调用资格
    ↓
Rule Engine 判断 allow / ask / deny
    ↓
需要时创建 Approval Request
    ↓
用户批准、拒绝或增加窄范围规则
    ↓
Sandbox Runtime 在操作系统边界内执行
    ↓
记录 Tool、Permission、Approval、Sandbox Item 和 Trace
```

阶段 3 完成后，系统应支持：

```text
只读分析
工作区内自动修改
受控命令执行
网络默认关闭
越界访问阻断
危险操作审批
临时和持久规则
Root / Child 权限隔离
审批向 Root Thread 冒泡
进程超时和取消
无静默降级
完整审计
```

---

# 二、参考系统结论

本设计主要参考：

```text
OpenAI Codex
Anthropic Claude Code
OpenHands
Python / OS 原生进程机制
```

用户所说的“Color Code”未找到对应的成熟 Coding Agent 产品，本方案按语境理解为 **Claude Code**。

---

## 2.1 Codex

Codex 将安全控制明确分成两层：

```text
Sandbox：
决定命令在技术上能够访问哪些文件和网络。

Approval：
决定命令在什么情况下必须先停下来询问用户。
```

Codex 本地模式不是默认依赖 Docker，而是按平台使用 OS 原生机制：

```text
macOS：
Seatbelt

Linux / WSL2：
bubblewrap 加 Linux 沙箱辅助机制

Windows：
restricted token / 低权限用户、ACL、Firewall 等原生机制
```

常见 Sandbox Mode：

```text
read-only
workspace-write
danger-full-access
```

常见 Approval Policy：

```text
untrusted
on-request
never
```

默认低摩擦组合是：

```text
sandbox_mode = workspace-write
approval_policy = on-request
```

Codex 默认关闭命令网络访问；命令留在工作区和允许边界内时可自动运行，超出边界时进入审批。

Codex 的 Rules 用于控制沙箱外命令：

```text
allow
prompt
forbidden
```

多条规则同时匹配时采用更严格的结果：

```text
forbidden > prompt > allow
```

---

## 2.2 Claude Code

Claude Code 同样将 Permission 与 Sandbox 分开。

Permission 规则：

```text
deny
ask
allow
```

执行优先级：

```text
deny > ask > allow
```

权限规则由 Claude Code Runtime 强制执行，而不是由模型遵守 Prompt。

Claude Code 支持的模式包括：

```text
default / manual
acceptEdits
plan
auto
dontAsk
bypassPermissions
```

其中 `bypassPermissions` 仅建议在容器或虚拟机等已有外层隔离边界中使用。

Claude Code 的本地 Bash Sandbox：

```text
macOS：
Seatbelt

Linux / WSL2：
bubblewrap + socat，可选 seccomp

Native Windows：
不支持，建议在 WSL2 中运行
```

Sandbox 约束 Bash 命令及其所有子进程，而 Permission 规则在命令执行前判断本次调用是否允许。

Claude Code 还使用 `PreToolUse` Hook：

```text
允许
强制询问
拒绝
```

但是 Hook 不能绕过已有 deny / ask 规则。

---

## 2.3 OpenHands

OpenHands 选择 Docker Runtime，因为其目标包括：

```text
执行任意代码
不同用户和项目隔离
统一环境
资源控制
可复现性
远程 Runtime
```

Docker 对 OpenHands 很合理，但并不意味着本地 CLI Coding Agent 必须使用 Docker。

---

# 三、是否需要 Docker

## 3.1 当前结论

阶段 3 **不强制 Docker**。

当前项目是本地、交互式、单用户 Coding Agent，更接近 Codex CLI 和 Claude Code，而不是 OpenHands 的服务型任意代码平台。

推荐路线：

```text
主实现：
OSNativeSandbox

第一安全后端：
Linux / WSL2 BubblewrapSandbox

测试后端：
FakeSandbox

显式危险后端：
NoSandbox / FullAccess

后续可选：
WindowsNativeSandbox
MacOSSeatbeltSandbox
DockerSandbox
```

---

## 3.2 为什么第一版使用 WSL2 + bubblewrap

用户当前主要在 Windows 环境开发。

实现真正的原生 Windows 沙箱需要：

```text
Restricted Token
专用低权限用户
ACL 文件边界
Job Object
Firewall Rule
Private Desktop
环境和 Registry 隔离
```

Codex 已经为此维护独立的 Windows Sandbox Helper。不能只使用 Python 的路径检查和 `subprocess` 参数就声称实现了原生 Windows Sandbox。

因此，阶段 3 的可靠实现优先采用：

```text
Windows Host
    ↓
WSL2
    ↓
bubblewrap
    ↓
受限 Linux Process
```

Claude Code 官方也在 Windows 上要求使用 WSL2 来启用其 Bash Sandbox。

---

## 3.3 阶段 3 的平台支持等级

### Tier 1：正式安全支持

```text
Linux + bubblewrap
WSL2 + bubblewrap
```

### Tier 2：后续扩展

```text
macOS + Seatbelt
Native Windows + 独立 Sandbox Helper
```

### Tier 3：显式非安全模式

```text
NoSandboxBackend
```

只能在用户明确选择：

```text
danger-full-access
```

时启用。

---

## 3.4 禁止静默降级

如果配置要求：

```text
sandbox_required = true
```

但系统无法启动 bubblewrap：

```text
必须失败
```

不能：

```text
打印 Warning
然后直接在 Host 上执行
```

默认行为应为：

```text
fail closed
```

---

# 四、阶段 3 的范围

## 4.1 必须实现

```text
Permission Model
Permission Engine
Rule Engine
Approval Manager
Approval CLI
Tool Execution Principal
Tool Runtime 强制授权
Sandbox Policy
Sandbox Backend Protocol
Bubblewrap Sandbox
Fake Sandbox
NoSandbox Full Access
Structured run_command
write_file
apply_patch
delete_path（受审批）
PreToolUse / PostToolUse Hook
Root / Child 权限继承
Approval 冒泡
Network 默认关闭
Environment 清理
Timeout / Cancel / Process Tree Cleanup
Permission / Approval / Sandbox Rollout Items
Trace
配置文件
完整测试
```

---

## 4.2 暂不实现

```text
Docker Runtime
Kubernetes
远程 Sandbox
Native Windows Sandbox Helper
macOS Seatbelt Backend
域名级透明网络代理
TLS Credential Masking
自动审批 Agent
持久化进程级 Checkpoint Resume
企业 Managed Policy Server
容器镜像管理
多用户隔离
Computer Use
```

注意：

```text
Native Windows 和 Docker 不是永久取消，
而是不作为阶段 3 第一版的验收条件。
```

---

# 五、核心安全原则

## 5.1 Sandbox 与 Permission 必须分开

```text
Permission：
这次调用是否允许？

Approval：
是否需要用户确认？

Sandbox：
即使调用被允许，进程实际上能访问什么？
```

不能将三者合并成一个 `is_safe()`。

---

## 5.2 Prompt 不是安全边界

下面内容只能影响模型行为：

```text
“不要访问工作区外”
“不要运行危险命令”
“不要读取 Secret”
```

真正安全控制必须由 Runtime 实施。

---

## 5.3 所有副作用经过 Tool Runtime

禁止：

```text
AgentLoop 直接 subprocess
Tool 自己绕过 Permission Engine
Subagent 直接写文件
Hook 直接扩大权限
MCP 后续绕过 Approval
```

唯一执行链：

```text
ToolCall
→ ToolRuntime
→ PermissionEngine
→ ApprovalManager
→ HookManager
→ Sandbox / Executor
```

---

## 5.4 任何 deny 都不能被 allow 覆盖

全局规则：

```text
DENY > ASK > ALLOW
```

无论 deny 来自：

```text
系统硬规则
用户规则
项目规则
AgentDefinition
父 Agent 委派
Hook
Sandbox 能力
```

都不能被较低层级 allow 覆盖。

---

## 5.5 Child 只能缩小权限

有效 Child 权限：

```text
system ceiling
∩ thread profile
∩ parent effective permission
∩ delegated permission
∩ child AgentDefinition
∩ tool requirement
∩ sandbox capability
```

Child 不能通过 Tool 参数要求获得父 Agent 没有的权限。

---

## 5.6 审批必须最小授权

批准一次命令不等于：

```text
允许所有 Bash
允许所有网络
允许整个 Home
```

审批应尽量限定：

```text
单次 Tool Call
当前 Turn
当前 Thread
精确路径
精确命令 argv 前缀
精确网络能力
```

---

# 六、权限模式设计

项目采用 Codex 风格的两个正交配置。

---

## 6.1 Sandbox Mode

```text
read-only
workspace-write
danger-full-access
```

### read-only

```text
允许：
工作区读取
搜索
只读命令

禁止：
工作区写入
删除
网络
Host 越界
```

### workspace-write

```text
允许：
工作区读取
工作区写入
Session Temp 写入
受限命令执行

默认禁止：
网络
工作区外写入
Secret 读取
Host 配置修改
```

### danger-full-access

```text
无 OS Sandbox
保留 Tool Permission 和硬性阻断
```

该模式必须：

```text
显式 CLI Flag
显示醒目警告
不能由模型切换
不能由项目配置自动开启
```

---

## 6.2 Approval Policy

```text
untrusted
on-request
never
```

### untrusted

未命中明确 allow 的命令和副作用调用都询问。

适合：

```text
第一次使用
不可信仓库
高敏感项目
```

### on-request

沙箱边界内的操作自动执行。

需要越界、网络或高风险操作时询问。

这是默认模式。

### never

Runtime 不发起互动审批。

但含义不是自动允许：

```text
命中 DENY：
拒绝

需要 ASK：
拒绝

沙箱无法满足：
拒绝
```

适合自动化和 CI。

---

## 6.3 推荐 Preset

### Plan

```toml
sandbox_mode = "read-only"
approval_policy = "on-request"
```

### Auto

```toml
sandbox_mode = "workspace-write"
approval_policy = "on-request"
```

### Manual

```toml
sandbox_mode = "workspace-write"
approval_policy = "untrusted"
```

### Full Access

```toml
sandbox_mode = "danger-full-access"
approval_policy = "never"
```

---

# 七、能力模型

```python
class Capability(StrEnum):
    FILE_READ = "FILE_READ"
    FILE_WRITE = "FILE_WRITE"
    FILE_DELETE = "FILE_DELETE"

    COMMAND_EXECUTE = "COMMAND_EXECUTE"
    PACKAGE_INSTALL = "PACKAGE_INSTALL"

    NETWORK_ACCESS = "NETWORK_ACCESS"
    LOCAL_NETWORK_ACCESS = "LOCAL_NETWORK_ACCESS"

    SECRET_READ = "SECRET_READ"
    ENV_READ = "ENV_READ"

    GIT_WRITE = "GIT_WRITE"
    GIT_COMMIT = "GIT_COMMIT"
    GIT_PUSH = "GIT_PUSH"

    SUBAGENT_CREATE = "SUBAGENT_CREATE"
    MCP_TOOL_CALL = "MCP_TOOL_CALL"

    EXTERNAL_SIDE_EFFECT = "EXTERNAL_SIDE_EFFECT"
    SANDBOX_ESCAPE = "SANDBOX_ESCAPE"
```

---

## 7.1 Tool Definition 扩展

```python
class ToolDefinition:
    name: str
    description: str

    input_model: type[BaseModel]
    output_model: type[BaseModel] | None

    required_capabilities: frozenset[Capability]
    risk_level: RiskLevel
    side_effect: SideEffectType

    sandbox_requirement: SandboxRequirement
    approval_hint: ApprovalHint

    timeout_seconds: float
    executor: ToolExecutor
```

---

## 7.2 Risk Level

```text
READ_ONLY
LOW
MEDIUM
HIGH
CRITICAL
```

风险等级只用于：

```text
默认策略
UI 展示
审计
```

它不能代替精确规则。

---

# 八、Tool Execution Principal

每次 Tool 执行必须带调用主体：

```python
@dataclass(frozen=True)
class ToolExecutionPrincipal:
    session_id: str
    thread_id: str
    turn_id: str

    agent_id: str
    parent_agent_id: str | None
    depth: int

    allowed_tools: frozenset[str]
    capabilities: frozenset[Capability]

    sandbox_mode: SandboxMode
    approval_policy: ApprovalPolicy
```

ToolRuntime 必须检查：

```text
Tool 是否存在
Tool 是否在 allowed_tools
Tool 所需 capability 是否具备
规则结果
是否需要 Approval
Sandbox 是否可满足
```

---

# 九、Permission Rule

## 9.1 Rule Decision

```text
ALLOW
ASK
DENY
```

---

## 9.2 Rule 来源

```text
BUILTIN
MANAGED
USER
TRUSTED_PROJECT
SESSION
TURN_APPROVAL
AGENT_DEFINITION
PARENT_DELEGATION
HOOK
```

---

## 9.3 Rule Matcher

第一版支持：

```text
ToolNameMatcher
PathMatcher
CommandArgvPrefixMatcher
AgentMatcher
CapabilityMatcher
```

预留：

```text
DomainMatcher
MCPToolMatcher
```

---

## 9.4 示例

```toml
[[permissions.rules]]
decision = "deny"
tool = "read_file"
path = "**/.env"

[[permissions.rules]]
decision = "ask"
tool = "delete_path"
path = "src/**"

[[permissions.rules]]
decision = "allow"
tool = "run_command"
argv_prefix = ["pytest"]

[[permissions.rules]]
decision = "deny"
tool = "run_command"
argv_prefix = ["git", "push"]

[[permissions.rules]]
decision = "deny"
tool = "spawn_subagent"
agent_name = "security_reviewer"
```

---

## 9.5 匹配结果

任何匹配规则返回：

```text
DENY：
立即拒绝

ASK：
创建 Approval Request

ALLOW：
继续检查下一层安全边界
```

如果没有规则匹配，使用：

```text
Approval Policy
+
Tool Default Policy
+
Sandbox Mode
```

---

# 十、规则配置与信任

## 10.1 配置层

```text
Built-in Hard Rules
    ↓
User Config
    ↓
Trusted Project Config
    ↓
Session Temporary Grants
    ↓
Agent Restrictions
```

---

## 10.2 项目规则必须先信任

仓库内可以存在：

```text
.harness/permissions.toml
```

但在工作区未被用户信任前：

```text
不能加载其中的 allow 规则
```

原因：

```text
不可信仓库可以提交恶意权限配置。
```

未信任项目规则可以加载：

```text
deny
ask
```

但不能自动扩大权限。

---

## 10.3 持久规则写入位置

用户在审批界面选择：

```text
“以后都允许”
```

默认写入：

```text
用户配置
```

不能自动写入 Git 仓库。

---

# 十一、Permission Engine

```python
class PermissionEngine:
    def evaluate(
        self,
        principal: ToolExecutionPrincipal,
        tool: ToolDefinition,
        input: BaseModel,
        sandbox_policy: SandboxPolicy,
    ) -> PermissionEvaluation:
        ...
```

---

## 11.1 Evaluation 结果

```python
class PermissionEvaluation:
    decision: PermissionDecision
    reason: str
    matched_rules: list[str]

    required_approval: ApprovalSpec | None
    effective_capabilities: frozenset[Capability]

    sandbox_policy: SandboxPolicy
    escalation: PermissionEscalation | None
```

---

## 11.2 评估流程

```text
1. Built-in Hard Deny
2. Agent Tool Allowlist
3. Capability Intersection
4. Resolve Paths / Symlinks
5. Normalize Command argv
6. Evaluate DENY Rules
7. Evaluate ASK Rules
8. Evaluate ALLOW Rules
9. Check Sandbox Feasibility
10. Apply Approval Policy
11. Return ALLOW / ASK / DENY
```

---

# 十二、结构化命令执行

## 12.1 不接受任意 Shell 字符串

阶段 3 的 `run_command` 输入使用：

```python
class RunCommandInput(BaseModel):
    program: str
    args: list[str] = []
    cwd: str = "."
    timeout_seconds: float | None = None
    env: dict[str, str] = {}
```

运行：

```python
create_subprocess_exec(program, *args)
```

禁止：

```python
shell=True
```

---

## 12.2 为什么不直接复制 Claude 的 Bash 字符串规则

Claude Code 已经实现了：

```text
Shell Compound Command Parsing
PowerShell AST
Process Wrapper Canonicalization
Read-only Command Classification
```

当前项目没有成熟 Shell Parser。

如果直接使用字符串前缀：

```text
allow "git status"
```

攻击者可能通过：

```text
git status && dangerous-command
```

绕过。

因此第一版使用结构化 argv，拒绝管道、重定向和复合命令。

---

## 12.3 后续 Shell Tool

后续可以单独增加：

```text
run_shell
```

但必须在具有成熟 Parser 和专项安全测试后启用。

---

# 十三、内置 Tool 风险定义

## 13.1 read_file / list_files / search_text

```text
Capabilities：
FILE_READ

默认：
工作区内自动允许
Secret 路径拒绝
工作区外根据 Profile 决定
```

---

## 13.2 write_file

```text
Capabilities：
FILE_WRITE

默认：
workspace-write 内允许
受保护文件 ASK 或 DENY
```

保护路径：

```text
.git/**
.harness/**
.env
*.pem
*.key
pyproject lock / package lock 可按规则询问
```

---

## 13.3 apply_patch

```text
Capabilities：
FILE_WRITE
可能包含 FILE_DELETE

要求：
Patch 解析
路径边界
符号链接检查
写入前备份 / Diff Item
```

---

## 13.4 delete_path

```text
Capabilities：
FILE_DELETE

默认：
ASK

目录递归删除：
HIGH / CRITICAL
```

硬性阻断：

```text
workspace root
filesystem root
home root
.harness thread store
.git root（除明确支持操作）
```

---

## 13.5 run_command

```text
Capabilities：
COMMAND_EXECUTE

默认：
在 OS Sandbox 中执行
网络关闭
环境变量清理
```

---

## 13.6 package install

检测命令：

```text
pip install
uv add
poetry add
npm install
pnpm add
apt
dnf
brew
```

默认：

```text
PACKAGE_INSTALL
NETWORK_ACCESS
ASK
```

---

## 13.7 Git

```text
git status / diff / log：
READ_ONLY

git add：
GIT_WRITE

git commit：
GIT_COMMIT

git push：
GIT_PUSH + NETWORK_ACCESS
默认 ASK
```

---

# 十四、Approval Manager

## 14.1 Approval Request

```python
class ApprovalRequest:
    approval_id: str

    session_id: str
    thread_id: str
    turn_id: str

    agent_id: str
    tool_call_id: str
    tool_name: str

    reason: str
    risk_level: RiskLevel
    requested_capabilities: set[Capability]

    command_preview: list[str] | None
    path_preview: list[str]
    network_preview: list[str]

    requested_scope: ApprovalScope
    created_at: datetime
    status: ApprovalStatus
```

---

## 14.2 Approval Decision

```text
ALLOW_ONCE
ALLOW_TURN
ALLOW_THREAD
ALLOW_RULE
DENY_ONCE
DENY_RULE
CANCEL_TURN
```

---

## 14.3 Approval Scope

```text
TOOL_CALL
TURN
THREAD
PERSISTENT_USER_RULE
```

默认推荐：

```text
ALLOW_ONCE
```

---

## 14.4 Approval 生命周期

```text
ToolCall
    ↓
PermissionEvaluation = ASK
    ↓
Append approval.requested Item
    ↓
Turn 保持 IN_PROGRESS
    ↓
CLI 显示 Approval
    ↓
用户选择
    ↓
Append approval.decided Item
    ↓
重新评估原 Tool Call
    ↓
批准后执行同一个 Tool Call
```

---

## 14.5 幂等

审批后不得生成新的 Tool Call ID。

```text
同一 tool_call_id
+
同一 approval_id
```

只能执行一次。

---

## 14.6 Approval 与持久恢复

阶段 3 支持：

```text
当前进程内暂停和恢复
```

不支持：

```text
进程退出后恢复待审批 Tool Call
```

后者属于 Checkpoint 阶段。

如果进程在待审批期间退出：

```text
恢复 Thread 时将该 Turn 标记为 INTERRUPTED
```

---

# 十五、Subagent Approval 冒泡

Child Agent 触发审批时：

```text
Child Tool Call
    ↓
Child Permission Engine
    ↓
Approval Request
    ↓
Root Thread UI
```

Approval Request 必须显示：

```text
Child Agent Name
Child Agent ID
委派任务
具体 Tool
具体路径 / Command
风险
```

Child 不直接和用户交互。

---

## 15.1 Parent Ceiling

即使用户批准 Child 操作：

```text
也不能超过 Parent Effective Permission
```

如果 Child 请求父级没有的能力：

```text
DENY
```

而不是创建 Approval。

---

# 十六、Hook 设计

借鉴 Claude Code 的 PreToolUse Hook。

---

## 16.1 Hook 点

```text
PreToolUse
PermissionRequest
PreSandboxExec
PostSandboxExec
PostToolUse
ToolFailure
SandboxViolation
```

---

## 16.2 Hook Decision

```text
PASS
ASK
DENY
```

Hook 不能直接返回：

```text
ALLOW_OVERRIDE
```

原因：

```text
Hook 不能绕过已有 DENY。
```

---

## 16.3 Hook 优先级

```text
Built-in Hard Deny
    ↓
Rule Deny
    ↓
Hook Deny
    ↓
Rule Ask / Hook Ask
    ↓
Allow
```

---

## 16.4 Hook 配置

```toml
[[hooks.pre_tool_use]]
matcher = "run_command"
command = ["python", ".harness/hooks/check_command.py"]
timeout_seconds = 5
failure_mode = "deny"
```

---

## 16.5 Hook 安全

Hook 本身是用户代码。

要求：

```text
项目 Hook 只在 Workspace Trusted 后加载
Hook 超时
输出大小限制
失败默认 DENY
不能读取 API Key
不能直接执行目标 Tool
```

---

# 十七、Sandbox Policy

```python
class SandboxPolicy:
    mode: SandboxMode

    workspace_roots: list[Path]
    readable_roots: list[Path]
    writable_roots: list[Path]

    denied_read_paths: list[PathPattern]
    denied_write_paths: list[PathPattern]

    network_policy: NetworkPolicy

    env_allowlist: set[str]
    env_overrides: dict[str, str]

    temp_dir: Path
    process_limit: int
    timeout_seconds: float
```

---

# 十八、Sandbox Backend Protocol

```python
class SandboxBackend(Protocol):
    name: str

    async def check_available(self) -> SandboxAvailability:
        ...

    async def prepare(
        self,
        policy: SandboxPolicy,
        execution: SandboxExecutionRequest,
    ) -> PreparedSandboxExecution:
        ...

    async def execute(
        self,
        prepared: PreparedSandboxExecution,
    ) -> SandboxExecutionResult:
        ...

    async def terminate(
        self,
        execution_id: str,
    ) -> None:
        ...

    async def close(self) -> None:
        ...
```

---

# 十九、Bubblewrap Sandbox

## 19.1 依赖

```text
WSL2 / Linux
bubblewrap
```

可选：

```text
seccomp helper
```

---

## 19.2 基础命令边界

建议使用：

```text
bwrap
--die-with-parent
--new-session
--unshare-all
--unshare-net
```

并按需配置：

```text
/proc
/dev
/tmp
工作区
系统二进制和运行库
```

具体参数由实现和平台测试决定，不能直接字符串拼接用户参数。

---

## 19.3 Filesystem

### workspace-write

```text
Workspace：
read-write bind

System Runtime：
read-only bind

Session Temp：
writable tmpfs / bind

Home：
空目录或合成目录
```

### read-only

```text
Workspace：
read-only bind

Temp：
writable

其他：
read-only runtime roots
```

---

## 19.4 System Runtime Roots

为了执行 Python、Git、Pytest，需要只读挂载：

```text
/usr
/bin
/lib
/lib64
/etc 中必要文件
Python / Conda Environment
Git executable
```

不要将整个 Home 可读地挂入 Sandbox。

---

## 19.5 Network

默认：

```text
--unshare-net
```

即完全关闭命令网络。

第一版支持：

```text
NetworkPolicy.NONE
NetworkPolicy.FULL_AFTER_APPROVAL
```

`FULL_AFTER_APPROVAL` 可以使用共享 Host Network，但必须经过明确审批。

---

## 19.6 域名 Allowlist

成熟的域名 Allowlist 需要：

```text
Network Namespace
外部 Proxy
阻断直接连接
DNS / IP 检查
```

本阶段保留接口，但不把简单环境变量代理冒充安全边界。

---

## 19.7 Environment

默认只传递：

```text
PATH（重建）
LANG
LC_ALL
TERM
HOME（Sandbox Home）
TMPDIR
必要 Python 环境
```

不传递：

```text
DEEPSEEK_API_KEY
OPENAI_API_KEY
AWS_*
GITHUB_TOKEN
SSH_AUTH_SOCK
数据库密码
完整 Host Environment
```

---

## 19.8 Process Control

必须实现：

```text
新 Process Group
Timeout
取消
Kill Entire Process Tree
stdout / stderr 上限
进程数量限制（可用时）
```

---

# 二十、NoSandbox Backend

`NoSandboxBackend` 只用于：

```text
单元测试
显式 Full Access
平台诊断
```

不得在：

```text
workspace-write
read-only
```

模式中自动选择。

---

# 二十一、原生 Windows 后端方向

Native Windows 后端不在阶段 3 第一验收范围，但设计保留。

推荐单独实现一个：

```text
Rust / C++ Sandbox Helper
```

而不是在主 Python 进程里拼接 ACL 命令。

需要：

```text
Elevated Backend：
专用低权限 Sandbox User
ACL
Firewall
Local Policy
Job Object
Private Desktop

Unelevated Backend：
Restricted Token
ACL
Job Object
较弱 Network Isolation
```

只有当该 Helper 通过专项安全测试后，才能标记为：

```text
WindowsNativeSandbox = secure
```

---

# 二十二、File Tool 与 Sandbox 的关系

OS Sandbox 主要约束：

```text
run_command 及其子进程
```

内置 Python Tool：

```text
read_file
write_file
apply_patch
delete_path
```

运行在 Harness 主进程中，不能依赖 Bubblewrap 自动限制。

因此它们必须使用同一套：

```text
FileSystemPolicy
Path Resolver
Permission Engine
Symlink Resolver
Protected Path Rules
```

---

# 二十三、Path Policy

## 23.1 路径检查

```text
拒绝绝对路径（除内部已解析）
规范化
resolve symlink
检查原路径
检查最终目标
检查每个父目录
应用 deny / allow
```

---

## 23.2 Symlink

Allow Rule：

```text
链接路径和最终目标都必须允许
```

Deny Rule：

```text
链接路径或最终目标任意一个命中即拒绝
```

---

## 23.3 Protected Paths

默认 DENY：

```text
.env
**/.env
**/*.pem
**/*.key
~/.ssh/**
~/.aws/**
~/.config/gcloud/**
.harness/threads/**
```

默认 ASK：

```text
.git/**
package lock files
CI 配置
deployment 配置
```

---

# 二十四、Thread / Turn / Item 集成

新增 Rollout Item：

```text
permission.evaluated
permission.denied

approval.requested
approval.decided

sandbox.prepared
sandbox.started
sandbox.completed
sandbox.failed
sandbox.violation

hook.started
hook.completed
hook.failed

file.changed
command.started
command.completed
```

---

## 24.1 Turn 状态

等待审批时：

```text
Turn 仍为 IN_PROGRESS
```

不需要增加：

```text
Turn.WAITING_APPROVAL
```

审批状态由当前：

```text
Approval Item
```

表示。

---

## 24.2 Trace 字段

```text
session_id
thread_id
turn_id
item_id

agent_id
parent_agent_id

tool_call_id
approval_id
sandbox_execution_id

permission_decision
matched_rules
sandbox_backend
sandbox_mode
```

---

# 二十五、配置示例

```toml
[security]
sandbox_mode = "workspace-write"
approval_policy = "on-request"
sandbox_required = true

[security.workspace]
writable_roots = ["."]
readable_roots = ["."]

[security.network]
mode = "none"

[security.environment]
allow = ["PATH", "LANG", "LC_ALL", "TERM"]
deny_patterns = ["*_TOKEN", "*_KEY", "*_SECRET", "*PASSWORD*"]

[security.sandbox]
backend = "auto"
fail_if_unavailable = true
default_timeout_seconds = 120
max_output_chars = 50000

[security.approval]
default_scope = "tool_call"
allow_persistent_rules = true

[[permissions.rules]]
decision = "deny"
tool = "read_file"
path = "**/.env"

[[permissions.rules]]
decision = "ask"
tool = "delete_path"
path = "**"

[[permissions.rules]]
decision = "allow"
tool = "run_command"
argv_prefix = ["pytest"]

[[permissions.rules]]
decision = "ask"
tool = "run_command"
argv_prefix = ["git", "commit"]

[[permissions.rules]]
decision = "deny"
tool = "run_command"
argv_prefix = ["git", "push"]
```

---

# 二十六、CLI

新增命令：

```text
/permissions
/sandbox
/approvals
```

---

## 26.1 /permissions

展示：

```text
Active Preset
Sandbox Mode
Approval Policy
Rules
Temporary Grants
Agent Effective Permissions
```

允许用户切换：

```text
Plan
Auto
Manual
Full Access
```

Full Access 必须二次确认。

---

## 26.2 /sandbox

展示：

```text
Backend
Availability
Workspace Roots
Network
Environment
Temp
Fail Closed
```

---

## 26.3 Approval UI

示例：

```text
Explorer Agent 请求执行：

pytest tests/unit/test_auth.py

工作目录：
D:\project

Sandbox：
workspace-write / network-off

原因：
验证登录修复

风险：
LOW

[1] 允许一次
[2] 当前 Turn 允许 pytest *
[3] 当前 Thread 允许 pytest *
[4] 永久允许精确规则
[5] 拒绝
[6] 取消当前 Turn
```

---

# 二十七、推荐代码目录

```text
src/agent_harness/
├── security/
│   ├── capabilities.py
│   ├── principal.py
│   ├── profiles.py
│   ├── rules.py
│   ├── matchers.py
│   ├── permission_engine.py
│   ├── approval.py
│   ├── hooks.py
│   └── protected_paths.py
│
├── sandbox/
│   ├── base.py
│   ├── manager.py
│   ├── policy.py
│   ├── fake.py
│   ├── none.py
│   └── bubblewrap.py
│
├── tools/
│   ├── runtime.py
│   ├── execution_context.py
│   └── builtins/
│       ├── write_file.py
│       ├── apply_patch.py
│       ├── delete_path.py
│       └── run_command.py
│
├── hooks/
│   ├── runner.py
│   └── models.py
│
└── cli/
    ├── permissions_ui.py
    └── approval_ui.py
```

不要求机械拆分文件，但职责必须分离。

---

# 二十八、实施顺序

## Step 1：Security Domain Model

实现：

```text
Capability
RiskLevel
SandboxMode
ApprovalPolicy
Rule
Principal
PermissionEvaluation
ApprovalRequest
SandboxPolicy
```

---

## Step 2：Tool Runtime 强制 Principal

修改所有 Tool 执行路径：

```text
没有 Principal：
拒绝执行
```

先修复现有：

```text
只隐藏 Tool Schema、执行时未检查 allowed_tools
```

的问题。

---

## Step 3：Permission Rule Engine

完成：

```text
deny > ask > allow
Tool Matcher
Path Matcher
Argv Prefix Matcher
Agent Matcher
规则来源
Trusted Project Rule
```

---

## Step 4：Approval Manager

完成：

```text
请求
CLI 决策
临时 Grant
持久 Rule
Item / Trace
幂等
```

先用 Fake Executor 测试。

---

## Step 5：File Write Tools

实现：

```text
write_file
apply_patch
delete_path
file.changed Item
```

全部经过 Permission Engine 和 Path Policy。

---

## Step 6：Structured run_command

实现：

```text
argv
cwd
env
timeout
stdout/stderr
process group
```

暂不接 Sandbox，先用 FakeSandbox 测试完整调用链。

---

## Step 7：Bubblewrap Backend

完成：

```text
Availability
Policy Compilation
read-only
workspace-write
network none
env sanitize
timeout
cancel
process tree
```

---

## Step 8：Subagent Permission

完成：

```text
Parent Ceiling
Delegated Permissions
Child Effective Principal
Approval Bubble
```

---

## Step 9：Hooks

完成：

```text
PreToolUse
PermissionRequest
PostToolUse
fail closed
```

---

## Step 10：CLI Profiles

完成：

```text
/permissions
/sandbox
Approval UI
Plan / Auto / Manual / Full Access
```

---

## Step 11：Thread / Turn / Item

将 Permission、Approval、Sandbox 和 File Change 写入 Rollout。

---

## Step 12：安全测试与回归

全部通过后才能进入阶段 4。

---

# 二十九、测试要求

## 29.1 Permission

- [ ] Tool 不在 Agent allowlist 时拒绝；
- [ ] Child 不能获得 Parent 没有的能力；
- [ ] DENY 覆盖 ASK 和 ALLOW；
- [ ] ASK 覆盖 ALLOW；
- [ ] 没有匹配规则时使用 Policy；
- [ ] 项目未信任时不加载 allow；
- [ ] 模型不能切换 Full Access；
- [ ] Hook 不能覆盖 DENY。

---

## 29.2 Path

- [ ] `../` 逃逸拒绝；
- [ ] 绝对路径越界拒绝；
- [ ] Symlink 越界拒绝；
- [ ] Symlink 指向 Secret 拒绝；
- [ ] Workspace 内写入允许；
- [ ] Workspace 外写入 ASK；
- [ ] `.env` 拒绝；
- [ ] `.harness/threads` 拒绝；
- [ ] 删除 Workspace Root 拒绝。

---

## 29.3 Command

- [ ] `shell=True` 不存在；
- [ ] argv Prefix 精确匹配；
- [ ] `pytest` Allow 生效；
- [ ] `git push` Deny 生效；
- [ ] 复合命令无法通过 args 绕过；
- [ ] cwd 越界拒绝；
- [ ] env Secret 不传入；
- [ ] timeout 终止完整进程树；
- [ ] cancel 终止完整进程树；
- [ ] stdout/stderr 截断。

---

## 29.4 Sandbox

- [ ] read-only 无法写 Workspace；
- [ ] workspace-write 可以写 Workspace；
- [ ] 无法写工作区外；
- [ ] 无法读取 denied path；
- [ ] 网络默认不可达；
- [ ] Host Home 不可见或受限；
- [ ] API Key 不存在于 Env；
- [ ] Sandbox 不可用时 fail closed；
- [ ] 不会静默选择 NoSandbox；
- [ ] 子进程继承边界。

---

## 29.5 Approval

- [ ] Allow Once 只执行一次；
- [ ] Allow Turn 只在当前 Turn 生效；
- [ ] Allow Thread 不跨 Thread；
- [ ] Persistent Rule 写入用户配置；
- [ ] Deny 不执行；
- [ ] Never Policy 下 ASK 直接拒绝；
- [ ] 同一 Tool Call 不重复执行；
- [ ] Child Approval 显示 Agent 信息；
- [ ] 用户取消 Approval 后 Turn 可终止；
- [ ] 进程退出后 Pending Approval 恢复为 Interrupted。

---

## 29.6 Hooks

- [ ] PASS；
- [ ] ASK；
- [ ] DENY；
- [ ] Timeout；
- [ ] Invalid Output；
- [ ] Hook 失败默认 DENY；
- [ ] Project Hook 未信任不加载；
- [ ] Hook 不接收 Secret Env。

---

## 29.7 Rollout / Trace

- [ ] permission.evaluated；
- [ ] approval.requested；
- [ ] approval.decided；
- [ ] sandbox.started；
- [ ] sandbox.completed；
- [ ] file.changed；
- [ ] Agent / Turn / Tool 关联正确；
- [ ] Secret 不进入 Trace；
- [ ] Sequence 单调。

---

# 三十、阶段 3 验收标准

## 架构

- [ ] Permission、Approval、Sandbox 三层分离；
- [ ] 所有副作用经过 Tool Runtime；
- [ ] ToolRuntime 强制 Principal；
- [ ] Child 权限只能缩小；
- [ ] Prompt 不承担安全强制；
- [ ] Sandbox Backend 可替换。

## Permission

- [ ] deny > ask > allow；
- [ ] 规则具有明确来源和作用域；
- [ ] 项目 allow 需要 Trust；
- [ ] Full Access 只能用户显式开启；
- [ ] Tool allowlist 在执行时生效。

## Approval

- [ ] Tool Call 可暂停等待用户；
- [ ] 支持 once / turn / thread / persistent；
- [ ] Child Approval 向 Root 冒泡；
- [ ] 审批后同一 Tool Call 幂等恢复；
- [ ] 审批完整写入 Rollout。

## Sandbox

- [ ] Linux / WSL2 Bubblewrap 正常工作；
- [ ] read-only 和 workspace-write 有真实 OS 边界；
- [ ] 网络默认关闭；
- [ ] 环境变量默认清理；
- [ ] 超时和取消清理进程树；
- [ ] Sandbox 不可用时默认失败；
- [ ] Docker 不是强制依赖。

## Tools

- [ ] write_file；
- [ ] apply_patch；
- [ ] delete_path；
- [ ] structured run_command；
- [ ] 文件和命令均受权限与 Sandbox 控制。

## 工程质量

- [ ] 所有旧测试通过；
- [ ] 新增安全测试通过；
- [ ] Linux / WSL2 Integration Test 通过；
- [ ] GitHub Actions 至少在 Linux 执行；
- [ ] Ruff 通过；
- [ ] 类型检查通过；
- [ ] README 写明安全边界和平台支持。

---

# 三十一、阶段 3 完成后暂不声称的能力

即使阶段 3 验收通过，也不能声称：

```text
原生 Windows 已安全隔离
域名级网络代理已完成
Secret 自动安全注入
任意不可信代码绝对安全
多用户强隔离
容器级资源配额
进程崩溃后精确恢复审批
```

应准确描述为：

> 已实现面向本地交互式 Coding Agent 的权限、人工审批与 Linux/WSL2 OS 原生进程沙箱。默认网络关闭，工作区写入受限，所有副作用经过统一 Tool Runtime。

---

# 三十二、给 Codex 的实施约束

Codex 正式编码前先输出：

```text
1. 当前代码与阶段 3 目标的差异；
2. 新增和修改文件列表；
3. Permission Evaluation 流程图；
4. Rule Schema；
5. Approval 状态机；
6. Sandbox Backend 接口；
7. Bubblewrap 命令构造方案；
8. Process Tree 终止方案；
9. Thread / Turn / Item 新增类型；
10. 测试矩阵；
11. 与本文档不同的设计及理由。
```

必须遵守：

```text
1. 不强制加入 Docker；
2. 不用路径检查冒充 OS Sandbox；
3. 不静默 Unsandboxed Fallback；
4. 不使用 shell=True；
5. 不允许模型控制 Full Access；
6. 不允许 Child 扩大权限；
7. 不允许 Hook 覆盖 DENY；
8. 不把 Approval 等同于全局授权；
9. 不提前实现 MCP、Memory 和 Skill；
10. 不为赶进度跳过真实 Sandbox Integration Test。
```

---

# 三十三、官方参考资料

## OpenAI Codex

- Agent approvals & security  
  https://developers.openai.com/codex/agent-approvals-security

- Sandbox  
  https://developers.openai.com/codex/sandboxing

- Rules  
  https://developers.openai.com/codex/agent-configuration/rules

- Windows Sandbox  
  https://developers.openai.com/codex/windows/windows-sandbox

- Open-source sandbox manager  
  https://github.com/openai/codex/blob/main/codex-rs/sandboxing/src/manager.rs

## Anthropic Claude Code

- Permissions  
  https://code.claude.com/docs/en/permissions

- Sandboxed Bash  
  https://code.claude.com/docs/en/sandboxing

- Hooks  
  https://code.claude.com/docs/en/hooks-guide

## OpenHands

- Runtime Architecture  
  https://docs.openhands.dev/openhands/usage/architecture/runtime

---

# 三十四、最终结论

阶段 3 不采用“Docker 是唯一沙箱”的方案。

采用的是 Codex / Claude Code 风格：

```text
Permission Rules
+
Approval Policy
+
OS-native Sandbox
+
Hooks
```

第一安全实现：

```text
Linux / WSL2
+
bubblewrap
+
network off
+
workspace boundary
+
sanitized environment
```

Docker 保留为未来可插拔后端，用于：

```text
远程执行
不可信任意代码
多用户隔离
环境复现
资源配额
```

当前项目的默认体验应为：

```text
workspace-write
+
on-request
+
Bubblewrap Sandbox
+
No Network
```

这样既能像 Codex 一样在工作区内自动读取、修改和运行测试，又不会把整个宿主机权限直接交给模型。
