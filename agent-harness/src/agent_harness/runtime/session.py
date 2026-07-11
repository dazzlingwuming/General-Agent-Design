from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_harness.config import HarnessConfig
from agent_harness.domain.messages import CanonicalMessage
from agent_harness.domain.run import RunState
from agent_harness.runtime.run_manager import RunManager
from agent_harness.tracing.jsonl import JsonlTraceSink
from agent_harness.tracing.summary import write_result_summary
from agent_harness.utils.ids import new_id
from agent_harness.utils.serialization import to_jsonable
from agent_harness.utils.time import iso_now


@dataclass(slots=True)
class ConversationSession:
    """Persist and execute a multi-turn interactive CLI conversation."""

    config: HarnessConfig
    manager: RunManager
    workspace: Path
    session_id: str = field(default_factory=lambda: new_id("session"))
    state: RunState | None = None

    @property
    def session_dir(self) -> Path:
        """Return the directory that owns all artifacts for this conversation."""
        return self.config.trace.session_directory / self.session_id

    @property
    def transcript_path(self) -> Path:
        """Return the append-only transcript path for user and assistant messages."""
        return self.session_dir / "transcript.jsonl"

    def start(self) -> None:
        """Create session metadata and initialize reusable run state."""
        self.session_dir.mkdir(parents=True, exist_ok=True)
        root = self.workspace.resolve()
        self.state = RunState(task="", workspace_root=root)
        self.state.run_id = self.session_id
        self._write_metadata(status="active")

    async def run_turn(self, user_input: str) -> RunState:
        """Append one user message, run the agent loop, and persist the turn result."""
        if self.state is None:
            self.start()
        assert self.state is not None
        self.state.turn_count += 1
        self.state.turn_id = f"turn_{self.state.turn_count:04d}"
        self.state.task = user_input
        self.state.final_output = None
        self.state.error = None
        self.state.messages.append(CanonicalMessage(role="user", content=user_input))
        self._append_transcript({"type": "user", "turn_id": self.state.turn_id, "content": user_input})
        state = await self.manager.run_existing(self.state, self.config.trace.session_directory)
        self._append_transcript(
            {
                "type": "assistant",
                "turn_id": self.state.turn_id,
                "status": state.status.value,
                "content": state.final_output,
                "error": to_jsonable(state.error),
            }
        )
        self._write_turn_summary(state)
        self._write_metadata(status="active")
        return state

    def close(self) -> None:
        """Mark the persisted session as closed without deleting its transcript."""
        self._write_metadata(status="closed")

    def _append_transcript(self, payload: dict[str, Any]) -> None:
        """Append one JSONL transcript record with a timestamp."""
        self.session_dir.mkdir(parents=True, exist_ok=True)
        record = {"timestamp": iso_now(), "session_id": self.session_id, **payload}
        with self.transcript_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(to_jsonable(record), ensure_ascii=False) + "\n")

    def _write_metadata(self, status: str) -> None:
        """Write the current session metadata used by future resume/list commands."""
        self.session_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": self.session_id,
            "status": status,
            "workspace_root": str(self.workspace.resolve()),
            "turn_count": self.state.turn_count if self.state else 0,
            "updated_at": iso_now(),
            "trace_path": str(self.session_dir / "events.jsonl"),
            "transcript_path": str(self.transcript_path),
        }
        (self.session_dir / "session.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_turn_summary(self, state: RunState) -> None:
        """Write a per-turn result while keeping the session-level result current."""
        turns_dir = self.session_dir / "turns"
        turns_dir.mkdir(parents=True, exist_ok=True)
        result_path = write_result_summary(state, self.session_dir, self.session_dir / "events.jsonl")
        turn_path = turns_dir / f"{state.turn_id or 'turn_unknown'}-result.json"
        turn_path.write_text(result_path.read_text(encoding="utf-8"), encoding="utf-8")


def create_session_trace(session_id: str, session_root: Path, config: HarnessConfig) -> JsonlTraceSink:
    """Create a trace sink whose run_id is the stable interactive session id."""
    return JsonlTraceSink(session_id, session_root, fail_on_write_error=config.trace.fail_on_write_error)
