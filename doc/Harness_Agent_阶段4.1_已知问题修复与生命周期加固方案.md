# General Agent Harness：阶段 4.1 已知问题修复与生命周期加固方案

> 版本：v1.0  
> 日期：2026-07-11  
> 目标仓库：`dazzlingwuming/General-Agent-Design/agent-harness`  
> 对应提交：`ed3de011750d2e789e92c4d5420837b2aed94730`  
> 用途：直接交给 Codex，先修复阶段 4 已知问题，再进入阶段 5 MCP。  
> 说明：保留现有 Guidance、Trust、Skill Catalog、Skill Snapshot、Resource 和 Fork 主体实现；不修改沙箱路线。

---

# 一、当前判断

阶段 4 的问题主要不是缺少功能，而是以下生命周期和作用域没有完全统一：

1. 用户 `$skill` 与模型 `activate_skill` 没有走同一条执行管线；
2. `Skill 已加载` 与 `Skill 正在执行` 被混成同一个状态；
3. `allowed-tools` 错误地永久收窄 Main Agent 后续所有 Turn；
4. 用户专用 Skill 激活后无法正常读取 Resource；
5. Guidance 与 Skills 初始化状态相互耦合；
6. Git Project Root 与启动 CWD 没有正确分开；
7. Untrusted Guidance 仍可能占用有效预算；
8. Skill 扫描限制发生在递归遍历之后；
9. Trust 与 Snapshot 直接覆盖写入，缺少原子替换。

如果这些问题直接带入 MCP，MCP 也会重复出现“发现、显式调用、模型调用、权限作用域和跨 Turn 生命周期不一致”的问题。因此应先完成一个独立的阶段 4.1。

---

# 二、成熟系统提供的依据

## 2.1 Codex：Project Root 与 CWD 分离

Codex 从 Project Root（通常是 Git Root）向下遍历到当前工作目录；每层依次检查 `AGENTS.override.md`、`AGENTS.md` 和 fallback 文件，并且每层最多加载一个。Runtime 因此必须区分：

```text
project_root
workspace_root
cwd
```

不能把一个 `workspace` 同时当作三者。

官方资料：

- https://developers.openai.com/codex/guides/agents-md/

## 2.2 Claude Code：用户调用和模型调用执行同一个 Skill

Claude Code 的 `disable-model-invocation` 与 `user-invocable` 只控制“谁能发起调用”。一旦 Skill 被合法调用，`context: fork` 就应创建隔离 Subagent，无论调用来自用户还是模型。

官方资料：

- https://code.claude.com/docs/en/skills

## 2.3 Claude Code：Fork Skill 的工具范围属于 Child

`context: fork` 会创建独立上下文，Skill 内容成为 Child 的任务，`agent` 决定 Child 的模型、工具和权限环境。Fork Skill 的工具约束不应反向永久影响 Parent。

## 2.4 Skill 工具限制必须有明确期限

Claude Code 的工具限制只在 Skill Active 期间有效，部分限制会在下一条用户消息时清除。当前 Harness 可以继续采用更保守的“只收窄、不预批准”语义，但必须明确：

```text
Inline Skill：仅当前 Root Turn。
Fork Skill：仅当前 Child Turn。
```

不能作用于整个 Thread。

## 2.5 Agent Skills：Activation 与 Resource 是两个阶段

开放标准采用：

```text
Catalog → Instructions → Resources
```

Resource Access 的依据应该是“已经存在合法 Activation，并且资源位于该 Activation Manifest”，而不是重新检查 Skill 当初是否允许模型发起调用。

官方资料：

- https://agentskills.io/specification
- https://agentskills.io/integrate-skills

## 2.6 Python：扫描前剪枝与原子替换

Python 官方 `os.walk(topdown=True)` 支持在进入目录前修改 `dirnames`，适合真正限制扫描范围。`os.replace()` 可以用同目录临时文件原子替换目标文件。

官方资料：

