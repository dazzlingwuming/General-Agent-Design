from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path

from agent_harness.config import HarnessConfig
from agent_harness.domain.model import Usage
from agent_harness.providers.fake import FakeModelProvider, ScriptedStep
from agent_harness.runtime.run_manager import RunManager
from agent_harness.runtime.session import ConversationSession
from agent_harness.utils.time import utc_now


async def test_new_turn_resets_turn_owned_state_and_preserves_history(tmp_path: Path) -> None:
    """Reset timestamps, usage, cancellation, and summary without losing prior messages."""
    config = HarnessConfig()
    config.provider.name = "fake"
    config.trace.thread_directory = tmp_path / "threads"
    session = ConversationSession(config, RunManager(config, FakeModelProvider([ScriptedStep(content="一"), ScriptedStep(content="二")])), tmp_path)
    await session.start()
    first = await session.run_turn("第一轮")
    first_started = first.started_at
    first.usage_total = Usage(input_tokens=99, total_tokens=99)
    first.cancellation_requested = True
    first.agent_summary = {"stale": True}
    first.started_at = utc_now() - timedelta(hours=1)
    second = await session.run_turn("第二轮")
    assert second.started_at > first_started
    assert second.completed_at is not None and second.completed_at >= second.started_at
    assert second.usage_total == Usage()
    assert second.cancellation_requested is False
    assert second.agent_summary is not None and "stale" not in second.agent_summary
    assert [message.content for message in second.messages if message.role == "user"] == ["第一轮", "第二轮"]
    result = json.loads((session.thread_dir / "turns" / "turn_0002-result.json").read_text(encoding="utf-8"))
    assert result["duration_ms"] < 60_000
    await session.close()
