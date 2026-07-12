from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Callable

from agent_harness.context.builder import ContextBuilder
from agent_harness.domain.agent import AgentDefinition
from agent_harness.domain.errors import BudgetExceededError, CancellationError, HarnessError, ProviderProtocolError
from agent_harness.domain.messages import CanonicalMessage
from agent_harness.domain.model import ModelProvider, Usage
from agent_harness.domain.run import RunState, RunStatus
from agent_harness.runtime.completion import CompletionPolicy, TextFinalCompletionPolicy
from agent_harness.tools.runtime import ToolExecutionPrincipal, ToolRuntime
from agent_harness.security.models import ApprovalPolicy, Capability, SandboxMode
from agent_harness.tracing.jsonl import JsonlTraceSink
from agent_harness.runtime.budgets import check_iteration, check_model_calls, check_tool_calls, check_wall_time
from agent_harness.utils.time import utc_now


@dataclass(slots=True)
class AgentLoop:
    agent: AgentDefinition
    provider: ModelProvider
    context_builder: ContextBuilder
    tool_runtime: ToolRuntime
    trace: JsonlTraceSink
    terminal_tool_names: set[str] = field(default_factory=set)
    final_guard: Callable[[], str | None] | None = None
    completion_policy: CompletionPolicy | None = None
    mailbox_provider: Callable[[], list[str]] | None = None
    sandbox_mode: SandboxMode = SandboxMode.WORKSPACE_WRITE
    approval_policy: ApprovalPolicy = ApprovalPolicy.ON_REQUEST
    parent_agent_id: str | None = None
    depth: int = 0
    enabled_tools_provider: Callable[[list[str]], list[str]] | None = None
    tool_success_callback: Callable[[str, dict], None] | None = None

    async def run(self, state: RunState) -> RunState:
        """Drive the single-agent model/tool loop until completion or failure."""
        policy = self.completion_policy or TextFinalCompletionPolicy(self.final_guard)
        terminal_tool_names = self.terminal_tool_names | policy.terminal_tool_names()
        state.status = RunStatus.RUNNING
        state.updated_at = utc_now()
        self.trace.emit("run.started", payload={"agent": self.agent.name})
        try:
            while state.status == RunStatus.RUNNING:
                if state.cancellation_requested:
                    raise CancellationError("Run cancellation requested")
                check_wall_time(state, self.agent.limits)
                check_iteration(state, self.agent.limits)
                state.iteration += 1
                self._drain_mailbox(state)
                self.trace.emit("iteration.started", iteration=state.iteration)
                request = self.context_builder.build(state, self.agent, self.tool_runtime.registry)
                self.trace.emit(
                    "context.built",
                    iteration=state.iteration,
                    payload={"message_count": len(request.messages), "tool_count": len(request.tools)},
                )
                check_model_calls(state, self.agent.limits)
                self.trace.emit("model.requested", iteration=state.iteration, payload={"model": request.model})
                state.model_call_count += 1
                response = await self.provider.complete(request)
                self._add_usage(state, response.usage)
                self.trace.emit(
                    "model.completed",
                    iteration=state.iteration,
                    payload={"finish_reason": response.finish_reason, "tool_call_count": len(response.tool_calls)},
                )
                state.messages.append(response.assistant_message)
                if response.tool_calls:
                    for call in response.tool_calls:
                        if state.cancellation_requested:
                            raise CancellationError("Run cancellation requested")
                        check_tool_calls(state, self.agent.limits)
                        self.trace.emit("tool.requested", iteration=state.iteration, payload={"tool": call.name, "tool_call_id": call.id})
                        self.trace.emit("tool.started", iteration=state.iteration, payload={"tool": call.name, "tool_call_id": call.id})
                        result = await self.tool_runtime.execute(call, self._principal(state))
                        if result.status == "success" and self.tool_success_callback and isinstance(call.arguments, dict):
                            self.tool_success_callback(call.name, call.arguments)
                        state.tool_call_count += 1
                        state.messages.append(
                            CanonicalMessage(
                                role="tool",
                                content=result.content,
                                tool_call_id=result.tool_call_id,
                                tool_name=result.tool_name,
                                metadata={"status": result.status, "error_code": result.error_code},
                            )
                        )
                        self._drain_mailbox(state)
                        if result.status == "success" and call.name in terminal_tool_names:
                            state.final_output = result.content
                            state.status = RunStatus.COMPLETED
                            state.completed_at = utc_now()
                            state.updated_at = state.completed_at
                            self.trace.emit(
                                "agent.result_submitted",
                                iteration=state.iteration,
                                payload={"tool": call.name, "tool_call_id": call.id},
                            )
                            self.trace.emit("iteration.completed", iteration=state.iteration, payload={"continued": False})
                            return state
                        event_name = "tool.completed" if result.status == "success" else "tool.failed"
                        if result.status == "timeout":
                            event_name = "tool.timed_out"
                        self.trace.emit(
                            event_name,
                            iteration=state.iteration,
                            payload={
                                "tool": result.tool_name,
                                "tool_call_id": result.tool_call_id,
                                "status": result.status,
                                "error_code": result.error_code,
                                "duration_ms": result.duration_ms,
                            },
                        )
                    self.trace.emit("iteration.completed", iteration=state.iteration, payload={"continued": True})
                    continue
                final_text = (response.assistant_message.content or "").strip()
                if not final_text:
                    raise ProviderProtocolError("Model returned neither tool calls nor final text")
                decision = policy.on_text_response(state, final_text)
                if not decision.should_complete:
                    state.messages.append(CanonicalMessage(role="user", content=decision.repair_message or "请继续。"))
                    self.trace.emit("agent.output_repair_requested", iteration=state.iteration, payload={"final_text_chars": len(final_text)})
                    self.trace.emit("iteration.completed", iteration=state.iteration, payload={"continued": True, "guarded": True})
                    continue
                state.final_output = final_text
                state.status = RunStatus.COMPLETED
                state.completed_at = utc_now()
                state.updated_at = state.completed_at
                self.trace.emit("iteration.completed", iteration=state.iteration, payload={"continued": False})
                self.trace.emit("run.completed", iteration=state.iteration, payload={"final_output_chars": len(final_text)})
                return state
            return state
        except asyncio.CancelledError as exc:
            state.status = RunStatus.CANCELLED
            state.completed_at = utc_now()
            state.updated_at = state.completed_at
            state.error = CancellationError("Run task was cancelled").to_run_error()
            self.trace.emit("run.cancelled", iteration=state.iteration, payload={"error": state.error})
            raise exc
        except CancellationError as exc:
            state.status = RunStatus.CANCELLED
            state.completed_at = utc_now()
            state.updated_at = state.completed_at
            state.error = exc.to_run_error()
            self.trace.emit("run.cancelled", iteration=state.iteration, payload={"error": state.error})
            return state
        except BudgetExceededError as exc:
            state.status = RunStatus.FAILED
            state.completed_at = utc_now()
            state.updated_at = state.completed_at
            state.error = exc.to_run_error()
            self.trace.emit("budget.exceeded", iteration=state.iteration, payload=state.error.details)
            self.trace.emit("run.failed", iteration=state.iteration, payload={"error": state.error})
            return state
        except HarnessError as exc:
            state.status = RunStatus.FAILED
            state.completed_at = utc_now()
            state.updated_at = state.completed_at
            state.error = exc.to_run_error()
            self.trace.emit("run.failed", iteration=state.iteration, payload={"error": state.error})
            return state
        except Exception as exc:
            state.status = RunStatus.FAILED
            state.completed_at = utc_now()
            state.updated_at = state.completed_at
            state.error = HarnessError(str(exc)).to_run_error()
            self.trace.emit("run.failed", iteration=state.iteration, payload={"error": state.error})
            return state

    def _add_usage(self, state: RunState, usage: Usage) -> None:
        """Accumulate provider token usage fields when the provider returns them."""
        for field_name in ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens"):
            value = getattr(usage, field_name)
            if value is None:
                continue
            current = getattr(state.usage_total, field_name)
            setattr(state.usage_total, field_name, value if current is None else current + value)

    def _principal(self, state: RunState) -> ToolExecutionPrincipal:
        """Build the execution principal for the current agent and turn."""
        capabilities = {Capability.FILE_READ}
        if self.agent.name == "coding_assistant":
            capabilities.update(
                {
                    Capability.FILE_WRITE,
                    Capability.FILE_DELETE,
                    Capability.COMMAND_EXECUTE,
                    Capability.MCP_TOOL_CALL,
                    Capability.NETWORK_ACCESS,
                    Capability.EXTERNAL_SIDE_EFFECT,
                }
            )
        if self.agent.can_spawn_subagents:
            capabilities.add(Capability.SUBAGENT_CREATE)
        if any(name.startswith("mcp__") for name in self.agent.enabled_tools):
            capabilities.update({Capability.MCP_TOOL_CALL, Capability.NETWORK_ACCESS, Capability.EXTERNAL_SIDE_EFFECT})
        return ToolExecutionPrincipal(
            session_id=state.run_id,
            thread_id=state.run_id,
            turn_id=state.turn_id or "turn_unknown",
            agent_id=self.agent.name,
            parent_agent_id=self.parent_agent_id,
            depth=self.depth,
            allowed_tools=frozenset(self.enabled_tools_provider(self.agent.enabled_tools) if self.enabled_tools_provider else self.agent.enabled_tools),
            capabilities=frozenset(capabilities),
            sandbox_mode=self.sandbox_mode,
            approval_policy=self.approval_policy,
        )

    def _drain_mailbox(self, state: RunState) -> None:
        """Append pending parent follow-up messages at safe model-loop boundaries."""
        if not self.mailbox_provider:
            return
        for message in self.mailbox_provider():
            state.messages.append(CanonicalMessage(role="user", content=f"父 Agent 追加指令：\n{message}"))
