from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol
from typing import Any

from agent_harness.security.models import Capability, RiskLevel, SideEffectType, ToolExecutionPrincipal
from agent_harness.utils.ids import new_id
from agent_harness.utils.time import utc_now


class ApprovalDecision(str, Enum):
    """User decisions supported by the first interactive approval manager."""

    ALLOW_ONCE = "ALLOW_ONCE"
    ALLOW_TURN = "ALLOW_TURN"
    ALLOW_THREAD = "ALLOW_THREAD"
    DENY_ONCE = "DENY_ONCE"
    CANCEL_TURN = "CANCEL_TURN"


class TurnCancellationRequested(asyncio.CancelledError):
    """Control-flow signal raised when an approval explicitly cancels the turn."""

    def __init__(self, approval_id: str, tool_name: str) -> None:
        """Store sanitized approval identity without retaining tool arguments."""
        super().__init__(f"User cancelled turn during approval for {tool_name}")
        self.approval_id = approval_id
        self.tool_name = tool_name


@dataclass(slots=True)
class ApprovalRequest:
    """Narrow, auditable request to execute one pending tool call."""

    principal: ToolExecutionPrincipal
    tool_call_id: str
    tool_name: str
    reason: str
    risk_level: RiskLevel
    requested_capabilities: frozenset[Capability]
    command_preview: tuple[str, ...] = ()
    path_preview: tuple[str, ...] = ()
    argument_preview: dict[str, Any] = field(default_factory=dict)
    config_scope: str | None = None
    server_name: str | None = None
    identity_summary: str | None = None
    remote_tool_name: str | None = None
    canonical_tool_name: str | None = None
    effective_approval_mode: str | None = None
    approval_source: str | None = None
    side_effect: SideEffectType = SideEffectType.NONE
    annotations: dict[str, Any] = field(default_factory=dict)
    annotations_trusted: bool = False
    approval_id: str = field(default_factory=lambda: new_id("approval"))
    created_at: object = field(default_factory=utc_now)


class ApprovalHandler(Protocol):
    """Interface used by ToolRuntime to resolve an ASK decision."""

    async def request(self, request: ApprovalRequest) -> ApprovalDecision:
        """Resolve one approval request."""
        ...


@dataclass(slots=True)
class DenyApprovalHandler:
    """Fail-closed handler for non-interactive runs."""

    async def request(self, request: ApprovalRequest) -> ApprovalDecision:
        """Deny every request without prompting."""
        return ApprovalDecision.DENY_ONCE


@dataclass(slots=True)
class ConsoleApprovalHandler:
    """Prompt the root terminal for a narrow approval decision."""

    async def request(self, request: ApprovalRequest) -> ApprovalDecision:
        """Display the request in Chinese and read a decision without blocking the event loop."""
        return await asyncio.to_thread(self._prompt, request)

    def _prompt(self, request: ApprovalRequest) -> ApprovalDecision:
        """Synchronously render and collect one terminal approval."""
        preview = " ".join(request.command_preview) or ", ".join(request.path_preview) or request.tool_name
        print(f"\n权限审批：{request.principal.agent_id} 请求 {request.tool_name}")
        print(f"操作：{preview}\n原因：{request.reason}\n风险：{request.risk_level.value}")
        if request.server_name:
            print(f"MCP：scope={request.config_scope} server={request.server_name} identity={request.identity_summary}")
            print(f"工具：remote={request.remote_tool_name} canonical={request.canonical_tool_name}")
            print(f"审批：mode={request.effective_approval_mode} source={request.approval_source} side_effect={request.side_effect.value}")
            print(f"Annotations（trusted={request.annotations_trusted}）：{request.annotations}")
        print(f"Principal：agent={request.principal.agent_id} thread={request.principal.thread_id} turn={request.principal.turn_id}")
        if request.argument_preview:
            print(f"参数：{request.argument_preview}")
        print("[1] 允许一次  [2] 当前 Turn 允许  [3] 当前 Thread 允许  [4] 拒绝  [5] 取消 Turn")
        choice = input("请选择，默认 4：").strip()
        return {"1": ApprovalDecision.ALLOW_ONCE, "2": ApprovalDecision.ALLOW_TURN, "3": ApprovalDecision.ALLOW_THREAD, "5": ApprovalDecision.CANCEL_TURN}.get(choice, ApprovalDecision.DENY_ONCE)


def redact_approval_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Redact secret-shaped values before approval display or audit serialization."""
    secret_tokens = ("authorization", "token", "secret", "password", "passwd", "api_key", "apikey", "bearer")

    def redact(value: Any, key: str = "") -> Any:
        """Recursively copy values while replacing sensitive fields and CLI flag values."""
        if any(token in key.casefold() for token in secret_tokens):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {str(child_key): redact(child_value, str(child_key)) for child_key, child_value in value.items()}
        if isinstance(value, list):
            result: list[Any] = []
            hide_next = False
            for item in value:
                text = str(item)
                if hide_next:
                    result.append("[REDACTED]")
                    hide_next = False
                elif text.startswith("--") and any(token in text.casefold() for token in secret_tokens):
                    result.append(text.split("=", 1)[0] + ("=[REDACTED]" if "=" in text else ""))
                    hide_next = "=" not in text
                else:
                    result.append(redact(item))
            return result
        return value

    return redact(arguments)
