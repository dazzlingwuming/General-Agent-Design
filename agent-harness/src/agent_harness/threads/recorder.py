from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from agent_harness.rollout.items import RolloutItem
from agent_harness.utils.serialization import to_jsonable
from agent_harness.rollout.integrity import hash_item, load_verified


@dataclass(slots=True)
class _FlushCommand:
    """Queue command that acknowledges when all previous items are durable."""

    ack: asyncio.Future[None]


@dataclass(slots=True)
class _ShutdownCommand:
    """Queue command that drains writes and terminates the recorder task."""

    ack: asyncio.Future[None]


class RecorderState(str, Enum):
    """Lifecycle states for the single-writer rollout recorder."""

    OPEN = "OPEN"
    FAILED = "FAILED"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"


class RolloutRecorder:
    """Single-writer append-only JSONL recorder for one thread rollout."""

    def __init__(self, rollout_path: Path, *, on_failure: Callable[[BaseException], None] | None = None) -> None:
        """Create a recorder for the supplied rollout file path."""
        self.rollout_path = rollout_path
        self._queue: asyncio.Queue[list[RolloutItem] | _FlushCommand | _ShutdownCommand] = asyncio.Queue()
        self._writer_task: asyncio.Task[None] | None = None
        self._state = RecorderState.OPEN
        self._failure: BaseException | None = None
        self._on_failure = on_failure
        existing = load_verified(rollout_path) if rollout_path.exists() else []
        self._sequence = max((item.sequence_number for item in existing), default=0)
        self._previous_hash = next((item.item_hash for item in reversed(existing) if item.item_hash), "")

    def start(self) -> None:
        """Start the background writer task if it is not already running."""
        self._raise_if_unavailable()
        if self._writer_task is None:
            self._writer_task = asyncio.create_task(self._writer(), name=f"rollout-recorder:{self.rollout_path}")

    async def record(self, items: list[RolloutItem]) -> None:
        """Queue rollout items for append-only persistence."""
        self._raise_if_unavailable()
        if not items:
            return
        self.start()
        await self._queue.put(items)

    def record_nowait(self, items: list[RolloutItem]) -> None:
        """Queue items from a synchronous audit callback running on the event loop."""
        self._raise_if_unavailable()
        if not items:
            return
        self.start()
        self._queue.put_nowait(items)

    async def flush(self) -> None:
        """Wait until all items queued before this call have been written."""
        self._raise_if_unavailable()
        self.start()
        loop = asyncio.get_running_loop()
        ack: asyncio.Future[None] = loop.create_future()
        await self._queue.put(_FlushCommand(ack))
        await ack

    async def shutdown(self) -> None:
        """Drain pending writes, stop the writer task, and surface writer failures."""
        self._raise_if_failed()
        if self._state == RecorderState.CLOSED:
            return
        if self._writer_task is None:
            self._state = RecorderState.CLOSED
            return
        self._state = RecorderState.CLOSING
        loop = asyncio.get_running_loop()
        ack: asyncio.Future[None] = loop.create_future()
        await self._queue.put(_ShutdownCommand(ack))
        try:
            await asyncio.wait_for(asyncio.shield(ack), timeout=5.0)
            await asyncio.wait_for(asyncio.shield(self._writer_task), timeout=5.0)
        except BaseException:
            self._raise_if_failed()
            raise
        self._state = RecorderState.CLOSED

    async def _writer(self) -> None:
        """Consume recorder commands sequentially and write JSONL lines."""
        while True:
            command = await self._queue.get()
            try:
                if isinstance(command, list):
                    await asyncio.to_thread(self._append_items, command)
                elif isinstance(command, _FlushCommand):
                    command.ack.set_result(None)
                elif isinstance(command, _ShutdownCommand):
                    command.ack.set_result(None)
                    return
            except Exception as exc:
                self._set_failure(exc)
                return
            finally:
                self._queue.task_done()

    def _set_failure(self, exc: BaseException) -> None:
        """Store the first writer failure and fail every queued acknowledgement."""
        if self._failure is not None:
            return
        self._failure = exc
        self._state = RecorderState.FAILED
        while True:
            try:
                command = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if isinstance(command, (_FlushCommand, _ShutdownCommand)) and not command.ack.done():
                command.ack.set_exception(exc)
            self._queue.task_done()
        if self._on_failure is not None:
            self._on_failure(exc)

    def _raise_if_failed(self) -> None:
        """Raise the original sticky writer failure when persistence is unavailable."""
        if self._failure is not None:
            raise self._failure

    def _raise_if_unavailable(self) -> None:
        """Reject writes after sticky failure or recorder closure."""
        self._raise_if_failed()
        if self._state != RecorderState.OPEN:
            raise RuntimeError(f"Rollout recorder is not open: {self._state.value}")

    def _append_items(self, items: list[RolloutItem]) -> None:
        """Append serialized rollout items to disk in queue order."""
        self.rollout_path.parent.mkdir(parents=True, exist_ok=True)
        with self.rollout_path.open("a", encoding="utf-8") as handle:
            for item in items:
                self._sequence += 1
                durable = replace(item, schema_version=2, sequence_number=self._sequence, previous_hash=self._previous_hash, item_hash="")
                durable = replace(durable, item_hash=hash_item(durable))
                handle.write(json.dumps(to_jsonable(durable), ensure_ascii=False, sort_keys=True) + "\n")
                self._previous_hash = durable.item_hash
            handle.flush()
            os.fsync(handle.fileno())
