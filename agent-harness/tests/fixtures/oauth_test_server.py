from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import parse_qs

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


PORT = int(os.getenv("OAUTH_TEST_PORT", "8000"))
BASE = f"http://127.0.0.1:{PORT}"
EVENT_LOG = Path(os.environ["OAUTH_EVENT_LOG"])


def record(event: str, payload: dict | None = None) -> None:
    """Append one secret-free OAuth fixture event for deterministic assertions."""
    EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with EVENT_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": event, **(payload or {})}, ensure_ascii=False) + "\n")


async def endpoint(request: Request) -> JSONResponse:
    """Serve protected resource metadata, OAuth metadata, tokens, and the resource."""
    path = request.url.path
    if "oauth-protected-resource" in path:
        record("protected_resource_metadata")
        return JSONResponse({"resource": f"{BASE}/mcp", "authorization_servers": [BASE], "scopes_supported": ["read", "write"]})
    if "oauth-authorization-server" in path or "openid-configuration" in path:
        record("authorization_server_metadata")
        return JSONResponse({"issuer": BASE, "authorization_endpoint": f"{BASE}/authorize", "token_endpoint": f"{BASE}/token", "registration_endpoint": f"{BASE}/register", "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"], "token_endpoint_auth_methods_supported": ["none"], "code_challenge_methods_supported": ["S256"], "scopes_supported": ["read", "write"]})
    if path == "/register":
        record("register")
        return JSONResponse({"client_id": "local-client", "redirect_uris": ["http://127.0.0.1:3030/callback"], "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "token_endpoint_auth_method": "none"}, status_code=201)
    if path == "/token":
        form = parse_qs((await request.body()).decode("utf-8"))
        grant = form.get("grant_type", [""])[0]
        if grant == "refresh_token":
            refresh = form.get("refresh_token", [""])[0]
            if refresh != "refresh-good":
                record("invalid_grant")
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            record("refresh")
            return JSONResponse({"access_token": "access-refreshed", "token_type": "Bearer", "expires_in": 3600, "refresh_token": "refresh-good", "scope": "read"})
        verifier = form.get("code_verifier", [""])[0]
        if grant != "authorization_code" or not verifier:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        record("authorization_code", {"has_code_verifier": True})
        return JSONResponse({"access_token": "access-initial", "token_type": "Bearer", "expires_in": 1, "refresh_token": "refresh-good", "scope": "read"})
    if path == "/mcp":
        authorization = request.headers.get("authorization", "")
        if authorization in {"Bearer access-initial", "Bearer access-refreshed"}:
            record("resource_authorized", {"token": authorization.rsplit(" ", 1)[-1]})
            return JSONResponse({"ok": True})
        record("resource_unauthorized")
        return JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": f'Bearer resource_metadata="{BASE}/.well-known/oauth-protected-resource" scope="read"'})
    return JSONResponse({"error": "not_found", "path": path}, status_code=404)


app = Starlette(routes=[Route("/{path:path}", endpoint, methods=["GET", "POST"])])


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="error")
