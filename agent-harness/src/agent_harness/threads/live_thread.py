from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_harness.rollout.items import RolloutItem
from agent_harness.threads.recorder import RolloutRecorder
from agent_harness.turns.state import ThreadState, ThreadStatus
from agent_harness.utils.serialization import to_jsonable
from agent_harness.utils.time import iso_now


@dataclass(slots=True)
class LiveThread:
    """Active persistence handle for one loaded thread."""

    state: ThreadState
    thread_dir: Path
    recorder: RolloutRecorder

    @property
    def metadata_path(self) -> Path:
        """Return the metadata JSON path for this thread."""
        return self.thread_dir / "metadata.json"

    @property
    def rollout_path(self) -> Path:
        """Return the append-only canonical rollout path for this thread."""
        return self.thread_dir / "rollout.jsonl"

    async def append_items(self, items: list[RolloutItem]) -> None:
        """Append canonical rollout items through the single writer."""
        await self.recorder.record(items)

    async def update_metadata(self, patch: dict[str, Any]) -> None:
        """Apply a shallow metadata patch and persist the thread metadata file."""
        for key, value in patch.items():
            setattr(self.state, key, value)
        self.state.updated_at = iso_now()
        await asyncio_to_thread_write_json(self.metadata_path, _thread_metadata(self.state))

    async def persist(self) -> None:
        """Persist metadata and flush queued rollout items."""
        await asyncio_to_thread_write_json(self.metadata_path, _thread_metadata(self.state))
        await self.recorder.flush()

    async def flush(self) -> None:
        """Wait until all queued rollout items are durable."""
        await self.recorder.flush()

    async def shutdown(self) -> None:
        """Flush and stop the thread recorder."""
        await self.recorder.shutdown()


async def asyncio_to_thread_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON from an async caller without blocking the event loop."""
    import asyncio

    await asyncio.to_thread(_write_json, path, payload)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write one formatted JSON object to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def _thread_metadata(state: ThreadState) -> dict[str, Any]:
    """Convert thread state into compact query metadata without rollout history."""
    return {
        "thread_id": state.thread_id,
        "session_id": state.session_id,
        "parent_thread_id": state.parent_thread_id,
        "forked_from_id": state.forked_from_id,
        "workspace_root": str(state.workspace_root),
        "name": state.metadata.get("name"),
        "preview": state.metadata.get("preview"),
        "status": state.status.value if isinstance(state.status, ThreadStatus) else state.status,
        "model_provider": state.metadata.get("model_provider"),
        "model": state.metadata.get("model"),
        "created_at": state.created_at,
        "updated_at": state.updated_at,
        "last_turn_id": state.active_turn_id,
        "turn_count": state.turn_count,
        "archived": bool(state.metadata.get("archived", False)),
        "child_thread_ids": list(state.child_thread_ids),
    }
