from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agent_harness.guidance.trust import ProjectTrustContext
from agent_harness.mcp.errors import MCPConfigurationError
from agent_harness.mcp.models import MCPConfigScope, MCPServerConfig, MCPTransport


@dataclass(frozen=True, slots=True)
class MCPResolvedConfig:
    """Resolved server winners plus blocked and invalid diagnostics."""

    servers: tuple[MCPServerConfig, ...]
    blocked: tuple[MCPServerConfig, ...] = ()
    diagnostics: tuple[str, ...] = ()


class MCPConfigResolver:
    """Discover scoped MCP files and select complete same-name winners."""

    def __init__(self, project_root: Path, trust: ProjectTrustContext, *, user_root: Path | None = None, admin_root: Path | None = None) -> None:
        """Store canonical roots and the already-resolved workspace trust context."""
        self.project_root = project_root.resolve()
        self.trust = trust
        self.user_root = (user_root or _user_root()).resolve()
        self.admin_root = (admin_root or _admin_root()).resolve()

    def resolve(self, inline_user_servers: dict[str, Any] | None = None) -> MCPResolvedConfig:
        """Resolve LOCAL > PROJECT > USER winners without cross-entry field merging."""
        sources = [
            (MCPConfigScope.USER, self.user_root / "mcp.json"),
            (MCPConfigScope.PROJECT, self.project_root / ".mcp.json"),
            (MCPConfigScope.LOCAL, self.user_root / "projects" / _project_identity(self.project_root) / "mcp.json"),
        ]
        winners: dict[str, MCPServerConfig] = {}
        blocked: list[MCPServerConfig] = []
        diagnostics: list[str] = []
        if inline_user_servers:
            self._parse_rows(inline_user_servers, MCPConfigScope.USER, winners, blocked, diagnostics)
        for scope, path in sources:
            if not path.is_file():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                rows = raw.get("mcpServers", raw.get("servers", {})) if isinstance(raw, dict) else {}
                if not isinstance(rows, dict):
                    raise MCPConfigurationError("server map must be an object")
                self._parse_rows(rows, scope, winners, blocked, diagnostics)
            except (OSError, json.JSONDecodeError, MCPConfigurationError) as exc:
                diagnostics.append(f"{path}: {exc}")
        return MCPResolvedConfig(tuple(sorted(winners.values(), key=lambda item: item.name)), tuple(blocked), tuple(diagnostics))

    def _parse_rows(self, rows: dict[str, Any], scope: MCPConfigScope, winners: dict[str, MCPServerConfig], blocked: list[MCPServerConfig], diagnostics: list[str]) -> None:
        """Parse one scope and replace lower-priority complete entries by name."""
        for name, row in rows.items():
            try:
                config = parse_server_config(str(name), row, scope, self.project_root)
                if scope == MCPConfigScope.PROJECT and not self.trust.mcp_allowed:
                    blocked.append(config)
                    winners.pop(config.name, None)
                    continue
                winners[config.name] = config
            except (TypeError, ValueError, MCPConfigurationError) as exc:
                diagnostics.append(f"{scope.value}:{name}: {exc}")


def parse_server_config(name: str, row: Any, scope: MCPConfigScope, project_root: Path) -> MCPServerConfig:
    """Validate one MCP JSON entry and return a secret-free immutable config."""
    if not isinstance(row, dict):
        raise MCPConfigurationError("server entry must be an object")
    transport_value = row.get("transport") or row.get("type") or ("streamable_http" if row.get("url") else "stdio")
    if transport_value in {"http", "streamable-http"}:
        transport_value = "streamable_http"
    transport = MCPTransport(str(transport_value))
    command = str(row["command"]) if row.get("command") else None
    url = str(row["url"]) if row.get("url") else None
    if transport == MCPTransport.STDIO and (not command or url):
        raise MCPConfigurationError("stdio requires command and forbids url")
    if transport == MCPTransport.STREAMABLE_HTTP and (not url or command):
        raise MCPConfigurationError("streamable_http requires url and forbids command")
    if url:
        _validate_url(url, bool(row.get("allow_insecure_http", False)))
    cwd = Path(str(row["cwd"])) if row.get("cwd") else None
    if cwd and not cwd.is_absolute():
        cwd = (project_root / cwd).resolve()
    env = row.get("env", {})
    headers = row.get("headers", {})
    approvals = row.get("tool_approval_overrides", {})
    canonical = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return MCPServerConfig(
        name=name,
        scope=scope,
        transport=transport,
        enabled=bool(row.get("enabled", True)),
        required=bool(row.get("required", False)),
        command=command,
        args=tuple(map(str, row.get("args", []))),
        cwd=cwd,
        env=tuple(sorted((str(key), str(value)) for key, value in env.items())) if isinstance(env, dict) else (),
        env_vars=tuple(map(str, row.get("env_vars", []))),
        url=url,
        bearer_token_env_var=row.get("bearer_token_env_var"),
        headers=tuple(sorted((str(key), str(value)) for key, value in headers.items())) if isinstance(headers, dict) else (),
        auth_mode=str(row.get("auth_mode", "none")),
        oauth_scopes=tuple(map(str, row.get("oauth_scopes", []))),
        startup_timeout_seconds=float(row.get("startup_timeout_seconds", row.get("startup_timeout_sec", 10))),
        tool_timeout_seconds=float(row.get("tool_timeout_seconds", row.get("tool_timeout_sec", 60))),
        cleanup_timeout_seconds=float(row.get("cleanup_timeout_seconds", 5)),
        enabled_tools=tuple(map(str, row.get("enabled_tools", []))),
        disabled_tools=tuple(map(str, row.get("disabled_tools", []))),
        default_approval_mode=str(row.get("default_approval_mode", "inherit")),
        tool_approval_overrides=tuple(sorted((str(key), str(value)) for key, value in approvals.items())) if isinstance(approvals, dict) else (),
        always_load_tools=bool(row.get("always_load_tools", False)),
        trusted=scope in {MCPConfigScope.ADMIN, MCPConfigScope.USER, MCPConfigScope.LOCAL},
        config_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def forwarded_environment(config: MCPServerConfig) -> dict[str, str]:
    """Build a narrow stdio environment and forward named secrets only at launch."""
    allowed = {"PATH", "LANG", "LC_ALL", "TERM", "SYSTEMROOT", "WINDIR"}
    names = allowed | set(config.env_vars)
    result = {name: os.environ[name] for name in names if name in os.environ}
    result.update(dict(config.env))
    return result


def _validate_url(url: str, allow_insecure_http: bool) -> None:
    """Allow HTTPS and loopback HTTP while rejecting unsafe clear-text remotes."""
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.netloc:
        return
    if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        return
    if parsed.scheme == "http" and parsed.netloc and allow_insecure_http:
        return
    raise MCPConfigurationError("MCP URL must use HTTPS; clear-text HTTP is limited to loopback")


def _project_identity(root: Path) -> str:
    """Return a stable non-secret directory key for project-local user config."""
    return hashlib.sha256(str(root.resolve()).casefold().encode("utf-8")).hexdigest()[:24]


def _user_root() -> Path:
    """Return the platform user configuration root."""
    return Path(os.getenv("APPDATA", Path.home() / ".agent-harness")) / ("agent-harness" if os.getenv("APPDATA") else "")


def _admin_root() -> Path:
    """Return the platform administrator configuration root."""
    return Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "AgentHarness"
