from __future__ import annotations

import asyncio
import json
import os
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, cast

import httpx
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.client.session import ListRootsFnT, MessageHandlerFnT, RequestContext
from mcp.shared.session import RequestResponder
from pydantic import AnyUrl, FileUrl

from agent_harness.mcp.config import forwarded_environment
from agent_harness.mcp.auth import create_oauth_provider
from agent_harness.mcp.errors import MCPConnectionError
from agent_harness.mcp.models import MCPPromptRecord, MCPResourceRecord, MCPServerConfig, MCPServerSnapshot, MCPServerStatus, MCPToolRecord, MCPTransport

AuditSink = Callable[[str, dict[str, Any]], None]


class MCPServerConnection:
    """Own one official SDK ClientSession and its transport for a thread lifetime."""

    def __init__(self, config: MCPServerConfig, roots: tuple[Path, ...], audit: AuditSink | None = None) -> None:
        """Initialize disconnected state without starting a process or network request."""
        self.config = config
        self.roots = tuple(root.resolve() for root in roots)
        self.audit = audit
        self.status = MCPServerStatus.NOT_CONNECTED if config.enabled else MCPServerStatus.DISABLED
        self.session: ClientSession | None = None
        self.initialize_result: types.InitializeResult | None = None
        self._stack: AsyncExitStack | None = None
        self._http_client: httpx.AsyncClient | None = None
        self.tools: tuple[MCPToolRecord, ...] = ()
        self.resources: tuple[MCPResourceRecord, ...] = ()
        self.prompts: tuple[MCPPromptRecord, ...] = ()
        self.error: str | None = None

    async def connect(self) -> None:
        """Open transport, initialize the protocol, and discover declared catalogs."""
        if not self.config.enabled:
            return
        self.status = MCPServerStatus.CONNECTING
        self._emit("mcp.server_connecting", {"server": self.config.name, "transport": self.config.transport.value})
        stack = AsyncExitStack()
        self._stack = stack
        try:
            read, write = await asyncio.wait_for(self._open_transport(stack), timeout=self.config.startup_timeout_seconds)
            session = ClientSession(
                read,
                write,
                read_timeout_seconds=timedelta(seconds=self.config.tool_timeout_seconds),
                list_roots_callback=cast(ListRootsFnT, self._list_roots),
                message_handler=cast(MessageHandlerFnT, self._handle_message),
            )
            self.session = await stack.enter_async_context(session)
            self.initialize_result = await asyncio.wait_for(self.session.initialize(), timeout=self.config.startup_timeout_seconds)
            await self.refresh_catalogs()
            self.status = MCPServerStatus.READY
            self._emit("mcp.server_ready", {"server": self.config.name, "tool_count": len(self.tools), "resource_count": len(self.resources), "prompt_count": len(self.prompts)})
        except BaseException as exc:
            self.error = str(exc)
            self.status = MCPServerStatus.FAILED
            await stack.aclose()
            self._stack = None
            self.session = None
            self._emit("mcp.server_failed", {"server": self.config.name, "error": self.error})
            raise MCPConnectionError(f"MCP server {self.config.name} failed: {exc}") from exc

    async def refresh_catalogs(self) -> None:
        """Refresh only catalogs supported by the negotiated server capabilities."""
        if not self.session or not self.initialize_result:
            raise MCPConnectionError(f"MCP server {self.config.name} is not initialized")
        capabilities = self.initialize_result.capabilities
        if capabilities.tools is not None:
            tool_result = await self.session.list_tools()
            self.tools = tuple(self._tool_record(tool) for tool in tool_result.tools if self._tool_enabled(tool.name))
        if capabilities.resources is not None:
            resource_result = await self.session.list_resources()
            self.resources = tuple(MCPResourceRecord(self.config.name, str(item.uri), item.name, item.description or "", item.mimeType) for item in resource_result.resources)
        if capabilities.prompts is not None:
            prompt_result = await self.session.list_prompts()
            self.prompts = tuple(MCPPromptRecord(self.config.name, item.name, item.description or "", tuple(arg.model_dump(by_alias=True, exclude_none=True) for arg in (item.arguments or []))) for item in prompt_result.prompts)
        self._emit("mcp.catalog_refreshed", {"server": self.config.name})

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any] | str:
        """Call one MCP tool exactly once and normalize all standard content types."""
        session = self._require_session()
        self._emit("mcp.tool_call_started", {"server": self.config.name, "tool": name})
        result = await asyncio.wait_for(session.call_tool(name, arguments), timeout=self.config.tool_timeout_seconds)
        normalized = _normalize_tool_result(result)
        self._emit("mcp.tool_call_completed", {"server": self.config.name, "tool": name, "is_error": result.isError})
        if result.isError:
            raise MCPConnectionError(str(normalized))
        return normalized

    async def read_resource(self, uri: str) -> dict[str, Any]:
        """Read one server resource and return a JSON-safe payload."""
        result = await self._require_session().read_resource(AnyUrl(uri))
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def get_prompt(self, name: str, arguments: dict[str, str] | None = None) -> dict[str, Any]:
        """Resolve one prompt template into protocol messages."""
        result = await self._require_session().get_prompt(name, arguments)
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def close(self) -> None:
        """Gracefully close the SDK session and underlying transport resources."""
        if not self._stack:
            return
        self.status = MCPServerStatus.STOPPING
        try:
            await asyncio.wait_for(self._stack.aclose(), timeout=self.config.cleanup_timeout_seconds)
        finally:
            self._stack = None
            self.session = None
            self.status = MCPServerStatus.STOPPED
            self._emit("mcp.server_stopped", {"server": self.config.name})

    def snapshot(self) -> MCPServerSnapshot:
        """Build a credential-free server snapshot suitable for thread persistence."""
        result = self.initialize_result
        return MCPServerSnapshot(
            self.config.name,
            self.status,
            self.config.scope,
            self.config.transport,
            self.config.config_hash,
            str(result.protocolVersion) if result else None,
            result.instructions if result else None,
            result.capabilities.model_dump(mode="json", by_alias=True, exclude_none=True) if result else {},
            self.error,
        )

    async def _open_transport(self, stack: AsyncExitStack) -> tuple[Any, Any]:
        """Enter the configured official SDK transport context."""
        if self.config.transport == MCPTransport.STDIO:
            params = StdioServerParameters(command=self.config.command or "", args=list(self.config.args), cwd=self.config.cwd, env=forwarded_environment(self.config))
            return await stack.enter_async_context(stdio_client(params))
        headers = dict(self.config.headers)
        if self.config.bearer_token_env_var:
            token = os.getenv(self.config.bearer_token_env_var)
            if not token:
                raise MCPConnectionError(f"Missing bearer token environment variable: {self.config.bearer_token_env_var}")
            headers["Authorization"] = f"Bearer {token}"
        auth = create_oauth_provider(self.config) if self.config.auth_mode == "oauth" else None
        self._http_client = await stack.enter_async_context(httpx.AsyncClient(headers=headers, auth=auth, follow_redirects=False, timeout=self.config.tool_timeout_seconds))
        read, write, _ = await stack.enter_async_context(streamable_http_client(self.config.url or "", http_client=self._http_client))
        return read, write

    async def _list_roots(self, _context: RequestContext[ClientSession, Any]) -> types.ListRootsResult | types.ErrorData:
        """Expose only the thread workspace roots already approved by the host."""
        return types.ListRootsResult(roots=[types.Root(uri=FileUrl(root.as_uri()), name=root.name) for root in self.roots])

    async def _handle_message(
        self,
        message: RequestResponder[types.ServerRequest, types.ClientResult] | types.ServerNotification | Exception,
    ) -> None:
        """Refresh catalogs when a server sends a negotiated list-changed notification."""
        if isinstance(message, Exception):
            self._emit("mcp.server_message_error", {"server": self.config.name, "error": str(message)})
            return
        notification = getattr(message, "root", message)
        if isinstance(notification, (types.ToolListChangedNotification, types.ResourceListChangedNotification, types.PromptListChangedNotification)):
            self._emit("mcp.catalog_list_changed", {"server": self.config.name, "notification": type(notification).__name__})
            asyncio.create_task(self._refresh_after_notification())

    async def _refresh_after_notification(self) -> None:
        """Refresh catalogs outside the SDK notification dispatch call stack."""
        try:
            await self.refresh_catalogs()
        except Exception as exc:
            self.status = MCPServerStatus.DEGRADED
            self.error = str(exc)
            self._emit("mcp.catalog_refresh_failed", {"server": self.config.name, "error": self.error})

    def _tool_record(self, tool: types.Tool) -> MCPToolRecord:
        """Convert one SDK tool model into the Harness catalog record."""
        return MCPToolRecord(
            self.config.name,
            tool.name,
            canonical_tool_name(self.config.name, tool.name),
            tool.description or "MCP tool",
            dict(tool.inputSchema),
            dict(tool.outputSchema) if tool.outputSchema else None,
            tool.annotations.model_dump(by_alias=True, exclude_none=True) if tool.annotations else {},
        )

    def _tool_enabled(self, name: str) -> bool:
        """Apply allowlist first and denylist second to remote tool names."""
        return (not self.config.enabled_tools or name in self.config.enabled_tools) and name not in self.config.disabled_tools

    def _require_session(self) -> ClientSession:
        """Return the ready SDK session or raise a stable runtime error."""
        if not self.session or self.status != MCPServerStatus.READY:
            raise MCPConnectionError(f"MCP server {self.config.name} is not ready")
        return self.session

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        """Emit a sanitized MCP lifecycle or operation event."""
        if self.audit:
            self.audit(event, payload)


def canonical_tool_name(server_name: str, tool_name: str) -> str:
    """Create a provider-compatible stable MCP tool name."""
    def sanitize(value: str) -> str:
        """Replace provider-invalid name characters with underscores."""
        return "".join(char if char.isalnum() or char == "_" else "_" for char in value)

    return f"mcp__{sanitize(server_name)}__{sanitize(tool_name)}"[:64]


def _normalize_tool_result(result: types.CallToolResult) -> dict[str, Any] | str:
    """Prefer structured content and otherwise serialize mixed standard content safely."""
    if result.structuredContent is not None:
        return result.structuredContent
    texts = [item.text for item in result.content if isinstance(item, types.TextContent)]
    if len(texts) == len(result.content):
        return "\n".join(texts)
    return json.dumps([item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in result.content], ensure_ascii=False)
