from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_harness.sandbox.base import CommandExecution
from agent_harness.sandbox.bubblewrap import WslBubblewrapSandboxBackend
from agent_harness.security.models import SandboxMode, SandboxPolicy


@pytest.mark.skipif(os.name != "nt", reason="Windows WSL2 integration test")
async def test_wsl_bubblewrap_read_only_boundary(tmp_path: Path):
    """Verify a real WSL2 bubblewrap backend blocks workspace writes when installed."""
    backend = WslBubblewrapSandboxBackend()
    available, reason = await backend.availability()
    if not available:
        pytest.skip(f"WSL2 bubblewrap unavailable: {reason}")
    policy = SandboxPolicy(SandboxMode.READ_ONLY, tmp_path, (tmp_path,), ())
    result = await backend.execute(CommandExecution("touch", ("blocked.txt",), tmp_path), policy)
    assert result.exit_code != 0
    assert not (tmp_path / "blocked.txt").exists()
