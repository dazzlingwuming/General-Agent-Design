# General Agent Harness：阶段 4 Project Guidance 与 Agent Skills 详细设计

> 文档版本：v1.0  
> 文档日期：2026-07-11  
> 目标仓库：`https://github.com/dazzlingwuming/General-Agent-Design/tree/main/agent-harness`  
> 当前阶段：阶段 4——Project Guidance 与 Agent Skills  
> 文档用途：直接交给 Codex，作为阶段 4 的设计、实现、测试与验收依据  
> 前置条件：
> - 单 Agent Loop 已完成；
> - `Thread → Turn → Item` 持续会话模型已完成或正在按修复文档落地；
> - Subagent Runtime 已存在；
> - Permission / Approval 可以保留；
> - Sandbox 暂时延期，不作为本阶段依赖；
>
> 本阶段明确不包含：
> - Long-term Memory / Auto Memory；
> - MCP；
> - Docker、云沙箱或 OS 原生沙箱；
> - 插件市场；
> - 自动修改项目指导文件；
> - 自动执行 Skill 脚本。

---

# 一、阶段 4 要解决什么问题

当前 Harness 已经能够：

```text
与用户持续对话
调用模型
调用工具
创建 Subagent
记录 Thread / Turn / Item
```

但它仍然缺少两个成熟 Coding Agent 都依赖的机制。

## 1.1 Project Guidance

Agent 每次进入一个项目时，需要自动知道：

```text
这个项目怎么构建；
测试命令是什么；
代码风格是什么；
目录结构有什么约束；
哪些文件不能随意修改；
当前子目录有哪些特殊规则；
团队希望 Agent 如何工作。
```

这些内容不应该由用户每个 Thread 重新解释。

它们应当像 Codex 的：

```text
AGENTS.md
AGENTS.override.md
```

以及 Claude Code 的：

```text
CLAUDE.md
.claude/rules/
```

一样，在项目或路径范围内持续生效。

## 1.2 Agent Skills

有些内容不应该在每个 Turn 中常驻，例如：

```text
如何生成数据库迁移；
如何审查 Pull Request；
如何发布测试环境；
如何生成 API 文档；
如何执行某种复杂代码分析流程；
如何按照团队模板生成报告。
```

这些是任务相关的可复用流程。

如果全部放进 `AGENTS.md`：

```text
会浪费上下文；
会让无关任务受到干扰；
会降低模型对关键指令的注意力。
```

因此应当像 Codex 和 Claude Code 的 Skill 一样：

```text
Thread 启动：
只加载 Skill 的 name + description

任务匹配：
加载完整 SKILL.md

Skill 引用资料或脚本：
再按需读取
```

---

# 二、官方系统的处理方式

本设计主要参考：

```text
OpenAI Codex
Anthropic Claude Code
Agent Skills Open Standard
```

## 2.1 Codex 的 AGENTS.md

Codex 在开始工作前读取 `AGENTS.md`。

Codex 的发现顺序：

```text
Global Scope
    ↓
Project Root
    ↓
当前工作目录
```

全局范围：

```text
~/.codex/AGENTS.override.md
如果不存在或为空：
~/.codex/AGENTS.md
```

项目范围：

```text
从 Git Root 走到 CWD
每一层目录最多选择一个文件：

AGENTS.override.md
优先于
AGENTS.md
优先于
配置中的 fallback filename
```

合并顺序：

```text
Project Root
    ↓
中间目录
    ↓
当前工作目录
```

越接近当前工作目录的指导内容越晚进入 Prompt，因此对当前路径更具体。

Codex 默认限制合并后的 Project Guidance 大小，当前官方默认是：

```text
32 KiB
```

Codex 在 TUI 中通常每次启动一个会话时构建一次指导链，而不是每个 Tool Call 重新扫描整个项目。

## 2.2 Claude Code 的 CLAUDE.md

Claude Code 同样支持：

```text
Organization Guidance
User Guidance
Project Guidance
Local Guidance
```

它会从当前目录向上寻找 `CLAUDE.md` 和 `CLAUDE.local.md`，并将发现的内容合并。

Claude Code 还有两个值得借鉴的能力。

### 路径范围规则

```text
.claude/rules/*.md
```

规则可通过 Frontmatter 指定：

```yaml
---
paths:
  - "src/api/**/*.ts"
---
```

只有当 Agent 处理匹配路径时，这些规则才加入上下文。

这能够避免把前端、后端、测试、部署等所有规则一次性塞入 Prompt。

### Import

Claude Code 的指导文件可以导入其他文件，并对：

```text
递归深度
循环引用
路径解析
```

进行限制。

## 2.3 Codex Skills

Codex Skill 使用渐进式披露。

Thread 启动时：

```text
只向模型提供：
name
description
path
```

当模型判断 Skill 与任务匹配时：

```text
再读取完整 SKILL.md
```

官方当前还限制初始 Skill Catalog：

```text
最多使用模型 Context Window 的 2%
或者 Context Window 未知时最多 8,000 字符
```

Codex 支持：

```text
显式调用：
用户通过 /skills 或 $skill-name 选择

隐式调用：
模型根据 description 判断并加载
```

Skill 目录：

```text
skill-name/
├── SKILL.md
├── scripts/
├── references/
├── assets/
└── agents/
```

## 2.4 Claude Code Skills

Claude Code 同样只在初始上下文中放 Skill Description，完整内容在调用时加载。

它支持：

```text
用户调用；
模型调用；
仅用户调用；
仅模型调用；
Inline Context；
Fork / Subagent Context；
Skill Supporting Files；
Skill Script；
allowed-tools。
```

Claude Code 还明确区分：

```text
CLAUDE.md：
应当在每个 Session 中持续存在的指导。

Skill：
只在任务相关时加载的流程或领域能力。
```

## 2.5 Agent Skills Open Standard

Agent Skills 已形成开放格式。

最小 Skill：

```text
skill-name/
└── SKILL.md
```

`SKILL.md`：

```yaml
---
name: code-review
description: Reviews code changes and identifies correctness, security, and test risks. Use when the user asks for a review, diff analysis, or PR feedback.
---

Skill instructions...
```

开放标准定义三层加载：

```text
Tier 1：
Metadata
启动时加载 name + description

Tier 2：
Instructions
激活时加载完整 SKILL.md

Tier 3：
Resources
需要时加载 scripts / references / assets
```

这正是阶段 4 应采用的基础格式。

---

# 三、阶段 4 的最终边界

阶段 4 分成两个独立子系统。

```text
Guidance System
+
Skill System
```

它们都向 Context Builder 提供内容，但生命周期、加载方式和使用目的不同。

## 3.1 Guidance 的定位

```text
持续、稳定、项目相关、行为指导
```

典型内容：

