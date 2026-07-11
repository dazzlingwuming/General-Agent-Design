from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_harness.domain.run import RunLimits
from agent_harness.security.models import ApprovalPolicy, SandboxMode
from agent_harness.security.models import Capability, PermissionDecision, RuleSource
from agent_harness.security.rules import PermissionRule


@dataclass(slots=True)
class ProviderConfig:
    name: str = "deepseek"
    model: str = "deepseek-v4-flash"
    base_url: str = "https://api.deepseek.com"
    api_key: str | None = None
    timeout_seconds: int = 120
    max_attempts: int = 3
    api_key_env: str = "DEEPSEEK_API_KEY"


@dataclass(slots=True)
class AgentConfig:
    """Agent generation settings loaded from config, env, and CLI."""

    temperature: float = 0.0
    max_output_tokens: int = 4096


@dataclass(slots=True)
class ToolConfig:
    """Tool runtime limits that apply to every built-in tool."""

    default_timeout_seconds: int = 30
    max_result_chars: int = 20000


@dataclass(slots=True)
class ContextConfig:
    """Context estimation settings used before each model call."""

    char_to_token_ratio: float = 4.0
    max_estimated_input_tokens: int = 120000
    recent_turns: int = 3


@dataclass(slots=True)
class TraceConfig:
    """Trace persistence settings for JSONL events and run summaries."""

    directory: Path = Path(".harness/runs")
    thread_directory: Path = Path(".harness/threads")
    session_directory: Path = Path(".harness/sessions")
    fail_on_write_error: bool = True


@dataclass(slots=True)
class SubagentConfig:
    """Subagent runtime limits for the run-scoped scheduler."""

    max_concurrent: int = 3
    max_total: int = 8
    max_depth: int = 1
    max_turns_per_thread: int = 4
    max_followup_message_chars: int = 8000


@dataclass(slots=True)
class SecurityConfig:
    """Permission, approval, and platform sandbox settings."""

    sandbox_mode: SandboxMode = SandboxMode.WORKSPACE_WRITE
    approval_policy: ApprovalPolicy = ApprovalPolicy.ON_REQUEST
    sandbox_required: bool = True
    sandbox_backend: str = "auto"
    wsl_distribution: str | None = None
    network_enabled: bool = False
    default_timeout_seconds: float = 120.0
    max_output_chars: int = 50000
    environment_allow: tuple[str, ...] = ("PATH", "LANG", "LC_ALL", "TERM", "SYSTEMROOT", "WINDIR")
    trusted_project: bool = False
    full_access_confirmed: bool = False
    rules: list[PermissionRule] = field(default_factory=list)


@dataclass(slots=True)
class GuidanceConfig:
    """Project guidance discovery, import, rule, and trust limits."""

    enabled: bool = True
    max_guidance_bytes: int = 32768
    max_import_depth: int = 4
    max_import_files: int = 32
    max_import_total_bytes: int = 32768
    project_doc_fallback_filenames: tuple[str, ...] = ("CLAUDE.md",)
    require_workspace_trust: bool = True
    activate_search_candidates: bool = False


