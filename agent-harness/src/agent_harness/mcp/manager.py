from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

from agent_harness.mcp.connection import MCPServerConnection
from agent_harness.mcp.errors import MCPConnectionError
from agent_harness.mcp.models import MCPServerConfig, MCPServerStatus


class MCPServerManager:
    """Connect MCP servers concurrently while isolating optional failures."""

    def __init__(self, configs: tuple[MCPServerConfig, ...], roots: tuple[Path, ...], audit: Callable[[str, dict[str, Any]], None] | None = None, max_parallel: int = 4, connect_in_parallel: bool = True) -> None:
        """Create disconnected connection objects and a bounded connect semaphore."""
        self.connections = {config.name: MCPServerConnection(config, roots, audit) for config in configs}
        self.max_parallel = max_parallel
        self.connect_in_parallel = connect_in_parallel

    @property
    def active_servers(self) -> dict[str, MCPServerConnection]:
        """Return only successfully initialized connections."""
        return {name: item for name, item in self.connections.items() if item.status == MCPServerStatus.READY}

    @property
    def failed_servers(self) -> dict[str, MCPServerConnection]:
        """Return connections that failed their latest initialization."""
        return {name: item for name, item in self.connections.items() if item.status == MCPServerStatus.FAILED}

    async def start(self) -> None:
        """Connect enabled servers concurrently and fail only for required entries."""
        semaphore = asyncio.Semaphore(self.max_parallel)

        async def connect_one(connection: MCPServerConnection) -> Exception | None:
            """Connect one server under the manager concurrency bound."""
            async with semaphore:
                try:
                    await connection.connect()
                    return None
                except Exception as exc:
                    return exc

        results = await asyncio.gather(*(connect_one(item) for item in self.connections.values())) if self.connect_in_parallel else [await connect_one(item) for item in self.connections.values()]
        required_errors = [error for error, item in zip(results, self.connections.values()) if error and item.config.required]
        if required_errors:
            await self.shutdown()
            raise MCPConnectionError("Required MCP server failed: " + "; ".join(map(str, required_errors)))

    async def reconnect(self, name: str) -> None:
        """Close and initialize one named server with a fresh protocol session."""
        connection = self.connections[name]
        await connection.close()
        connection.status = MCPServerStatus.RECONNECTING
        await connection.connect()

    async def shutdown(self) -> None:
        """Close every connection concurrently and suppress isolated cleanup errors."""
        await asyncio.gather(*(item.close() for item in self.connections.values()), return_exceptions=True)
