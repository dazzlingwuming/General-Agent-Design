from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from mcp import types


@dataclass(frozen=True, slots=True)
class MCPNormalizedToolResult:
    """Losslessly retain standard MCP result channels without flattening mixed content."""

    structured_content: dict[str, Any] | None
    text_content: tuple[str, ...]
    resource_links: tuple[dict[str, Any], ...]
    embedded_resources: tuple[dict[str, Any], ...]
    images: tuple[dict[str, Any], ...]
    audio: tuple[dict[str, Any], ...]
    is_error: bool

    def model_payload(self) -> dict[str, Any]:
        """Build a JSON-safe model payload while preserving every content channel."""
        return {"structured_content": self.structured_content, "text": list(self.text_content), "resource_links": list(self.resource_links), "embedded_resources": list(self.embedded_resources), "images": list(self.images), "audio": list(self.audio), "is_error": self.is_error}

    def error_message(self) -> str:
        """Render a compact model-visible execution error from server content."""
        return "\n".join(self.text_content) if self.text_content else json.dumps(self.model_payload(), ensure_ascii=False)


def normalize_tool_result(result: types.CallToolResult) -> MCPNormalizedToolResult:
    """Partition mixed MCP content into stable typed channels."""
    texts: list[str] = []
    links: list[dict[str, Any]] = []
    resources: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    audio: list[dict[str, Any]] = []
    for item in result.content:
        payload = item.model_dump(mode="json", by_alias=True, exclude_none=True)
        if isinstance(item, types.TextContent):
            texts.append(item.text)
        elif isinstance(item, types.ResourceLink):
            links.append(payload)
        elif isinstance(item, types.EmbeddedResource):
            resources.append(payload)
        elif isinstance(item, types.ImageContent):
            images.append(payload)
        elif isinstance(item, types.AudioContent):
            audio.append(payload)
        else:
            resources.append(payload)
    return MCPNormalizedToolResult(result.structuredContent, tuple(texts), tuple(links), tuple(resources), tuple(images), tuple(audio), bool(result.isError))
