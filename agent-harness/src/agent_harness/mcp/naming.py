from __future__ import annotations

import hashlib
import re
import unicodedata


def canonical_tool_name(server_name: str, tool_name: str, *, max_length: int = 64, hash_length: int = 8) -> str:
    """Create a readable provider-safe tool name with a collision-resistant suffix."""
    suffix = hashlib.sha256(f"{server_name}\0{tool_name}".encode()).hexdigest()[:hash_length]
    server = _readable_component(server_name)
    tool = _readable_component(tool_name)
    available = max(2, max_length - len("mcp____") - len("__") - len(suffix))
    server_budget = max(1, min(len(server), available // 3))
    tool_budget = max(1, available - server_budget)
    return f"mcp__{server[:server_budget]}__{tool[:tool_budget]}__{suffix}"


def _readable_component(value: str) -> str:
    """Normalize Unicode and replace provider-invalid runs with one underscore."""
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9_]+", "_", normalized).strip("_") or "item"
