from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path

import pytest

from agent_harness.cli_observability import redact, render_event
from agent_harness.cli_observability import CliObservability
from agent_harness.config import HarnessConfig
from agent_harness.domain.model import Usage
from agent_harness.tracing.bus import RuntimeEventBus
from agent_harness.tracing.events import TraceEvent
from agent_harness.tracing.jsonl import JsonlTraceSink
from agent_harness.tracing.reducer import RuntimePhase, TraceReducer
from agent_harness.tracing.usage import PricingSnapshot, UsageReducer, read_trace
from agent_harness.providers.fake import FakeModelProvider, ScriptedStep
from agent_harness.runtime.run_manager import RunManager
from agent_harness.runtime.session import ConversationSession


def _event(sequence: int, event_type: str, turn_id: str = "turn_0001", **payload) -> TraceEvent:
    """Create one deterministic test event with normalized thread identity."""
    return TraceEvent(event_type, "thread_test", sequence, thread_id="thread_test", turn_id=turn_id, payload=payload)


def test_usage_reducer_separates_last_turn_thread_and_reuse() -> None:
    """Keep the three token scopes separate and ignore recovered response reuse."""
    reducer = UsageReducer()
    reducer.apply(_event(1, "turn.started"))
    reducer.apply(_event(2, "model.response.completed", provider="deepseek", model="v4", usage={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}))
    reducer.apply(_event(3, "model.response.reused", provider="deepseek", model="v4", usage={"input_tokens": 10, "total_tokens": 12}))
    reducer.apply(_event(4, "turn.started", "turn_0002"))
    reducer.apply(_event(5, "model.response.completed", "turn_0002", provider="deepseek", model="v4", usage={"input_tokens": 20, "output_tokens": 3, "total_tokens": 23}))
    snapshot = reducer.snapshot()
    assert snapshot.last_call.total_tokens == 23
    assert snapshot.current_turn.total_tokens == 23
    assert snapshot.current_thread.total_tokens == 35
    assert len(reducer.records) == 2


def test_pricing_uses_cache_split_and_does_not_double_reasoning() -> None:
    """Calculate Decimal cost from cache split while treating reasoning as output detail."""
    pricing = PricingSnapshot("p1", "deepseek", "v4", "CNY", 1000, "2026-01-01", "https://example.test",
        cache_hit_input_per_unit=Decimal("1"), cache_miss_input_per_unit=Decimal("2"), output_per_unit=Decimal("3"))
    usage = Usage(input_tokens=100, cached_input_tokens=60, cache_miss_input_tokens=40, output_tokens=10, reasoning_tokens=5, total_tokens=110)
    assert pricing.estimate(usage) == Decimal("0.17")


def test_jsonl_sink_publishes_same_event_and_replays_strictly(tmp_path: Path) -> None:
    """Persist and publish one immutable event, then reject reordered trace input."""
    received = []
    bus = RuntimeEventBus()
    bus.subscribe(received.append)
    sink = JsonlTraceSink("thread_test", tmp_path, event_bus=bus)
    event_id = sink.emit("turn.started", turn_id="turn_0001")
    sink.close()
    events = read_trace(tmp_path / "thread_test" / "events.jsonl")
    assert events[0].event_id == event_id == received[0].event_id
    assert events[0].schema_version == 2
    path = tmp_path / "bad.jsonl"
    path.write_text("\n".join([json.dumps(_event(2, "turn.started").to_dict()), json.dumps(_event(1, "turn.completed").to_dict())]), encoding="utf-8")
    with pytest.raises(ValueError, match="strictly increasing"):
        read_trace(path)


def test_trace_reducer_and_renderer_hide_secrets_and_internal_events() -> None:
    """Derive phase from typed events and redact credentials from terminal cells."""
    reducer = TraceReducer()
    event = _event(1, "tool.execution.started", tool_name="run_command", arguments={"api_key": "secret"})
    reducer.apply(event)
    assert reducer.state.phase == RuntimePhase.RUNNING_TOOL
    assert "secret" not in (render_event(event, 8, True) or "")
    assert redact({"Authorization": "Bearer secret"}) == {"Authorization": "[REDACTED]"}
    assert render_event(_event(2, "checkpoint.saved"), 8, True) is None


async def test_status_usage_and_trace_replay_snapshot(tmp_path: Path) -> None:
    """Render stable command views from persisted trace rather than runtime internals."""
    config = HarnessConfig()
    config.provider.name = "fake"
    config.provider.model = "fake-model"
    config.trace.thread_directory = tmp_path / "threads"
    session = ConversationSession(config, RunManager(config, FakeModelProvider([ScriptedStep(content="完成")])) , tmp_path)
    await session.start()
    await session.run_turn("测试可观测性")
    view = CliObservability(config, session)
    view.replay()
    status = view.status()
    usage = view.usage()
    trace = view.trace()
    assert "Agent Harness Status" in status
    assert "This turn" in status and "Thread" in status
    assert "Token Usage" in usage
    assert "Calling fake-model" in trace
    assert "reasoning_content" not in trace
    await session.close()
