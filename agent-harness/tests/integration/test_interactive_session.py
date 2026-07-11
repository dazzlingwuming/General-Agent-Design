from __future__ import annotations

import json
from pathlib import Path

from agent_harness.config import HarnessConfig
from agent_harness.providers.fake import FakeModelProvider, ScriptedStep
from agent_harness.runtime.run_manager import RunManager
from agent_harness.runtime.session import ConversationSession


async def test_conversation_session_appends_rollout_items_across_turns(tmp_path: Path):
    """Verify that a multi-turn conversation persists one append-only thread rollout."""
    config = HarnessConfig()
    config.provider.name = "fake"
    config.trace.thread_directory = tmp_path / "threads"
    provider = FakeModelProvider([ScriptedStep(content="第一轮回答"), ScriptedStep(content="第二轮回答")])
    manager = RunManager(config=config, provider=provider)
    session = ConversationSession(config=config, manager=manager, workspace=tmp_path)
    await session.start()

    first = await session.run_turn("第一轮问题")
    first_rollout_size = session.rollout_path.stat().st_size
    second = await session.run_turn("第二轮问题")
    await session.close()

    thread_dirs = list(config.trace.thread_directory.iterdir())
    rollout = [
        json.loads(line)
        for line in session.rollout_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    item_types = [item["item_type"] for item in rollout]
    metadata = json.loads((session.thread_dir / "metadata.json").read_text(encoding="utf-8"))
    events = [
        json.loads(line)
        for line in (session.thread_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    sequences = [event["sequence_number"] for event in events]

    assert first.run_id == second.run_id == session.session_id
    assert len(thread_dirs) == 1
    assert session.rollout_path.stat().st_size > first_rollout_size
    assert item_types.count("thread.created") == 1
    assert item_types.count("turn.started") == 2
    assert item_types.count("user_message") == 2
    assert item_types.count("agent_message") == 2
    assert item_types.count("turn.completed") == 2
    assert metadata["thread_id"] == session.session_id
    assert metadata["session_id"] == session.session_id
    assert metadata["turn_count"] == 2
    assert (session.thread_dir / "turns" / "turn_0001-result.json").exists()
    assert (session.thread_dir / "turns" / "turn_0002-result.json").exists()
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))


async def test_resume_thread_reuses_rollout_without_creating_turn(tmp_path: Path):
    """Verify that resuming a thread loads history and does not append a new turn."""
    config = HarnessConfig()
    config.provider.name = "fake"
    config.trace.thread_directory = tmp_path / "threads"
    provider = FakeModelProvider([ScriptedStep(content="第一轮回答"), ScriptedStep(content="第二轮回答")])
    manager = RunManager(config=config, provider=provider)
    session = ConversationSession(config=config, manager=manager, workspace=tmp_path)
    await session.start()
    await session.run_turn("第一轮问题")
    thread_id = session.session_id
    rollout_before_resume = session.rollout_path.read_text(encoding="utf-8")
    await session.close()

    resumed = ConversationSession(config=config, manager=manager, workspace=tmp_path)
    await resumed.resume(thread_id)
    rollout_after_resume = resumed.rollout_path.read_text(encoding="utf-8")
    assert rollout_after_resume == rollout_before_resume
    assert resumed.state is not None
    assert resumed.state.turn_count == 1
    assert [message.role for message in resumed.state.messages] == ["user", "assistant"]

    await resumed.run_turn("第二轮问题")
    await resumed.close()
    rollout = [
        json.loads(line)
        for line in resumed.rollout_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [item["payload"].get("text") for item in rollout if item["item_type"] == "user_message"] == ["第一轮问题", "第二轮问题"]


async def test_resume_skips_corrupted_rollout_line(tmp_path: Path):
    """Verify that a malformed JSONL row does not block thread resume."""
    config = HarnessConfig()
    config.provider.name = "fake"
    config.trace.thread_directory = tmp_path / "threads"
    provider = FakeModelProvider([ScriptedStep(content="回答")])
    manager = RunManager(config=config, provider=provider)
    session = ConversationSession(config=config, manager=manager, workspace=tmp_path)
    await session.start()
    await session.run_turn("问题")
    thread_id = session.session_id
    with session.rollout_path.open("a", encoding="utf-8") as handle:
        handle.write("{broken json\n")
    await session.close()

    resumed = ConversationSession(config=config, manager=manager, workspace=tmp_path)
    await resumed.resume(thread_id)

    assert resumed.state is not None
    assert resumed.state.turn_count == 1
    assert len(resumed.state.messages) == 2
    await resumed.close()
