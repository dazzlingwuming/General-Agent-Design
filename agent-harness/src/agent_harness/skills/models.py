from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class SkillScope(StrEnum):
    """Supported precedence scopes for skill discovery."""

    BUNDLED = "bundled"
    ADMIN = "admin"
    USER = "user"
    PROJECT = "project"


@dataclass(frozen=True, slots=True)
class SkillDiagnostic:
    """One recoverable skill discovery or activation problem."""

    level: str
    code: str
    message: str
    path: str | None = None


@dataclass(frozen=True, slots=True)
class SkillResource:
    """One lazily readable supporting file under a skill directory."""

    relative_path: str
    kind: str
    byte_size: int
    content_hash: str = ""


@dataclass(frozen=True, slots=True)
class SkillRecord:
    """Validated skill metadata discovered without loading its instruction body."""

    skill_id: str
    qualified_name: str
    name: str
    description: str
    scope: SkillScope
    base_dir: Path
    skill_path: Path
    metadata_hash: str
    license: str | None = None
    compatibility: str | None = None
    metadata: tuple[tuple[str, str], ...] = ()
    allowed_tools: tuple[str, ...] = ()
    disable_model_invocation: bool = False
    user_invocable: bool = True
    argument_hint: str | None = None
    context_mode: str = "inline"
    agent: str | None = None
    trusted: bool = True
    enabled: bool = True
    resources: tuple[SkillResource, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillCatalogSnapshot:
    """Budgeted model-visible skill metadata for one runtime."""

    catalog_id: str
    skills: tuple[SkillRecord, ...]
    rendered: str
    char_count: int
    omitted_skill_ids: tuple[str, ...] = ()
    diagnostics: tuple[SkillDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillActivationSnapshot:
    """Fully rendered skill content persisted for thread resume."""

    activation_id: str
    skill_snapshot_id: str
    skill_id: str
    qualified_name: str
    activated_turn_id: str
    arguments: str
    arguments_hash: str
    rendered_instructions: str
    content_hash: str
    source_path: str
    allowed_tools: tuple[str, ...]
    context_mode: str
    agent: str | None
    resources: tuple[SkillResource, ...]
    context_priority: str = "durable_guidance"
    protected_from_normal_pruning: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize this activation for durable thread snapshots."""
        data = asdict(self)
        return data
