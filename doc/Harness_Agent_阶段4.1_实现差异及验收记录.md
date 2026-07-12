# Harness Agent 阶段 4.1 实现差异及验收记录

> 日期：2026-07-11  
> 依据：`Harness_Agent_阶段4.1_已知问题修复与生命周期加固方案.md`  
> 原则：以当前项目结构和可验证行为为准，不机械照搬建议目录；不修改沙箱路线，不进入 MCP。

## 1. 本次完成内容

### 1.1 Skill 生命周期

- 新增 `SkillInvocationService`，用户 `$skill` 与模型 `activate_skill` 共用同一调用管线。
- 新增 `SkillInvocationRequest`、`SkillInvocationSource` 和 `SkillInvocationResult`。
- 新增 `SkillExecution` 与 `SkillExecutionRegistry`，把可持久化 Activation 和有期限的 Execution 分开。
- 用户显式 `$fork-skill` 会真正启动 Child Agent，并等待结构化 `submit_result` 后再由 Root Agent 生成回答。
- 原始 `$skill` 用户文本不再被参数文本替换，会原样进入 Rollout 和模型历史。
- Inline Skill 的 `allowed-tools` 只在当前 Root Turn 的 Active Execution 中生效，Turn 结束后恢复。
- Fork Skill 的工具约束只通过 `DelegationRequest.allowed_tools` 传给 Child，不收窄 Parent。

### 1.2 Resource 授权与一致性

- `read_skill_resource` 优先使用 `activation_id`，兼容使用已激活 Skill 名查找 Activation。
- Resource 读取不再调用 `SkillManager.resolve()`，因此不会错误重复执行模型调用 Gate。
- 用户专用 `disable-model-invocation: true` Skill 激活后可以读取 Manifest 内资源，同时模型仍不能主动激活该 Skill。
- Resource Manifest 新增 SHA-256；文件缺失、越界或内容变化时拒绝读取，要求重新激活。
- 只有存在模型可调用 Skill 时才注册 `activate_skill`；存在 Skill Record 或 Activation 时独立注册 `read_skill_resource`。

### 1.3 Project Paths 与初始化

- 新增 `ProjectPaths(project_root, workspace_root, cwd)` 和 Git Root 解析。
- Thread Metadata 持久化 `project_root`、`workspace_root` 和 `cwd`，Resume 后保持启动目录语义。
- Guidance 按 `project_root -> cwd` 目录链发现。
- Project Skill 只检查目录链每层的 `.agents/skills`，不再全仓库 `rglob(".agents/skills")`，不会加载兄弟目录 Skill。
- Guidance 与 Skills 使用独立 `SubsystemInitState`，关闭任一子系统不会让另一子系统每 Turn 重扫。
- `/guidance reload` 与 `/skills reload` 已拆开；Skills Reload 会从 Activation Snapshot 恢复激活内容。

### 1.4 扫描、预算与持久化

- Untrusted Project Guidance/Path Rule 在预算计算前跳过，不再挤占可信 Guidance 预算。
- Skill Discovery 改为 `os.walk(topdown=True, followlinks=False)`，进入目录前剪除 `.git`、`node_modules`、`.venv`、`.harness` 等目录。
- Discovery 使用流式 Frontmatter Reader，读到第二个 `---` 即停止，不读取正文。
- 新增 Skill 文件、Frontmatter、正文和资源数量限制。
- 新增统一 `atomic_write_text()` 和 `atomic_write_json()`。
- Workspace Trust、Guidance Snapshot、Skill Activation Snapshot 和 Thread Metadata 改为同目录临时文件加 `os.replace()`。
- Workspace Trust 增加进程内 `RLock`，避免同一进程内并发读改写覆盖。

## 2. 与方案文档不同之处

### 2.1 未机械拆成建议目录

方案建议把 Activation、Execution、Invocation、Resource Runtime 拆成更多模块。本项目保留现有 `skills/activation.py`、`skills/resources.py` 和 `runtime/run_manager.py`，只新增 `skills/execution.py`、`skills/invocation.py`、`project/roots.py`、`utils/atomic_files.py`。原因是现有模块规模仍可控，继续拆分会增加接口迁移而没有直接改善行为。

