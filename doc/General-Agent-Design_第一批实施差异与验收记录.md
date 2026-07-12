# General Agent Design 第一批实施差异与验收记录

日期：2026-07-12

## 实施范围

- 提取宿主无关的 Windows 到 WSL 路径转换纯函数。
- 将测试划分为 `unit`、`integration_local`、`platform_linux`、`platform_windows`、`live_provider` 和 `live_oauth`。
- 将 GitHub Actions 拆分为 Ubuntu/Windows 核心门禁和 Linux 平台门禁，加入手动触发、最小权限和并发取消。
- 采用单一 `uv.lock` 锁定方案，CI 使用固定 setup-uv 提交和固定 uv 版本安装依赖。
- 修复锁定环境中 Mypy 2.2.0 揭示的 dataclass 与 MCP 浮点配置类型问题。
- 将带前导空格的 ` doc/` 迁移为 `doc/`，新增根 README 和仓库级 `AGENTS.md`。
- 更新包描述和测试命令，但不擅自改变发布版本号。

## 与方案不同之处

1. 本地验收使用现有 CPython 3.12.7；Workflow 固定使用文档要求的 Python 3.11。Python 3.11 的最终结果必须由 GitHub runner 或独立干净环境证明。
2. Windows/WSL 平台测试在本机因 WSL distribution 中没有 bubblewrap 而跳过。只证明测试分类正确，不代表 WSL 沙箱通过。
3. 核心测试有一个 symlink 测试因当前 Windows 权限不允许创建符号链接而跳过。skip reason 明确，Ubuntu CI 应实际执行该测试。
4. 后续完整实现提交 `7c59c158a2ba860683ea890a22548ec0c476e141` 已取得 GitHub Actions 绿色结果。
5. 包版本仍为 `0.1.0`。版本提升属于发布策略，不用版本号代替阶段完成状态。

## 本地验收

```text
uv lock --check
Resolved 58 packages

uv run --no-sync python -m ruff check src tests
All checks passed

uv run --no-sync python -m mypy src
Success: no issues found in 109 source files

uv run --no-sync python -m pytest -m "unit or integration_local" -q -rs
97 passed, 1 skipped, 3 deselected

uv run --no-sync python -m pytest -q -rs
97 passed, 4 skipped
```

## 远端验收

- Workflow Run：`https://github.com/dazzlingwuming/General-Agent-Design/actions/runs/29184509102`
- Commit：`7c59c158a2ba860683ea890a22548ec0c476e141`
- Conclusion：`success`
- 覆盖 Ubuntu/Windows core 与 Linux platform jobs。

仍不属于该 CI 证明的事项：具备 WSL2+bubblewrap 的 Windows platform acceptance，以及正式发布版本提升。