- https://docs.python.org/3/library/os.html#os.walk
- https://docs.python.org/3/library/os.html#os.replace

---

# 三、修复后的核心模型

必须区分：

```text
SkillRecord
    ↓
SkillActivation
    ↓
SkillExecution
```

## 3.1 SkillRecord

表示被发现的 Skill 定义，跟随 Catalog Snapshot，包含：

```text
name
description
scope
SKILL.md path
frontmatter
allowed-tools
context mode
agent
resource manifest
trusted
enabled
```

它不表示 Skill 已经执行。

## 3.2 SkillActivation

表示 Skill 内容已经被加载到当前 Thread，可跨 Turn 保留：

```text
activation_id
skill_id
rendered instructions
arguments
content hash
resource manifest
source path
activated_turn_id
```

它负责 Context、Resume 和 Resource Access，但不直接修改 Main Agent Tool。

## 3.3 SkillExecution

表示 Skill 当前正在执行：

```text
execution_id
activation_id
invocation_source
root_turn_id
child_agent_id
child_turn_id
status
effective_tools
started_at
completed_at
result
error
```

生命周期：

```text
Inline：调用开始 → 当前 Root Turn 结束
Fork：Child Turn 创建 → Child 成功/失败/取消
```

只有 SkillExecution 可以影响 Tool Scope。

## 3.4 调用来源

```python
class SkillInvocationSource(StrEnum):
    USER_EXPLICIT = "user_explicit"
    MODEL_TOOL = "model_tool"
    SUBAGENT_PRELOAD = "subagent_preload"
    SYSTEM = "system"
```

调用来源只用于入口权限、审计和 UI，不应产生两套执行实现。

---

# 四、统一 Skill 调用管线

新增：

```python
class SkillInvocationService:
    async def invoke(
        self,
        request: SkillInvocationRequest,
        runtime: SkillRuntimeContext,
    ) -> SkillInvocationResult:
        ...
```

请求：

```python
@dataclass(frozen=True)
class SkillInvocationRequest:
    name: str
    arguments: str
    source: SkillInvocationSource
    thread_id: str
    turn_id: str
    parent_agent_id: str
    tool_call_id: str | None = None
```

统一流程：

```text
resolve_for_invocation
    ↓
检查 user-invocable / disable-model-invocation
    ↓
创建或复用 Activation
    ↓
创建 SkillExecution
    ↓
Inline 或 Fork
    ↓
统一 Audit、Snapshot 和 Result
```

两个入口只负责适配：

```text
模型 activate_skill Tool
→ SkillInvocationService.invoke(MODEL_TOOL)

用户 $skill
→ SkillInvocationService.invoke(USER_EXPLICIT)
```

禁止两边各自实现 resolve、activate、fork 和 audit。

---

# 五、修复 P0-1：用户 `$fork-skill` 没有真正 Fork

## 当前原因

`ConversationSession._activate_explicit_skill()` 只调用 `SkillManager.activate()`，不处理 `context_mode == "fork"`。

## 目标行为

用户输入：

```text
$code-review-fork 检查价格逻辑
```

应执行：

```text
记录原始 user_message
→ skill.invocation_requested
→ 创建/复用 Activation
→ 创建 Reviewer Child
→ 等待 submit_result
→ skill.completed
→ Main Agent 基于结构化结果生成最终回复
```

原始 `$skill` 文本必须进入 Rollout，不能只保存展开后的参数。

## 验收测试

- 用户 `$code-review-fork` 产生 `agent.spawned`；
- reviewer 真正运行；
- Child 调用 `submit_result`；
- Root 再调用一次模型生成最终回答；
- Rollout 保留原始 `$skill` 文本和 invocation source。

---

# 六、修复 P0-2：allowed-tools 永久收窄 Main Agent

## 当前原因

当前 `_effective_skill_tools()` 遍历所有 `skill_manager.active`。Activation 跨 Turn 保留，因此完成后的 Skill 仍永久影响 Tool。

