from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent_harness.domain.errors import CancellationError
from agent_harness.domain.run import RunState, RunStatus
from agent_harness.rollout.items import ItemStatus, RolloutItem
from agent_harness.threads.live_thread import LiveThread
from agent_harness.turns.state import ThreadStatus
from agent_harness.utils.serialization import to_jsonable


@dataclass(slots=True)
class TurnController:
    """Own start and exactly-once terminal persistence for one root turn."""

    live_thread: LiveThread
    turn_id: str
    item_factory: Callable[..., RolloutItem]
    summary_writer: Callable[[RunState], None]
    finalization_timeout_seconds: float = 5.0
    _finalized: bool = False
    _finalize_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def start(self, user_text: str, turn_number: int) -> None:
        """Persist the active metadata and initial user items for this turn."""
        self.live_thread.state.active_turn_id = self.turn_id
        self.live_thread.state.status = ThreadStatus.ACTIVE
        await self.live_thread.update_metadata({"status": ThreadStatus.ACTIVE, "active_turn_id": self.turn_id})
        await self.live_thread.append_items(
            [
                self.item_factory("turn.started", self.turn_id, payload={"turn_number": turn_number}),
                self.item_factory("user_message", self.turn_id, payload={"text": user_text, "input_kind": "initial"}),
            ]
        )

    async def complete(self, state: RunState) -> None:
        """Finalize a successful run state as turn.completed."""
        await self._finalize(state, "turn.completed", ItemStatus.COMPLETED)

    async def fail(self, state: RunState) -> None:
        """Finalize an unsuccessful run state as turn.failed."""
        await self._finalize(state, "turn.failed", ItemStatus.FAILED)

    async def cancel(self, state: RunState, reason: str) -> None:
        """Finalize a user or runtime cancellation as turn.cancelled."""
        state.status = RunStatus.CANCELLED
        state.error = CancellationError(reason).to_run_error()
        await self._finalize(state, "turn.cancelled", ItemStatus.CANCELLED)

    async def finalize_recovered(self, state: RunState) -> None:
        """Finalize a recovered terminal state without rewriting its durable outcome."""
        if state.status == RunStatus.COMPLETED:
            await self._finalize(state, "turn.completed", ItemStatus.COMPLETED)
        elif state.status == RunStatus.CANCELLED:
            await self._finalize(state, "turn.cancelled", ItemStatus.CANCELLED)
        else:
            await self._finalize(state, "turn.failed", ItemStatus.FAILED)

    async def interrupt(self, state: RunState, reason: str) -> None:
        """Finalize an explicit runtime interruption distinct from user cancellation."""
        if state.error is None:
            state.error = CancellationError(reason).to_run_error()
        await self._finalize(state, "turn.interrupted", ItemStatus.INTERRUPTED)

    async def cancel_shielded(self, state: RunState, reason: str) -> None:
        """Protect cancellation finalization from caller cancellation with a bounded wait."""
        task = asyncio.create_task(self.cancel(state, reason), name=f"turn-finalize:{self.turn_id}")
        await asyncio.wait_for(asyncio.shield(task), timeout=self.finalization_timeout_seconds)

    async def _finalize(self, state: RunState, terminal_type: str, terminal_status: ItemStatus) -> None:
        """Persist one terminal item, reset thread metadata, flush, and write summaries."""
        async with self._finalize_lock:
            if self._finalized:
                return
            await self.live_thread.append_items(
                [
                    self.item_factory(
                        "agent_message",
                        self.turn_id,
                        payload={"text": state.final_output, "status": state.status.value, "error": to_jsonable(state.error)},
                    ),
                    self.item_factory(
                        terminal_type,
                        self.turn_id,
                        status=terminal_status,
                        payload=self._terminal_payload(state),
                    ),
                ]
            )
            self.live_thread.state.status = ThreadStatus.IDLE
            self.live_thread.state.active_turn_id = None
            await self.live_thread.update_metadata({"status": ThreadStatus.IDLE, "active_turn_id": None})
            await self.live_thread.flush()
            self.summary_writer(state)
            self._finalized = True

    def _terminal_payload(self, state: RunState) -> dict[str, Any]:
        """Build the common terminal payload for every completion outcome."""
        return {
            "final_output": state.final_output,
            "error": to_jsonable(state.error),
            "iteration": state.iteration,
            "model_call_count": state.model_call_count,
            "tool_call_count": state.tool_call_count,
            "usage": to_jsonable(state.usage_total),
        }
