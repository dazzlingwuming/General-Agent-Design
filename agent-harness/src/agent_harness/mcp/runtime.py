from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any, Callable

from agent_harness.domain.tools import ToolDefinition
from agent_harness.mcp.config import MCPResolvedConfig
from agent_harness.mcp.manager import MCPServerManager
from agent_harness.mcp.approval_policy import MCPApprovalResolver
from agent_harness.mcp.models import MCPPromptRecord, MCPResourceRecord, MCPToolRecord
from agent_harness.security.models import Capability, RiskLevel, SideEffectType
from agent_harness.tools.registry import ToolRegistry


class MCPRuntime:
    """Provide thread-scoped MCP lifecycle, discovery, and internal tool adapters."""

    def __init__(self, resolved: MCPResolvedConfig, roots: tuple[Path, ...], audit: Callable[[str, dict[str, Any]], None] | None = None, *, connect_in_parallel: bool = True, max_parallel_connections: int = 4, disclosure_mode: str = "auto", max_estimated_input_tokens: int = 120000, char_to_token_ratio: float = 4.0, max_tool_context_ratio: float = 0.10) -> None:
        """Create a disconnected manager from trusted resolved configuration."""
        self.resolved = resolved
        self.manager = MCPServerManager(resolved.servers, roots, audit, max_parallel=max_parallel_connections, connect_in_parallel=connect_in_parallel)
        self.audit = audit
        self.disclosure_mode = disclosure_mode
        self.max_estimated_input_tokens = max_estimated_input_tokens
        self.char_to_token_ratio = char_to_token_ratio
        self.max_tool_context_ratio = max_tool_context_ratio
        self.always_loaded: set[str] = set()
        self.turn_loaded: dict[str, set[str]] = {}
        self.effective_disclosure_mode = "search"

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

    def register_tools(self, registry: ToolRegistry, *, eager: bool = False, turn_id_provider: Callable[[], str] | None = None) -> list[str]:
        """Register eager remote adapters and progressive catalog helper tools."""
        names: list[str] = []
        configured_mode = "eager" if eager else self.disclosure_mode
        self.effective_disclosure_mode = self._resolve_disclosure_mode(configured_mode)
        selected = self.tools() if self.effective_disclosure_mode == "eager" else tuple(tool for tool in self.tools() if self.manager.connections[tool.server_name].config.always_load_tools)
        self.always_loaded.update(item.canonical_name for item in selected)
        for record in self.tools():
            registry.register(self._adapter(record))
            if record.canonical_name in self.always_loaded:
                names.append(record.canonical_name)
        registry.register(self._search_tool(turn_id_provider or (lambda: "turn_unknown")))
        registry.register(self._resource_tool())
        return [*names, "mcp_search_tools", "mcp_read_resource"]

    def effective_tool_names(self, turn_id: str, names: list[str]) -> list[str]:
        """Hide deferred MCP schemas until search loads them for the current runtime."""
        remote = {item.canonical_name for item in self.tools()}
        loaded = self.always_loaded | self.turn_loaded.get(turn_id, set())
        return [name for name in names if name not in remote or name in loaded]

    def finish_turn(self, turn_id: str) -> None:
        """Drop schemas activated only for the completed turn."""
        removed = self.turn_loaded.pop(turn_id, set())
        for name in sorted(removed):
            self._emit("mcp.tool_deactivated", {"turn_id": turn_id, "canonical_tool": name})

    def register_delegated_tools(self, registry: ToolRegistry, allowed_names: tuple[str, ...]) -> list[str]:
        """Register only an explicitly delegated MCP subset while sharing thread connections."""
        records = {item.canonical_name: item for item in self.tools()}
        selected = [name for name in allowed_names if name in records]
        for name in selected:
            registry.register(self._adapter(records[name]))
        return selected

    def _adapter(self, record: MCPToolRecord) -> ToolDefinition:
        """Adapt one remote tool behind the existing ToolRuntime security boundary."""
        async def execute(arguments: dict[str, Any]) -> dict[str, Any] | str:
            """Delegate one validated call to the owning ready MCP connection."""
            return await self.manager.active_servers[record.server_name].call_tool(record.remote_name, arguments)

        server = self.manager.connections[record.server_name].config
        approval = MCPApprovalResolver().resolve(server, record)
        return ToolDefinition(
            record.canonical_name,
            f"[{record.server_name}] {record.description}",
            record.input_schema,
            execute,
            output_schema=None,  # The connection validates structuredContent before normalization.
            timeout_seconds=int(self.manager.connections[record.server_name].config.tool_timeout_seconds),
            risk_level=(
                RiskLevel.HIGH
                if self.manager.connections[record.server_name].config.scope.value == "project"
                or self.manager.connections[record.server_name].config.default_approval_mode in {"always", "writes"}
                else RiskLevel.MEDIUM
            ),
            side_effect=SideEffectType.EXTERNAL,
            required_capabilities=frozenset({Capability.MCP_TOOL_CALL, Capability.NETWORK_ACCESS, Capability.EXTERNAL_SIDE_EFFECT}),
            metadata={"mcp_server": record.server_name, "mcp_remote_tool": record.remote_name, "mcp_approval_mode": approval.mode.value, "mcp_approval_decision": approval.decision.value if approval.decision else None, "mcp_annotations": record.annotations, "mcp_annotation_trusted": server.trusted},
        )

    def _search_tool(self, turn_id_provider: Callable[[], str]) -> ToolDefinition:
        """Create a progressive disclosure tool that loads matching remote schemas."""
        async def search(arguments: dict[str, Any]) -> dict[str, Any]:
            """Search name and description, register matches, and return full schemas."""
            query = str(arguments["query"]).casefold()
            limit = max(1, min(int(arguments.get("limit", 5)), 20))
            server_filter = str(arguments["server"]) if arguments.get("server") else None
            matches = [item for item in self.tools() if (not server_filter or item.server_name == server_filter) and (query in item.remote_name.casefold() or query in item.description.casefold())]
            matches.sort(key=lambda item: (item.server_name.casefold(), item.remote_name.casefold()))
            matches = matches[:limit]
            turn_id = turn_id_provider()
            loaded = self.turn_loaded.setdefault(turn_id, set())
            for item in matches:
                loaded.add(item.canonical_name)
                self._emit("mcp.tool_activated", {"turn_id": turn_id, "server": item.server_name, "canonical_tool": item.canonical_name})
            return {"tools": [{"name": item.canonical_name, "server": item.server_name, "description": item.description, "input_schema": item.input_schema, "approval_mode": MCPApprovalResolver().resolve(self.manager.connections[item.server_name].config, item).mode.value} for item in matches]}

        return ToolDefinition(
            "mcp_search_tools",
            "搜索可用 MCP 工具并按需加载其完整参数定义。",
            {"type": "object", "properties": {"query": {"type": "string"}, "server": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, "required": ["query"], "additionalProperties": False},
            search,
            required_capabilities=frozenset({Capability.MCP_TOOL_CALL}),
        )

    def _resolve_disclosure_mode(self, configured_mode: str) -> str:
        """Resolve AUTO against the configured ten-percent tool schema budget."""
        if configured_mode in {"eager", "search"}:
            return configured_mode
        serialized = json.dumps([{"name": item.canonical_name, "description": item.description, "input": item.input_schema, "output": item.output_schema} for item in self.tools()], ensure_ascii=False, separators=(",", ":"))
        estimated = int(len(serialized) / max(self.char_to_token_ratio, 0.1))
        budget = int(self.max_estimated_input_tokens * self.max_tool_context_ratio)
        effective = "eager" if estimated <= budget else "search"
        self._emit("mcp.tool_disclosure_resolved", {"configured_mode": configured_mode, "effective_mode": effective, "estimated_tool_tokens": estimated, "tool_budget_tokens": budget})
        return effective

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        """Emit one MCP runtime event through the configured audit sink."""
        if self.audit:
            self.audit(event, payload)

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
