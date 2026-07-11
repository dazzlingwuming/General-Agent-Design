from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from agent_harness.domain.tools import ToolDefinition
from agent_harness.tools.builtins.list_files import IGNORED_NAMES
from agent_harness.utils.paths import ensure_not_secret, resolve_workspace_path


def create_search_text_tool(workspace_root: Path, timeout_seconds: int = 30) -> ToolDefinition:
    """Create the read-only text search tool bound to one workspace root."""
    async def executor(args: dict[str, Any]) -> dict[str, Any]:
        """Search text files under the workspace using Python's regex engine."""
        return await asyncio.to_thread(_search_text_sync, workspace_root, args)

    return ToolDefinition(
        name="search_text",
        description="在工作区文本文件中搜索字符串或正则表达式。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path": {"type": "string"},
                "glob": {"type": "string"},
                "regex": {"type": "boolean"},
                "case_sensitive": {"type": "boolean"},
                "max_results": {"type": "integer"},
                "context_lines": {"type": "integer"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        executor=executor,
        timeout_seconds=timeout_seconds,
    )


def _search_text_sync(workspace_root: Path, args: dict[str, Any]) -> dict[str, Any]:
    """Search text files on a worker thread."""
    query = args["query"]
    base = resolve_workspace_path(workspace_root, args.get("path", "."), must_exist=True)
    pattern = args.get("glob", "*")
    regex = bool(args.get("regex", False))
    case_sensitive = bool(args.get("case_sensitive", False))
    max_results = int(args.get("max_results", 50))
    context_lines = int(args.get("context_lines", 0))
    flags = 0 if case_sensitive else re.IGNORECASE
    needle = re.compile(query if regex else re.escape(query), flags)
    files = [base] if base.is_file() else sorted(base.rglob(pattern), key=lambda p: str(p).lower())
    results: list[dict[str, Any]] = []
    for file_path in files:
        if not file_path.is_file():
            continue
        if any(part in IGNORED_NAMES for part in file_path.parts):
            continue
        try:
            ensure_not_secret(file_path)
            rel = file_path.resolve().relative_to(workspace_root.resolve())
            text = file_path.read_text(encoding="utf-8")
        except Exception:
            continue
        lines = text.splitlines()
        for index, line in enumerate(lines, start=1):
            if not needle.search(line):
                continue
            before_start = max(1, index - context_lines)
            after_end = min(len(lines), index + context_lines)
            results.append(
                {
                    "path": str(rel).replace("\\", "/"),
                    "line": index,
                    "text": line,
                    "context": [
                        {"line": i, "text": lines[i - 1]}
                        for i in range(before_start, after_end + 1)
                        if i != index
                    ],
                }
            )
            if len(results) >= max_results:
                return {"query": query, "results": results, "truncated": True}
    return {"query": query, "results": results, "truncated": False}
