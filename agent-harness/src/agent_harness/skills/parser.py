from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from agent_harness.skills.models import SkillDiagnostic, SkillRecord, SkillResource, SkillScope

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def parse_skill_metadata(
    path: Path,
    scope: SkillScope,
    qualified_prefix: str,
    *,
    trusted: bool,
    max_skill_file_bytes: int = 1048576,
    max_frontmatter_bytes: int = 16384,
    max_resource_files: int = 200,
) -> tuple[SkillRecord | None, list[SkillDiagnostic]]:
    """Parse only SKILL.md frontmatter and build a lazy resource manifest."""
    diagnostics: list[SkillDiagnostic] = []
    try:
        frontmatter_text = read_skill_frontmatter(path, max_frontmatter_bytes, max_skill_file_bytes)
    except (OSError, UnicodeDecodeError) as exc:
        return None, [SkillDiagnostic("error", "skill_read", str(exc), str(path))]
    try:
        frontmatter = yaml.safe_load(frontmatter_text) or {}
        if not isinstance(frontmatter, dict):
            raise ValueError("SKILL.md Frontmatter 必须是映射")
    except (ValueError, yaml.YAMLError) as exc:
        return None, [SkillDiagnostic("error", "invalid_frontmatter", str(exc), str(path))]
    name = str(frontmatter.get("name") or "")
    description = str(frontmatter.get("description") or "").strip()
    if not NAME_RE.fullmatch(name) or len(name) > 64:
        return None, [SkillDiagnostic("error", "invalid_name", "Skill name 不符合开放标准", str(path))]
    if not description or len(description) > 1024:
        return None, [SkillDiagnostic("error", "invalid_description", "Skill description 必须为 1-1024 字符", str(path))]
    if path.parent.name != name:
        diagnostics.append(SkillDiagnostic("warning", "directory_name_mismatch", "Skill name 与父目录名不一致", str(path)))
    metadata = frontmatter.get("metadata") or {}
    if not isinstance(metadata, dict):
        return None, [SkillDiagnostic("error", "invalid_metadata", "Skill metadata 必须是映射", str(path))]
    allowed = frontmatter.get("allowed-tools", "")
    allowed_tools = tuple(str(allowed).split()) if isinstance(allowed, str) else tuple(str(item) for item in allowed or [])
    digest = hashlib.sha256(frontmatter_text.encode("utf-8")).hexdigest()
    qualified = f"{qualified_prefix}:{name}"
    return SkillRecord(
        skill_id=qualified,
        qualified_name=qualified,
        name=name,
        description=description,
        scope=scope,
        base_dir=path.parent.resolve(),
        skill_path=path.resolve(),
        metadata_hash=digest,
        license=str(frontmatter["license"]) if frontmatter.get("license") else None,
        compatibility=str(frontmatter["compatibility"]) if frontmatter.get("compatibility") else None,
        metadata=tuple(sorted((str(key), str(value)) for key, value in metadata.items())),
        allowed_tools=allowed_tools,
        disable_model_invocation=bool(frontmatter.get("disable-model-invocation", False)),
        user_invocable=bool(frontmatter.get("user-invocable", True)),
        argument_hint=str(frontmatter["argument-hint"]) if frontmatter.get("argument-hint") else None,
        context_mode=str(frontmatter.get("context", "inline")),
        agent=str(frontmatter["agent"]) if frontmatter.get("agent") else None,
        trusted=trusted,
        resources=_resource_manifest(path.parent, max_resource_files),
    ), diagnostics


def read_skill_frontmatter(path: Path, max_frontmatter_bytes: int, max_skill_file_bytes: int) -> str:
    """Read bounded YAML frontmatter and stop before the instruction body."""
    if path.stat().st_size > max_skill_file_bytes:
        raise ValueError("SKILL.md 超过大小限制")
    consumed = 0
    lines: list[str] = []
    with path.open("r", encoding="utf-8") as stream:
        first = stream.readline()
        consumed += len(first.encode("utf-8"))
        if first.rstrip("\r\n") != "---":
            raise ValueError("SKILL.md 缺少 YAML Frontmatter")
        for line in stream:
            consumed += len(line.encode("utf-8"))
            if consumed > max_frontmatter_bytes:
                raise ValueError("SKILL.md Frontmatter 超过大小限制")
            if line.rstrip("\r\n") == "---":
                return "".join(lines)
            lines.append(line)
    raise ValueError("SKILL.md Frontmatter 缺少结束标记")


def split_skill_file(raw: str) -> tuple[dict, str]:
    """Split required YAML frontmatter from the Markdown instruction body."""
    if not raw.startswith("---\n"):
        raise ValueError("SKILL.md 缺少 YAML Frontmatter")
    marker = raw.find("\n---\n", 4)
    if marker < 0:
        raise ValueError("SKILL.md Frontmatter 缺少结束标记")
    metadata = yaml.safe_load(raw[4:marker]) or {}
    if not isinstance(metadata, dict):
        raise ValueError("SKILL.md Frontmatter 必须是映射")
    return metadata, raw[marker + 5 :].strip()


def _resource_manifest(base_dir: Path, max_files: int = 200) -> tuple[SkillResource, ...]:
    """List supporting files without reading their content or executing scripts."""
    resources: list[SkillResource] = []
    for directory, kind in (("references", "reference"), ("assets", "asset"), ("scripts", "script")):
        root = base_dir / directory
        if not root.exists():
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            if len(resources) >= max_files:
                return tuple(resources)
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(base_dir.resolve(strict=True))
                resources.append(SkillResource(resolved.relative_to(base_dir.resolve()).as_posix(), kind, resolved.stat().st_size, _file_hash(resolved)))
            except (OSError, ValueError):
                continue
    return tuple(resources)


def _file_hash(path: Path) -> str:
    """Hash one resource for later activation consistency checks."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
