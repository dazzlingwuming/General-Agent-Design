from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from agent_harness.config import MODEL_ALIASES, ProviderConfig, default_user_config_path, load_config, normalize_model_name, write_user_config
from agent_harness.domain.errors import HarnessError
from agent_harness.domain.run import RunStatus
from agent_harness.providers.deepseek import DeepSeekProvider
from agent_harness.providers.fake import default_demo_provider
from agent_harness.rollout.items import RolloutItem
from agent_harness.runtime.run_manager import RunManager
from agent_harness.runtime.session import ConversationSession
from agent_harness.threads.local_store import LocalThreadStore
from agent_harness.tools.builtins.factory import create_default_registry
from agent_harness.security.approval import ConsoleApprovalHandler
from agent_harness.security.models import ApprovalPolicy, SandboxMode
from agent_harness.utils.serialization import to_jsonable
from agent_harness.guidance.trust import WorkspaceTrustState, WorkspaceTrustStore
from agent_harness.mcp.auth import KeyringTokenStorage, credential_identity
from agent_harness.utils.atomic_files import atomic_write_json
from agent_harness.mcp.config import parse_server_config
from agent_harness.mcp.models import MCPConfigScope
from agent_harness.mcp.connection import MCPServerConnection
from agent_harness.checkpoints.store import CheckpointStore
from agent_harness.recovery.coordinator import RecoveryCoordinator
from agent_harness.memory.store import MemoryStore, project_identity


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for code, run, tools, and inspect commands."""
    parser = argparse.ArgumentParser(prog="agent-harness")
    sub = parser.add_subparsers(dest="command")

    code = sub.add_parser("code")
    code.add_argument("task_text", nargs="*")
    code.add_argument("--task")
    code.add_argument("--provider", choices=["deepseek", "fake"])
    code.add_argument("--model", choices=sorted(MODEL_ALIASES))
    code.add_argument("--config")
    code.add_argument("--max-iterations", type=int)
    code.add_argument("--trace-dir")
    code.add_argument("--sandbox", choices=[mode.value for mode in SandboxMode])
    code.add_argument("--approval-policy", choices=[policy.value for policy in ApprovalPolicy])
    code.add_argument("--danger-full-access", action="store_true")

    setup = sub.add_parser("setup")
    setup.add_argument("--provider", choices=["deepseek"], default="deepseek")

    exec_cmd = sub.add_parser("exec")
    exec_cmd.add_argument("--workspace", required=True)
    exec_cmd.add_argument("--task", required=True)
    exec_cmd.add_argument("--provider", choices=["deepseek", "fake"])
    exec_cmd.add_argument("--model", choices=sorted(MODEL_ALIASES))
    exec_cmd.add_argument("--config")
    exec_cmd.add_argument("--max-iterations", type=int)
    exec_cmd.add_argument("--trace-dir")
    exec_cmd.add_argument("--sandbox", choices=[mode.value for mode in SandboxMode])
    exec_cmd.add_argument("--approval-policy", choices=[policy.value for policy in ApprovalPolicy])
    exec_cmd.add_argument("--danger-full-access", action="store_true")

    run = sub.add_parser("run", help=argparse.SUPPRESS)
    run.add_argument("--workspace", required=True)
    run.add_argument("--task", required=True)
    run.add_argument("--provider", choices=["deepseek", "fake"])
    run.add_argument("--model", choices=sorted(MODEL_ALIASES))
    run.add_argument("--config")
    run.add_argument("--max-iterations", type=int)
    run.add_argument("--trace-dir")
    run.add_argument("--sandbox", choices=[mode.value for mode in SandboxMode])
    run.add_argument("--approval-policy", choices=[policy.value for policy in ApprovalPolicy])
    run.add_argument("--danger-full-access", action="store_true")

    tools = sub.add_parser("tools")
    tools.add_argument("--workspace", default=".")

    threads = sub.add_parser("threads")
    threads.add_argument("--thread-dir", default=".harness/threads")

    sessions = sub.add_parser("sessions", help=argparse.SUPPRESS)
    sessions.add_argument("--session-dir", default=".harness/threads")

    resume = sub.add_parser("resume")
    resume.add_argument("thread_id", nargs="?")
    resume.add_argument("--provider", choices=["deepseek", "fake"])
    resume.add_argument("--model", choices=sorted(MODEL_ALIASES))
    resume.add_argument("--config")
    resume.add_argument("--max-iterations", type=int)
    resume.add_argument("--trace-dir")
    resume.add_argument("--sandbox", choices=[mode.value for mode in SandboxMode])
    resume.add_argument("--approval-policy", choices=[policy.value for policy in ApprovalPolicy])
    resume.add_argument("--danger-full-access", action="store_true")

    recover = sub.add_parser("recover")
    recover.add_argument("thread_id")
    recover.add_argument("--status", action="store_true")

    memory = sub.add_parser("memory")
    memory_sub = memory.add_subparsers(dest="memory_command", required=True)
    memory_sub.add_parser("list")
    memory_search = memory_sub.add_parser("search")
    memory_search.add_argument("query")
    memory_add = memory_sub.add_parser("add")
    memory_add.add_argument("content")
    memory_add.add_argument("--thread-id", default="user")
    memory_invalidate = memory_sub.add_parser("invalidate")
    memory_invalidate.add_argument("memory_id")
    memory_invalidate.add_argument("--reason", default="user_invalidated")
    memory_delete = memory_sub.add_parser("delete")
    memory_delete.add_argument("memory_id")

    inspect = sub.add_parser("inspect")
    inspect.add_argument("id")
    inspect.add_argument("--trace-dir", default=".harness/runs")
    inspect.add_argument("--session", action="store_true")
    inspect.add_argument("--thread", action="store_true")

    migrate = sub.add_parser("migrate-sessions")
    migrate.add_argument("--session-dir", default=".harness/sessions")
    migrate.add_argument("--thread-dir", default=".harness/threads")
    mcp = sub.add_parser("mcp")
    mcp_sub = mcp.add_subparsers(dest="mcp_command", required=True)
    mcp_sub.add_parser("list")
    mcp_get = mcp_sub.add_parser("get")
    mcp_get.add_argument("name")
    mcp_add = mcp_sub.add_parser("add")
    mcp_add.add_argument("name")
    mcp_add.add_argument("target")
    mcp_add.add_argument("server_args", nargs="*")
    mcp_add.add_argument("--transport", choices=["stdio", "streamable_http"], default="stdio")
    mcp_add.add_argument("--bearer-token-env-var")
    mcp_add.add_argument("--oauth", action="store_true")
    mcp_remove = mcp_sub.add_parser("remove")
    mcp_remove.add_argument("name")
    mcp_logout = mcp_sub.add_parser("logout")
    mcp_logout.add_argument("name")
    mcp_login = mcp_sub.add_parser("login")
    mcp_login.add_argument("name")
    return parser


def _apply_common_run_args(config, args: argparse.Namespace) -> None:
    """Apply provider, model, budget, and trace CLI overrides to config."""
    if args.provider:
        config.provider.name = args.provider
    if args.model:
        config.provider.model = normalize_model_name(args.model)
    if args.max_iterations:
        config.run.max_iterations = args.max_iterations
    if args.trace_dir:
        config.trace.directory = Path(args.trace_dir)
    if getattr(args, "sandbox", None):
        config.security.sandbox_mode = SandboxMode(args.sandbox)
    if getattr(args, "approval_policy", None):
        config.security.approval_policy = ApprovalPolicy(args.approval_policy)
    if getattr(args, "danger_full_access", False):
        config.security.sandbox_mode = SandboxMode.DANGER_FULL_ACCESS
        config.security.approval_policy = ApprovalPolicy.NEVER
        config.security.full_access_confirmed = True


def _resolve_provider(config):
    """Create the configured model provider for one CLI run."""
    if config.provider.name == "fake":
        return default_demo_provider()
    return DeepSeekProvider(
        api_key=config.provider.api_key,
        base_url=config.provider.base_url,
        timeout_seconds=config.provider.timeout_seconds,
        max_attempts=config.provider.max_attempts,
    )


def _choose_model() -> str:
    """Prompt the user to choose one supported DeepSeek model."""
    print("请选择模型：")
    print("1. v4-flash 速度更快，适合日常分析")
    print("2. v4-pro   能力更强，适合复杂分析")
    choice = input("请输入 1 或 2，默认 1：").strip()
    return "deepseek-v4-pro" if choice == "2" else "deepseek-v4-flash"


def _setup(args: argparse.Namespace) -> int:
    """Interactively collect provider settings and save them to user config."""
    print("首次配置 Agent Harness")
    print("配置会保存到：")
    print(default_user_config_path())
    base_url = input("请输入 DeepSeek API URL，直接回车使用 https://api.deepseek.com：").strip() or "https://api.deepseek.com"
    api_key_env = input("请输入保存 API Key 的环境变量名，默认 DEEPSEEK_API_KEY：").strip() or "DEEPSEEK_API_KEY"
    model = _choose_model()
    path = write_user_config(ProviderConfig(name=args.provider, model=model, base_url=base_url, api_key_env=api_key_env))
    print(f"配置已保存：{path}")
    print(f"请在系统环境变量中设置 {api_key_env}，不要把 API Key 写入项目文件或 config.toml。")
    print("以后可以在任意目录运行：agent-harness code")
    return 0


def _ensure_configured(config) -> bool:
    """Return whether the configured provider has enough credentials to run."""
    if config.provider.name == "fake":
        return True
    return bool(config.provider.api_key or os.getenv(config.provider.api_key_env))


async def _run(args: argparse.Namespace) -> int:
    """Execute one non-interactive task from CLI arguments and print a summary."""
    config = load_config(Path(args.config) if args.config else None)
    _apply_common_run_args(config, args)
    if not _ensure_configured(config):
        print("还没有配置 API Key。请先运行：agent-harness setup")
        return 2

    provider = _resolve_provider(config)
    manager = RunManager(config=config, provider=provider)
    try:
        state = await manager.run(args.task, Path(args.workspace))
    except HarnessError as exc:
        print(f"错误：{exc}")
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        await provider.close()
    print(f"Task ID: {state.run_id}")
    print(f"Status: {state.status.value}")
    print(f"Trace: {config.trace.directory / state.run_id / 'events.jsonl'}")
    print(f"Result: {config.trace.directory / state.run_id / 'result.json'}")
    if state.status == RunStatus.COMPLETED:
        print("\nFinal output:\n" + (state.final_output or ""))
        return 0
    print("\nError:")
    print(state.error.message if state.error else "unknown")
    return 1


async def _code(args: argparse.Namespace) -> int:
    """Run against the current working directory or enter an interactive session."""
    task = args.task or " ".join(args.task_text).strip()
    if not task:
        return await _interactive(args)
    args.workspace = str(Path.cwd())
    args.task = task
    return await _run(args)


async def _interactive(args: argparse.Namespace) -> int:
    """Start a Codex-style multi-turn terminal session in the current directory."""
    config = load_config(Path(args.config) if getattr(args, "config", None) else None)
    _apply_common_run_args(config, args)
    if not _ensure_configured(config):
        print("还没有配置 API Key。请先运行：agent-harness setup")
        return 2
    provider = _resolve_provider(config)
    manager = RunManager(config=config, provider=provider, approval_handler=ConsoleApprovalHandler())
    session = ConversationSession(config=config, manager=manager, workspace=Path.cwd(), project_trusted=_resolve_workspace_trust(Path.cwd(), config))
    await session.start()
    print(f"Thread ID: {session.session_id}")
    print(f"Workspace: {Path.cwd()}")
    print("输入 /exit 退出，/new 开启新 Thread，/status 查看当前 Thread。")
    try:
        while True:
            try:
                task = input("\n> ").strip()
            except EOFError:
                break
            if not task:
                continue
            if task in {"/exit", "/quit"}:
                break
            if task == "/status":
                print(f"Thread: {session.session_id}")
                print(f"Turns: {session.state.turn_count if session.state else 0}")
                print(f"Rollout: {session.rollout_path}")
                continue
            if task.startswith("/permissions"):
                _permissions_command(config, task)
                continue
            if task == "/sandbox":
                _print_sandbox(config)
                continue
            if task == "/approvals":
                print("审批策略：" + config.security.approval_policy.value + "；临时授权只保存在当前运行进程内。")
                continue
            if await _phase4_command(task, session):
                continue
            if task == "/new":
                await session.close()
                session = ConversationSession(config=config, manager=manager, workspace=Path.cwd(), project_trusted=_resolve_workspace_trust(Path.cwd(), config))
                await session.start()
                print(f"New Thread ID: {session.session_id}")
                continue
            state = await session.run_turn(task)
            if state.final_output:
                print("\n" + state.final_output)
            elif state.error:
                print(f"\n错误：{state.error.message}")
    except KeyboardInterrupt:
        print("\n已退出。")
        return 130
    finally:
        await session.close()
        await provider.close()
    return 0


async def _resume(args: argparse.Namespace) -> int:
    """Resume an existing thread and continue the interactive CLI."""
    config = load_config(Path(args.config) if getattr(args, "config", None) else None)
    _apply_common_run_args(config, args)
    if not _ensure_configured(config):
        print("还没有配置 API Key。请先运行：agent-harness setup")
        return 2
    thread_id = args.thread_id
    if not thread_id:
        rows = await LocalThreadStore(config.trace.thread_directory).list_threads()
        thread_id = rows[0]["thread_id"] if rows else None
    if not thread_id:
        print("没有可恢复的 Thread。请先运行：agent-harness code")
        return 1
    provider = _resolve_provider(config)
    manager = RunManager(config=config, provider=provider, approval_handler=ConsoleApprovalHandler())
    session = ConversationSession(config=config, manager=manager, workspace=Path.cwd(), project_trusted=_resolve_workspace_trust(Path.cwd(), config))
    try:
        await session.resume(thread_id)
        print(f"Thread ID: {session.session_id}")
        print(f"Workspace: {session.state.workspace_root if session.state else Path.cwd()}")
        print("输入 /exit 退出，/new 开启新 Thread，/status 查看当前 Thread。")
        recovered = await session.continue_incomplete_turn()
        if recovered and recovered.final_output:
            print("\n已恢复原 Turn：\n" + recovered.final_output)
        while True:
            try:
                task = input("\n> ").strip()
            except EOFError:
                break
            if not task:
                continue
            if task in {"/exit", "/quit"}:
                break
            if task == "/status":
                print(f"Thread: {session.session_id}")
                print(f"Turns: {session.state.turn_count if session.state else 0}")
                print(f"Rollout: {session.rollout_path}")
                continue
            if task.startswith("/permissions"):
                _permissions_command(config, task)
                continue
            if task == "/sandbox":
                _print_sandbox(config)
                continue
            if task == "/approvals":
                print("审批策略：" + config.security.approval_policy.value + "；临时授权只保存在当前运行进程内。")
                continue
            if await _phase4_command(task, session):
                continue
            if task == "/new":
                await session.close()
                session = ConversationSession(config=config, manager=manager, workspace=Path.cwd(), project_trusted=_resolve_workspace_trust(Path.cwd(), config))
                await session.start()
                print(f"New Thread ID: {session.session_id}")
                continue
            state = await session.run_turn(task)
            if state.final_output:
                print("\n" + state.final_output)
            elif state.error:
                print(f"\n错误：{state.error.message}")
    finally:
        await session.close()
        await provider.close()
    return 0


def _tools(args: argparse.Namespace) -> int:
    """Print registered tool names and JSON schemas for a workspace."""
    registry = create_default_registry(Path(args.workspace).resolve())
    for tool in registry.list():
        print(f"{tool.name}: {tool.description}")
        print(json.dumps(tool.input_schema, ensure_ascii=False, indent=2))
    return 0


def _permissions_command(config, command: str) -> None:
    """Show or switch the current user-controlled security preset."""
    parts = command.split(maxsplit=1)
    if len(parts) == 1:
        print(f"Sandbox Mode: {config.security.sandbox_mode.value}")
        print(f"Approval Policy: {config.security.approval_policy.value}")
        print("可用：/permissions plan|auto|manual|full-access")
        return
    preset = parts[1].strip().lower()
    profiles = {
        "plan": (SandboxMode.READ_ONLY, ApprovalPolicy.ON_REQUEST),
        "auto": (SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.ON_REQUEST),
        "manual": (SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.UNTRUSTED),
    }
    if preset == "full-access":
        confirmation = input("Full Access 将关闭 OS 沙箱。请输入 FULL ACCESS 确认：").strip()
        if confirmation != "FULL ACCESS":
            print("未切换。")
            return
        config.security.sandbox_mode = SandboxMode.DANGER_FULL_ACCESS
        config.security.approval_policy = ApprovalPolicy.NEVER
        config.security.full_access_confirmed = True
        print("已切换到 danger-full-access。")
        return
    if preset not in profiles:
        print("未知权限预设。")
        return
    config.security.sandbox_mode, config.security.approval_policy = profiles[preset]
    config.security.full_access_confirmed = False
    print(f"已切换：{preset}")


def _print_sandbox(config) -> None:
    """Print effective sandbox settings without exposing environment secrets."""
    security = config.security
    print(f"Backend: {security.sandbox_backend}")
    print(f"Mode: {security.sandbox_mode.value}")
    print(f"Network: {'enabled' if security.network_enabled else 'off'}")
    print(f"Fail Closed: {security.sandbox_required}")
    print(f"WSL Distribution: {security.wsl_distribution or 'default'}")


def _resolve_workspace_trust(workspace: Path, config) -> bool:
    """Prompt once for repository-provided Guidance and Skills before model injection."""
    if config.security.trusted_project or (not config.guidance.require_workspace_trust and not config.skills.require_workspace_trust):
        return True
    has_project_config = any(
        path.exists()
        for path in (workspace / "AGENTS.md", workspace / "AGENTS.override.md", workspace / "CLAUDE.md", workspace / ".agents", workspace / ".mcp.json")
    )
    if not has_project_config:
        return False
    store = WorkspaceTrustStore(default_user_config_path().parent / "workspace-trust.json")
    state = store.get(workspace)
    if state in {WorkspaceTrustState.TRUSTED, WorkspaceTrustState.TRUSTED_ONCE}:
        return True
    if state == WorkspaceTrustState.UNTRUSTED:
        return False
    print("检测到项目提供的 AGENTS.md、Rules 或 Skills。是否信任这些 Agent 配置？")
    print("1. 仅本次信任  2. 始终信任此 Workspace  3. 不信任")
    choice = input("请选择 1/2/3，默认 3：").strip()
    selected = WorkspaceTrustState.TRUSTED_ONCE if choice == "1" else WorkspaceTrustState.TRUSTED if choice == "2" else WorkspaceTrustState.UNTRUSTED
    store.set(workspace, selected)
    return selected in {WorkspaceTrustState.TRUSTED, WorkspaceTrustState.TRUSTED_ONCE}


async def _phase4_command(command: str, session: ConversationSession) -> bool:
    """Handle Guidance, Skills, and Trust inspection commands in an idle CLI thread."""
    if command.startswith("/mcp"):
        runtime = session.manager.mcp_runtime
        if not runtime:
            print("当前 Thread 未启用 MCP Runtime。")
            return True
        if command.startswith("/mcp resources"):
            server = command.removeprefix("/mcp resources").strip() or None
            for resource in runtime.resources():
                if server is None or resource.server_name == server:
                    print(f"{resource.server_name}\t{resource.uri}\t{resource.name}\t{resource.mime_type or ''}")
            return True
        if command.startswith("/mcp resource "):
            parts = command.removeprefix("/mcp resource ").split(maxsplit=1)
            if len(parts) != 2 or parts[0] not in runtime.manager.active_servers:
                print("用法：/mcp resource <server> <uri>")
                return True
            payload = await runtime.manager.active_servers[parts[0]].read_resource(parts[1])
            queued = await session.queue_external_context("mcp_resource", parts[0], parts[1], payload)
            print("资源已加入下一轮上下文。" if queued else "相同资源内容已存在，未重复加入。")
            return True
        if command.startswith("/mcp prompts"):
            server = command.removeprefix("/mcp prompts").strip() or None
            for prompt in runtime.prompts():
                if server is None or prompt.server_name == server:
                    print(f"{prompt.server_name}/{prompt.name}\t{prompt.description}")
            return True
        if command.startswith("/mcp prompt "):
            parts = command.removeprefix("/mcp prompt ").split()
            target = parts[0].split("/", maxsplit=1) if parts else []
            if len(target) != 2 or target[0] not in runtime.manager.active_servers:
                print("用法：/mcp prompt <server>/<name> key=value ...")
                return True
            values = dict(item.split("=", maxsplit=1) for item in parts[1:] if "=" in item)
            payload = await runtime.manager.active_servers[target[0]].get_prompt(target[1], values or None)
            queued = await session.queue_external_context("mcp_prompt", target[0], target[1], payload)
            print("Prompt 已加入下一轮上下文。" if queued else "相同 Prompt 内容已存在，未重复加入。")
            return True
        if command != "/mcp":
            return False
        for row in runtime.status_rows():
            print(
                f"{row['name']}: {row['status']} transport={row['transport']} "
                f"tools={row['tool_count']} resources={row['resource_count']} prompts={row['prompt_count']}"
            )
        for blocked in runtime.resolved.blocked:
            print(f"{blocked.name}: blocked_untrusted scope={blocked.scope.value}")
        return True
    if command == "/trust":
        print("Workspace Trust：" + ("trusted" if session.project_trusted else "untrusted"))
        return True
    if command.startswith("/guidance"):
        snapshot = session.manager.guidance_snapshot
        if command == "/guidance reload":
            assert session.thread_id is not None
            session.manager.reload_guidance(session.thread_id, session.workspace, project_trusted=session.project_trusted)
            snapshot = session.manager.guidance_snapshot
            print("Guidance 已重新加载。")
        if snapshot is None:
            print("Guidance 未启用。")
            return True
        inspect_id = command.removeprefix("/guidance inspect ").strip() if command.startswith("/guidance inspect ") else None
        if inspect_id:
            document = next((item for item in (*snapshot.documents, *snapshot.path_rules) if item.document_id == inspect_id), None)
            print(document.content if document else "未找到 Guidance Document。")
            return True
        print(f"Snapshot: {snapshot.snapshot_id} bytes={snapshot.total_bytes} truncated={snapshot.truncated}")
        for document in (*snapshot.documents, *snapshot.path_rules):
            print(f"{document.document_id}  {document.source_kind.value}  trusted={document.trusted}  {document.path}")
        for diagnostic in snapshot.diagnostics:
            print(f"{diagnostic.level}: {diagnostic.code}: {diagnostic.message}")
        return True
    if command.startswith("/skills"):
        manager = session.manager.skill_manager
        catalog = session.manager.skill_catalog
        if command == "/skills reload":
            assert session.thread_id is not None
            session.manager.reload_skills(session.thread_id, session.workspace, project_trusted=session.project_trusted)
            manager = session.manager.skill_manager
            catalog = session.manager.skill_catalog
            print("Skill Catalog 已重新加载；已有 Activation Snapshot 保持不变。")
        if manager is None or catalog is None:
            print("Skills 未启用。")
            return True
        if command == "/skills active":
            for activation in manager.active:
                print(f"{activation.qualified_name}  {activation.activation_id}  turn={activation.activated_turn_id}")
            return True
        inspect_name = command.removeprefix("/skills inspect ").strip() if command.startswith("/skills inspect ") else None
        if inspect_name:
            try:
                record = manager.resolve(inspect_name, user_invocation=True)
                print(record.skill_path.read_text(encoding="utf-8"))
            except (ValueError, PermissionError, OSError) as exc:
                print(f"错误：{exc}")
            return True
        for record in manager.records:
            print(f"{record.qualified_name}  scope={record.scope.value} trusted={record.trusted} context={record.context_mode}\n  {record.description}")
        return True
    return False


def _sessions(args: argparse.Namespace) -> int:
    """List saved interactive threads; kept as a hidden compatibility alias."""
    args.thread_dir = args.session_dir
    return _threads(args)


def _threads(args: argparse.Namespace) -> int:
    """List saved interactive threads under the current workspace."""
    root = Path(args.thread_dir)
    if not root.exists():
        print(f"没有找到 thread 目录：{root}")
        return 0
    rows = []
    for thread_dir in root.iterdir():
        meta_path = thread_dir / "metadata.json"
        if not meta_path.exists():
            continue
        rows.append(json.loads(meta_path.read_text(encoding="utf-8")))
    if not rows:
        print(f"没有保存的 thread：{root}")
        return 0
    rows.sort(key=lambda data: data.get("updated_at") or "", reverse=True)
    for data in rows:
        print(f"{data.get('thread_id')}  turns={data.get('turn_count')}  status={data.get('status')}  updated={data.get('updated_at')}")
    return 0


def _inspect(args: argparse.Namespace) -> int:
    """Print a saved result or thread metadata/rollout preview."""
    if args.thread or args.session:
        base = Path(".harness/threads")
        thread_dir = base / args.id
        meta_path = thread_dir / "metadata.json"
        rollout_path = thread_dir / "rollout.jsonl"
        if not meta_path.exists():
            print(f"Thread not found: {thread_dir}")
            return 1
        print(meta_path.read_text(encoding="utf-8"))
        if rollout_path.exists():
            lines = [line for line in rollout_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            print(f"\nrollout_items={len(lines)}")
        return 0
    path = Path(args.trace_dir) / args.id / "result.json"
    if not path.exists():
        print(f"Result not found: {path}")
        return 1
    print(path.read_text(encoding="utf-8"))
    return 0


def _recover(args: argparse.Namespace) -> int:
    """Inspect the deterministic recovery plan for one thread."""
    config = load_config()
    store = CheckpointStore((Path.cwd() / config.persistence.runtime_db).resolve())
    checkpoint = store.latest(args.thread_id)
    if checkpoint is None:
        print(f"未找到 Checkpoint：{args.thread_id}")
        return 1
    plan = RecoveryCoordinator().plan(checkpoint)
    print(json.dumps({"thread_id": args.thread_id, "turn_id": checkpoint.turn_id, "checkpoint_id": checkpoint.checkpoint_id,
        "resume_point": checkpoint.resume_point.value, "status": checkpoint.turn_status.value,
        "disposition": plan.disposition.value, "reason": plan.reason}, ensure_ascii=False, indent=2))
    return 0


def _memory_command(args: argparse.Namespace) -> int:
    """Manage project-isolated long-term memories without starting a model provider."""
    config = load_config()
    root = Path.cwd().resolve()
    store = MemoryStore((root / config.memory.database).resolve())
    identity = project_identity(root)
    if args.memory_command == "add":
        record = store.create_explicit(args.content, project_identity=identity, thread_id=args.thread_id)
        print(record.memory_id)
        return 0
    if args.memory_command == "search":
        records = store.search(args.query, project_identity=identity, limit=config.memory.max_results)
    elif args.memory_command == "list":
        records = store.list(project_identity=identity)
    elif args.memory_command == "invalidate":
        return 0 if store.invalidate(args.memory_id, args.reason) else 1
    elif args.memory_command == "delete":
        return 0 if store.delete(args.memory_id) else 1
    else:
        return 2
    for record in records:
        print(f"{record.memory_id}\t{record.verification_status.value}\t{record.content}")
    return 0


def _migrate_sessions(args: argparse.Namespace) -> int:
    """Migrate legacy session transcripts into append-only thread rollout files."""
    session_root = Path(args.session_dir)
    thread_root = Path(args.thread_dir)
    if not session_root.exists():
        print(f"没有找到旧 session 目录：{session_root}")
        return 0
    migrated = 0
    skipped = 0
    for session_dir in session_root.iterdir():
        session_json = session_dir / "session.json"
        transcript = session_dir / "transcript.jsonl"
        if not session_json.exists():
            skipped += 1
            continue
        metadata = json.loads(session_json.read_text(encoding="utf-8"))
        thread_id = str(metadata.get("session_id") or session_dir.name)
        target = thread_root / thread_id
        if (target / "metadata.json").exists():
            skipped += 1
            continue
        try:
            _migrate_one_session(metadata, transcript, target)
            migrated += 1
        except Exception as exc:
            skipped += 1
            print(f"迁移失败 {session_dir.name}: {exc}")
    print(f"迁移完成：migrated={migrated} skipped={skipped}")
    return 0


def _migrate_one_session(metadata: dict, transcript: Path, target: Path) -> None:
    """Convert one legacy session directory into thread metadata and rollout history."""
    thread_id = str(metadata.get("session_id") or target.name)
    target.mkdir(parents=True, exist_ok=True)
    thread_metadata = {
        "thread_id": thread_id,
        "session_id": thread_id,
        "parent_thread_id": None,
        "forked_from_id": None,
        "workspace_root": metadata.get("workspace_root"),
        "name": None,
        "preview": None,
        "status": "CLOSED",
        "model_provider": None,
        "model": None,
        "created_at": metadata.get("updated_at"),
        "updated_at": metadata.get("updated_at"),
        "last_turn_id": None,
        "turn_count": metadata.get("turn_count", 0),
        "archived": False,
        "child_thread_ids": [],
    }
    (target / "metadata.json").write_text(json.dumps(thread_metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    items = [
        RolloutItem.create(
            "thread.created",
            session_id=thread_id,
            thread_id=thread_id,
            payload={"workspace_root": metadata.get("workspace_root"), "migrated_from": "legacy_session"},
        )
    ]
    if transcript.exists():
        for line in transcript.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            role_type = str(record.get("type") or "")
            turn_id = record.get("turn_id")
            if role_type == "user":
                items.append(RolloutItem.create("user_message", session_id=thread_id, thread_id=thread_id, turn_id=turn_id, payload={"text": record.get("content"), "input_kind": "initial"}))
            if role_type == "assistant":
                items.append(RolloutItem.create("agent_message", session_id=thread_id, thread_id=thread_id, turn_id=turn_id, payload={"text": record.get("content"), "status": record.get("status"), "error": record.get("error")}))
                items.append(RolloutItem.create("turn.completed", session_id=thread_id, thread_id=thread_id, turn_id=turn_id, payload={"final_output": record.get("content"), "error": record.get("error")}))
    with (target / "rollout.jsonl").open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(to_jsonable(item), ensure_ascii=False) + "\n")


def _mcp_config_path() -> Path:
    """Return the user-scoped MCP JSON file managed by CLI commands."""
    return default_user_config_path().parent / "mcp.json"


def _read_mcp_rows() -> dict[str, dict]:
    """Read user MCP definitions and recover from a missing configuration file."""
    path = _mcp_config_path()
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw.get("mcpServers", {}) if isinstance(raw, dict) else {}
    return {str(name): dict(value) for name, value in rows.items() if isinstance(value, dict)}


def _write_mcp_rows(rows: dict[str, dict]) -> None:
    """Atomically persist user MCP definitions without credential values."""
    atomic_write_json(_mcp_config_path(), {"mcpServers": rows})


async def _mcp_command(args: argparse.Namespace) -> int:
    """Manage user-scoped MCP definitions and secure OAuth credentials."""
    rows = _read_mcp_rows()
    if args.mcp_command == "list":
        for name, row in sorted(rows.items()):
            target = row.get("url") or row.get("command")
            print(f"{name}\t{row.get('transport', 'stdio')}\t{target}\t{'enabled' if row.get('enabled', True) else 'disabled'}")
        return 0
    if args.mcp_command == "get":
        found = rows.get(args.name)
        if found is None:
            print(f"未找到 MCP Server：{args.name}")
            return 1
        print(json.dumps(found, ensure_ascii=False, indent=2))
        return 0
    if args.mcp_command == "add":
        if args.transport == "stdio":
            row = {"transport": "stdio", "command": args.target, "args": args.server_args}
        else:
            row = {"transport": "streamable_http", "url": args.target}
            if args.bearer_token_env_var:
                row["bearer_token_env_var"] = args.bearer_token_env_var
            if args.oauth:
                row["auth_mode"] = "oauth"
        rows[args.name] = row
        _write_mcp_rows(rows)
        print(f"已保存 MCP Server：{args.name}")
        return 0
    if args.mcp_command == "remove":
        if rows.pop(args.name, None) is None:
            print(f"未找到 MCP Server：{args.name}")
            return 1
        _write_mcp_rows(rows)
        print(f"已移除 MCP Server：{args.name}")
        return 0
    if args.mcp_command == "logout":
        found = rows.get(args.name)
        if found is None:
            print(f"未找到 MCP Server：{args.name}")
            return 1
        config = parse_server_config(args.name, found, MCPConfigScope.USER, Path.cwd())
        await KeyringTokenStorage(credential_identity(config)).clear()
        print(f"已清除 MCP OAuth 凭据：{args.name}")
        return 0
    if args.mcp_command == "login":
        found = rows.get(args.name)
        if found is None:
            print(f"未找到 MCP Server：{args.name}")
            return 1
        config = parse_server_config(args.name, found, MCPConfigScope.USER, Path.cwd())
        if config.auth_mode != "oauth":
            print("该 MCP Server 未配置 auth_mode=oauth。")
            return 1
        connection = MCPServerConnection(config, (Path.cwd(),))
        await connection.connect()
        await connection.close()
        print(f"MCP OAuth 授权成功：{args.name}")
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    """Dispatch the CLI command and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args.command = "code"
        args.task_text = []
        args.task = None
        args.provider = None
        args.model = None
        args.config = None
        args.max_iterations = None
        args.trace_dir = None
        args.sandbox = None
        args.approval_policy = None
        args.danger_full_access = False
        return asyncio.run(_interactive(args))
    if args.command == "setup":
        return _setup(args)
    if args.command == "code":
        return asyncio.run(_code(args))
    if args.command == "resume":
        return asyncio.run(_resume(args))
    if args.command in {"exec", "run"}:
        return asyncio.run(_run(args))
    if args.command == "tools":
        return _tools(args)
    if args.command == "threads":
        return _threads(args)
    if args.command == "sessions":
        return _sessions(args)
    if args.command == "inspect":
        return _inspect(args)
    if args.command == "recover":
        return _recover(args)
    if args.command == "memory":
        return _memory_command(args)
    if args.command == "migrate-sessions":
        return _migrate_sessions(args)
    if args.command == "mcp":
        return asyncio.run(_mcp_command(args))
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
