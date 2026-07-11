from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
from pathlib import Path

from agent_harness.config import MODEL_ALIASES, ProviderConfig, default_user_config_path, load_config, normalize_model_name, write_user_config
from agent_harness.domain.errors import HarnessError
from agent_harness.domain.run import RunStatus
from agent_harness.providers.deepseek import DeepSeekProvider
from agent_harness.providers.fake import default_demo_provider
from agent_harness.runtime.run_manager import RunManager
from agent_harness.runtime.session import ConversationSession
from agent_harness.tools.builtins.factory import create_default_registry


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

    run = sub.add_parser("run", help=argparse.SUPPRESS)
    run.add_argument("--workspace", required=True)
    run.add_argument("--task", required=True)
    run.add_argument("--provider", choices=["deepseek", "fake"])
    run.add_argument("--model", choices=sorted(MODEL_ALIASES))
    run.add_argument("--config")
    run.add_argument("--max-iterations", type=int)
    run.add_argument("--trace-dir")

    tools = sub.add_parser("tools")
    tools.add_argument("--workspace", default=".")

    sessions = sub.add_parser("sessions")
    sessions.add_argument("--session-dir", default=".harness/sessions")

    inspect = sub.add_parser("inspect")
    inspect.add_argument("id")
    inspect.add_argument("--trace-dir", default=".harness/runs")
    inspect.add_argument("--session", action="store_true")
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
    api_key = getpass.getpass("请输入 API Key（输入时不会显示）：").strip()
    if not api_key:
        print("API Key 不能为空。")
        return 2
    model = _choose_model()
    path = write_user_config(ProviderConfig(name=args.provider, model=model, base_url=base_url, api_key=api_key))
    print(f"配置已保存：{path}")
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
    manager = RunManager(config=config, provider=provider)
    session = ConversationSession(config=config, manager=manager, workspace=Path.cwd())
    session.start()
    print(f"Session ID: {session.session_id}")
    print(f"Workspace: {Path.cwd()}")
    print("输入 /exit 退出，/new 开启新会话，/status 查看当前会话。")
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
                print(f"Session: {session.session_id}")
                print(f"Turns: {session.state.turn_count if session.state else 0}")
                print(f"Trace: {session.session_dir / 'events.jsonl'}")
                continue
            if task == "/new":
                session.close()
                session = ConversationSession(config=config, manager=manager, workspace=Path.cwd())
                session.start()
                print(f"New Session ID: {session.session_id}")
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
        session.close()
        await provider.close()
    return 0


def _tools(args: argparse.Namespace) -> int:
    """Print registered tool names and JSON schemas for a workspace."""
    registry = create_default_registry(Path(args.workspace).resolve())
    for tool in registry.list():
        print(f"{tool.name}: {tool.description}")
        print(json.dumps(tool.input_schema, ensure_ascii=False, indent=2))
    return 0


def _sessions(args: argparse.Namespace) -> int:
    """List saved interactive sessions under the current workspace."""
    root = Path(args.session_dir)
    if not root.exists():
        print(f"没有找到 session 目录：{root}")
        return 0
    sessions = sorted(root.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True)
    if not sessions:
        print(f"没有保存的 session：{root}")
        return 0
    for session_dir in sessions:
        meta_path = session_dir / "session.json"
        if meta_path.exists():
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            print(f"{data.get('session_id')}  turns={data.get('turn_count')}  status={data.get('status')}  updated={data.get('updated_at')}")
        else:
            print(session_dir.name)
    return 0


def _inspect(args: argparse.Namespace) -> int:
    """Print a saved result.json for an existing task or session id."""
    base = Path(".harness/sessions") if args.session else Path(args.trace_dir)
    path = base / args.id / "result.json"
    if not path.exists():
        print(f"Result not found: {path}")
        return 1
    print(path.read_text(encoding="utf-8"))
    return 0


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
        return asyncio.run(_interactive(args))
    if args.command == "setup":
        return _setup(args)
    if args.command == "code":
        return asyncio.run(_code(args))
    if args.command in {"exec", "run"}:
        return asyncio.run(_run(args))
    if args.command == "tools":
        return _tools(args)
    if args.command == "sessions":
        return _sessions(args)
    if args.command == "inspect":
        return _inspect(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
