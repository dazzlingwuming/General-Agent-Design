from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current UTC datetime with timezone information."""
    return datetime.now(timezone.utc)


def iso_now() -> str:
    """Return the current UTC time formatted as an ISO-8601 string."""
    return utc_now().isoformat()


def duration_ms(start: datetime, end: datetime) -> int:
    """Return the duration between two datetimes in whole milliseconds."""
    return int((end - start).total_seconds() * 1000)
