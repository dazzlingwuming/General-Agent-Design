from __future__ import annotations

from pathlib import Path

from agent_harness.config import HarnessConfig
from agent_harness.domain.messages import ToolCall
from agent_harness.providers.fake import FakeModelProvider, ScriptedStep
from agent_harness.runtime.run_manager import RunManager


async def test_agent_loop_direct_final(tmp_path: Path):
    """Verify that a direct final model response completes the run."""
    provider = FakeModelProvider([ScriptedStep(content="done")])
    state = await RunManager(HarnessConfig(), provider).run("answer", tmp_path)
    assert state.status.value == "COMPLETED"
    assert state.final_output == "done"


async def test_agent_loop_tool_then_final(tmp_path: Path):
    """Verify that one tool result can feed the next model turn."""
    (tmp_path / "a.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    provider = FakeModelProvider(
        [
            ScriptedStep(tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "a.py"})]),
            ScriptedStep(content="a.py defines main at line 1."),
        ]
    )
    state = await RunManager(HarnessConfig(), provider).run("inspect", tmp_path)
    assert state.status.value == "COMPLETED"
    assert state.tool_call_count == 1
    assert "line 1" in state.final_output


async def test_agent_loop_multiple_tool_calls_same_turn(tmp_path: Path):
    """Verify that multiple tool calls in one model turn execute sequentially."""
    (tmp_path / "a.py").write_text("abc", encoding="utf-8")
    provider = FakeModelProvider(
        [
            ScriptedStep(
                tool_calls=[
                    ToolCall(id="c1", name="list_files", arguments={"path": "."}),
                    ToolCall(id="c2", name="read_file", arguments={"path": "a.py"}),
                ]
            ),
            ScriptedStep(content="done"),
        ]
    )
    state = await RunManager(HarnessConfig(), provider).run("inspect", tmp_path)
    assert state.status.value == "COMPLETED"
    assert state.tool_call_count == 2


async def test_agent_loop_unknown_tool_is_feedback_not_crash(tmp_path: Path):
    """Verify that unknown tools are returned to the model as tool errors."""
    provider = FakeModelProvider(
        [
            ScriptedStep(tool_calls=[ToolCall(id="c1", name="missing_tool", arguments={})]),
            ScriptedStep(content="I recovered from the tool error."),
        ]
    )
    state = await RunManager(HarnessConfig(), provider).run("inspect", tmp_path)
    assert state.status.value == "COMPLETED"
    assert state.tool_call_count == 1


async def test_agent_loop_max_tool_calls(tmp_path: Path):
    """Verify that tool-call budget exhaustion fails the run explicitly."""
    config = HarnessConfig()
    config.run.max_tool_calls = 0
    provider = FakeModelProvider([ScriptedStep(tool_calls=[ToolCall(id="c1", name="list_files", arguments={})])])
    state = await RunManager(config, provider).run("inspect", tmp_path)
    assert state.status.value == "FAILED"
    assert state.error.code == "LIMIT_REACHED"
