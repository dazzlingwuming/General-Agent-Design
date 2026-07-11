from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from agent_harness.domain.errors import RunError
from agent_harness.domain.messages import CanonicalMessage
from agent_harness.domain.model import Usage
from agent_harness.utils.ids import new_id
from agent_harness.utils.time import utc_now


class RunStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(slots=True)
class RunLimits:
    max_iterations: int = 20
    max_model_calls: int = 20
    max_tool_calls: int = 50
    max_wall_time_seconds: int = 900


@dataclass(slots=True)
class RunState:
    """Mutable state for one interactive session or one non-interactive run."""

    task: str
    workspace_root: Path
    agent_name: str = "coding_assistant"
    run_id: str = field(default_factory=lambda: new_id("run"))
    turn_id: str | None = None
    turn_count: int = 0
    session_summary: str = ""
    status: RunStatus = RunStatus.CREATED
    messages: list[CanonicalMessage] = field(default_factory=list)
    iteration: int = 0
    model_call_count: int = 0
    tool_call_count: int = 0
    usage_total: Usage = field(default_factory=Usage)
    started_at: object = field(default_factory=utc_now)
    updated_at: object = field(default_factory=utc_now)
    completed_at: object | None = None
    final_output: str | None = None
    error: RunError | None = None
    cancellation_requested: bool = False
    agent_summary: dict | None = None
