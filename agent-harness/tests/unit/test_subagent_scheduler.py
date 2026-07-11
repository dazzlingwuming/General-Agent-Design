from __future__ import annotations

import asyncio
from pathlib import Path

from agent_harness.agents.registry import create_default_agent_registry
from agent_harness.config import HarnessConfig
from agent_harness.domain.messages import ToolCall
from agent_harness.domain.subagents import DelegationRequest
from agent_harness.providers.fake import FakeModelProvider, ScriptedStep
from agent_harness.runtime.subagents.scheduler import SubagentScheduler
from agent_harness.tracing.jsonl import JsonlTraceSink


async def test_scheduler_spawn_status_and_wait(tmp_path: Path):
    """Verify direct scheduler spawn, status lookup, and wait result handling."""
    provider = FakeModelProvider(
        scripts_by_agent={
            "explorer": [
                ScriptedStep(
                    tool_calls=[
                        ToolCall(
                            id="submit_1",
                            name="submit_result",
                            arguments={
                                "summary": "done",
                                "evidence": [],
                                "unresolved_questions": [],
                                "confidence": 1.0,
                            },
                        )
                    ]
                )
            ]
        }
    )
    config = HarnessConfig()
    trace = JsonlTraceSink("run_test", tmp_path)
    scheduler = SubagentScheduler(
        run_id="run_test",
        workspace_root=tmp_path,
        config=config,
        provider=provider,
        trace=trace,
        agent_registry=create_default_agent_registry("fake-model", "fake", config.run),
    )
    try:
        handle = await scheduler.spawn(DelegationRequest(agent_name="explorer", task="do it"))
        waited = await scheduler.wait([handle["agent_id"]], timeout_seconds=5)
        assert waited["timed_out"] is False
        assert waited["results"][0]["summary"] == "done"
        assert scheduler.status(handle["agent_id"])["status"] == "IDLE"
    finally:
        trace.close()


async def test_scheduler_cancel_child(tmp_path: Path):
    """Verify that cancelling a running child marks it cancelled."""
    provider = FakeModelProvider(
        scripts_by_agent={
            "explorer": [
                ScriptedStep(
                    tool_calls=[
                        ToolCall(
                            id="submit_1",
                            name="submit_result",
                            arguments={
                                "summary": "done",
                                "evidence": [],
                                "unresolved_questions": [],
                                "confidence": 1.0,
                            },
                        )
                    ]
                )
            ]
        }
    )
    config = HarnessConfig()
    trace = JsonlTraceSink("run_cancel", tmp_path)
    scheduler = SubagentScheduler(
        run_id="run_cancel",
        workspace_root=tmp_path,
        config=config,
        provider=provider,
        trace=trace,
        agent_registry=create_default_agent_registry("fake-model", "fake", config.run),
    )
    try:
        handle = await scheduler.spawn(DelegationRequest(agent_name="explorer", task="do it"))
        await scheduler.cancel(handle["agent_id"])
        await asyncio.gather(*scheduler._tasks.values(), return_exceptions=True)
        assert scheduler.status(handle["agent_id"])["status"] in {"CANCELLED", "IDLE"}
    finally:
        trace.close()


async def test_scheduler_repairs_plain_text_child_output(tmp_path: Path):
    """Verify a child that answers with text gets one repair chance to submit_result."""
    provider = FakeModelProvider(
        scripts_by_agent={
            "explorer": [
                ScriptedStep(content="我已经完成分析，但还没调用工具。"),
                ScriptedStep(
                    tool_calls=[
                        ToolCall(
                            id="submit_after_repair",
                            name="submit_result",
                            arguments={
                                "summary": "修正后提交",
                                "evidence": [],
                                "unresolved_questions": [],
                                "confidence": 0.8,
                            },
                        )
                    ]
                ),
            ]
        }
    )
    config = HarnessConfig()
    trace = JsonlTraceSink("run_repair", tmp_path)
    scheduler = SubagentScheduler(
        run_id="run_repair",
        workspace_root=tmp_path,
        config=config,
        provider=provider,
        trace=trace,
        agent_registry=create_default_agent_registry("fake-model", "fake", config.run),
    )
    try:
        handle = await scheduler.spawn(DelegationRequest(agent_name="explorer", task="do it"))
        waited = await scheduler.wait([handle["agent_id"]], timeout_seconds=5)
        assert waited["results"][0]["summary"] == "修正后提交"
    finally:
        trace.close()
    events = (tmp_path / "run_repair" / "events.jsonl").read_text(encoding="utf-8")
    assert "agent.output_repair_requested" in events


async def test_scheduler_reuses_idle_thread_for_followup(tmp_path: Path):
    """Verify follow-up on an idle thread creates a second turn in the same thread."""
    provider = FakeModelProvider(
        scripts_by_agent={
            "explorer:1": [
                ScriptedStep(
                    tool_calls=[
                        ToolCall(
                            id="submit_1",
                            name="submit_result",
                            arguments={"summary": "第一轮", "evidence": [], "unresolved_questions": [], "confidence": 0.7},
                        )
                    ]
                )
            ],
            "explorer:2": [
                ScriptedStep(
                    tool_calls=[
                        ToolCall(
                            id="submit_2",
                            name="submit_result",
                            arguments={"summary": "第二轮", "evidence": [], "unresolved_questions": [], "confidence": 0.9},
                        )
                    ]
                )
            ],
        }
    )
    config = HarnessConfig()
    trace = JsonlTraceSink("run_followup", tmp_path)
    scheduler = SubagentScheduler(
        run_id="run_followup",
        workspace_root=tmp_path,
        config=config,
        provider=provider,
        trace=trace,
        agent_registry=create_default_agent_registry("fake-model", "fake", config.run),
    )
    try:
        handle = await scheduler.spawn(DelegationRequest(agent_name="explorer", task="第一轮"))
        await scheduler.wait([handle["agent_id"]], timeout_seconds=5)
        await scheduler.send_message(handle["agent_id"], "继续第二轮")
        waited = await scheduler.wait([handle["agent_id"]], timeout_seconds=5)
        assert waited["results"][0]["summary"] == "第二轮"
        assert scheduler.status(handle["agent_id"])["turn_count"] == 2
    finally:
        trace.close()


async def test_scheduler_wait_timeout_does_not_cancel_child(tmp_path: Path):
    """Verify wait timeout returns without cancelling a slow child."""
    provider = FakeModelProvider(
        scripts_by_agent={
            "explorer": [
                ScriptedStep(
                    delay_seconds=0.05,
                    tool_calls=[
                        ToolCall(
                            id="submit_slow",
                            name="submit_result",
                            arguments={"summary": "慢任务完成", "evidence": [], "unresolved_questions": [], "confidence": 1.0},
                        )
                    ],
                )
            ]
        }
    )
    config = HarnessConfig()
    trace = JsonlTraceSink("run_timeout", tmp_path)
    scheduler = SubagentScheduler(
        run_id="run_timeout",
        workspace_root=tmp_path,
        config=config,
        provider=provider,
        trace=trace,
        agent_registry=create_default_agent_registry("fake-model", "fake", config.run),
    )
    try:
        handle = await scheduler.spawn(DelegationRequest(agent_name="explorer", task="慢任务"))
        timed_out = await scheduler.wait([handle["agent_id"]], timeout_seconds=0.001)
        assert timed_out["timed_out"] is True
        completed = await scheduler.wait([handle["agent_id"]], timeout_seconds=5)
        assert completed["results"][0]["summary"] == "慢任务完成"
    finally:
        trace.close()
