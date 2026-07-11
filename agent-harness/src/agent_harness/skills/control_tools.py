from __future__ import annotations

from collections.abc import Callable

from agent_harness.domain.tools import ToolDefinition
from agent_harness.security.models import Capability, RiskLevel, SideEffectType
from agent_harness.skills.activation import SkillManager
from agent_harness.skills.invocation import SkillInvocationRequest, SkillInvocationService, SkillInvocationSource
from agent_harness.skills.resources import read_skill_resource


def create_activate_skill_tool(
    service: SkillInvocationService,
    turn_id_provider: Callable[[], str],
    thread_id_provider: Callable[[], str],
    audit: Callable[[str, dict], None],
) -> ToolDefinition:
    """Create the model-facing Skill activation tool for the current thread runtime."""

    async def execute(arguments: dict) -> dict:
        """Activate one model-invocable Skill and return structured activation metadata."""
        try:
            result = await service.invoke(SkillInvocationRequest(str(arguments["skill"]), str(arguments.get("arguments") or ""), SkillInvocationSource.MODEL_TOOL, thread_id_provider(), turn_id_provider()))
        except Exception as exc:
            audit("skill.activation_failed", {"skill": str(arguments.get("skill") or ""), "error": str(exc)})
            raise
        activation = result.activation
        if result.delegated_result is not None:
            return {"status": "delegated", "skill": activation.qualified_name, "activation_id": activation.activation_id, "execution_id": result.execution_id, "result": result.delegated_result}
        return {"status": "activated" if result.activation_created else "already_active", "skill": activation.qualified_name, "activation_id": activation.activation_id, "execution_id": result.execution_id}

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
        activation_id = str(arguments.get("activation_id") or "")
        activation = next((item for item in reversed(manager.active) if item.activation_id == activation_id), None)
        if activation is None and arguments.get("skill"):
            activation = next((item for item in reversed(manager.active) if item.qualified_name == str(arguments["skill"]) or item.skill_id == str(arguments["skill"])), None)
        if activation is None:
            raise PermissionError("Skill 尚未激活")
        record = next(item for item in manager.records if item.skill_id == activation.skill_id)
        relative_path = str(arguments["path"])
        content = read_skill_resource(record, activation, relative_path, max_bytes)
        audit("skill.resource_read", {"skill_id": record.skill_id, "path": relative_path, "chars": len(content)})
        return {"skill": record.qualified_name, "path": relative_path, "content": content}

    return ToolDefinition(
        name="read_skill_resource",
        description="读取已激活 Skill 的声明资源；scripts 只能读取，绝不会执行。",
        input_schema={"type": "object", "properties": {"activation_id": {"type": "string"}, "skill": {"type": "string"}, "path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
        executor=execute,
        risk_level=RiskLevel.READ_ONLY,
        side_effect=SideEffectType.NONE,
        required_capabilities=frozenset({Capability.FILE_READ}),
    )
