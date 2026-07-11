from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agent_harness.domain.errors import ToolExecutionError
from agent_harness.domain.tools import ToolDefinition
from agent_harness.utils.paths import ensure_not_secret, resolve_workspace_path


def create_read_file_tool(workspace_root: Path, timeout_seconds: int = 30) -> ToolDefinition:
    """Create the read-only file reader tool bound to one workspace root."""
    async def executor(args: dict[str, Any]) -> str:
        """Read a UTF-8 text file with line numbers after workspace validation."""
        return await asyncio.to_thread(_read_file_sync, workspace_root, args)

    return ToolDefinition(
        name="read_file",
        description="读取工作区内 UTF-8 文本文件，并返回带行号的内容。",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
                "max_chars": {"type": "integer"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        executor=executor,
        timeout_seconds=timeout_seconds,
    )


def _read_file_sync(workspace_root: Path, args: dict[str, Any]) -> str:
    """Read and format a text file on a worker thread."""
    path = resolve_workspace_path(workspace_root, args["path"], must_exist=True)
    ensure_not_secret(path)
    if path.is_dir():
        raise ToolExecutionError("Cannot read a directory")
    if path.stat().st_size > 2_000_000:
        raise ToolExecutionError("File is too large to read in phase 1")
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        raise ToolExecutionError("Refusing to read likely binary file")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolExecutionError("File is not valid UTF-8 text") from exc
    lines = text.splitlines()
    start = max(1, int(args.get("start_line", 1)))
    end_arg = args.get("end_line")
    end = int(end_arg) if end_arg is not None else min(len(lines), start + 199)
    max_chars = int(args.get("max_chars", 12000))
    selected = lines[start - 1 : end]
    body_lines: list[str] = []
    chars = 0
    truncated = False
    for idx, line in enumerate(selected, start=start):
        rendered = f"{idx} | {line}"
        if chars + len(rendered) > max_chars:
            truncated = True
            break
        body_lines.append(rendered)
        chars += len(rendered)
    rel = path.resolve().relative_to(workspace_root.resolve())
    header = f"path: {str(rel).replace(chr(92), '/')}\nlines: {start}-{start + len(body_lines) - 1}\ntruncated: {str(truncated).lower()}\n"
    return header + "\n".join(body_lines)