## 正确语义

### Inline Skill

```text
effective_tools =
Agent Tools
∩ 当前 Turn 所有 ACTIVE Inline SkillExecution 的 allowed-tools
```

当前 Turn 完成后，Tool Restriction 清除；Skill Instructions 可以继续留在 Context。

### Fork Skill

```text
Child Tools =
Parent Delegatable Tools
∩ Child AgentDefinition Tools
∩ Skill allowed-tools
∩ Permission Engine
```

Parent Tools 不变化。

## 修改建议

用：

```python
SkillExecutionRegistry.effective_tools_for(
    turn_id,
    agent_id,
    base_tools,
)
```

替代遍历全部 Activation。

## 验收测试

- Fork Skill 只收窄 Child；
- Parent 在同一 Turn 仍可调用 `wait_subagents`；
- Parent 下一 Turn 仍可使用 `write_file`、`activate_skill` 等；
- Inline Skill 当前 Turn 收窄；
- 下一 Turn 恢复；
- 多个 Inline Skill 取交集。

---

# 七、修复 P0-3：用户专用 Skill 无法读取 Resource

## 当前原因

`read_skill_resource` 再次调用 `manager.resolve()`，被当成模型发起 Skill 调用。对 `disable-model-invocation: true` 的 Skill 会错误拒绝。

## 正确授权

Resource Tool 不应重新执行 Invocation Gate，只应验证：

```text
Activation 存在；
Activation 属于当前 Thread；
Resource 位于 Activation Manifest；
真实路径没有逃逸；
文件符合大小与 Hash。
```

推荐输入：

```python
class ReadSkillResourceInput(BaseModel):
    activation_id: str
    path: str
```

如保留 Skill Name，也应先解析“当前 Thread 最近有效 Activation”，而不是重新按模型调用权限判断。

## Tool 注册条件

- `activate_skill`：存在 model-invocable Skill 时注册；
- `read_skill_resource`：Skills 子系统启用且存在任意 SkillRecord 或 Active Activation 时注册。

## 验收测试

- 项目只有一个 `disable-model-invocation` Skill；
- Catalog 对模型为空；
- 用户 `$skill` 成功激活；
- `read_skill_resource` 已注册；
- 模型可读取合法 Resource；
- 模型仍不能主动调用该 Skill。

---

# 八、修复 P0-4：Guidance 与 Skills 初始化耦合

## 当前原因

当前条件：

```python
if guidance_snapshot is None or skill_manager is None:
    initialize_project_context()
```

任一子系统关闭，另一个会每 Turn 重复初始化。

## 修复模型

```python
class SubsystemInitState(StrEnum):
    NOT_INITIALIZED = "not_initialized"
    INITIALIZED = "initialized"
    DISABLED = "disabled"
    FAILED = "failed"
```

Thread Runtime 保存：

```text
guidance_init_state
skills_init_state
```

拆分：

```python
initialize_guidance()
initialize_skills()
reload_guidance()
reload_skills()
```

## 规则

- Guidance Disabled：状态设为 DISABLED，不触发重复初始化；
- Skills Disabled：同理；
- `/guidance reload` 不改变 SkillManager 和 Active Activation；
- `/skills reload` 不改变 Guidance Snapshot；
- Skills Reload 重新发现 Catalog，但保留 Activation Snapshot。

## 验收测试

覆盖四种组合：

```text
Guidance On / Skills On
Guidance On / Skills Off
Guidance Off / Skills On
Guidance Off / Skills Off
```

并验证连续多个 Turn 不重复扫描、不丢失 Activation。

---

# 九、修复 P0-5：Project Root 与 CWD 模型错误

## 目标字段

```python
@dataclass(frozen=True)
class ProjectPaths:
    project_root: Path
    workspace_root: Path
    cwd: Path
```

语义：

