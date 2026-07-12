from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_harness.rollout.items import RolloutItem
from agent_harness.threads.recorder import RolloutRecorder


class FailingRecorder(RolloutRecorder):
    """Inject a deterministic disk failure into the recorder writer."""

    def _append_items(self, items: list[RolloutItem]) -> None:
        """Fail every append with the same test-visible disk error."""
        raise OSError("injected rollout failure")


async def test_rollout_failure_is_sticky_and_shutdown_is_bounded(tmp_path: Path):
    """Surface one writer failure to all operations without restarting or hanging."""
    failures: list[BaseException] = []
    recorder = FailingRecorder(tmp_path / "rollout.jsonl", on_failure=failures.append)
    item = RolloutItem.create("test.item", session_id="thread", thread_id="thread")
    await recorder.record([item])
    with pytest.raises(OSError, match="injected rollout failure") as first:
        await recorder.flush()
    writer_task = recorder._writer_task
    with pytest.raises(OSError) as second:
        await recorder.record([item])
    with pytest.raises(OSError) as third:
        await asyncio.wait_for(recorder.shutdown(), timeout=0.5)
    assert first.value is second.value is third.value
    assert failures == [first.value]
    assert recorder._writer_task is writer_task
