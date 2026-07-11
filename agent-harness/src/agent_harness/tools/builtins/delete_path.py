from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from agent_harness.domain.tools import ToolDefinition
from agent_harness.security.models import Capability, RiskLevel, SideEffectType
from agent_harness.security.path_policy import FileSystemPolicy


def create_delete_path_tool(workspace_root: Path, timeout_seconds: int = 30) -> ToolDefinition:
    """Create a protected workspace deletion tool that always requires approval by default."""
    policy = FileSystemPolicy(workspace_root)

    async def execute(args: dict) -> dict:
        """Resolve the path again and delete one file or explicitly recursive directory."""
        target = policy.resolve(str(args["path"]), allow_root=False).resolved
        recursive = bool(args.get("recursive", False))
        await asyncio.to_thread(_delete, target, recursive)
        return {"path": str(target.relative_to(workspace_root.resolve())), "deleted": True, "recursive": recursive}

    return ToolDefinition(
        name="delete_path",
        description="删除工作区内的文件或经明确指定的目录。默认需要审批。",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string", "minLength": 1}, "recursive": {"type": "boolean"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        executor=execute,
        timeout_seconds=timeout_seconds,
        risk_level=RiskLevel.HIGH,
        side_effect=SideEffectType.FILESYSTEM,
        required_capabilities=frozenset({Capability.FILE_DELETE}),
    )


def _delete(path: Path, recursive: bool) -> None:
    """Delete one path while requiring an explicit recursive flag for directories."""
    if path.is_dir():
        if not recursive:
            raise ValueError("recursive=true is required to delete a directory")
        shutil.rmtree(path)
        return
    path.unlink()

