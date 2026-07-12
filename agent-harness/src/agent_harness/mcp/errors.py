class MCPRuntimeError(RuntimeError):
    """Base error for recoverable MCP runtime failures."""


class MCPConfigurationError(MCPRuntimeError):
    """Raised when an MCP server entry is invalid or unsafe."""


class MCPConnectionError(MCPRuntimeError):
    """Raised when a required MCP server cannot initialize."""


class MCPAuthRequiredError(MCPRuntimeError):
    """Raised when an MCP HTTP server requires user authorization."""
