from agent_harness.domain.errors import HarnessError


class MCPRuntimeError(RuntimeError):
    """Base error for recoverable MCP runtime failures."""


class MCPConfigurationError(MCPRuntimeError):
    """Raised when an MCP server entry is invalid or unsafe."""


class MCPConnectionError(MCPRuntimeError):
    """Raised when a required MCP server cannot initialize."""


class MCPAuthRequiredError(MCPRuntimeError):
    """Raised when an MCP HTTP server requires user authorization."""


class MCPProtocolError(MCPRuntimeError):
    """Raised for invalid MCP messages or server contract violations."""


class MCPTransportError(MCPRuntimeError):
    """Raised for transport failures unrelated to a tool execution result."""


class MCPToolExecutionError(HarnessError):
    """Represent a tools/call response whose isError flag is true."""

    code = "MCP_TOOL_EXECUTION_ERROR"


class MCPToolOutcomeUnknown(HarnessError):
    """Represent an interrupted side-effecting call that cannot be retried safely."""

    code = "MCP_TOOL_OUTCOME_UNKNOWN"
