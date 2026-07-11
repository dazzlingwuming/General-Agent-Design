from __future__ import annotations

import asyncio

from agent_harness.domain.messages import ToolCall
from agent_harness.domain.tools import ToolDefinition
from agent_harness.tools.registry import ToolRegistry
from agent_harness.tools.runtime import ToolExecutionPrincipal, ToolRuntime
from agent_harness.security.models import Capability


async def ok(args):
    """Return a small dictionary containing the provided value."""
    return {"value": args["value"]}


async def slow(args):
    """Sleep long enough to trigger the tool runtime timeout path."""
    await asyncio.sleep(0.2)
    return "late"


def make_principal(*tools: str, capabilities: frozenset = frozenset({Capability.FILE_READ})) -> ToolExecutionPrincipal:
    """Create an explicit phase 3 principal for ToolRuntime unit tests."""
    return ToolExecutionPrincipal("thread_1", "thread_1", "turn_0001", "agent", allowed_tools=frozenset(tools), capabilities=capabilities)


async def test_tool_runtime_requires_principal():
    """Verify that no tool can execute without an explicit principal."""
    registry = ToolRegistry()
    registry.register(ToolDefinition("ok", "desc", {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}, ok))
    result = await ToolRuntime(registry).execute(ToolCall(id="c0", name="ok", arguments={"value": "x"}))
    assert result.status == "error"
    assert result.error_code == "TOOL_AUTHORIZATION"


async def test_tool_runtime_validates_required_argument():
    """Verify that missing required arguments become recoverable tool errors."""
    registry = ToolRegistry()
    registry.register(ToolDefinition("ok", "desc", {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}, ok))
    result = await ToolRuntime(registry).execute(ToolCall(id="c1", name="ok", arguments={}), make_principal("ok"))
    assert result.status == "error"
    assert result.error_code == "TOOL_INPUT_VALIDATION"


async def test_tool_runtime_timeout():
    """Verify that slow tools return timeout ToolResult objects."""
    registry = ToolRegistry()
    registry.register(ToolDefinition("slow", "desc", {"type": "object", "properties": {}, "required": []}, slow, timeout_seconds=0.01))
    result = await ToolRuntime(registry).execute(ToolCall(id="c1", name="slow", arguments={}), make_principal("slow"))
    assert result.status == "timeout"


async def test_tool_runtime_truncates_output():
    """Verify that oversized tool output is truncated before reaching the model."""
    registry = ToolRegistry()
    registry.register(ToolDefinition("ok", "desc", {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}, ok))
    result = await ToolRuntime(registry, max_result_chars=5).execute(ToolCall(id="c1", name="ok", arguments={"value": "abcdef"}), make_principal("ok"))
    assert result.metadata["truncated"] is True


async def test_tool_runtime_rejects_unallowed_tool_name():
    """Verify that hidden tool names cannot bypass execution-time authorization."""
    registry = ToolRegistry()
    registry.register(ToolDefinition("ok", "desc", {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}, ok))
    principal = ToolExecutionPrincipal(
        session_id="thread_1",
        thread_id="thread_1",
        turn_id="turn_0001",
        agent_id="agent",
        allowed_tools=frozenset({"read_file"}),
        capabilities=frozenset({"FILE_READ"}),
    )

    result = await ToolRuntime(registry).execute(ToolCall(id="c1", name="ok", arguments={"value": "x"}), principal)

    assert result.status == "error"
    assert result.error_code == "TOOL_AUTHORIZATION"


async def test_tool_runtime_rejects_missing_capability():
    """Verify that required tool capabilities are enforced after allowlist checks."""
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "ok",
            "desc",
            {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
            ok,
            required_capabilities=[Capability.COMMAND_EXECUTE],
        )
    )
    principal = ToolExecutionPrincipal(
        session_id="thread_1",
        thread_id="thread_1",
        turn_id="turn_0001",
        agent_id="agent",
        allowed_tools=frozenset({"ok"}),
        capabilities=frozenset({"FILE_READ"}),
    )

    result = await ToolRuntime(registry).execute(ToolCall(id="c1", name="ok", arguments={"value": "x"}), principal)

    assert result.status == "error"
    assert result.error_code == "TOOL_AUTHORIZATION"


async def test_tool_runtime_validates_enum_range_array_and_nested_object():
    """Verify that the local schema validator covers deeper JSON Schema constraints."""
    registry = ToolRegistry()
    schema = {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["fast", "safe"]},
            "count": {"type": "integer", "minimum": 1, "maximum": 3},
            "tags": {"type": "array", "items": {"type": "string", "minLength": 2}},
            "options": {
                "type": "object",
                "properties": {"enabled": {"type": "boolean"}},
                "required": ["enabled"],
                "additionalProperties": False,
            },
        },
        "required": ["mode", "count", "tags", "options"],
        "additionalProperties": False,
    }
    registry.register(ToolDefinition("ok", "desc", schema, ok))

    good = await ToolRuntime(registry).execute(
        ToolCall(id="c1", name="ok", arguments={"mode": "safe", "count": 2, "tags": ["aa"], "options": {"enabled": True}, "value": "x"}), make_principal("ok")
    )
    bad = await ToolRuntime(registry).execute(
        ToolCall(id="c2", name="ok", arguments={"mode": "slow", "count": 4, "tags": ["a"], "options": {"enabled": True}}), make_principal("ok")
    )

    assert good.status == "error"
    assert good.error_code == "TOOL_INPUT_VALIDATION"
    assert bad.status == "error"
    assert bad.error_code == "TOOL_INPUT_VALIDATION"
