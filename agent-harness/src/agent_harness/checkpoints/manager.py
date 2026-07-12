from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from agent_harness.checkpoints.models import CheckpointEnvelope, DurableTurnStatus, ResumePoint
from agent_harness.checkpoints.serializer import serialize_run_state
from agent_harness.checkpoints.store import CheckpointStore
from agent_harness.domain.run import RunState
from agent_harness.utils.ids import new_id
from agent_harness.utils.time import iso_now


@dataclass(slots=True)
class CheckpointManager:
    """Write monotonic checkpoints for one thread without owning transient runtime objects."""

    store: CheckpointStore
    thread_id: str
    provider_name: str
    model_name: str
    config_digest: str = ""
    runtime_version: str = "0.1.0"
    sequence: int = 0

    def save(self, state: RunState, point: ResumePoint, status: DurableTurnStatus = DurableTurnStatus.RUNNING, *, pending_action_ids: tuple[str, ...] = ()) -> CheckpointEnvelope:
        """Commit one complete JSON state snapshot at a safe logical boundary."""
        if not state.turn_id:
            raise ValueError("Cannot checkpoint a state without turn_id")
        self.sequence += 1
        envelope = CheckpointEnvelope(
            schema_version=1,
            checkpoint_id=new_id("checkpoint"),
            session_id=self.thread_id,
            thread_id=self.thread_id,
            turn_id=state.turn_id,
            agent_id=state.agent_name,
            checkpoint_sequence=self.sequence,
            rollout_sequence=0,
            resume_point=point,
            turn_status=status,
            runtime_version=self.runtime_version,
            config_digest=self.config_digest or _digest({"provider": self.provider_name, "model": self.model_name}),
            provider_name=self.provider_name,
            model_name=self.model_name,
            serialized_state=serialize_run_state(state),
            pending_action_ids=pending_action_ids,
            created_at=iso_now(),
        )
        return self.store.save(envelope)

    def resume_sequence(self, turn_id: str) -> None:
        """Continue the checkpoint sequence of an existing durable turn."""
        latest = self.store.latest(self.thread_id, turn_id)
        self.sequence = latest.checkpoint_sequence if latest else 0


def stable_action_id(thread_id: str, turn_id: str, tool_call_id: str) -> str:
    """Derive one stable logical action identity across process restarts."""
    return "action_" + hashlib.sha256("\0".join((thread_id, turn_id, tool_call_id)).encode()).hexdigest()[:32]


def _digest(payload: dict[str, str]) -> str:
    """Hash compatibility metadata using canonical JSON."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
