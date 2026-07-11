from __future__ import annotations

from pathlib import Path

from agent_harness.config import ProviderConfig, default_user_config_path, load_config, normalize_model_name, write_user_config
from agent_harness.providers.deepseek import DeepSeekProvider


def test_model_aliases_map_to_real_model_ids():
    """Verify that short CLI aliases map to the provider model identifiers."""
    assert normalize_model_name("v4-flash") == "deepseek-v4-flash"
    assert normalize_model_name("v4-pro") == "deepseek-v4-pro"


def test_load_config_reads_dotenv_without_overwriting_existing_env(tmp_path: Path, monkeypatch):
    """Verify that .env values can provide the DeepSeek-compatible base URL."""
    env_path = tmp_path / ".env"
    config_path = tmp_path / "harness.toml"
    env_path.write_text("DEEPSEEK_API_URL=https://example.invalid\n", encoding="utf-8")
    config_path.write_text("", encoding="utf-8")
    monkeypatch.delenv("DEEPSEEK_API_URL", raising=False)
    config = load_config(config_path)
    assert config.provider.base_url == "https://example.invalid"


def test_deepseek_provider_repr_does_not_expose_api_key(monkeypatch):
    """Verify that provider repr does not leak the configured API key."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-test-value")
    provider = DeepSeekProvider(base_url="https://example.invalid")
    try:
        assert "secret-test-value" not in repr(provider)
    finally:
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


def test_user_config_is_loaded_by_default(tmp_path: Path, monkeypatch):
    """Verify that default user config is loaded when no config path is passed."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    write_user_config(
        ProviderConfig(
            name="deepseek",
            model="v4-pro",
            base_url="https://example.invalid",
            api_key="secret-value",
        )
    )
    config = load_config()
    assert default_user_config_path() == tmp_path / "agent-harness" / "config.toml"
    assert config.provider.model == "deepseek-v4-pro"
    assert config.provider.api_key == "secret-value"


def test_load_config_reads_subagent_limits(tmp_path: Path):
    """Verify phase 2 subagent scheduler limits can be configured from TOML."""
    path = tmp_path / "harness.toml"
    path.write_text(
        "\n".join(
            [
                "[subagents]",
                "max_concurrent = 2",
                "max_total = 5",
                "max_depth = 1",
                "max_turns_per_thread = 3",
                "max_followup_message_chars = 1200",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.subagents.max_concurrent == 2
    assert config.subagents.max_total == 5
    assert config.subagents.max_turns_per_thread == 3
    assert config.subagents.max_followup_message_chars == 1200
