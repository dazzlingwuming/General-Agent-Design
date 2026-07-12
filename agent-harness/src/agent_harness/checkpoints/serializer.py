from __future__ import annotations

from typing import Any, cast

from agent_harness.domain.messages import CanonicalMessage, MessageRole, ToolCall
from agent_harness.domain.model import Usage
from agent_harness.domain.run import RunState, RunStatus
from agent_harness.utils.serialization import to_jsonable


def serialize_run_state(state: RunState) -> dict[str, Any]:
    """Serialize only plain execution data and exclude all runtime clients and callables."""
    return to_jsonable(state)


def restore_run_state(payload: dict[str, Any], workspace_root: Any) -> RunState:
    """Rebuild mutable RunState from a versioned JSON checkpoint payload."""
    state = RunState(task=str(payload.get("task", "")), workspace_root=workspace_root)
    state.run_id = str(payload["run_id"])
    state.turn_id = payload.get("turn_id")
    state.turn_count = int(payload.get("turn_count", 0))
    state.session_summary = str(payload.get("session_summary", ""))
    state.status = RunStatus(str(payload.get("status", RunStatus.CREATED.value)))
    state.iteration = int(payload.get("iteration", 0))
    state.model_call_count = int(payload.get("model_call_count", 0))
    state.tool_call_count = int(payload.get("tool_call_count", 0))
    usage = payload.get("usage_total") or {}
    state.usage_total = Usage(
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        total_tokens=usage.get("total_tokens"),
        cached_input_tokens=usage.get("cached_input_tokens"),
        provider_details=dict(usage.get("provider_details") or {}),
    )
    state.final_output = payload.get("final_output")
    state.cancellation_requested = bool(payload.get("cancellation_requested", False))
    state.messages = [_message_from_dict(row) for row in payload.get("messages", [])]
    return state


def _message_from_dict(row: dict[str, Any]) -> CanonicalMessage:
    """Recreate one canonical message including model-issued tool calls."""
    calls = [ToolCall(id=str(call["id"]), name=str(call["name"]), arguments=dict(call.get("arguments") or {})) for call in row.get("tool_calls", [])]
    return CanonicalMessage(
        role=cast(MessageRole, str(row["role"])),
        content=str(row.get("content") or ""),
        reasoning_content=row.get("reasoning_content"),
        message_id=str(row.get("message_id") or ""),
        tool_call_id=row.get("tool_call_id"),
        tool_name=row.get("tool_name"),
        tool_calls=calls,
        created_at=row.get("created_at"),
        metadata=dict(row.get("metadata") or {}),
    )
