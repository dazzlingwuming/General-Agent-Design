from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from agent_harness.domain.messages import ToolCall
from agent_harness.domain.tools import ToolDefinition
from agent_harness.sandbox.base import CommandResult
from agent_harness.sandbox.fake import FakeSandboxBackend
from agent_harness.sandbox.base import CommandExecution
from agent_harness.sandbox.bubblewrap import WslBubblewrapSandboxBackend, windows_path_to_wsl
from agent_harness.security.approval import ApprovalDecision, TurnCancellationRequested
from agent_harness.security.approval_grants import ApprovalGrantStore
from agent_harness.security.hooks import HookDecision, HookManager
from agent_harness.security.models import (
    ApprovalPolicy,
    Capability,
    PermissionDecision,
    RiskLevel,
    RuleSource,
    SandboxMode,
    SandboxPolicy,
    ToolExecutionPrincipal,
)
from agent_harness.security.permission_engine import PermissionEngine
from agent_harness.security.rules import PermissionRule
from agent_harness.tools.builtins.run_command import create_run_command_tool
from agent_harness.tools.registry import ToolRegistry
from agent_harness.tools.runtime import ToolRuntime


async def noop(args: dict) -> str:
    """Return a stable value for permission-only tests."""
    return "ok"


def make_principal(*tools: str, capabilities: frozenset[Capability], policy: ApprovalPolicy = ApprovalPolicy.ON_REQUEST) -> ToolExecutionPrincipal:
    """Create one test principal with explicit capabilities and policy."""
    return ToolExecutionPrincipal("thread", "thread", "turn", "agent", allowed_tools=frozenset(tools), capabilities=capabilities, approval_policy=policy)


def make_policy(root: Path, mode: SandboxMode = SandboxMode.WORKSPACE_WRITE) -> SandboxPolicy:
    """Create a workspace-only, network-disabled sandbox policy."""
    return SandboxPolicy(mode, root, (root,), (root,) if mode == SandboxMode.WORKSPACE_WRITE else ())


def test_rule_precedence_and_untrusted_project_allow(tmp_path: Path):
    """Verify DENY beats ASK/ALLOW and untrusted project ALLOW cannot expand access."""
    tool = ToolDefinition("custom", "desc", {"type": "object", "properties": {}}, noop, required_capabilities=frozenset())
    principal = make_principal("custom", capabilities=frozenset())
    rules = [
        PermissionRule("allow", PermissionDecision.ALLOW, tool="custom"),
        PermissionRule("ask", PermissionDecision.ASK, tool="custom"),
        PermissionRule("deny", PermissionDecision.DENY, tool="custom"),
    ]
    assert PermissionEngine(rules).evaluate(principal, tool, {}, make_policy(tmp_path)).decision == PermissionDecision.DENY
    untrusted = PermissionRule("project-allow", PermissionDecision.ALLOW, RuleSource.TRUSTED_PROJECT, tool="custom", trusted=False)
    assert PermissionEngine([untrusted]).evaluate(principal, tool, {}, make_policy(tmp_path)).reason == "Security profile default"


def test_read_only_denies_write(tmp_path: Path):
    """Verify the read-only profile denies tools with write risk."""
    tool = ToolDefinition("write_file", "desc", {"type": "object", "properties": {"path": {"type": "string"}}}, noop, risk_level=RiskLevel.MEDIUM, required_capabilities=frozenset({Capability.FILE_WRITE}))
    principal = ToolExecutionPrincipal("thread", "thread", "turn", "agent", allowed_tools=frozenset({"write_file"}), capabilities=frozenset({Capability.FILE_WRITE}), sandbox_mode=SandboxMode.READ_ONLY)
    assert PermissionEngine().evaluate(principal, tool, {"path": "a.txt"}, make_policy(tmp_path, SandboxMode.READ_ONLY)).decision == PermissionDecision.DENY


