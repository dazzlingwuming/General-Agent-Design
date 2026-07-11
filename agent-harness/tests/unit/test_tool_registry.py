from __future__ import annotations

import pytest

from agent_harness.domain.tools import ToolDefinition
from agent_harness.tools.registry import ToolRegistry


async def noop(args):
    """Return a stable value for registry tests."""
    return "ok"


def test_registry_rejects_duplicate_tool_names():
    """Verify that duplicate tool names are rejected during registration."""
    registry = ToolRegistry()
    tool = ToolDefinition("x", "desc", {"type": "object", "properties": {}, "required": []}, noop)
    registry.register(tool)
    with pytest.raises(ValueError):
        registry.register(tool)


def test_registry_exports_stable_schema_order():
    """Verify that exported tool schemas are sorted by name for deterministic tests."""
    registry = ToolRegistry()
    registry.register(ToolDefinition("b", "desc", {"type": "object", "properties": {}, "required": []}, noop))
    registry.register(ToolDefinition("a", "desc", {"type": "object", "properties": {}, "required": []}, noop))
    assert [item["function"]["name"] for item in registry.export_schemas()] == ["a", "b"]
