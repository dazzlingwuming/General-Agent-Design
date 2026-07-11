from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

import yaml

from agent_harness.guidance.models import GuidanceDiagnostic, GuidanceDocument, WorkingSet


def split_frontmatter(content: str) -> tuple[dict, str]:
    """Parse an optional YAML frontmatter block and return metadata plus body."""
    if not content.startswith("---\n"):
        return {}, content
    marker = content.find("\n---\n", 4)
    if marker < 0:
        raise ValueError("Frontmatter 缺少结束标记")
    metadata = yaml.safe_load(content[4:marker]) or {}
    if not isinstance(metadata, dict):
        raise ValueError("Frontmatter 必须是 YAML 映射")
    return metadata, content[marker + 5 :].strip()


def rule_patterns(content: str) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    """Extract path and exclude globs from one rule document."""
    metadata, body = split_frontmatter(content)
    paths = metadata.get("paths", [])
    excludes = metadata.get("exclude", [])
    if isinstance(paths, str):
        paths = [paths]
    if isinstance(excludes, str):
        excludes = [excludes]
    return tuple(str(item) for item in paths), tuple(str(item) for item in excludes), body


def activate_rules(
    rules: tuple[GuidanceDocument, ...], working_set: WorkingSet, workspace: Path
) -> tuple[GuidanceDocument, ...]:
    """Activate deterministic path rules from confirmed paths for the current turn."""
    relative_paths: list[str] = []
    root = workspace.resolve()
    for value in sorted(working_set.confirmed_paths):
        try:
            relative_paths.append(Path(value).resolve().relative_to(root).as_posix())
        except ValueError:
            continue
    active: list[GuidanceDocument] = []
    for rule in rules:
        if not rule.path_patterns:
            active.append(rule)
            continue
        matched = any(
            any(_glob_matches(path, pattern) for pattern in rule.path_patterns)
            and not any(_glob_matches(path, pattern) for pattern in rule.exclude_patterns)
            for path in relative_paths
        )
        if matched or rule.document_id in working_set.active_rule_ids:
            working_set.active_rule_ids.add(rule.document_id)
            active.append(rule)
    return tuple(sorted(active, key=_rule_sort_key))


def _glob_matches(path: str, pattern: str) -> bool:
    """Match normalized POSIX paths, including root files for double-star patterns."""
    normalized = pattern.replace("\\", "/")
    variants = {normalized}
    if normalized.startswith("**/"):
        variants.add(normalized[3:])
    if "/**/" in normalized:
        variants.add(normalized.replace("/**/", "/"))
    return any(fnmatch(path, variant) for variant in variants)


def _rule_sort_key(rule: GuidanceDocument) -> tuple[int, int, int, str]:
    """Return stable precedence and glob-specificity ordering for a path rule."""
    fixed = max((len([part for part in pattern.split("/") if "*" not in part and "?" not in part]) for pattern in rule.path_patterns), default=0)
    wildcards = min((pattern.count("*") + pattern.count("?") for pattern in rule.path_patterns), default=0)
    return (rule.precedence, fixed, -wildcards, str(rule.path))


def rule_diagnostic(path: Path, exc: Exception) -> GuidanceDiagnostic:
    """Create a normalized diagnostic for invalid path-rule frontmatter."""
    return GuidanceDiagnostic("error", "invalid_rule", str(exc), str(path))
