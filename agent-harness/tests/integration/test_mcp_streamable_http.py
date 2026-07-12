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
from agent_harness.mcp.errors import MCPToolOutcomeUnknown
import json
import pytest


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


@pytest.mark.parametrize("method", ["resources/read", "prompts/get"])
async def test_real_http_session_404_reinitializes_and_retries_reads(tmp_path: Path, method: str) -> None:
    """Recover a real expired Streamable HTTP session and retry one read-class request."""
    port = free_port()
    event_log = tmp_path / "events.jsonl"
    script = Path(__file__).parents[1] / "fixtures" / "mcp_test_server.py"
    environment = {**os.environ, "MCP_TEST_PORT": str(port), "MCP_FAULT_METHOD": method, "MCP_EVENT_LOG": str(event_log)}
    process = subprocess.Popen([sys.executable, str(script), "streamable-http-fault"], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for_port(port)
        config = parse_server_config("fault", {"url": f"http://127.0.0.1:{port}/mcp"}, MCPConfigScope.USER, tmp_path)
        runtime = MCPRuntime(type("Resolved", (), {"servers": (config,), "blocked": (), "diagnostics": ()})(), (tmp_path,))
        await runtime.start()
        connection = runtime.manager.connections["fault"]
        try:
            if method == "resources/read":
                result = await connection.read_resource("memo://acceptance")
                assert "真实 MCP 资源读取成功" in json.dumps(result, ensure_ascii=False)
            else:
                result = await connection.get_prompt("chinese_review", {"topic": "会话恢复"})
                assert "会话恢复" in json.dumps(result, ensure_ascii=False)
            assert connection.generation == 2
        finally:
            await runtime.close()
        events = [json.loads(line) for line in event_log.read_text(encoding="utf-8").splitlines()]
        assert sum(item["method"] == "initialize" for item in events) == 2
        assert sum(item["method"] == method for item in events) == 2
    finally:
        process.terminate()
        process.wait(timeout=10)


async def test_real_http_tool_404_reconnects_without_replaying_call(tmp_path: Path) -> None:
    """Rebuild an expired tool session while reporting unknown outcome without replay."""
    port = free_port()
    event_log = tmp_path / "events.jsonl"
    script = Path(__file__).parents[1] / "fixtures" / "mcp_test_server.py"
    environment = {**os.environ, "MCP_TEST_PORT": str(port), "MCP_FAULT_METHOD": "tools/call", "MCP_EVENT_LOG": str(event_log)}
    process = subprocess.Popen([sys.executable, str(script), "streamable-http-fault"], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for_port(port)
        config = parse_server_config("fault", {"url": f"http://127.0.0.1:{port}/mcp"}, MCPConfigScope.USER, tmp_path)
        runtime = MCPRuntime(type("Resolved", (), {"servers": (config,), "blocked": (), "diagnostics": ()})(), (tmp_path,))
        await runtime.start()
        connection = runtime.manager.connections["fault"]
        try:
            with pytest.raises(MCPToolOutcomeUnknown):
                await connection.call_tool("echo_chinese", {"text": "不得重放"})
            assert connection.generation == 2
        finally:
            await runtime.close()
        events = [json.loads(line) for line in event_log.read_text(encoding="utf-8").splitlines()]
        assert sum(item["method"] == "initialize" for item in events) == 2
        assert sum(item["method"] == "tools/call" for item in events) == 1
    finally:
        process.terminate()
        process.wait(timeout=10)
