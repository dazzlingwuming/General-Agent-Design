from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from agent_harness.tracing.events import TraceEvent
from agent_harness.tracing.usage import UsageReducer, UsageSnapshot


class RuntimePhase(StrEnum):
    """Stable user-visible runtime phases derived only from typed events."""

    READY = "ready"
    PREPARING = "preparing"
    BUILDING_CONTEXT = "building_context"
    CALLING_MODEL = "calling_model"
    PROCESSING_RESPONSE = "processing_response"
    WAITING_APPROVAL = "waiting_approval"
    RUNNING_TOOL = "running_tool"
    WAITING_SUBAGENT = "waiting_subagent"
    COMPACTING = "compacting"
    RECOVERING = "recovering"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


PHASE_BY_EVENT = {
    "turn.started": RuntimePhase.PREPARING, "turn.created": RuntimePhase.PREPARING,
    "context.build.started": RuntimePhase.BUILDING_CONTEXT, "context.built": RuntimePhase.BUILDING_CONTEXT,
    "model.request.started": RuntimePhase.CALLING_MODEL, "model.requested": RuntimePhase.CALLING_MODEL,
    "model.response.completed": RuntimePhase.PROCESSING_RESPONSE, "model.completed": RuntimePhase.PROCESSING_RESPONSE,
    "approval.requested": RuntimePhase.WAITING_APPROVAL, "tool.execution.started": RuntimePhase.RUNNING_TOOL, "tool.started": RuntimePhase.RUNNING_TOOL,
    "subagent.started": RuntimePhase.WAITING_SUBAGENT, "context.compaction.started": RuntimePhase.COMPACTING,
    "agent.spawned": RuntimePhase.WAITING_SUBAGENT, "agent.wait_started": RuntimePhase.WAITING_SUBAGENT,
    "recovery.started": RuntimePhase.RECOVERING, "turn.finalizing": RuntimePhase.FINALIZING,
    "turn.completed": RuntimePhase.COMPLETED, "run.completed": RuntimePhase.COMPLETED,
    "turn.failed": RuntimePhase.FAILED, "run.failed": RuntimePhase.FAILED,
    "turn.cancelled": RuntimePhase.CANCELLED, "run.cancelled": RuntimePhase.CANCELLED,
}


@dataclass(slots=True)
class TraceViewState:
    """Materialized CLI state produced from historical replay and live events."""

    phase: RuntimePhase = RuntimePhase.READY
    active_tool: str | None = None
    model: str | None = None
    turn_id: str | None = None
    events: list[TraceEvent] = field(default_factory=list)


class TraceReducer:
    """Reduce immutable runtime facts into a renderer-independent view state."""

    def __init__(self, usage: UsageReducer | None = None) -> None:
        """Initialize an empty view and shared usage reducer."""
        self.state = TraceViewState()
        self.usage = usage or UsageReducer()

    def apply(self, event: TraceEvent) -> None:
        """Apply one event without inspecting provider or tool runtime internals."""
        self.state.events.append(event)
        self.state.phase = PHASE_BY_EVENT.get(event.event_type, self.state.phase)
        self.state.turn_id = event.turn_id or str(event.payload.get("turn_id") or self.state.turn_id or "") or None
        if event.event_type in {"model.request.started", "model.requested", "model.response.completed", "model.completed"}:
            self.state.model = str(event.payload.get("model") or self.state.model or "") or None
        if event.event_type in {"tool.execution.started", "tool.started"}:
            self.state.active_tool = str(event.payload.get("tool_name") or event.payload.get("tool") or "") or None
        if event.event_type in {"tool.execution.completed", "tool.execution.failed", "tool.completed", "tool.failed", "tool.timed_out"}:
            self.state.active_tool = None
        self.usage.apply(event)

    def usage_snapshot(self) -> UsageSnapshot:
        """Return usage for the reducer's current turn and complete thread."""
        return self.usage.snapshot(self.state.turn_id)