```text
项目架构；
构建和测试命令；
代码风格；
命名约定；
目录边界；
必须遵守的开发流程；
路径相关规则；
团队约定。
```

Guidance 默认不由模型选择是否加载。

只要其 Scope 与当前 Thread / Turn 匹配，就应进入 Context。

## 3.2 Skill 的定位

```text
可复用、任务相关、按需加载、具有工作流
```

典型内容：

```text
代码审查流程；
发布流程；
数据库迁移流程；
事故排查流程；
报告生成；
特定框架升级；
专项测试；
领域知识。
```

Skill 不应默认全部进入 Context。

## 3.3 Tool 的定位

```text
可执行原子操作
```

例如：

```text
read_file
search_text
write_file
run_command
spawn_subagent
```

Skill 可以指导 Agent 如何组合 Tool，但 Skill 本身不是 Tool。

## 3.4 Permission 的定位

Guidance 和 Skill 都是 Prompt Context，不是强制安全边界。

```text
AGENTS.md 写“禁止 git push”
```

不能代替：

```text
Permission Rule 对 git push 的 DENY
```

Skill 的：

```text
allowed-tools
```

也不能扩大当前 Agent 的 Permission。

## 3.5 Memory 的定位

Memory 是 Agent 根据历史执行积累的知识。

阶段 4 不实现：

```text
模型自动写项目记忆；
模型自动修改 AGENTS.md；
自动总结经验；
跨 Thread 自动记忆。
```

这些属于后续 Memory 阶段。

---

# 四、总体架构

```text
ThreadManager
    ↓
ThreadRuntime.start / resume
    ↓
GuidanceManager.discover()
SkillManager.discover()
    ↓
GuidanceSnapshot
SkillCatalogSnapshot
    ↓
ContextBuilder
    ├── Core System Prompt
    ├── Project Guidance
    ├── Active Path Rules
    ├── Skill Catalog
    ├── Activated Skills
    ├── Conversation History
    └── Current Turn
```

运行中：

```text
模型发现任务匹配某个 Skill
    ↓
activate_skill
    ↓
SkillManager 验证
    ↓
加载 SKILL.md
    ↓
生成 SkillActivationItem
    ↓
加入当前 Thread Context
```

路径规则：

```text
Tool 读取 src/api/users.py
    ↓
WorkingSet 更新
    ↓
PathRuleResolver 匹配 src/api/**/*.py
    ↓
激活 API Rules
    ↓
下一次 Model Call 注入
```

---

# 五、推荐目录结构

项目约定：

```text
repository/
├── AGENTS.md
├── AGENTS.override.md
│
├── .agents/
│   ├── rules/
│   │   ├── python-style.md
│   │   ├── testing.md
│   │   └── api/
│   │       └── api-rules.md
│   │
│   └── skills/
│       ├── code-review/
│       │   ├── SKILL.md
│       │   ├── references/
│       │   └── scripts/
│       │
│       └── generate-migration/
│           ├── SKILL.md
│           └── references/
│
└── src/
```

用户级：

```text
~/.agent-harness/
├── AGENTS.md
├── AGENTS.override.md
└── rules/
```

跨客户端 Skill：

```text
~/.agents/skills/
```

Harness 自己的用户 Skill：

```text
~/.agent-harness/skills/
```

系统内置：

```text
<package>/bundled_skills/
```

管理员级：

```text
Linux / WSL：
/etc/agent-harness/AGENTS.md
/etc/agent-harness/rules/
/etc/agent-harness/skills/

Windows：
%PROGRAMDATA%\AgentHarness\
```

---

# 六、Project Guidance 领域模型

## 6.1 GuidanceSource

```python
class GuidanceSourceKind(StrEnum):
    CORE = "core"
    ADMIN = "admin"
    USER = "user"
    PROJECT = "project"
    PATH_RULE = "path_rule"
```

## 6.2 GuidanceDocument

```python
@dataclass(frozen=True)
class GuidanceDocument:
    document_id: str
    source_kind: GuidanceSourceKind

    path: Path
    scope_root: Path | None
    relative_path: str | None

    content: str
    content_hash: str
    byte_size: int

    precedence: int
    directory_depth: int

    path_patterns: tuple[str, ...]
    exclude_patterns: tuple[str, ...]

    trusted: bool
    loaded_at: datetime
```

## 6.3 GuidanceSnapshot

```python
@dataclass(frozen=True)
class GuidanceSnapshot:
    snapshot_id: str
    runtime_instance_id: str
    thread_id: str

    documents: tuple[GuidanceDocument, ...]
    combined_hash: str
    total_bytes: int

    truncated: bool
    omitted_documents: tuple[str, ...]
    diagnostics: tuple[GuidanceDiagnostic, ...]
```

GuidanceSnapshot 表示：

```text
当前 Thread Runtime 启动时使用的指导版本。
```

---

# 七、Guidance Discovery

## 7.1 发现时点

执行：

```text
thread/start
thread/resume
显式 /guidance reload
```

不执行：

```text
每个 Tool Call；
每个 Model Call；
文件系统每次变化。
```

原因：

```text
保持一个 Turn 内的指导稳定；
避免中途改变模型行为；
避免反复扫描；
方便复现和审计。
```

## 7.2 Global Discovery

顺序：

```text
Admin Guidance
    ↓
User Guidance
```

每个目录的选择规则：

```text
AGENTS.override.md
如果不存在或为空：
AGENTS.md
```

每个 Scope 最多选择一个主 Guidance 文件。

## 7.3 Project Discovery

首先确定：

```text
Project Root
```

优先：

```text
Git Root
```

没有 Git 时：

```text
Workspace Root
```

从：

```text
Project Root
```

走到：

```text
Thread CWD
```

在每一层目录依次检查：

```text
AGENTS.override.md
AGENTS.md
project_doc_fallback_filenames
```

每一层最多选一个非空文件。

默认 fallback：

```toml
project_doc_fallback_filenames = ["CLAUDE.md"]
```

注意：

```text
同一目录发现 AGENTS.md 和 CLAUDE.md 时，
只选择 AGENTS.md。
```

避免同一内容重复加载。

## 7.4 Merge Order

基础顺序：

```text
Admin
User
Project Root
Nested Project Directory
Current Working Directory
```

项目内：

```text
更具体目录的 Guidance 后出现。
```

模型行为说明中应明确：

```text
路径更具体的项目指导优先于更宽泛项目指导；
管理员指导不可由项目指导取消；
技术强制规则仍由 Permission / Runtime 执行。
```

## 7.5 Guidance Size Limit

默认：

```toml
max_guidance_bytes = 32768
```

达到限制后：

```text
停止继续加入更低优先级或更远范围内容；
记录 omitted_documents；
输出 Warning；
/guidance 可查看。
```

不能：

```text
在 Markdown 内容中间静默截断。
```

