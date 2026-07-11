from __future__ import annotations

from pathlib import Path

from agent_harness.tools.builtins.list_files import create_list_files_tool
from agent_harness.tools.builtins.read_file import create_read_file_tool
from agent_harness.tools.builtins.search_text import create_search_text_tool
from agent_harness.tools.registry import ToolRegistry


def create_default_registry(workspace_root: Path, timeout_seconds: int = 30) -> ToolRegistry:
    """Register all phase 1 built-in read-only tools for one workspace."""
    registry = ToolRegistry()
    registry.register(create_list_files_tool(workspace_root, timeout_seconds))
    registry.register(create_read_file_tool(workspace_root, timeout_seconds))
    registry.register(create_search_text_tool(workspace_root, timeout_seconds))
    return registry