### 2.2 显式调用采用 Turn 开始前的 Pending Request

用户 `$skill` 不在 `ConversationSession` 内直接执行，而是保留原始消息并加入 `pending_skill_invocations`，待 `RunManager` 创建本 Turn 的 Scheduler 后交给统一 Service。这样 User Fork 与 Model Fork 能共享同一个 Child Runtime，且不会维护两套 Fork 实现。

### 2.3 Resource Tool 暂时兼容 Skill Name

方案推荐强制输入 `activation_id`。当前实现新增 `activation_id`，但为了兼容阶段 4 已有调用，也允许按当前 Thread 的已激活 Skill 名查找最近 Activation。该兼容路径不会重新执行 Invocation Gate，也不能访问其他 Thread 的 Manager。

### 2.4 Execution 只做运行时状态，不跨进程恢复

Activation Snapshot 跨 Turn 和 Resume 保留；Execution 是 Turn/Child 生命周期状态，目前不持久化和恢复。异常恢复仍由已有 `turn.interrupted` 机制处理。本项与方案“跨进程 SkillExecution Resume 可延期”一致。

### 2.5 原子写范围按关键状态实施

本次覆盖 Trust、Guidance Snapshot、Skill Activation Snapshot 和 Thread Metadata。Append-only Rollout 继续由单写入器追加；普通 Turn Result 和 Child Result 尚未全部切换为原子替换，因为它们不是恢复 Activation/Trust 的授权依据。后续可统一，但不阻塞阶段 5。

### 2.6 Project Root 默认边界

从 Git 仓库子目录启动且未显式传入 Workspace Root 时，`workspace_root` 默认提升为 Git Root，与方案推荐一致。Resume 使用 Thread Metadata 中的原始 `cwd`，不会退化成 Git Root CWD。

## 3. 未完成或延期项

- GitHub Actions 的真实成功记录尚未产生。本地测试成功不能冒充远端 CI；需推送本次提交后查看 Actions。
- Child 独立 `LocalThreadStore` 持久化仍沿用阶段 2 方案，未在本阶段改造。
- Active Turn 中 Reload 排队、跨进程 Execution Resume、二进制 Resource、Git Remote Trust Identity 按原方案延期。
- Skill Resource Manifest 当前会读取资源内容计算 Hash；它不读取正文进入 Context，但严格说不是完全零内容 I/O。这样做是为了在 Activation Snapshot 中提供可验证一致性，属于有意取舍。
- `SkillExecution` 已支持 Active/Completed/Failed/Cancelled 状态模型；当前同步 Fork 路径会记录 Completed/Failed，用户中断导致的独立 `skill.execution_cancelled` 事件尚未建立专门信号链。

## 4. 测试覆盖

新增 `tests/unit/test_phase41_hardening.py`，覆盖：

- 用户与模型共用 Invocation Service；
- Inline 当前 Turn 收窄、下一 Turn 恢复；
- 用户专用 Skill Resource；
- Resource 内容变化拒绝；
- Root 到 CWD Skill 链与兄弟目录隔离；
- Untrusted Guidance 不占预算；
- `node_modules` 前置剪枝；
- Frontmatter Reader 不读取 Body；
- 原子替换失败保留旧文件。

扩展 `tests/integration/test_phase4_runtime.py`，覆盖用户 `$code-review-fork`：

- 真实创建 Reviewer Child；
- Child 调用 `submit_result`；
- Root 再次生成最终回答；
- Rollout 保留原始 `$skill` 文本；
- Rollout 记录 `invocation_source=user_explicit`。

## 5. 本地验收结果

```text
Ruff:       All checks passed
Mypy:       Success, 95 source files
Pytest:     83 passed, 4 skipped
compileall: passed
diff check: passed
```

4 个 skipped 为平台/环境条件测试，不是本次新增失败。

## 6. 阶段 4.1 验收判断

本地可验证的核心生命周期、作用域、路径、扫描、预算和关键持久化问题已经完成。进入阶段 5 前仍需把本次变更推送到 GitHub，并确认 GitHub Actions 真实成功；在该记录出现前，方案中的最后一项不能标记为完成。
