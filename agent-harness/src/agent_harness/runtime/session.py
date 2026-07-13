from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from agent_harness.config import HarnessConfig
from agent_harness.domain.messages import CanonicalMessage
from agent_harness.domain.errors import RunError
from agent_harness.domain.run import RunState, RunStatus
from agent_harness.rollout.items import ItemStatus, RolloutItem
from agent_harness.runtime.run_manager import RunManager
from agent_harness.threads.live_thread import LiveThread
from agent_harness.threads.local_store import LocalThreadStore
from agent_harness.tracing.jsonl import JsonlTraceSink
from agent_harness.tracing.summary import write_result_summary
from agent_harness.turns.state import InputItem, ThreadStatus
from agent_harness.turns.controller import TurnController
from agent_harness.context.external import ExternalContextItem
from agent_harness.artifacts.store import ArtifactSource
from agent_harness.project.roots import resolve_project_paths
from agent_harness.skills.invocation import SkillInvocationRequest, SkillInvocationSource
from agent_harness.guidance.trust import ProjectTrustContext, TrustDecisionSource, WorkspaceTrustState, resolve_project_trust
from agent_harness.checkpoints.store import CheckpointStore
from agent_harness.checkpoints.serializer import restore_run_state
from agent_harness.checkpoints.models import ResumePoint
from agent_harness.recovery.coordinator import RecoveryCoordinator, RecoveryDisposition
from agent_harness.compaction.service import CompactionService
from agent_harness.domain.model import Usage
from agent_harness.utils.time import utc_now


