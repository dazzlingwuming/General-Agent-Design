from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from agent_harness.checkpoints.models import CheckpointEnvelope, DurableTurnStatus, ResumePoint
from agent_harness.checkpoints.store import CheckpointStore
from agent_harness.domain.tools import ToolEffectClass, ToolRecoveryPolicy
from agent_harness.memory.models import MemoryRecord, MemoryScope, MemorySourceKind, VerificationStatus
from agent_harness.memory.store import MemoryStore, project_identity, render_memories
from agent_harness.recovery.coordinator import RecoveryCoordinator, RecoveryDisposition
from agent_harness.rollout.integrity import RolloutIntegrityError, load_verified
from agent_harness.rollout.items import RolloutItem
from agent_harness.threads.recorder import RolloutRecorder
from agent_harness.tools.builtins.factory import create_default_registry
from agent_harness.utils.time import iso_now


pytestmark = pytest.mark.unit


def _checkpoint() -> CheckpointEnvelope:
    """Build one minimal valid checkpoint fixture."""
    return CheckpointEnvelope(1, "cp1", "thread1", "thread1", "turn1", "root", 1, 0, ResumePoint.AFTER_MODEL,
        DurableTurnStatus.RUNNING, "0.1.0", "cfg", "fake", "fake", {"messages": []}, created_at=iso_now())


def test_checkpoint_round_trip_and_integrity(tmp_path: Path) -> None:
    """Persist, reload, and reject a checkpoint whose payload hash was modified."""
    store = CheckpointStore(tmp_path / "runtime.sqlite3")
    saved = store.save(_checkpoint())
    assert store.latest("thread1") == saved
    with store.database.transaction() as db:
        payload = json.loads(db.execute("SELECT payload_json FROM checkpoints").fetchone()[0])
        payload["model_name"] = "tampered"
        db.execute("UPDATE checkpoints SET payload_json=?", (json.dumps(payload),))
    with pytest.raises(ValueError, match="integrity"):
        store.latest("thread1")


def test_recovery_matrix_blocks_unknown_side_effect() -> None:
    """Never auto-replay an in-flight command or other non-idempotent write."""
    checkpoint = replace(_checkpoint(), resume_point=ResumePoint.TOOL_IN_FLIGHT).with_hash()
    plan = RecoveryCoordinator().plan(checkpoint, ToolRecoveryPolicy.NEVER_RETRY)
    assert plan.disposition == RecoveryDisposition.MANUAL
    assert plan.resume_point == ResumePoint.RECOVERY_REQUIRED


def test_builtin_tools_declare_recovery_policy(tmp_path: Path) -> None:
    """Require every built-in tool definition to expose deterministic recovery metadata."""
    registry = create_default_registry(tmp_path)
    definitions = {tool.name: tool for tool in registry.list()}
    for definition in definitions.values():
        assert isinstance(definition.effect_class, ToolEffectClass)
        assert isinstance(definition.recovery_policy, ToolRecoveryPolicy)
    assert definitions["write_file"].recovery_policy == ToolRecoveryPolicy.VERIFY_THEN_SYNTHESIZE
    assert definitions["delete_path"].recovery_policy == ToolRecoveryPolicy.MANUAL_RECONCILIATION


def test_memory_is_project_scoped_sourced_and_revocable(tmp_path: Path) -> None:
    """Keep memories isolated by checkout and support retrieval, invalidation, and deletion."""
    store = MemoryStore(tmp_path / "memory.sqlite3")
    first = project_identity(tmp_path / "one")
    second = project_identity(tmp_path / "two")
    record = store.create_explicit("测试命令使用 uv run pytest", project_identity=first, thread_id="thread1")
    assert [item.memory_id for item in store.search("pytest", project_identity=first)] == [record.memory_id]
    assert store.search("pytest", project_identity=second) == []
    assert "authority=\"non_authoritative\"" in render_memories([record], 1000)
    assert store.invalidate(record.memory_id, "changed")
    assert store.search("pytest", project_identity=first) == []
    assert store.delete(record.memory_id)


def test_memory_rejects_secrets_and_unverified_mcp(tmp_path: Path) -> None:
    """Reject secret-like content and direct trust promotion from external MCP content."""
    store = MemoryStore(tmp_path / "memory.sqlite3")
    with pytest.raises(ValueError, match="secret"):
        store.create_explicit("API_KEY=sk-12345678901234567890", project_identity="p", thread_id="t")
    now = iso_now()
    record = MemoryRecord(1, "m1", "p", MemoryScope.PROJECT, "fact", "external", {}, MemorySourceKind.MCP_EXTERNAL,
        VerificationStatus.VERIFIED, 0.9, "external", "t", "turn", ("item",), (), "", "model", now, now, project_identity="p")
    with pytest.raises(ValueError, match="MCP"):
        store.write(record)


@pytest.mark.asyncio
async def test_rollout_hash_chain_and_corruption_policy(tmp_path: Path) -> None:
    """Repair a corrupt tail but reject corruption inside canonical rollout history."""
    path = tmp_path / "rollout.jsonl"
    recorder = RolloutRecorder(path)
    await recorder.record([RolloutItem.create("thread.created", session_id="t", thread_id="t")])
    await recorder.flush()
    await recorder.shutdown()
    assert load_verified(path)[0].sequence_number == 1
    path.write_bytes(path.read_bytes() + b"{partial")
    assert len(load_verified(path)) == 1
    original = path.read_bytes()
    path.write_bytes(b"{broken}\n" + original)
    with pytest.raises(RolloutIntegrityError):
        load_verified(path)
