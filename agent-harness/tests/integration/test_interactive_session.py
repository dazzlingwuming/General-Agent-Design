from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent_harness.config import HarnessConfig
from agent_harness.providers.fake import FakeModelProvider, ScriptedStep
from agent_harness.runtime.run_manager import RunManager
from agent_harness.runtime.session import ConversationSession
from agent_harness.rollout.items import RolloutItem
from agent_harness.threads.local_store import LocalThreadStore
from agent_harness.domain.messages import ToolCall
from agent_harness.domain.model import ModelRequest, ModelResponse
from agent_harness.security.approval import ApprovalDecision


class CancelTurnApprovalHandler:
    """Cancel every approval to exercise session-level control flow."""

    async def request(self, request):
        """Return the explicit cancel-turn decision."""
        return ApprovalDecision.CANCEL_TURN


class CapturingProvider(FakeModelProvider):
    """Retain canonical requests while returning scripted responses."""

    def __init__(self, steps: list[ScriptedStep]) -> None:
        """Initialize scripted behavior and an empty request list."""
        super().__init__(steps)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Capture the request before delegating to the fake provider."""
        self.requests.append(request)
        return await super().complete(request)


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


async def test_resume_incomplete_turn_uses_one_recorder_and_is_idempotent(tmp_path: Path, monkeypatch):
    """Keep an incomplete durable turn resumable without inventing an interrupted terminal item."""
    store = LocalThreadStore(tmp_path / "threads")
    live = await store.create_thread(tmp_path, provider="fake", model="fake-model")
    turn_id = "turn_0001"
    await live.append_items([RolloutItem.create("turn.started", session_id=live.state.session_id, thread_id=live.state.thread_id, turn_id=turn_id)])
    await live.flush()
    thread_id = live.state.thread_id
    await live.shutdown()
    original_factory = store._live_thread
    created = []

    def tracked_factory(state):
        """Record each live handle created by the resume operation."""
        result = original_factory(state)
        created.append(result)
        return result

    monkeypatch.setattr(store, "_live_thread", tracked_factory)
    resumed = await store.resume_thread(thread_id)
    assert created == [resumed]
    await resumed.shutdown()
    resumed_again = await store.resume_thread(thread_id)
    history = await store.load_history(thread_id)
    assert len(created) == 2
    assert sum(item.item_type == "turn.interrupted" for item in history) == 0
    assert sum(item.item_type == "turn.started" for item in history) == 1
    await resumed_again.shutdown()


async def test_cancelled_turn_persists_one_terminal_and_resets_metadata(tmp_path: Path):
    """Persist active turn cancellation before propagating CancelledError to the caller."""
    config = HarnessConfig()
    config.provider.name = "fake"
    config.trace.thread_directory = tmp_path / "threads"
    provider = FakeModelProvider([ScriptedStep(delay_seconds=30, content="不会完成")])
    manager = RunManager(config=config, provider=provider)
    session = ConversationSession(config=config, manager=manager, workspace=tmp_path)
    await session.start()
    turn_task = asyncio.create_task(session.run_turn("取消这一轮"))
    while provider.calls == 0:
        await asyncio.sleep(0)
    turn_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn_task
    assert session.live_thread is not None
    await session.live_thread.flush()
    rollout = [json.loads(line) for line in session.rollout_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    metadata = json.loads((session.thread_dir / "metadata.json").read_text(encoding="utf-8"))
    assert sum(item["item_type"] == "turn.cancelled" for item in rollout) == 1
    assert not any(item["item_type"] == "turn.interrupted" for item in rollout)
    assert metadata["status"] == "IDLE"
    assert metadata["last_turn_id"] is None
    await session.close()


async def test_approval_cancel_turn_stops_model_loop_and_persists_cancellation(tmp_path: Path):
    """Route approval CANCEL_TURN through TurnController instead of a tool error."""
    config = HarnessConfig()
    config.provider.name = "fake"
    config.trace.thread_directory = tmp_path / "threads"
    target = tmp_path / "remove.txt"
    target.write_text("keep", encoding="utf-8")
    provider = FakeModelProvider([ScriptedStep(tool_calls=[ToolCall("delete", "delete_path", {"path": "remove.txt"})])])
    manager = RunManager(config=config, provider=provider, approval_handler=CancelTurnApprovalHandler())
    session = ConversationSession(config=config, manager=manager, workspace=tmp_path)
    await session.start()
    with pytest.raises(asyncio.CancelledError):
        await session.run_turn("删除文件")
    assert target.exists()
    assert provider.calls == 1
    rollout = [json.loads(line) for line in session.rollout_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert sum(item["item_type"] == "turn.cancelled" for item in rollout) == 1
    await session.close()


async def test_user_selected_external_context_is_deduplicated_and_injected_after_input(tmp_path: Path):
    """Inject bounded untrusted MCP content into the next turn with rollout provenance."""
    config = HarnessConfig()
    config.provider.name = "fake"
    config.trace.thread_directory = tmp_path / "custom-threads"
    config.context.max_external_item_bytes = 32
    provider = CapturingProvider([ScriptedStep(content="已读取外部上下文")])
    manager = RunManager(config=config, provider=provider)
    session = ConversationSession(config=config, manager=manager, workspace=tmp_path)
    await session.start()
    payload = {"contents": [{"text": "这是一个超过内联限制的 MCP 资源内容" * 3}]}
    selected = await session.queue_external_context("mcp_resource", "docs", "resource://guide", payload, "text/plain")
    duplicate = await session.queue_external_context("mcp_resource", "docs", "resource://guide", payload, "text/plain")
    assert selected is not None and selected.artifact_id is not None
    assert duplicate is None
    await session.run_turn("请分析选择的资源")
    visible = provider.requests[0].messages
    user_messages = [message for message in visible if message.role == "user"]
    assert user_messages[0].content == "请分析选择的资源"
    assert user_messages[1].metadata["external_context"] is True
    assert 'trust="external_untrusted_user_selected"' in user_messages[1].content
    assert "Artifact artifact_" in user_messages[1].content
    assert manager.artifact_store is not None
    assert manager.artifact_store.root == (session.thread_dir / "artifacts").resolve()
    rollout = [json.loads(line) for line in session.rollout_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert sum(item["item_type"] == "external_context.selected" for item in rollout) == 1
    assert sum(item["item_type"] == "external_context.injected" for item in rollout) == 1
    await session.close()


async def test_pending_external_context_survives_thread_resume(tmp_path: Path):
    """Rebuild a selected but not yet injected MCP item from append-only rollout."""
    config = HarnessConfig()
    config.provider.name = "fake"
    config.trace.thread_directory = tmp_path / "threads"
    manager = RunManager(config=config, provider=FakeModelProvider([]))
    session = ConversationSession(config=config, manager=manager, workspace=tmp_path)
    await session.start()
    await session.queue_external_context("mcp_prompt", "prompts", "review", {"messages": ["恢复后可见"]})
    thread_id = session.session_id
    await session.close()
    provider = CapturingProvider([ScriptedStep(content="已恢复")])
    resumed = ConversationSession(config=config, manager=RunManager(config=config, provider=provider), workspace=tmp_path)
    await resumed.resume(thread_id)
    assert len(resumed.pending_external_context) == 1
    await resumed.run_turn("继续")
    assert "恢复后可见" in provider.requests[0].messages[-1].content
    await resumed.close()
