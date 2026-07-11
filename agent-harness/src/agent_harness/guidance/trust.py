from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from agent_harness.utils.atomic_files import atomic_write_json


class WorkspaceTrustState(StrEnum):
    """Trust decisions that gate repository-provided agent configuration."""

    UNKNOWN = "unknown"
    TRUSTED_ONCE = "trusted_once"
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


@dataclass(slots=True)
class WorkspaceTrustStore:
    """Persist trust by canonical workspace identity and keep once grants in memory."""

    path: Path
    once_roots: set[str] | None = None
    _lock: threading.RLock | None = None

    def __post_init__(self) -> None:
        """Initialize the process-local trust-once collection."""
        if self.once_roots is None:
            self.once_roots = set()
        if self._lock is None:
            self._lock = threading.RLock()

    def identity(self, workspace: Path) -> str:
        """Return a normalized case-insensitive identity for one workspace root."""
        return str(workspace.resolve()).casefold()

    def get(self, workspace: Path) -> WorkspaceTrustState:
        """Read the effective trust state for one canonical workspace."""
        identity = self.identity(workspace)
        if identity in (self.once_roots or set()):
            return WorkspaceTrustState.TRUSTED_ONCE
        return WorkspaceTrustState(self._read().get(identity, WorkspaceTrustState.UNKNOWN.value))

    def set(self, workspace: Path, state: WorkspaceTrustState) -> None:
        """Apply a process-only or persistent workspace trust decision."""
        identity = self.identity(workspace)
        if state == WorkspaceTrustState.TRUSTED_ONCE:
            assert self.once_roots is not None
            self.once_roots.add(identity)
            return
        assert self._lock is not None
        with self._lock:
            data = self._read()
            data[identity] = state.value
            atomic_write_json(self.path, data)

    def _read(self) -> dict[str, str]:
        """Read valid trust rows and recover safely from missing or malformed files."""
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return {str(key): str(value) for key, value in data.items()} if isinstance(data, dict) else {}