```text
project_root：Git Root，用于 Guidance、Project Skill 和项目身份；
cwd：用户启动目录，是 Guidance 链终点和 Tool 默认 CWD；
workspace_root：Tool 可访问边界。
```

阶段 4.1 推荐默认：

```text
project_root = Git Root
workspace_root = Git Root
cwd = 启动目录
```

## Root 解析

```text
git -C <cwd> rev-parse --show-toplevel
```

成功后应验证：

```python
cwd.resolve().relative_to(project_root.resolve())
```

当前代码方向相反，需要修正。

## Guidance

从：

```text
project_root → cwd
```

逐层发现。

## Project Skill

只检查作用域链每层的：

```text
.agents/skills
```

不要对整个仓库 `rglob(".agents/skills")`，否则会加载无关兄弟目录。

例如从 `apps/web` 启动，应加载：

```text
repo/.agents/skills
repo/apps/.agents/skills
repo/apps/web/.agents/skills
```

不加载：

```text
repo/apps/mobile/.agents/skills
```

## 验收测试

构造 Root、apps、web 和 mobile 的 Guidance/Skills，确认从 web 启动时只加载 Root → web 链。

---

# 十、修复 P1-1：Untrusted Guidance 不应占用预算

当前应调整为：

```text
发现和解析
→ 记录 Diagnostic
→ Trust Gate
→ Eligible Documents
→ Budget Accounting
→ Snapshot
```

不可信 Project Guidance 和 Path Rule可以记录为 skipped，但不能增加 `total_bytes`，不能挤掉可信 User/Admin Guidance。

测试：

```text
Untrusted Project AGENTS.md = 40 KiB
User AGENTS.md = 1 KiB
Budget = 32 KiB
```

结果必须加载 User Guidance，Snapshot `total_bytes` 只计算可信内容。

---

# 十一、修复 P1-2：Skill 扫描要在递归前剪枝

## 当前问题

`rglob` 已经遍历后再检查深度和目录数，限制无法阻止大仓库扫描。

## 实现

使用：

```python
os.walk(root, topdown=True, followlinks=False)
```

在进入子目录前修改 `dirnames[:]`。

默认忽略：

```text
.git
.hg
.svn
node_modules
.venv
venv
__pycache__
build
dist
target
coverage
.next
.cache
.harness
```

Project Scope 优先使用 Root → CWD 目录链，不需要扫描整个仓库。User/Admin Roots 才使用受限递归。

测试应在 `node_modules` 放大量 SKILL.md，证明 Discovery 没有进入该目录。

---

# 十二、修复 P1-3：Metadata-only 需要真正 I/O Lazy

当前 Discovery 读取完整 `SKILL.md`。应新增 Frontmatter-only Reader：

```python
read_skill_frontmatter(
    path,
    max_frontmatter_bytes,
    max_skill_file_bytes,
)
```

流程：

```text
stat 大小
→ 检查 max_skill_file_bytes
→ 流式读取首个 Frontmatter
→ 找到第二个 ---
→ 停止读取
```

只有 Skill 激活时才读取 Body。

建议配置：

```toml
max_skill_file_bytes = 1048576
max_frontmatter_bytes = 16384
max_skill_body_bytes = 524288
max_resource_files_per_skill = 200
```

Resource Manifest 只读取路径、类型、大小和可选 Hash，不读取正文。

---

# 十三、修复 P1-4：Trust 与 Snapshot 使用原子写

新增统一方法：

```python
def atomic_write_text(path: Path, content: str) -> None:
    ...
```

步骤：

```text
父目录创建
→ 同目录临时文件
→ 写入
→ flush
→ 可选 fsync(file)
→ os.replace(temp, target)
→ 清理临时文件
```

再封装：

```python
atomic_write_json(path, value)
```

替换：

- `workspace-trust.json`；
- Guidance Snapshot；
- Skill Activation Snapshot；
- 其他关键 Thread Metadata。

WorkspaceTrustStore 至少增加进程内 Lock，防止同一进程多个 Thread 同时覆盖。

