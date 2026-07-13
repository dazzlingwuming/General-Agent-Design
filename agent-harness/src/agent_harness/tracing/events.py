from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_harness.utils.ids import new_id
from agent_harness.utils.serialization import to_jsonable
from agent_harness.utils.time import iso_now


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """Versioned runtime fact shared by persistence, replay, and live CLI views."""

    event_type: str
    run_id: str
    sequence_number: int
    schema_version: int = 2
    event_id: str = field(default_factory=lambda: new_id("evt"))
    timestamp: str = field(default_factory=iso_now)
    iteration: int = 0
    parent_event_id: str | None = None
    correlation_id: str | None = None
    logical_action_id: str | None = None
    agent_id: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    parent_agent_id: str | None = None
    delegation_request_id: str | None = None
    depth: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible event representation without exposing objects."""
        value = to_jsonable(self)
        return value if isinstance(value, dict) else {}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TraceEvent:
        """Parse persisted v1 or v2 events into the current in-memory schema."""
        known = {name for name in cls.__dataclass_fields__}
        data = {key: value for key, value in value.items() if key in known}
        data.setdefault("schema_version", 1)
        data.setdefault("payload", {})
        return cls(**data)
