from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent_harness.agents.loader import load_agent_definitions
from agent_harness.agents.outputs import SUBAGENT_RESULT_SCHEMA, OutputSchemaRegistry, create_default_output_registry
from agent_harness.context.prompt import SYSTEM_PROMPT
from agent_harness.domain.agent import AgentDefinition
from agent_harness.domain.run import RunLimits


def default_child_prompt(role: str) -> str:
    """Return the Chinese system prompt used by phase 2 child agents."""
    return f"""你是 {role} 子 Agent。

规则：
1. 你只处理父 Agent 委派给你的具体任务。
2. 只能使用提供给你的只读工具查看工作区。
3. 不要假装读取过未通过工具返回的文件。
4. 完成后必须调用 submit_result，不要直接用普通文本结束。
5. submit_result 必须包含 summary、evidence、unresolved_questions、confidence。
6. evidence 只写真实工具证据，路径和行号要尽量准确。
"""


@dataclass(slots=True)
class AgentRegistry:
    """Static registry of root and child agent definitions."""

    _agents: dict[str, AgentDefinition] = field(default_factory=dict)
    output_registry: OutputSchemaRegistry = field(default_factory=create_default_output_registry)
    max_depth: int = 1

    def register(self, definition: AgentDefinition) -> None:
        """Register one immutable agent definition by unique name."""
        if definition.name in self._agents:
            raise ValueError(f"Agent already registered: {definition.name}")
        self._agents[definition.name] = definition

    def validate(self, known_tools: set[str]) -> None:
        """Validate all registered agent definitions against phase 2 constraints."""
        for definition in self._agents.values():
            missing_tools = sorted(set(definition.enabled_tools) - known_tools)
            if missing_tools:
                raise ValueError(f"Agent {definition.name} references unknown tools: {missing_tools}")
            if definition.max_depth > self.max_depth:
                raise ValueError(f"Agent {definition.name} exceeds max_depth={self.max_depth}")
            if definition.name != "coding_assistant" and definition.can_spawn_subagents:
                raise ValueError(f"Child agent cannot spawn subagents: {definition.name}")
            if definition.output_schema_id and not self.output_registry.has(definition.output_schema_id):
                raise ValueError(f"Agent {definition.name} references unknown output schema: {definition.output_schema_id}")

    def get(self, name: str) -> AgentDefinition:
        """Return one agent definition by name."""
        try:
            return self._agents[name]
        except KeyError as exc:
            raise ValueError(f"Unknown agent: {name}") from exc

    def list_children(self) -> list[AgentDefinition]:
        """Return definitions that can be used as child agents."""
        return [agent for agent in self._agents.values() if agent.name != "coding_assistant"]

    def tool_description(self) -> str:
        """Render child agent descriptions for the root prompt."""
        lines = []
        for agent in self.list_children():
            lines.append(f"- {agent.name}: {agent.description}")
        return "\n".join(lines)

    def export_agent_tool_descriptions(self) -> list[dict[str, str]]:
        """Return child agent descriptions as structured prompt metadata."""
        return [{"name": agent.name, "description": agent.description} for agent in self.list_children()]


def load_registry_from_toml(directory: Path, known_tools: set[str], max_depth: int = 1) -> AgentRegistry:
    """Load and validate an AgentRegistry from a directory of TOML files."""
    registry = AgentRegistry(max_depth=max_depth)
    for definition in load_agent_definitions(directory):
        registry.register(definition)
    registry.validate(known_tools)
    return registry


def create_default_agent_registry(model: str, provider: str, root_limits: RunLimits) -> AgentRegistry:
    """Create the phase 2 built-in root and child agent definitions."""
    registry = AgentRegistry()
    registry.register(
        AgentDefinition(
            name="coding_assistant",
            description="Main repository analysis agent",
            system_prompt=SYSTEM_PROMPT,
            model_provider=provider,
            model=model,
            enabled_tools=[
                "list_files",
                "read_file",
                "search_text",
                "spawn_subagent",
                "wait_subagents",
                "get_subagent_status",
                "send_subagent_message",
                "cancel_subagent",
                "close_subagent",
            ],
            limits=root_limits,
            can_spawn_subagents=True,
            max_depth=1,
        )
    )
    child_limits = RunLimits(max_iterations=8, max_model_calls=5, max_tool_calls=15, max_wall_time_seconds=600)
    for name, description, role in [
        ("explorer", "定位代码位置、调用链和关键证据。", "代码探索"),
        ("reviewer", "审查代码正确性风险和边界情况。", "代码审查"),
        ("test_analyst", "分析测试覆盖、缺失场景和验证风险。", "测试分析"),
    ]:
        registry.register(
            AgentDefinition(
                name=name,
                description=description,
                system_prompt=default_child_prompt(role),
                model_provider=provider,
                model=model,
                enabled_tools=["list_files", "read_file", "search_text", "submit_result"],
                limits=child_limits,
                can_spawn_subagents=False,
                max_depth=1,
                output_schema_id="subagent_result",
            )
        )
    return registry
