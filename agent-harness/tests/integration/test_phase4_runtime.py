from __future__ import annotations

import json
from pathlib import Path

from agent_harness.config import HarnessConfig
from agent_harness.domain.messages import ToolCall
from agent_harness.providers.fake import FakeModelProvider, ScriptedStep
from agent_harness.runtime.run_manager import RunManager


def submit_review() -> ToolCall:
    """Build the structured terminal tool call used by the forked reviewer."""
    return ToolCall(
        id="submit_review",
        name="submit_result",
        arguments={
            "summary": "审查完成",
            "evidence": [{"path": "calculator/pricing.py", "start_line": 1, "end_line": 10, "claim": "已检查价格逻辑"}],
            "unresolved_questions": [],
            "confidence": 0.9,
            "structured_data": {"findings": []},
        },
    )


async def test_bundled_fork_skill_runs_in_independent_reviewer(tmp_path: Path) -> None:
    """Activate a bundled fork Skill, run its declared child, and return structured output."""
    workspace = Path(__file__).parents[1] / "fixtures" / "demo_repo"
    config = HarnessConfig()
    config.provider.name = "fake"
    config.trace.directory = tmp_path / "runs"
    config.trace.thread_directory = tmp_path / "threads"
    provider = FakeModelProvider(
        scripts_by_agent={
            "coding_assistant": [
                ScriptedStep(tool_calls=[ToolCall(id="activate", name="activate_skill", arguments={"skill": "code-review-fork", "arguments": "检查价格逻辑"})]),
                ScriptedStep(content="已收到独立 reviewer 的结构化审查结果。"),
            ],
            "reviewer": [ScriptedStep(tool_calls=[submit_review()])],
        }
    )
    state = await RunManager(config, provider).run("使用独立 Skill 审查价格逻辑。", workspace)
    assert state.status.value == "COMPLETED"
    assert state.agent_summary and state.agent_summary["succeeded"] == 1
    events = [json.loads(line) for line in (config.trace.directory / state.run_id / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(item["event_type"] == "agent.spawned" for item in events)


async def test_guidance_and_catalog_are_in_system_context_not_history(tmp_path: Path) -> None:
    """Inject trusted Guidance and Skill metadata without appending either to RunState history."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("始终使用中文。", encoding="utf-8")
    skill = workspace / ".agents" / "skills" / "inspect"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: inspect\ndescription: 检查项目内容时使用。\n---\n完整 Skill 正文", encoding="utf-8")
    config = HarnessConfig()
    config.provider.name = "fake"
    config.guidance.require_workspace_trust = False
    config.skills.require_workspace_trust = False
    config.trace.directory = tmp_path / "runs"
    config.trace.thread_directory = tmp_path / "threads"
    provider = FakeModelProvider([ScriptedStep(content="完成。")])
    state = await RunManager(config, provider).run("开始", workspace)
    request = provider.calls
    assert request == 1
    assert all("始终使用中文" not in (message.content or "") for message in state.messages)
