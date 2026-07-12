from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from typing import Any

import uvicorn
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Mount


server = Server("paginated-fixture")
CURSORS = {None: 0, "opaque-A": 2, "opaque-B": 4}
NEXT = {0: "opaque-A", 2: "opaque-B", 4: None}


def page(values: list[Any], cursor: str | None) -> tuple[list[Any], str | None]:
    """Return two deterministic items using an opaque cursor mapping."""
    offset = CURSORS.get(cursor)
    if offset is None:
        raise ValueError("invalid opaque cursor")
    return values[offset : offset + 2], NEXT[offset]


@server.list_tools()
async def list_tools(request: types.ListToolsRequest) -> types.ListToolsResult:
    """Return one serialized page from the five-tool catalog."""
    values = [types.Tool(name=f"tool-{index}", description=f"工具 {index}", inputSchema={"type": "object"}) for index in range(5)]
    items, cursor = page(values, request.params.cursor if request.params else None)
    return types.ListToolsResult(tools=items, nextCursor=cursor)


@server.list_resources()
async def list_resources(request: types.ListResourcesRequest) -> types.ListResourcesResult:
    """Return one serialized page from the five-resource catalog."""
    values = [types.Resource(uri=f"memo://item-{index}", name=f"resource-{index}") for index in range(5)]
    items, cursor = page(values, request.params.cursor if request.params else None)
    return types.ListResourcesResult(resources=items, nextCursor=cursor)


@server.list_prompts()
async def list_prompts(request: types.ListPromptsRequest) -> types.ListPromptsResult:
    """Return one serialized page from the five-prompt catalog."""
    values = [types.Prompt(name=f"prompt-{index}", description=f"提示 {index}") for index in range(5)]
    items, cursor = page(values, request.params.cursor if request.params else None)
    return types.ListPromptsResult(prompts=items, nextCursor=cursor)


async def list_templates(request: types.ListResourceTemplatesRequest) -> types.ServerResult:
    """Return paginated templates through the SDK handler registration point."""
    values = [types.ResourceTemplate(uriTemplate=f"repo://{{path}}/{index}", name=f"template-{index}") for index in range(5)]
    items, cursor = page(values, request.params.cursor if request.params else None)
    return types.ServerResult(types.ListResourceTemplatesResult(resourceTemplates=items, nextCursor=cursor))


server.request_handlers[types.ListResourceTemplatesRequest] = list_templates


async def run_stdio() -> None:
    """Serve the low-level fixture over official stdio transport."""
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def run_http() -> None:
    """Serve the low-level fixture through the official Streamable HTTP manager."""
    manager = StreamableHTTPSessionManager(server, json_response=True)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette):
        """Own the HTTP session manager for the Starlette application lifetime."""
        async with manager.run():
            yield

    app = Starlette(routes=[Mount("/", app=manager.handle_request)], lifespan=lifespan)
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("MCP_TEST_PORT", "8000")), log_level="error")


def main() -> None:
    """Select stdio or Streamable HTTP fixture transport."""
    if len(sys.argv) > 1 and sys.argv[1] == "streamable-http":
        run_http()
    else:
        asyncio.run(run_stdio())


if __name__ == "__main__":
    main()
