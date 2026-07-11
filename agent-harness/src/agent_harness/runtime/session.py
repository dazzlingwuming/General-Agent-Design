from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent_harness.config import HarnessConfig
from agent_harness.domain.messages import CanonicalMessage
from agent_harness.domain.run import RunState, RunStatus
from agent_harness.rollout.items import ItemStatus, RolloutItem
from agent_harness.runtime.run_manager import RunManager
from agent_harness.threads.live_thread import LiveThread
from agent_harness.threads.local_store import LocalThreadStore
from agent_harness.tracing.jsonl import JsonlTraceSink
from agent_harness.tracing.summary import write_result_summary
from agent_harness.turns.state import InputItem, ThreadStatus
from agent_harness.utils.serialization import to_jsonable


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
        live = await self.store.create_thread(
            self.workspace.resolve(),
            provider=self.config.provider.name,
            model=self.config.provider.model,
        )
        self.live_thread = live
        self.thread_id = live.state.thread_id
        self.state = RunState(task="", workspace_root=self.workspace.resolve())
        self.state.run_id = live.state.thread_id
        self.manager.rollout_audit = lambda event, payload: self._record_audit_item(event, None, payload)
        self.manager.initialize_project_context(self.thread_id, self.workspace, project_trusted=self.project_trusted)
        self.manager.rollout_audit = None

    async def resume(self, thread_id: str) -> None:
        """Resume an existing thread and rebuild the minimal model-visible history."""
        live = await self.store.resume_thread(thread_id)
        self.live_thread = live
        self.thread_id = live.state.thread_id
        self.state = RunState(task="", workspace_root=live.state.workspace_root)
        self.state.run_id = live.state.thread_id
        self.state.turn_count = live.state.turn_count
        self.state.messages.extend(_messages_from_history(await self.store.load_history(thread_id)))
        self.manager.rollout_audit = lambda event, payload: self._record_audit_item(event, None, payload)
        self.manager.initialize_project_context(self.thread_id, live.state.workspace_root, project_trusted=self.project_trusted, resume=True)
        self.manager.rollout_audit = None

    async def run_turn(self, user_input: str) -> RunState:
        """Append one user message as a new turn, execute the agent loop, and persist items."""
        await self._ensure_started()
        assert self.live_thread is not None
        assert self.state is not None
        self.manager.reset_turn_context()
        turn_id = f"turn_{self.live_thread.state.turn_count + 1:04d}"
        user_input = self._activate_explicit_skill(user_input, turn_id)
        input_item = InputItem(text=user_input)
        self.live_thread.state.turn_count += 1
        self.live_thread.state.active_turn_id = turn_id
        self.live_thread.state.status = ThreadStatus.ACTIVE
        self.state.turn_count = self.live_thread.state.turn_count
        self.state.turn_id = turn_id
        self.state.task = user_input
        self.state.final_output = None
        self.state.error = None
        self.state.iteration = 0
        self.state.model_call_count = 0
        self.state.tool_call_count = 0
        self.state.status = RunStatus.CREATED
        self.state.messages.append(CanonicalMessage(role="user", content=user_input))
        await self.live_thread.update_metadata({"status": ThreadStatus.ACTIVE, "active_turn_id": turn_id})
        await self.live_thread.append_items(
            [
                self._item("turn.started", turn_id, payload={"turn_number": self.live_thread.state.turn_count}),
                self._item("user_message", turn_id, payload={"text": input_item.text, "input_kind": input_item.input_kind}),
            ]
        )
        self.manager.rollout_audit = lambda event, payload: self._record_audit_item(event, turn_id, payload)
        try:
            state = await self.manager.run_existing(self.state, self.config.trace.thread_directory)
        finally:
            self.manager.rollout_audit = None
        terminal_item = "turn.completed" if state.status == RunStatus.COMPLETED else "turn.failed"
        terminal_status = ItemStatus.COMPLETED if state.status == RunStatus.COMPLETED else ItemStatus.FAILED
        self.live_thread.state.status = ThreadStatus.IDLE
        self.live_thread.state.active_turn_id = None
        await self.live_thread.append_items(
            [
                self._item(
                    "agent_message",
                    turn_id,
                    payload={"text": state.final_output, "status": state.status.value, "error": to_jsonable(state.error)},
                ),
                self._item(
                    terminal_item,
                    turn_id,
                    status=terminal_status,
                    payload={
                        "final_output": state.final_output,
                        "error": to_jsonable(state.error),
                        "iteration": state.iteration,
                        "model_call_count": state.model_call_count,
                        "tool_call_count": state.tool_call_count,
                        "usage": to_jsonable(state.usage_total),
                    },
                ),
            ]
        )
        await self.live_thread.update_metadata({"status": ThreadStatus.IDLE, "active_turn_id": None})
        await self.live_thread.flush()
        self._write_turn_summary(state)
        return state

    async def close(self) -> None:
        """Mark the live runtime closed without deleting its canonical history."""
        if self.live_thread is None:
            return
        self.live_thread.state.status = ThreadStatus.CLOSED
        await self.live_thread.update_metadata({"status": ThreadStatus.CLOSED, "active_turn_id": None})
        await self.live_thread.shutdown()

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

    def _activate_explicit_skill(self, user_input: str, turn_id: str) -> str:
        """Expand a leading $skill invocation before the user turn enters model history."""
        if not user_input.startswith("$") or not self.manager.skill_manager:
            return user_input
        token, _, arguments = user_input[1:].partition(" ")
        activation, created = self.manager.skill_manager.activate(token, arguments.strip(), turn_id, user_invocation=True)
        event = "skill.activated" if created else "skill.already_active"
        self._record_audit_item(event, turn_id, {"skill_id": activation.skill_id, "activation_id": activation.activation_id, "explicit": True})
        return arguments.strip() or f"请按照 Skill {activation.qualified_name} 执行。"


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
