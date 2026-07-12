from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from agent_harness.utils.atomic_files import atomic_write_json


@dataclass(slots=True)
class MCPLaunchApprovalStore:
    """Persist project stdio first-launch approvals by exact config hash."""

    path: Path

    def approved(self, project_identity: str, config_hash: str) -> bool:
        """Return whether this exact project server configuration was approved."""
        return config_hash in self._read().get(project_identity, [])

    def approve(self, project_identity: str, config_hash: str) -> None:
        """Persist an approval that automatically expires when configuration changes."""
        data = self._read()
        hashes = set(data.get(project_identity, []))
        hashes.add(config_hash)
        data[project_identity] = sorted(hashes)
        atomic_write_json(self.path, data)

    def _read(self) -> dict[str, list[str]]:
        """Read valid approval rows and fail closed on malformed state."""
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {str(key): [str(item) for item in value] for key, value in raw.items() if isinstance(value, list)} if isinstance(raw, dict) else {}
