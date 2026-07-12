# General Agent Design

本仓库实现一个本地 CLI Coding Agent Harness。当前形态是持续交互的 Thread/Turn/Item Runtime，已包含工具调用、Subagent、权限审批、Project Guidance、Agent Skills 和 MCP Client Runtime。

项目尚未完成 Native Windows 沙箱、Context Compaction，以及文档中明确延期的 MCP 扩展协议。当前状态与阶段差异以 [`doc/`](doc/) 下的设计和验收记录为准，不能把阶段骨架或单元测试等同于真实环境验收。

## 快速开始

```powershell
cd agent-harness
python -m pip install -e .
agent-harness setup
```

随后进入要处理的项目目录并运行：

```powershell
agent-harness
```

详细配置、CLI 命令和数据目录见 [`agent-harness/README.md`](agent-harness/README.md)。

## 开发与测试

项目使用 `uv.lock` 固定开发和 CI 依赖：

```powershell
cd agent-harness
uv sync --locked --extra test
uv run --no-sync python -m ruff check src tests
uv run --no-sync python -m mypy src
uv run --no-sync python -m pytest -m "unit or integration_local" -q
```

平台测试和真实外部服务测试单独运行，默认核心门禁不需要 API Key、浏览器登录或 WSL distribution。

## 目录

- `agent-harness/src/agent_harness/`：Runtime 实现。
- `agent-harness/tests/`：单元、本地集成、平台和 live 测试。
- `doc/`：阶段设计、实施差异和验收记录。
- `.github/workflows/agent-harness.yml`：锁定环境质量门。

## 当前边界

- Windows Restricted Token、ACL、Job Object 和 WSL 沙箱完善仍延期。
- SSE、Sampling、Elicitation、Tasks、Apps、Resource Subscription 和跨进程 MCP Session 恢复仍延期。
- 没有对应提交的 GitHub Actions 绿色记录时，只能描述为本地验证通过。
