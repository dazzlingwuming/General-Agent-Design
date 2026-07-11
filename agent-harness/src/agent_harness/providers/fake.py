from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from agent_harness.domain.messages import CanonicalMessage, ToolCall
from agent_harness.domain.model import ModelRequest, ModelResponse, ProviderCapabilities
from agent_harness.domain.errors import ProviderProtocolError


@dataclass(slots=True)
class ScriptedStep:
    content: str = ""
    tool_calls: list[ToolCall] | None = None
    finish_reason: str | None = None
    delay_seconds: float = 0.0
    error: Exception | None = None


class FakeModelProvider:
    name = "fake"
    capabilities = ProviderCapabilities(
        supports_tools=True,
        supports_parallel_tool_calls=False,
        supports_usage=False,
        max_context_tokens=200000,
    )

    def __init__(self, steps: list[ScriptedStep] | None = None, scripts_by_agent: dict[str, list[ScriptedStep]] | None = None):
        """Initialize the fake provider with an ordered scripted response list."""
        self.steps = steps or []
        self.scripts_by_agent = scripts_by_agent or {}
        self.calls = 0
        self.agent_calls: dict[str, int] = {}

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Return the next scripted response without calling a network service."""
        agent_name = request.request_metadata.get("agent_name")
        script_key = self._script_key(request)
        if script_key in self.scripts_by_agent:
            steps = self.scripts_by_agent[script_key]
            index = self.agent_calls.get(script_key, 0)
            if index >= len(steps):
                raise ProviderProtocolError(f"Fake provider script exhausted for agent: {script_key}")
            self.agent_calls[script_key] = index + 1
            step = steps[index]
            return await self._response_from_step(step, request)
        if self.calls >= len(self.steps):
            raise ProviderProtocolError("Fake provider script exhausted")
        step = self.steps[self.calls]
        self.calls += 1
        return await self._response_from_step(step, request)

    async def _response_from_step(self, step: ScriptedStep, request: ModelRequest) -> ModelResponse:
        """Convert one scripted step into a canonical response after delay/error hooks."""
        if step.delay_seconds:
            await asyncio.sleep(step.delay_seconds)
        if step.error:
            raise step.error
        tool_calls = step.tool_calls or []
        message = CanonicalMessage(role="assistant", content=step.content, tool_calls=tool_calls)
        return ModelResponse(
            assistant_message=message,
            tool_calls=tool_calls,
            finish_reason=step.finish_reason or ("tool_calls" if tool_calls else "stop"),
            model=request.model,
        )

    def _script_key(self, request: ModelRequest) -> str:
        """Resolve the most specific fake script key for the current model request."""
        agent_name = str(request.request_metadata.get("agent_name", ""))
        turn_sequence = request.request_metadata.get("turn_sequence")
        model_call_sequence = request.request_metadata.get("model_call_sequence")
        candidates: list[str] = []
        if agent_name and turn_sequence and model_call_sequence:
            candidates.append(f"{agent_name}:{turn_sequence}:{model_call_sequence}")
        if agent_name and turn_sequence:
            candidates.append(f"{agent_name}:{turn_sequence}")
        if agent_name:
            candidates.append(agent_name)
        for candidate in candidates:
            if candidate in self.scripts_by_agent:
                return candidate
        return agent_name

    async def close(self) -> None:
        """Provide the same async close interface as real providers."""
        return None


def default_demo_provider() -> FakeModelProvider:
    """Create a Chinese-readable scripted provider for local demo runs."""
    return FakeModelProvider(
        [
            ScriptedStep(tool_calls=[ToolCall(id="call_1", name="list_files", arguments={"path": ".", "recursive": True, "max_depth": 4})]),
            ScriptedStep(tool_calls=[ToolCall(id="call_2", name="search_text", arguments={"query": "calculate_total", "path": ".", "glob": "*.py", "max_results": 20})]),
            ScriptedStep(tool_calls=[ToolCall(id="call_3", name="read_file", arguments={"path": "calculator/pricing.py", "start_line": 1, "end_line": 120})]),
            ScriptedStep(content="已确认：calculator/pricing.py 中的 calculate_total 是价格计算入口。折扣在小计计算之后、calculate_total 内部应用；这个结论基于前几轮工具返回的证据。"),
        ]
    )
