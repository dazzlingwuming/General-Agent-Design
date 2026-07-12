from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agent_harness.domain.errors import ArtifactError


_MIME_EXTENSIONS = {
    "application/json": ".json",
    "text/plain": ".txt",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/ogg": ".ogg",
}


@dataclass(frozen=True, slots=True)
class ArtifactSource:
    """Auditable origin for one host-owned artifact."""

    thread_id: str
    turn_id: str
    server_name: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """Stable reference returned instead of unbounded inline bytes."""

    artifact_id: str
    path: str
    size_bytes: int
    mime_type: str
    sha256: str


@dataclass(slots=True)
class ArtifactStore:
    """Persist validated artifacts atomically under one owning thread directory."""

    root: Path
    max_encoded_bytes: int = 12_000_000
    max_item_bytes: int = 8_000_000
    max_turn_bytes: int = 20_000_000
    max_thread_bytes: int = 100_000_000
    _turn_usage: dict[str, int] = field(default_factory=dict)
    _thread_usage: int = field(init=False)

    def __post_init__(self) -> None:
        """Resolve the root and account for existing artifact payloads on resume."""
        self.root = self.root.resolve()
        self._thread_usage = sum(path.stat().st_size for path in self.root.glob("**/*") if path.is_file() and not path.name.endswith(".metadata.json")) if self.root.exists() else 0

    async def put_text(self, content: str, mime_type: str, source: ArtifactSource) -> ArtifactRef:
        """Encode and persist one textual artifact without blocking the event loop."""
        return await self.put_bytes(content.encode("utf-8"), mime_type, source)

    async def put_base64(self, encoded: str, declared_mime: str, source: ArtifactSource) -> ArtifactRef:
        """Validate encoded and decoded limits before persisting base64 content."""
        if len(encoded.encode("ascii", errors="ignore")) > self.max_encoded_bytes:
            raise ArtifactError("Encoded artifact exceeds item limit")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ArtifactError("Artifact contains invalid base64 data") from exc
        return await self.put_bytes(data, declared_mime, source)

    async def put_bytes(self, data: bytes, declared_mime: str | None, source: ArtifactSource) -> ArtifactRef:
        """Validate MIME and quotas, deduplicate by hash, and atomically write bytes."""
        return await asyncio.to_thread(self._put_bytes, data, declared_mime, source)

    async def cleanup(self) -> None:
        """Delete this store's owned artifact directory after validating its root."""
        await asyncio.to_thread(self._cleanup)

    def _put_bytes(self, data: bytes, declared_mime: str | None, source: ArtifactSource) -> ArtifactRef:
        """Perform the synchronous quota check and atomic payload/metadata writes."""
        mime_type = self._validated_mime(data, declared_mime)
        size = len(data)
        if size > self.max_item_bytes:
            raise ArtifactError("Decoded artifact exceeds item limit")
        digest = hashlib.sha256(data).hexdigest()
        artifact_id = f"artifact_{digest[:24]}"
        extension = _MIME_EXTENSIONS[mime_type]
        directory = self.root / "mcp"
        path = directory / f"{artifact_id}{extension}"
        if path.exists():
            return ArtifactRef(artifact_id, str(path), path.stat().st_size, mime_type, digest)
        turn_total = self._turn_usage.get(source.turn_id, 0)
        if turn_total + size > self.max_turn_bytes:
            raise ArtifactError("Turn artifact quota exceeded")
        if self._thread_usage + size > self.max_thread_bytes:
            raise ArtifactError("Thread artifact quota exceeded")
        directory.mkdir(parents=True, exist_ok=True)
        self._atomic_write(path, data)
        metadata = {
            "artifact_id": artifact_id,
            "sha256": digest,
            "size_bytes": size,
            "mime_type": mime_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": asdict(source),
        }
        self._atomic_write(path.with_name(path.name + ".metadata.json"), json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))
        self._turn_usage[source.turn_id] = turn_total + size
        self._thread_usage += size
        return ArtifactRef(artifact_id, str(path), size, mime_type, digest)

    def _validated_mime(self, data: bytes, declared_mime: str | None) -> str:
        """Apply an allowlist and reject declared binary MIME that conflicts with magic bytes."""
        normalized = (declared_mime or "application/octet-stream").split(";", 1)[0].strip().lower()
        if normalized not in _MIME_EXTENSIONS:
            raise ArtifactError(f"Artifact MIME type is not allowed: {normalized}")
        sniffed = self._sniff_binary_mime(data)
        if normalized.startswith(("image/", "audio/")) and sniffed != normalized:
            raise ArtifactError("Artifact content does not match its declared MIME type")
        return normalized

    def _sniff_binary_mime(self, data: bytes) -> str | None:
        """Recognize the supported image and audio signatures without trusting server headers."""
        signatures = (
            (b"\x89PNG\r\n\x1a\n", "image/png"),
            (b"\xff\xd8\xff", "image/jpeg"),
            (b"GIF8", "image/gif"),
            (b"RIFF", "audio/wav" if data[8:12] == b"WAVE" else "image/webp" if data[8:12] == b"WEBP" else None),
            (b"OggS", "audio/ogg"),
            (b"ID3", "audio/mpeg"),
        )
        for signature, mime_type in signatures:
            if mime_type and data.startswith(signature):
                return mime_type
        if len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0:
            return "audio/mpeg"
        return None

    def _atomic_write(self, path: Path, data: bytes) -> None:
        """Write bytes to a same-directory temporary file before atomic replacement."""
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _cleanup(self) -> None:
        """Remove only the resolved store root owned by this instance."""
        if self.root.exists():
            shutil.rmtree(self.root)
        self._thread_usage = 0
        self._turn_usage.clear()
