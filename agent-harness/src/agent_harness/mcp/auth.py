from __future__ import annotations

import asyncio
import webbrowser
from urllib.parse import parse_qs, urlparse

import keyring
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl

from agent_harness.mcp.models import MCPServerConfig


class KeyringTokenStorage(TokenStorage):
    """Store MCP OAuth tokens in the operating-system credential backend."""

    def __init__(self, server_name: str) -> None:
        """Namespace credentials by MCP server without exposing values in config files."""
        self.service = f"agent-harness-mcp:{server_name}"

    async def get_tokens(self) -> OAuthToken | None:
        """Read and validate an OAuth token record from the OS credential store."""
        raw = await asyncio.to_thread(keyring.get_password, self.service, "tokens")
        return OAuthToken.model_validate_json(raw) if raw else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        """Persist refreshed OAuth tokens in the OS credential store."""
        await asyncio.to_thread(keyring.set_password, self.service, "tokens", tokens.model_dump_json(exclude_none=True))

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        """Read dynamic client registration metadata from the credential store."""
        raw = await asyncio.to_thread(keyring.get_password, self.service, "client_info")
        return OAuthClientInformationFull.model_validate_json(raw) if raw else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        """Persist dynamic client registration metadata securely."""
        await asyncio.to_thread(keyring.set_password, self.service, "client_info", client_info.model_dump_json(exclude_none=True))

    async def clear(self) -> None:
        """Remove tokens and client registration data for logout."""
        for username in ("tokens", "client_info"):
            try:
                await asyncio.to_thread(keyring.delete_password, self.service, username)
            except keyring.errors.PasswordDeleteError:
                continue


def create_oauth_provider(config: MCPServerConfig) -> OAuthClientProvider:
    """Create the official SDK OAuth 2.1 provider, which performs PKCE and discovery."""
    if not config.url:
        raise ValueError("OAuth requires an HTTP MCP server URL")
    redirect_uri = AnyUrl("http://127.0.0.1:3030/callback")
    metadata = OAuthClientMetadata(
        client_name="Agent Harness",
        redirect_uris=[redirect_uri],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        scope=" ".join(config.oauth_scopes) or None,
    )
    return OAuthClientProvider(
        config.url,
        metadata,
        KeyringTokenStorage(config.name),
        redirect_handler=_open_authorization_url,
        callback_handler=_read_authorization_callback,
        timeout=300,
    )


async def _open_authorization_url(url: str) -> None:
    """Open the authorization URL in the user's default browser."""
    await asyncio.to_thread(webbrowser.open, url)
    print(f"请在浏览器完成 MCP 授权：{url}")


async def _read_authorization_callback() -> tuple[str, str | None]:
    """Read the redirected URL from the terminal and return code plus state."""
    value = await asyncio.to_thread(input, "请粘贴授权完成后的完整回调 URL：")
    query = parse_qs(urlparse(value.strip()).query)
    code = query.get("code", [""])[0]
    state = query.get("state", [None])[0]
    if not code:
        raise ValueError("OAuth callback URL does not contain code")
    return code, state
