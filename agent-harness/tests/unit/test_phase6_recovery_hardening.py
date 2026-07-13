from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from agent_harness.checkpoints.manager import CheckpointManager
from agent_harness.checkpoints.models import DurableTurnStatus, ResumePoint
from agent_harness.checkpoints.store import CheckpointStore
from agent_harness.checkpoints.serializer import restore_run_state, serialize_run_state
from agent_harness.compaction.service import CompactionService
from agent_harness.config import HarnessConfig
from agent_harness.domain.errors import RunError
from agent_harness.domain.messages import CanonicalMessage, ToolCall
from agent_harness.domain.run import RunState, RunStatus
from agent_harness.memory.store import MemoryStore
from agent_harness.persistence.database import SQLiteDatabase
from agent_harness.providers.fake import FakeModelProvider, ScriptedStep
from agent_harness.rollout.integrity import RolloutIntegrityError, load_verified
from agent_harness.rollout.items import RolloutItem
from agent_harness.runtime.run_manager import RunManager
from agent_harness.runtime.session import ConversationSession
from agent_harness.threads.recorder import RolloutRecorder
from agent_harness.turns.state import ThreadStatus
from agent_harness.utils.serialization import to_jsonable
from agent_harness.utils.time import utc_now


pytestmark = pytest.mark.unit


def test_restore_run_state_preserves_budget_and_terminal_fields(tmp_path: Path) -> None:
    """Restore timestamps, identity, structured errors, and agent summary exactly."""
    state = RunState("任务", tmp_path, agent_name="reviewer")
    state.started_at = utc_now() - timedelta(minutes=10)
    state.updated_at = utc_now() - timedelta(minutes=5)
    state.completed_at = utc_now()
    state.error = RunError("E", "错误", "runtime", True, {"key": "value"}, "ValueError")
    state.agent_summary = {"completed": 2}
    restored = restore_run_state(serialize_run_state(state), tmp_path)
    assert restored.agent_name == state.agent_name
    assert restored.started_at == state.started_at
    assert restored.updated_at == state.updated_at
    assert restored.completed_at == state.completed_at
    assert restored.error == state.error
    assert restored.agent_summary == state.agent_summary


@pytest.mark.asyncio
async def test_after_tool_resume_finishes_remaining_calls_before_model(tmp_path: Path) -> None:
    """Reuse one committed multi-tool response and execute only its unfinished call."""
    (tmp_path / "a.txt").write_text("内容", encoding="utf-8")
    first = ToolCall("first", "list_files", {"path": "."})
    second = ToolCall("second", "read_file", {"path": "a.txt"})
    state = RunState("查看文件", tmp_path)
    state.turn_id = "turn_0001"
    state.turn_count = 1
    state.status = RunStatus.RUNNING
    state.iteration = 1
    state.messages = [
        CanonicalMessage(role="user", content="查看文件"),
        CanonicalMessage(role="assistant", tool_calls=[first, second]),
        CanonicalMessage(role="tool", content="已完成", tool_call_id="first", tool_name="list_files"),
    ]
    provider = FakeModelProvider([ScriptedStep(content="全部完成")])
    manager = RunManager(HarnessConfig(), provider)
    manager.resume_point = ResumePoint.AFTER_TOOL
    result = await manager.run_existing(state, tmp_path / "traces")
    assert result.status == RunStatus.COMPLETED
    assert any(message.tool_call_id == "second" for message in result.messages)
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_before_finalize_resume_never_calls_provider_or_tool(tmp_path: Path) -> None:
    """Finalize a decided answer directly from its durable boundary."""
    state = RunState("任务", tmp_path)
    state.turn_id = "turn_0001"
    state.turn_count = 1
    state.status = RunStatus.RUNNING
    state.final_output = "已决定的结果"
    provider = FakeModelProvider([])
    manager = RunManager(HarnessConfig(), provider)
    manager.resume_point = ResumePoint.BEFORE_FINALIZE
    result = await manager.run_existing(state, tmp_path / "traces")
    assert result.status == RunStatus.COMPLETED
    assert result.final_output == "已决定的结果"
    assert provider.calls == 0
    assert result.tool_call_count == 0


