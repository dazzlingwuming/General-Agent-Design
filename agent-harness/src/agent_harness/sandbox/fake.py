from __future__ import annotations

from dataclasses import dataclass, field

from agent_harness.sandbox.base import CommandExecution, CommandResult
from agent_harness.security.models import SandboxPolicy


@dataclass(slots=True)
class FakeSandboxBackend:
    """Deterministic test backend that records calls without spawning processes."""

    result: CommandResult = field(default_factory=lambda: CommandResult(0, "", "", backend="fake"))
    executions: list[CommandExecution] = field(default_factory=list)
    name: str = "fake"

    async def availability(self) -> tuple[bool, str]:
        """Report the test backend as available without claiming OS isolation."""
        return True, "test backend"

    async def execute(self, execution: CommandExecution, policy: SandboxPolicy) -> CommandResult:
        """Record and return the configured deterministic result."""
        self.executions.append(execution)
        return self.result

