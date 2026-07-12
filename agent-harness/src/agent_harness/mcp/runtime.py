from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from agent_harness.domain.tools import ToolDefinition
from agent_harness.mcp.config import MCPResolvedConfig
from agent_harness.mcp.manager import MCPServerManager
from agent_harness.mcp.models import MCPPromptRecord, MCPResourceRecord, MCPToolRecord
from agent_harness.security.models import Capability, RiskLevel, SideEffectType
from agent_harness.tools.registry import ToolRegistry


class MCPRuntime:
    """Provide thread-scoped MCP lifecycle, discovery, and internal tool adapters."""

    def __init__(self, resolved: MCPResolvedConfig, roots: tuple[Path, ...], audit: Callable[[str, dict[str, Any]], None] | None = None) -> None:
        """Create a disconnected manager from trusted resolved configuration."""
        self.resolved = resolved
        self.manager = MCPServerManager(resolved.servers, roots, audit)
        self.audit = audit
        self.loaded_tool_names: set[str] = set()

    async def start(self) -> None:
        """Initialize all configured MCP servers for reuse across turns."""
        await self.manager.start()

    async def close(self) -> None:
        """Release all server sessions when the owning thread runtime closes."""
        await self.manager.shutdown()

    def tools(self) -> tuple[MCPToolRecord, ...]:
        """Return all filtered tool records from active servers."""
        return tuple(tool for connection in self.manager.active_servers.values() for tool in connection.tools)

    def resources(self) -> tuple[MCPResourceRecord, ...]:
        """Return resource metadata from active capable servers."""
        return tuple(item for connection in self.manager.active_servers.values() for item in connection.resources)

    def prompts(self) -> tuple[MCPPromptRecord, ...]:
        """Return prompt metadata from active capable servers."""
        return tuple(item for connection in self.manager.active_servers.values() for item in connection.prompts)

    def register_tools(self, registry: ToolRegistry, *, eager: bool = False) -> list[str]:
        """Register eager remote adapters and progressive catalog helper tools."""
        names: list[str] = []
        selected = self.tools() if eager else tuple(tool for tool in self.tools() if self.manager.connections[tool.server_name].config.always_load_tools)
        self.loaded_tool_names.update(item.canonical_name for item in selected)
        for record in self.tools():
            registry.register(self._adapter(record))
            if record.canonical_name in self.loaded_tool_names:
                names.append(record.canonical_name)
        registry.register(self._search_tool(registry))
        registry.register(self._resource_tool())
        registry.register(self._prompt_tool())
        return [*names, "mcp_search_tools", "mcp_read_resource", "mcp_get_prompt"]

    def effective_tool_names(self, names: list[str]) -> list[str]:
        """Hide deferred MCP schemas until search loads them for the current runtime."""
        remote = {item.canonical_name for item in self.tools()}
        return [name for name in names if name not in remote or name in self.loaded_tool_names]

    def _adapter(self, record: MCPToolRecord) -> ToolDefinition:
        """Adapt one remote tool behind the existing ToolRuntime security boundary."""
        async def execute(arguments: dict[str, Any]) -> dict[str, Any] | str:
            """Delegate one validated call to the owning ready MCP connection."""
            return await self.manager.active_servers[record.server_name].call_tool(record.remote_name, arguments)

        return ToolDefinition(
            record.canonical_name,
            f"[{record.server_name}] {record.description}",
            record.input_schema,
            execute,
            output_schema=record.output_schema,
            timeout_seconds=int(self.manager.connections[record.server_name].config.tool_timeout_seconds),
            risk_level=(
                RiskLevel.HIGH
                if self.manager.connections[record.server_name].config.scope.value == "project"
                or self.manager.connections[record.server_name].config.default_approval_mode in {"always", "writes"}
                else RiskLevel.MEDIUM
            ),
            side_effect=SideEffectType.EXTERNAL,
            required_capabilities=frozenset({Capability.MCP_TOOL_CALL, Capability.NETWORK_ACCESS, Capability.EXTERNAL_SIDE_EFFECT}),
        )

    def _search_tool(self, registry: ToolRegistry) -> ToolDefinition:
        """Create a progressive disclosure tool that loads matching remote schemas."""
        async def search(arguments: dict[str, Any]) -> dict[str, Any]:
            """Search name and description, register matches, and return full schemas."""
            query = str(arguments["query"]).casefold()
            limit = max(1, min(int(arguments.get("limit", 5)), 20))
            matches = [item for item in self.tools() if query in item.remote_name.casefold() or query in item.description.casefold()][:limit]
            for item in matches:
                self.loaded_tool_names.add(item.canonical_name)
            return {"tools": [{"name": item.canonical_name, "server": item.server_name, "description": item.description, "input_schema": item.input_schema} for item in matches]}

        return ToolDefinition(
            "mcp_search_tools",
            "搜索可用 MCP 工具并按需加载其完整参数定义。",
            {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, "required": ["query"], "additionalProperties": False},
            search,
            required_capabilities=frozenset({Capability.MCP_TOOL_CALL}),
        )

    def _resource_tool(self) -> ToolDefinition:
        """Create a model-visible resource reader routed through ToolRuntime."""
        async def read(arguments: dict[str, Any]) -> dict[str, Any]:
            """Read a URI only from the explicitly selected active server."""
            return await self.manager.active_servers[str(arguments["server"])].read_resource(str(arguments["uri"]))

        return ToolDefinition(
            "mcp_read_resource",
            "读取指定 MCP Server 暴露的资源。",
            {"type": "object", "properties": {"server": {"type": "string"}, "uri": {"type": "string"}}, "required": ["server", "uri"], "additionalProperties": False},
            read,
            risk_level=RiskLevel.MEDIUM,
            side_effect=SideEffectType.NETWORK,
            required_capabilities=frozenset({Capability.MCP_TOOL_CALL, Capability.NETWORK_ACCESS}),
        )

    def _prompt_tool(self) -> ToolDefinition:
        """Create a prompt retrieval tool without automatically injecting prompt text."""
        async def get(arguments: dict[str, Any]) -> dict[str, Any]:
            """Fetch one prompt only after an explicit tool call."""
            raw = arguments.get("arguments")
            values = {str(key): str(value) for key, value in raw.items()} if isinstance(raw, dict) else None
            return await self.manager.active_servers[str(arguments["server"])].get_prompt(str(arguments["name"]), values)

        return ToolDefinition(
            "mcp_get_prompt",
            "获取指定 MCP Server 的 Prompt 模板结果。",
            {"type": "object", "properties": {"server": {"type": "string"}, "name": {"type": "string"}, "arguments": {"type": "object"}}, "required": ["server", "name"], "additionalProperties": False},
            get,
            required_capabilities=frozenset({Capability.MCP_TOOL_CALL, Capability.NETWORK_ACCESS}),
        )

    def status_rows(self) -> list[dict[str, Any]]:
        """Return sanitized lifecycle and catalog summaries for CLI display."""
        rows = []
        for connection in self.manager.connections.values():
            snapshot = asdict(connection.snapshot())
            snapshot.pop("capabilities", None)
            snapshot["tool_count"] = len(connection.tools)
            snapshot["resource_count"] = len(connection.resources)
            snapshot["prompt_count"] = len(connection.prompts)
            rows.append(snapshot)
        return rows
