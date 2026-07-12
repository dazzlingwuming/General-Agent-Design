from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from mcp.client.auth import TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from agent_harness.mcp.auth import build_oauth_provider, credential_identity
from agent_harness.mcp.config import parse_server_config
from agent_harness.mcp.models import MCPConfigScope
from tests.integration.test_mcp_streamable_http import free_port, wait_for_port


class MemoryTokenStorage(TokenStorage):
    """Keep OAuth test credentials in memory without touching OS keyring."""

    def __init__(self) -> None:
        """Initialize empty token and client registration state."""
        self.tokens: OAuthToken | None = None
        self.client: OAuthClientInformationFull | None = None
        self.expiry_time: float | None = None

    async def get_tokens(self) -> OAuthToken | None:
        """Return current in-memory tokens."""
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        """Replace current in-memory tokens."""
        self.tokens = tokens
        self.expiry_time = time.time() + tokens.expires_in if tokens.expires_in is not None else None

    async def get_token_expiry_time(self) -> float | None:
        """Return the absolute expiry persisted beside in-memory tokens."""
        return self.expiry_time

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        """Return dynamic client registration state."""
        return self.client

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        """Replace dynamic client registration state."""
        self.client = client_info

    async def clear(self) -> None:
        """Delete all in-memory credentials for logout coverage."""
        self.tokens = None
        self.client = None
        self.expiry_time = None


async def test_local_oauth_pkce_refresh_invalid_grant_logout_and_identity(tmp_path: Path) -> None:
    """Exercise official OAuth client discovery and token lifecycle against loopback HTTP."""
    port = free_port()
    event_log = tmp_path / "oauth-events.jsonl"
    script = Path(__file__).parents[1] / "fixtures" / "oauth_test_server.py"
    process = subprocess.Popen([sys.executable, str(script)], env={**os.environ, "OAUTH_TEST_PORT": str(port), "OAUTH_EVENT_LOG": str(event_log)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for_port(port)
        config = parse_server_config("oauth", {"url": f"http://127.0.0.1:{port}/mcp", "auth_mode": "oauth", "oauth_scopes": ["read"]}, MCPConfigScope.USER, tmp_path)
        storage = MemoryTokenStorage()
        callback: dict[str, str | None] = {}

        async def redirect(url: str) -> None:
            """Capture SDK PKCE authorization parameters and simulate user consent."""
            query = parse_qs(urlparse(url).query)
            assert query.get("code_challenge") and query.get("code_challenge_method") == ["S256"]
            callback["state"] = query.get("state", [None])[0]

        async def complete_callback() -> tuple[str, str | None]:
            """Return one deterministic authorization code with the captured state."""
            return "local-code", callback.get("state")

        provider = build_oauth_provider(config, storage, redirect, complete_callback, timeout=10)
        async with httpx.AsyncClient(auth=provider, timeout=10) as client:
            first = await client.post(config.url or "")
            assert first.status_code == 200
            await asyncio.sleep(1.1)
            second = await client.post(config.url or "")
            assert second.status_code == 200
            assert storage.tokens is not None
        storage.tokens = OAuthToken(access_token="expired", token_type="Bearer", expires_in=0, refresh_token="refresh-bad", scope="read")
        storage.expiry_time = time.time() - 1
        recovered_provider = build_oauth_provider(config, storage, redirect, complete_callback, timeout=10)
        async with httpx.AsyncClient(auth=recovered_provider, timeout=10) as client:
            third = await client.post(config.url or "")
            assert third.status_code == 200
        events = [json.loads(line) for line in event_log.read_text(encoding="utf-8").splitlines()]
        assert any(item["event"] == "authorization_code" and item["has_code_verifier"] for item in events)
        assert any(item["event"] == "refresh" for item in events)
        assert any(item["event"] == "invalid_grant" for item in events)
        await storage.clear()
        assert storage.tokens is None and storage.client is None
        changed_scope = parse_server_config("oauth", {"url": config.url, "auth_mode": "oauth", "oauth_scopes": ["read", "write"]}, MCPConfigScope.USER, tmp_path)
        changed_resource = parse_server_config("oauth", {"url": f"http://127.0.0.1:{port}/other", "auth_mode": "oauth", "oauth_scopes": ["read"]}, MCPConfigScope.USER, tmp_path)
        assert credential_identity(config).digest != credential_identity(changed_scope).digest
        assert credential_identity(config).digest != credential_identity(changed_resource).digest
    finally:
        process.terminate()
        process.wait(timeout=10)
