from __future__ import annotations

import asyncio
import webbrowser
import hashlib
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse, urlunparse
from collections.abc import Awaitable, Callable

import keyring
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl

from agent_harness.mcp.models import MCPServerConfig


@dataclass(frozen=True, slots=True)
class MCPCredentialIdentity:
    """Bind stored OAuth material to one exact resource-server identity."""

    server_name: str
    canonical_resource_uri: str
    auth_mode: str
    scopes: tuple[str, ...]

    @property
    def digest(self) -> str:
        """Return the stable non-secret identity digest used by the credential service."""
        value = "\0".join((self.server_name, self.canonical_resource_uri, self.auth_mode, *self.scopes))
        return hashlib.sha256(value.encode()).hexdigest()


def credential_identity(config: MCPServerConfig) -> MCPCredentialIdentity:
    """Canonicalize URL and scopes so configuration changes require fresh authorization."""
    if not config.url:
        raise ValueError("OAuth credential identity requires an HTTP URL")
    parsed = urlparse(config.url)
    host = (parsed.hostname or "").lower()
    default_port = (parsed.scheme.lower() == "https" and parsed.port == 443) or (parsed.scheme.lower() == "http" and parsed.port == 80)
    netloc = host if default_port or parsed.port is None else f"{host}:{parsed.port}"
    path = "/" + "/".join(part for part in parsed.path.split("/") if part)
    uri = urlunparse((parsed.scheme.lower(), netloc, path or "/", "", parsed.query, ""))
    return MCPCredentialIdentity(config.name, uri, config.auth_mode, tuple(sorted(set(config.oauth_scopes))))


class KeyringTokenStorage(TokenStorage):
    """Store MCP OAuth tokens in the operating-system credential backend."""

    def __init__(self, identity: MCPCredentialIdentity | str) -> None:
        """Namespace credentials by resource identity without exposing values in config files."""
        digest = identity.digest if isinstance(identity, MCPCredentialIdentity) else hashlib.sha256(identity.encode()).hexdigest()
        self.service = f"agent-harness-mcp:{digest[:24]}"

    async def get_tokens(self) -> OAuthToken | None:
        """Read and validate an OAuth token record from the OS credential store."""
        raw = await asyncio.to_thread(keyring.get_password, self.service, "tokens")
        if not raw:
            return None
        payload = json.loads(raw)
        return OAuthToken.model_validate(payload.get("tokens", payload))

    async def set_tokens(self, tokens: OAuthToken) -> None:
        """Persist refreshed OAuth tokens in the OS credential store."""
        payload = {"tokens": tokens.model_dump(mode="json", exclude_none=True), "stored_at": time.time()}
        await asyncio.to_thread(keyring.set_password, self.service, "tokens", json.dumps(payload, separators=(",", ":")))

    async def get_token_expiry_time(self) -> float | None:
        """Recover absolute expiry from the persisted issue time and expires_in value."""
        raw = await asyncio.to_thread(keyring.get_password, self.service, "tokens")
        if not raw:
            return None
        payload = json.loads(raw)
        if not isinstance(payload, dict) or "tokens" not in payload or "stored_at" not in payload:
            return None
        expires_in = payload["tokens"].get("expires_in") if isinstance(payload["tokens"], dict) else None
        return float(payload["stored_at"]) + int(expires_in) if expires_in is not None else None

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
    return build_oauth_provider(config, KeyringTokenStorage(credential_identity(config)), _open_authorization_url, _read_authorization_callback)


def build_oauth_provider(
    config: MCPServerConfig,
    storage: TokenStorage,
    redirect_handler: Callable[[str], Awaitable[None]],
    callback_handler: Callable[[], Awaitable[tuple[str, str | None]]],
    *,
    timeout: float = 300,
) -> OAuthClientProvider:
    """Build the official provider with injectable deterministic OAuth I/O boundaries."""
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
    return HarnessOAuthClientProvider(
        config.url,
        metadata,
        storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
        timeout=timeout,
    )


class HarnessOAuthClientProvider(OAuthClientProvider):
    """Restore persisted absolute token expiry missing from the SDK TokenStorage contract."""

    async def _initialize(self) -> None:
        """Load SDK state and recover expiry when the storage exposes issue-time metadata."""
        await super()._initialize()
        getter = getattr(self.context.storage, "get_token_expiry_time", None)
        if getter is not None:
            self.context.token_expiry_time = await getter()


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