测试需要模拟写入异常，证明旧文件不会损坏。

---

# 十四、Resource Snapshot 一致性

Activation Snapshot 虽可恢复 Instructions，但 Resource 仍依赖当前源目录。建议 Manifest 增加 Hash：

```text
relative_path
type
size
content_hash
```

读取时判断：

```text
AVAILABLE
MISSING
CHANGED
```

如果文件变化，默认拒绝或要求重新激活，不能静默读取与历史 Snapshot 不一致的新内容。

---

# 十五、建议的代码结构

```text
src/agent_harness/
├── skills/
│   ├── invocation.py
│   ├── execution.py
│   ├── activation.py
│   ├── control_tools.py
│   ├── resources.py
│   ├── discovery.py
│   └── parser.py
├── guidance/
│   ├── discovery.py
│   ├── runtime.py
│   └── trust.py
├── runtime/
│   ├── project_context.py
│   ├── run_manager.py
│   └── session.py
├── project/
│   └── roots.py
└── utils/
    └── atomic_files.py
```

不要求机械照搬目录，但职责必须分离。

---

# 十六、Rollout 与 Trace

新增或补全：

```text
skill.invocation_requested
skill.invocation_rejected
skill.activation_created
skill.activation_reused
skill.execution_started
skill.execution_completed
skill.execution_failed
skill.execution_cancelled
skill.resource_read
skill.resource_changed
skill.resource_missing
guidance.document_skipped_untrusted
guidance.document_skipped_budget
project.paths_resolved
```

关键字段：

```text
invocation_source
activation_id
execution_id
context_mode
root_turn_id
child_agent_id
effective_tools
project_root
workspace_root
cwd
```

---

# 十七、实施顺序

## Step 1：先写失败测试

先复现：

1. 用户 `$fork-skill` 不 Fork；
2. Fork 后 Parent Tool 永久消失；
3. 用户专用 Skill Resource 失败；
4. 单独关闭 Guidance/Skills 导致重复初始化；
5. 子目录启动时 Root AGENTS 未加载。

## Step 2：引入 ProjectPaths

更新 Thread Metadata、Guidance 和 Skill Discovery。

## Step 3：拆分 Guidance/Skills 初始化

加入独立 Init State 和 Reload。

## Step 4：分离 Activation 与 Execution

先修正 Tool Scope。

## Step 5：统一 SkillInvocationService

让用户和模型入口走同一服务。

## Step 6：修复 Resource Tool

基于 Activation 授权。

## Step 7：修复 Trust Budget、扫描和 Frontmatter Reader

## Step 8：统一 Atomic Writer

## Step 9：补 Rollout、Trace 和文档

## Step 10：完整回归

```text
Ruff
Mypy
Pytest
compileall
git diff --check
GitHub Actions
```

---

# 十八、必须新增的测试

## Invocation

- 用户 Inline；
- 模型 Inline；
- 用户 Fork；
- 模型 Fork；
- 两种入口共用 Service；
- 调用来源 Gate 正确；
- 原始 `$skill` 文本进入 Rollout。

## Tool Scope

- Fork 不收窄 Parent；
- Child Tools 正确取交集；
- Inline 仅当前 Turn；
- 下一 Turn 恢复；
- 多 Inline Skill 取交集；
- Permission Deny 始终优先。

## Resource

- 用户专用 Skill 激活后可读；
- 模型不能主动激活它；
- 未激活拒绝；
- 其他 Thread Activation 拒绝；
- Manifest 外路径拒绝；
- Symlink 逃逸拒绝；
- Catalog 为空时 Resource Tool 仍可用。

## Initialization

- Guidance/Skills 四种启停组合；
- 每个子系统只初始化一次；
- 独立 Reload；
- Resume 恢复 Activation。

## Project Paths

- Git Root 等于 CWD；
- CWD 是 Root 子目录；
- 非 Git Workspace；
- Root → CWD Guidance；
- Root → CWD Project Skill；
- 不加载兄弟目录 Skill；
- Tool 默认 CWD 正确。