一个 Document 要么完整加载，要么不加载。

管理员 Guidance 可以拥有独立保留预算。

## 7.6 空文件与编码

```text
空文件跳过；
只支持 UTF-8；
无法解码则记录错误；
不阻止整个 Thread 启动；
管理员文件失败可配置为 fail closed。
```

---

# 八、Guidance Import

借鉴 Claude Code，但采用更严格语法。

## 8.1 语法

只识别独立行：

```text
@import path/to/file.md
```

不将普通 Markdown 中所有 `@filename` 都视作导入。

这样可以避免：

```text
文档提到 @README 时意外导入。
```

## 8.2 路径规则

相对路径：

```text
相对于当前 Guidance 文件所在目录。
```

项目 Guidance：

```text
只能导入当前 Trusted Project Root 内的文件。
```

用户 Guidance：

```text
只能导入 User Guidance Root 内文件。
```

管理员 Guidance：

```text
只能导入 Admin Root 内文件。
```

默认禁止绝对路径 Import。

## 8.3 限制

```toml
max_import_depth = 4
max_import_files = 32
max_import_total_bytes = 32768
```

必须支持：

```text
循环检测；
重复导入去重；
Symlink 最终路径检查；
Import Error Diagnostic。
```

---

# 九、路径范围规则

## 9.1 目录

```text
.agents/rules/**/*.md
~/.agent-harness/rules/**/*.md
/etc/agent-harness/rules/**/*.md
```

## 9.2 Frontmatter

```yaml
---
paths:
  - "src/api/**/*.py"
  - "tests/api/**/*.py"
exclude:
  - "src/api/generated/**"
description: API implementation rules
---

# API Rules

- Validate all external input.
- Use ApiError for expected failures.
- Add tests for error paths.
```

## 9.3 无 paths 规则

没有 `paths`：

```text
Thread Runtime 启动时直接加载；
作用范围等同当前 Scope 的主 Guidance。
```

## 9.4 有 paths 规则

只在当前 Turn 的 Working Set 命中时加载。

Working Set 来源：

```text
用户显式提到的仓库路径；
read_file；
write_file；
apply_patch；
search_text 返回的候选路径；
Subagent 返回的 Evidence Path。
```

不是所有 Search Result 都立即激活。

建议：

```text
read / write / patch 的文件：
确定激活。

search_text 只返回但未读取的文件：
可以标记 Candidate，不立即激活。
```

## 9.5 激活生命周期

路径规则一旦在当前 Turn 激活：

```text
当前 Turn 剩余时间持续有效。
```

下一 Turn：

```text
重新根据新的 Working Set 计算。
```

这样避免一个长 Thread 访问过很多模块后，所有路径规则永久堆积在 Context 中。

## 9.6 多规则排序

```text
Admin Rules
User Rules
Project Root Rules
Nested Rules
更具体 Glob Rules
```

Glob Specificity 可按：

```text
固定路径段数量；
Wildcard 数量；
目录深度。
```

排序必须确定，不能依赖文件系统遍历顺序。

---

# 十、Workspace Trust

Project Guidance 和 Project Skill 都是仓库提供的指令，可能形成 Prompt Injection。

因此必须引入：

```text
WorkspaceTrustState
```

状态：

```text
UNKNOWN
TRUSTED
UNTRUSTED
```

## 10.1 未信任 Workspace

允许：

```text
读取普通源代码；
显示发现了哪些 Guidance / Skill；
用户显式查看文件。
```

不允许：

```text
自动向模型注入 Project Guidance；
向 Skill Catalog 暴露 Project Skills；
自动激活项目 Skill；
执行 Skill Script。
```

## 10.2 信任交互

Thread 首次打开仓库：

```text
发现：
2 个 AGENTS.md
5 个 Rule
3 个 Skill

是否信任此仓库中的 Agent 配置？
```

用户选择：

```text
Trust Once
Trust Workspace
Do Not Trust
```

信任记录根据：

```text
规范化 Workspace Root；
可选 Git Remote；
可选 Repository Identity。
```

不能仅根据目录名。

## 10.3 用户与管理员内容

```text
Admin / User Guidance：
默认信任。

Project Guidance：
需要 Workspace Trust。
```

---

# 十一、Guidance Context 注入

## 11.1 不作为普通用户消息

Guidance 不应追加成：

```text
role=user
```

它应作为 Context Builder 的稳定指导区。

建议结构：

```xml
<project_guidance snapshot_id="guidance_x">
  <document source="user" path="...">
    ...
  </document>

  <document source="project" path="AGENTS.md">
    ...
  </document>

  <path_rule path=".agents/rules/api.md">
    ...
  </path_rule>
</project_guidance>
```

## 11.2 每个 Model Request 重建

Context Builder 每次构建请求时：

```text
读取 GuidanceSnapshot；
加入当前 Turn Active Path Rules；
生成稳定 Guidance Section。
```

不需要每一轮把同样 Guidance 追加到 Canonical Conversation History。

## 11.3 可复现性

Guidance 文件可能在 Thread 运行后被修改。

因此 Thread Runtime 启动时应保存：

```text
GuidanceSnapshot 内容；
Content Hash；
Source Path；
加载顺序。
```

建议存储：

```text
.harness/threads/<thread_id>/snapshots/guidance-<hash>.json
```

Rollout 中记录：

```text
guidance.snapshot_created
```

恢复旧历史时能够知道过去 Turn 使用了什么 Guidance。

新 Runtime Resume：

```text
重新扫描当前 Guidance；
生成新 Snapshot；
如果 Hash 变化，记录 guidance.snapshot_changed。
```

新 Turn 使用新 Snapshot。

---

# 十二、Guidance CLI

## 12.1 /guidance

显示：

```text
当前 Snapshot ID
总字节数
加载顺序
Source
Path
Hash
是否 Trusted
当前 Turn 激活的 Path Rules
被跳过的文件
Diagnostic
```

## 12.2 /guidance reload

规则：

```text
Thread IDLE：
立即 Reload。

Thread ACTIVE：
不改变当前 Turn；
排队到 Turn 结束后 Reload。
```

不能在一个 Model / Tool Loop 中途替换 Guidance。

## 12.3 /guidance inspect <id>

查看某个 Guidance Document 的完整内容和来源。

---

# 十三、Skill Format

阶段 4 采用 Agent Skills Open Standard。

## 13.1 最小 Skill

```text
.agents/skills/code-review/
└── SKILL.md
```

```yaml
---
name: code-review
description: Reviews code changes for correctness, security, maintainability, and missing tests. Use when the user requests a review, PR analysis, or diff inspection.
---

# Code Review

1. Inspect the changed files.
2. Identify correctness and regression risks.
3. Check tests.
4. Report findings by severity.
```

