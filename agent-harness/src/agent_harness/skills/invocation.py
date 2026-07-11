from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from agent_harness.skills.activation import SkillManager
from agent_harness.skills.execution import SkillExecutionRegistry, SkillExecutionStatus
from agent_harness.skills.models import SkillActivationSnapshot


class SkillInvocationSource(StrEnum):
    """Authorized origins for a Skill invocation request."""

    USER_EXPLICIT = "user_explicit"
    MODEL_TOOL = "model_tool"
    SUBAGENT_PRELOAD = "subagent_preload"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class SkillInvocationRequest:
    """Source-neutral request consumed by the unified invocation pipeline."""

    name: str
    arguments: str
    source: SkillInvocationSource
    thread_id: str
    turn_id: str
    parent_agent_id: str = "coding_assistant"
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class SkillInvocationResult:
    """Structured result shared by user and model invocation adapters."""

    activation: SkillActivationSnapshot
    execution_id: str
    activation_created: bool
    delegated_result: dict[str, Any] | None = None


@dataclass(slots=True)
class SkillInvocationService:
    """Apply invocation gates, activation, execution, delegation, and audit once."""

    manager: SkillManager
    executions: SkillExecutionRegistry
    audit: Callable[[str, dict[str, Any]], None]
    fork_handler: Callable[[SkillActivationSnapshot], Awaitable[dict[str, Any]]] | None = None

    async def invoke(self, request: SkillInvocationRequest) -> SkillInvocationResult:
        """Execute one user or model request through the same lifecycle pipeline."""
        self.audit("skill.invocation_requested", {"skill": request.name, "invocation_source": request.source.value, "root_turn_id": request.turn_id})
        user_invocation = request.source == SkillInvocationSource.USER_EXPLICIT
        try:
            activation, created = self.manager.activate(request.name, request.arguments, request.turn_id, user_invocation=user_invocation)
        except Exception as exc:
            self.audit("skill.invocation_rejected", {"skill": request.name, "invocation_source": request.source.value, "error": str(exc)})
            raise
        execution = self.executions.start(activation, request.source.value, request.turn_id, request.parent_agent_id)
        self.audit("skill.activation_created" if created else "skill.activation_reused", {"skill_id": activation.skill_id, "activation_id": activation.activation_id})
        self.audit("skill.execution_started", {"execution_id": execution.execution_id, "activation_id": activation.activation_id, "context_mode": activation.context_mode, "effective_tools": list(execution.effective_tools)})
        delegated: dict[str, Any] | None = None
        if activation.context_mode == "fork":
            if self.fork_handler is None:
                execution.status = SkillExecutionStatus.FAILED
                execution.error = "Fork Skill runtime unavailable"
                raise RuntimeError("当前 Runtime 不支持 Fork Skill")
            try:
                delegated = await self.fork_handler(activation)
                execution.child_agent_id = str(delegated.get("agent_id") or "") or None
                execution.result = delegated
                execution.status = SkillExecutionStatus.COMPLETED
            except Exception as exc:
                execution.status = SkillExecutionStatus.FAILED
                execution.error = str(exc)
                self.audit("skill.execution_failed", {"execution_id": execution.execution_id, "error": str(exc)})
                raise
            self.audit("skill.execution_completed", {"execution_id": execution.execution_id, "activation_id": activation.activation_id, "child_agent_id": execution.child_agent_id})
        return SkillInvocationResult(activation, execution.execution_id, created, delegated)