## Discovery 与持久化

- Untrusted 不占预算；
- `node_modules` 剪枝；
- Depth/Directory Limit 真正阻止递归；
- Frontmatter Reader 不读取 Body；
- 超大 Skill 被拒绝；
- Trust、Guidance、Skill Snapshot 原子写；
- 写入失败不损坏旧文件。

---

# 十九、阶段 4.1 验收标准

进入阶段 5 前必须满足：

- [ ] 用户与模型调用共用 SkillInvocationService；
- [ ] 用户 `$fork-skill` 真正创建 Child；
- [ ] SkillRecord、Activation、Execution 分离；
- [ ] Fork Skill 只约束 Child；
- [ ] Inline Skill 只约束当前 Turn；
- [ ] Parent 后续 Turn Tool 恢复；
- [ ] Resource 基于 Activation 授权；
- [ ] 用户专用 Skill Resource 正常；
- [ ] Guidance 与 Skills 独立初始化和 Reload；
- [ ] Project Root 与 CWD 分离；
- [ ] Runtime 真正执行 Root → CWD 发现；
- [ ] Project Skill 不扫描无关兄弟目录；
- [ ] Untrusted 内容不占有效预算；
- [ ] Skill 扫描支持前置剪枝；
- [ ] Metadata Reader 不读取整个 Body；
- [ ] 关键 JSON 使用原子写；
- [ ] 所有旧测试和新增测试通过；
- [ ] GitHub Actions 有真实成功记录。

---

# 二十、可以继续延期的内容

以下不阻塞阶段 5：

```text
从自然语言解析路径
从 Subagent Evidence 激活 Path Rule
完整 Context Compaction
Child 独立 LocalThreadStore 持久化
Git Remote 组合 Trust Identity
HTML Comment stripping
Active Turn 中 Reload 排队
二进制 Skill Resource
跨进程 SkillExecution Resume
```

这些必须继续记录在实现差异文档中。

---

# 二十一、给 Codex 的执行要求

正式编码前先输出：

1. 9 个问题的复现路径；
2. SkillRecord / Activation / Execution 状态模型；
3. SkillInvocationService 接口；
4. 用户与模型调用统一流程；
5. Inline/Fork Tool Scope 表；
6. Project Root / Workspace Root / CWD 模型；
7. Guidance/Skills 独立初始化方案；
8. 目录扫描剪枝算法；
9. Atomic Writer 设计；
10. 修改文件和测试列表；
11. 与本文不同的设计及理由。

实施约束：

```text
先写失败测试再修；
不删除渐进式 Catalog；
不提前加载所有 Skill Body；
不让 allowed-tools 永久作用于 Thread；
不让 Fork Skill修改 Parent Tool；
不保留两套 Invocation 实现；
不用 rglob 后置检查冒充受限扫描；
不进入 MCP；
不修改沙箱路线。
```

---

# 二十二、最终结论

阶段 4 不需要推倒重写。应集中修复：

```text
调用入口统一
Activation 与 Execution 分离
工具限制作用域
Resource 授权对象
Guidance/Skills 初始化
Project Root 与 CWD
Trust Budget
目录扫描
原子持久化
```

修复后的标准流程：

```text
用户或模型请求 Skill
    ↓
统一 SkillInvocationService
    ↓
Invocation Gate
    ↓
创建或复用 SkillActivation
    ↓
创建 SkillExecution
    ↓
Inline：仅当前 Root Turn 收窄工具
或
Fork：仅当前 Child Turn 收窄工具
    ↓
Execution 完成，工具限制结束
    ↓
Activation Instructions 可继续存在于 Thread Context
```

完成阶段 4.1 后，阶段 5 MCP 才能复用一套稳定的能力生命周期：

```text
发现
目录
显式/模型调用
执行作用域
权限交集
结果返回
持久化
审计
```
