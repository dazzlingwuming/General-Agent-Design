from __future__ import annotations

import pytest

from agent_harness.context.builder import ContextBuilder
from agent_harness.domain.agent import AgentDefinition
from agent_harness.domain.messages import CanonicalMessage
from agent_harness.domain.run import RunState
from agent_harness.tools.builtins.factory import create_default_registry


def test_context_builder_includes_system_and_history(tmp_path):
    """Verify that context includes the system prompt before run history."""
    run = RunState(task="task", workspace_root=tmp_path)
    run.messages.append(CanonicalMessage(role="user", content="hello"))
    request = ContextBuilder().build(run, AgentDefinition("a", "d", "sys"), create_default_registry(tmp_path))
    assert [m.role for m in request.messages] == ["system", "user"]
    assert len(request.tools) == 3


def test_context_builder_enforces_limit(tmp_path):
    """Verify that estimated context overflow raises a context error."""
    run = RunState(task="task", workspace_root=tmp_path)
    run.messages.append(CanonicalMessage(role="user", content="x" * 100))
    with pytest.raises(Exception):
        ContextBuilder(max_estimated_input_tokens=1).build(run, AgentDefinition("a", "d", "sys"), create_default_registry(tmp_path))