@pytest.mark.parametrize("path", ["../outside.txt", ".env", ".harness/threads/thread/rollout.jsonl"])
def test_permission_path_policy_denies_escape_and_secrets(tmp_path: Path, path: str):
    """Verify built-in path policy rejects escapes, secrets, and canonical thread storage."""
    tool = ToolDefinition("read_file", "desc", {"type": "object", "properties": {"path": {"type": "string"}}}, noop)
    principal = make_principal("read_file", capabilities=frozenset({Capability.FILE_READ}))
    with pytest.raises(Exception):
        PermissionEngine().evaluate(principal, tool, {"path": path}, make_policy(tmp_path))


class AllowOnceHandler:
    """Approval test handler that records every request and allows it once."""

    def __init__(self) -> None:
        """Create an empty request log."""
        self.requests = []

    async def request(self, request):
        """Record and approve one request."""
        self.requests.append(request)
        return ApprovalDecision.ALLOW_ONCE


class FixedApprovalHandler:
    """Return one configured decision while retaining sanitized requests."""

    def __init__(self, decision: ApprovalDecision) -> None:
        """Store the fixed decision and initialize the request log."""
        self.decision = decision
        self.requests = []

    async def request(self, request):
        """Record and return the configured approval decision."""
        self.requests.append(request)
        return self.decision


async def test_approval_and_never_policy(tmp_path: Path):
    """Verify ASK prompts once while never policy rejects the same request."""
    registry = ToolRegistry()
    registry.register(ToolDefinition("danger", "desc", {"type": "object", "properties": {}}, noop, risk_level=RiskLevel.HIGH, required_capabilities=frozenset()))
    handler = AllowOnceHandler()
    runtime = ToolRuntime(registry, approval_handler=handler, workspace_root=tmp_path)
    allowed = await runtime.execute(ToolCall("call1", "danger", {}), make_principal("danger", capabilities=frozenset()))
    denied = await runtime.execute(ToolCall("call2", "danger", {}), make_principal("danger", capabilities=frozenset(), policy=ApprovalPolicy.NEVER))
    assert allowed.status == "success"
    assert len(handler.requests) == 1
    assert denied.error_code == "TOOL_AUTHORIZATION"


async def test_thread_approval_is_shared_but_argument_scoped(tmp_path: Path):
    """Reuse a thread grant across turns only for the same principal arguments."""
    async def read_target(args: dict) -> str:
        """Return the approved target path for the grant test."""
        return str(args["path"])

    registry = ToolRegistry()
    registry.register(ToolDefinition("scoped", "desc", {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}, read_target, required_capabilities=frozenset()))
    grants = ApprovalGrantStore()
    allow = FixedApprovalHandler(ApprovalDecision.ALLOW_THREAD)
    engine = PermissionEngine([PermissionRule("ask", PermissionDecision.ASK, tool="scoped")])
    first = ToolRuntime(registry, permission_engine=engine, approval_handler=allow, approval_grants=grants, workspace_root=tmp_path)
    principal_one = make_principal("scoped", capabilities=frozenset())
    assert (await first.execute(ToolCall("one", "scoped", {"path": "a.txt"}), principal_one)).status == "success"
    deny = FixedApprovalHandler(ApprovalDecision.DENY_ONCE)
    second = ToolRuntime(registry, permission_engine=engine, approval_handler=deny, approval_grants=grants, workspace_root=tmp_path)
    principal_two = ToolExecutionPrincipal("thread", "thread", "turn-2", "agent", allowed_tools=frozenset({"scoped"}), capabilities=frozenset())
    assert (await second.execute(ToolCall("two", "scoped", {"path": "a.txt"}), principal_two)).status == "success"
    denied = await second.execute(ToolCall("three", "scoped", {"path": "b.txt"}), principal_two)
    assert denied.error_code == "TOOL_AUTHORIZATION"
    assert len(allow.requests) == 1
    assert len(deny.requests) == 1


