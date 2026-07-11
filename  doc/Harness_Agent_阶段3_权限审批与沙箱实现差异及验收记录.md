# Harness Agent 阶段 3 权限审批与沙箱实现差异及验收记录

> 记录日期：2026-07-11  
> 开发主机：Windows  
> 设计依据：`Harness_Agent_阶段3_权限审批与OS原生沙箱详细设计.md`

## 一、已完成

- 建立独立 `security` 领域：Capability、RiskLevel、SandboxMode、ApprovalPolicy、Principal、Rule、PermissionEvaluation。
- ToolRuntime 强制 Principal；无 Principal 的调用直接拒绝。
- 完成 `DENY > ASK > ALLOW` 和未信任项目 ALLOW 失效机制。
- 完成工作区逃逸、绝对路径、符号链接最终目标、`.env`、密钥文件和 `.harness/threads` 阻断。
- 完成 once、turn、thread 进程内审批；`never` 策略下 ASK 直接拒绝。
- 完成 PreToolUse、PostToolUse Hook；Hook 不能覆盖 DENY，异常和超时失败关闭。
- 完成 `write_file`、`apply_patch`、`delete_path` 和结构化 `run_command`。
- 完成 FakeSandbox、NoSandbox、Linux Bubblewrap、Windows WSL2 Bubblewrap 后端。
- Windows 自动后端只选择 WSL2 Bubblewrap；不可用时不回退 Host 执行。
- 完成网络默认关闭、环境白名单、输出截断、超时和进程组清理的后端实现。
- Permission、Approval、Hook、Sandbox、Command、File Change 事件写入 Trace；交互 Thread 同时写入 Rollout。
- 增加 `/permissions`、`/sandbox`、`/approvals` 和 Linux GitHub Actions。

## 二、与设计文档不同的实现

### 1. Windows 正式后端

本实现遵循设计文档的 WSL2 + bubblewrap 路线，没有实现 Native Windows Restricted Token、ACL、Job Object 和 Firewall Helper。单纯使用 Python `subprocess`、路径检查或 Job Object 不能构成文档要求的完整 OS 安全边界。

### 2. apply_patch 输入

第一版使用 `path + old_text + new_text`，要求 `old_text` 在目标文件中精确出现一次。没有实现通用 Unified Diff、多文件 Patch 和删除 Patch，因为当前项目没有成熟 Patch Parser，不能让未经充分验证的字符串解析承担安全边界。

### 3. Persistent User Rule

当前支持从用户配置读取持久规则，但审批界面尚未提供 `ALLOW_RULE` / `DENY_RULE` 自动写入。安全写回还需要原子 TOML 编辑、重复规则合并和规则预览。

### 4. Child Approval

Child 当前只有只读工具和 `submit_result`，权限由 Agent Definition 与 Principal 双重收窄。Parent Ceiling 已生效，但尚无 Child 副作用工具触发 Approval 冒泡的真实交互路径。

### 5. Pending Approval 恢复

只支持当前进程内等待和恢复。退出后精确恢复待执行 Tool Call 未实现，符合阶段文档暂不实现范围。

### 6. Windows 进程树

受保护模式的 Windows 命令运行在 WSL2/bubblewrap 中。`danger-full-access` 下的 Native Windows NoSandbox 只终止直接进程，不声称具备完整 Job Object 进程树安全。

## 三、本机验收结果

```text
pytest: 62 passed, 4 skipped
compileall: passed
ruff: passed
mypy: passed（76 个源码文件）
fake-provider CLI: COMPLETED
```

本机 `wsl.exe` 存在，但没有安装 Linux 发行版，因此没有可用 `bubblewrap`。真实 WSL2 Bubblewrap Integration Test 已编写但被跳过。以下项目不能在本机声称验收通过：

- read-only 和 workspace-write 真实 OS 边界；
- 网络命名空间隔离；
- Host Home 可见性限制；
- bubblewrap 子进程继承边界；
- WSL2 内 timeout/cancel 进程树清理。

当前 Conda 环境已安装项目测试依赖，Ruff 和 Mypy 均已通过。Linux CI 已配置，但远端 GitHub Actions 尚未在本次本地实现中运行。

## 四、不能声称的能力

- Native Windows 安全沙箱已经完成；
- 任意不可信代码绝对安全；
- 域名级网络代理或自动安全注入 Secret；
- 持久审批规则自动写回；
- Child 副作用审批冒泡已经端到端验收；
- 本机真实 Bubblewrap 集成测试已经通过。
