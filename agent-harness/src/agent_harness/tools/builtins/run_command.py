from __future__ import annotations

from pathlib import Path

from agent_harness.domain.errors import ToolInputValidationError
from agent_harness.domain.tools import ToolDefinition, ToolEffectClass, ToolRecoveryPolicy
from agent_harness.sandbox.base import CommandExecution, SandboxBackend
from agent_harness.security.models import Capability, RiskLevel, SandboxPolicy, SideEffectType
from agent_harness.security.path_policy import FileSystemPolicy


SHELL_PROGRAMS = {"cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "sh", "bash", "zsh"}
COMPOUND_TOKENS = {"&&", "||", ";", "|", ">", ">>", "<"}


def create_run_command_tool(workspace_root: Path, backend: SandboxBackend, sandbox_policy: SandboxPolicy, timeout_seconds: int = 120) -> ToolDefinition:
    """Create a structured, sandbox-backed command tool without shell parsing."""
    path_policy = FileSystemPolicy(workspace_root)

    async def execute(args: dict) -> dict:
        """Validate structured argv and execute it through the selected sandbox backend."""
        program = str(args["program"])
        argv = tuple(str(value) for value in args.get("args", []))
        _reject_shell_or_compound(program, argv)
        cwd = path_policy.resolve(str(args.get("cwd", "."))).resolved
        requested_timeout = float(args.get("timeout_seconds", timeout_seconds))
        env = {str(key): str(value) for key, value in args.get("env", {}).items()}
        forbidden = sorted(set(env) - set(sandbox_policy.environment_allow))
        if forbidden:
            raise ToolInputValidationError("Command env contains non-allowlisted names", details={"names": forbidden})
        result = await backend.execute(CommandExecution(program, argv, cwd, env, requested_timeout), sandbox_policy)
        return {"exit_code": result.exit_code, "stdout": result.stdout, "stderr": result.stderr, "timed_out": result.timed_out, "truncated": result.truncated, "sandbox_backend": result.backend}

    return ToolDefinition(
        name="run_command",
        description="在 OS 沙箱内使用结构化 argv 执行命令，不支持 shell、管道或重定向。",
        input_schema={
            "type": "object",
            "properties": {
                "program": {"type": "string", "minLength": 1},
                "args": {"type": "array", "items": {"type": "string"}},
                "cwd": {"type": "string"},
                "timeout_seconds": {"type": "number", "minimum": 0.1, "maximum": 900},
                "env": {"type": "object", "additionalProperties": {"type": "string"}},
            },
            "required": ["program"],
            "additionalProperties": False,
        },
        executor=execute,
        timeout_seconds=timeout_seconds + 5,
        risk_level=RiskLevel.MEDIUM,
        side_effect=SideEffectType.PROCESS,
        required_capabilities=frozenset({Capability.COMMAND_EXECUTE}),
        requires_sandbox=True,
        effect_class=ToolEffectClass.NON_IDEMPOTENT_WRITE,
        recovery_policy=ToolRecoveryPolicy.NEVER_RETRY,
    )


def _reject_shell_or_compound(program: str, args: tuple[str, ...]) -> None:
    """Reject shell interpreters and standalone compound-command control tokens."""
    if Path(program).name.lower() in SHELL_PROGRAMS:
        raise ToolInputValidationError("Shell interpreters are not supported by run_command")
    if any(value in COMPOUND_TOKENS for value in args):
        raise ToolInputValidationError("Compound command tokens are not supported")
