from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from agent_harness.domain.run import RunState, RunStatus
from agent_harness.persistence.database import SQLiteDatabase
from agent_harness.utils.ids import new_id
from agent_harness.utils.time import iso_now


@dataclass(frozen=True, slots=True)
class CompactionRecord:
    """Durable pointer from canonical source messages to a rebuildable summary."""

    compaction_id: str
    thread_id: str
    turn_id: str
    source_hash: str
    summary_text: str
    summary_hash: str
    protected_message_ids: tuple[str, ...]
    created_at: str
    schema_version: int = 1


class CompactionService:
    """Compact model-visible history only after a completed turn is idle."""

    def __init__(self, database: SQLiteDatabase, *, retain_recent_turns: int, max_summary_chars: int) -> None:
        """Configure deterministic retention and initialize the compaction table."""
        self.database = database
        self.retain_recent_turns = retain_recent_turns
        self.max_summary_chars = max_summary_chars
        with database.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS compaction_records(compaction_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, turn_id TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL)")

    def compact(self, state: RunState) -> CompactionRecord | None:
        """Summarize old visible messages while preserving tool/pending content and canonical files."""
        if state.status != RunStatus.COMPLETED or not state.turn_id:
            return None
        user_indexes = [index for index, message in enumerate(state.messages) if message.role == "user" and not message.metadata.get("external_context")]
        if len(user_indexes) <= self.retain_recent_turns:
            return None
        boundary = user_indexes[-self.retain_recent_turns]
        old = state.messages[:boundary]
        protected = [message for message in old if message.tool_calls or message.role == "tool"]
        compactable = [message for message in old if message not in protected]
        if not compactable:
            return None
        source = "\n".join(f"{message.role}: {message.content}" for message in compactable)
        summary = source[: self.max_summary_chars]
        record = CompactionRecord(new_id("compaction"), state.run_id, state.turn_id, hashlib.sha256(source.encode()).hexdigest(), summary,
            hashlib.sha256(summary.encode()).hexdigest(), tuple(message.message_id for message in protected), iso_now())
        with self.database.transaction() as db:
            db.execute("INSERT INTO compaction_records VALUES(?,?,?,?,?)", (record.compaction_id, record.thread_id, record.turn_id,
                json.dumps({field: getattr(record, field) for field in record.__dataclass_fields__}, ensure_ascii=False), record.created_at))
        state.session_summary = summary
        state.messages = [*protected, *state.messages[boundary:]]
        return record
