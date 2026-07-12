from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import Any

from agent_harness.checkpoints.models import CheckpointEnvelope, DurableTurnStatus, ResumePoint
from agent_harness.persistence.database import SQLiteDatabase
from agent_harness.utils.serialization import to_jsonable


class CheckpointStore:
    """Persist checkpoints and durable transition events in a local SQLite database."""

    def __init__(self, path: Path) -> None:
        """Initialize the runtime schema using idempotent migrations."""
        self.database = SQLiteDatabase(path)
        self.migrate()

    def migrate(self) -> None:
        """Create the phase 6 runtime schema and record its migration version."""
        with self.database.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS checkpoints(
                    checkpoint_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                    checkpoint_sequence INTEGER NOT NULL, resume_point TEXT NOT NULL, turn_status TEXT NOT NULL,
                    payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(thread_id, turn_id, checkpoint_sequence));
                CREATE INDEX IF NOT EXISTS checkpoints_latest ON checkpoints(thread_id, turn_id, checkpoint_sequence DESC);
                CREATE TABLE IF NOT EXISTS logical_actions(
                    logical_action_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                    action_type TEXT NOT NULL, state TEXT NOT NULL, request_json TEXT NOT NULL,
                    result_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS pending_approvals(
                    approval_id TEXT PRIMARY KEY, logical_action_id TEXT NOT NULL, thread_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL, payload_json TEXT NOT NULL, state TEXT NOT NULL, decision TEXT);
                CREATE TABLE IF NOT EXISTS recovery_records(
                    recovery_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                    action TEXT NOT NULL, detail_json TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS rollout_outbox(
                    event_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, sequence_number INTEGER NOT NULL,
                    payload_json TEXT NOT NULL, projected_at TEXT, UNIQUE(thread_id, sequence_number));
                INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(1, datetime('now'));
            """)

    def save(self, checkpoint: CheckpointEnvelope, *, outbox_events: tuple[dict[str, Any], ...] = ()) -> CheckpointEnvelope:
        """Commit a checkpoint and its canonical rollout intents in one transaction."""
        durable = checkpoint.with_hash()
        payload = json.dumps(to_jsonable(durable), ensure_ascii=False, sort_keys=True)
        with self.database.transaction() as db:
            db.execute(
                "INSERT INTO checkpoints VALUES(?,?,?,?,?,?,?,?,?)",
                (durable.checkpoint_id, durable.thread_id, durable.turn_id, durable.checkpoint_sequence, durable.resume_point.value,
                 durable.turn_status.value, payload, durable.payload_hash, durable.created_at),
            )
            for event in outbox_events:
                db.execute(
                    "INSERT OR IGNORE INTO rollout_outbox(event_id,thread_id,sequence_number,payload_json) VALUES(?,?,?,?)",
                    (event["event_id"], durable.thread_id, int(event["sequence_number"]), json.dumps(event, ensure_ascii=False, sort_keys=True)),
                )
        return durable

    def latest(self, thread_id: str, turn_id: str | None = None) -> CheckpointEnvelope | None:
        """Load and verify the newest checkpoint for a thread or one specific turn."""
        db = self.database.connect()
        try:
            if turn_id:
                row = db.execute("SELECT payload_json FROM checkpoints WHERE thread_id=? AND turn_id=? ORDER BY checkpoint_sequence DESC LIMIT 1", (thread_id, turn_id)).fetchone()
            else:
                row = db.execute("SELECT payload_json FROM checkpoints WHERE thread_id=? ORDER BY rowid DESC LIMIT 1", (thread_id,)).fetchone()
        finally:
            db.close()
        if row is None:
            return None
        checkpoint = _checkpoint_from_dict(json.loads(row[0]))
        if not checkpoint.verify():
            raise ValueError(f"Checkpoint integrity check failed: {checkpoint.checkpoint_id}")
        return checkpoint

    def list_pending_outbox(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return unprojected canonical events in stable thread sequence order."""
        db = self.database.connect()
        try:
            rows = db.execute("SELECT payload_json FROM rollout_outbox WHERE projected_at IS NULL ORDER BY thread_id, sequence_number LIMIT ?", (limit,)).fetchall()
            return [json.loads(row[0]) for row in rows]
        finally:
            db.close()

    def mark_projected(self, event_id: str) -> None:
        """Mark one outbox event projected without deleting audit history."""
        with self.database.transaction() as db:
            db.execute("UPDATE rollout_outbox SET projected_at=datetime('now') WHERE event_id=?", (event_id,))

    def save_approval(self, approval_id: str, logical_action_id: str, thread_id: str, turn_id: str, payload: dict[str, Any], state: str, decision: str | None = None) -> None:
        """Upsert one stable approval request or committed decision."""
        with self.database.transaction() as db:
            db.execute(
                "INSERT INTO pending_approvals VALUES(?,?,?,?,?,?,?) ON CONFLICT(approval_id) DO UPDATE SET payload_json=excluded.payload_json,state=excluded.state,decision=excluded.decision",
                (approval_id, logical_action_id, thread_id, turn_id, json.dumps(payload, ensure_ascii=False, sort_keys=True), state, decision),
            )

    def approval_decision(self, approval_id: str) -> str | None:
        """Return an already committed decision for the exact approval identity."""
        db = self.database.connect()
        try:
            row = db.execute("SELECT decision FROM pending_approvals WHERE approval_id=? AND state='decided'", (approval_id,)).fetchone()
            return str(row[0]) if row and row[0] else None
        finally:
            db.close()


def _checkpoint_from_dict(data: dict[str, Any]) -> CheckpointEnvelope:
    """Decode a checkpoint while rejecting unknown incompatible schema versions."""
    if int(data.get("schema_version", 0)) != 1:
        raise ValueError(f"Unsupported checkpoint schema: {data.get('schema_version')}")
    names = {item.name for item in fields(CheckpointEnvelope)}
    values = {key: value for key, value in data.items() if key in names}
    values["resume_point"] = ResumePoint(values["resume_point"])
    values["turn_status"] = DurableTurnStatus(values["turn_status"])
    for key in ("pending_action_ids", "pending_approval_ids", "child_execution_ids"):
        values[key] = tuple(values.get(key) or ())
    return CheckpointEnvelope(**values)
