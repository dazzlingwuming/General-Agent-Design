from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from agent_harness.skills.models import SkillActivationSnapshot
from agent_harness.utils.ids import new_id


class SkillExecutionStatus(StrEnum):
    """Lifecycle states for one bounded Skill execution."""

    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class SkillExecution:
    """Turn-local execution state that alone can restrict effective tools."""

    execution_id: str
    activation_id: str
    invocation_source: str
    root_turn_id: str
    agent_id: str
    context_mode: str
    effective_tools: tuple[str, ...]
    child_agent_id: str | None = None
    status: SkillExecutionStatus = SkillExecutionStatus.ACTIVE
    result: dict | None = None
    error: str | None = None


@dataclass(slots=True)
class SkillExecutionRegistry:
    """Own active and completed executions and calculate turn-local tool scope."""

    executions: list[SkillExecution] = field(default_factory=list)

    def start(self, activation: SkillActivationSnapshot, source: str, turn_id: str, agent_id: str = "coding_assistant") -> SkillExecution:
        """Create one active execution from a durable activation."""
        execution = SkillExecution(
            execution_id=new_id("skill_execution"),
            activation_id=activation.activation_id,
            invocation_source=source,
            root_turn_id=turn_id,
            agent_id=agent_id,
            context_mode=activation.context_mode,
            effective_tools=activation.allowed_tools,
        )
        self.executions.append(execution)
        return execution

    def effective_tools_for(self, turn_id: str, agent_id: str, base_tools: list[str]) -> list[str]:
        """Intersect base tools with active inline executions for this turn and agent."""
        allowed = set(base_tools)
        for execution in self.executions:
            if execution.status == SkillExecutionStatus.ACTIVE and execution.context_mode == "inline" and execution.root_turn_id == turn_id and execution.agent_id == agent_id and execution.effective_tools:
                allowed.intersection_update(execution.effective_tools)
        return [name for name in base_tools if name in allowed]

    def finish_turn(self, turn_id: str) -> None:
        """Complete every remaining inline execution at the root turn boundary."""
        for execution in self.executions:
            if execution.root_turn_id == turn_id and execution.context_mode == "inline" and execution.status == SkillExecutionStatus.ACTIVE:
                execution.status = SkillExecutionStatus.COMPLETED
