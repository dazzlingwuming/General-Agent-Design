from __future__ import annotations

from dataclasses import dataclass, field

from agent_harness.domain.errors import BudgetExceededError
from agent_harness.domain.run import RunLimits, RunState
from agent_harness.utils.time import utc_now


def check_wall_time(run: RunState, limits: RunLimits) -> None:
    """Fail the run when elapsed wall time exceeds the configured limit."""
    elapsed = (utc_now() - run.started_at).total_seconds()
    if elapsed > limits.max_wall_time_seconds:
        raise BudgetExceededError("Max wall time exceeded", details={"limit": "MAX_WALL_TIME"})


def check_iteration(run: RunState, limits: RunLimits) -> None:
    """Fail before starting a new iteration beyond the configured limit."""
    if run.iteration >= limits.max_iterations:
        raise BudgetExceededError("Max iterations exceeded", details={"limit": "MAX_ITERATIONS"})


def check_model_calls(run: RunState, limits: RunLimits) -> None:
    """Fail before issuing a model call beyond the configured limit."""
    if run.model_call_count >= limits.max_model_calls:
        raise BudgetExceededError("Max model calls exceeded", details={"limit": "MAX_MODEL_CALLS"})


def check_tool_calls(run: RunState, limits: RunLimits) -> None:
    """Fail before executing a tool call beyond the configured limit."""
    if run.tool_call_count >= limits.max_tool_calls:
        raise BudgetExceededError("Max tool calls exceeded", details={"limit": "MAX_TOOL_CALLS"})


@dataclass(slots=True)
class BudgetLease:
    """Reserved local budget assigned to one child agent."""

    agent_id: str
    model_calls: int
    tool_calls: int
    released: bool = False


@dataclass(slots=True)
class RunBudgetManager:
    """Run-level budget manager that separates global and child-local budgets."""

    global_limits: RunLimits
    reserved_model_calls: int = 0
    reserved_tool_calls: int = 0
    used_child_model_calls: int = 0
    used_child_tool_calls: int = 0
    _leases: dict[str, BudgetLease] = field(default_factory=dict)

    def reserve_child(self, agent_id: str, local_limits: RunLimits) -> BudgetLease:
        """Reserve a child-local budget without exceeding global run limits."""
        next_model = self.reserved_model_calls + local_limits.max_model_calls
        next_tools = self.reserved_tool_calls + local_limits.max_tool_calls
        if next_model > self.global_limits.max_model_calls:
            raise BudgetExceededError("Child model-call budget exceeds root budget", details={"limit": "GLOBAL_MODEL_CALLS"})
        if next_tools > self.global_limits.max_tool_calls:
            raise BudgetExceededError("Child tool-call budget exceeds root budget", details={"limit": "GLOBAL_TOOL_CALLS"})
        lease = BudgetLease(agent_id=agent_id, model_calls=local_limits.max_model_calls, tool_calls=local_limits.max_tool_calls)
        self._leases[agent_id] = lease
        self.reserved_model_calls = next_model
        self.reserved_tool_calls = next_tools
        return lease

    def release_child(self, agent_id: str, used_model_calls: int, used_tool_calls: int) -> None:
        """Release a child budget lease and record the actual child usage."""
        lease = self._leases.get(agent_id)
        if not lease or lease.released:
            return
        lease.released = True
        self.reserved_model_calls -= lease.model_calls
        self.reserved_tool_calls -= lease.tool_calls
        self.used_child_model_calls += used_model_calls
        self.used_child_tool_calls += used_tool_calls

    def summary(self) -> dict[str, int]:
        """Return a compact budget accounting summary for result.json."""
        return {
            "reserved_model_calls": self.reserved_model_calls,
            "reserved_tool_calls": self.reserved_tool_calls,
            "used_child_model_calls": self.used_child_model_calls,
            "used_child_tool_calls": self.used_child_tool_calls,
        }
