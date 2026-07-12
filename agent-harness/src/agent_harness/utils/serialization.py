from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, cast


def to_jsonable(value: Any) -> Any:
    """Recursively convert dataclasses, enums, paths, and datetimes to JSON values."""
    if not isinstance(value, type) and is_dataclass(value):
        return {k: to_jsonable(v) for k, v in asdict(cast(Any, value)).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return value
