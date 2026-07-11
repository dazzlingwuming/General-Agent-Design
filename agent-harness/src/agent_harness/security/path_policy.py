from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

from agent_harness.domain.errors import ToolAuthorizationError


PROTECTED_DENY_PATTERNS = (".env", "**/.env", "**/*.pem", "**/*.key", ".harness/threads/**")
PROTECTED_ASK_PATTERNS = (".git/**", "**/package-lock.json", "**/pnpm-lock.yaml", "**/poetry.lock")


@dataclass(frozen=True, slots=True)
class ResolvedPath:
    """Original and symlink-resolved forms of a workspace-relative path."""

    original: Path
    resolved: Path
    relative: str


@dataclass(slots=True)
class FileSystemPolicy:
    """Resolve paths and enforce workspace, secret, and protected-store boundaries."""

    workspace_root: Path

    def resolve(self, raw_path: str, *, allow_root: bool = True) -> ResolvedPath:
        """Resolve a relative path and reject absolute, escaping, or protected targets."""
        candidate = Path(raw_path)
        if candidate.is_absolute():
            raise ToolAuthorizationError("Absolute paths are not allowed", details={"path": raw_path})
        root = self.workspace_root.resolve()
        original = root / candidate
        resolved = original.resolve(strict=False)
        if not resolved.is_relative_to(root):
            raise ToolAuthorizationError("Path escapes workspace", details={"path": raw_path})
        if not allow_root and resolved == root:
            raise ToolAuthorizationError("Workspace root cannot be modified or deleted", details={"path": raw_path})
        relative = resolved.relative_to(root).as_posix() or "."
        self._reject_denied(relative)
        return ResolvedPath(original=original, resolved=resolved, relative=relative)

    def requires_approval(self, resolved_path: ResolvedPath) -> bool:
        """Return whether the resolved path matches a built-in protected ASK pattern."""
        return any(fnmatch.fnmatch(resolved_path.relative, pattern) for pattern in PROTECTED_ASK_PATTERNS)

    def _reject_denied(self, relative: str) -> None:
        """Reject built-in secret and harness persistence paths."""
        if any(fnmatch.fnmatch(relative, pattern) for pattern in PROTECTED_DENY_PATTERNS):
            raise ToolAuthorizationError("Protected path is denied", details={"path": relative})

