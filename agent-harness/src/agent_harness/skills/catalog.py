from __future__ import annotations

import hashlib

from agent_harness.skills.models import SkillCatalogSnapshot, SkillDiagnostic, SkillRecord


def build_catalog(
    records: tuple[SkillRecord, ...], diagnostics: tuple[SkillDiagnostic, ...], *, max_chars: int
) -> SkillCatalogSnapshot:
    """Build a complete-entry metadata catalog without loading any Skill body."""
    entries: list[str] = []
    included: list[SkillRecord] = []
    omitted: list[str] = []
    for record in records:
        if not record.enabled or not record.trusted or record.disable_model_invocation:
            continue
        entry = f"- {record.qualified_name}: {record.description} (path: {record.skill_path})"
        candidate = "\n".join([*entries, entry])
        if len(candidate) > max_chars:
            omitted.append(record.skill_id)
            continue
        entries.append(entry)
        included.append(record)
    rendered = "\n".join(entries)
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    return SkillCatalogSnapshot(
        catalog_id=f"skill_catalog_{digest[:16]}",
        skills=tuple(included),
        rendered=rendered,
        char_count=len(rendered),
        omitted_skill_ids=tuple(omitted),
        diagnostics=diagnostics,
    )
