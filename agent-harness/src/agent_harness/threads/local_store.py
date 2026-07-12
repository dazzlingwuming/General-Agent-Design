from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from agent_harness.rollout.items import ItemStatus, RolloutItem, item_from_dict
from agent_harness.threads.live_thread import LiveThread
from agent_harness.threads.recorder import RolloutRecorder
from agent_harness.turns.state import ThreadState, ThreadStatus
from agent_harness.utils.ids import new_id
from agent_harness.utils.time import iso_now


class LocalThreadStore:
    """Local filesystem implementation of ThreadStore using metadata.json and rollout.jsonl."""

    def __init__(self, root: Path) -> None:
        """Create a local thread store under the supplied root directory."""
        self.root = root

    async def create_thread(self, workspace_root: Path, *, provider: str, model: str, project_root: Path | None = None, cwd: Path | None = None) -> LiveThread:
        """Create a new local thread and persist its metadata plus thread.created item."""
        thread_id = new_id("thread")
        state = ThreadState(
            thread_id=thread_id,
            session_id=thread_id,
            workspace_root=workspace_root.resolve(),
            status=ThreadStatus.IDLE,
            project_root=(project_root or workspace_root).resolve(),
            cwd=(cwd or workspace_root).resolve(),
            metadata={"model_provider": provider, "model": model, "archived": False},
        )
        live = self._live_thread(state)
        await live.update_metadata({})
        await live.append_items(
            [
                RolloutItem.create(
                    "thread.created",
                    session_id=state.session_id,
                    thread_id=state.thread_id,
                    payload={"workspace_root": str(state.workspace_root), "model_provider": provider, "model": model},
                )
            ]
        )
        await live.flush()
        return live

    async def resume_thread(self, thread_id: str) -> LiveThread:
        """Load a thread from metadata and mark incomplete turns interrupted during recovery."""
        metadata = await asyncio.to_thread(self._read_metadata, thread_id)
        state = self._state_from_metadata(metadata)
        history = await self.load_history(thread_id)
        active_turn_id = _active_turn_from_history(history)
        live = self._live_thread(state)
        try:
            if active_turn_id:
                item = RolloutItem.create(
                    "turn.interrupted",
                    session_id=state.session_id,
                    thread_id=state.thread_id,
                    turn_id=active_turn_id,
                    status=ItemStatus.INTERRUPTED,
                    payload={"reason": "recovered incomplete turn as interrupted"},
                )
                await live.append_items([item])
                await live.flush()
            state.status = ThreadStatus.IDLE
            state.active_turn_id = None
            await live.update_metadata({})
            return live
        except BaseException:
            await live.shutdown()
            raise

    async def append_items(self, thread_id: str, items: list[RolloutItem]) -> None:
        """Append items to a thread by opening a short-lived live handle."""
        live = await self.resume_thread(thread_id)
        try:
            await live.append_items(items)
            await live.flush()
        finally:
            await live.shutdown()

    async def load_history(self, thread_id: str) -> list[RolloutItem]:
        """Read rollout history while skipping malformed JSONL rows."""
        return await asyncio.to_thread(self._load_history_sync, thread_id)

    async def list_threads(self) -> list[dict[str, Any]]:
        """Return metadata rows for all local threads sorted by update time descending."""
        if not self.root.exists():
            return []
        rows = await asyncio.to_thread(self._list_threads_sync)
        return sorted(rows, key=lambda row: row.get("updated_at") or "", reverse=True)

    def _live_thread(self, state: ThreadState) -> LiveThread:
        """Create a live thread wrapper for an already built state object."""
        thread_dir = self.root / state.thread_id
        return LiveThread(state=state, thread_dir=thread_dir, recorder=RolloutRecorder(thread_dir / "rollout.jsonl"))

    def _read_metadata(self, thread_id: str) -> dict[str, Any]:
        """Read one thread metadata file from disk."""
        path = self.root / thread_id / "metadata.json"
        if not path.exists():
            raise FileNotFoundError(f"Thread not found: {thread_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _state_from_metadata(self, metadata: dict[str, Any]) -> ThreadState:
        """Rebuild ThreadState from compact metadata."""
        return ThreadState(
            thread_id=str(metadata["thread_id"]),
            session_id=str(metadata.get("session_id") or metadata["thread_id"]),
            workspace_root=Path(str(metadata["workspace_root"])),
            status=ThreadStatus(str(metadata.get("status") or ThreadStatus.IDLE.value)),
            project_root=Path(str(metadata.get("project_root") or metadata["workspace_root"])),
            cwd=Path(str(metadata.get("cwd") or metadata["workspace_root"])),
            parent_thread_id=metadata.get("parent_thread_id"),
            forked_from_id=metadata.get("forked_from_id"),
            active_turn_id=metadata.get("last_turn_id"),
            child_thread_ids=list(metadata.get("child_thread_ids") or []),
            created_at=str(metadata.get("created_at") or iso_now()),
            updated_at=str(metadata.get("updated_at") or iso_now()),
            turn_count=int(metadata.get("turn_count") or 0),
            metadata={
                "name": metadata.get("name"),
                "preview": metadata.get("preview"),
                "model_provider": metadata.get("model_provider"),
                "model": metadata.get("model"),
                "archived": bool(metadata.get("archived", False)),
            },
        )

    def _load_history_sync(self, thread_id: str) -> list[RolloutItem]:
        """Load rollout items from JSONL and ignore corrupted rows."""
        path = self.root / thread_id / "rollout.jsonl"
        if not path.exists():
            return []
        items: list[RolloutItem] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                items.append(item_from_dict(json.loads(line)))
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                continue
        return items

    def _list_threads_sync(self) -> list[dict[str, Any]]:
        """Read every metadata.json row under the thread root."""
        rows: list[dict[str, Any]] = []
        for child in self.root.iterdir():
            path = child / "metadata.json"
            if not path.exists():
                continue
            try:
                rows.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
        return rows


def _active_turn_from_history(history: list[RolloutItem]) -> str | None:
    """Return the last started turn that lacks a terminal item."""
    active: str | None = None
    for item in history:
        if item.item_type == "turn.started":
            active = item.turn_id
        if item.item_type in {"turn.completed", "turn.failed", "turn.cancelled", "turn.interrupted"} and item.turn_id == active:
            active = None
    return active
