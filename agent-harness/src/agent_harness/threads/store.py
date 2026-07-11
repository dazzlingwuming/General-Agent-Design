from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agent_harness.rollout.items import RolloutItem
from agent_harness.threads.live_thread import LiveThread


class ThreadStore(Protocol):
    """Persistence interface for thread metadata and canonical rollout history."""

    async def create_thread(self, workspace_root: Path, *, provider: str, model: str) -> LiveThread:
        """Create a new thread and return its live persistence handle."""

    async def resume_thread(self, thread_id: str) -> LiveThread:
        """Load an existing thread and return a live persistence handle."""

    async def append_items(self, thread_id: str, items: list[RolloutItem]) -> None:
        """Append canonical history items to a thread rollout."""

    async def load_history(self, thread_id: str) -> list[RolloutItem]:
        """Load canonical rollout history for a thread."""
