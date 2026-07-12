from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agent_harness.domain.agent import AgentDefinition
from agent_harness.domain.errors import ContextLimitError
from agent_harness.domain.messages import CanonicalMessage
from agent_harness.domain.model import ModelRequest
from agent_harness.domain.run import RunState
from agent_harness.guidance.models import GuidanceDocument, GuidanceSnapshot
from agent_harness.skills.models import SkillActivationSnapshot, SkillCatalogSnapshot
from agent_harness.tools.registry import ToolRegistry


@dataclass(slots=True)
class ContextBuilder:
    """Build model requests from stable runtime context and compact conversation history."""

    char_to_token_ratio: float = 4.0
    max_estimated_input_tokens: int = 120000
    recent_turns: int = 3
    guidance_snapshot: GuidanceSnapshot | None = None
    active_path_rules_provider: Callable[[], tuple[GuidanceDocument, ...]] | None = None
    skill_catalog: SkillCatalogSnapshot | None = None
    active_skills_provider: Callable[[], tuple[SkillActivationSnapshot, ...]] | None = None
    enabled_tools_provider: Callable[[list[str]], list[str]] | None = None
    retrieved_memory_provider: Callable[[], str] | None = None

    def build(self, run: RunState, agent: AgentDefinition, registry: ToolRegistry) -> ModelRequest:
        """Build one model request without appending durable guidance to conversation history."""
        messages = [CanonicalMessage(role="system", content=self._stable_system_content(agent.system_prompt)), *self._visible_history(run)]
        estimated_tokens = self.estimate_tokens(messages)
        if estimated_tokens > self.max_estimated_input_tokens:
            raise ContextLimitError(
                "Estimated context size exceeds configured input token limit",
                details={"estimated_tokens": estimated_tokens, "limit": self.max_estimated_input_tokens},
            )
        return ModelRequest(
            model=agent.model,
            messages=messages,
            tools=registry.export_schemas(self.enabled_tools_provider(agent.enabled_tools) if self.enabled_tools_provider else agent.enabled_tools),
            temperature=agent.temperature,
            max_output_tokens=agent.max_output_tokens,
            request_metadata={"agent_name": agent.name, "run_id": run.run_id, "turn_sequence": run.turn_count or 1, "model_call_sequence": run.model_call_count + 1},
        )

    def _stable_system_content(self, core_prompt: str) -> str:
        """Rebuild Guidance, Catalog, and durable active Skills for every request."""
        sections = [core_prompt]
        if self.guidance_snapshot:
            documents = list(self.guidance_snapshot.documents)
            if self.active_path_rules_provider:
                documents.extend(self.active_path_rules_provider())
            trusted = [document for document in documents if document.trusted]
            if trusted:
                sections.append(_render_guidance(trusted, self.guidance_snapshot.snapshot_id))
        if self.skill_catalog and self.skill_catalog.rendered:
            sections.append("<available_skills>\n需要相关工作流时调用 activate_skill。\n" + self.skill_catalog.rendered + "\n</available_skills>")
        active = self.active_skills_provider() if self.active_skills_provider else ()
        if active:
            sections.append(_render_active_skills(active))
        if self.retrieved_memory_provider:
            memory = self.retrieved_memory_provider()
            if memory:
                sections.append(memory)
        sections.append("Runtime Permission 和 Tool Policy 始终优先；Skill 不能扩大工具权限；更具体路径 Guidance 优先。")
        return "\n\n".join(sections)

    def _visible_history(self, run: RunState) -> list[CanonicalMessage]:
        """Return compact conversation history visible to the next model request."""
        selected = self._recent_messages(run.messages)
        if run.session_summary:
            return [CanonicalMessage(role="user", content=f"此前对话摘要：\n{run.session_summary}"), *selected]
        return selected

    def _recent_messages(self, messages: list[CanonicalMessage]) -> list[CanonicalMessage]:
        """Keep only recent user turns and their following assistant/tool messages."""
        if self.recent_turns <= 0:
            return messages
        indexes = [index for index, message in enumerate(messages) if message.role == "user" and not message.metadata.get("external_context")]
        return messages if len(indexes) <= self.recent_turns else messages[indexes[-self.recent_turns] :]

    def estimate_tokens(self, messages: list[CanonicalMessage]) -> int:
        """Estimate input tokens using the configured character ratio."""
        chars = sum(len(message.content or "") for message in messages)
        chars += sum(len(call.name) + len(str(call.arguments)) for message in messages for call in message.tool_calls)
        return int(chars / self.char_to_token_ratio)


def _render_guidance(documents: list[GuidanceDocument], snapshot_id: str) -> str:
    """Render trusted guidance as a stable non-conversation section."""
    rows = [f'<project_guidance snapshot_id="{snapshot_id}">']
    for document in documents:
        rows.extend([f'<document source="{document.source_kind.value}" path="{document.relative_path or document.path}">', document.content, "</document>"])
    return "\n".join([*rows, "</project_guidance>"])


def _render_active_skills(active: tuple[SkillActivationSnapshot, ...]) -> str:
    """Render durable Skill activation snapshots after the metadata catalog."""
    rows = ["<active_skills>"]
    for activation in active:
        rows.extend([f'<skill name="{activation.qualified_name}" activation_id="{activation.activation_id}">', activation.rendered_instructions, "</skill>"])
    return "\n".join([*rows, "</active_skills>"])
