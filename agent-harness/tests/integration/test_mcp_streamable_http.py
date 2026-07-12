from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from agent_harness.mcp.config import parse_server_config
from agent_harness.mcp.models import MCPConfigScope, MCPServerStatus
from agent_harness.mcp.runtime import MCPRuntime


def free_port() -> int:
    """Reserve an available loopback port for the real HTTP fixture process."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_port(port: int, timeout: float = 10.0) -> None:
    """Wait until the fixture accepts TCP connections or fail the test."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise TimeoutError(f"MCP HTTP fixture did not start on port {port}")


async def test_real_streamable_http_sdk_lifecycle(tmp_path: Path) -> None:
    """Connect through real Streamable HTTP and execute a protocol tool call."""
    port = free_port()
    script = Path(__file__).parents[1] / "fixtures" / "mcp_test_server.py"
    environment = {**os.environ, "MCP_TEST_PORT": str(port)}
    process = subprocess.Popen([sys.executable, str(script), "streamable-http"], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for_port(port)
        config = parse_server_config("http_fixture", {"transport": "streamable_http", "url": f"http://127.0.0.1:{port}/mcp", "always_load_tools": True}, MCPConfigScope.USER, tmp_path)
        runtime = MCPRuntime(type("Resolved", (), {"servers": (config,), "blocked": (), "diagnostics": ()})(), (tmp_path,))
        await runtime.start()
        try:
            connection = runtime.manager.connections["http_fixture"]
            assert connection.status == MCPServerStatus.READY
            result = await connection.call_tool("echo_chinese", {"text": "HTTP 真实调用"})
            assert result["structured_content"] == {"echo": "HTTP 真实调用"}
        finally:
            await runtime.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