@dataclass(slots=True)
class ConversationSession:
    """Compatibility wrapper that executes a Codex-style persistent thread."""

    config: HarnessConfig
    manager: RunManager
    workspace: Path
    thread_id: str | None = None
    state: RunState | None = None
    live_thread: LiveThread | None = None
    store: LocalThreadStore = field(init=False)
    project_trusted: bool = False
    trust_context: ProjectTrustContext | None = None
    pending_external_context: list[ExternalContextItem] = field(default_factory=list)
    external_context_hashes: set[str] = field(default_factory=set)
    incomplete_turn: bool = False

    def __post_init__(self) -> None:
        """Initialize the local thread store used by this conversation wrapper."""
        self.store = LocalThreadStore(self.config.trace.thread_directory)

    @property
    def session_id(self) -> str:
        """Return the stable session id; first version keeps session_id equal to thread_id."""
        return self.thread_id or ""

    @property
    def session_dir(self) -> Path:
        """Return the directory that owns all artifacts for this thread."""
        if not self.thread_id:
            return self.config.trace.thread_directory
        return self.config.trace.thread_directory / self.thread_id

    @property
    def thread_dir(self) -> Path:
        """Return the canonical persisted directory for this thread."""
        return self.session_dir

    @property
    def rollout_path(self) -> Path:
        """Return the append-only canonical rollout history path."""
        return self.thread_dir / "rollout.jsonl"

    async def start(self) -> None:
        """Create a new thread and initialize reusable run state."""
        paths = resolve_project_paths(self.workspace)
        self.manager.project_paths = paths
        live = await self.store.create_thread(
            paths.workspace_root,
            provider=self.config.provider.name,
            model=self.config.provider.model,
            project_root=paths.project_root,
            cwd=paths.cwd,
        )
        self.live_thread = live
        self.thread_id = live.state.thread_id
        self.state = RunState(task="", workspace_root=paths.workspace_root)
        self.state.run_id = live.state.thread_id
        self.manager.rollout_audit = lambda event, payload: self._record_audit_item(event, None, payload)
        self.trust_context = self.trust_context or self._legacy_trust_context()
        self.manager.initialize_project_context(self.thread_id, paths.cwd, trust_context=self.trust_context)
        await self.manager.initialize_mcp()
        self.manager.rollout_audit = None

    async def resume(self, thread_id: str) -> None:
        """Resume an existing thread and rebuild the minimal model-visible history."""
        live = await self.store.resume_thread(thread_id)
        self.live_thread = live
        self.thread_id = live.state.thread_id
        self.state = RunState(task="", workspace_root=live.state.workspace_root)
        self.state.run_id = live.state.thread_id
        self.state.turn_count = live.state.turn_count
        history = await self.store.load_history(thread_id)
        self.state.messages.extend(_messages_from_history(history))
        self._restore_external_context(history)
        self.manager.rollout_audit = lambda event, payload: self._record_audit_item(event, None, payload)
        resume_cwd = live.state.cwd or live.state.workspace_root
        self.manager.project_paths = resolve_project_paths(resume_cwd, live.state.workspace_root)
        if self.config.persistence.enabled:
            checkpoint_store = CheckpointStore((live.state.workspace_root / self.config.persistence.runtime_db).resolve())
            self.manager.checkpoint_store = checkpoint_store
            checkpoint = checkpoint_store.latest(thread_id)
            if checkpoint and checkpoint.resume_point == ResumePoint.TERMINAL:
                self.state = restore_run_state(checkpoint.serialized_state, live.state.workspace_root)
                await self._repair_terminal_checkpoint(history)
                self._apply_latest_compaction()
            elif checkpoint:
                plan = RecoveryCoordinator().plan(checkpoint)
                if plan.disposition in {RecoveryDisposition.CONTINUE, RecoveryDisposition.RETRY}:
                    self.state = restore_run_state(checkpoint.serialized_state, live.state.workspace_root)
                    self.manager.resume_point = checkpoint.resume_point
                    self.incomplete_turn = True
                    self._apply_latest_compaction()
                else:
                    live.state.status = ThreadStatus.RECOVERY_REQUIRED
                    await live.update_metadata({"status": ThreadStatus.RECOVERY_REQUIRED})
        self.trust_context = self.trust_context or self._legacy_trust_context()
        self.manager.initialize_project_context(self.thread_id, resume_cwd, trust_context=self.trust_context, resume=True)
        await self.manager.initialize_mcp()
        self.manager.rollout_audit = None

    async def _repair_terminal_checkpoint(self, history: list[RolloutItem]) -> None:
        """Close the checkpoint-to-rollout crash window without duplicating a terminal item."""
        assert self.state is not None
        assert self.live_thread is not None
        turn_id = self.state.turn_id or "turn_unknown"
        terminal_types = {"turn.completed", "turn.failed", "turn.cancelled", "turn.interrupted"}
        terminal_exists = any(item.turn_id == turn_id and item.item_type in terminal_types for item in history)
        if not terminal_exists:
            controller = TurnController(self.live_thread, turn_id, self._item, self._write_turn_summary)
            await controller.finalize_recovered(self.state)
            return
        self.live_thread.state.status = ThreadStatus.IDLE
        self.live_thread.state.active_turn_id = None
        await self.live_thread.update_metadata({"status": ThreadStatus.IDLE, "active_turn_id": None})

    def _apply_latest_compaction(self) -> None:
        """Reapply a verified durable compaction after checkpoint restoration."""
        if not self.config.compaction.enabled or self.state is None or not self.manager.checkpoint_store:
            return
        service = CompactionService(
            self.manager.checkpoint_store.database,
            retain_recent_turns=self.config.compaction.retain_recent_turns,
            max_summary_chars=self.config.compaction.max_summary_chars,
        )
        service.apply_latest(self.state)

    async def continue_incomplete_turn(self) -> RunState | None:
        """Continue the original checkpointed turn without appending a new user turn."""
        if not self.incomplete_turn or self.state is None or self.live_thread is None:
            return None
        self.incomplete_turn = False
        turn_id = self.state.turn_id
        self.manager.rollout_audit = lambda event, payload: self._record_audit_item(event, turn_id, payload)
        try:
            result = await self.manager.run_existing(self.state, self.config.trace.thread_directory)
        finally:
            self.manager.rollout_audit = None
        controller = TurnController(self.live_thread, self.state.turn_id or "turn_unknown", self._item, self._write_turn_summary)
        if result.status == RunStatus.COMPLETED:
            await controller.complete(result)
        elif result.status == RunStatus.CANCELLED:
            await controller.cancel(result, "Recovered turn cancelled")
        else:
            await controller.fail(result)
        return result

    async def run_turn(self, user_input: str) -> RunState:
        """Append one user message as a new turn, execute the agent loop, and persist items."""
        await self._ensure_started()
        assert self.live_thread is not None
        assert self.state is not None
        self.manager.reset_turn_context()
        turn_id = f"turn_{self.live_thread.state.turn_count + 1:04d}"
        self._queue_explicit_skill(user_input, turn_id)
        input_item = InputItem(text=user_input)
        self.live_thread.state.turn_count += 1
        self.state.turn_count = self.live_thread.state.turn_count
        _reset_state_for_new_turn(self.state, turn_id=turn_id, task=user_input)
        self.state.messages.append(CanonicalMessage(role="user", content=user_input))
        await self._inject_pending_external_context(turn_id)
        controller = TurnController(self.live_thread, turn_id, self._item, self._write_turn_summary)
        await controller.start(input_item.text, self.live_thread.state.turn_count)
        self.manager.rollout_audit = lambda event, payload: self._record_audit_item(event, turn_id, payload)
        try:
            state = await self.manager.run_existing(self.state, self.config.trace.thread_directory)
        except asyncio.CancelledError:
            await controller.cancel_shielded(self.state, "Turn execution cancelled")
            raise
        except Exception as exc:
            self.state.status = RunStatus.FAILED
            if self.state.error is None:
                self.state.error = RunError(code="TURN_FAILED", message=str(exc), category="runtime", recoverable=False, cause_type=type(exc).__name__)
            await controller.fail(self.state)
            raise
        finally:
            self.manager.rollout_audit = None
        if state.status == RunStatus.COMPLETED:
            await controller.complete(state)
            self._compact_idle_state(state)
        elif state.status == RunStatus.CANCELLED:
            await controller.cancel(state, "Turn execution cancelled")
        else:
            await controller.fail(state)
        return state

    def _compact_idle_state(self, state: RunState) -> None:
        """Compact model-visible history after terminal persistence without touching rollout JSONL."""
        if not self.config.compaction.enabled or not self.config.compaction.auto_compact or not self.manager.checkpoint_store:
            return
        estimated = sum(len(message.content) for message in state.messages) / self.config.context.char_to_token_ratio
        threshold = self.config.context.max_estimated_input_tokens * self.config.compaction.estimated_token_threshold
        if estimated < threshold:
            return
        service = CompactionService(self.manager.checkpoint_store.database, retain_recent_turns=self.config.compaction.retain_recent_turns,
            max_summary_chars=self.config.compaction.max_summary_chars)
        service.compact(state)

    async def close(self) -> None:
        """Mark the live runtime closed without deleting its canonical history."""
        if self.live_thread is None:
            return
        await self.manager.close_mcp()
        self.live_thread.state.status = ThreadStatus.CLOSED
        await self.live_thread.update_metadata({"status": ThreadStatus.CLOSED, "active_turn_id": None})
        await self.live_thread.shutdown()

    def _legacy_trust_context(self) -> ProjectTrustContext:
        """Convert the compatibility boolean into independent subsystem trust gates."""
        status = WorkspaceTrustState.TRUSTED if self.project_trusted else WorkspaceTrustState.UNKNOWN
        return resolve_project_trust(
            status,
            TrustDecisionSource.INTERACTIVE if self.project_trusted else TrustDecisionSource.DEFAULT,
            guidance_requires_trust=self.config.guidance.require_workspace_trust,
            skills_require_trust=self.config.skills.require_workspace_trust,
            mcp_requires_trust=self.config.mcp.require_workspace_trust,
        )

    async def _ensure_started(self) -> None:
        """Create a thread lazily when callers run a turn without an explicit start."""
        if self.live_thread is None:
            await self.start()

    def _item(
        self,
        item_type: str,
        turn_id: str | None,
        *,
        status: ItemStatus = ItemStatus.COMPLETED,
        payload: dict | None = None,
    ) -> RolloutItem:
        """Create one rollout item bound to this thread and its main agent."""
        assert self.thread_id is not None
        return RolloutItem.create(
            item_type,
            session_id=self.thread_id,
            thread_id=self.thread_id,
            turn_id=turn_id,
            agent_id="coding_assistant",
            status=status,
            payload=payload,
        )

    def _write_turn_summary(self, state: RunState) -> None:
        """Write a per-turn result while keeping the thread-level result current."""
        turns_dir = self.thread_dir / "turns"
        turns_dir.mkdir(parents=True, exist_ok=True)
        result_path = write_result_summary(state, self.thread_dir, self.thread_dir / "events.jsonl")
        turn_path = turns_dir / f"{state.turn_id or 'turn_unknown'}-result.json"
        turn_path.write_text(result_path.read_text(encoding="utf-8"), encoding="utf-8")

    def _record_audit_item(self, event: str, turn_id: str | None, payload: dict) -> None:
        """Queue one permission, approval, sandbox, command, or file event in the rollout."""
        assert self.live_thread is not None
        if event.startswith("file.") and payload.get("path"):
            self.manager.working_set.confirmed_paths.add(str(payload["path"]))
        self.live_thread.recorder.record_nowait([self._item(event, turn_id, payload=payload)])

    def _queue_explicit_skill(self, user_input: str, turn_id: str) -> None:
        """Queue a leading user Skill request while preserving its original history text."""
        if not user_input.startswith("$") or not self.manager.skill_manager:
            return
        token, _, arguments = user_input[1:].partition(" ")
        self.manager.pending_skill_invocations.append(
            SkillInvocationRequest(token, arguments.strip(), SkillInvocationSource.USER_EXPLICIT, self.thread_id or "", turn_id)
        )

    async def queue_external_context(self, source_kind: str, server_name: str, source_name: str, payload: dict, mime_type: str | None = None) -> ExternalContextItem | None:
        """Deduplicate and queue user-selected MCP content for the next safe model boundary."""
        await self._ensure_started()
        assert self.live_thread is not None
        assert self.thread_id is not None
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        encoded = serialized.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        if digest in self.external_context_hashes:
            return None
        artifact_id: str | None = None
        content = serialized
        if len(encoded) > self.config.context.max_external_item_bytes:
            store = self.manager.ensure_artifact_store(self.thread_dir / "artifacts")
            reference = await store.put_text(serialized, "application/json", ArtifactSource(self.thread_id, self.live_thread.state.active_turn_id or "pending", server_name, source_name))
            artifact_id = reference.artifact_id
            content = f"外部内容超过上下文单项限制，已保存为 Artifact {reference.artifact_id}，SHA-256={reference.sha256}，大小={reference.size_bytes} 字节。"
        item = ExternalContextItem(source_kind, server_name, source_name, mime_type, digest, "external_untrusted_user_selected", len(encoded), content, artifact_id)  # type: ignore[arg-type]
        self.external_context_hashes.add(digest)
        if self.live_thread.state.status == ThreadStatus.ACTIVE:
            self.manager.turn_steer_mailbox.append(item.render())
            destination = "active_turn_mailbox"
        else:
            self.pending_external_context.append(item)
            destination = "next_turn"
        await self.live_thread.append_items([self._item("external_context.selected", self.live_thread.state.active_turn_id, payload={"source_kind": source_kind, "server": server_name, "source": source_name, "sha256": digest, "size_bytes": len(encoded), "mime_type": mime_type, "artifact_id": artifact_id, "content": content, "trust_label": item.trust_label, "destination": destination})])
        await self.live_thread.flush()
        return item

    async def _inject_pending_external_context(self, turn_id: str) -> None:
        """Append bounded pending external items after the user's initial turn message."""
        assert self.state is not None
        assert self.live_thread is not None
        total = 0
        injected: list[RolloutItem] = []
        remaining: list[ExternalContextItem] = []
        for item in self.pending_external_context:
            context_cost = len(item.content.encode("utf-8")) if item.artifact_id else item.size_bytes
            if total + context_cost > self.config.context.max_external_turn_bytes:
                remaining.append(item)
                continue
            total += context_cost
            self.state.messages.append(CanonicalMessage(role="user", content=item.render(), metadata={"external_context": True, "trust": item.trust_label, "sha256": item.content_hash}))
            injected.append(self._item("external_context.injected", turn_id, payload={"source_kind": item.source_kind, "server": item.server_name, "source": item.source_name, "sha256": item.content_hash, "size_bytes": item.size_bytes, "artifact_id": item.artifact_id}))
        self.pending_external_context = remaining
        if injected:
            await self.live_thread.append_items(injected)

    def _restore_external_context(self, history: list[RolloutItem]) -> None:
        """Rebuild pending and dedup state from selected/injected rollout provenance."""
        injected = {str(item.payload.get("sha256")) for item in history if item.item_type == "external_context.injected"}
        for rollout_item in history:
            if rollout_item.item_type != "external_context.selected":
                continue
            payload = rollout_item.payload
            digest = str(payload.get("sha256") or "")
            if not digest:
                continue
            self.external_context_hashes.add(digest)
            if digest in injected:
                continue
            source_kind = str(payload.get("source_kind"))
            if source_kind not in {"mcp_resource", "mcp_prompt"}:
                continue
            self.pending_external_context.append(
                ExternalContextItem(
                    source_kind,  # type: ignore[arg-type]
                    str(payload.get("server") or ""),
                    str(payload.get("source") or ""),
                    str(payload["mime_type"]) if payload.get("mime_type") else None,
                    digest,
                    str(payload.get("trust_label") or "external_untrusted_user_selected"),
                    int(payload.get("size_bytes") or 0),
                    str(payload.get("content") or ""),
                    str(payload["artifact_id"]) if payload.get("artifact_id") else None,
                )
            )


