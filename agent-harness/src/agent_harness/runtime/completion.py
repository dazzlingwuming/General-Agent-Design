from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from agent_harness.domain.run import RunState


@dataclass(slots=True)
class CompletionDecision:
    """Decision returned by a completion policy after a model text response."""

    should_complete: bool
    repair_message: str | None = None


class CompletionPolicy(Protocol):
    """Protocol that separates root text completion from child structured completion."""

    def terminal_tool_names(self) -> set[str]:
        """Return tool names that terminate the current agent turn when successful."""
        ...

    def on_text_response(self, state: RunState, final_text: str) -> CompletionDecision:
        """Decide whether ordinary assistant text may finish this agent turn."""
        ...


@dataclass(slots=True)
class TextFinalCompletionPolicy:
    """Completion policy for root agents that may finish with ordinary text."""

    final_guard: Callable[[], str | None] | None = None

    def terminal_tool_names(self) -> set[str]:
        """Return no terminal tools because root completion is natural language text."""
        return set()

    def on_text_response(self, state: RunState, final_text: str) -> CompletionDecision:
        """Allow final text unless the root completion guard asks for more work first."""
        if self.final_guard:
            guard_message = self.final_guard()
            if guard_message:
                return CompletionDecision(should_complete=False, repair_message=guard_message)
        return CompletionDecision(should_complete=True)


@dataclass(slots=True)
class StructuredSubagentCompletionPolicy:
    """Completion policy for child agents that must end through submit_result."""

    max_repairs: int = 1
    repair_count: int = 0
    _terminal_tools: set[str] = field(default_factory=lambda: {"submit_result"})

    def terminal_tool_names(self) -> set[str]:
        """Return the internal terminal tools accepted for child agent completion."""
        return set(self._terminal_tools)

    def on_text_response(self, state: RunState, final_text: str) -> CompletionDecision:
        """Reject plain text once or more and ask the child to call submit_result."""
        if self.repair_count >= self.max_repairs:
            return CompletionDecision(should_complete=True)
        self.repair_count += 1
        repair = (
            "你的上一条回复没有调用 submit_result，因此不能结束子 Agent Turn。\n"
            "请立即调用 submit_result，字段必须包含 summary、evidence、unresolved_questions、confidence；"
            "不要再使用普通文本作为最终回复。"
        )
        return CompletionDecision(should_complete=False, repair_message=repair)
