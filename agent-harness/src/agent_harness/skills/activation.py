from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from agent_harness.skills.models import SkillActivationSnapshot, SkillRecord, SkillResource
from agent_harness.skills.parser import split_skill_file
from agent_harness.utils.ids import new_id

POSITIONAL_RE = re.compile(r"\$(?:ARGUMENTS\[(\d+)\]|(\d+))")


@dataclass(slots=True)
class SkillManager:
    """Resolve, activate, persist, and resume skills for one thread runtime."""

    records: tuple[SkillRecord, ...]
    snapshot_root: Path
    active: list[SkillActivationSnapshot] = field(default_factory=list)

    def resolve(self, name: str, *, user_invocation: bool = False) -> SkillRecord:
        """Resolve a qualified or unambiguous short skill name with invocation gates."""
        matches = [record for record in self.records if record.qualified_name == name or record.name == name]
        if not matches:
            raise ValueError(f"Skill 不存在：{name}")
        if len(matches) > 1 and all(record.qualified_name != name for record in matches):
            choices = ", ".join(record.qualified_name for record in matches)
            raise ValueError(f"Skill 名称有歧义，请使用 Qualified Name：{choices}")
        record = next((item for item in matches if item.qualified_name == name), matches[0])
        if not record.enabled or not record.trusted:
            raise PermissionError(f"Skill 未启用或 Workspace 未信任：{record.qualified_name}")
        if user_invocation and not record.user_invocable:
            raise PermissionError(f"Skill 不允许用户显式调用：{record.qualified_name}")
        if not user_invocation and record.disable_model_invocation:
            raise PermissionError(f"Skill 只能由用户显式调用：{record.qualified_name}")
        return record

    def activate(self, name: str, arguments: str, turn_id: str, *, user_invocation: bool = False) -> tuple[SkillActivationSnapshot, bool]:
        """Load and render one Skill body, deduplicate it, and persist a durable snapshot."""
        record = self.resolve(name, user_invocation=user_invocation)
        raw = record.skill_path.read_text(encoding="utf-8")
        _, body = split_skill_file(raw)
        rendered = render_arguments(body, arguments)
        content_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        for activation in self.active:
            if activation.skill_id == record.skill_id and activation.content_hash == content_hash:
                return activation, False
        arguments_hash = hashlib.sha256(arguments.encode("utf-8")).hexdigest()
        activation = SkillActivationSnapshot(
            activation_id=new_id("skill_activation"),
            skill_snapshot_id=f"skill_snapshot_{content_hash[:16]}",
            skill_id=record.skill_id,
            qualified_name=record.qualified_name,
            activated_turn_id=turn_id,
            arguments=arguments,
            arguments_hash=arguments_hash,
            rendered_instructions=rendered,
            content_hash=content_hash,
            source_path=str(record.skill_path),
            allowed_tools=record.allowed_tools,
            context_mode=record.context_mode,
            agent=record.agent,
            resources=record.resources,
        )
        self.active.append(activation)
        self._persist(activation)
        return activation, True

    def resume(self) -> None:
        """Restore active Skill snapshots without reading original SKILL.md files."""
        if not self.snapshot_root.exists():
            return
        restored: list[SkillActivationSnapshot] = []
        for path in sorted(self.snapshot_root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                resources = tuple(SkillResource(**item) for item in data.pop("resources", []))
                restored.append(SkillActivationSnapshot(resources=resources, **data))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        self.active = restored

    def _persist(self, activation: SkillActivationSnapshot) -> None:
        """Write one immutable activation snapshot under the owning thread."""
        self.snapshot_root.mkdir(parents=True, exist_ok=True)
        path = self.snapshot_root / f"{activation.activation_id}.json"
        path.write_text(json.dumps(activation.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def render_arguments(body: str, arguments: str) -> str:
    """Render whole and positional argument placeholders without shell interpretation."""
    values = arguments.split()

    def replace(match: re.Match[str]) -> str:
        """Replace one positional placeholder with a safely bounded argument value."""
        index = int(match.group(1) or match.group(2) or 0)
        return values[index] if index < len(values) else ""

    rendered = POSITIONAL_RE.sub(replace, body).replace("$ARGUMENTS", arguments)
    if arguments and "$ARGUMENTS" not in body and not POSITIONAL_RE.search(body):
        rendered += f"\n\nARGUMENTS: {arguments}"
    return rendered
