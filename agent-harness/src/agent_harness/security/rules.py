from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_harness.security.models import Capability, PermissionDecision, RuleSource, ToolExecutionPrincipal


@dataclass(frozen=True, slots=True)
class PermissionRule:
    """One narrow permission rule with optional tool, path, command, and agent matchers."""

    rule_id: str
    decision: PermissionDecision
    source: RuleSource = RuleSource.USER
    tool: str | None = None
    path: str | None = None
    argv_prefix: tuple[str, ...] = ()
    agent: str | None = None
    capability: Capability | None = None
    trusted: bool = True

    def matches(self, principal: ToolExecutionPrincipal, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Return whether every configured matcher accepts this tool request."""
        if self.tool and self.tool != tool_name:
            return False
        if self.agent and self.agent != principal.agent_id:
            return False
        if self.capability and self.capability not in principal.capabilities:
            return False
        if self.path and not self._matches_any_path(arguments):
            return False
        if self.argv_prefix and not self._matches_argv(arguments):
            return False
        return True

    def _matches_any_path(self, arguments: dict[str, Any]) -> bool:
        """Match a normalized slash-separated path argument against the rule glob."""
        values = [arguments.get(name) for name in ("path", "cwd")]
        values.extend(arguments.get("paths", []) if isinstance(arguments.get("paths"), list) else [])
        return any(fnmatch.fnmatch(Path(str(value)).as_posix(), self.path or "") for value in values if value is not None)

    def _matches_argv(self, arguments: dict[str, Any]) -> bool:
        """Match a structured program and argument list using an exact argv prefix."""
        argv = [str(arguments.get("program", "")), *[str(value) for value in arguments.get("args", [])]]
        return tuple(argv[: len(self.argv_prefix)]) == self.argv_prefix


def resolve_rule_decision(matches: list[PermissionRule]) -> PermissionDecision | None:
    """Resolve matching rules using the global DENY then ASK then ALLOW precedence."""
    decisions = {rule.decision for rule in matches}
    for decision in (PermissionDecision.DENY, PermissionDecision.ASK, PermissionDecision.ALLOW):
        if decision in decisions:
            return decision
    return None

