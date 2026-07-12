from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent_harness.utils.ids import new_id
from agent_harness.utils.time import iso_now


class ItemStatus(str, Enum):
    """Lifecycle states shared by canonical rollout items."""

    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


@dataclass(slots=True)
class RolloutItem:
    """Append-only canonical record for one thread, turn, agent, or tool action."""

    item_id: str
    item_type: str
    session_id: str
    thread_id: str
    turn_id: str | None
    agent_id: str | None
    parent_agent_id: str | None
    child_thread_id: str | None
    status: ItemStatus
    created_at: str
    completed_at: str | None
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        item_type: str,
        *,
        session_id: str,
        thread_id: str,
        turn_id: str | None = None,
        agent_id: str | None = None,
        parent_agent_id: str | None = None,
        child_thread_id: str | None = None,
        status: ItemStatus = ItemStatus.COMPLETED,
        payload: dict[str, Any] | None = None,
        item_id: str | None = None,
        completed_at: str | None = None,
    ) -> RolloutItem:
        """Build a rollout item with the common identity and timestamp fields filled."""
        now = iso_now()
        return cls(
            item_id=item_id or new_id("item"),
            item_type=item_type,
            session_id=session_id,
            thread_id=thread_id,
            turn_id=turn_id,
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
            child_thread_id=child_thread_id,
            status=status,
            created_at=now,
            completed_at=completed_at if completed_at is not None else now,
            payload=payload or {},
        )


def item_from_dict(data: dict[str, Any]) -> RolloutItem:
    """Deserialize one rollout item from a JSON-compatible dictionary."""
    return RolloutItem(
        item_id=str(data["item_id"]),
        item_type=str(data["item_type"]),
        session_id=str(data["session_id"]),
        thread_id=str(data["thread_id"]),
        turn_id=data.get("turn_id"),
        agent_id=data.get("agent_id"),
        parent_agent_id=data.get("parent_agent_id"),
        child_thread_id=data.get("child_thread_id"),
        status=ItemStatus(str(data["status"])),
        created_at=str(data["created_at"]),
        completed_at=data.get("completed_at"),
        payload=dict(data.get("payload") or {}),
    )
