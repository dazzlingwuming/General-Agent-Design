from __future__ import annotations

import os
from dataclasses import dataclass

from agent_harness.sandbox.base import SandboxBackend
from agent_harness.sandbox.bubblewrap import BubblewrapSandboxBackend, WslBubblewrapSandboxBackend
from agent_harness.sandbox.none import NoSandboxBackend
from agent_harness.security.models import SandboxMode


@dataclass(slots=True)
class SandboxManager:
    """Select a platform backend while forbidding silent unsandboxed fallback."""

    backend_name: str = "auto"
    wsl_distribution: str | None = None

    def create_backend(self, mode: SandboxMode) -> SandboxBackend:
        """Create the configured backend and require explicit full access for none."""
        if mode == SandboxMode.DANGER_FULL_ACCESS:
            if self.backend_name not in {"auto", "none"}:
                raise RuntimeError("danger-full-access requires the none backend")
            return NoSandboxBackend()
        if self.backend_name == "none":
            raise RuntimeError("NoSandbox cannot satisfy a protected sandbox mode")
        if self.backend_name not in {"auto", "bubblewrap", "wsl2-bubblewrap"}:
            raise RuntimeError(f"Unknown sandbox backend: {self.backend_name}")
        if os.name == "nt" or self.backend_name == "wsl2-bubblewrap":
            return WslBubblewrapSandboxBackend(distribution=self.wsl_distribution)
        return BubblewrapSandboxBackend()
