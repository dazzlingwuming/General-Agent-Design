from __future__ import annotations

import json
from pathlib import Path

from agent_harness.config import HarnessConfig
from agent_harness.domain.messages import ToolCall
from agent_harness.providers.fake import FakeModelProvider, ScriptedStep
from agent_harness.runtime.run_manager import RunManager


def _submit_call(call_id: str, summary: str) -> ToolCall:
    """Build a submit_result tool call for a scripted child agent."""
    return ToolCall(
        id=call_id,
        name="submit_result",
        arguments={
            "summary": summary,
            "evidence": [{"path": "calculator/pricing.py", "start_line": 8, "end_line": 12, "claim": summary}],
            "unresolved_questions": [],
            "confidence": 0.9,
            "structured_data": {"kind": "demo"},
        },
    )


async def test_root_spawns_parallel_children_and_waits(tmp_path: Path):
    """Verify the phase 2 root-to-child spawn/wait/submit_result path."""
    workspace = Path(__file__).parents[1] / "fixtures" / "demo_repo"
    trace_dir = tmp_path / "runs"
    config = HarnessConfig()
    config.provider.name = "fake"
    config.trace.directory = trace_dir
    provider = FakeModelProvider(
        scripts_by_agent={
            "coding_assistant": [
                ScriptedStep(
                    tool_calls=[
                        ToolCall(
                            id="spawn_1",
                            name="spawn_subagent",
                            arguments={"agent_name": "explorer", "task": "定位 calculate_total"},
                        ),
                        ToolCall(
                            id="spawn_2",
                            name="spawn_subagent",
                            arguments={"agent_name": "reviewer", "task": "审查折扣风险"},
                        ),
                    ]
                ),
                ScriptedStep(tool_calls=[ToolCall(id="wait_1", name="wait_subagents", arguments={"mode": "all"})]),
                ScriptedStep(content="已综合 explorer 和 reviewer 的结果完成分析。"),
            ],
            "explorer": [ScriptedStep(tool_calls=[_submit_call("submit_1", "calculate_total 定义位于 pricing.py")])],
            "reviewer": [ScriptedStep(tool_calls=[_submit_call("submit_2", "折扣逻辑需要关注重复应用风险")])],
        }
    )

    state = await RunManager(config, provider).run("并行分析价格计算。", workspace)

    assert state.status.value == "COMPLETED"
    assert state.agent_summary is not None
    assert state.agent_summary["total_spawned"] == 2
    assert state.agent_summary["succeeded"] == 2
    assert "explorer" in json.dumps(state.agent_summary, ensure_ascii=False)

    result = json.loads((trace_dir / state.run_id / "result.json").read_text(encoding="utf-8"))
    assert result["agent_summary"]["total_spawned"] == 2
    assert (trace_dir / state.run_id / "agents").exists()


async def test_three_child_demo_and_monotonic_trace(tmp_path: Path):
    """Verify the phase 2 demo shape with three children and one shared trace sequence."""
    workspace = Path(__file__).parents[1] / "fixtures" / "demo_repo"
    trace_dir = tmp_path / "runs"
    config = HarnessConfig()
    config.provider.name = "fake"
    config.trace.directory = trace_dir
    provider = FakeModelProvider(
        scripts_by_agent={
            "coding_assistant": [
                ScriptedStep(
                    tool_calls=[
                        ToolCall(id="spawn_explorer", name="spawn_subagent", arguments={"agent_name": "explorer", "task": "定位价格和折扣调用链"}),
                        ToolCall(id="spawn_reviewer", name="spawn_subagent", arguments={"agent_name": "reviewer", "task": "审查重复折扣风险"}),
                        ToolCall(id="spawn_tests", name="spawn_subagent", arguments={"agent_name": "test_analyst", "task": "分析测试覆盖"}),
                    ]
                ),
                ScriptedStep(tool_calls=[ToolCall(id="wait_all", name="wait_subagents", arguments={"mode": "all"})]),
                ScriptedStep(content="综合三个子 Agent 的结构化结果：存在重复折扣风险，需要补充测试。"),
            ],
            "explorer": [ScriptedStep(tool_calls=[_submit_call("submit_explorer", "调用链定位完成")])],
            "reviewer": [ScriptedStep(tool_calls=[_submit_call("submit_reviewer", "发现重复折扣风险")])],
            "test_analyst": [ScriptedStep(tool_calls=[_submit_call("submit_tests", "测试未覆盖重复折扣")])],
        }
    )

    state = await RunManager(config, provider).run("分析订单总价计算是否可能重复应用折扣。", workspace)

    assert state.status.value == "COMPLETED"
    assert state.agent_summary is not None
    assert state.agent_summary["total_spawned"] == 3
    assert state.agent_summary["succeeded"] == 3
    assert state.agent_summary["max_concurrent_observed"] >= 1

    events = [
        json.loads(line)
        for line in (trace_dir / state.run_id / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    sequences = [event["sequence_number"] for event in events]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    assert any(event["event_type"] == "agent.spawned" and event["agent_id"] for event in events)


async def test_child_failure_does_not_cancel_sibling_or_root(tmp_path: Path):
    """Verify one child provider failure is isolated from siblings and root completion."""
    workspace = Path(__file__).parents[1] / "fixtures" / "demo_repo"
    trace_dir = tmp_path / "runs"
    config = HarnessConfig()
    config.provider.name = "fake"
    config.trace.directory = trace_dir
    provider = FakeModelProvider(
        scripts_by_agent={
            "coding_assistant": [
                ScriptedStep(
                    tool_calls=[
                        ToolCall(id="spawn_ok", name="spawn_subagent", arguments={"agent_name": "explorer", "task": "正常分析"}),
                        ToolCall(id="spawn_fail", name="spawn_subagent", arguments={"agent_name": "reviewer", "task": "故障分析"}),
                    ]
                ),
                ScriptedStep(tool_calls=[ToolCall(id="wait_all", name="wait_subagents", arguments={"mode": "all"})]),
                ScriptedStep(content="已收到一个成功结果和一个失败状态，根 Agent 继续完成。"),
            ],
            "explorer": [ScriptedStep(tool_calls=[_submit_call("submit_ok", "成功结果")])],
            "reviewer": [ScriptedStep(error=RuntimeError("模拟 provider 故障"))],
        }
    )

    state = await RunManager(config, provider).run("验证失败隔离。", workspace)

    assert state.status.value == "COMPLETED"
    assert state.agent_summary is not None
    assert state.agent_summary["total_spawned"] == 2
    assert state.agent_summary["succeeded"] == 1
    assert state.agent_summary["failed"] == 1
