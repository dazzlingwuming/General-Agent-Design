from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from agent_harness.utils.ids import new_id
from agent_harness.utils.time import utc_now

MessageRole = Literal["system", "user", "assistant", "tool"]


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] | str
    raw_arguments: str | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    sequence_index: int = 0


@dataclass(slots=True)
class CanonicalMessage:
    role: MessageRole
    content: str = ""
    message_id: str = field(default_factory=lambda: new_id("msg"))
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None
    created_at: object = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

