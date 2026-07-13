from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from agent_harness.domain.errors import ConfigurationError
from agent_harness.tracing.bus import RuntimeEventBus
from agent_harness.tracing.events import TraceEvent
from agent_harness.tracing.pipeline import CallbackTraceSink, CompositeTraceSink, TraceSink


class JsonlTraceSink:
    def __init__(self, run_id: str, trace_root: Path, *, fail_on_write_error: bool = True, event_bus: RuntimeEventBus | None = None):
        """Open the append-only JSONL trace file for a single run."""
        self.run_id = run_id
        self.run_dir = trace_root / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "events.jsonl"
        self.fail_on_write_error = fail_on_write_error
        self.sequence_number = self._read_last_sequence_number()
        self._handle = self.path.open("a", encoding="utf-8")
        self._lock = threading.Lock()
        self.event_bus = event_bus
        sinks: list[TraceSink] = [CallbackTraceSink(self._write_event)]
        if event_bus:
            sinks.append(event_bus)
        self.composite = CompositeTraceSink(sinks)

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
        correlation_id: str | None = None,
        logical_action_id: str | None = None,
    ) -> str:
        """Append one typed trace event and return its generated event id."""
        with self._lock:
            self.sequence_number += 1
            event = TraceEvent(event_type=event_type, run_id=self.run_id, sequence_number=self.sequence_number, iteration=iteration,
                parent_event_id=parent_event_id, correlation_id=correlation_id, logical_action_id=logical_action_id,
                agent_id=agent_id, thread_id=thread_id, turn_id=turn_id, parent_agent_id=parent_agent_id,
                delegation_request_id=delegation_request_id, depth=depth, payload=payload or {})
            self.composite.write(event)
            return event.event_id

    def _write_event(self, event: TraceEvent) -> None:
        """Persist an already-created event before it reaches live subscribers."""
        try:
            self._handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
            self._handle.flush()
        except Exception as exc:
            if self.fail_on_write_error:
                raise ConfigurationError("Trace write failed", details={"path": str(self.path)}) from exc

    def read_events(self) -> list[TraceEvent]:
        """Read persisted events in strict sequence order for deterministic replay."""
        events = [TraceEvent.from_dict(json.loads(line)) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        expected = list(range(1, len(events) + 1))
        actual = [event.sequence_number for event in events]
        if actual != expected:
            raise ConfigurationError("Trace sequence is not contiguous", details={"path": str(self.path)})
        return events

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
