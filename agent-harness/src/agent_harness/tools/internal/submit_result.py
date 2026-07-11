from __future__ import annotations

from typing import Any

from agent_harness.agents.registry import SUBAGENT_RESULT_SCHEMA
from agent_harness.domain.errors import ToolInputValidationError
from agent_harness.domain.tools import ToolDefinition


def create_submit_result_tool(timeout_seconds: int = 30) -> ToolDefinition:
    """Create the internal terminal tool used by child agents to submit results."""

    async def executor(args: dict[str, Any]) -> dict[str, Any]:
        """Return the already-validated structured subagent result payload."""
        _validate_structured_result(args)
        return args

    return ToolDefinition(
        name="submit_result",
        description="提交子 Agent 的结构化分析结果，并结束当前子 Agent Turn。",
        input_schema=SUBAGENT_RESULT_SCHEMA,
        executor=executor,
        timeout_seconds=timeout_seconds,
        risk_level="internal",
        required_capabilities=[],
    )


def _validate_structured_result(args: dict[str, Any]) -> None:
    """Validate phase 2 subagent result fields beyond shallow JSON Schema checks."""
    confidence = args.get("confidence")
    if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
        raise ToolInputValidationError("confidence must be a number between 0.0 and 1.0")
    evidence = args.get("evidence", [])
    if not isinstance(evidence, list):
        raise ToolInputValidationError("evidence must be an array")
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            raise ToolInputValidationError(f"evidence[{index}] must be an object")
        for field in ("path", "start_line", "end_line", "claim"):
            if field not in item:
                raise ToolInputValidationError(f"evidence[{index}] missing {field}")
        if not isinstance(item["start_line"], int) or not isinstance(item["end_line"], int):
            raise ToolInputValidationError(f"evidence[{index}] line numbers must be integers")
        if item["start_line"] <= 0 or item["end_line"] < item["start_line"]:
            raise ToolInputValidationError(f"evidence[{index}] line range is invalid")
