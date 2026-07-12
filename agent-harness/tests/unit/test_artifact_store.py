from __future__ import annotations

import base64
from pathlib import Path

import pytest

from agent_harness.artifacts.store import ArtifactSource, ArtifactStore
from agent_harness.domain.errors import ArtifactError


PNG_1X1 = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


async def test_artifact_store_validates_deduplicates_and_cleans_up(tmp_path: Path):
    """Persist validated binary content once under the configured thread root."""
    root = tmp_path / "custom-thread" / "artifacts"
    store = ArtifactStore(root, max_encoded_bytes=1024, max_item_bytes=1024, max_turn_bytes=2048, max_thread_bytes=4096)
    source = ArtifactSource("thread", "turn", "server", "image", "call")
    encoded = base64.b64encode(PNG_1X1).decode("ascii")
    first = await store.put_base64(encoded, "image/png", source)
    second = await store.put_bytes(PNG_1X1, "image/png", source)
    assert first == second
    assert Path(first.path).is_relative_to(root.resolve())
    assert Path(first.path).read_bytes() == PNG_1X1
    assert Path(first.path + ".metadata.json").exists()
    assert len(list((root / "mcp").glob("*.png"))) == 1
    await store.cleanup()
    assert not root.exists()


async def test_artifact_store_rejects_mime_spoofing_invalid_base64_and_quotas(tmp_path: Path):
    """Fail closed for forged binary types, malformed encoding, and every size boundary."""
    source = ArtifactSource("thread", "turn")
    store = ArtifactStore(tmp_path / "artifacts", max_encoded_bytes=16, max_item_bytes=8, max_turn_bytes=10, max_thread_bytes=12)
    with pytest.raises(ArtifactError, match="invalid base64"):
        await store.put_base64("%%%", "image/png", source)
    with pytest.raises(ArtifactError, match="Encoded"):
        await store.put_base64("A" * 20, "image/png", source)
    with pytest.raises(ArtifactError, match="does not match"):
        await store.put_bytes(b"not a png", "image/png", source)
    with pytest.raises(ArtifactError, match="item limit"):
        await store.put_bytes(b"123456789", "text/plain", source)
    await store.put_bytes(b"123456", "text/plain", source)
    with pytest.raises(ArtifactError, match="Turn artifact quota"):
        await store.put_bytes(b"abcde", "text/plain", source)
    other_turn = ArtifactSource("thread", "other")
    await store.put_bytes(b"abcde", "text/plain", other_turn)
    with pytest.raises(ArtifactError, match="Thread artifact quota"):
        await store.put_bytes(b"xyz", "text/plain", ArtifactSource("thread", "third"))
