from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_harness.domain.tools import ToolDefinition
from agent_harness.security.models import ApprovalPolicy, PermissionDecision, PermissionEvaluation, SandboxMode, SandboxPolicy, ToolExecutionPrincipal
from agent_harness.security.path_policy import FileSystemPolicy
from agent_harness.security.rules import PermissionRule, resolve_rule_decision


@dataclass(slots=True)
class PermissionEngine:
    """Evaluate hard boundaries, explicit rules, and profile defaults for one tool call."""

    rules: list[PermissionRule] = field(default_factory=list)

    def evaluate(self, principal: ToolExecutionPrincipal, tool: ToolDefinition, arguments: dict[str, Any], sandbox_policy: SandboxPolicy) -> PermissionEvaluation:
        """Return an auditable ALLOW, ASK, or DENY decision without executing the tool."""
        if tool.name not in principal.allowed_tools:
            return self._result(PermissionDecision.DENY, "Tool is outside the agent allowlist", principal, sandbox_policy)
        missing = set(tool.required_capabilities) - set(principal.capabilities)
        if missing:
            names = sorted(str(value.value if hasattr(value, "value") else value) for value in missing)
            return self._result(PermissionDecision.DENY, f"Missing capabilities: {names}", principal, sandbox_policy)
        path_decision = self._evaluate_paths(tool.name, arguments, sandbox_policy)
        if path_decision:
            return self._result(path_decision, "Built-in path policy", principal, sandbox_policy)
        matches = [rule for rule in self.rules if rule.matches(principal, tool.name, arguments) and (rule.trusted or rule.decision != PermissionDecision.ALLOW)]
        rule_decision = resolve_rule_decision(matches)
        if rule_decision:
            return PermissionEvaluation(rule_decision, "Matched permission rules", [rule.rule_id for rule in matches], principal.capabilities, sandbox_policy)
        mcp_decision = self._evaluate_mcp_policy(tool)
        if mcp_decision is not None:
            return self._result(mcp_decision, f"MCP approval mode {tool.metadata.get('mcp_approval_mode')}", principal, sandbox_policy)
        default = self._default_decision(principal, tool)
        return self._result(default, "Security profile default", principal, sandbox_policy)

    def _evaluate_paths(self, tool_name: str, arguments: dict[str, Any], sandbox_policy: SandboxPolicy) -> PermissionDecision | None:
        """Apply fail-closed workspace path checks before configurable rules."""
        if tool_name not in {"read_file", "list_files", "search_text", "write_file", "apply_patch", "delete_path", "run_command"}:
            return None
        policy = FileSystemPolicy(sandbox_policy.workspace_root)
        raw = str(arguments.get("cwd", arguments.get("path", ".")))
        resolved = policy.resolve(raw, allow_root=tool_name not in {"delete_path", "write_file"})
        return PermissionDecision.ASK if policy.requires_approval(resolved) else None

    def _default_decision(self, principal: ToolExecutionPrincipal, tool: ToolDefinition) -> PermissionDecision:
        """Resolve an unmatched call from approval policy, sandbox mode, and side effects."""
        if principal.sandbox_mode == SandboxMode.READ_ONLY and tool.risk_level.value != "READ_ONLY":
            return PermissionDecision.DENY
        if principal.approval_policy == ApprovalPolicy.UNTRUSTED and tool.risk_level.value != "READ_ONLY":
            return PermissionDecision.ASK
        if tool.name == "delete_path" or tool.risk_level.value in {"HIGH", "CRITICAL"}:
            return PermissionDecision.ASK
        return PermissionDecision.ALLOW

    def _evaluate_mcp_policy(self, tool: ToolDefinition) -> PermissionDecision | None:
        """Apply resolved MCP approval metadata without bypassing earlier hard denials."""
        raw = tool.metadata.get("mcp_approval_decision")
        return PermissionDecision(str(raw)) if raw in {item.value for item in PermissionDecision} else None

    def _result(self, decision: PermissionDecision, reason: str, principal: ToolExecutionPrincipal, policy: SandboxPolicy) -> PermissionEvaluation:
        """Build a permission evaluation with common effective fields."""
        return PermissionEvaluation(decision, reason, effective_capabilities=principal.capabilities, sandbox_policy=policy)
