from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_harness.config import HarnessConfig, load_nearest_dotenv
from agent_harness.providers.deepseek import DeepSeekProvider
from agent_harness.runtime.run_manager import RunManager

load_nearest_dotenv(Path(__file__).resolve())


@pytest.mark.live
@pytest.mark.skipif(not os.getenv("DEEPSEEK_API_KEY"), reason="DEEPSEEK_API_KEY not set")
async def test_deepseek_live_basic_protocol():
    """Verify that the configured DeepSeek-compatible endpoint accepts one real run."""
    workspace = Path(__file__).parents[1] / "fixtures" / "demo_repo"
    config = HarnessConfig()
    config.provider.model = "deepseek-v4-flash"
    config.run.max_iterations = 6
    provider = DeepSeekProvider(max_attempts=1)
    try:
        state = await RunManager(config, provider).run("List the main files in this repository briefly.", workspace)
    finally:
        await provider.close()
    assert state.status.value in {"COMPLETED", "FAILED"}
    assert state.model_call_count >= 1
