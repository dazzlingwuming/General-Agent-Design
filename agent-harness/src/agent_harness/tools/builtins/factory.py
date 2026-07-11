from __future__ import annotations

from pathlib import Path

from agent_harness.tools.builtins.list_files import create_list_files_tool
from agent_harness.tools.builtins.apply_patch import create_apply_patch_tool
from agent_harness.tools.builtins.delete_path import create_delete_path_tool
from agent_harness.tools.builtins.read_file import create_read_file_tool
from agent_harness.tools.builtins.run_command import create_run_command_tool
from agent_harness.tools.builtins.search_text import create_search_text_tool
from agent_harness.tools.builtins.write_file import create_write_file_tool
from agent_harness.tools.registry import ToolRegistry
from agent_harness.sandbox.base import SandboxBackend
from agent_harness.security.models import SandboxPolicy


def create_default_registry(workspace_root: Path, timeout_seconds: int = 30, *, sandbox_backend: SandboxBackend | None = None, sandbox_policy: SandboxPolicy | None = None) -> ToolRegistry:
    """Register read and write tools, plus command execution when a backend is supplied."""
    registry = ToolRegistry()
    registry.register(create_list_files_tool(workspace_root, timeout_seconds))
    registry.register(create_read_file_tool(workspace_root, timeout_seconds))
    registry.register(create_search_text_tool(workspace_root, timeout_seconds))
    registry.register(create_write_file_tool(workspace_root, timeout_seconds))
    registry.register(create_apply_patch_tool(workspace_root, timeout_seconds))
    registry.register(create_delete_path_tool(workspace_root, timeout_seconds))
    if sandbox_backend is not None and sandbox_policy is not None:
        registry.register(create_run_command_tool(workspace_root, sandbox_backend, sandbox_policy, int(sandbox_policy.timeout_seconds)))
    return registry
