from __future__ import annotations

from pathlib import Path

from agent_harness.guidance.discovery import GuidanceManager
from agent_harness.guidance.imports import ImportLimits, expand_imports
from agent_harness.guidance.models import WorkingSet
from agent_harness.guidance.rules import activate_rules
from agent_harness.guidance.trust import WorkspaceTrustState, WorkspaceTrustStore


def manager(workspace: Path, user_root: Path, *, limit: int = 32768) -> GuidanceManager:
    """Create a deterministic GuidanceManager for one temporary repository."""
    return GuidanceManager(workspace, workspace, user_root, max_guidance_bytes=limit)


def test_guidance_override_nested_fallback_and_order(tmp_path: Path) -> None:
    """Choose one file per directory and merge user, root, and nested scopes in order."""
    workspace = tmp_path / "repo"
    nested = workspace / "src"
    user = tmp_path / "user"
    nested.mkdir(parents=True)
    user.mkdir()
    (user / "AGENTS.md").write_text("用户规则", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("根规则", encoding="utf-8")
    (nested / "AGENTS.md").write_text("应被覆盖", encoding="utf-8")
    (nested / "AGENTS.override.md").write_text("嵌套覆盖", encoding="utf-8")
    snapshot = GuidanceManager(workspace, nested, user).discover("thread", "runtime", project_trusted=True)
    assert [item.content for item in snapshot.documents] == ["用户规则", "根规则", "嵌套覆盖"]


def test_untrusted_project_guidance_is_not_injected(tmp_path: Path) -> None:
    """Keep discovered project instructions outside the active document list until trusted."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("不可信项目指令", encoding="utf-8")
    snapshot = manager(workspace, tmp_path / "user").discover("thread", "runtime", project_trusted=False)
    assert not snapshot.documents


def test_guidance_budget_omits_whole_document(tmp_path: Path) -> None:
    """Never truncate Markdown content midway when the byte budget is exhausted."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("123456789", encoding="utf-8")
    snapshot = manager(workspace, tmp_path / "user", limit=5).discover("thread", "runtime", project_trusted=True)
    assert snapshot.truncated and not snapshot.documents
    assert snapshot.omitted_documents


def test_import_cycle_and_code_fence_are_safe(tmp_path: Path) -> None:
    """Detect recursive imports while leaving directives inside code fences untouched."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.md").write_text("@import b.md\n```\n@import ignored.md\n```", encoding="utf-8")
    (root / "b.md").write_text("@import a.md", encoding="utf-8")
    content, diagnostics = expand_imports(root / "a.md", root, ImportLimits())
    assert "@import ignored.md" in content
    assert any(item.code == "import_cycle" for item in diagnostics)


def test_path_rule_activates_for_confirmed_path_and_respects_exclude(tmp_path: Path) -> None:
    """Activate path rules only from confirmed paths and retain activation for the turn."""
    workspace = tmp_path / "repo"
    rules = workspace / ".agents" / "rules"
    rules.mkdir(parents=True)
    (rules / "api.md").write_text('---\npaths: ["src/**/*.py"]\nexclude: ["src/generated/**"]\n---\nAPI 规则', encoding="utf-8")
    snapshot = manager(workspace, tmp_path / "user").discover("thread", "runtime", project_trusted=True)
    working = WorkingSet(confirmed_paths={str(workspace / "src" / "api.py")})
    active = activate_rules(snapshot.path_rules, working, workspace)
    assert len(active) == 1
    working.confirmed_paths = {str(workspace / "src" / "generated" / "api.py")}
    assert activate_rules(snapshot.path_rules, working, workspace) == active


def test_workspace_trust_once_and_persistent_states(tmp_path: Path) -> None:
    """Separate process-only trust from canonical persistent trust decisions."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = WorkspaceTrustStore(tmp_path / "trust.json")
    assert store.get(workspace) == WorkspaceTrustState.UNKNOWN
    store.set(workspace, WorkspaceTrustState.TRUSTED_ONCE)
    assert store.get(workspace) == WorkspaceTrustState.TRUSTED_ONCE
    second = WorkspaceTrustStore(tmp_path / "trust.json")
    assert second.get(workspace) == WorkspaceTrustState.UNKNOWN
    second.set(workspace, WorkspaceTrustState.TRUSTED)
    assert WorkspaceTrustStore(tmp_path / "trust.json").get(workspace) == WorkspaceTrustState.TRUSTED
