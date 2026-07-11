from __future__ import annotations

from pathlib import Path

import pytest

from agent_harness.guidance.discovery import GuidanceManager
from agent_harness.project.roots import ProjectPaths
from agent_harness.skills.activation import SkillManager
from agent_harness.skills.discovery import SkillDiscovery, SkillSearchPath
from agent_harness.skills.execution import SkillExecutionRegistry
from agent_harness.skills.invocation import SkillInvocationRequest, SkillInvocationService, SkillInvocationSource
from agent_harness.skills.models import SkillScope
from agent_harness.skills.parser import read_skill_frontmatter
from agent_harness.skills.resources import read_skill_resource
from agent_harness.utils.atomic_files import atomic_write_text


def write_skill(root: Path, name: str, *, extra: str = "", body: str = "执行任务") -> Path:
    """Create one compact Skill fixture with optional extension metadata."""
    directory = root / name
    directory.mkdir(parents=True)
    path = directory / "SKILL.md"
    path.write_text(f"---\nname: {name}\ndescription: 用于阶段四点一测试。\n{extra}---\n{body}", encoding="utf-8")
    return path


async def test_user_and_model_share_invocation_service_and_turn_scope(tmp_path: Path) -> None:
    """Use one service for both sources and release inline tool scope after the turn."""
    root = tmp_path / "skills"
    write_skill(root, "inspect", extra="allowed-tools: read_file\n")
    records, _ = SkillDiscovery((SkillSearchPath(SkillScope.USER, root, "user"),)).discover()
    manager = SkillManager(records, tmp_path / "snapshots")
    executions = SkillExecutionRegistry()
    events: list[str] = []
    service = SkillInvocationService(manager, executions, lambda event, payload: events.append(event))
    request = SkillInvocationRequest("inspect", "src", SkillInvocationSource.USER_EXPLICIT, "thread", "turn_1")
    result = await service.invoke(request)
    assert result.activation.qualified_name == "user:inspect"
    assert executions.effective_tools_for("turn_1", "coding_assistant", ["read_file", "write_file"]) == ["read_file"]
    executions.finish_turn("turn_1")
    assert executions.effective_tools_for("turn_2", "coding_assistant", ["read_file", "write_file"]) == ["read_file", "write_file"]
    assert events[:2] == ["skill.invocation_requested", "skill.activation_created"]


def test_user_only_activation_can_read_resource_and_detect_change(tmp_path: Path) -> None:
    """Authorize resources by activation instead of repeating the model invocation gate."""
    root = tmp_path / "skills"
    write_skill(root, "private", extra="disable-model-invocation: true\n")
    resource = root / "private" / "references" / "guide.md"
    resource.parent.mkdir()
    resource.write_text("原始参考", encoding="utf-8")
    records, _ = SkillDiscovery((SkillSearchPath(SkillScope.USER, root, "user"),)).discover()
    manager = SkillManager(records, tmp_path / "snapshots")
    activation, _ = manager.activate("private", "", "turn_1", user_invocation=True)
    assert read_skill_resource(records[0], activation, "references/guide.md") == "原始参考"
    resource.write_text("已变化", encoding="utf-8")
    with pytest.raises(RuntimeError, match="重新激活"):
        read_skill_resource(records[0], activation, "references/guide.md")


def test_project_scope_chain_excludes_sibling_skills(tmp_path: Path) -> None:
    """Discover project Skills only from root through cwd, excluding sibling apps."""
    root = tmp_path / "repo"
    web = root / "apps" / "web"
    mobile = root / "apps" / "mobile"
    web.mkdir(parents=True)
    mobile.mkdir(parents=True)
    write_skill(root / ".agents" / "skills", "root-skill")
    write_skill(web / ".agents" / "skills", "web-skill")
    write_skill(mobile / ".agents" / "skills", "mobile-skill")
    paths = ProjectPaths(root, root, web)
    searches = tuple(SkillSearchPath(SkillScope.PROJECT, directory / ".agents" / "skills", "project") for directory in paths.scope_chain())
    records, _ = SkillDiscovery(searches).discover()
    assert {record.name for record in records} == {"root-skill", "web-skill"}


def test_untrusted_guidance_does_not_consume_budget(tmp_path: Path) -> None:
    """Budget trusted user guidance after rejecting an oversized untrusted project file."""
    root = tmp_path / "repo"
    user = tmp_path / "user"
    root.mkdir()
    user.mkdir()
    (root / "AGENTS.md").write_text("x" * 40000, encoding="utf-8")
    (user / "AGENTS.md").write_text("可信用户规则", encoding="utf-8")
    snapshot = GuidanceManager(root, root, user, max_guidance_bytes=1024).discover("thread", "runtime", project_trusted=False)
    assert [item.content for item in snapshot.documents] == ["可信用户规则"]
    assert snapshot.total_bytes == len("可信用户规则".encode("utf-8"))


def test_discovery_prunes_ignored_directories_and_frontmatter_is_lazy(tmp_path: Path) -> None:
    """Avoid ignored trees and stop frontmatter reads before an oversized body."""
    root = tmp_path / "skills"
    write_skill(root, "visible", body="正文" * 10000)
    write_skill(root / "node_modules", "hidden")
    records, _ = SkillDiscovery((SkillSearchPath(SkillScope.USER, root, "user"),)).discover()
    assert [record.name for record in records] == ["visible"]
    frontmatter = read_skill_frontmatter(root / "visible" / "SKILL.md", 16384, 1048576)
    assert "正文" not in frontmatter


def test_atomic_write_failure_keeps_old_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the previous target intact when the atomic replacement operation fails."""
    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")

    def fail_replace(source: Path, destination: Path) -> None:
        """Simulate an operating-system replacement failure."""
        raise OSError("replace failed")

    monkeypatch.setattr("agent_harness.utils.atomic_files.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "old"
