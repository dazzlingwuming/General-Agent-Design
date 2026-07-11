from __future__ import annotations

import asyncio

from agent_harness.turns.state import InputItem


class TurnInputMailbox:
    """Async mailbox for steering input appended to an active turn."""

    def __init__(self) -> None:
        """Initialize an empty input queue for one active turn."""
        self._queue: asyncio.Queue[InputItem] = asyncio.Queue()

    async def put(self, input_item: InputItem) -> None:
        """Append one steering input item to the mailbox."""
        await self._queue.put(input_item)

    async def drain(self) -> list[InputItem]:
        """Return and remove all currently queued steering input items."""
        items: list[InputItem] = []
        while not self._queue.empty():
            items.append(self._queue.get_nowait())
            self._queue.task_done()
        return items

    def has_pending(self) -> bool:
        """Return whether the turn has unconsumed steering input."""
        return not self._queue.empty()