## 13.2 标准 Frontmatter

必须支持：

```text
name
description
license
compatibility
metadata
allowed-tools
```

约束遵循 Open Standard：

```text
name：
1-64 字符；
小写字母、数字、连字符；
与目录名匹配。

description：
1-1024 字符；
说明 Skill 做什么；
说明何时使用。
```

## 13.3 兼容 Claude Code 字段

阶段 4 可支持：

```text
disable-model-invocation
user-invocable
argument-hint
context
agent
```

示例：

```yaml
---
name: deploy-staging
description: Deploys the current project to the staging environment.
disable-model-invocation: true
user-invocable: true
argument-hint: "[service]"
context: fork
agent: test-analyst
---
```

## 13.4 字段语义

### disable-model-invocation

```text
true：
不进入模型 Skill Catalog；
只能用户显式调用。
```

适合：

```text
deploy
commit
publish
send-message
```

### user-invocable

```text
false：
用户 Skill Selector 不展示；
模型可以按描述自动激活。
```

适合：

```text
legacy-system-context
internal-api-conventions
```

### context

```text
inline：
加载到 Main Agent Thread。

fork：
创建 Subagent 执行。
```

### agent

仅在：

```text
context: fork
```

时生效，指定已有 AgentDefinition。

---

# 十四、allowed-tools 的安全语义

Agent Skills Standard 的 `allowed-tools` 在一些实现中表示预批准 Tool。

本项目不能让 Skill 扩大权限。

本项目定义：

```text
Skill allowed-tools 是 Skill 执行期的 Tool 上限。
```

有效 Tool：

```text
Agent Principal Allowed Tools
∩
Skill allowed-tools
∩
Permission Engine
```

例如 Agent 没有：

```text
run_command
```

即使 Skill 声明：

```yaml
allowed-tools: run_command
```

也不能获得该 Tool。

如果 Skill 未声明 `allowed-tools`：

```text
继续使用 Agent 当前 Tool 上限。
```

---

# 十五、Skill Directory

## 15.1 扫描位置

按 Scope：

```text
SYSTEM
ADMIN
USER
PROJECT
NESTED_PROJECT
```

默认目录：

```text
Bundled：
<package>/bundled_skills/

Admin：
/etc/agent-harness/skills/

User：
~/.agent-harness/skills/
~/.agents/skills/

Project：
$REPO_ROOT/.agents/skills/
从 Repo Root 到 CWD 的每层 .agents/skills/
```

可选兼容：

```text
.claude/skills/
```

默认关闭，通过配置开启：

```toml
[skills]
scan_claude_compatibility_paths = false
```

## 15.2 Nested Skill

Monorepo 示例：

```text
repo/.agents/skills/deploy/
apps/web/.agents/skills/deploy/
```

两个 Skill 都保留。

不进行内容合并。

## 15.3 Skill ID 与名称冲突

不能静默覆盖同名 Skill。

每个 Skill 具有：

```text
skill_id
scope
qualified_name
display_name
```

示例：

```text
project:code-review
user:code-review
apps/web:deploy
```

规则：

```text
名称唯一：
可使用 code-review。

名称冲突：
必须使用 qualified_name。
```

模型 Catalog 中对冲突 Skill 一律展示 Qualified Name。

用户显式调用未限定名称且存在冲突：

```text
返回选择列表；
不能任意选一个。
```

## 15.4 Symlink

允许 Skill 目录 Symlink，但要求：

```text
解析循环；
同一最终目标只加载一次；
目标必须位于 Trusted Scope Root；
或者位于用户显式允许的 Shared Skill Root。
```

Project Skill 不能通过 Symlink 指向：

```text
用户 Home 中任意位置；
Secret 目录；
其他未信任项目。
```

## 15.5 扫描限制

```toml
max_skill_scan_depth = 6
max_skill_directories = 2000
max_skills = 500
```

跳过：

```text
.git
node_modules
.venv
build
dist
缓存目录
```

---

# 十六、Skill Parser

## 16.1 Strict 与 Lenient

建议两阶段：

```text
Parse：
尽量兼容。

Validate：
输出 Diagnostic。
```

以下情况：

```text
description 缺失：
跳过 Skill。

YAML 完全无法解析：
跳过 Skill。

name 与目录不一致：
Warning，可以加载。

未知 Frontmatter：
保留到 metadata_extensions。
```

## 16.2 SkillRecord

```python
@dataclass(frozen=True)
class SkillRecord:
    skill_id: str
    name: str
    qualified_name: str
    description: str

    path: Path
    base_directory: Path
    scope: SkillScope

    license: str | None
    compatibility: str | None
    metadata: dict[str, str]

    allowed_tools: tuple[str, ...]

    disable_model_invocation: bool
    user_invocable: bool
    context_mode: SkillContextMode
    agent_name: str | None
    argument_hint: str | None

    content_hash: str
    trusted: bool
    enabled: bool

    resource_manifest: SkillResourceManifest
    diagnostics: tuple[SkillDiagnostic, ...]
```

---

# 十七、渐进式披露

## 17.1 Tier 1：Skill Catalog

Thread Runtime 启动时只提供：

```text
qualified_name
description
```

必要时提供：

```text
scope
```

不加载：

```text
SKILL.md Body
references
scripts
assets
```

## 17.2 Catalog Budget

默认：

```text
Context Window 的 2%
或者未知时 8,000 字符
```

处理顺序：

```text
1. 只放 Enabled + Trusted + 可调用 Skill；
2. Description 超长时压缩到上限；
3. 按 Scope 与稳定名称排序；
4. 超过预算时截断完整 Skill Entry；
5. 输出 Catalog Truncated Warning。
```

不能截断到半个 Skill。

## 17.3 Catalog 过滤

不进入模型 Catalog：

```text
disabled Skill；
Untrusted Project Skill；
disable-model-invocation Skill；
Permission 明确禁止访问的 Skill；
环境 compatibility 明确不满足的 Skill。
```

用户显式 Skill Selector 仍可显示：

```text
disable-model-invocation Skill。
```

但不显示：

```text
Untrusted 或 Disabled Skill，
除非用户要求查看全部。
```

## 17.4 Catalog 注入

建议使用：

```xml
<available_skills>
  <skill name="project:code-review">
    Reviews code changes...
  </skill>
</available_skills>
```

配套短指令：

```text
当任务符合 Skill Description 时，
在执行任务前调用 activate_skill。
不要猜测不存在的 Skill。
```

---

# 十八、Skill 激活机制

本项目选择：

```text
Dedicated activate_skill Tool
```

而不是让模型直接通过 `read_file` 读取任意 `SKILL.md`。

原因：

```text
可以执行 Trust 检查；
可以执行 Enable 检查；
可以限制名称；
可以结构化包裹内容；
可以记录 Activation；
可以列出资源；
可以处理去重；
可以与 Context Compaction 集成；
可以支持 Inline / Fork。
```

