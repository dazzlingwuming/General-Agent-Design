from __future__ import annotations

import asyncio

from agent_harness.domain.messages import ToolCall
from agent_harness.domain.tools import ToolDefinition
from agent_harness.tools.registry import ToolRegistry
from agent_harness.tools.runtime import ToolRuntime


async def ok(args):
    """Return a small dictionary containing the provided value."""
    return {"value": args["value"]}


async def slow(args):
    """Sleep long enough to trigger the tool runtime timeout path."""
    await asyncio.sleep(0.2)
    return "late"


async def test_tool_runtime_validates_required_argument():
    """Verify that missing required arguments become recoverable tool errors."""
    registry = ToolRegistry()
    registry.register(ToolDefinition("ok", "desc", {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}, ok))
    result = await ToolRuntime(registry).execute(ToolCall(id="c1", name="ok", arguments={}))
    assert result.status == "error"
    assert result.error_code == "TOOL_INPUT_VALIDATION"


async def test_tool_runtime_timeout():
    """Verify that slow tools return timeout ToolResult objects."""
    registry = ToolRegistry()
    registry.register(ToolDefinition("slow", "desc", {"type": "object", "properties": {}, "required": []}, slow, timeout_seconds=0.01))
    result = await ToolRuntime(registry).execute(ToolCall(id="c1", name="slow", arguments={}))
    assert result.status == "timeout"


async def test_tool_runtime_truncates_output():
    """Verify that oversized tool output is truncated before reaching the model."""
    registry = ToolRegistry()
    registry.register(ToolDefinition("ok", "desc", {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}, ok))
    result = await ToolRuntime(registry, max_result_chars=5).execute(ToolCall(id="c1", name="ok", arguments={"value": "abcdef"}))
    assert result.metadata["truncated"] is True
