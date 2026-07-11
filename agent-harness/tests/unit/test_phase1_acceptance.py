from __future__ import annotations

import json
from pathlib import Path

from agent_harness.config import HarnessConfig
from agent_harness.context.builder import ContextBuilder
from agent_harness.context.prompt import SYSTEM_PROMPT
from agent_harness.domain.agent import AgentDefinition
from agent_harness.domain.errors import ProviderError
from agent_harness.domain.messages import CanonicalMessage, ToolCall
from agent_harness.domain.model import ModelRequest, ModelResponse, ProviderCapabilities
from agent_harness.domain.run import RunState
from agent_harness.providers.fake import FakeModelProvider, ScriptedStep
from agent_harness.runtime.agent_loop import AgentLoop
from agent_harness.runtime.run_manager import RunManager
from agent_harness.tools.builtins.factory import create_default_registry
from agent_harness.tools.runtime import ToolRuntime
from agent_harness.tracing.jsonl import JsonlTraceSink


class InspectingProvider:
    """Fake provider that asserts tool schemas and tool-result messages across turns."""

    name = "inspecting"
    capabilities = ProviderCapabilities(supports_tools=True)

    def __init__(self) -> None:
        """Initialize the call counter used to drive scripted assertions."""
        self.calls = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Assert request shape and return a multi-turn scripted response."""
        self.calls += 1
        tool_names = {tool["function"]["name"] for tool in request.tools}
        assert {"list_files", "read_file", "search_text"}.issubset(tool_names)
        if self.calls == 1:
            return ModelResponse(
                assistant_message=CanonicalMessage(
                    role="assistant",
                    tool_calls=[ToolCall(id="c1", name="list_files", arguments={"path": "."})],
                ),
                tool_calls=[ToolCall(id="c1", name="list_files", arguments={"path": "."})],
                finish_reason="tool_calls",
            )
        if self.calls == 2:
            assert any(m.role == "tool" and m.tool_call_id == "c1" for m in request.messages)
            return ModelResponse(
                assistant_message=CanonicalMessage(
                    role="assistant",
                    tool_calls=[ToolCall(id="c2", name="search_text", arguments={"query": "main", "glob": "*.py"})],
                ),
                tool_calls=[ToolCall(id="c2", name="search_text", arguments={"query": "main", "glob": "*.py"})],
                finish_reason="tool_calls",
            )
        assert any(m.role == "tool" and m.tool_call_id == "c2" for m in request.messages)
        return ModelResponse(assistant_message=CanonicalMessage(role="assistant", content="多轮工具调用完成。"), finish_reason="stop")

    async def close(self) -> None:
        """Match the ModelProvider close protocol."""
        return None


class FailingProvider:
    """Fake provider that always raises a provider-level error."""

    name = "failing"
    capabilities = ProviderCapabilities(supports_tools=True)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Raise a provider error to verify run failure semantics."""
        raise ProviderError("provider unavailable")

    async def close(self) -> None:
        """Match the ModelProvider close protocol."""
        return None


async def test_phase1_multi_turn_loop_and_tool_results_enter_context(tmp_path: Path):
    """Verify schemas, two tool rounds, tool result feedback, and final output."""
    (tmp_path / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    config = HarnessConfig()
    state = await RunManager(config, InspectingProvider()).run("请多轮分析。", tmp_path)
    assert state.status.value == "COMPLETED"
    assert state.model_call_count == 3
    assert state.tool_call_count == 2
    assert state.final_output == "多轮工具调用完成。"


async def test_phase1_unique_run_ids(tmp_path: Path):
    """Verify that each submitted run receives a unique run id."""
    first = await RunManager(HarnessConfig(), FakeModelProvider([ScriptedStep(content="one")])).run("one", tmp_path)
    second = await RunManager(HarnessConfig(), FakeModelProvider([ScriptedStep(content="two")])).run("two", tmp_path)
    assert first.run_id != second.run_id


async def test_phase1_provider_error_fails_run(tmp_path: Path):
    """Verify that provider errors fail the run without crashing the process."""
    state = await RunManager(HarnessConfig(), FailingProvider()).run("fail", tmp_path)
    assert state.status.value == "FAILED"
    assert state.error is not None
    assert state.error.code == "PROVIDER_ERROR"


async def test_phase1_provider_empty_response_fails_run(tmp_path: Path):
    """Verify that empty provider responses are protocol errors, not infinite loops."""
    provider = FakeModelProvider([ScriptedStep(content="", tool_calls=[])])
    state = await RunManager(HarnessConfig(), provider).run("empty", tmp_path)
    assert state.status.value == "FAILED"
    assert state.error is not None
    assert state.error.code == "PROVIDER_PROTOCOL_ERROR"


async def test_phase1_model_call_budget(tmp_path: Path):
    """Verify that max_model_calls is enforced."""
    config = HarnessConfig()
    config.run.max_model_calls = 1
    provider = FakeModelProvider(
        [
            ScriptedStep(tool_calls=[ToolCall(id="c1", name="list_files", arguments={"path": "."})]),
            ScriptedStep(content="should not reach"),
        ]
    )
    state = await RunManager(config, provider).run("budget", tmp_path)
    assert state.status.value == "FAILED"
    assert state.error is not None
    assert state.error.code == "LIMIT_REACHED"


async def test_phase1_iteration_budget(tmp_path: Path):
    """Verify that max_iterations is enforced."""
    config = HarnessConfig()
    config.run.max_iterations = 1
    provider = FakeModelProvider(
        [
            ScriptedStep(tool_calls=[ToolCall(id="c1", name="list_files", arguments={"path": "."})]),
            ScriptedStep(content="should not reach"),
        ]
    )
    state = await RunManager(config, provider).run("budget", tmp_path)
    assert state.status.value == "FAILED"
    assert state.error is not None
    assert state.error.code == "LIMIT_REACHED"


async def test_phase1_cancellation_state(tmp_path: Path):
    """Verify that cancellation_requested produces CANCELLED rather than FAILED."""
    trace = JsonlTraceSink("run_cancel_acceptance", tmp_path)
    registry = create_default_registry(tmp_path)
    state = RunState(task="cancel", workspace_root=tmp_path)
    state.messages.append(CanonicalMessage(role="user", content="cancel"))
    state.cancellation_requested = True
    loop = AgentLoop(
        agent=AgentDefinition(name="coding_assistant", description="test", system_prompt=SYSTEM_PROMPT),
        provider=FakeModelProvider([ScriptedStep(content="should not run")]),
        context_builder=ContextBuilder(),
        tool_runtime=ToolRuntime(registry),
        trace=trace,
    )
    try:
        result = await loop.run(state)
    finally:
        trace.close()
    assert result.status.value == "CANCELLED"
    assert result.error is not None
    assert result.error.code == "CANCELLED"


async def test_phase1_trace_sequence_and_result_json(tmp_path: Path):
    """Verify that JSONL trace is ordered and result.json is written."""
    config = HarnessConfig()
    config.trace.directory = tmp_path / "runs"
    state = await RunManager(config, FakeModelProvider([ScriptedStep(content="done")])).run("trace", tmp_path)
    run_dir = config.trace.directory / state.run_id
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [event["sequence_number"] for event in events] == list(range(1, len(events) + 1))
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["run_id"] == state.run_id
    assert result["status"] == "COMPLETED"

