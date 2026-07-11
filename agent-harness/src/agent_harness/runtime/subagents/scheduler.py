from __future__ import annotations

import asyncio
import json
import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_harness.agents.registry import AgentRegistry
from agent_harness.config import HarnessConfig
from agent_harness.context.builder import ContextBuilder
from agent_harness.domain.errors import CancellationError, HarnessError, RunError
from agent_harness.domain.messages import CanonicalMessage
from agent_harness.domain.model import ModelProvider
from agent_harness.domain.run import RunState
from agent_harness.domain.subagents import AgentThreadState, AgentThreadStatus, DelegationRequest, SubagentResult
from agent_harness.runtime.agent_loop import AgentLoop
from agent_harness.runtime.budgets import RunBudgetManager
from agent_harness.runtime.completion import StructuredSubagentCompletionPolicy
from agent_harness.tools.builtins.factory import create_default_registry
from agent_harness.tools.internal.submit_result import create_submit_result_tool
from agent_harness.tools.runtime import ToolRuntime
from agent_harness.tracing.jsonl import JsonlTraceSink
from agent_harness.utils.serialization import to_jsonable
from agent_harness.utils.time import utc_now


@dataclass(slots=True)
class SubagentScheduler:
    """Run-scoped scheduler that owns all child agent threads and tasks."""

    run_id: str
    workspace_root: Path
    config: HarnessConfig
    provider: ModelProvider
    trace: JsonlTraceSink
    agent_registry: AgentRegistry
    max_concurrent: int = 3
    max_total: int = 8
    max_depth: int = 1
    _threads: dict[str, AgentThreadState] = field(default_factory=dict)
    _tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    _condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    _semaphore: asyncio.Semaphore = field(init=False)
    _idempotency: dict[str, str] = field(default_factory=dict)
    _budget: RunBudgetManager = field(init=False)
    _running_count: int = 0
    _max_concurrent_observed: int = 0

    def __post_init__(self) -> None:
        """Initialize concurrency controls after dataclass construction."""
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        self._budget = RunBudgetManager(self.config.run)

    async def spawn(self, request: DelegationRequest) -> dict[str, Any]:
        """Create a child thread and schedule it without waiting for completion."""
        if request.idempotency_key and request.idempotency_key in self._idempotency:
            return self.status(self._idempotency[request.idempotency_key])
        if len(self._threads) >= self.max_total:
            raise HarnessError("Subagent creation limit reached", details={"limit": self.max_total})
        definition = self.agent_registry.get(request.agent_name)
        if definition.can_spawn_subagents:
            raise HarnessError("Root agent cannot be spawned as a child")
        if definition.max_depth > self.max_depth:
            raise HarnessError("Subagent depth limit reached", details={"limit": self.max_depth})
        thread = AgentThreadState(
            run_id=self.run_id,
            parent_agent_id="root",
            agent_definition_name=definition.name,
            depth=1,
            task=request.task,
        )
        self._threads[thread.agent_id] = thread
        self._budget.reserve_child(thread.agent_id, definition.limits)
        if request.idempotency_key:
            self._idempotency[request.idempotency_key] = thread.agent_id
        self.trace.emit(
            "agent.spawned",
            payload={"agent_id": thread.agent_id, "agent_name": definition.name, "task": request.task},
            agent_id=thread.agent_id,
            thread_id=thread.thread_id,
            parent_agent_id=thread.parent_agent_id,
            depth=thread.depth,
        )
        self.trace.emit(
            "agent.budget_reserved",
            payload={"agent_id": thread.agent_id, "model_calls": definition.limits.max_model_calls, "tool_calls": definition.limits.max_tool_calls},
            agent_id=thread.agent_id,
            thread_id=thread.thread_id,
            parent_agent_id=thread.parent_agent_id,
            depth=thread.depth,
        )
        thread.status = AgentThreadStatus.QUEUED
        self._tasks[thread.agent_id] = asyncio.create_task(self._run_child(thread, request))
        return self.status(thread.agent_id)

    def status(self, agent_id: str) -> dict[str, Any]:
        """Return a compact serializable status for one child thread."""
        thread = self._get(agent_id)
        return {
            "agent_id": thread.agent_id,
            "agent_name": thread.agent_definition_name,
            "status": thread.status.value,
            "task": thread.task,
            "turn_count": thread.turn_count,
            "has_result": thread.last_result is not None,
            "error": thread.error.message if thread.error else None,
        }

    async def wait(self, agent_ids: list[str] | None = None, mode: str = "all", timeout_seconds: float | None = None) -> dict[str, Any]:
        """Wait for selected child agents to finish, fail, cancel, or close."""
        selected = agent_ids or list(self._threads)
        self.trace.emit("agent.wait_started", payload={"agent_ids": selected, "mode": mode, "timeout_seconds": timeout_seconds})

        async def waiter() -> None:
            """Block on condition notifications until wait criteria are satisfied."""
            async with self._condition:
                while True:
                    done = [agent_id for agent_id in selected if self._is_done(agent_id)]
                    if (mode == "any" and done) or (mode != "any" and len(done) == len(selected)):
                        return
                    await self._condition.wait()

        timed_out = False
        try:
            await asyncio.wait_for(waiter(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            timed_out = True
        results = [self._result_or_status(agent_id) for agent_id in selected]
        self.trace.emit("agent.wait_completed", payload={"agent_ids": selected, "timed_out": timed_out})
        return {"timed_out": timed_out, "results": results}

    async def send_message(self, agent_id: str, message: str) -> dict[str, Any]:
        """Add a follow-up message to a running or idle child thread."""
        thread = self._get(agent_id)
        if thread.status in {AgentThreadStatus.CLOSED, AgentThreadStatus.CANCELLED, AgentThreadStatus.FAILED}:
            raise HarnessError("Cannot send follow-up to inactive child agent")
        if len(message) > self.config.subagents.max_followup_message_chars:
            raise HarnessError("Follow-up message is too long", details={"limit": self.config.subagents.max_followup_message_chars})
        if thread.turn_count >= self.config.subagents.max_turns_per_thread:
            raise HarnessError("Child thread turn limit reached", details={"limit": self.config.subagents.max_turns_per_thread})
        thread.mailbox.append(message)
        self.trace.emit("agent.followup_enqueued", payload={"agent_id": agent_id, "message_chars": len(message)})
        if thread.status == AgentThreadStatus.IDLE:
            request = DelegationRequest(agent_name=thread.agent_definition_name, task=message, context="Follow-up in the same thread")
            thread.status = AgentThreadStatus.QUEUED
            self._tasks[thread.agent_id] = asyncio.create_task(self._run_child(thread, request))
        return self.status(agent_id)

    async def cancel(self, agent_id: str) -> dict[str, Any]:
        """Cancel one child task without cancelling sibling agents."""
        thread = self._get(agent_id)
        thread.status = AgentThreadStatus.CANCELLING
        task = self._tasks.get(agent_id)
        if task and not task.done():
            task.cancel()
            thread.status = AgentThreadStatus.CANCELLED
        else:
            thread.status = AgentThreadStatus.CANCELLED
        self.trace.emit("agent.cancel_requested", payload={"agent_id": agent_id})
        async with self._condition:
            self._condition.notify_all()
        return self.status(agent_id)

    async def close(self, agent_id: str, force: bool = False) -> dict[str, Any]:
        """Close an idle child thread, optionally cancelling it first."""
        thread = self._get(agent_id)
        if thread.status in {AgentThreadStatus.CREATED, AgentThreadStatus.QUEUED, AgentThreadStatus.RUNNING, AgentThreadStatus.CANCELLING} and not force:
            raise HarnessError("Cannot close active child without force=true")
        if force and thread.status in {AgentThreadStatus.CREATED, AgentThreadStatus.QUEUED, AgentThreadStatus.RUNNING, AgentThreadStatus.CANCELLING}:
            await self.cancel(agent_id)
        thread.status = AgentThreadStatus.CLOSED
        thread.closed_at = utc_now()
        self.trace.emit("agent.closed", payload={"agent_id": agent_id})
        async with self._condition:
            self._condition.notify_all()
        return self.status(agent_id)

    async def cancel_all(self) -> None:
        """Cancel every active child task before the root run exits."""
        self.trace.emit("supervisor.cancelling_all", payload={"active": self.active_agent_ids()})
        for agent_id in self.active_agent_ids():
            await self.cancel(agent_id)
        await asyncio.gather(*(task for task in self._tasks.values()), return_exceptions=True)

    def active_agent_ids(self) -> list[str]:
        """Return ids of child agents that can still consume work."""
        return [
            agent_id
            for agent_id, thread in self._threads.items()
            if thread.status in {AgentThreadStatus.CREATED, AgentThreadStatus.QUEUED, AgentThreadStatus.RUNNING, AgentThreadStatus.CANCELLING}
        ]

    def summary(self) -> dict[str, Any]:
        """Return aggregate child-agent counts for result.json."""
        statuses = [thread.status for thread in self._threads.values()]
        return {
            "total_spawned": len(self._threads),
            "succeeded": sum(1 for s in statuses if s == AgentThreadStatus.IDLE),
            "failed": sum(1 for s in statuses if s == AgentThreadStatus.FAILED),
            "cancelled": sum(1 for s in statuses if s == AgentThreadStatus.CANCELLED),
            "closed": sum(1 for s in statuses if s == AgentThreadStatus.CLOSED),
            "max_concurrent_observed": self._max_concurrent_observed,
            "total_child_model_calls": self._budget.used_child_model_calls,
            "total_child_tool_calls": self._budget.used_child_tool_calls,
            "budget": self._budget.summary(),
            "agent_tree": [self.status(agent_id) for agent_id in sorted(self._threads)],
        }

    async def _run_child(self, thread: AgentThreadState, request: DelegationRequest) -> None:
        """Execute one child turn while containing ordinary failures inside the thread."""
        acquired = False
        try:
            async with self._semaphore:
                acquired = True
                self._running_count += 1
                self._max_concurrent_observed = max(self._max_concurrent_observed, self._running_count)
                thread.status = AgentThreadStatus.RUNNING
                thread.updated_at = utc_now()
                thread.turn_count += 1
                self.trace.emit(
                    "agent.started",
                    payload={"agent_id": thread.agent_id, "agent_name": thread.agent_definition_name},
                    agent_id=thread.agent_id,
                    thread_id=thread.thread_id,
                    parent_agent_id=thread.parent_agent_id,
                    depth=thread.depth,
                )
                state = self._build_child_run_state(thread, request)
                definition = copy.copy(self.agent_registry.get(thread.agent_definition_name))
                if request.allowed_tools:
                    definition.enabled_tools = [name for name in definition.enabled_tools if name in request.allowed_tools or name == "submit_result"]
                registry = create_default_registry(self.workspace_root, self.config.tools.default_timeout_seconds)
                registry.register(create_submit_result_tool(self.config.tools.default_timeout_seconds))
                loop = AgentLoop(
                    agent=definition,
                    provider=self.provider,
                    context_builder=ContextBuilder(
                        char_to_token_ratio=self.config.context.char_to_token_ratio,
                        max_estimated_input_tokens=self.config.context.max_estimated_input_tokens,
                    ),
                    tool_runtime=ToolRuntime(registry=registry, max_result_chars=self.config.tools.max_result_chars),
                    trace=self.trace,
                    completion_policy=StructuredSubagentCompletionPolicy(max_repairs=1),
                    mailbox_provider=lambda: self._drain_mailbox(thread),
                )
                result_state = await loop.run(state)
                if result_state.final_output and result_state.status.value == "COMPLETED":
                    try:
                        payload = json.loads(result_state.final_output)
                    except json.JSONDecodeError as exc:
                        self.trace.emit(
                            "agent.result_validation_failed",
                            payload={"agent_id": thread.agent_id, "error": str(exc)},
                            agent_id=thread.agent_id,
                            thread_id=thread.thread_id,
                            parent_agent_id=thread.parent_agent_id,
                            depth=thread.depth,
                        )
                        raise HarnessError("Child agent did not submit valid structured output") from exc
                    result = self._make_success_result(thread, payload)
                    thread.last_result = result
                    thread.message_history = result_state.messages
                    thread.cumulative_usage = result_state.usage_total
                    self._write_result(thread, result)
                    thread.status = AgentThreadStatus.IDLE
                    self.trace.emit(
                        "agent.idle",
                        payload={"agent_id": thread.agent_id},
                        agent_id=thread.agent_id,
                        thread_id=thread.thread_id,
                        parent_agent_id=thread.parent_agent_id,
                        depth=thread.depth,
                    )
                else:
                    thread.status = AgentThreadStatus.FAILED
                    thread.error = result_state.error
                    self.trace.emit(
                        "agent.failed",
                        payload={"agent_id": thread.agent_id, "error": result_state.error},
                        agent_id=thread.agent_id,
                        thread_id=thread.thread_id,
                        parent_agent_id=thread.parent_agent_id,
                        depth=thread.depth,
                    )
                self._budget.release_child(thread.agent_id, result_state.model_call_count, result_state.tool_call_count)
                self.trace.emit(
                    "agent.budget_released",
                    payload={"agent_id": thread.agent_id, "model_calls": result_state.model_call_count, "tool_calls": result_state.tool_call_count},
                    agent_id=thread.agent_id,
                    thread_id=thread.thread_id,
                    parent_agent_id=thread.parent_agent_id,
                    depth=thread.depth,
                )
        except asyncio.CancelledError:
            thread.status = AgentThreadStatus.CANCELLED
            thread.error = CancellationError("Child agent cancelled").to_run_error()
            self.trace.emit("agent.cancelled", payload={"agent_id": thread.agent_id})
            raise
        except Exception as exc:
            thread.status = AgentThreadStatus.FAILED
            thread.error = RunError(code="SUBAGENT_FAILED", message=str(exc), category="subagent", recoverable=True, cause_type=type(exc).__name__)
            self.trace.emit("agent.failed", payload={"agent_id": thread.agent_id, "error": thread.error})
        finally:
            self._budget.release_child(thread.agent_id, 0, 0)
            if acquired and self._running_count > 0:
                self._running_count -= 1
            thread.updated_at = utc_now()
            async with self._condition:
                self._condition.notify_all()

    def _build_child_run_state(self, thread: AgentThreadState, request: DelegationRequest) -> RunState:
        """Create or extend a child RunState sharing the parent run id."""
        packet = "\n".join(
            [
                f"委派任务：{request.task}",
                f"显式上下文：{request.context or '(无)'}",
                f"关注重点：{request.expected_focus or '(无)'}",
            ]
        )
        state = RunState(task=request.task, workspace_root=self.workspace_root, agent_name=thread.agent_definition_name)
        state.run_id = self.run_id
        state.turn_count = thread.turn_count
        if thread.message_history:
            state.messages.extend(thread.message_history)
            state.messages.append(CanonicalMessage(role="user", content=f"父 Agent 新增委派 Turn：\n{packet}"))
        else:
            state.messages.append(CanonicalMessage(role="user", content=packet))
        thread.message_history = state.messages
        return state

    def _drain_mailbox(self, thread: AgentThreadState) -> list[str]:
        """Remove all pending follow-up messages from a child thread mailbox."""
        messages = list(thread.mailbox)
        thread.mailbox.clear()
        return messages

    def _make_success_result(self, thread: AgentThreadState, payload: dict[str, Any]) -> SubagentResult:
        """Convert submit_result payload into a SubagentResult envelope."""
        return SubagentResult(
            agent_id=thread.agent_id,
            agent_name=thread.agent_definition_name,
            status="succeeded",
            summary=payload.get("summary", ""),
            evidence=payload.get("evidence", []),
            unresolved_questions=payload.get("unresolved_questions", []),
            confidence=float(payload.get("confidence", 0.0)),
            structured_data=payload.get("structured_data", {}),
        )

    def _write_result(self, thread: AgentThreadState, result: SubagentResult) -> None:
        """Write the child result envelope under the parent run directory."""
        agent_dir = self.trace.run_dir / "agents" / thread.agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)
        result_path = agent_dir / f"turn-{thread.turn_count:04d}-result.json"
        result.result_ref = str(result_path)
        result_path.write_text(json.dumps(to_jsonable(result), ensure_ascii=False, indent=2), encoding="utf-8")

    def _result_or_status(self, agent_id: str) -> dict[str, Any]:
        """Return a completed result if available, otherwise a status object."""
        thread = self._get(agent_id)
        if thread.last_result:
            return to_jsonable(thread.last_result)
        return self.status(agent_id)

    def _is_done(self, agent_id: str) -> bool:
        """Return whether a thread has reached a waitable terminal or idle state."""
        return self._get(agent_id).status in {AgentThreadStatus.IDLE, AgentThreadStatus.FAILED, AgentThreadStatus.CANCELLED, AgentThreadStatus.CLOSED}

    def _get(self, agent_id: str) -> AgentThreadState:
        """Return a child thread by id or raise a readable error."""
        try:
            return self._threads[agent_id]
        except KeyError as exc:
            raise HarnessError(f"Unknown child agent: {agent_id}") from exc
