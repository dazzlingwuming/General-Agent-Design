from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from agent_harness.mcp.config import parse_server_config
from agent_harness.mcp.connection import MCPServerConnection
from agent_harness.mcp.models import MCPConfigScope
from tests.integration.test_mcp_streamable_http import free_port, wait_for_port


def assert_complete_catalog(connection: MCPServerConnection) -> None:
    """Assert all four real protocol catalogs traversed three pages in stable order."""
    assert [item.remote_name for item in connection.tools] == [f"tool-{index}" for index in range(5)]
    assert [item.name for item in connection.resources] == [f"resource-{index}" for index in range(5)]
    assert [item["name"] for item in connection.resource_templates] == [f"template-{index}" for index in range(5)]
    assert [item.name for item in connection.prompts] == [f"prompt-{index}" for index in range(5)]
    assert connection.catalog_page_count == 12
    assert connection.catalog_truncated is False


async def test_real_stdio_protocol_collects_all_catalog_pages(tmp_path: Path) -> None:
    """Traverse opaque cursors through the official stdio client and server codecs."""
    script = Path(__file__).parents[1] / "fixtures" / "mcp_paginated_server.py"
    config = parse_server_config("pages", {"command": sys.executable, "args": [str(script)]}, MCPConfigScope.USER, tmp_path)
    connection = MCPServerConnection(config, (tmp_path,))
    await connection.connect()
    try:
        assert_complete_catalog(connection)
    finally:
        await connection.close()


async def test_real_http_protocol_collects_all_catalog_pages(tmp_path: Path) -> None:
    """Traverse opaque cursors through official Streamable HTTP serialization."""
    port = free_port()
    script = Path(__file__).parents[1] / "fixtures" / "mcp_paginated_server.py"
    process = subprocess.Popen([sys.executable, str(script), "streamable-http"], env={**os.environ, "MCP_TEST_PORT": str(port)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for_port(port)
        config = parse_server_config("pages", {"url": f"http://127.0.0.1:{port}/mcp"}, MCPConfigScope.USER, tmp_path)
        connection = MCPServerConnection(config, (tmp_path,))
        await connection.connect()
        try:
            assert_complete_catalog(connection)
        finally:
            await connection.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
