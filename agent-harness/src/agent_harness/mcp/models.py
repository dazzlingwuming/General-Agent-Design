from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class MCPConfigScope(StrEnum):
    """Configuration scopes ordered independently by the resolver."""

    ADMIN = "admin"
    USER = "user"
    LOCAL = "local"
    PROJECT = "project"
    BUNDLED = "bundled"


class MCPTransport(StrEnum):
    """Supported MCP v1 transports."""

    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"


class MCPServerStatus(StrEnum):
    """Observable lifecycle states for one server connection."""

    DISABLED = "disabled"
    BLOCKED_UNTRUSTED = "blocked_untrusted"
    NOT_CONNECTED = "not_connected"
    CONNECTING = "connecting"
    AUTH_REQUIRED = "auth_required"
    READY = "ready"
    DEGRADED = "degraded"
    RECONNECTING = "reconnecting"
    FAILED = "failed"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class MCPServerConfig:
    """Validated immutable definition for one MCP server."""

    name: str
    scope: MCPConfigScope
    transport: MCPTransport
    enabled: bool = True
    required: bool = False
    command: str | None = None
    args: tuple[str, ...] = ()
    cwd: Path | None = None
    env: tuple[tuple[str, str], ...] = ()
    env_vars: tuple[str, ...] = ()
    url: str | None = None
    bearer_token_env_var: str | None = None
    headers: tuple[tuple[str, str], ...] = ()
    auth_mode: str = "none"
    oauth_scopes: tuple[str, ...] = ()
    startup_timeout_seconds: float = 10.0
    tool_timeout_seconds: float = 60.0
    cleanup_timeout_seconds: float = 5.0
    enabled_tools: tuple[str, ...] = ()
    disabled_tools: tuple[str, ...] = ()
    default_approval_mode: str = "inherit"
    tool_approval_overrides: tuple[tuple[str, str], ...] = ()
    always_load_tools: bool = False
    trusted: bool = False
    config_hash: str = ""


@dataclass(frozen=True, slots=True)
class MCPToolRecord:
    """Catalog metadata for one remotely implemented MCP tool."""

    server_name: str
    remote_name: str
    canonical_name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MCPResourceRecord:
    """Catalog metadata for one MCP resource."""

    server_name: str
    uri: str
    name: str
    description: str = ""
    mime_type: str | None = None


@dataclass(frozen=True, slots=True)
class MCPPromptRecord:
    """Catalog metadata for one MCP prompt template."""

    server_name: str
    name: str
    description: str = ""
    arguments: tuple[dict[str, Any], ...] = ()


@dataclass(slots=True)
class MCPServerSnapshot:
    """Persistable, credential-free state for thread resume diagnostics."""

    name: str
    status: MCPServerStatus
    scope: MCPConfigScope
    transport: MCPTransport
    config_hash: str
    protocol_version: str | None = None
    instructions: str | None = None
    capabilities: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    catalog_page_count: int = 0
    catalog_truncated: bool = False
    catalog_hash: str = ""
    instructions_hash: str = ""
    instructions_chars: int = 0
    credential_identity_hash: str | None = None
    canonical_tool_mapping: tuple[dict[str, str], ...] = ()
