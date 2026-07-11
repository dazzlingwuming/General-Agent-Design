from __future__ import annotations

from typing import Any

from agent_harness.domain.subagents import DelegationRequest
from agent_harness.domain.tools import ToolDefinition
from agent_harness.security.models import Capability, RiskLevel
from agent_harness.runtime.subagents.scheduler import SubagentScheduler
from agent_harness.tools.registry import ToolRegistry


def register_subagent_control_tools(registry: ToolRegistry, scheduler: SubagentScheduler, timeout_seconds: int = 30) -> None:
    """Register root-only subagent control tools into the root tool registry."""
    registry.register(_spawn_tool(scheduler, timeout_seconds))
    registry.register(_wait_tool(scheduler, timeout_seconds))
    registry.register(_status_tool(scheduler, timeout_seconds))
    registry.register(_send_message_tool(scheduler, timeout_seconds))
    registry.register(_cancel_tool(scheduler, timeout_seconds))
    registry.register(_close_tool(scheduler, timeout_seconds))


def _spawn_tool(scheduler: SubagentScheduler, timeout_seconds: int) -> ToolDefinition:
    """Create the non-blocking spawn_subagent control tool."""

    async def executor(args: dict[str, Any]) -> dict[str, Any]:
        """Spawn a child agent and immediately return its handle."""
        return await scheduler.spawn(
            DelegationRequest(
                agent_name=args["agent_name"],
                task=args["task"],
                context=args.get("context", ""),
                expected_focus=args.get("expected_focus", ""),
                idempotency_key=args.get("idempotency_key"),
            )
        )

    return ToolDefinition(
        name="spawn_subagent",
        description="创建一个子 Agent 线程并立即返回 handle，不等待其完成。",
        input_schema={
            "type": "object",
            "properties": {
                "agent_name": {"type": "string"},
                "task": {"type": "string"},
                "context": {"type": "string"},
                "expected_focus": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["agent_name", "task"],
            "additionalProperties": False,
        },
        executor=executor,
        timeout_seconds=timeout_seconds,
        risk_level=RiskLevel.LOW,
        required_capabilities=frozenset({Capability.SUBAGENT_CREATE}),
    )


def _wait_tool(scheduler: SubagentScheduler, timeout_seconds: int) -> ToolDefinition:
    """Create the wait_subagents control tool."""

    async def executor(args: dict[str, Any]) -> dict[str, Any]:
        """Wait for all or any selected child agents."""
        return await scheduler.wait(
            agent_ids=args.get("agent_ids"),
            mode=args.get("mode", "all"),
            timeout_seconds=args.get("timeout_seconds"),
        )

    return ToolDefinition(
        name="wait_subagents",
        description="等待一个或多个子 Agent 完成，返回结构化摘要。",
        input_schema={
            "type": "object",
            "properties": {
                "agent_ids": {"type": "array"},
                "mode": {"type": "string"},
                "timeout_seconds": {"type": "number"},
            },
            "required": [],
            "additionalProperties": False,
        },
        executor=executor,
        timeout_seconds=timeout_seconds,
        risk_level=RiskLevel.READ_ONLY,
        required_capabilities=frozenset(),
    )


def _status_tool(scheduler: SubagentScheduler, timeout_seconds: int) -> ToolDefinition:
    """Create the get_subagent_status control tool."""

    async def executor(args: dict[str, Any]) -> dict[str, Any]:
        """Return current state for one child agent."""
        return scheduler.status(args["agent_id"])

    return ToolDefinition(
        name="get_subagent_status",
        description="查看一个子 Agent 的当前状态。",
        input_schema={
            "type": "object",
            "properties": {"agent_id": {"type": "string"}},
            "required": ["agent_id"],
            "additionalProperties": False,
        },
        executor=executor,
        timeout_seconds=timeout_seconds,
        risk_level=RiskLevel.READ_ONLY,
        required_capabilities=frozenset(),
    )


def _send_message_tool(scheduler: SubagentScheduler, timeout_seconds: int) -> ToolDefinition:
    """Create the send_subagent_message follow-up control tool."""

    async def executor(args: dict[str, Any]) -> dict[str, Any]:
        """Send a follow-up message to a child thread."""
        return await scheduler.send_message(args["agent_id"], args["message"])

    return ToolDefinition(
        name="send_subagent_message",
        description="向已有子 Agent 线程发送追加指令。",
        input_schema={
            "type": "object",
            "properties": {"agent_id": {"type": "string"}, "message": {"type": "string"}},
            "required": ["agent_id", "message"],
            "additionalProperties": False,
        },
        executor=executor,
        timeout_seconds=timeout_seconds,
        risk_level=RiskLevel.LOW,
        required_capabilities=frozenset(),
    )


def _cancel_tool(scheduler: SubagentScheduler, timeout_seconds: int) -> ToolDefinition:
    """Create the cancel_subagent control tool."""

    async def executor(args: dict[str, Any]) -> dict[str, Any]:
        """Cancel one child agent task."""
        return await scheduler.cancel(args["agent_id"])

    return ToolDefinition(
        name="cancel_subagent",
        description="取消一个仍在运行或排队的子 Agent。",
        input_schema={
            "type": "object",
            "properties": {"agent_id": {"type": "string"}},
            "required": ["agent_id"],
            "additionalProperties": False,
        },
        executor=executor,
        timeout_seconds=timeout_seconds,
        risk_level=RiskLevel.LOW,
        required_capabilities=frozenset(),
    )


def _close_tool(scheduler: SubagentScheduler, timeout_seconds: int) -> ToolDefinition:
    """Create the close_subagent control tool."""

    async def executor(args: dict[str, Any]) -> dict[str, Any]:
        """Close one child agent thread."""
        return await scheduler.close(args["agent_id"], force=bool(args.get("force", False)))

    return ToolDefinition(
        name="close_subagent",
        description="关闭一个子 Agent 线程；运行中线程需要 force=true。",
        input_schema={
            "type": "object",
            "properties": {"agent_id": {"type": "string"}, "force": {"type": "boolean"}},
            "required": ["agent_id"],
            "additionalProperties": False,
        },
        executor=executor,
        timeout_seconds=timeout_seconds,
        risk_level=RiskLevel.LOW,
        required_capabilities=frozenset(),
    )
