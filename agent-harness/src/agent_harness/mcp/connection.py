from __future__ import annotations

import asyncio
import hashlib
import os
from fnmatch import fnmatch
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
from agent_harness.mcp.auth import credential_identity
from agent_harness.mcp.errors import MCPConnectionError, MCPProtocolError, MCPToolExecutionError, MCPToolOutcomeUnknown, MCPTransportError
from agent_harness.mcp.naming import canonical_tool_name
from agent_harness.mcp.pagination import collect_paginated
from agent_harness.mcp.results import normalize_tool_result
from agent_harness.mcp.schema_validation import check_mcp_schema, validate_mcp_value
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
        self.resource_templates: tuple[dict[str, Any], ...] = ()
        self.error: str | None = None
        self.catalog_stale = False
        self.catalog_page_count = 0
        self.catalog_truncated = False
        self._refresh_lock = asyncio.Lock()
        self._reconnect_lock = asyncio.Lock()
        self._closing = False

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
        except asyncio.CancelledError:
            self.status = MCPServerStatus.STOPPING
            await stack.aclose()
            self._stack = None
            self.session = None
            self._emit("mcp.server_connect_cancelled", {"server": self.config.name})
            raise
        except Exception as exc:
            self.error = str(exc)
            self.status = MCPServerStatus.FAILED
            await stack.aclose()
            self._stack = None
            self.session = None
            self._emit("mcp.server_failed", {"server": self.config.name, "error": self.error})
            raise MCPConnectionError(f"MCP server {self.config.name} failed: {exc}") from exc

    async def refresh_catalogs(self) -> None:
        """Refresh only catalogs supported by the negotiated server capabilities."""
        async with self._refresh_lock:
            if not self.session or not self.initialize_result:
                raise MCPConnectionError(f"MCP server {self.config.name} is not initialized")
            self._emit("mcp.catalog_refresh_started", {"server": self.config.name})
            capabilities = self.initialize_result.capabilities
            page_counts: list[int] = []
            truncated = False
            try:
                if capabilities.tools is not None:
                    raw_tools, pages, cut = await collect_paginated(lambda cursor: self._require_session().list_tools(cursor=cursor), get_items=lambda page: page.tools, get_next_cursor=lambda page: page.nextCursor, max_pages=100, max_items=2000, on_page=self._page_loaded)
                    records = tuple(self._tool_record(tool) for tool in raw_tools if self._tool_enabled(tool.name))
                    self._assert_unique_names(records)
                    self.tools = records
                    page_counts.append(pages)
                    truncated |= cut
                if capabilities.resources is not None:
                    raw_resources, pages, cut = await collect_paginated(lambda cursor: self._require_session().list_resources(cursor=cursor), get_items=lambda page: page.resources, get_next_cursor=lambda page: page.nextCursor, max_pages=100, max_items=5000, on_page=self._page_loaded)
                    self.resources = tuple(MCPResourceRecord(self.config.name, str(item.uri), item.name, item.description or "", item.mimeType) for item in raw_resources)
                    page_counts.append(pages)
                    truncated |= cut
                    raw_templates, pages, cut = await collect_paginated(lambda cursor: self._require_session().list_resource_templates(cursor=cursor), get_items=lambda page: page.resourceTemplates, get_next_cursor=lambda page: page.nextCursor, max_pages=100, max_items=5000, on_page=self._page_loaded)
                    self.resource_templates = tuple(item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in raw_templates)
                    page_counts.append(pages)
                    truncated |= cut
                if capabilities.prompts is not None:
                    raw_prompts, pages, cut = await collect_paginated(lambda cursor: self._require_session().list_prompts(cursor=cursor), get_items=lambda page: page.prompts, get_next_cursor=lambda page: page.nextCursor, max_pages=100, max_items=1000, on_page=self._page_loaded)
                    self.prompts = tuple(MCPPromptRecord(self.config.name, item.name, item.description or "", tuple(arg.model_dump(by_alias=True, exclude_none=True) for arg in (item.arguments or []))) for item in raw_prompts)
                    page_counts.append(pages)
                    truncated |= cut
            except asyncio.CancelledError:
                self._emit("mcp.catalog_refresh_cancelled", {"server": self.config.name})
                raise
            self.catalog_page_count = sum(page_counts)
            self.catalog_truncated = truncated
            self.catalog_stale = False
            if truncated:
                self.status = MCPServerStatus.DEGRADED
                self._emit("mcp.catalog_truncated", {"server": self.config.name})
            self._emit("mcp.catalog_refresh_completed", {"server": self.config.name, "pages": self.catalog_page_count})

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call one MCP tool exactly once and normalize all standard content types."""
        if self.catalog_stale:
            await self.refresh_catalogs()
        record = next((item for item in self.tools if item.remote_name == name), None)
        if record:
            validate_mcp_value(arguments, record.input_schema, label="input")
        self._emit("mcp.tool_call_started", {"server": self.config.name, "tool": name})
        try:
            result = await asyncio.wait_for(self._require_session().call_tool(name, arguments), timeout=self.config.tool_timeout_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._is_session_not_found(exc):
                await self._reinitialize("session_not_found")
                self._emit("mcp.tool_outcome_unknown", {"server": self.config.name, "tool": name})
                raise MCPToolOutcomeUnknown("MCP session expired during tool execution; the outcome is unknown and was not retried") from exc
            raise MCPTransportError(str(exc)) from exc
        normalized = normalize_tool_result(result)
        self._emit("mcp.tool_call_completed", {"server": self.config.name, "tool": name, "is_error": result.isError})
        if result.isError:
            self._emit("mcp.tool_execution_error", {"server": self.config.name, "tool": name})
            raise MCPToolExecutionError(normalized.error_message(), details={"mcp_result": normalized.model_payload()})
        payload = normalized.model_payload()
        if record and record.output_schema and normalized.structured_content is not None:
            validate_mcp_value(normalized.structured_content, record.output_schema, label="output")
        return payload

    async def read_resource(self, uri: str) -> dict[str, Any]:
        """Read one server resource and return a JSON-safe payload."""
        result = await self._execute_with_session_recovery(lambda: self._require_session().read_resource(AnyUrl(uri)))
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def get_prompt(self, name: str, arguments: dict[str, str] | None = None) -> dict[str, Any]:
        """Resolve one prompt template into protocol messages."""
        result = await self._execute_with_session_recovery(lambda: self._require_session().get_prompt(name, arguments))
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def close(self) -> None:
        """Gracefully close the SDK session and underlying transport resources."""
        if not self._stack:
            return
        self._closing = True
        self.status = MCPServerStatus.STOPPING
        try:
            await asyncio.wait_for(self._stack.aclose(), timeout=self.config.cleanup_timeout_seconds)
        finally:
            self._stack = None
            self.session = None
            self.status = MCPServerStatus.STOPPED
            self._closing = False
            self._emit("mcp.server_stopped", {"server": self.config.name})

    def snapshot(self) -> MCPServerSnapshot:
        """Build a credential-free server snapshot suitable for thread persistence."""
        result = self.initialize_result
        catalog_payload = [{"canonical_name": item.canonical_name, "remote_name": item.remote_name, "server_name": item.server_name} for item in self.tools]
        catalog_hash = hashlib.sha256(json_bytes(catalog_payload)).hexdigest()
        instructions = result.instructions if result and result.instructions else ""
        identity_hash = credential_identity(self.config).digest if self.config.auth_mode == "oauth" and self.config.url else None
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
            self.catalog_page_count,
            self.catalog_truncated,
            catalog_hash,
            hashlib.sha256(instructions.encode()).hexdigest() if instructions else "",
            len(instructions),
            identity_hash,
            tuple({**item, "name_hash": item["canonical_name"].rsplit("__", 1)[-1]} for item in catalog_payload),
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
            self.catalog_stale = True
            self._emit("mcp.catalog_stale", {"server": self.config.name})

    def _tool_record(self, tool: types.Tool) -> MCPToolRecord:
        """Convert one SDK tool model into the Harness catalog record."""
        check_mcp_schema(dict(tool.inputSchema))
        if tool.outputSchema:
            check_mcp_schema(dict(tool.outputSchema))
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
        allowed = not self.config.enabled_tools or any(fnmatch(name, pattern) for pattern in self.config.enabled_tools)
        denied = any(fnmatch(name, pattern) for pattern in self.config.disabled_tools)
        return allowed and not denied

    def _require_session(self) -> ClientSession:
        """Return the ready SDK session or raise a stable runtime error."""
        if not self.session or self.status not in {MCPServerStatus.CONNECTING, MCPServerStatus.RECONNECTING, MCPServerStatus.READY, MCPServerStatus.DEGRADED}:
            raise MCPConnectionError(f"MCP server {self.config.name} is not ready")
        return self.session

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        """Emit a sanitized MCP lifecycle or operation event."""
        if self.audit:
            self.audit(event, payload)

    async def _execute_with_session_recovery(self, operation: Callable[[], Any]) -> Any:
        """Retry one read-only operation once after rebuilding an expired HTTP session."""
        try:
            return await operation()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._is_session_not_found(exc):
                raise MCPTransportError(str(exc)) from exc
            await self._reinitialize("session_not_found")
            return await operation()

    async def _reinitialize(self, reason: str) -> None:
        """Serialize a fresh transport, initialize handshake, and catalog snapshot."""
        async with self._reconnect_lock:
            self._emit("mcp.session_expired", {"server": self.config.name, "reason": reason})
            await self.close()
            self.status = MCPServerStatus.RECONNECTING
            await self.connect()
            self._emit("mcp.session_reinitialized", {"server": self.config.name, "reason": reason})

    def _is_session_not_found(self, exc: BaseException) -> bool:
        """Recognize HTTP 404 from typed exceptions, chained responses, then compatibility text."""
        current: BaseException | None = exc
        while current:
            response = getattr(current, "response", None)
            if getattr(response, "status_code", None) == 404 or getattr(current, "status_code", None) == 404:
                return True
            current = current.__cause__ or current.__context__
        message = str(exc).casefold()
        return "404" in message and any(marker in message for marker in ("session", "not found", "invalid"))

    def _assert_unique_names(self, records: tuple[MCPToolRecord, ...]) -> None:
        """Reject a catalog that maps distinct remote tools to one provider name."""
        mapped: dict[str, str] = {}
        for record in records:
            previous = mapped.setdefault(record.canonical_name, record.remote_name)
            if previous != record.remote_name:
                raise MCPProtocolError(f"Canonical MCP tool name collision: {record.canonical_name}")

    def _page_loaded(self, page: int, cursor: str | None, item_count: int) -> None:
        """Emit one credential-free catalog pagination event."""
        self._emit("mcp.catalog_page_loaded", {"server": self.config.name, "page": page, "cursor": cursor, "item_count": item_count})


def json_bytes(value: Any) -> bytes:
    """Serialize snapshot metadata deterministically for hashing."""
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
