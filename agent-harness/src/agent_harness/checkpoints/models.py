from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from agent_harness.utils.serialization import to_jsonable


class ResumePoint(StrEnum):
    """Stable logical boundaries from which one turn can continue."""

    BEFORE_MODEL = "before_model"
    MODEL_IN_FLIGHT = "model_in_flight"
    AFTER_MODEL = "after_model"
    WAITING_APPROVAL = "waiting_approval"
    BEFORE_TOOL = "before_tool"
    TOOL_IN_FLIGHT = "tool_in_flight"
    AFTER_TOOL = "after_tool"
    WAITING_SUBAGENT = "waiting_subagent"
    BEFORE_FINALIZE = "before_finalize"
    PAUSED = "paused"
    RECOVERY_REQUIRED = "recovery_required"
    TERMINAL = "terminal"


class DurableTurnStatus(StrEnum):
    """Persisted lifecycle state independent from in-process RunStatus."""

    CREATED = "created"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_SUBAGENT = "waiting_subagent"
    PAUSED = "paused"
    RECOVERING = "recovering"
    RECOVERY_REQUIRED = "recovery_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class CheckpointEnvelope:
    """Versioned, JSON-only snapshot of a turn at one safe execution boundary."""

    schema_version: int
    checkpoint_id: str
    session_id: str
    thread_id: str
    turn_id: str
    agent_id: str
    checkpoint_sequence: int
    rollout_sequence: int
    resume_point: ResumePoint
    turn_status: DurableTurnStatus
    runtime_version: str
    config_digest: str
    provider_name: str
    model_name: str
    serialized_state: dict[str, Any]
    parent_agent_id: str | None = None
    child_thread_id: str | None = None
    pending_action_ids: tuple[str, ...] = ()
    pending_approval_ids: tuple[str, ...] = ()
    child_execution_ids: tuple[str, ...] = ()
    created_at: str = ""
    payload_hash: str = ""

    def with_hash(self) -> CheckpointEnvelope:
        """Return a copy whose hash covers every field except the hash itself."""
        payload = to_jsonable(replace(self, payload_hash=""))
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return replace(self, payload_hash=hashlib.sha256(encoded).hexdigest())

    def verify(self) -> bool:
        """Verify that persisted checkpoint content has not changed."""
        return bool(self.payload_hash) and self.with_hash().payload_hash == self.payload_hash
