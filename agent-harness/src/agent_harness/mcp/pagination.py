from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

from agent_harness.mcp.errors import MCPProtocolError

T = TypeVar("T")
PageT = TypeVar("PageT")


async def collect_paginated(fetch_page: Callable[[str | None], Awaitable[PageT]], *, get_items: Callable[[PageT], Sequence[T]], get_next_cursor: Callable[[PageT], str | None], max_pages: int, max_items: int, on_page: Callable[[int, str | None, int], None] | None = None) -> tuple[tuple[T, ...], int, bool]:
    """Collect a bounded cursor catalog and reject cursor loops."""
    if max_pages < 1 or max_items < 1:
        raise ValueError("MCP pagination limits must be positive")
    items: list[T] = []
    seen_cursors: set[str] = set()
    cursor: str | None = None
    page_count = 0
    truncated = False
    while True:
        page_count += 1
        page = await fetch_page(cursor)
        page_items = tuple(get_items(page))
        if on_page:
            on_page(page_count, cursor, len(page_items))
        remaining = max_items - len(items)
        items.extend(page_items[:remaining])
        next_cursor = get_next_cursor(page)
        if len(page_items) > remaining:
            truncated = True
            break
        if not next_cursor:
            break
        if next_cursor in seen_cursors or next_cursor == cursor:
            raise MCPProtocolError("MCP catalog returned a repeated cursor")
        seen_cursors.add(next_cursor)
        if page_count >= max_pages:
            truncated = True
            break
        cursor = next_cursor
    return tuple(items), page_count, truncated