## 18.1 activate_skill

输入：

```python
class ActivateSkillInput(BaseModel):
    name: str
    arguments: str | None = None
```

Tool Schema 中：

```text
name 使用当前可激活 Skill 的 enum。
```

没有可用 Skill 时：

```text
不注册 activate_skill Tool。
```

## 18.2 用户显式调用

CLI 支持：

```text
$code-review
$generate-migration users
```

也可支持：

```text
/skill code-review
```

CLI 在发送 User Message 前解析。

用户显式 Skill 不需要模型先调用 `activate_skill`。

Harness 直接：

```text
解析 Skill；
渲染 Arguments；
创建 SkillActivationItem；
将 Skill Content 加入 Context；
再启动 Turn。
```

## 18.3 模型隐式调用

模型看到 Catalog 后：

```text
activate_skill(name="project:code-review")
```

SkillManager 返回结构化内容。

---

# 十九、Skill Content Rendering

## 19.1 返回 Body 还是完整文件

Dedicated Tool 使用：

```text
Frontmatter 解析后；
向模型返回 Markdown Body；
另附标准化 Metadata。
```

避免模型重复解析 YAML。

## 19.2 结构化包裹

```xml
<skill_content
    id="skill_x"
    name="project:code-review"
    activation_id="activation_x">

  <instructions>
    ...
  </instructions>

  <skill_directory>
    /workspace/.agents/skills/code-review
  </skill_directory>

  <resources>
    <file type="reference">references/checklist.md</file>
    <file type="script">scripts/analyze_diff.py</file>
  </resources>

  <arguments>
    ...
  </arguments>
</skill_content>
```

## 19.3 Resource Manifest

激活时只枚举：

```text
relative path
resource type
size
hash
```

不读取全部内容。

模型按需使用：

```text
read_skill_resource
```

或者普通受控 File Tool。

推荐增加专用 Tool：

```text
read_skill_resource
```

它只能读取：

```text
当前已激活 Skill 目录下的文件。
```

这样不需要将 User Skill 目录暴露为通用 Workspace Path。

---

# 二十、Skill Supporting Files

## 20.1 references/

用途：

```text
详细技术参考；
API 文档；
团队规则；
Examples；
Checklist。
```

按需读取。

## 20.2 assets/

用途：

```text
模板；
Schema；
静态数据；
样例文件。
```

是否读取、复制或使用，由现有 Tool Runtime 控制。

## 20.3 scripts/

阶段 4 只完成：

```text
发现；
Manifest；
允许 Agent 请求执行。
```

不实现：

```text
激活 Skill 时自动执行 Script；
使用 !command 动态注入；
绕过 Permission；
绕过未来 Sandbox。
```

执行 Script 时必须走：

```text
run_command
Permission Engine
Approval
未来 Sandbox Backend
```

如果当前没有安全命令执行环境：

```text
Skill Script 只能被发现，不能执行；
必须返回明确 Unsupported。
```

---

# 二十一、Skill Invocation 控制

## 21.1 默认

```text
用户可以调用；
模型可以调用。
```

## 21.2 仅用户

```yaml
disable-model-invocation: true
```

适合：

```text
deploy
publish
commit
send-message
create-release
```

这些 Skill 不进入模型 Catalog。

## 21.3 仅模型

```yaml
user-invocable: false
```

适合：

```text
framework-conventions
legacy-system-context
domain-glossary
```

用户 Selector 不展示，但模型 Catalog 可见。

---

# 二十二、Skill Inline Context

默认：

```yaml
context: inline
```

激活后：

```text
Skill Content 加入 Main Agent Thread Context。
```

## 22.1 去重

维护：

```text
ThreadActiveSkillSet
```

Key：

```text
skill_id
+
rendered_content_hash
```

同一 Skill 同一渲染内容重复激活：

```text
不再次注入完整内容；
返回“Skill 已加载”。
```

Arguments 不同导致内容不同：

```text
可以新增 Activation。
```

## 22.2 生命周期

借鉴 Claude Code：

```text
激活后的 Skill 在当前 Thread Runtime 中保持有效。
```

但要记录：

```text
activation_id
activated_turn_id
content_hash
arguments_hash
```

## 22.3 Thread Resume

恢复 Thread 时：

```text
从 Rollout 找到有效 SkillActivationItem；
恢复 Active Skill；
使用当时保存的 Skill Snapshot 内容；
不依赖磁盘文件仍然相同。
```

新一次显式重新激活：

```text
读取当前文件版本；
如 Hash 改变，创建新 Activation。
```

---

# 二十三、Skill Fork / Subagent Context

Skill 可声明：

```yaml
context: fork
agent: reviewer
```

## 23.1 流程

```text
用户或模型激活 Skill
    ↓
SkillManager 读取完整 Skill
    ↓
构建 SkillDelegationPacket
    ↓
SubagentScheduler 创建 Child Thread / Turn
    ↓
Child 初始 Context：
AgentDefinition
+ Skill Content
+ User Task
+ Arguments
    ↓
Child 执行
    ↓
结构化结果返回 Main Agent
```

## 23.2 为什么使用 Fork

适合：

```text
复杂代码审查；
大型日志分析；
独立验证；
专项迁移方案；
长流程研究。
```

避免把大量 Skill 过程塞入 Main Context。

## 23.3 Tool 上限

Child 有效 Tool：

```text
Parent Effective Tools
∩
Child AgentDefinition Tools
∩
Skill allowed-tools
```

## 23.4 失败

如果声明的 Agent 不存在：

```text
Skill Activation Failed；
不能自动改用任意 Agent。
```

如果阶段 2 Runtime 还未完全稳定：

```text
可以先实现 Inline；
Fork 标记为 Unsupported；
但必须保留测试和接口。
```

最终阶段 4 验收建议包含至少一个 Fork Skill。

---

# 二十四、Skill 与 Context Compaction

Skill Content 不能作为普通旧 Tool Result 被随意删除。

## 24.1 Protected Context

SkillActivationItem 标记：

```text
context_priority = durable_guidance
protected_from_normal_pruning = true
```

## 24.2 Compaction

当 Context Compaction 发生：

```text
保留每个 Active Skill 的最近一次有效 Activation。
```

建议预算：

```toml
max_reattached_skill_tokens_per_skill = 5000
max_total_reattached_skill_tokens = 25000
```

超出时：

```text
最近激活 Skill 优先；
旧 Skill 可以暂时移除；
记录 skill.compaction_omitted；
/skills 显示状态。
```

阶段 4 可以先实现数据标记和接口，完整 Compaction 算法可与 Thread Context Manager 同步落地。

---

# 二十五、Skill Snapshot 与可复现性

