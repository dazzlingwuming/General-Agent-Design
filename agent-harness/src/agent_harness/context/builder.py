from __future__ import annotations

from dataclasses import dataclass

from agent_harness.domain.agent import AgentDefinition
from agent_harness.domain.errors import ContextLimitError
from agent_harness.domain.messages import CanonicalMessage
from agent_harness.domain.model import ModelRequest
from agent_harness.domain.run import RunState
from agent_harness.tools.registry import ToolRegistry


@dataclass(slots=True)
class ContextBuilder:
    char_to_token_ratio: float = 4.0
    max_estimated_input_tokens: int = 120000
    recent_turns: int = 3

    def build(self, run: RunState, agent: AgentDefinition, registry: ToolRegistry) -> ModelRequest:
        """Build one model request from current run history and enabled tool schemas."""
        messages = [CanonicalMessage(role="system", content=agent.system_prompt), *self._visible_history(run)]
        estimated_tokens = self.estimate_tokens(messages)
        if estimated_tokens > self.max_estimated_input_tokens:
            raise ContextLimitError(
                "Estimated context size exceeds configured input token limit",
                details={"estimated_tokens": estimated_tokens, "limit": self.max_estimated_input_tokens},
            )
        return ModelRequest(
            model=agent.model,
            messages=messages,
            tools=registry.export_schemas(agent.enabled_tools),
            temperature=agent.temperature,
            max_output_tokens=agent.max_output_tokens,
            request_metadata={
                "agent_name": agent.name,
                "run_id": run.run_id,
                "turn_sequence": run.turn_count or 1,
                "model_call_sequence": run.model_call_count + 1,
            },
        )

    def _visible_history(self, run: RunState) -> list[CanonicalMessage]:
        """Return the compact session history visible to the next model request."""
        selected = self._recent_messages(run.messages)
        if run.session_summary:
            summary = CanonicalMessage(role="user", content=f"此前对话摘要：\n{run.session_summary}")
            return [summary, *selected]
        return selected

    def _recent_messages(self, messages: list[CanonicalMessage]) -> list[CanonicalMessage]:
        """Keep only the most recent user turns and their following assistant/tool messages."""
        if self.recent_turns <= 0:
            return messages
        user_indexes = [index for index, message in enumerate(messages) if message.role == "user"]
        if len(user_indexes) <= self.recent_turns:
            return messages
        start = user_indexes[-self.recent_turns]
        return messages[start:]

    def estimate_tokens(self, messages: list[CanonicalMessage]) -> int:
        """Estimate input tokens with a simple character ratio for phase 1 limits."""
        chars = sum(len(m.content or "") for m in messages)
        for message in messages:
            for call in message.tool_calls:
                chars += len(call.name) + len(str(call.arguments))
        return int(chars / self.char_to_token_ratio)
