from __future__ import annotations

from uuid import uuid4


def new_id(prefix: str) -> str:
    """Generate a prefixed opaque identifier for runs, messages, and events."""
    return f"{prefix}_{uuid4().hex}"
