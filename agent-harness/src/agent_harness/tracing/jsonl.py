from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from agent_harness.domain.errors import ConfigurationError
from agent_harness.utils.ids import new_id
from agent_harness.utils.serialization import to_jsonable
from agent_harness.utils.time import iso_now


class JsonlTraceSink:
    def __init__(self, run_id: str, trace_root: Path, *, fail_on_write_error: bool = True):
        """Open the append-only JSONL trace file for a single run."""
        self.run_id = run_id
        self.run_dir = trace_root / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "events.jsonl"
        self.fail_on_write_error = fail_on_write_error
        self.sequence_number = self._read_last_sequence_number()
        self._handle = self.path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

    def emit(
        self,
        event_type: str,
        *,
        iteration: int = 0,
        payload: dict[str, Any] | None = None,
        parent_event_id: str | None = None,
        agent_id: str | None = None,
        thread_id: str | None = None,
        turn_id: str | None = None,
        parent_agent_id: str | None = None,
        delegation_request_id: str | None = None,
        depth: int | None = None,
    ) -> str:
        """Append one typed trace event and return its generated event id."""
        with self._lock:
            self.sequence_number += 1
            event = {
                "event_id": new_id("evt"),
                "event_type": event_type,
                "timestamp": iso_now(),
                "run_id": self.run_id,
                "sequence_number": self.sequence_number,
                "iteration": iteration,
                "parent_event_id": parent_event_id,
                "agent_id": agent_id,
                "thread_id": thread_id,
                "turn_id": turn_id,
                "parent_agent_id": parent_agent_id,
                "delegation_request_id": delegation_request_id,
                "depth": depth,
                "payload": to_jsonable(payload or {}),
            }
            try:
                self._handle.write(json.dumps(event, ensure_ascii=False) + "\n")
                self._handle.flush()
            except Exception as exc:
                if self.fail_on_write_error:
                    raise ConfigurationError("Trace write failed", details={"path": str(self.path)}) from exc
            return event["event_id"]

    def close(self) -> None:
        """Close the underlying JSONL file handle."""
        with self._lock:
            self._handle.close()

    def _read_last_sequence_number(self) -> int:
        """Return the last persisted sequence number when appending to a trace."""
        if not self.path.exists():
            return 0
        last_sequence = 0
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                last_sequence = max(last_sequence, int(event.get("sequence_number", 0)))
        except Exception as exc:
            if self.fail_on_write_error:
                raise ConfigurationError("Trace read failed", details={"path": str(self.path)}) from exc
        return last_sequence
