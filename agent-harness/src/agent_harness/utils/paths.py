from __future__ import annotations

from pathlib import Path

from agent_harness.domain.errors import WorkspaceBoundaryError

SECRET_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_dsa",
    "id_ed25519",
}
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}


def resolve_workspace_path(workspace_root: Path, user_path: str | None, *, must_exist: bool = False) -> Path:
    """Resolve a user path and reject anything outside the workspace root."""
    if not user_path:
        user_path = "."
    raw = Path(user_path)
    if raw.is_absolute():
        raise WorkspaceBoundaryError("Absolute paths are not allowed", details={"path": user_path})
    root = workspace_root.resolve()
    candidate = (root / raw).resolve(strict=must_exist)
    if root != candidate and root not in candidate.parents:
        raise WorkspaceBoundaryError("Path escapes workspace root", details={"path": user_path})
    return candidate


def ensure_not_secret(path: Path) -> None:
    """Reject common secret filenames and private-key suffixes."""
    lower_name = path.name.lower()
    if lower_name in SECRET_NAMES or path.suffix.lower() in SECRET_SUFFIXES:
        raise WorkspaceBoundaryError("Refusing to access common secret file", details={"path": str(path)})
