from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from agent_harness.rollout.items import RolloutItem, item_from_dict
from agent_harness.utils.serialization import to_jsonable


class RolloutIntegrityError(ValueError):
    """Raised when canonical history is internally corrupted or reordered."""


def hash_item(item: RolloutItem) -> str:
    """Hash one v2 item using canonical JSON while excluding its own hash."""
    payload = to_jsonable(replace(item, item_hash=""))
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_verified(path: Path, *, repair_tail: bool = True) -> list[RolloutItem]:
    """Read canonical history, repair only a partial final row, and fail closed otherwise."""
    if not path.exists():
        return []
    raw = path.read_bytes()
    lines = raw.splitlines(keepends=True)
    items: list[RolloutItem] = []
    previous_hash = ""
    expected_sequence = 1
    valid_bytes = 0
    for index, encoded in enumerate(lines):
        is_last = index == len(lines) - 1
        try:
            data = json.loads(encoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            if repair_tail and is_last:
                _quarantine_tail(path, raw[valid_bytes:])
                path.write_bytes(raw[:valid_bytes])
                break
            raise RolloutIntegrityError(f"Malformed rollout row {index + 1}: {exc}") from exc
        item = item_from_dict(data)
        if item.schema_version == 1:
            items.append(item)
            valid_bytes += len(encoded)
            continue
        if item.sequence_number != expected_sequence:
            raise RolloutIntegrityError(f"Rollout sequence mismatch at row {index + 1}: expected {expected_sequence}, got {item.sequence_number}")
        if item.previous_hash != previous_hash or hash_item(item) != item.item_hash:
            raise RolloutIntegrityError(f"Rollout hash chain mismatch at row {index + 1}")
        items.append(item)
        previous_hash = item.item_hash
        expected_sequence += 1
        valid_bytes += len(encoded)
    return items


def _quarantine_tail(path: Path, tail: bytes) -> None:
    """Preserve a partial final write beside the canonical file for audit."""
    if tail:
        target = path.with_name(path.name + ".corrupt-tail")
        target.write_bytes(tail)