@dataclass(slots=True)
class SkillsConfig:
    """Agent Skills discovery, catalog, activation, and resource limits."""

    enabled: bool = True
    require_workspace_trust: bool = True
    catalog_context_ratio: float = 0.02
    catalog_fallback_max_chars: int = 8000
    max_skills: int = 500
    max_skill_scan_depth: int = 6
    max_skill_directories: int = 2000
    max_resource_bytes: int = 100000
    max_skill_file_bytes: int = 1048576
    max_frontmatter_bytes: int = 16384
    max_skill_body_bytes: int = 524288
    max_resource_files_per_skill: int = 200
    support_fork_context: bool = True
    disabled_skill_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class HarnessConfig:
    """Top-level configuration object shared by CLI and runtime code."""

    provider: ProviderConfig = field(default_factory=ProviderConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    run: RunLimits = field(default_factory=RunLimits)
    tools: ToolConfig = field(default_factory=ToolConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    trace: TraceConfig = field(default_factory=TraceConfig)
    subagents: SubagentConfig = field(default_factory=SubagentConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    guidance: GuidanceConfig = field(default_factory=GuidanceConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)


MODEL_ALIASES = {
    "v4-flash": "deepseek-v4-flash",
    "v4-pro": "deepseek-v4-pro",
    "deepseek-v4-flash": "deepseek-v4-flash",
    "deepseek-v4-pro": "deepseek-v4-pro",
}


def default_user_config_path() -> Path:
    """Return the per-user config path used by the CLI on this machine."""
    appdata = os.getenv("APPDATA")
    if appdata:
        return Path(appdata) / "agent-harness" / "config.toml"
    return Path.home() / ".agent-harness" / "config.toml"


def write_user_config(provider: ProviderConfig) -> Path:
    """Persist provider settings without writing API keys to disk."""
    path = default_user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(
        [
            "[provider]",
            f'name = "{provider.name}"',
            f'model = "{normalize_model_name(provider.model)}"',
            f'base_url = "{provider.base_url}"',
            f'api_key_env = "{provider.api_key_env}"',
            f"timeout_seconds = {provider.timeout_seconds}",
            f"max_attempts = {provider.max_attempts}",
            "",
        ]
    )
    path.write_text(content, encoding="utf-8")
    return path


def normalize_model_name(model: str) -> str:
    """Normalize supported CLI model aliases into provider model IDs."""
    return MODEL_ALIASES.get(model, model)


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE pairs into the process environment without printing secrets."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_nearest_dotenv(start: Path | None = None) -> bool:
    """Load the nearest .env file from the current directory or one of its parents."""
    current = (start or Path.cwd()).resolve()
    candidates = [current, *current.parents]
    for directory in candidates:
        env_path = directory / ".env"
        if env_path.exists():
            load_dotenv(env_path)
            return True
    return False


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    """Return a TOML section as a dictionary, or an empty dictionary when absent."""
    value = data.get(name, {})
    return value if isinstance(value, dict) else {}


def load_config(path: Path | None = None) -> HarnessConfig:
    """Load harness configuration with CLI/env-friendly defaults."""
    if path is None:
        user_config = default_user_config_path()
        if user_config.exists():
            path = user_config
    if path is not None:
        load_nearest_dotenv(path.parent)
    raw: dict[str, Any] = {}
    if path and path.exists():
        raw = tomllib.loads(path.read_text(encoding="utf-8"))

    provider = ProviderConfig(**{**_section(raw, "provider")})
    agent = AgentConfig(**{**_section(raw, "agent")})
    run = RunLimits(**{**_section(raw, "run")})
    tools = ToolConfig(**{**_section(raw, "tools")})
    context = ContextConfig(**{**_section(raw, "context")})
    subagents = SubagentConfig(**{**_section(raw, "subagents")})
    security_data = _section(raw, "security")
    sandbox_data = _section(security_data, "sandbox")
    network_data = _section(security_data, "network")
    environment_data = _section(security_data, "environment")
    is_user_config = path is not None and path.resolve() == default_user_config_path().resolve()
    trusted_project = bool(security_data.get("trusted_project", False))
    security = SecurityConfig(
        sandbox_mode=SandboxMode(security_data.get("sandbox_mode", "workspace-write")),
        approval_policy=ApprovalPolicy(security_data.get("approval_policy", "on-request")),
        sandbox_required=bool(security_data.get("sandbox_required", True)),
        sandbox_backend=str(sandbox_data.get("backend", "auto")),
        wsl_distribution=sandbox_data.get("wsl_distribution"),
        network_enabled=str(network_data.get("mode", "none")) != "none",
        default_timeout_seconds=float(sandbox_data.get("default_timeout_seconds", 120)),
        max_output_chars=int(sandbox_data.get("max_output_chars", 50000)),
        environment_allow=tuple(environment_data.get("allow", ["PATH", "LANG", "LC_ALL", "TERM", "SYSTEMROOT", "WINDIR"])),
        trusted_project=trusted_project,
        rules=_load_permission_rules(raw, is_user_config=is_user_config, trusted_project=trusted_project),
    )
    guidance_data = _section(raw, "guidance")
    guidance_rules = _section(guidance_data, "rules")
    guidance = GuidanceConfig(
        enabled=bool(guidance_data.get("enabled", True)),
        max_guidance_bytes=int(guidance_data.get("max_guidance_bytes", 32768)),
        max_import_depth=int(guidance_data.get("max_import_depth", 4)),
        max_import_files=int(guidance_data.get("max_import_files", 32)),
        max_import_total_bytes=int(guidance_data.get("max_import_total_bytes", 32768)),
        project_doc_fallback_filenames=tuple(guidance_data.get("project_doc_fallback_filenames", ["CLAUDE.md"])),
        require_workspace_trust=bool(guidance_data.get("require_workspace_trust", True)),
        activate_search_candidates=bool(guidance_rules.get("activate_search_candidates", False)),
    )
    skills_data = _section(raw, "skills")
    skills = SkillsConfig(
        enabled=bool(skills_data.get("enabled", True)),
        require_workspace_trust=bool(skills_data.get("require_workspace_trust", True)),
        catalog_context_ratio=float(skills_data.get("catalog_context_ratio", 0.02)),
        catalog_fallback_max_chars=int(skills_data.get("catalog_fallback_max_chars", 8000)),
        max_skills=int(skills_data.get("max_skills", 500)),
        max_skill_scan_depth=int(skills_data.get("max_skill_scan_depth", 6)),
        max_skill_directories=int(skills_data.get("max_skill_directories", 2000)),
        max_resource_bytes=int(skills_data.get("max_resource_bytes", 100000)),
        max_skill_file_bytes=int(skills_data.get("max_skill_file_bytes", 1048576)),
        max_frontmatter_bytes=int(skills_data.get("max_frontmatter_bytes", 16384)),
        max_skill_body_bytes=int(skills_data.get("max_skill_body_bytes", 524288)),
        max_resource_files_per_skill=int(skills_data.get("max_resource_files_per_skill", 200)),
        support_fork_context=bool(skills_data.get("support_fork_context", True)),
        disabled_skill_ids=tuple(
            str(item.get("id")) for item in skills_data.get("config", []) if isinstance(item, dict) and item.get("enabled") is False
        ),
    )
    trace_data = _section(raw, "trace")
    trace = TraceConfig(
        directory=Path(trace_data.get("directory", ".harness/runs")),
        thread_directory=Path(trace_data.get("thread_directory", trace_data.get("session_directory", ".harness/threads"))),
        session_directory=Path(trace_data.get("session_directory", ".harness/sessions")),
        fail_on_write_error=bool(trace_data.get("fail_on_write_error", True)),
    )

    if env_model := os.getenv("AGENT_HARNESS_MODEL"):
        provider.model = normalize_model_name(env_model)
    if env_provider := os.getenv("AGENT_HARNESS_PROVIDER"):
        provider.name = env_provider
    if env_url := os.getenv("DEEPSEEK_API_URL"):
        provider.base_url = env_url
    provider.model = normalize_model_name(provider.model)
    return HarnessConfig(provider, agent, run, tools, context, trace, subagents, security, guidance, skills)


def _load_permission_rules(raw: dict[str, Any], *, is_user_config: bool, trusted_project: bool) -> list[PermissionRule]:
    """Parse permission rules and mark project ALLOW rules untrusted until explicitly trusted."""
    permission_data = _section(raw, "permissions")
    rows = permission_data.get("rules", [])
    if not isinstance(rows, list):
        return []
    rules: list[PermissionRule] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        decision = PermissionDecision(str(row.get("decision", "deny")).upper())
        source = RuleSource.USER if is_user_config else RuleSource.TRUSTED_PROJECT
        capability = Capability(str(row["capability"])) if row.get("capability") else None
        argv = row.get("argv_prefix", [])
        rules.append(
            PermissionRule(
                rule_id=str(row.get("id", f"config-rule-{index + 1}")),
                decision=decision,
                source=source,
                tool=row.get("tool"),
                path=row.get("path"),
                argv_prefix=tuple(str(value) for value in argv) if isinstance(argv, list) else (),
                agent=row.get("agent_name"),
                capability=capability,
                trusted=is_user_config or trusted_project or decision != PermissionDecision.ALLOW,
            )
        )
    return rules
