# Harness Agent 阶段 4 Project Guidance 与 Agent Skills 实现差异及验收记录

> 记录日期：2026-07-11  
> 对照设计：`Harness_Agent_阶段4_Project_Guidance与Agent_Skills详细设计.md`

## 1. 已完成范围

### Project Guidance

- 支持 Windows Admin、User、Project Root 到 CWD 的分层发现。
- 每层按 `AGENTS.override.md`、`AGENTS.md`、fallback 顺序选择一个非空文件。
- 默认采用 32 KiB 总预算，按完整 Document 加入或省略，不截断 Markdown。
- 支持 UTF-8 Diagnostic、Content Hash、稳定顺序和持久 Guidance Snapshot。
- 支持独立行 `@import relative.md`，包含深度、数量、总字节、循环、重复、绝对路径和 Symlink 边界控制。
- 支持 Admin、User、Project `.agents/rules/**/*.md`，以及 `paths`、`exclude` Frontmatter。
- `read_file`、`write_file`、`apply_patch`、`delete_path` 成功后更新 Confirmed Working Set；`search_text` 默认只进入 Candidate。
- Path Rule 当前 Turn 激活后持续有效，下一 Turn 重建 Working Set。
- Guidance 不写入 Conversation History，每次 Model Request 从 Snapshot 重建稳定区。
- 支持 `/guidance`、`/guidance inspect <id>`、`/guidance reload`。

### Workspace Trust

- 支持 `UNKNOWN`、`TRUSTED_ONCE`、`TRUSTED`、`UNTRUSTED`。
- Trust Once 只在当前进程保存；永久决定写入用户配置目录的 `workspace-trust.json`。
- Identity 使用规范化绝对 Workspace Root，并在 Windows 上进行大小写归一化。
- 未信任 Project Guidance 不进入 Context；未信任 Project Skill 不进入模型 Catalog，也不能激活。
- 支持 `/trust` 查询。

### Agent Skills

- 使用 PyYAML 解析 Agent Skills Standard Frontmatter，不使用字符串模拟 YAML。
- 支持 `name`、`description`、`license`、`compatibility`、`metadata`、`allowed-tools`。
- 支持 Claude 兼容字段 `disable-model-invocation`、`user-invocable`、`argument-hint`、`context`、`agent`。
- 支持 Bundled、Admin、User、`~/.agents/skills`、Project 和 Nested Project Scope。
- Discovery 只保留 Metadata 和 Resource Manifest；Skill Body 只在激活时读取。
- 支持 Qualified Name、同名歧义错误和同一 Symlink Target 去重。
- Catalog 使用模型上下文预算的 2%；未知窗口的配置回退值为 8,000 字符。
- 支持模型 `activate_skill` 和用户 `$skill-name arguments`。
- 支持 `$ARGUMENTS`、`$ARGUMENTS[N]` 和 `$N` 渲染。
- 支持 Activation Content Hash 去重、持久 Snapshot 和不依赖源文件的 Resume。
- 支持 `read_skill_resource`，限制在 Active Skill Manifest 和真实 Skill 目录内。
- `scripts/` 只进入 Manifest，不会自动执行。
- 支持 `/skills`、`/skills active`、`/skills inspect <name>`、`/skills reload`。
- 支持 `[[skills.config]] enabled=false` 隐藏指定 Skill。

### Fork Skill

- Bundled `code-review-fork` 使用 `context: fork` 和 `agent: reviewer`。
- Child 初始任务只包含 Skill 内容、参数和显式上下文，不包含 Main Thread 完整历史。
- Child 使用结构化 `submit_result` 返回结果。
- Child Tool 是 Child AgentDefinition 和 Skill `allowed-tools` 的交集；Parent 原始权限不会被扩大。
- 自动化集成测试已真实运行 Fork、Child Model Loop 和结构化结果回传。

## 2. 与设计或官方系统不同的实现

### 2.1 `allowed-tools` 采用“权限收窄”语义

Agent Skills Standard 将 `allowed-tools` 描述为实验性的预批准工具；Claude Code 也可用它减少审批。

