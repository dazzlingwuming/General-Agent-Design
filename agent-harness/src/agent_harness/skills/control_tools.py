from __future__ import annotations

from collections.abc import Awaitable, Callable

from agent_harness.domain.tools import ToolDefinition
from agent_harness.security.models import Capability, RiskLevel, SideEffectType
from agent_harness.skills.activation import SkillManager
from agent_harness.skills.resources import read_skill_resource
from agent_harness.skills.models import SkillActivationSnapshot


def create_activate_skill_tool(
    manager: SkillManager,
    turn_id_provider: Callable[[], str],
    audit: Callable[[str, dict], None],
    fork_handler: Callable[[SkillActivationSnapshot], Awaitable[dict]] | None = None,
) -> ToolDefinition:
    """Create the model-facing Skill activation tool for the current thread runtime."""

    async def execute(arguments: dict) -> dict:
        """Activate one model-invocable Skill and return structured activation metadata."""
        try:
            activation, created = manager.activate(str(arguments["skill"]), str(arguments.get("arguments") or ""), turn_id_provider())
        except Exception as exc:
            audit("skill.activation_failed", {"skill": str(arguments.get("skill") or ""), "error": str(exc)})
            raise
        event = "skill.activated" if created else "skill.already_active"
        audit(event, {"skill_id": activation.skill_id, "activation_id": activation.activation_id, "content_hash": activation.content_hash})
        if activation.context_mode == "fork":
            if fork_handler is None:
                raise RuntimeError("当前 Runtime 不支持 Fork Skill")
            result = await fork_handler(activation)
            audit("skill.delegated", {"skill_id": activation.skill_id, "activation_id": activation.activation_id, "agent_id": result.get("agent_id")})
            audit("skill.completed", {"skill_id": activation.skill_id, "activation_id": activation.activation_id, "status": result.get("status")})
            return {"status": "delegated", "skill": activation.qualified_name, "activation_id": activation.activation_id, "result": result}
        return {"status": "activated" if created else "already_active", "skill": activation.qualified_name, "activation_id": activation.activation_id}

    return ToolDefinition(
        name="activate_skill",
        description="按名称激活一个可用 Agent Skill；完整说明会在下一次模型请求中加入上下文。",
        input_schema={"type": "object", "properties": {"skill": {"type": "string"}, "arguments": {"type": "string"}}, "required": ["skill"], "additionalProperties": False},
        executor=execute,
        risk_level=RiskLevel.READ_ONLY,
        side_effect=SideEffectType.NONE,
        required_capabilities=frozenset({Capability.FILE_READ}),
    )


def create_read_skill_resource_tool(manager: SkillManager, max_bytes: int, audit: Callable[[str, dict], None]) -> ToolDefinition:
    """Create a bounded tool for reading declared resources of active Skills."""

    async def execute(arguments: dict) -> dict:
        """Read one UTF-8 resource only after its owning Skill has been activated."""
        record = manager.resolve(str(arguments["skill"]))
        activation = next((item for item in reversed(manager.active) if item.skill_id == record.skill_id), None)
        if activation is None:
            raise PermissionError("Skill 尚未激活")
        relative_path = str(arguments["path"])
        content = read_skill_resource(record, activation, relative_path, max_bytes)
        audit("skill.resource_read", {"skill_id": record.skill_id, "path": relative_path, "chars": len(content)})
        return {"skill": record.qualified_name, "path": relative_path, "content": content}

    return ToolDefinition(
        name="read_skill_resource",
        description="读取已激活 Skill 的声明资源；scripts 只能读取，绝不会执行。",
        input_schema={"type": "object", "properties": {"skill": {"type": "string"}, "path": {"type": "string"}}, "required": ["skill", "path"], "additionalProperties": False},
        executor=execute,
        risk_level=RiskLevel.READ_ONLY,
        side_effect=SideEffectType.NONE,
        required_capabilities=frozenset({Capability.FILE_READ}),
    )
