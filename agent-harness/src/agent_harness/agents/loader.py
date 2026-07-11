from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from agent_harness.domain.agent import AgentDefinition
from agent_harness.domain.run import RunLimits


def load_agent_definition(path: Path) -> AgentDefinition:
    """Load one AgentDefinition from a TOML file."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return agent_definition_from_dict(raw)


def load_agent_definitions(directory: Path) -> list[AgentDefinition]:
    """Load all TOML agent definitions from a directory in filename order."""
    if not directory.exists():
        return []
    return [load_agent_definition(path) for path in sorted(directory.glob("*.toml"))]


def agent_definition_from_dict(raw: dict[str, Any]) -> AgentDefinition:
    """Convert parsed TOML data into an AgentDefinition."""
    limits_data = raw.get("limits", {})
    if not isinstance(limits_data, dict):
        limits_data = {}
    tools = raw.get("enabled_tools", raw.get("allowed_tools", ["list_files", "read_file", "search_text"]))
    return AgentDefinition(
        name=str(raw["name"]),
        description=str(raw.get("description", "")),
        system_prompt=str(raw.get("system_prompt", "")),
        model_provider=str(raw.get("model_provider", raw.get("provider", "deepseek"))),
        model=str(raw.get("model", "deepseek-chat")),
        temperature=float(raw.get("temperature", 0.0)),
        max_output_tokens=int(raw.get("max_output_tokens", 4096)),
        enabled_tools=list(tools),
        limits=RunLimits(**limits_data),
        can_spawn_subagents=bool(raw.get("can_spawn_subagents", False)),
        max_depth=int(raw.get("max_depth", 0)),
        output_schema_id=raw.get("output_schema_id"),
        context_policy=dict(raw.get("context_policy", {})),
        metadata=dict(raw.get("metadata", {})),
    )
