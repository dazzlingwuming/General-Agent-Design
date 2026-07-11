from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent_harness.domain.errors import RunError
from agent_harness.domain.messages import CanonicalMessage
from agent_harness.domain.model import Usage
from agent_harness.utils.ids import new_id
from agent_harness.utils.time import utc_now


class AgentThreadStatus(str, Enum):
    """Lifecycle states for a managed child agent thread."""

    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    IDLE = "IDLE"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    CLOSED = "CLOSED"


class AgentTurnStatus(str, Enum):
    """Lifecycle states for one child agent turn."""

    CREATED = "CREATED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(slots=True)
class EvidenceItem:
    """Structured evidence item returned by a child agent."""

    path: str
    start_line: int
    end_line: int
    claim: str
    excerpt: str | None = None


@dataclass(slots=True)
class SubagentResult:
    """Structured result envelope passed from a child agent back to the root agent."""

    agent_id: str
    agent_name: str
    status: str
    summary: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    confidence: float = 0.0
    structured_data: dict[str, Any] = field(default_factory=dict)
    result_ref: str | None = None
    error: RunError | None = None


@dataclass(slots=True)
class AgentTurnState:
    """Runtime state for one initial task or follow-up inside a child thread."""

    agent_id: str
    input_message: str
    sequence: int
    turn_id: str = field(default_factory=lambda: new_id("turn"))
    status: AgentTurnStatus = AgentTurnStatus.CREATED
    started_at: object | None = None
    completed_at: object | None = None
    result: SubagentResult | None = None
    usage: Usage = field(default_factory=Usage)
    model_call_count: int = 0
    tool_call_count: int = 0
    error: RunError | None = None


@dataclass(slots=True)
class AgentThreadState:
    """Runtime state for one managed child agent thread."""

    run_id: str
    parent_agent_id: str
    agent_definition_name: str
    depth: int
    task: str
    agent_id: str = field(default_factory=lambda: new_id("agent"))
    thread_id: str = field(default_factory=lambda: new_id("thread"))
    status: AgentThreadStatus = AgentThreadStatus.CREATED
    message_history: list[CanonicalMessage] = field(default_factory=list)
    current_turn_id: str | None = None
    turn_count: int = 0
    mailbox: list[str] = field(default_factory=list)
    created_at: object = field(default_factory=utc_now)
    updated_at: object = field(default_factory=utc_now)
    last_result: SubagentResult | None = None
    cumulative_usage: Usage = field(default_factory=Usage)
    error: RunError | None = None
    closed_at: object | None = None


@dataclass(slots=True)
class DelegationRequest:
    """Validated request from the root agent to spawn or reuse a child agent."""

    agent_name: str
    task: str
    context: str = ""
    expected_focus: str = ""
    idempotency_key: str | None = None

