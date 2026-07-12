from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx
from mcp import types

from agent_harness.domain.errors import ToolInputValidationError
from agent_harness.mcp.approval_policy import MCPApprovalMode, MCPApprovalResolver
from agent_harness.mcp.auth import credential_identity
from agent_harness.guidance.trust import TrustDecisionSource, WorkspaceTrustState, resolve_project_trust
from agent_harness.mcp.config import MCPConfigResolver, parse_server_config
from agent_harness.mcp.connection import MCPServerConnection
from agent_harness.mcp.errors import MCPProtocolError, MCPToolExecutionError
from agent_harness.mcp.models import MCPConfigScope, MCPServerStatus, MCPToolRecord
from agent_harness.mcp.naming import canonical_tool_name
from agent_harness.mcp.pagination import collect_paginated
from agent_harness.mcp.runtime import MCPRuntime
from agent_harness.mcp.schema_validation import validate_mcp_value
from agent_harness.tools.registry import ToolRegistry


async def test_pagination_collects_all_pages_and_rejects_cursor_loop() -> None:
    """Collect every page and stop a server that repeats its cursor forever."""
    pages = {None: SimpleNamespace(items=(1, 2), nextCursor="a"), "a": SimpleNamespace(items=(3, 4), nextCursor="b"), "b": SimpleNamespace(items=(5,), nextCursor=None)}

    async def fetch(cursor: str | None) -> SimpleNamespace:
        """Return one deterministic in-memory protocol page."""
        return pages[cursor]

    items, count, truncated = await collect_paginated(fetch, get_items=lambda page: page.items, get_next_cursor=lambda page: page.nextCursor, max_pages=10, max_items=10)
    assert items == (1, 2, 3, 4, 5)
    assert (count, truncated) == (3, False)

    async def looping(_cursor: str | None) -> SimpleNamespace:
        """Return an invalid repeated cursor for protocol-loop coverage."""
        return SimpleNamespace(items=(1,), nextCursor="same")

    with pytest.raises(MCPProtocolError):
        await collect_paginated(looping, get_items=lambda page: page.items, get_next_cursor=lambda page: page.nextCursor, max_pages=10, max_items=10)


async def test_tool_execution_error_keeps_connection_ready(tmp_path: Path) -> None:
    """Treat isError as a model-visible tool failure rather than a broken connection."""
    config = parse_server_config("fixture", {"url": "https://example.com/mcp"}, MCPConfigScope.USER, tmp_path)
    connection = MCPServerConnection(config, (tmp_path,))

    class Session:
        """Provide one valid MCP error result without a transport failure."""

        async def call_tool(self, _name: str, _arguments: dict) -> types.CallToolResult:
            """Return a server-declared execution error."""
            return types.CallToolResult(isError=True, content=[types.TextContent(type="text", text="参数 project_id 不存在")])

    connection.session = Session()  # type: ignore[assignment]
    connection.status = MCPServerStatus.READY
    with pytest.raises(MCPToolExecutionError, match="project_id"):
        await connection.call_tool("missing", {})
    assert connection.status == MCPServerStatus.READY


async def test_tool_call_cancellation_propagates(tmp_path: Path) -> None:
    """Propagate cancellation across the MCP tool boundary without wrapping it."""
    config = parse_server_config("fixture", {"url": "https://example.com/mcp"}, MCPConfigScope.USER, tmp_path)
    connection = MCPServerConnection(config, (tmp_path,))

    class Session:
        """Simulate a cancellable pending server call."""

        async def call_tool(self, _name: str, _arguments: dict) -> types.CallToolResult:
            """Wait until the test cancels this operation."""
            await asyncio.sleep(60)
            raise AssertionError("unreachable")

    connection.session = Session()  # type: ignore[assignment]
    connection.status = MCPServerStatus.READY
    task = asyncio.create_task(connection.call_tool("slow", {}))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_read_operation_reinitializes_once_after_typed_http_404(tmp_path: Path) -> None:
    """Rebuild an expired session and retry only a read-class operation once."""
    config = parse_server_config("fixture", {"url": "https://example.com/mcp"}, MCPConfigScope.USER, tmp_path)
    connection = MCPServerConnection(config, (tmp_path,))
    calls = 0
    reinitialized: list[str] = []

    async def operation() -> str:
        """Fail once with a typed 404 response, then return the recovered value."""
        nonlocal calls
        calls += 1
        if calls == 1:
            request = httpx.Request("POST", config.url or "")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("expired session", request=request, response=response)
        return "recovered"

    async def reinitialize(reason: str) -> None:
        """Record the recovery reason without opening a real network transport."""
        reinitialized.append(reason)

    connection._reinitialize = reinitialize  # type: ignore[method-assign]
    assert await connection._execute_with_session_recovery(operation) == "recovered"
    assert (calls, reinitialized) == (2, ["session_not_found"])


