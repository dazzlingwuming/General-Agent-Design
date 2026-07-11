from __future__ import annotations

from pathlib import Path

import pytest

from agent_harness.agents.registry import AgentRegistry, load_registry_from_toml
from agent_harness.domain.agent import AgentDefinition


def test_registry_rejects_unknown_tool() -> None:
    """Verify agent validation fails when a definition references an unknown tool."""
    registry = AgentRegistry()
    registry.register(AgentDefinition(name="child", description="", system_prompt="", enabled_tools=["missing_tool"]))

    with pytest.raises(ValueError, match="unknown tools"):
        registry.validate({"read_file"})


def test_registry_rejects_child_spawn() -> None:
    """Verify phase 2 child agents cannot enable subagent spawning."""
    registry = AgentRegistry()
    registry.register(
        AgentDefinition(
            name="explorer",
            description="",
            system_prompt="",
            enabled_tools=["read_file"],
            can_spawn_subagents=True,
        )
    )

    with pytest.raises(ValueError, match="Child agent cannot spawn"):
        registry.validate({"read_file"})


def test_registry_loads_agent_toml(tmp_path: Path) -> None:
    """Verify static agent definitions can be loaded and validated from TOML."""
    (tmp_path / "explorer.toml").write_text(
        "\n".join(
            [
                'name = "explorer"',
                'description = "定位代码"',
                'system_prompt = "请分析代码"',
                'model_provider = "fake"',
                'model = "fake-model"',
                'enabled_tools = ["read_file", "submit_result"]',
                "can_spawn_subagents = false",
                "max_depth = 1",
                'output_schema_id = "subagent_result"',
                "[limits]",
                "max_iterations = 4",
                "max_model_calls = 3",
                "max_tool_calls = 5",
                "max_wall_time_seconds = 30",
            ]
        ),
        encoding="utf-8",
    )

    registry = load_registry_from_toml(tmp_path, {"read_file", "submit_result"})

    assert registry.get("explorer").output_schema_id == "subagent_result"
    assert registry.get("explorer").limits.max_model_calls == 3
