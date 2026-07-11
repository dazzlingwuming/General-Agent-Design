from __future__ import annotations

import json
from pathlib import Path

from agent_harness.config import HarnessConfig
from agent_harness.providers.fake import FakeModelProvider, ScriptedStep
from agent_harness.runtime.run_manager import RunManager
from agent_harness.runtime.session import ConversationSession


async def test_conversation_session_reuses_one_trace_across_turns(tmp_path: Path):
    """Verify that a multi-turn session does not create one top-level run per prompt."""
    config = HarnessConfig()
    config.provider.name = "fake"
    config.trace.session_directory = tmp_path / "sessions"
    provider = FakeModelProvider([ScriptedStep(content="第一轮回答"), ScriptedStep(content="第二轮回答")])
    manager = RunManager(config=config, provider=provider)
    session = ConversationSession(config=config, manager=manager, workspace=tmp_path)
    session.start()

    first = await session.run_turn("第一轮问题")
    second = await session.run_turn("第二轮问题")
    session.close()

    session_dirs = list(config.trace.session_directory.iterdir())
    events = [
        json.loads(line)
        for line in (session.session_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    sequences = [event["sequence_number"] for event in events]

    assert first.run_id == second.run_id == session.session_id
    assert len(session_dirs) == 1
    assert (session.session_dir / "turns" / "turn_0001-result.json").exists()
    assert (session.session_dir / "turns" / "turn_0002-result.json").exists()
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
