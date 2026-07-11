from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_harness.domain.run import RunLimits


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
class HarnessConfig:
    """Top-level configuration object shared by CLI and runtime code."""

    provider: ProviderConfig = field(default_factory=ProviderConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    run: RunLimits = field(default_factory=RunLimits)
    tools: ToolConfig = field(default_factory=ToolConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    trace: TraceConfig = field(default_factory=TraceConfig)
    subagents: SubagentConfig = field(default_factory=SubagentConfig)


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
    """Persist provider settings to the user config file for future CLI runs."""
    path = default_user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(
        [
            "[provider]",
            f'name = "{provider.name}"',
            f'model = "{normalize_model_name(provider.model)}"',
            f'base_url = "{provider.base_url}"',
            f'api_key = "{provider.api_key}"',
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
    trace_data = _section(raw, "trace")
    trace = TraceConfig(
        directory=Path(trace_data.get("directory", ".harness/runs")),
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
    return HarnessConfig(provider, agent, run, tools, context, trace, subagents)
