from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agent_harness.mcp.models import MCPServerConfig, MCPToolRecord
from agent_harness.security.models import PermissionDecision


class MCPApprovalMode(StrEnum):
    """Supported MCP-specific approval modes."""

    INHERIT = "inherit"
    ALWAYS = "always"
    WRITES = "writes"
    NEVER = "never"


@dataclass(frozen=True, slots=True)
class MCPApprovalDecision:
    """Auditable MCP policy decision applied after permission hard boundaries."""

    mode: MCPApprovalMode
    decision: PermissionDecision | None
    reason: str


class MCPApprovalResolver:
    """Resolve per-tool overrides and trusted read annotations."""

    def resolve(self, server: MCPServerConfig, tool: MCPToolRecord) -> MCPApprovalDecision:
        """Resolve one tool without allowing MCP metadata to bypass host denials."""
        configured = dict(server.tool_approval_overrides).get(tool.remote_name, server.default_approval_mode)
        mode = MCPApprovalMode(configured)
        if mode == MCPApprovalMode.ALWAYS:
            return MCPApprovalDecision(mode, PermissionDecision.ASK, "MCP approval mode always")
        if mode == MCPApprovalMode.NEVER:
            return MCPApprovalDecision(mode, PermissionDecision.ALLOW, "MCP layer adds no approval")
        if mode == MCPApprovalMode.WRITES:
            annotations = tool.annotations
            safe_read = server.trusted and annotations.get("readOnlyHint") is True and annotations.get("destructiveHint") is not True
            return MCPApprovalDecision(mode, PermissionDecision.ALLOW if safe_read else PermissionDecision.ASK, "Trusted read-only MCP annotation" if safe_read else "MCP write or untrusted annotation")
        return MCPApprovalDecision(mode, None, "Inherited host permission policy")