async def test_cancel_turn_is_control_flow_and_approval_preview_redacts_secrets(tmp_path: Path):
    """Propagate cancel-turn while preventing secret-shaped arguments from display."""
    registry = ToolRegistry()
    registry.register(ToolDefinition("danger", "desc", {"type": "object", "properties": {"api_key": {"type": "string"}, "args": {"type": "array"}}}, noop, required_capabilities=frozenset()))
    handler = FixedApprovalHandler(ApprovalDecision.CANCEL_TURN)
    runtime = ToolRuntime(registry, permission_engine=PermissionEngine([PermissionRule("ask", PermissionDecision.ASK, tool="danger")]), approval_handler=handler, workspace_root=tmp_path)
    with pytest.raises(TurnCancellationRequested):
        await runtime.execute(ToolCall("cancel", "danger", {"api_key": "secret-value", "args": ["--token", "secret-token"]}), make_principal("danger", capabilities=frozenset()))
    request = handler.requests[0]
    assert request.argument_preview["api_key"] == "[REDACTED]"
    assert request.argument_preview["args"] == ["--token", "[REDACTED]"]


async def test_hook_cannot_override_rule_deny(tmp_path: Path):
    """Verify a PASS hook cannot override an explicit permission DENY."""
    async def pass_hook(payload: dict) -> HookDecision:
        """Return PASS for the hook precedence test."""
        return HookDecision.PASS

    registry = ToolRegistry()
    registry.register(ToolDefinition("blocked", "desc", {"type": "object", "properties": {}}, noop, required_capabilities=frozenset()))
    runtime = ToolRuntime(registry, permission_engine=PermissionEngine([PermissionRule("deny", PermissionDecision.DENY, tool="blocked")]), hook_manager=HookManager({"PreToolUse": [pass_hook]}), workspace_root=tmp_path)
    result = await runtime.execute(ToolCall("call", "blocked", {}), make_principal("blocked", capabilities=frozenset()))
    assert result.error_code == "TOOL_AUTHORIZATION"


async def test_structured_command_uses_fake_sandbox_and_rejects_shell(tmp_path: Path):
    """Verify command argv reaches the backend and shell interpreters are rejected."""
    backend = FakeSandboxBackend(CommandResult(0, "通过", "", backend="fake"))
    policy = make_policy(tmp_path)
    registry = ToolRegistry()
    registry.register(create_run_command_tool(tmp_path, backend, policy))
    runtime = ToolRuntime(registry, workspace_root=tmp_path)
    principal = make_principal("run_command", capabilities=frozenset({Capability.COMMAND_EXECUTE}))
    good = await runtime.execute(ToolCall("call1", "run_command", {"program": "pytest", "args": ["-q"], "cwd": "."}), principal)
    bad = await runtime.execute(ToolCall("call2", "run_command", {"program": "powershell.exe", "args": ["-Command", "whoami"], "cwd": "."}), principal)
    assert good.status == "success"
    assert backend.executions[0].args == ("-q",)
    assert bad.error_code == "TOOL_INPUT_VALIDATION"


def test_windows_path_to_wsl_is_host_independent():
    """Verify Windows path translation has the same result on every host OS."""
    assert windows_path_to_wsl(PureWindowsPath("C:/repo/work tree")) == PurePosixPath("/mnt/c/repo/work tree")


def test_wsl_argv_keeps_posix_paths_and_linux_path(tmp_path: Path):
    """Verify WSL argv compilation preserves already translated POSIX paths."""
    backend = WslBubblewrapSandboxBackend()
    policy = make_policy(tmp_path)
    execution = CommandExecution("pytest", ("-q",), tmp_path)
    workspace = "/mnt/c/repo"
    argv = backend._build_wsl_argv(execution, policy, workspace, workspace)
    assert workspace in argv
    assert "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" in argv
    assert str(tmp_path) not in argv
