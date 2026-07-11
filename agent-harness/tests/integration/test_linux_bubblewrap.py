from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_harness.sandbox.base import CommandExecution
from agent_harness.sandbox.bubblewrap import BubblewrapSandboxBackend
from agent_harness.security.models import SandboxMode, SandboxPolicy


@pytest.mark.skipif(os.name == "nt", reason="Native Linux bubblewrap integration test")
async def test_linux_bubblewrap_read_only_and_network_namespace(tmp_path: Path):
    """Verify native bubblewrap blocks workspace writes under a network-disabled policy."""
    backend = BubblewrapSandboxBackend()
    available, reason = await backend.availability()
    if not available:
        pytest.skip(f"bubblewrap unavailable: {reason}")
    policy = SandboxPolicy(SandboxMode.READ_ONLY, tmp_path, (tmp_path,), (), network_enabled=False)
    result = await backend.execute(CommandExecution("touch", ("blocked.txt",), tmp_path), policy)
    assert result.exit_code != 0
    assert not (tmp_path / "blocked.txt").exists()
