from __future__ import annotations

from agent_harness.domain.errors import ToolNotFoundError
from agent_harness.domain.tools import ToolDefinition


class ToolRegistry:
    def __init__(self) -> None:
        """Create an empty registry keyed by unique tool name."""
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        """Register one tool definition and reject duplicate names."""
        if definition.name in self._tools:
            raise ValueError(f"Tool already registered: {definition.name}")
        if not definition.description:
            raise ValueError("Tool description is required")
        self._tools[definition.name] = definition

    def get(self, name: str) -> ToolDefinition:
        """Return a registered tool definition or raise a recoverable tool error."""
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(f"Unknown tool: {name}", details={"tool": name}) from exc

    def list(self) -> list[ToolDefinition]:
        """Return all tool definitions in stable name order."""
        return [self._tools[name] for name in sorted(self._tools)]

    def names(self) -> set[str]:
        """Return all registered tool names for agent definition validation."""
        return set(self._tools)

    def export_schemas(self, enabled_tools: list[str] | None = None) -> list[dict]:
        """Export model-visible schemas for all tools or an enabled subset."""
        names = enabled_tools if enabled_tools is not None else sorted(self._tools)
        return [self.get(name).to_model_schema() for name in names]
