from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class MemoryScope(StrEnum):
    """Isolation scope used by retrieval and deletion policies."""

    THREAD = "thread"
    PROJECT = "project"
    AGENT = "agent"


class MemorySourceKind(StrEnum):
    """Origin category retained with every durable memory."""

    USER_EXPLICIT = "user_explicit"
    TOOL_VERIFIED = "tool_verified"
    TEST_VERIFIED = "test_verified"
    MODEL_INFERRED = "model_inferred"
    MCP_EXTERNAL = "mcp_external"
    COMPACTION_SUMMARY = "compaction_summary"
    IMPORTED = "imported"


class VerificationStatus(StrEnum):
    """Evidence status which is intentionally independent from confidence."""

    USER_ASSERTED = "user_asserted"
    VERIFIED = "verified"
    INFERRED = "inferred"
    UNTRUSTED_EXTERNAL = "untrusted_external"
    STALE = "stale"
    CONFLICTED = "conflicted"


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """One sourced, scoped, revocable long-term memory record."""

    schema_version: int
    memory_id: str
    namespace: str
    scope: MemoryScope
    memory_type: str
    content: str
    structured_data: dict[str, Any]
    source_kind: MemorySourceKind
    verification_status: VerificationStatus
    confidence: float
    trust_label: str
    source_thread_id: str
    source_turn_id: str | None
    source_item_ids: tuple[str, ...]
    source_artifact_ids: tuple[str, ...]
    content_hash: str
    created_by: str
    created_at: str
    updated_at: str
    project_identity: str | None = None
    thread_id: str | None = None
    agent_name: str | None = None
    supersedes_id: str | None = None
    expires_at: str | None = None
    invalidated_at: str | None = None
    invalidation_reason: str | None = None
    sensitivity: str = "normal"
    tags: tuple[str, ...] = ()
