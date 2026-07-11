from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class GuidanceSourceKind(StrEnum):
    """Supported precedence scopes for guidance documents."""

    CORE = "core"
    ADMIN = "admin"
    USER = "user"
    PROJECT = "project"
    PATH_RULE = "path_rule"


@dataclass(frozen=True, slots=True)
class GuidanceDiagnostic:
    """One non-fatal discovery or parsing problem."""

    level: str
    code: str
    message: str
    path: str | None = None


@dataclass(frozen=True, slots=True)
class GuidanceDocument:
    """One fully loaded and independently budgeted guidance document."""

    document_id: str
    source_kind: GuidanceSourceKind
    path: Path
    scope_root: Path | None
    relative_path: str | None
    content: str
    content_hash: str
    byte_size: int
    precedence: int
    directory_depth: int
    path_patterns: tuple[str, ...] = ()
    exclude_patterns: tuple[str, ...] = ()
    trusted: bool = True
    loaded_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize this document into snapshot-safe JSON data."""
        data = asdict(self)
        data["source_kind"] = self.source_kind.value
        data["path"] = str(self.path)
        data["scope_root"] = str(self.scope_root) if self.scope_root else None
        return data


@dataclass(frozen=True, slots=True)
class GuidanceSnapshot:
    """Immutable guidance content selected for one thread runtime."""

    snapshot_id: str
    runtime_instance_id: str
    thread_id: str
    documents: tuple[GuidanceDocument, ...]
    path_rules: tuple[GuidanceDocument, ...]
    combined_hash: str
    total_bytes: int
    truncated: bool = False
    omitted_documents: tuple[str, ...] = ()
    diagnostics: tuple[GuidanceDiagnostic, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialize the complete snapshot for deterministic resume and audit."""
        return {
            "snapshot_id": self.snapshot_id,
            "runtime_instance_id": self.runtime_instance_id,
            "thread_id": self.thread_id,
            "documents": [document.to_dict() for document in self.documents],
            "path_rules": [document.to_dict() for document in self.path_rules],
            "combined_hash": self.combined_hash,
            "total_bytes": self.total_bytes,
            "truncated": self.truncated,
            "omitted_documents": list(self.omitted_documents),
            "diagnostics": [asdict(item) for item in self.diagnostics],
        }


@dataclass(slots=True)
class WorkingSet:
    """Turn-scoped paths that can activate conditional guidance rules."""

    confirmed_paths: set[str] = field(default_factory=set)
    candidate_paths: set[str] = field(default_factory=set)
    active_rule_ids: set[str] = field(default_factory=set)
