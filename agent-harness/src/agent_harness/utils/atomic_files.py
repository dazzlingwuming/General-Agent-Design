from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from agent_harness.utils.serialization import to_jsonable


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Durably replace a text file without exposing a partially written target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding=encoding, dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def atomic_write_json(path: Path, value: Any) -> None:
    """Serialize JSON and atomically replace the destination file."""
    atomic_write_text(path, json.dumps(to_jsonable(value), ensure_ascii=False, indent=2))
