from __future__ import annotations

import hashlib

from agent_harness.skills.models import SkillActivationSnapshot, SkillRecord


def read_skill_resource(record: SkillRecord, activation: SkillActivationSnapshot, relative_path: str, max_bytes: int = 100000) -> str:
    """Read one declared text resource from an activated Skill within its real path boundary."""
    if activation.skill_id != record.skill_id:
        raise PermissionError("Skill 尚未激活")
    candidate = (record.base_dir / relative_path).resolve(strict=True)
    candidate.relative_to(record.base_dir.resolve(strict=True))
    manifest = {item.relative_path: item for item in activation.resources}
    normalized = candidate.relative_to(record.base_dir.resolve()).as_posix()
    if normalized not in manifest:
        raise FileNotFoundError("资源不在 Skill Manifest 中")
    if candidate.stat().st_size > max_bytes:
        raise ValueError("Skill Resource 超过大小限制")
    if manifest[normalized].content_hash and hashlib.sha256(candidate.read_bytes()).hexdigest() != manifest[normalized].content_hash:
        raise RuntimeError("Skill Resource 已变化，请重新激活 Skill")
    try:
        return candidate.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("阶段 4 只支持读取 UTF-8 文本 Skill Resource") from exc
