from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Awaitable, Callable, Literal

from agent_harness.domain.messages import ToolCall
from agent_harness.security.models import Capability, RiskLevel, SideEffectType
from agent_harness.utils.time import utc_now

ToolStatus = Literal["success", "error", "cancelled", "timeout"]
ToolExecutor = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | str]]


class ToolEffectClass(StrEnum):
    """Durable recovery classification for a tool's observable effects."""

    PURE = "pure"
    READ_ONLY = "read_only"
    IDEMPOTENT_WRITE = "idempotent_write"
    RECONCILABLE_WRITE = "reconcilable_write"
    NON_IDEMPOTENT_WRITE = "non_idempotent_write"


class ToolRecoveryPolicy(StrEnum):
    """Deterministic policy for a started tool lacking a durable result."""

    RETRY_SAFE = "retry_safe"
    VERIFY_THEN_RETRY = "verify_then_retry"
    VERIFY_THEN_SYNTHESIZE = "verify_then_synthesize"
    MANUAL_RECONCILIATION = "manual_reconciliation"
    NEVER_RETRY = "never_retry"


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    executor: ToolExecutor
    output_schema: dict[str, Any] | None = None
    timeout_seconds: int = 30
    risk_level: RiskLevel = RiskLevel.READ_ONLY
    side_effect: SideEffectType = SideEffectType.NONE
    required_capabilities: frozenset[Capability] = field(default_factory=lambda: frozenset({Capability.FILE_READ}))
    requires_sandbox: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    effect_class: ToolEffectClass = ToolEffectClass.READ_ONLY
    recovery_policy: ToolRecoveryPolicy = ToolRecoveryPolicy.RETRY_SAFE

    def __post_init__(self) -> None:
        """Normalize legacy string metadata into the phase 3 security enums."""
        if not isinstance(self.risk_level, RiskLevel):
            legacy = str(self.risk_level).upper()
            self.risk_level = RiskLevel.READ_ONLY if legacy in {"READ_ONLY", "INTERNAL"} else RiskLevel(legacy)
        if not isinstance(self.side_effect, SideEffectType):
            self.side_effect = SideEffectType(str(self.side_effect).upper())
        self.required_capabilities = frozenset(
            value if isinstance(value, Capability) else Capability(str(value)) for value in self.required_capabilities
        )
        self.effect_class = ToolEffectClass(self.effect_class)
        self.recovery_policy = ToolRecoveryPolicy(self.recovery_policy)

    def to_model_schema(self) -> dict[str, Any]:
        """Convert this internal tool definition to a model-visible function schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


@dataclass(slots=True)
class ToolResult:
    tool_call_id: str
    tool_name: str
    status: ToolStatus
    content: str
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: object = field(default_factory=utc_now)
    completed_at: object | None = None
    duration_ms: int | None = None

    @classmethod
    def from_error(cls, call: ToolCall, code: str, message: str, *, status: ToolStatus = "error") -> "ToolResult":
        """Create a model-visible tool result from a recoverable tool error."""
        return cls(
            tool_call_id=call.id,
            tool_name=call.name,
            status=status,
            content=f"Tool error [{code}]: {message}",
            error_code=code,
            error_message=message,
        )
