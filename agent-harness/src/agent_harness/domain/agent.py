from __future__ import annotations

from dataclasses import dataclass, field

from agent_harness.domain.run import RunLimits


@dataclass(slots=True)
class AgentDefinition:
    """Static configuration for one root or child agent role."""

    name: str
    description: str
    system_prompt: str
    model_provider: str = "deepseek"
    model: str = "deepseek-chat"
    temperature: float = 0.0
    max_output_tokens: int = 4096
    enabled_tools: list[str] = field(default_factory=lambda: ["list_files", "read_file", "search_text"])
    limits: RunLimits = field(default_factory=RunLimits)
    can_spawn_subagents: bool = False
    max_depth: int = 0
    output_schema_id: str | None = None
    context_policy: dict[str, object] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)
