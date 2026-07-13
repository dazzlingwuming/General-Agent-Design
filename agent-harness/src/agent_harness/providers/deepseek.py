from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any, cast

import httpx

from agent_harness.domain.errors import (
    ConfigurationError,
    ProviderAuthenticationError,
    ProviderError,
    ProviderProtocolError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from agent_harness.domain.messages import CanonicalMessage, ToolCall
from agent_harness.domain.model import FinishReason, ModelRequest, ModelResponse, ProviderCapabilities, Usage


@dataclass(slots=True)
class DeepSeekProvider:
    api_key: str | None = field(default=None, repr=False)
    base_url: str = "https://api.deepseek.com"
    timeout_seconds: int = 120
    max_attempts: int = 3
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    name: str = "deepseek"
    capabilities: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            supports_tools=True,
            supports_parallel_tool_calls=False,
            supports_strict_tool_schema=False,
            supports_json_output=True,
            supports_usage=True,
            max_context_tokens=1_000_000,
        )
    )

    def __post_init__(self) -> None:
        """Create the HTTP client after loading API key and base URL settings."""
        self.api_key = self.api_key or os.getenv("DEEPSEEK_API_KEY")
        self.base_url = os.getenv("DEEPSEEK_API_URL", self.base_url)
        if not self.api_key:
            raise ConfigurationError("DEEPSEEK_API_KEY is required for DeepSeek provider")
        self._client = httpx.AsyncClient(
            base_url=self.base_url.rstrip("/"),
            timeout=self.timeout_seconds,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Send one canonical model request to the DeepSeek-compatible endpoint."""
        if self._client is None:
            raise ConfigurationError("DeepSeek provider client is not initialized")
        payload = {
            "model": request.model,
            "messages": [self._to_provider_message(m) for m in request.messages],
            "tools": request.tools or None,
            "tool_choice": request.tool_choice if request.tools else None,
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        last_error: ProviderError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await self._client.post("/chat/completions", json=payload)
                if response.status_code in (401, 403):
                    raise ProviderAuthenticationError("DeepSeek authentication failed")
                if response.status_code == 429:
                    raise ProviderRateLimitError("DeepSeek rate limit")
                if response.status_code >= 500:
                    raise ProviderTimeoutError(f"DeepSeek server error: {response.status_code}")
                if response.status_code >= 400:
                    raise ProviderProtocolError(f"DeepSeek request failed: {response.status_code} {response.text[:300]}")
                return self._parse_response(response.json())
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = ProviderTimeoutError(str(exc))
            except ProviderRateLimitError as exc:
                last_error = exc
            except ProviderTimeoutError as exc:
                last_error = exc
            if attempt < self.max_attempts:
                await asyncio.sleep(min(2 ** (attempt - 1), 8))
        raise last_error or ProviderError("DeepSeek request failed")

    async def close(self) -> None:
        """Close the underlying async HTTP client."""
        if self._client is not None:
            await self._client.aclose()

    def _to_provider_message(self, message: CanonicalMessage) -> dict[str, Any]:
        """Convert one canonical message into Chat Completions message format."""
        if message.role == "tool":
            return {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "name": message.tool_name,
                "content": message.content,
            }
        payload: dict[str, Any] = {"role": message.role, "content": message.content or None}
        if message.reasoning_content:
            payload["reasoning_content"] = message.reasoning_content
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.raw_arguments or json.dumps(call.arguments)},
                }
                for call in message.tool_calls
            ]
        return payload

    def _parse_response(self, raw: dict[str, Any]) -> ModelResponse:
        """Parse a Chat Completions response into the internal model protocol."""
        try:
            choice = raw["choices"][0]
            msg = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderProtocolError("DeepSeek response missing choices[0].message") from exc
        tool_calls: list[ToolCall] = []
        for index, call in enumerate(msg.get("tool_calls") or []):
            function = call.get("function") or {}
            raw_args = function.get("arguments") or "{}"
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = raw_args
            tool_calls.append(
                ToolCall(
                    id=call.get("id") or f"tool_call_{index}",
                    name=function.get("name", ""),
                    arguments=args,
                    raw_arguments=raw_args,
                    provider_metadata={"type": call.get("type")},
                    sequence_index=index,
                )
            )
        assistant = CanonicalMessage(
            role="assistant",
            content=msg.get("content") or "",
            reasoning_content=msg.get("reasoning_content"),
            tool_calls=tool_calls,
        )
        usage_raw = raw.get("usage") or {}
        completion_details = usage_raw.get("completion_tokens_details") or {}
        usage = Usage(
            input_tokens=usage_raw.get("prompt_tokens"),
            output_tokens=usage_raw.get("completion_tokens"),
            total_tokens=usage_raw.get("total_tokens"),
            cached_input_tokens=usage_raw.get("prompt_cache_hit_tokens"),
            cache_miss_input_tokens=usage_raw.get("prompt_cache_miss_tokens"),
            reasoning_tokens=completion_details.get("reasoning_tokens"),
            provider_details=usage_raw,
        )
        finish = choice.get("finish_reason") or "unknown"
        if finish == "tool_calls":
            normalized = "tool_calls"
        elif finish == "stop":
            normalized = "stop"
        elif finish == "length":
            normalized = "length"
        elif finish == "content_filter":
            normalized = "content_filter"
        else:
            normalized = "unknown"
        return ModelResponse(
            assistant_message=assistant,
            tool_calls=tool_calls,
            finish_reason=cast(FinishReason, normalized),
            usage=usage,
            response_id=raw.get("id"),
            model=raw.get("model"),
            provider_metadata={"created": raw.get("created"), "reasoning_content": msg.get("reasoning_content")},
        )