Skill 文件会变化。

激活时必须保存：

```text
Skill Metadata；
Rendered Instructions；
Content Hash；
Resource Manifest；
Arguments；
Source Path。
```

建议：

```text
.harness/threads/<thread_id>/snapshots/skills/<activation_id>.json
```

Rollout：

```text
skill.activated
```

引用 Snapshot。

不能只保存：

```text
/path/to/SKILL.md
```

否则 Resume 后文件变化，会导致历史无法重建。

---

# 二十六、Skill CLI

## 26.1 /skills

显示：

```text
可用 Skill；
Qualified Name；
Scope；
Description；
是否用户可调用；
是否模型可调用；
Context Mode；
Trust；
Enabled；
Compatibility；
Diagnostic。
```

## 26.2 /skills active

显示当前 Thread 已激活的 Skill。

## 26.3 /skills inspect <name>

显示：

```text
Frontmatter；
Instructions；
Resource Manifest；
Source；
Hash。
```

## 26.4 /skills reload

规则：

```text
Thread IDLE：
刷新 Catalog。

Thread ACTIVE：
Turn 完成后刷新。
```

已经激活的 Snapshot 不会被静默替换。

## 26.5 /skills disable <name>

写入用户配置：

```toml
[[skills.config]]
id = "user:some-skill"
enabled = false
```

---

# 二十七、配置示例

```toml
[guidance]
enabled = true
max_guidance_bytes = 32768
max_import_depth = 4
max_import_files = 32
project_doc_fallback_filenames = ["CLAUDE.md"]
strip_html_comments = true
require_workspace_trust = true

[guidance.rules]
enabled = true
project_directory = ".agents/rules"
user_directory = "~/.agent-harness/rules"
activate_search_candidates = false

[skills]
enabled = true
require_workspace_trust = true
catalog_context_ratio = 0.02
catalog_fallback_max_chars = 8000
max_skills = 500
max_skill_scan_depth = 6
max_skill_directories = 2000
scan_agent_skills_paths = true
scan_claude_compatibility_paths = false
support_fork_context = true

[[skills.search_paths]]
scope = "user"
path = "~/.agent-harness/skills"

[[skills.search_paths]]
scope = "user"
path = "~/.agents/skills"

[[skills.config]]
id = "user:deploy-production"
enabled = false
```

---

# 二十八、Thread / Turn / Item 集成

新增 Rollout Item。

## Guidance

```text
guidance.discovery_started
guidance.document_discovered
guidance.document_skipped
guidance.snapshot_created
guidance.snapshot_changed
guidance.path_rule_activated
guidance.reload_requested
guidance.reload_completed
```

## Skills

```text
skill.discovery_started
skill.discovered
skill.skipped
skill.catalog_created
skill.activation_requested
skill.activated
skill.already_active
skill.activation_failed
skill.resource_read
skill.delegated
skill.completed
skill.compaction_omitted
```

## 关键字段

```text
session_id
thread_id
turn_id
agent_id

guidance_snapshot_id
guidance_document_id

skill_id
activation_id
skill_snapshot_id
skill_content_hash
```

---

# 二十九、Trace 与 Rollout 的区别

Rollout 保存：

```text
影响对话恢复和行为重建的 canonical item。
```

Trace 保存：

```text
扫描耗时；
Parser 耗时；
候选数量；
Catalog 字符数；
激活耗时；
缓存命中；
Diagnostic。
```

例如：

```text
Skill Activated
```

进入 Rollout 和 Trace。

但：

```text
扫描 873 个目录耗时 54ms
```

只进入 Trace。

---

# 三十、推荐代码结构

```text
src/agent_harness/
├── guidance/
│   ├── models.py
│   ├── discovery.py
│   ├── loader.py
│   ├── imports.py
│   ├── rules.py
│   ├── working_set.py
│   ├── snapshot.py
│   ├── trust.py
│   └── context.py
│
├── skills/
│   ├── models.py
│   ├── discovery.py
│   ├── parser.py
│   ├── registry.py
│   ├── catalog.py
│   ├── activation.py
│   ├── rendering.py
│   ├── resources.py
│   ├── snapshots.py
│   └── control_tools.py
│
├── context/
│   └── builder.py
│
├── runtime/
│   ├── thread_runtime.py
│   └── subagents/
│
├── rollout/
│   └── items.py
│
└── cli/
    ├── guidance_commands.py
    ├── skills_commands.py
    └── trust_commands.py
```

---

# 三十一、Context Builder 改造

Context Builder 输入增加：

```python
@dataclass
class ContextBuildInput:
    thread_state: ThreadState
    turn_state: TurnState

    guidance_snapshot: GuidanceSnapshot
    active_path_rules: tuple[GuidanceDocument, ...]

    skill_catalog: SkillCatalogSnapshot
    active_skills: tuple[SkillActivationSnapshot, ...]

    messages: tuple[CanonicalMessage, ...]
    tools: tuple[ToolDefinition, ...]
```

构建顺序：

```text
1. Core System Instructions
2. Managed Guidance
3. User Guidance
4. Project Guidance
5. Current Turn Path Rules
6. Skill Usage Instructions
7. Available Skill Catalog
8. Active Skill Content
9. Conversation History
10. Current Turn Input
```

## 31.1 指令冲突说明

Context 中加入简短规则：

```text
- Runtime Permission 和 Tool Policy 始终优先；
- 管理员 Guidance 优先于用户和项目 Guidance；
- 更具体的项目路径 Guidance 优先于宽泛项目 Guidance；
- 当前用户请求可以选择任务目标，但不能取消 Runtime 安全规则；
- Skill 只控制当前工作流，不能扩大工具权限。
```

---

# 三十二、缓存与更新

## 32.1 Guidance Cache

Key：

```text
absolute_path
mtime
size
content_hash
```

## 32.2 Skill Cache

Metadata Cache：

```text
SKILL.md path
mtime
size
frontmatter hash
```

Body：

```text
激活时读取；
或缓存 content hash。
```

## 32.3 不做自动热更新

第一版不在 Turn 执行中监听文件变化。

更新时点：

```text
新 Thread Runtime；
Resume；
显式 Reload。
```

这样行为更容易复现。

---

# 三十三、安全要求

## 33.1 Project Prompt Injection

Untrusted Project 的：

```text
AGENTS.md
.agents/rules
.agents/skills
```

不得自动进入模型上下文。

## 33.2 Skill Script

Skill 激活不会自动运行 Script。

## 33.3 Skill Path

Project Skill Resource 读取必须限制在：

```text
Skill Base Directory
```

Symlink 最终目标也必须允许。

## 33.4 Skill Permission

Skill 不能：

```text
注册新 Tool；
绕过 Permission；
扩大 Child Permission；
修改 AgentDefinition；
自动启用网络；
自动安装依赖。
```

