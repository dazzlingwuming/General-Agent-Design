from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from agent_harness.domain.errors import RunError
from agent_harness.domain.model import Usage
from agent_harness.utils.time import iso_now


class ThreadStatus(str, Enum):
    """Runtime and persisted status for a conversation thread."""

    CREATED = "CREATED"
    IDLE = "IDLE"
    ACTIVE = "ACTIVE"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


class TurnStatus(str, Enum):
    """Lifecycle status for a single user request and the work that follows."""

    CREATED = "CREATED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INTERRUPTING = "INTERRUPTING"
    INTERRUPTED = "INTERRUPTED"


@dataclass(slots=True)
class InputItem:
    """User input captured as initial turn text or mid-turn steering text."""

    text: str
    input_kind: str = "initial"
    created_at: str = field(default_factory=iso_now)


@dataclass(slots=True)
class TurnState:
    """State for one root or child turn, including turn-local counters and output."""

    turn_id: str
    thread_id: str
    status: TurnStatus
    initial_user_input: list[InputItem] = field(default_factory=list)
    steer_inputs: list[InputItem] = field(default_factory=list)
    started_at: str = field(default_factory=iso_now)
    completed_at: str | None = None
    iteration: int = 0
    model_call_count: int = 0
    tool_call_count: int = 0
    usage: Usage = field(default_factory=Usage)
    final_output: str | None = None
    error: RunError | None = None


@dataclass(slots=True)
class ThreadState:
    """Persisted metadata and live status for a Codex-style conversation thread."""

    thread_id: str
    session_id: str
    workspace_root: Path
    status: ThreadStatus
    project_root: Path | None = None
    cwd: Path | None = None
    parent_thread_id: str | None = None
    forked_from_id: str | None = None
    main_agent_id: str = "coding_assistant"
    active_turn_id: str | None = None
    child_thread_ids: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=iso_now)
    updated_at: str = field(default_factory=iso_now)
    turn_count: int = 0
    cumulative_usage: Usage = field(default_factory=Usage)
    metadata: dict[str, Any] = field(default_factory=dict)
