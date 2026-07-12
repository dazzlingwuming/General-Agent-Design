# General Agent Design 一到五阶段加固最终实施差异与验收记录

> 日期：2026-07-12  
> 实施依据：`doc/General-Agent-Design_一到五阶段问题分析与实施方案.md`

## 完成范围

### 第一批：可信基线

- WSL 路径转换提取为宿主无关纯函数；平台测试明确分类。
- CI 增加 Ubuntu/Windows core、Linux platform、`workflow_dispatch`、最小权限和 concurrency。
- 使用唯一 `uv.lock`；CI 固定 setup-uv commit 和 uv 版本。
- 测试分类为 unit、integration_local、platform_linux、platform_windows、live_provider、live_oauth。
- 修复锁定 Mypy 揭示的类型问题；` doc` 已迁移为 `doc`。
- 新增根 README/AGENTS，函数 docstring 规则已固化。

### 第二批：生命周期、安全和所有权

- Subagent cancel 等待 task cleanup 后才写终态；force close 不越过清理。
- RolloutRecorder 使用 OPEN/FAILED/CLOSING/CLOSED sticky 状态，失败不复活，ack 不挂死。
- incomplete Turn resume 只创建一个 recorder，重复 resume 幂等。
- TurnController 统一 completed/failed/cancelled/interrupted，主动取消写唯一 `turn.cancelled` 并回收 metadata。
- ApprovalGrantStore 按 thread/turn/principal/tool/argument fingerprint/target scope 授权；Root grant 不泄露 child。
- `CANCEL_TURN` 是控制流，不再包装为普通 Tool error；审批参数先脱敏。
- invalid Admin MCP 配置 fail closed；不存在与无效严格区分。
- MCP Catalog copy-on-write 保留 last-good；health 与 availability 分离；generation single-flight。
- ArtifactStore 由 trace thread path 注入，不再硬编码 workspace 默认路径。

### 第三批：Stage 5 正式本地闭环

- 真实 Streamable HTTP 404：Resource/Prompt 重建并重试一次；Tool 重建但不重放。
- 真实 stdio/HTTP pagination：四类 Catalog 各 5 项、3 页 opaque cursor。
- External Context：用户选择、非 System、untrusted 标签、hash 去重、pending/active/resume、Artifact 回退。
- Approval UI：scope、identity、remote/canonical、mode/source、risk/side effect、annotation trust、principal、脱敏参数。
- Binary Artifact：Image/Audio、MIME sniff、base64、host filename、SHA-256、原子写、item/turn/thread quota、cleanup。
- Subagent MCP：scripted provider、显式子集、共享 connection、child principal attribution。
- Local OAuth：PRM/OASM、dynamic registration、PKCE、code exchange、refresh、invalid_grant、logout、identity 隔离。

## 与方案不同之处

1. MCP SDK 1.28.1 将真实 HTTP 404 折叠为精确 `McpError: Session terminated`；Host 在 typed status 后兼容该错误。
2. SDK Streamable HTTP context 要求同 task 退出 AnyIO CancelScope，因此实现了 connection owner task，而不是跨 task 直接 `AsyncExitStack.aclose()`。
3. SDK low-level Resource Template decorator 不接受分页 result；fixture 使用 SDK request handler 注册点返回标准分页类型。
4. OAuth SDK storage contract 不保存绝对过期时间；Keyring envelope 增加 `stored_at`，薄 provider 子类在重启时恢复 expiry，兼容旧 token JSON。
5. BM25 未实现。方案明确它不是 Stage 5 阻塞项；当前保持稳定 substring，待目录指标证明需要时再引入 FTS5。
6. 包版本保持 `0.1.0`，版本提升由发布策略决定，不用版本号代表阶段状态。

## 本地验收

```text
uv lock --check                                      passed, 58 packages
python -m ruff check src tests                       passed
python -m mypy src                                   passed, 114 source files
python -m pytest -m "unit or integration_local" -q  119 passed, 1 skipped, 3 deselected
git diff --check                                     passed
```

唯一 core skip 是当前 Windows 权限不允许创建 symlink。Linux CI 应实际执行该测试。

## 未宣称完成

- Windows Restricted Token、ACL、Job Object、WSL/bubblewrap 沙箱完善；
- SSE、Sampling、Elicitation、Tasks、Apps、Resource Subscription；
- 跨进程恢复旧 HTTP MCP Session ID；
- Context Compaction、通用 Unified Diff Parser；
- 外部 OAuth provider 与真实 DeepSeek live smoke；
- 外部 OAuth provider 与真实 DeepSeek live smoke 不属于默认 CI。

## GitHub Actions 验收

- Commit：`7c59c158a2ba860683ea890a22548ec0c476e141`
- Workflow Run：`https://github.com/dazzlingwuming/General-Agent-Design/actions/runs/29184509102`
- Conclusion：`success`
- Jobs：Ubuntu/Windows core、Linux platform。

准确状态是：一到五阶段本方案规定的三批加固已完成本地确定性实现、真实本地协议验收和对应 SHA 的 GitHub CI 验收；沙箱和方案明确延期能力仍未完成。
