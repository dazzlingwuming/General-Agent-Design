from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agent_harness.domain.errors import HarnessError, ToolInputValidationError, ToolTimeoutError
from agent_harness.domain.messages import ToolCall
from agent_harness.domain.tools import ToolResult
from agent_harness.tools.registry import ToolRegistry
from agent_harness.utils.time import duration_ms, utc_now


@dataclass(slots=True)
class ToolRuntime:
    registry: ToolRegistry
    max_result_chars: int = 20000

    async def execute(self, call: ToolCall) -> ToolResult:
        """Validate and execute one tool call, returning a canonical ToolResult."""
        started = utc_now()
        try:
            definition = self.registry.get(call.name)
            args = self._coerce_arguments(call.arguments)
            self._validate_json_schema(args, definition.input_schema)
            output = await asyncio.wait_for(definition.executor(args), timeout=definition.timeout_seconds)
            content = self._format_output(output)
            metadata: dict[str, Any] = {"output": output}
            if len(content) > self.max_result_chars:
                content = content[: self.max_result_chars] + "\n[truncated]"
                metadata["truncated"] = True
            return self._result(call, "success", content, started, metadata=metadata)
        except asyncio.TimeoutError:
            return self._result(call, "timeout", f"Tool timed out: {call.name}", started, "TOOL_TIMEOUT", "Tool timed out")
        except HarnessError as exc:
            run_error = exc.to_run_error()
            return self._result(call, "error", f"Tool error [{run_error.code}]: {run_error.message}", started, run_error.code, run_error.message, run_error.details)
        except Exception as exc:  # Tool bugs are recoverable tool errors in phase 1.
            err = ToolTimeoutError(str(exc)).to_run_error() if isinstance(exc, TimeoutError) else None
            return self._result(
                call,
                "error",
                f"Tool error [TOOL_EXECUTION_ERROR]: {exc}",
                started,
                err.code if err else "TOOL_EXECUTION_ERROR",
                str(exc),
            )

    def _coerce_arguments(self, arguments: dict[str, Any] | str) -> dict[str, Any]:
        """Parse provider arguments into a JSON object for local validation."""
        if isinstance(arguments, dict):
            return arguments
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise ToolInputValidationError("Tool arguments are not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ToolInputValidationError("Tool arguments must be a JSON object")
        return parsed

    def _validate_json_schema(self, args: dict[str, Any], schema: dict[str, Any]) -> None:
        """Apply the phase 1 subset of JSON Schema validation to tool arguments."""
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        for field in required:
            if field not in args:
                raise ToolInputValidationError(f"Missing required argument: {field}")
        for key, value in args.items():
            if key not in properties:
                if schema.get("additionalProperties", True) is False:
                    raise ToolInputValidationError(f"Unknown argument: {key}")
                continue
            expected = properties[key].get("type")
            if expected == "string" and not isinstance(value, str):
                raise ToolInputValidationError(f"Argument {key} must be a string")
            if expected == "integer" and not isinstance(value, int):
                raise ToolInputValidationError(f"Argument {key} must be an integer")
            if expected == "boolean" and not isinstance(value, bool):
                raise ToolInputValidationError(f"Argument {key} must be a boolean")
            if expected == "number" and not isinstance(value, (int, float)):
                raise ToolInputValidationError(f"Argument {key} must be a number")
            if expected == "array" and not isinstance(value, list):
                raise ToolInputValidationError(f"Argument {key} must be an array")
            if expected == "object" and not isinstance(value, dict):
                raise ToolInputValidationError(f"Argument {key} must be an object")

    def _format_output(self, output: dict[str, Any] | str) -> str:
        """Render a tool output object into compact text for the next model turn."""
        if isinstance(output, str):
            return output
        return json.dumps(output, ensure_ascii=False, indent=2)

    def _result(
        self,
        call: ToolCall,
        status: str,
        content: str,
        started: datetime,
        error_code: str | None = None,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Create a ToolResult with timing, status, and optional error fields."""
        completed = utc_now()
        return ToolResult(
            tool_call_id=call.id,
            tool_name=call.name,
            status=status,  # type: ignore[arg-type]
            content=content,
            error_code=error_code,
            error_message=error_message,
            metadata=metadata or {},
            started_at=started,
            completed_at=completed,
            duration_ms=duration_ms(started, completed),
        )
