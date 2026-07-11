from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_harness.domain.messages import ToolCall
from agent_harness.tools.builtins.factory import create_default_registry
from agent_harness.tools.runtime import ToolExecutionPrincipal, ToolRuntime
from agent_harness.security.models import Capability
from agent_harness.utils.paths import resolve_workspace_path


def test_rejects_parent_escape(tmp_path: Path):
    """Verify that parent-directory paths cannot escape the workspace."""
    with pytest.raises(Exception):
        resolve_workspace_path(tmp_path, "../outside.txt")


def test_rejects_absolute_path(tmp_path: Path):
    """Verify that absolute paths are rejected before tool execution."""
    with pytest.raises(Exception):
        resolve_workspace_path(tmp_path, str(Path.cwd()))


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink unavailable")
def test_rejects_symlink_escape(tmp_path: Path):
    """Verify that symlinks resolving outside the workspace are rejected."""
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation not permitted")
    with pytest.raises(Exception):
        resolve_workspace_path(tmp_path, "link.txt", must_exist=True)


async def test_read_file_blocks_secret_file(tmp_path: Path):
    """Verify that read_file refuses common secret filenames."""
    (tmp_path / ".env").write_text("TOKEN=x", encoding="utf-8")
    runtime = ToolRuntime(create_default_registry(tmp_path))
    principal = ToolExecutionPrincipal("thread", "thread", "turn", "agent", allowed_tools=frozenset({"read_file"}), capabilities=frozenset({Capability.FILE_READ}))
    result = await runtime.execute(ToolCall(id="c1", name="read_file", arguments={"path": ".env"}), principal)
    assert result.status == "error"
    assert result.error_code == "TOOL_AUTHORIZATION"
