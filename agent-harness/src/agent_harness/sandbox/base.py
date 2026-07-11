from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from agent_harness.security.models import SandboxPolicy


@dataclass(frozen=True, slots=True)
class CommandExecution:
    """Structured command request that never passes through a host shell."""

    program: str
    args: tuple[str, ...] = ()
    cwd: Path = Path(".")
    env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float | None = None


@dataclass(slots=True)
class CommandResult:
    """Captured result of one sandboxed process execution."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    truncated: bool = False
    backend: str = "unknown"


class SandboxBackend(Protocol):
    """Protocol implemented by safe and explicitly unsafe process backends."""

    name: str

    async def availability(self) -> tuple[bool, str]:
        """Return whether this backend can enforce its advertised boundary."""
        ...

    async def execute(self, execution: CommandExecution, policy: SandboxPolicy) -> CommandResult:
        """Execute one structured command under the supplied policy."""
        ...

