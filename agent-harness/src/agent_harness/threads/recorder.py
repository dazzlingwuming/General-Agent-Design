from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from agent_harness.rollout.items import RolloutItem
from agent_harness.utils.serialization import to_jsonable


@dataclass(slots=True)
class _FlushCommand:
    """Queue command that acknowledges when all previous items are durable."""

    ack: asyncio.Future[None]


@dataclass(slots=True)
class _ShutdownCommand:
    """Queue command that drains writes and terminates the recorder task."""

    ack: asyncio.Future[None]


class RolloutRecorder:
    """Single-writer append-only JSONL recorder for one thread rollout."""

    def __init__(self, rollout_path: Path) -> None:
        """Create a recorder for the supplied rollout file path."""
        self.rollout_path = rollout_path
        self._queue: asyncio.Queue[list[RolloutItem] | _FlushCommand | _ShutdownCommand] = asyncio.Queue()
        self._writer_task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start the background writer task if it is not already running."""
        if self._writer_task is None or self._writer_task.done():
            self._writer_task = asyncio.create_task(self._writer(), name=f"rollout-recorder:{self.rollout_path}")

    async def record(self, items: list[RolloutItem]) -> None:
        """Queue rollout items for append-only persistence."""
        if not items:
            return
        self.start()
        await self._queue.put(items)

    def record_nowait(self, items: list[RolloutItem]) -> None:
        """Queue items from a synchronous audit callback running on the event loop."""
        if not items:
            return
        self.start()
        self._queue.put_nowait(items)

    async def flush(self) -> None:
        """Wait until all items queued before this call have been written."""
        self.start()
        loop = asyncio.get_running_loop()
        ack: asyncio.Future[None] = loop.create_future()
        await self._queue.put(_FlushCommand(ack))
        await ack

    async def shutdown(self) -> None:
        """Drain pending writes, stop the writer task, and surface writer failures."""
        if self._writer_task is None:
            return
        loop = asyncio.get_running_loop()
        ack: asyncio.Future[None] = loop.create_future()
        await self._queue.put(_ShutdownCommand(ack))
        await ack
        await self._writer_task

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
                if isinstance(command, (_FlushCommand, _ShutdownCommand)) and not command.ack.done():
                    command.ack.set_exception(exc)
                raise
            finally:
                self._queue.task_done()

    def _append_items(self, items: list[RolloutItem]) -> None:
        """Append serialized rollout items to disk in queue order."""
        self.rollout_path.parent.mkdir(parents=True, exist_ok=True)
        with self.rollout_path.open("a", encoding="utf-8") as handle:
            for item in items:
                handle.write(json.dumps(to_jsonable(item), ensure_ascii=False) + "\n")
