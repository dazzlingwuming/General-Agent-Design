from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from agent_harness.domain.messages import CanonicalMessage, ToolCall

FinishReason = Literal["stop", "tool_calls", "length", "content_filter", "error", "unknown"]


@dataclass(slots=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_miss_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    provider_details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProviderCapabilities:
    supports_tools: bool
    supports_parallel_tool_calls: bool = False
    supports_strict_tool_schema: bool = False
    supports_json_output: bool = False
    supports_streaming: bool = False
    supports_usage: bool = False
    max_context_tokens: int | None = None


@dataclass(slots=True)
class ModelRequest:
    model: str
    messages: list[CanonicalMessage]
    tools: list[dict[str, Any]]
    tool_choice: str | None = "auto"
    temperature: float = 0.0
    max_output_tokens: int | None = None
    timeout_seconds: int | None = None
    request_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelResponse:
    assistant_message: CanonicalMessage
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: FinishReason = "unknown"
    usage: Usage = field(default_factory=Usage)
    response_id: str | None = None
    model: str | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)


class ModelProvider(Protocol):
    name: str
    capabilities: ProviderCapabilities

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Return one canonical model response for a canonical model request."""
        ...

    async def close(self) -> None:
        """Release any provider-owned resources such as network clients."""
        ...
