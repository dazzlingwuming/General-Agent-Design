from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ExternalContextItem:
    """User-selected, untrusted external content queued for a model boundary."""

    source_kind: Literal["mcp_resource", "mcp_prompt"]
    server_name: str
    source_name: str
    mime_type: str | None
    content_hash: str
    trust_label: str
    size_bytes: int
    content: str
    artifact_id: str | None = None

    def render(self) -> str:
        """Render an explicit untrusted user-context envelope for model input."""
        return (
            f'<external_context kind="{self.source_kind}" server="{self.server_name}" source="{self.source_name}" '
            f'trust="{self.trust_label}" sha256="{self.content_hash}">\n{self.content}\n</external_context>'
        )
