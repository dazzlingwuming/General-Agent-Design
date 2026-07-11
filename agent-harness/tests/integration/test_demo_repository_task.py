from __future__ import annotations

from pathlib import Path

from agent_harness.config import HarnessConfig
from agent_harness.providers.fake import default_demo_provider
from agent_harness.runtime.run_manager import RunManager


async def test_demo_repository_task_completes():
    """Run the demo repository task with the fake provider and require tool evidence."""
    workspace = Path(__file__).parents[1] / "fixtures" / "demo_repo"
    state = await RunManager(HarnessConfig(), default_demo_provider()).run(
        "请找出 calculate_total 的定义，并说明折扣计算流程。",
        workspace,
    )
    assert state.status.value == "COMPLETED"
    assert state.tool_call_count >= 3
    assert "calculate_total" in state.final_output
