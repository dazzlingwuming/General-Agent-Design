from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import json

from agent_harness.agents.registry import create_default_agent_registry
from agent_harness.config import HarnessConfig
from agent_harness.domain.messages import CanonicalMessage, ToolCall
from agent_harness.domain.model import ModelRequest, ModelResponse
from agent_harness.domain.subagents import DelegationRequest
from agent_harness.providers.fake import FakeModelProvider, ScriptedStep
from agent_harness.runtime.subagents.scheduler import SubagentScheduler
from agent_harness.tracing.jsonl import JsonlTraceSink
from agent_harness.mcp.config import parse_server_config
from agent_harness.mcp.models import MCPConfigScope, MCPServerStatus, MCPToolRecord
from agent_harness.mcp.naming import canonical_tool_name
from agent_harness.mcp.runtime import MCPRuntime


class CleanupBlockingProvider(FakeModelProvider):
    """Expose cancellation cleanup boundaries for scheduler ownership tests."""

    def __init__(self) -> None:
        """Create provider lifecycle events controlled by the test."""
        super().__init__()
        self.started = asyncio.Event()
        self.cleanup_started = asyncio.Event()
        self.cleanup_release = asyncio.Event()

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Block until cancelled, then delay task completion behind a cleanup barrier."""
        self.started.set()
        try:
            await asyncio.Future()
        finally:
            self.cleanup_started.set()
            await self.cleanup_release.wait()
        return ModelResponse(CanonicalMessage(role="assistant", content="unreachable"))


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


async def test_scheduler_cancel_waits_for_child_cleanup_before_terminal_state(tmp_path: Path):
    """Keep CANCELLING visible until the child task finishes cancellation cleanup."""
    provider = CleanupBlockingProvider()
    config = HarnessConfig()
    trace = JsonlTraceSink("run_cancel_cleanup", tmp_path)
    scheduler = SubagentScheduler(
        run_id="run_cancel_cleanup",
        workspace_root=tmp_path,
        config=config,
        provider=provider,
        trace=trace,
        agent_registry=create_default_agent_registry("fake-model", "fake", config.run),
    )
    try:
        handle = await scheduler.spawn(DelegationRequest(agent_name="explorer", task="等待取消"))
        await provider.started.wait()
        cancel_task = asyncio.create_task(scheduler.cancel(handle["agent_id"]))
        await provider.cleanup_started.wait()
        await asyncio.sleep(0)
        assert scheduler.status(handle["agent_id"])["status"] == "CANCELLING"
        assert not cancel_task.done()
        provider.cleanup_release.set()
        result = await cancel_task
        assert result["status"] == "CANCELLED"
    finally:
        provider.cleanup_release.set()
        await scheduler.cancel_all()
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


async def test_child_uses_only_delegated_mcp_tool_with_child_attribution(tmp_path: Path):
    """Execute a scripted child MCP call through the shared connection and narrow subset."""
    remote_name = "read_shared"
    canonical = canonical_tool_name("shared", remote_name)
    record = MCPToolRecord("shared", remote_name, canonical, "读取共享数据", {"type": "object", "properties": {}})
    server_config = parse_server_config("shared", {"url": "https://example.com/mcp", "default_approval_mode": "never"}, MCPConfigScope.USER, tmp_path)

    class SharedConnection:
        """Expose one shared MCP tool and record child calls without opening transport."""

        config = server_config
        tools = (record,)
        resources = ()
        prompts = ()
        status = MCPServerStatus.READY
        is_usable = True

        def __init__(self) -> None:
            """Initialize an empty remote call log."""
            self.calls: list[tuple[str, dict]] = []

        async def call_tool(self, name: str, arguments: dict) -> dict:
            """Record one delegated call and return a normalized payload."""
            self.calls.append((name, arguments))
            return {"text": ["共享读取成功"], "is_error": False}

    connection = SharedConnection()
    runtime = MCPRuntime(SimpleNamespace(servers=(), blocked=(), diagnostics=()), (tmp_path,))
    runtime.manager.connections = {"shared": connection}  # type: ignore[dict-item]
    provider = FakeModelProvider(
        scripts_by_agent={
            "explorer": [
                ScriptedStep(tool_calls=[ToolCall("mcp-call", canonical, {})]),
                ScriptedStep(tool_calls=[ToolCall("submit", "submit_result", {"summary": "已调用共享 MCP", "evidence": [], "unresolved_questions": [], "confidence": 1.0})]),
            ]
        }
    )
    config = HarnessConfig()
    trace = JsonlTraceSink("run_child_mcp", tmp_path)
    scheduler = SubagentScheduler("run_child_mcp", tmp_path, config, provider, trace, create_default_agent_registry("fake-model", "fake", config.run), mcp_runtime=runtime)
    try:
        handle = await scheduler.spawn(DelegationRequest(agent_name="explorer", task="读取 MCP", allowed_mcp_tools=(canonical, "mcp__unknown")))
        waited = await scheduler.wait([handle["agent_id"]], timeout_seconds=5)
        assert waited["results"][0]["summary"] == "已调用共享 MCP"
        assert connection.calls == [(remote_name, {})]
        events = [json.loads(line) for line in (tmp_path / "run_child_mcp" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        permission = next(item for item in events if item["event_type"] == "permission.evaluated" and item["payload"].get("tool") == canonical)
        assert permission["agent_id"] == handle["agent_id"]
        assert permission["thread_id"] != "run_child_mcp"
    finally:
        trace.close()
