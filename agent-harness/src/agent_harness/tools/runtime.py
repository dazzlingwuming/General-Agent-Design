from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from agent_harness.domain.errors import HarnessError, ToolAuthorizationError, ToolInputValidationError, ToolTimeoutError
from agent_harness.domain.messages import ToolCall
from agent_harness.domain.tools import ToolResult
from agent_harness.security.approval import ApprovalDecision, ApprovalHandler, ApprovalRequest, DenyApprovalHandler
from agent_harness.security.hooks import HookDecision, HookManager
from agent_harness.security.models import ApprovalPolicy, PermissionDecision, RiskLevel, SandboxPolicy, ToolExecutionPrincipal
from agent_harness.security.permission_engine import PermissionEngine
from agent_harness.tools.registry import ToolRegistry
from agent_harness.utils.time import duration_ms, utc_now


@dataclass(slots=True)
class ToolRuntime:
    registry: ToolRegistry
    max_result_chars: int = 20000
    permission_engine: PermissionEngine = field(default_factory=PermissionEngine)
    approval_handler: ApprovalHandler = field(default_factory=DenyApprovalHandler)
    hook_manager: HookManager = field(default_factory=HookManager)
    workspace_root: Path = Path(".")
    sandbox_policy_factory: Callable[[ToolExecutionPrincipal], SandboxPolicy] | None = None
    audit: Callable[[str, dict[str, Any]], None] | None = None
    _turn_grants: set[tuple[str, str, str]] = field(default_factory=set)
    _thread_grants: set[tuple[str, str]] = field(default_factory=set)
    _executed_approvals: set[str] = field(default_factory=set)

    async def execute(self, call: ToolCall, principal: ToolExecutionPrincipal | None = None) -> ToolResult:
        """Validate and execute one tool call, returning a canonical ToolResult."""
        started = utc_now()
        try:
            if principal is None:
                raise ToolAuthorizationError("Tool execution principal is required", details={"tool": call.name})
            definition = self.registry.get(call.name)
            args = self._coerce_arguments(call.arguments)
            self._validate_json_schema(args, definition.input_schema)
            policy = self._sandbox_policy(principal)
            evaluation = self.permission_engine.evaluate(principal, definition, args, policy)
            self._emit("permission.evaluated", {"tool": call.name, "tool_call_id": call.id, "decision": evaluation.decision.value, "reason": evaluation.reason, "matched_rules": evaluation.matched_rules})
            self._emit("hook.started", {"point": "PreToolUse", "tool": call.name, "tool_call_id": call.id})
            hook_decision = await self.hook_manager.run("PreToolUse", {"tool": call.name, "arguments": args, "principal": principal})
            self._emit("hook.completed", {"point": "PreToolUse", "tool": call.name, "tool_call_id": call.id, "decision": hook_decision.value})
            if hook_decision == HookDecision.DENY:
                evaluation.decision = PermissionDecision.DENY
                evaluation.reason = "PreToolUse hook denied the call"
            elif hook_decision == HookDecision.ASK and evaluation.decision == PermissionDecision.ALLOW:
                evaluation.decision = PermissionDecision.ASK
                evaluation.reason = "PreToolUse hook requested approval"
            await self._authorize_or_approve(call, definition.risk_level, definition.required_capabilities, args, principal, evaluation.decision, evaluation.reason)
            if definition.requires_sandbox:
                self._emit("sandbox.started", {"tool": call.name, "tool_call_id": call.id, "sandbox_mode": principal.sandbox_mode.value})
                self._emit("command.started", {"tool_call_id": call.id, "program": args.get("program"), "arg_count": len(args.get("args", [])), "cwd": args.get("cwd", ".")})
            output = await asyncio.wait_for(definition.executor(args), timeout=definition.timeout_seconds)
            if definition.output_schema is not None:
                self._validate_schema_value(output, definition.output_schema, "output")
            output_data = output if isinstance(output, dict) else {}
            if definition.requires_sandbox:
                self._emit("command.completed", {"tool_call_id": call.id, "exit_code": output_data.get("exit_code"), "timed_out": output_data.get("timed_out"), "truncated": output_data.get("truncated")})
                self._emit("sandbox.completed", {"tool": call.name, "tool_call_id": call.id, "backend": output_data.get("sandbox_backend")})
            if definition.side_effect.value == "FILESYSTEM":
                self._emit("file.changed", {"tool": call.name, "tool_call_id": call.id, "path": args.get("path")})
            self._emit("hook.started", {"point": "PostToolUse", "tool": call.name, "tool_call_id": call.id})
            await self.hook_manager.run("PostToolUse", {"tool": call.name, "arguments": args, "output": output, "principal": principal})
            self._emit("hook.completed", {"point": "PostToolUse", "tool": call.name, "tool_call_id": call.id})
            content = self._format_output(output)
            metadata: dict[str, Any] = {"output": output}
            if len(content) > self.max_result_chars:
                content = content[: self.max_result_chars] + "\n[truncated]"
                metadata["truncated"] = True
            return self._result(call, "success", content, started, metadata=metadata)
        except asyncio.TimeoutError:
            self._emit("sandbox.failed", {"tool": call.name, "tool_call_id": call.id, "reason": "timeout"})
            return self._result(call, "timeout", f"Tool timed out: {call.name}", started, "TOOL_TIMEOUT", "Tool timed out")
        except HarnessError as exc:
            run_error = exc.to_run_error()
            return self._result(call, "error", f"Tool error [{run_error.code}]: {run_error.message}", started, run_error.code, run_error.message, run_error.details)
        except Exception as exc:  # Tool bugs are recoverable tool errors in phase 1.
            if call.name == "run_command":
                self._emit("sandbox.failed", {"tool": call.name, "tool_call_id": call.id, "reason": type(exc).__name__})
            err = ToolTimeoutError(str(exc)).to_run_error() if isinstance(exc, TimeoutError) else None
            return self._result(
                call,
                "error",
                f"Tool error [TOOL_EXECUTION_ERROR]: {exc}",
                started,
                err.code if err else "TOOL_EXECUTION_ERROR",
                str(exc),
            )

    async def _authorize_or_approve(self, call: ToolCall, risk_level: RiskLevel, required_capabilities: frozenset, args: dict[str, Any], principal: ToolExecutionPrincipal, decision: PermissionDecision, reason: str) -> None:
        """Reject denied calls or resolve ASK through a narrow idempotent approval."""
        if decision == PermissionDecision.DENY:
            self._emit("permission.denied", {"tool": call.name, "tool_call_id": call.id, "reason": reason})
            raise ToolAuthorizationError(reason, details={"tool": call.name, "agent_id": principal.agent_id})
        if decision != PermissionDecision.ASK or self._has_grant(principal, call.name):
            return
        if principal.approval_policy == ApprovalPolicy.NEVER:
            raise ToolAuthorizationError("Approval policy never rejects ASK decisions", details={"tool": call.name})
        request = ApprovalRequest(
            principal=principal,
            tool_call_id=call.id,
            tool_name=call.name,
            reason=reason,
            risk_level=risk_level,
            requested_capabilities=required_capabilities,
            command_preview=tuple([str(args.get("program")), *map(str, args.get("args", []))]) if args.get("program") else (),
            path_preview=tuple(str(args[name]) for name in ("path", "cwd") if name in args),
        )
        self._emit("approval.requested", {"approval_id": request.approval_id, "tool_call_id": call.id, "tool": call.name, "agent_id": principal.agent_id})
        approval = await self.approval_handler.request(request)
        self._emit("approval.decided", {"approval_id": request.approval_id, "tool_call_id": call.id, "decision": approval.value})
        if approval in {ApprovalDecision.DENY_ONCE, ApprovalDecision.CANCEL_TURN}:
            raise ToolAuthorizationError("User denied tool approval", details={"tool": call.name, "approval_id": request.approval_id})
        if request.approval_id in self._executed_approvals:
            raise ToolAuthorizationError("Approval has already been consumed", details={"approval_id": request.approval_id})
        self._executed_approvals.add(request.approval_id)
        if approval == ApprovalDecision.ALLOW_TURN:
            self._turn_grants.add((principal.thread_id, principal.turn_id, call.name))
        if approval == ApprovalDecision.ALLOW_THREAD:
            self._thread_grants.add((principal.thread_id, call.name))

    def _has_grant(self, principal: ToolExecutionPrincipal, tool_name: str) -> bool:
        """Return whether a prior narrow grant covers this tool and scope."""
        return (principal.thread_id, principal.turn_id, tool_name) in self._turn_grants or (principal.thread_id, tool_name) in self._thread_grants

    def _sandbox_policy(self, principal: ToolExecutionPrincipal) -> SandboxPolicy:
        """Resolve the policy used by permission checks and sandboxed executors."""
        if self.sandbox_policy_factory:
            return self.sandbox_policy_factory(principal)
        root = self.workspace_root.resolve()
        writable = (root,) if principal.sandbox_mode.value == "workspace-write" else ()
        return SandboxPolicy(principal.sandbox_mode, root, (root,), writable)

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        """Emit a security audit event when a runtime sink is configured."""
        if self.audit:
            self.audit(event, payload)

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
        """Apply the supported JSON Schema validation subset to tool arguments."""
        self._validate_schema_value(args, schema, "arguments")

    def _validate_schema_value(self, value: Any, schema: dict[str, Any], path: str) -> None:
        """Validate one value recursively against the local JSON Schema subset."""
        expected = schema.get("type")
        if "enum" in schema and value not in schema["enum"]:
            raise ToolInputValidationError(f"{path} must be one of: {schema['enum']}")
        if expected == "object":
            if not isinstance(value, dict):
                raise ToolInputValidationError(f"{path} must be an object")
            self._validate_object(value, schema, path)
            return
        if expected == "array":
            if not isinstance(value, list):
                raise ToolInputValidationError(f"{path} must be an array")
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for index, item in enumerate(value):
                    self._validate_schema_value(item, item_schema, f"{path}[{index}]")
            return
        if expected == "string":
            if not isinstance(value, str):
                raise ToolInputValidationError(f"{path} must be a string")
            if "minLength" in schema and len(value) < int(schema["minLength"]):
                raise ToolInputValidationError(f"{path} is shorter than minLength")
            if "maxLength" in schema and len(value) > int(schema["maxLength"]):
                raise ToolInputValidationError(f"{path} exceeds maxLength")
            return
        if expected == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                raise ToolInputValidationError(f"{path} must be an integer")
            self._validate_number_bounds(value, schema, path)
            return
        if expected == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ToolInputValidationError(f"{path} must be a number")
            self._validate_number_bounds(float(value), schema, path)
            return
        if expected == "boolean" and not isinstance(value, bool):
            raise ToolInputValidationError(f"{path} must be a boolean")

    def _validate_object(self, args: dict[str, Any], schema: dict[str, Any], path: str) -> None:
        """Validate required, unknown, and nested object properties."""
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        for field_name in required:
            if field_name not in args:
                raise ToolInputValidationError(f"Missing required argument: {path}.{field_name}")
        for key, value in args.items():
            if key not in properties:
                if schema.get("additionalProperties", True) is False:
                    raise ToolInputValidationError(f"Unknown argument: {path}.{key}")
                continue
            self._validate_schema_value(value, properties[key], f"{path}.{key}")

    def _validate_number_bounds(self, value: int | float, schema: dict[str, Any], path: str) -> None:
        """Validate minimum and maximum constraints for numeric schemas."""
        if "minimum" in schema and value < schema["minimum"]:
            raise ToolInputValidationError(f"{path} is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ToolInputValidationError(f"{path} exceeds maximum")

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