@pytest.mark.asyncio
async def test_terminal_checkpoint_repairs_missing_rollout_terminal(tmp_path: Path) -> None:
    """Project one missing terminal item and idle metadata from a terminal checkpoint."""
    config = HarnessConfig()
    config.provider.name = "fake"
    config.trace.thread_directory = tmp_path / "threads"
    config.trace.directory = tmp_path / "traces"
    config.persistence.runtime_db = Path("runtime.sqlite3")
    original = ConversationSession(config, RunManager(config, FakeModelProvider([])), tmp_path)
    await original.start()
    assert original.live_thread is not None
    assert original.state is not None
    original.state.turn_id = "turn_0001"
    original.state.turn_count = 1
    original.state.status = RunStatus.COMPLETED
    original.state.final_output = "崩溃前已决定"
    original.live_thread.state.turn_count = 1
    original.live_thread.state.active_turn_id = "turn_0001"
    original.live_thread.state.status = ThreadStatus.ACTIVE
    await original.live_thread.update_metadata({"turn_count": 1, "active_turn_id": "turn_0001", "status": ThreadStatus.ACTIVE})
    await original.live_thread.append_items(
        [RolloutItem.create("turn.started", session_id=original.session_id, thread_id=original.session_id, turn_id="turn_0001")]
    )
    await original.live_thread.flush()
    store = CheckpointStore(tmp_path / "runtime.sqlite3")
    CheckpointManager(store, original.session_id, "fake", "fake").save(
        original.state, ResumePoint.TERMINAL, DurableTurnStatus.COMPLETED
    )
    thread_id = original.session_id
    await original.live_thread.shutdown()
    await original.manager.close_mcp()

    resumed = ConversationSession(config, RunManager(config, FakeModelProvider([])), tmp_path)
    await resumed.resume(thread_id)
    history = await resumed.store.load_history(thread_id)
    metadata = json.loads((resumed.thread_dir / "metadata.json").read_text(encoding="utf-8"))
    assert sum(item.item_type == "turn.completed" for item in history) == 1
    assert metadata["status"] == ThreadStatus.IDLE.value
    assert metadata["last_turn_id"] is None
    await resumed.close()


def test_compaction_is_verified_and_reapplied_after_restart(tmp_path: Path) -> None:
    """Load a durable summary and reconstruct the same compact model-visible state."""
    database = SQLiteDatabase(tmp_path / "runtime.sqlite3")
    service = CompactionService(database, retain_recent_turns=1, max_summary_chars=1000)
    original = RunState("第二问", tmp_path)
    original.run_id = "thread"
    original.turn_id = "turn_0002"
    original.status = RunStatus.COMPLETED
    original.messages = [
        CanonicalMessage(role="user", content="第一问"),
        CanonicalMessage(role="assistant", content="第一答"),
        CanonicalMessage(role="user", content="第二问"),
        CanonicalMessage(role="assistant", content="第二答"),
    ]
    checkpoint_payload = to_jsonable(original.messages)
    record = service.compact(original)
    assert record is not None
    restarted = RunState("第二问", tmp_path)
    restarted.run_id = "thread"
    restarted.turn_id = "turn_0002"
    restarted.messages = [restore_run_state({"run_id": "thread", "messages": checkpoint_payload}, tmp_path).messages][0]
    applied = service.apply_latest(restarted)
    assert applied == record
    assert restarted.session_summary == record.summary_text
    assert [message.content for message in restarted.messages] == ["第二问", "第二答"]


def test_memory_delete_removes_all_plaintext_and_sources(tmp_path: Path) -> None:
    """Leave only a hash tombstone after a user deletes durable memory."""
    store = MemoryStore(tmp_path / "memory.sqlite3")
    record = store.create_explicit("需要彻底消失的内容", project_identity="project", thread_id="thread", tags=("敏感标签",))
    assert store.delete(record.memory_id)
    db = store.database.connect()
    try:
        row = db.execute("SELECT content,payload_json FROM memory_records WHERE memory_id=?", (record.memory_id,)).fetchone()
        sources = db.execute("SELECT COUNT(*) FROM memory_sources WHERE memory_id=?", (record.memory_id,)).fetchone()[0]
    finally:
        db.close()
    serialized = json.dumps(json.loads(row[1]), ensure_ascii=False)
    assert row[0] == "[DELETED]"
    assert "需要彻底消失的内容" not in serialized
    assert "敏感标签" not in serialized
    assert sources == 0


@pytest.mark.asyncio
async def test_v1_item_after_v2_chain_fails_closed(tmp_path: Path) -> None:
    """Reject a legacy item injected after authenticated rollout history begins."""
    path = tmp_path / "rollout.jsonl"
    recorder = RolloutRecorder(path)
    await recorder.record([RolloutItem.create("thread.created", session_id="thread", thread_id="thread")])
    await recorder.flush()
    await recorder.shutdown()
    legacy = to_jsonable(replace(RolloutItem.create("legacy", session_id="thread", thread_id="thread"), schema_version=1))
    path.write_text(path.read_text(encoding="utf-8") + json.dumps(legacy) + "\n", encoding="utf-8")
    with pytest.raises(RolloutIntegrityError, match="Legacy"):
        load_verified(path)