def _messages_from_history(history: list[RolloutItem]) -> list[CanonicalMessage]:
    """Rebuild a compact assistant-visible message list from completed rollout history."""
    messages: list[CanonicalMessage] = []
    for item in history:
        if item.item_type == "user_message":
            messages.append(CanonicalMessage(role="user", content=str(item.payload.get("text") or "")))
        if item.item_type == "agent_message" and item.payload.get("text"):
            messages.append(CanonicalMessage(role="assistant", content=str(item.payload["text"])))
    return messages


def create_session_trace(session_id: str, session_root: Path, config: HarnessConfig) -> JsonlTraceSink:
    """Create a trace sink whose run_id is the stable interactive thread id."""
    return JsonlTraceSink(session_id, session_root, fail_on_write_error=config.trace.fail_on_write_error)


def _reset_state_for_new_turn(state: RunState, *, turn_id: str, task: str) -> None:
    """Reset fields owned by one turn while preserving thread history and identity."""
    now = utc_now()
    state.turn_id = turn_id
    state.task = task
    state.status = RunStatus.CREATED
    state.iteration = 0
    state.model_call_count = 0
    state.tool_call_count = 0
    state.usage_total = Usage()
    state.started_at = now
    state.updated_at = now
    state.completed_at = None
    state.final_output = None
    state.error = None
    state.cancellation_requested = False
    state.agent_summary = None
