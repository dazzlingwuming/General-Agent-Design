# Agent Harness 阶段 2

这是一个本地 CLI Coding Agent Harness。当前已完成阶段 1 单 Agent Runtime 和阶段 2 Subagent Runtime：Root Agent 可以在同一任务中创建、等待、追加指令、取消和关闭只读 Child Agent。

## 范围

已实现：

- shared root/child agent loop
- completion policy for root text final output and child structured output
- `list_files`, `read_file`, `search_text`
- phase 2 subagent control tools: `spawn_subagent`, `wait_subagents`, `get_subagent_status`, `send_subagent_message`, `cancel_subagent`, `close_subagent`
- child-agent terminal tool: `submit_result`
- workspace boundary checks and a basic secret-file denylist
- fake scripted provider
- DeepSeek chat-completions adapter
- in-memory session/run state
- session trace, task trace, and `result.json`
- CLI commands: `code`, `exec`, `sessions`, `tools`, `inspect`
- interactive session mode with multiple user turns

阶段 2 已加入：

- 静态 Agent Registry：`explorer`、`reviewer`、`test_analyst`
- Root Agent 通过 Tool 创建和管理 Child Agent
- Child Agent 使用独立消息历史和只读工具白名单
- `spawn_subagent` 非阻塞返回 handle
- `wait_subagents` 显式等待结构化结果
- wait timeout 不取消 child
- idle child follow-up 复用同一 thread
- Child Agent 通过 `submit_result` 返回结构化结果
- Global / Local child call budget accounting
- 并发安全 JSONL trace sequence
- Root Run 结束前清理活动 Child Agent
- `result.json` 增加 `agent_summary`

阶段 2 仍未实现：

- 跨进程恢复
- 数据库队列
- Web UI Agent 面板
- 两层以上嵌套
- 完整权限引擎和 Docker Sandbox

## 安装

```bash
python -m pip install -e .[test]
```

## 使用 Fake Provider 运行

```bash
agent-harness exec --provider fake --workspace tests/fixtures/demo_repo --task "请找出 calculate_total 的定义，并说明折扣计算流程。"
```

## 像 Coding Agent 一样进入目录后使用

先进入你要分析的目录：

```powershell
cd "D:\APP_self\临时的测试目录"
```

然后直接运行：

```powershell
agent-harness code --model v4-flash
```

或者直接运行：

```powershell
agent-harness
```

命令会进入连续对话：

```text
Session ID: session_xxx
输入 /exit 退出，/new 开启新会话，/status 查看当前会话。
> 
```

你可以连续输入，例如：

```text
> 请分析这个项目的目录结构、主要模块和入口文件。
> 详细看看入口文件。
> 根据刚才的内容总结一下。
```

如果只想执行一次，可以一行运行：

```powershell
agent-harness code --model v4-flash --task "请分析这个项目的目录结构、主要模块和入口文件。"
```

## 首次配置真实 DeepSeek-compatible 接口

首次使用先运行：

```powershell
agent-harness setup
```

它会提示你输入：

- DeepSeek API URL
- API Key
- 默认模型

配置会保存到 Windows 用户目录：

```text
%APPDATA%\agent-harness\config.toml
```

可选模型：

```text
v4-flash -> deepseek-v4-flash
v4-pro   -> deepseek-v4-pro
```

示例：

```bash
agent-harness code --model v4-flash --task "请分析当前目录的主要模块和入口文件。"
agent-harness exec --model v4-pro --workspace tests/fixtures/demo_repo --task "请找出 calculate_total 的定义和调用位置，并说明折扣计算流程。"
```

## 查看工具和运行结果

```bash
agent-harness sessions
agent-harness tools --workspace tests/fixtures/demo_repo
agent-harness inspect --session <session_id>
```

交互式 Session 写入 `.harness/sessions/<session_id>/`，其中：

```text
session.json
transcript.jsonl
events.jsonl
turns/turn_0001-result.json
result.json
```

一次性 `exec` 任务仍写入 `.harness/runs/<task_id>/`。交互式对话优先看 `.harness/sessions/`，不要把旧 `.harness/runs/` 当作当前 session 记录。

Subagent 结果在对应 trace 目录下：

```text
agents/<agent_id>/turn-0001-result.json
```

## 测试

```bash
pytest
pytest -m live
```

Live tests 会读取真实配置；如果没有 `DEEPSEEK_API_KEY`，会自动跳过。默认测试使用 Fake Provider，不会调用真实 API。

## 安全限制

当前只有应用层工作区检查、输出限制和只读工具。它没有 Docker Sandbox，也没有完整 Permission Engine。不要把它当作可以安全处理不可信仓库或不可信模型/工具行为的沙箱。