def test_approval_override_and_untrusted_writes_annotation(tmp_path: Path) -> None:
    """Prefer tool overrides and refuse to trust project read-only annotations."""
    config = parse_server_config("fixture", {"url": "https://example.com/mcp", "default_approval_mode": "writes", "tool_approval_overrides": {"read": "never"}}, MCPConfigScope.PROJECT, tmp_path)
    read = MCPToolRecord("fixture", "read", "read", "", {"type": "object"}, annotations={"readOnlyHint": True})
    other = MCPToolRecord("fixture", "other", "other", "", {"type": "object"}, annotations={"readOnlyHint": True})
    resolver = MCPApprovalResolver()
    assert resolver.resolve(config, read).mode == MCPApprovalMode.NEVER
    assert resolver.resolve(config, other).decision.value == "ASK"  # type: ignore[union-attr]


def test_canonical_names_and_oauth_identity_do_not_collide(tmp_path: Path) -> None:
    """Keep punctuation variants and same-name resource servers isolated."""
    names = {canonical_tool_name("server", value) for value in ("foo-bar", "foo.bar", "foo_bar", "工具")}
    assert len(names) == 4
    assert all(len(name) <= 64 for name in names)
    first = parse_server_config("same", {"url": "https://EXAMPLE.com:443/a/", "auth_mode": "oauth", "oauth_scopes": ["read"]}, MCPConfigScope.USER, tmp_path)
    same = parse_server_config("same", {"url": "https://example.com/a", "auth_mode": "oauth", "oauth_scopes": ["read"]}, MCPConfigScope.USER, tmp_path)
    other = parse_server_config("same", {"url": "https://example.com/b", "auth_mode": "oauth", "oauth_scopes": ["read"]}, MCPConfigScope.USER, tmp_path)
    assert credential_identity(first).digest == credential_identity(same).digest
    assert credential_identity(first).digest != credential_identity(other).digest


def test_jsonschema_supports_ref_oneof_pattern_and_format() -> None:
    """Use the standard validator for composed schemas, references, patterns, and formats."""
    schema = {"$defs": {"id": {"type": "string", "pattern": "^[A-Z]+$"}}, "type": "object", "properties": {"id": {"$ref": "#/$defs/id"}, "target": {"oneOf": [{"type": "integer"}, {"type": "string", "format": "email"}]}}, "required": ["id", "target"]}
    validate_mcp_value({"id": "ABC", "target": "a@example.com"}, schema, label="input")
    with pytest.raises(ToolInputValidationError):
        validate_mcp_value({"id": "abc", "target": False}, schema, label="input")


def test_admin_policy_cannot_be_overridden_and_denies_domains(tmp_path: Path) -> None:
    """Apply machine server winners and domain denials before user configuration."""
    admin = tmp_path / "admin"
    user = tmp_path / "user"
    project = tmp_path / "project"
    admin.mkdir()
    user.mkdir()
    project.mkdir()
    (admin / "mcp.json").write_text(json.dumps({"policy": {"deniedHttpDomains": ["blocked.example"], "deniedTools": ["*delete*"]}, "mcpServers": {"managed": {"url": "https://managed.example/mcp"}}}), encoding="utf-8")
    (user / "mcp.json").write_text(json.dumps({"mcpServers": {"managed": {"url": "https://override.example/mcp"}, "blocked": {"url": "https://blocked.example/mcp"}}}), encoding="utf-8")
    trust = resolve_project_trust(WorkspaceTrustState.TRUSTED, TrustDecisionSource.DEFAULT, guidance_requires_trust=True, skills_require_trust=True, mcp_requires_trust=True)
    resolved = MCPConfigResolver(project, trust, user_root=user, admin_root=admin).resolve()
    assert [(item.name, item.url) for item in resolved.servers] == [("managed", "https://managed.example/mcp")]
    assert [item.name for item in resolved.blocked] == ["blocked"]
    assert resolved.servers[0].disabled_tools == ("*delete*",)


async def test_disclosure_is_turn_local_and_auto_uses_budget() -> None:
    """Expire searched tools after a turn and resolve AUTO from the schema budget."""
    runtime = MCPRuntime(SimpleNamespace(servers=(), blocked=(), diagnostics=()), (), disclosure_mode="auto", max_estimated_input_tokens=100, char_to_token_ratio=1, max_tool_context_ratio=0.1)
    record = MCPToolRecord("server", "large", canonical_tool_name("server", "large"), "x" * 100, {"type": "object", "description": "y" * 100})
    connection = SimpleNamespace(tools=(record,), resources=(), prompts=(), status=MCPServerStatus.READY, config=SimpleNamespace(always_load_tools=False, default_approval_mode="inherit", tool_approval_overrides=(), trusted=True, tool_timeout_seconds=60, scope=MCPConfigScope.USER))
    runtime.manager.connections = {"server": connection}
    registry = ToolRegistry()
    runtime.register_tools(registry, turn_id_provider=lambda: "turn-1")
    assert runtime.effective_disclosure_mode == "search"
    await registry.get("mcp_search_tools").executor({"query": "large"})
    assert record.canonical_name in runtime.effective_tool_names("turn-1", [record.canonical_name])
    runtime.finish_turn("turn-1")
    assert record.canonical_name not in runtime.effective_tool_names("turn-2", [record.canonical_name])
