from __future__ import annotations

import asyncio
from pathlib import Path

from agent_harness.domain.tools import ToolDefinition, ToolEffectClass, ToolRecoveryPolicy
from agent_harness.security.models import Capability, RiskLevel, SideEffectType
from agent_harness.security.path_policy import FileSystemPolicy


def create_write_file_tool(workspace_root: Path, timeout_seconds: int = 30) -> ToolDefinition:
    """Create a workspace-confined UTF-8 file writer."""
    policy = FileSystemPolicy(workspace_root)

    async def execute(args: dict) -> dict:
        """Resolve the target again at execution time and write it atomically enough for local use."""
        target = policy.resolve(str(args["path"]), allow_root=False).resolved
        content = str(args["content"])
        await asyncio.to_thread(_write_text, target, content, bool(args.get("create_parents", True)))
        return {"path": str(target.relative_to(workspace_root.resolve())), "chars_written": len(content)}

    return ToolDefinition(
        name="write_file",
        description="在工作区内创建或覆盖 UTF-8 文本文件。",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "minLength": 1},
                "content": {"type": "string"},
                "create_parents": {"type": "boolean"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        executor=execute,
        timeout_seconds=timeout_seconds,
        risk_level=RiskLevel.MEDIUM,
        side_effect=SideEffectType.FILESYSTEM,
        required_capabilities=frozenset({Capability.FILE_WRITE}),
        effect_class=ToolEffectClass.RECONCILABLE_WRITE,
        recovery_policy=ToolRecoveryPolicy.VERIFY_THEN_SYNTHESIZE,
    )


def _write_text(path: Path, content: str, create_parents: bool) -> None:
    """Write UTF-8 text after optionally creating parent directories."""
    if create_parents:
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
