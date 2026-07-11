from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Capability(str, Enum):
    """Capabilities that may be granted to a tool execution principal."""

    FILE_READ = "FILE_READ"
    FILE_WRITE = "FILE_WRITE"
    FILE_DELETE = "FILE_DELETE"
    COMMAND_EXECUTE = "COMMAND_EXECUTE"
    PACKAGE_INSTALL = "PACKAGE_INSTALL"
    NETWORK_ACCESS = "NETWORK_ACCESS"
    LOCAL_NETWORK_ACCESS = "LOCAL_NETWORK_ACCESS"
    SECRET_READ = "SECRET_READ"
    ENV_READ = "ENV_READ"
    GIT_WRITE = "GIT_WRITE"
    GIT_COMMIT = "GIT_COMMIT"
    GIT_PUSH = "GIT_PUSH"
    SUBAGENT_CREATE = "SUBAGENT_CREATE"
    MCP_TOOL_CALL = "MCP_TOOL_CALL"
    EXTERNAL_SIDE_EFFECT = "EXTERNAL_SIDE_EFFECT"
    SANDBOX_ESCAPE = "SANDBOX_ESCAPE"


class RiskLevel(str, Enum):
    """Human-facing risk classification for tools and approval requests."""

    READ_ONLY = "READ_ONLY"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class SideEffectType(str, Enum):
    """Side-effect category produced by a tool."""

    NONE = "NONE"
    FILESYSTEM = "FILESYSTEM"
    PROCESS = "PROCESS"
    NETWORK = "NETWORK"
    EXTERNAL = "EXTERNAL"


class SandboxMode(str, Enum):
    """Supported local sandbox profiles."""

    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    DANGER_FULL_ACCESS = "danger-full-access"


class ApprovalPolicy(str, Enum):
    """Policy controlling whether ASK decisions may prompt the user."""

    UNTRUSTED = "untrusted"
    ON_REQUEST = "on-request"
    NEVER = "never"


class PermissionDecision(str, Enum):
    """Final or intermediate permission decision."""

    ALLOW = "ALLOW"
    ASK = "ASK"
    DENY = "DENY"


class RuleSource(str, Enum):
    """Origin of a permission rule for trust and audit decisions."""

    BUILTIN = "BUILTIN"
    MANAGED = "MANAGED"
    USER = "USER"
    TRUSTED_PROJECT = "TRUSTED_PROJECT"
    SESSION = "SESSION"
    TURN_APPROVAL = "TURN_APPROVAL"
    AGENT_DEFINITION = "AGENT_DEFINITION"
    PARENT_DELEGATION = "PARENT_DELEGATION"
    HOOK = "HOOK"


@dataclass(frozen=True, slots=True)
class ToolExecutionPrincipal:
    """Identity and immutable authorization ceiling for one tool call."""

    session_id: str
    thread_id: str
    turn_id: str
    agent_id: str
    parent_agent_id: str | None = None
    depth: int = 0
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    capabilities: frozenset[Capability] = field(default_factory=frozenset)
    sandbox_mode: SandboxMode = SandboxMode.WORKSPACE_WRITE
    approval_policy: ApprovalPolicy = ApprovalPolicy.ON_REQUEST


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    """Resolved filesystem, network, environment, and output sandbox policy."""

    mode: SandboxMode
    workspace_root: Path
    readable_roots: tuple[Path, ...]
    writable_roots: tuple[Path, ...]
    network_enabled: bool = False
    environment_allow: frozenset[str] = frozenset({"PATH", "LANG", "LC_ALL", "TERM", "SYSTEMROOT", "WINDIR"})
    timeout_seconds: float = 120.0
    max_output_chars: int = 50000


@dataclass(slots=True)
class PermissionEvaluation:
    """Auditable result returned by the permission engine."""

    decision: PermissionDecision
    reason: str
    matched_rules: list[str] = field(default_factory=list)
    effective_capabilities: frozenset[Capability] = field(default_factory=frozenset)
    sandbox_policy: SandboxPolicy | None = None