Harness 不采用该语义。Harness 的计算方式是：

```text
Agent Effective Tools
∩ Skill allowed-tools
```

Skill 不能预批准工具、绕过 Permission 或扩大 Parent/Child 权限。这是为了与阶段 3 的安全模型保持一致。

### 2.2 Guidance 使用稳定 System Section

Claude Code 官方实现会把 `CLAUDE.md` 作为 System Prompt 后的 User Message。Harness 按阶段 4 设计使用独立的稳定 System Section，并且不把 Guidance 写入 Canonical Conversation History。

这样可以避免 Resume 时重复追加，也能让 Snapshot 和历史消息分别管理。

### 2.3 第一版不做文件监听热更新

Claude Code 当前可以监听 Skill 文件变化。Harness 只在以下时点刷新：

```text
Thread Start
Thread Resume
/guidance reload
/skills reload
```

Active Turn 中不替换 Snapshot，以保证同一 Turn 行为稳定和可复现。

### 2.4 Import 语法比 Claude Code 更严格

Harness 只识别独立行：

```text
@import relative/path.md
```

代码块中的 Import 不解析，绝对路径 Import 默认拒绝。Claude Code 支持更宽松的行内 Import 和经过确认的外部 Import。

### 2.5 Skill Resource 第一版只返回 UTF-8 文本

`assets/` 中二进制文件会进入 Manifest，但 `read_skill_resource` 不直接返回二进制内容。后续应由专门的图片、PDF 或二进制资源 Tool 处理。

## 3. 当前仍未达到设计完整版的地方

以下内容没有伪装为已完成：

1. `/skills disable <name>` 尚未直接改写用户 TOML；当前可通过 `[[skills.config]] enabled=false` 配置并 Reload。
2. Working Set 尚未从用户自然语言中解析显式文件路径，也尚未自动消费 Subagent Evidence Path。
3. `search_text` 当前只把搜索目录参数记录为 Candidate，没有逐个解析 Tool Result 中的候选文件。
4. Admin Guidance 解码失败会记录 Diagnostic，但尚未提供可配置的 Admin fail-closed 启动策略。
5. Fork Child 由现有 Subagent Scheduler 管理并写 Trace/结构化结果；尚未把每个 Child 映射为 `LocalThreadStore` 下完整独立可恢复 Thread。
6. Active Skill 已有 `durable_guidance` 和 `protected_from_normal_pruning` 标记，但完整 Context Compaction 算法仍未在当前项目中存在。
7. Trust Identity 当前以规范 Workspace Root 为主，尚未加入 Git Remote 和 Repository Identity 组合校验。
8. Guidance HTML Comment stripping 配置尚未实现。
9. `/guidance reload` 和 `/skills reload` 只在当前同步 CLI 的 IDLE 输入边界执行；尚未实现 ACTIVE Turn 排队对象，因为 CLI 在 Turn 执行期间不接收下一条命令。

这些差异不涉及沙箱。按照用户要求，阶段 4 没有修改或扩展 Windows Sandbox 实现。

## 4. 验收结果

已执行：

```text
python -m ruff check src tests
python -m mypy src
python -m pytest -q
```

阶段 4 新测试覆盖：

- Guidance override、nested、fallback、预算和 Trust Gate；
- Import cycle、代码块和边界；
- Path Rule 激活与 Turn 内持续；
- Trust Once 和持久 Trust；
- Skill Metadata-only Catalog；
- Untrusted/model-disabled Skill 隐藏；
- Activation 去重、参数渲染、Snapshot Resume；
- Qualified Name Collision；
- Resource Manifest 和逃逸拒绝；
- Bundled Fork Skill、独立 Reviewer 和结构化结果；
- Guidance/Skill 不写入 Conversation History。

最终结果：

```text
Ruff：通过
Mypy：90 个源文件通过
Pytest：76 passed, 4 skipped
compileall：通过
git diff --check：通过
```

4 个 Skip 是需要特定平台能力或真实外部 Provider 的既有测试，不是阶段 4 功能失败。
