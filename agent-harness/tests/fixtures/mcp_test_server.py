from __future__ import annotations

import sys
import os
import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.responses import Response
import uvicorn

server = FastMCP("Harness Test Server", instructions="测试服务说明：只用于协议集成验收。", port=int(os.getenv("MCP_TEST_PORT", "8000")))


@server.tool()
def echo_chinese(text: str) -> dict[str, str]:
    """返回输入的中文文本。"""
    return {"echo": text}


@server.resource("memo://acceptance")
def acceptance_resource() -> str:
    """返回阶段五资源验收内容。"""
    return "真实 MCP 资源读取成功"


@server.prompt()
def chinese_review(topic: str) -> str:
    """生成中文审查提示词。"""
    return f"请用中文审查：{topic}"


def main() -> None:
    """Run the fixture with the transport selected by the first argument."""
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if transport == "streamable-http-fault":
        app: Any = Session404Middleware(server.streamable_http_app(), os.environ["MCP_FAULT_METHOD"], Path(os.environ["MCP_EVENT_LOG"]))
        uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("MCP_TEST_PORT", "8000")), log_level="error")
        return
    server.run(transport=transport)


class Session404Middleware:
    """Inject one real HTTP 404 for a selected session-bound MCP request."""

    def __init__(self, app: Any, fault_method: str, event_log: Path) -> None:
        """Store the wrapped ASGI app and deterministic one-shot fault state."""
        self.app = app
        self.fault_method = fault_method
        self.event_log = event_log
        self.injected = False

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        """Inspect one HTTP JSON-RPC body, log it, and optionally return session 404."""
        if scope.get("type") != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        body, replay = await self._read_and_replay(receive)
        try:
            method = str(json.loads(body).get("method", ""))
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            method = ""
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        self.event_log.parent.mkdir(parents=True, exist_ok=True)
        with self.event_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"method": method, "has_session": "mcp-session-id" in headers}, ensure_ascii=False) + "\n")
        if not self.injected and method == self.fault_method and "mcp-session-id" in headers:
            self.injected = True
            await Response("expired session", status_code=404)(scope, replay, send)
            return
        await self.app(scope, replay, send)

    async def _read_and_replay(self, receive: Any) -> tuple[bytes, Any]:
        """Buffer an ASGI request body and return a receive function that replays it once."""
        chunks: list[bytes] = []
        more = True
        while more:
            message = await receive()
            chunks.append(message.get("body", b""))
            more = bool(message.get("more_body", False))
        sent = False

        async def replay() -> dict[str, Any]:
            """Replay the buffered request body to the wrapped application."""
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        return b"".join(chunks), replay


if __name__ == "__main__":
    main()
