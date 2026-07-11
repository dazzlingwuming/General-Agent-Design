from __future__ import annotations

from pathlib import Path

import pytest

from agent_harness.skills.activation import SkillManager, render_arguments
from agent_harness.skills.catalog import build_catalog
from agent_harness.skills.discovery import SkillDiscovery, SkillSearchPath
from agent_harness.skills.models import SkillScope
from agent_harness.skills.resources import read_skill_resource


def write_skill(root: Path, name: str, body: str = "执行 $ARGUMENTS", extra: str = "") -> Path:
    """Write one standard-compatible test Skill and return its SKILL.md path."""
    directory = root / name
    directory.mkdir(parents=True)
    path = directory / "SKILL.md"
    path.write_text(f"---\nname: {name}\ndescription: 在测试任务中使用 {name}。\n{extra}---\n{body}", encoding="utf-8")
    return path


def test_discovery_catalog_does_not_retain_body(tmp_path: Path) -> None:
    """Expose metadata through the catalog without storing the Skill instruction body."""
    root = tmp_path / "skills"
    path = write_skill(root, "code-review", body="绝不能在 Catalog 中出现的正文")
    records, diagnostics = SkillDiscovery((SkillSearchPath(SkillScope.USER, root, "user"),)).discover()
    catalog = build_catalog(records, diagnostics, max_chars=8000)
    assert len(records) == 1 and "绝不能" not in catalog.rendered
    assert records[0].skill_path == path.resolve()


def test_catalog_hides_untrusted_and_model_disabled_skills(tmp_path: Path) -> None:
    """Keep untrusted project and user-only Skill descriptions out of model context."""
    root = tmp_path / "skills"
    write_skill(root, "deploy", extra="disable-model-invocation: true\n")
    records, diagnostics = SkillDiscovery((SkillSearchPath(SkillScope.PROJECT, root, "project", False),)).discover()
    catalog = build_catalog(records, diagnostics, max_chars=8000)
    assert not catalog.skills and not catalog.rendered


def test_activation_deduplicates_and_resumes_snapshot(tmp_path: Path) -> None:
    """Persist rendered content once and resume it without the original Skill file."""
    root = tmp_path / "skills"
    path = write_skill(root, "review")
    records, _ = SkillDiscovery((SkillSearchPath(SkillScope.USER, root, "user"),)).discover()
    snapshots = tmp_path / "snapshots"
    manager = SkillManager(records, snapshots)
    first, created = manager.activate("review", "src/a.py", "turn_1", user_invocation=True)
    second, created_again = manager.activate("review", "src/a.py", "turn_2", user_invocation=True)
    assert created and not created_again and first.activation_id == second.activation_id
    path.unlink()
    resumed = SkillManager(records, snapshots)
    resumed.resume()
    assert resumed.active[0].rendered_instructions == "执行 src/a.py"


def test_qualified_name_is_required_for_collision(tmp_path: Path) -> None:
    """Reject ambiguous short names instead of silently overwriting a Skill."""
    user = tmp_path / "user"
    project = tmp_path / "project"
    write_skill(user, "review")
    write_skill(project, "review")
    records, _ = SkillDiscovery((SkillSearchPath(SkillScope.USER, user, "user"), SkillSearchPath(SkillScope.PROJECT, project, "project"))).discover()
    manager = SkillManager(records, tmp_path / "snapshots")
    with pytest.raises(ValueError, match="歧义"):
        manager.resolve("review", user_invocation=True)
    assert manager.resolve("user:review", user_invocation=True).scope == SkillScope.USER


def test_resource_requires_activation_and_blocks_escape(tmp_path: Path) -> None:
    """Read declared resources only within the activated Skill real-path boundary."""
    root = tmp_path / "skills"
    write_skill(root, "review")
    references = root / "review" / "references"
    references.mkdir()
    (references / "guide.md").write_text("参考内容", encoding="utf-8")
    records, _ = SkillDiscovery((SkillSearchPath(SkillScope.USER, root, "user"),)).discover()
    manager = SkillManager(records, tmp_path / "snapshots")
    activation, _ = manager.activate("review", "", "turn_1", user_invocation=True)
    assert read_skill_resource(records[0], activation, "references/guide.md") == "参考内容"
    with pytest.raises((ValueError, FileNotFoundError)):
        read_skill_resource(records[0], activation, "../outside.md")


def test_argument_rendering_supports_whole_and_positional_values() -> None:
    """Render standard whole and positional argument placeholders deterministically."""
    assert render_arguments("修复 $0，目标 $ARGUMENTS", "issue-1 main") == "修复 issue-1，目标 issue-1 main"
