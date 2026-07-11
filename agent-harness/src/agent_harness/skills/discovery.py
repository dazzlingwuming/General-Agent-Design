from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from agent_harness.skills.models import SkillDiagnostic, SkillRecord, SkillScope
from agent_harness.skills.parser import parse_skill_metadata


@dataclass(frozen=True, slots=True)
class SkillSearchPath:
    """One configured skill directory and its trust scope."""

    scope: SkillScope
    path: Path
    prefix: str
    trusted: bool = True


@dataclass(slots=True)
class SkillDiscovery:
    """Discover bounded SKILL.md metadata across configured scopes."""

    search_paths: tuple[SkillSearchPath, ...]
    max_skills: int = 500
    max_scan_depth: int = 6
    max_directories: int = 2000
    max_skill_file_bytes: int = 1048576
    max_frontmatter_bytes: int = 16384
    max_resource_files_per_skill: int = 200
    ignored_directories: tuple[str, ...] = (".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", "build", "dist", "target", "coverage", ".next", ".cache", ".harness")

    def discover(self) -> tuple[tuple[SkillRecord, ...], tuple[SkillDiagnostic, ...]]:
        """Return all non-overwriting skill records and diagnostics in stable order."""
        records: list[SkillRecord] = []
        diagnostics: list[SkillDiagnostic] = []
        targets: set[Path] = set()
        directory_count = 0
        for search in self.search_paths:
            if not search.path.exists():
                continue
            for current, dirnames, filenames in os.walk(search.path, topdown=True, followlinks=False):
                current_path = Path(current)
                depth = len(current_path.relative_to(search.path).parts)
                dirnames[:] = sorted(name for name in dirnames if name not in self.ignored_directories and depth < self.max_scan_depth)
                directory_count += 1
                if directory_count > self.max_directories or len(records) >= self.max_skills:
                    diagnostics.append(SkillDiagnostic("warning", "scan_limit", "Skill 扫描达到配置上限", str(search.path)))
                    return tuple(records), tuple(diagnostics)
                if "SKILL.md" not in filenames:
                    continue
                path = current_path / "SKILL.md"
                try:
                    relative = path.relative_to(search.path)
                    target = path.resolve(strict=True)
                    target.parent.resolve().relative_to(search.path.resolve())
                except (OSError, ValueError):
                    diagnostics.append(SkillDiagnostic("error", "skill_boundary", "Skill 路径或 Symlink 超出搜索范围", str(path)))
                    continue
                if target in targets:
                    continue
                targets.add(target)
                nested_prefix = search.prefix
                if search.scope == SkillScope.PROJECT and len(relative.parts) > 2:
                    nested_prefix = f"project:{'/'.join(relative.parts[:-2])}"
                record, found = parse_skill_metadata(
                    path,
                    search.scope,
                    nested_prefix,
                    trusted=search.trusted,
                    max_skill_file_bytes=self.max_skill_file_bytes,
                    max_frontmatter_bytes=self.max_frontmatter_bytes,
                    max_resource_files=self.max_resource_files_per_skill,
                )
                diagnostics.extend(found)
                if record:
                    records.append(record)
        return tuple(sorted(records, key=lambda item: (item.scope.value, item.qualified_name))), tuple(diagnostics)