## 33.5 Guidance 不是 Enforcement

README 必须明确：

```text
Guidance 影响模型行为；
Permission / Hook 才是确定性执行边界。
```

---

# 三十四、实施顺序

## Step 1：Guidance Domain

实现：

```text
GuidanceDocument
GuidanceSnapshot
Diagnostic
Source Scope
Hash
```

## Step 2：AGENTS.md Discovery

实现：

```text
Global；
Project Root → CWD；
Override；
Fallback；
Size Limit；
UTF-8；
Snapshot。
```

## Step 3：Workspace Trust

实现：

```text
Trust State；
首次提示；
Project Guidance Gate；
Project Skill Gate。
```

## Step 4：Guidance Context

接入 Context Builder。

增加：

```text
/guidance
/guidance reload
```

## Step 5：Guidance Imports

实现：

```text
@import；
Depth；
Cycle；
Boundary；
Size。
```

## Step 6：Path Rules

实现：

```text
.agents/rules；
Frontmatter；
Glob；
Working Set；
Turn 生命周期。
```

## Step 7：Skill Parser 与 Registry

实现 Open Standard：

```text
SKILL.md；
Frontmatter；
Resource Manifest；
Diagnostic；
Qualified Name；
Collision。
```

## Step 8：Skill Catalog

实现：

```text
Progressive Disclosure；
Budget；
Filtering；
Context Injection。
```

## Step 9：Inline Activation

实现：

```text
activate_skill；
$skill；
Rendering；
Snapshot；
Dedup；
Rollout。
```

## Step 10：Skill Resources

实现：

```text
read_skill_resource；
Boundary；
Manifest；
No Eager Load。
```

## Step 11：Fork Skill

接入 Subagent Runtime。

## Step 12：Resume 与 Compaction 标记

实现：

```text
恢复 Active Skills；
Guidance Snapshot Change；
Protected Skill Context。
```

## Step 13：CLI 与文档

完成：

```text
/skills；
/skills active；
/skills inspect；
/skills reload；
/trust。
```

## Step 14：测试与 CI

所有 Guidance、Skill 和回归测试通过。

---

# 三十五、测试矩阵

## 35.1 AGENTS.md Discovery

- [ ] User AGENTS.md；
- [ ] User AGENTS.override.md 优先；
- [ ] Project Root AGENTS.md；
- [ ] Nested AGENTS.md；
- [ ] Nested Override；
- [ ] CLAUDE.md Fallback；
- [ ] 同目录 AGENTS.md 优先于 CLAUDE.md；
- [ ] Root → CWD 顺序稳定；
- [ ] 空文件跳过；
- [ ] UTF-8 错误 Diagnostic；
- [ ] 32 KiB Limit；
- [ ] Document 不被半截断；
- [ ] 无 Git 时使用 Workspace Root。

## 35.2 Imports

- [ ] 正常相对 Import；
- [ ] 四层 Import；
- [ ] 超过深度拒绝；
- [ ] 循环 Import；
- [ ] 重复 Import 去重；
- [ ] Project Root 逃逸拒绝；
- [ ] Symlink 逃逸拒绝；
- [ ] 代码块内 `@import` 不解析；
- [ ] Total Bytes Limit。

## 35.3 Path Rules

- [ ] 无 paths 规则常驻；
- [ ] read_file 命中激活；
- [ ] write_file 命中激活；
- [ ] Search Candidate 默认不激活；
- [ ] exclude 生效；
- [ ] 多 Glob；
- [ ] Nested Rule；
- [ ] Rule 当前 Turn 持续；
- [ ] 下一 Turn 重新计算；
- [ ] 排序确定；
- [ ] Symlink 路径匹配正确。

## 35.4 Trust

- [ ] UNKNOWN Project 不注入 Guidance；
- [ ] UNKNOWN Project 不展示 Model Skills；
- [ ] Trust Once；
- [ ] Trust Workspace；
- [ ] Do Not Trust；
- [ ] User Guidance 始终可用；
- [ ] Trust 记录使用规范路径；
- [ ] Symlink Project Identity；
- [ ] Project 变化后 Trust 行为明确。

## 35.5 Skill Parser

- [ ] 最小 SKILL.md；
- [ ] name 缺失；
- [ ] description 缺失；
- [ ] 无效 YAML；
- [ ] name 与目录不一致 Warning；
- [ ] Optional Fields；
- [ ] Claude Compatibility Fields；
- [ ] Unknown Fields；
- [ ] Resource Manifest；
- [ ] Symlink Skill；
- [ ] Symlink Loop；
- [ ] 扫描上限。

## 35.6 Skill Collision

- [ ] User 与 Project 同名；
- [ ] Root 与 Nested 同名；
- [ ] Qualified Name；
- [ ] Ambiguous Explicit Invocation；
- [ ] Catalog 不静默覆盖；
- [ ] 同一 Target Symlink 去重。

## 35.7 Catalog

- [ ] 仅加载 Metadata；
- [ ] Body 不提前读取；
- [ ] 2% Budget；
- [ ] 8,000 字符 Fallback；
- [ ] Entry 完整截断；
- [ ] Disabled Skill 隐藏；
- [ ] disable-model-invocation 隐藏；
- [ ] user-invocable=false 仍供模型使用；
- [ ] Untrusted Project Skill 隐藏；
- [ ] 无 Skill 时不注册 Tool；
- [ ] Catalog 顺序稳定。

## 35.8 Activation

- [ ] 模型 activate_skill；
- [ ] 用户 `$skill`；
- [ ] 不存在 Skill；
- [ ] Ambiguous Name；
- [ ] Disabled Skill；
- [ ] Untrusted Skill；
- [ ] Arguments；
- [ ] Structured Wrapper；
- [ ] Snapshot；
- [ ] Rollout Item；
- [ ] 重复激活去重；
- [ ] 不同 Arguments 新 Activation；
- [ ] Skill 内容修改后新 Hash。

## 35.9 Skill Resource

- [ ] references 读取；
- [ ] assets 读取；
- [ ] scripts Manifest；
- [ ] 不 Eager Load；
- [ ] Skill Directory 逃逸拒绝；
- [ ] Symlink 逃逸拒绝；
- [ ] 未激活 Skill Resource 拒绝；
- [ ] 大文件限制；
- [ ] 二进制行为明确。

## 35.10 allowed-tools

- [ ] Skill allowed-tools 缩小权限；
- [ ] Skill 不能扩大 Agent Tools；
- [ ] Child Tools 做交集；
- [ ] Permission DENY 继续生效；
- [ ] 缺失 allowed-tools 使用 Agent 上限。

## 35.11 Fork Skill

