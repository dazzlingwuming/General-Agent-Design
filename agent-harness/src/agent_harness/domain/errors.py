from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RunError:
    code: str
    message: str
    category: str
    recoverable: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    cause_type: str | None = None


class HarnessError(Exception):
    code = "HARNESS_ERROR"
    category = "internal"
    recoverable = False

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        """Store a user-facing error message and structured diagnostic details."""
        super().__init__(message)
        self.details = details or {}

    def to_run_error(self) -> RunError:
        """Convert an exception into the serializable RunError representation."""
        return RunError(
            code=self.code,
            message=str(self),
            category=self.category,
            recoverable=self.recoverable,
            details=self.details,
            cause_type=type(self).__name__,
        )


class ConfigurationError(HarnessError):
    code = "CONFIGURATION_ERROR"
    category = "configuration"


class ProviderError(HarnessError):
    code = "PROVIDER_ERROR"
    category = "provider"


class ProviderTimeoutError(ProviderError):
    code = "PROVIDER_TIMEOUT"
    recoverable = True


class ProviderRateLimitError(ProviderError):
    code = "PROVIDER_RATE_LIMIT"
    recoverable = True


class ProviderAuthenticationError(ProviderError):
    code = "PROVIDER_AUTHENTICATION"


class ProviderProtocolError(ProviderError):
    code = "PROVIDER_PROTOCOL_ERROR"


class ContextLimitError(HarnessError):
    code = "CONTEXT_LIMIT_EXCEEDED"
    category = "context"


class ToolNotFoundError(HarnessError):
    code = "TOOL_NOT_FOUND"
    category = "tool"
    recoverable = True


class ToolInputValidationError(HarnessError):
    code = "TOOL_INPUT_VALIDATION"
    category = "tool"
    recoverable = True


class ToolAuthorizationError(HarnessError):
    code = "TOOL_AUTHORIZATION"
    category = "tool"
    recoverable = True


class ToolExecutionError(HarnessError):
    code = "TOOL_EXECUTION_ERROR"
    category = "tool"
    recoverable = True


class ToolTimeoutError(ToolExecutionError):
    code = "TOOL_TIMEOUT"


class WorkspaceBoundaryError(ToolExecutionError):
    code = "WORKSPACE_BOUNDARY"


class BudgetExceededError(HarnessError):
    code = "LIMIT_REACHED"
    category = "budget"


class CancellationError(HarnessError):
    code = "CANCELLED"
    category = "cancellation"


class InternalInvariantError(HarnessError):
    code = "INTERNAL_INVARIANT"
    category = "internal"


class ArtifactError(HarnessError):
    """Reject unsafe, invalid, or over-quota artifact content."""

    code = "ARTIFACT_ERROR"
    category = "artifact"
