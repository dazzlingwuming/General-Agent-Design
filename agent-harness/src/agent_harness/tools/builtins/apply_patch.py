from __future__ import annotations

import asyncio
from pathlib import Path

from agent_harness.domain.errors import ToolInputValidationError
from agent_harness.domain.tools import ToolDefinition, ToolEffectClass, ToolRecoveryPolicy
from agent_harness.security.models import Capability, RiskLevel, SideEffectType
from agent_harness.security.path_policy import FileSystemPolicy


def create_apply_patch_tool(workspace_root: Path, timeout_seconds: int = 30) -> ToolDefinition:
    """Create a deterministic exact-text replacement tool confined to one workspace file."""
    policy = FileSystemPolicy(workspace_root)

    async def execute(args: dict) -> dict:
        """Replace exactly one matching old-text block and reject ambiguous patches."""
        target = policy.resolve(str(args["path"]), allow_root=False).resolved
        return await asyncio.to_thread(_replace_exact, target, str(args["old_text"]), str(args["new_text"]))

    return ToolDefinition(
        name="apply_patch",
        description="在工作区文件中精确替换一段唯一文本。",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "minLength": 1},
                "old_text": {"type": "string", "minLength": 1},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
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


def _replace_exact(path: Path, old_text: str, new_text: str) -> dict:
    """Apply one exact replacement while rejecting missing or duplicate matches."""
    content = path.read_text(encoding="utf-8")
    count = content.count(old_text)
    if count != 1:
        raise ToolInputValidationError("old_text must match exactly once", details={"path": str(path), "matches": count})
    path.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
    return {"path": str(path), "replacements": 1, "chars_delta": len(new_text) - len(old_text)}
