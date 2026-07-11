from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_harness.domain.tools import ToolDefinition
from agent_harness.utils.paths import ensure_not_secret, resolve_workspace_path

IGNORED_NAMES = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", ".harness"}


def create_list_files_tool(workspace_root: Path, timeout_seconds: int = 30) -> ToolDefinition:
    """Create the read-only directory listing tool bound to one workspace root."""
    async def executor(args: dict[str, Any]) -> dict[str, Any]:
        """List workspace files with stable ordering and basic ignore rules."""
        base = resolve_workspace_path(workspace_root, args.get("path", "."), must_exist=True)
        if not base.exists():
            return {"path": args.get("path", "."), "entries": [], "truncated": False}
        recursive = bool(args.get("recursive", False))
        max_depth = int(args.get("max_depth", 3))
        include_hidden = bool(args.get("include_hidden", False))
        limit = int(args.get("limit", 200))
        entries: list[dict[str, Any]] = []
        root = workspace_root.resolve()

        def visible(path: Path) -> bool:
            """Return whether a path should be included in list_files output."""
            if path.name in IGNORED_NAMES:
                return False
            if not include_hidden and path.name.startswith("."):
                return False
            try:
                ensure_not_secret(path)
            except Exception:
                return False
            return True

        candidates = base.rglob("*") if recursive else base.iterdir()
        for child in sorted(candidates, key=lambda p: str(p).lower()):
            if not visible(child):
                if child.is_dir() and not recursive:
                    continue
                continue
            rel = child.resolve().relative_to(root)
            if recursive and len(rel.parts) > max_depth:
                continue
            entries.append({"path": str(rel).replace("\\", "/"), "type": "dir" if child.is_dir() else "file"})
            if len(entries) >= limit:
                return {"path": str(base.relative_to(root)).replace("\\", "/"), "entries": entries, "truncated": True}
        return {"path": str(base.relative_to(root)).replace("\\", "/"), "entries": entries, "truncated": False}

    return ToolDefinition(
        name="list_files",
        description="列出工作区内的文件和目录。",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative directory path."},
                "recursive": {"type": "boolean"},
                "max_depth": {"type": "integer"},
                "include_hidden": {"type": "boolean"},
                "limit": {"type": "integer"},
            },
            "required": [],
            "additionalProperties": False,
        },
        executor=executor,
        timeout_seconds=timeout_seconds,
    )
