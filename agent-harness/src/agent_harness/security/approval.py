from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from agent_harness.security.models import Capability, RiskLevel, ToolExecutionPrincipal
from agent_harness.utils.ids import new_id
from agent_harness.utils.time import utc_now


class ApprovalDecision(str, Enum):
    """User decisions supported by the first interactive approval manager."""

    ALLOW_ONCE = "ALLOW_ONCE"
    ALLOW_TURN = "ALLOW_TURN"
    ALLOW_THREAD = "ALLOW_THREAD"
    DENY_ONCE = "DENY_ONCE"
    CANCEL_TURN = "CANCEL_TURN"


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
        print("[1] 允许一次  [2] 当前 Turn 允许  [3] 当前 Thread 允许  [4] 拒绝  [5] 取消 Turn")
        choice = input("请选择，默认 4：").strip()
        return {"1": ApprovalDecision.ALLOW_ONCE, "2": ApprovalDecision.ALLOW_TURN, "3": ApprovalDecision.ALLOW_THREAD, "5": ApprovalDecision.CANCEL_TURN}.get(choice, ApprovalDecision.DENY_ONCE)

