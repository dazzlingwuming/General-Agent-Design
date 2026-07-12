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
4. 当前没有对应未提交改动 SHA 的 GitHub Actions 运行，因此只能记录本地通过，不能记录 CI 已完成。
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

## 尚未完成

- 推送后取得对应 commit SHA 的 Ubuntu/Windows 核心门禁和 Linux 平台门禁绿色记录。
- 在具备 WSL2 和 bubblewrap 的 Windows 环境执行 `platform_windows` 验收。
- 根据正式发布策略决定是否提升包版本。

在上述 CI 记录产生前，第一批状态是“实现和本地质量门完成，远端正式验收待完成”。
