from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from agent_harness.security.models import ToolExecutionPrincipal


@dataclass(frozen=True, slots=True)
class GrantKey:
    """Identity and argument boundary for one approval grant."""

    thread_id: str
    turn_id: str | None
    principal_id: str
    tool_name: str
    argument_fingerprint: str
    target_scope: tuple[str, ...]


@dataclass(slots=True)
class ApprovalGrantStore:
    """Hold narrow turn and thread approval grants for one host runtime."""

    _turn_grants: set[GrantKey] = field(default_factory=set)
    _thread_grants: set[GrantKey] = field(default_factory=set)

    def key(self, principal: ToolExecutionPrincipal, tool_name: str, arguments: dict[str, Any], *, turn_scoped: bool) -> GrantKey:
        """Build a stable non-secret key from canonical arguments and target fields."""
        canonical = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        targets = tuple(sorted(str(arguments[name]) for name in ("path", "cwd", "uri", "url") if arguments.get(name) is not None))
        return GrantKey(principal.thread_id, principal.turn_id if turn_scoped else None, principal.agent_id, tool_name, fingerprint, targets)

    def grant_turn(self, principal: ToolExecutionPrincipal, tool_name: str, arguments: dict[str, Any]) -> None:
        """Allow matching arguments for the current principal and turn."""
        self._turn_grants.add(self.key(principal, tool_name, arguments, turn_scoped=True))

    def grant_thread(self, principal: ToolExecutionPrincipal, tool_name: str, arguments: dict[str, Any]) -> None:
        """Allow matching arguments for the current principal across later turns."""
        self._thread_grants.add(self.key(principal, tool_name, arguments, turn_scoped=False))

    def allows(self, principal: ToolExecutionPrincipal, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Return whether a matching narrow turn or thread grant exists."""
        return self.key(principal, tool_name, arguments, turn_scoped=True) in self._turn_grants or self.key(principal, tool_name, arguments, turn_scoped=False) in self._thread_grants

    def clear_turn(self, thread_id: str, turn_id: str) -> None:
        """Remove grants whose lifetime ends with the supplied turn."""
        self._turn_grants = {item for item in self._turn_grants if not (item.thread_id == thread_id and item.turn_id == turn_id)}