- [ ] 创建 Child Thread；
- [ ] Child 获取 Skill Content；
- [ ] Child 不获取完整 Main History；
- [ ] AgentDefinition 正确；
- [ ] Tool 交集；
- [ ] 结构化结果；
- [ ] Child 失败；
- [ ] Child Cancel；
- [ ] Skill / Agent 不存在。

## 35.12 Thread Resume

- [ ] Guidance Snapshot 保存；
- [ ] Resume 重建 Active Skills；
- [ ] Skill 原文件删除仍可重建历史；
- [ ] Guidance 文件改变产生 Change Item；
- [ ] 新 Turn 使用新 Snapshot；
- [ ] 旧 Turn 仍引用旧 Snapshot。

## 35.13 Context

- [ ] Guidance 不重复追加到 History；
- [ ] Skill Catalog 每次构建正确；
- [ ] Active Skill 进入 Context；
- [ ] Path Rule 下一 Model Call 生效；
- [ ] Skill 标记为 Protected；
- [ ] Catalog 与 Tool Enum 一致；
- [ ] Token / Char Budget 可核对。

---

# 三十六、阶段 4 验收标准

## Guidance

- [ ] 支持 Global、User、Project、Nested Guidance；
- [ ] 支持 `AGENTS.override.md`；
- [ ] 支持 fallback；
- [ ] 支持 32 KiB Limit；
- [ ] 支持 Snapshot；
- [ ] 支持 `/guidance`；
- [ ] 支持显式 Reload；
- [ ] 一个 Turn 内 Guidance 稳定。

## Path Rules

- [ ] 支持 `.agents/rules/`；
- [ ] 支持 `paths` Frontmatter；
- [ ] 支持 Working Set 激活；
- [ ] 支持 Turn 生命周期；
- [ ] 不把全部 Rule 一次性加入 Context。

## Trust

- [ ] 未信任 Project 不自动注入；
- [ ] Trust 状态可查询；
- [ ] Project Skill 同样受 Trust 限制；
- [ ] Skill Script 不自动执行。

## Skills

- [ ] 兼容 Agent Skills Standard；
- [ ] 支持 `SKILL.md`；
- [ ] 支持 Project / User / Admin / Bundled Scope；
- [ ] 支持渐进式披露；
- [ ] 支持 Catalog Budget；
- [ ] 支持显式和隐式激活；
- [ ] 支持 Qualified Name；
- [ ] 支持 Resource Manifest；
- [ ] 支持 Snapshot 和 Resume；
- [ ] 支持 Activation 去重；
- [ ] Skill 不能扩大权限。

## Subagent

- [ ] 至少一个 `context: fork` Skill 成功运行；
- [ ] Child 使用独立 Context；
- [ ] Child 返回结构化结果；
- [ ] Tool Permission 交集正确。

## Thread / Turn / Item

- [ ] Guidance 与 Skill 产生 canonical Item；
- [ ] Snapshot 可重建；
- [ ] Resume 后行为一致；
- [ ] Trace 可审计发现和激活过程。

## 工程质量

- [ ] 所有旧测试通过；
- [ ] 新增阶段 4 测试通过；
- [ ] Ruff 通过；
- [ ] Mypy / Pyright 通过；
- [ ] GitHub Actions 通过；
- [ ] README 说明 Guidance、Skill、Tool、Permission、Memory 的区别。

---

# 三十七、阶段 4 明确不做

```text
1. 不实现 Auto Memory；
2. 不允许模型自动修改 AGENTS.md；
3. 不实现 MCP；
4. 不实现 Skill Marketplace；
5. 不在 Skill 激活时自动运行 Script；
6. 不实现 Docker 或云沙箱；
7. 不把 Skill allowed-tools 当作权限提升；
8. 不把 Guidance 当作安全强制；
9. 不做语义冲突自动合并；
10. 不在 Active Turn 中热更新 Guidance。
```

---

# 三十八、Codex 实施约束

Codex 正式编码前先提交：

```text
1. 当前 Context Builder 与阶段 4 目标的差异；
2. Guidance Discovery 流程；
3. Guidance Merge Order；
4. Trust 状态模型；
5. Path Rule 激活模型；
6. Skill Record Schema；
7. Skill Catalog 格式和预算算法；
8. activate_skill Tool Schema；
9. Skill Snapshot / Resume 方案；
10. Fork Skill 接入 Subagent 的流程；
11. 修改和新增文件列表；
12. 测试列表；
13. 与本文档不同的设计及理由。
```

必须遵守：

```text
1. 不把所有 Skill Body 在启动时加载；
2. 不用关键词硬编码代替模型选择；
3. 不静默覆盖同名 Skill；
4. 不允许 Project Skill 绕过 Trust；
5. 不允许 Skill 扩大权限；
6. 不自动执行 scripts；
7. 不将 Guidance 追加成普通 User Message；
8. 不在 Turn 中途改变 Guidance Snapshot；
9. 不提前实现 Memory 和 MCP；
10. 不删除 Thread / Turn / Item 的可恢复结构。
```

---

# 三十九、官方参考资料

## OpenAI Codex

- Custom instructions with AGENTS.md  
  https://developers.openai.com/codex/guides/agents-md/

- Build skills  
  https://developers.openai.com/codex/skills/

## Anthropic Claude Code

- How Claude remembers your project  
  https://code.claude.com/docs/en/memory

- Extend Claude with skills  
  https://code.claude.com/docs/en/skills

## Agent Skills Open Standard

- Overview  
  https://agentskills.io/home

- Specification  
  https://agentskills.io/specification

- Adding skills support  
  https://agentskills.io/integrate-skills

---

# 四十、最终结论

阶段 4 的核心不是增加两个 Markdown 读取函数。

它应建立两套生命周期不同的 Context 能力。

```text
Project Guidance：
持续、分层、按路径生效。

Agent Skills：
发现时只加载 Metadata，
任务匹配时加载完整 Instructions，
资源继续按需加载。
```

最终运行过程：

```text
Thread Start / Resume
    ↓
发现并冻结 Guidance Snapshot
    ↓
发现 Skill Metadata
    ↓
构建有限 Skill Catalog
    ↓
用户开始 Turn
    ↓
Context Builder 注入 Guidance + Catalog
    ↓
Agent 读取相关文件
    ↓
激活 Path Rule
    ↓
Agent 判断 Skill 匹配
    ↓
activate_skill
    ↓
加载 Skill Instructions
    ↓
按需读取 Resource
    ↓
Inline 执行或 Fork Subagent
    ↓
记录 Item、Snapshot 和 Trace
```

完成本阶段后，Harness 才具备成熟 Coding Agent 的项目适配能力：

```text
进入不同仓库时自动理解项目约定；
处理不同目录时获得局部规则；
执行复杂工作流时按需加载 Skill；
避免把所有知识一次性塞进上下文；
保持 Thread 可恢复和行为可审计。
```
