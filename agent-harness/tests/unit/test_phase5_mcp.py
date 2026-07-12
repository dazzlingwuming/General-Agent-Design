from __future__ import annotations

import json
import sys
from pathlib import Path

from agent_harness.guidance.trust import TrustDecisionSource, WorkspaceTrustState, resolve_project_trust
from agent_harness.mcp.config import MCPConfigResolver, parse_server_config
from agent_harness.mcp.models import MCPConfigScope, MCPServerStatus, MCPTransport
from agent_harness.mcp.runtime import MCPRuntime
from agent_harness.tools.registry import ToolRegistry


def trust(status: WorkspaceTrustState = WorkspaceTrustState.TRUSTED):
    """Build a deterministic all-requiring-trust context for MCP tests."""
    return resolve_project_trust(status, TrustDecisionSource.DEFAULT, guidance_requires_trust=True, skills_require_trust=True, mcp_requires_trust=True)


def test_trust_gates_are_independent() -> None:
    """Allow Guidance independently while keeping Skills and MCP project config blocked."""
    context = resolve_project_trust(WorkspaceTrustState.UNKNOWN, TrustDecisionSource.DEFAULT, guidance_requires_trust=False, skills_require_trust=True, mcp_requires_trust=True)
    assert context.guidance_allowed is True
    assert context.skills_allowed is False
    assert context.mcp_allowed is False
    assert context.project_stdio_allowed is False


def test_scope_winner_is_complete_and_untrusted_project_is_blocked(tmp_path: Path) -> None:
    """Select a complete project entry and never merge it with a user entry."""
    project = tmp_path / "repo"
    user = tmp_path / "user"
    project.mkdir()
    user.mkdir()
    (user / "mcp.json").write_text(json.dumps({"mcpServers": {"same": {"url": "https://example.com/mcp"}}}), encoding="utf-8")
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {"same": {"command": "python", "args": ["server.py"]}}}), encoding="utf-8")
    trusted = MCPConfigResolver(project, trust(), user_root=user, admin_root=tmp_path / "admin").resolve()
    assert trusted.servers[0].transport == MCPTransport.STDIO
    assert trusted.servers[0].url is None
    blocked = MCPConfigResolver(project, trust(WorkspaceTrustState.UNKNOWN), user_root=user, admin_root=tmp_path / "admin").resolve()
    assert [item.name for item in blocked.blocked] == ["same"]
    assert blocked.servers == ()


async def test_real_stdio_sdk_lifecycle_tools_resources_and_prompts(tmp_path: Path) -> None:
    """Exercise a real official-SDK stdio server without protocol mocks."""
    script = Path(__file__).parents[1] / "fixtures" / "mcp_test_server.py"
    config = parse_server_config(
        "fixture",
        {"command": sys.executable, "args": [str(script), "stdio"], "always_load_tools": True},
        MCPConfigScope.USER,
        tmp_path,
    )
    runtime = MCPRuntime(type("Resolved", (), {"servers": (config,), "blocked": (), "diagnostics": ()})(), (tmp_path,))
    await runtime.start()
    try:
        assert runtime.manager.connections["fixture"].status == MCPServerStatus.READY
        registry = ToolRegistry()
        names = runtime.register_tools(registry)
        echo_name = next(item.canonical_name for item in runtime.tools() if item.remote_name == "echo_chinese")
        assert echo_name in names
        result = await registry.get(echo_name).executor({"text": "真实调用"})
        assert result["structured_content"] == {"echo": "真实调用"}
        resource = await runtime.manager.active_servers["fixture"].read_resource("memo://acceptance")
        assert "真实 MCP 资源读取成功" in str(resource)
        prompt = await runtime.manager.active_servers["fixture"].get_prompt("chinese_review", {"topic": "权限边界"})
        assert "请用中文审查" in str(prompt)
    finally:
        await runtime.close()
