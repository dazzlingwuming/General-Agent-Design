from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import sys
from typing import Any

from agent_harness.config import HarnessConfig
from agent_harness.domain.model import Usage
from agent_harness.runtime.session import ConversationSession
from agent_harness.tracing.events import TraceEvent
from agent_harness.tracing.reducer import RuntimePhase, TraceReducer
from agent_harness.tracing.usage import read_trace


SECRET_KEY = re.compile(r"(api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|bearer|secret|password|cookie)", re.IGNORECASE)
PHASE_LABELS = {
    RuntimePhase.READY: "Ready", RuntimePhase.PREPARING: "Preparing", RuntimePhase.BUILDING_CONTEXT: "Building context",
    RuntimePhase.CALLING_MODEL: "Calling model", RuntimePhase.PROCESSING_RESPONSE: "Processing response",
    RuntimePhase.WAITING_APPROVAL: "Waiting approval", RuntimePhase.RUNNING_TOOL: "Running tool",
    RuntimePhase.WAITING_SUBAGENT: "Waiting subagent", RuntimePhase.COMPACTING: "Compacting",
    RuntimePhase.RECOVERING: "Recovering", RuntimePhase.FINALIZING: "Finalizing", RuntimePhase.COMPLETED: "Completed",
    RuntimePhase.FAILED: "Failed", RuntimePhase.CANCELLED: "Cancelled",
}


@dataclass(slots=True)
class CliObservability:
    """Own replay, live reduction, and terminal-safe observability presentation."""

    config: HarnessConfig
    session: ConversationSession
    reducer: TraceReducer = field(default_factory=TraceReducer)
    emitted_event_ids: set[str] = field(default_factory=set)

    def replay(self) -> None:
        """Rebuild view state from durable history before subscribing to live events."""
        path = self.session.thread_dir / "events.jsonl"
        if not path.exists():
            return
        for event in read_trace(path):
            self.reducer.apply(event)
            self.emitted_event_ids.add(event.event_id)

    def apply(self, event: TraceEvent) -> str | None:
        """Reduce a live event once and return a committed transcript cell when visible."""
        if event.event_id in self.emitted_event_ids:
            return None
        self.emitted_event_ids.add(event.event_id)
        self.reducer.apply(event)
        return render_event(event, self.config.tui.show_tool_output_lines, self.config.tui.unicode and sys.stdout.isatty())

    def status(self) -> str:
        """Render a static status snapshot without reading runtime private fields."""
        usage = self.reducer.usage_snapshot()
        state = self.session.state
        phase = PHASE_LABELS[self.reducer.state.phase]
        if self.reducer.state.active_tool:
            phase += f": {self.reducer.state.active_tool}"
        lines = ["Agent Harness Status", "", f"  Thread       {self.session.session_id}",
            f"  Turn         {self.reducer.state.turn_id or (state.turn_id if state else None) or 'none'}",
            f"  Phase        {phase}", "", f"  Provider     {self.config.provider.name}",
            f"  Model        {self.reducer.state.model or self.config.provider.model}", f"  Workspace    {self.session.workspace}",
            f"  Sandbox      {self.config.security.sandbox_mode.value}", f"  Approval     {self.config.security.approval_policy.value}", "", "Usage",
            f"  Context      {_context(usage.current_context_tokens, usage.context_window_tokens, usage.context_estimated)}",
            f"  Last call    {_usage_line(usage.last_call)}", f"  This turn    {_tokens(usage.current_turn.total_tokens)} total",
            f"  Thread       {_tokens(usage.current_thread.total_tokens)} total",
            f"  Cache        {_tokens(usage.last_call.cached_input_tokens)} hit + {_tokens(usage.last_call.cache_miss_input_tokens)} miss",
            f"  Reasoning    {_tokens(usage.last_call.reasoning_tokens)} · included in output",
            f"  Cost         {_cost(usage.turn_estimated_cost, usage.currency)} · this turn",
            f"               {_cost(usage.thread_estimated_cost, usage.currency)} · thread", "", "Files",
            f"  Trace        {self.session.thread_dir / 'events.jsonl'}", f"  Rollout      {self.session.rollout_path}"]
        return "\n".join(lines)

    def usage(self, raw: bool = False) -> str:
        """Render detailed per-call usage, optionally with sanitized provider fields."""
        records = self.reducer.usage.records
        lines = ["Token Usage", ""]
        for index, record in enumerate(records, 1):
            line = f"  {index}. {record.model} · {_tokens(record.usage.input_tokens)} input · {_tokens(record.usage.output_tokens)} output · {_tokens(record.usage.total_tokens)} total · {_cost(record.estimated_cost, record.currency)}"
            lines.append(line)
            if raw:
                lines.append("     " + json.dumps(redact(record.provider_details), ensure_ascii=False, sort_keys=True))
        snapshot = self.reducer.usage_snapshot()
        lines.extend(["", f"Turn total    {_usage_line(snapshot.current_turn)} · {_cost(snapshot.turn_estimated_cost, snapshot.currency)}",
            f"Thread total  {_usage_line(snapshot.current_thread)} · {_cost(snapshot.thread_estimated_cost, snapshot.currency)}"])
        return "\n".join(lines)

    def trace(self, mode: str = "default") -> str:
        """Render replayed events as compact, full, or sanitized raw trace output."""
        lines: list[str] = ["Trace", ""]
        for event in self.reducer.state.events:
            if mode == "raw":
                lines.append(json.dumps(redact(event.to_dict()), ensure_ascii=False, sort_keys=True))
                continue
            cell = render_event(event, 1000 if mode == "full" else self.config.tui.show_tool_output_lines, self.config.tui.unicode and sys.stdout.isatty(),
                include_internal=mode == "full")
            if cell:
                lines.append(cell)
        return "\n".join(lines)

    def status_line(self, width: int = 120) -> str:
        """Render configured status fields and remove low-priority fields when narrow."""
        snapshot = self.reducer.usage_snapshot()
        values = {
            "phase": PHASE_LABELS[self.reducer.state.phase], "model": self.reducer.state.model or self.config.provider.model,
            "context-remaining": _remaining(snapshot.current_context_tokens, snapshot.context_window_tokens),
            "turn-tokens": f"turn {_tokens(snapshot.current_turn.total_tokens)}", "thread-tokens": f"thread {_tokens(snapshot.current_thread.total_tokens)}",
            "estimated-cost": _cost(snapshot.turn_estimated_cost, snapshot.currency), "permissions": self.config.security.sandbox_mode.value,
        }
        parts = [values[name] for name in self.config.tui.status_line if name in values]
        while len(" · ".join(parts)) > width and len(parts) > 3:
            parts.pop()
        return " · ".join(parts)


def render_event(event: TraceEvent, output_lines: int, unicode: bool, *, include_internal: bool = False) -> str | None:
    """Render one committed transcript cell from a typed event with bounded output."""
    bullet = "•" if unicode else "*"
    failure = "×" if unicode else "x"
    payload = redact(event.payload)
    if event.event_type == "model.request.started":
        return f"{bullet} Calling {payload.get('model', 'model')}"
    if event.event_type == "model.response.completed":
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        return f"{bullet} Model completed\n  -> {_tokens(usage.get('input_tokens'))} input · {_tokens(usage.get('output_tokens'))} output · {payload.get('duration_ms', 0)}ms"
    if event.event_type.startswith("tool.execution."):
        name = payload.get("tool_name") or payload.get("tool") or "tool"
        title = _tool_title(str(name), payload.get("arguments"))
        if event.event_type == "tool.execution.started":
            return f"{bullet} {title}"
        marker = bullet if event.event_type == "tool.execution.completed" else failure
        preview = str(payload.get("output_preview") or "")
        preview_lines = preview.splitlines()
        shown = preview_lines[:output_lines]
        hidden = len(preview_lines) - len(shown)
        suffix = f"\n  -> ... +{hidden} lines hidden (/trace full)" if hidden else ""
        body = "\n".join(f"  -> {line}" for line in shown)
        return f"{marker} {title} · {payload.get('status', event.event_type.rsplit('.', 1)[-1])} · {payload.get('duration_ms', 0)}ms" + (f"\n{body}" if body else "") + suffix
    if event.event_type in {"approval.requested", "approval.decided", "approval.reused"}:
        return f"{bullet} {event.event_type.replace('.', ' ')}: {payload.get('tool_name') or payload.get('tool') or ''}".rstrip()
    if event.event_type.startswith("subagent.") or event.event_type.startswith("agent.spawn") or event.event_type in {"agent.completed", "agent.failed", "agent.cancelled"} or event.event_type.startswith("recovery."):
        return f"{bullet} {event.event_type.replace('.', ' ')}"
    if event.event_type in {"turn.failed", "run.failed", "turn.cancelled", "run.cancelled"}:
        return f"{failure} {event.event_type.replace('.', ' ')}"
    if include_internal:
        return f"{bullet} {event.event_type} · {json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
    return None


def redact(value: Any) -> Any:
    """Recursively remove credential-like fields before any terminal rendering."""
    if isinstance(value, dict):
        return {key: "[REDACTED]" if SECRET_KEY.search(str(key)) else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def _tokens(value: int | None) -> str:
    """Format token counts compactly while preserving unavailable values."""
    if value is None:
        return "n/a"
    return f"{value / 1000:.1f}k" if value >= 1000 else str(value)


def _usage_line(usage: Usage) -> str:
    """Format input, output, and total without conflating their meanings."""
    return f"{_tokens(usage.input_tokens)} input + {_tokens(usage.output_tokens)} output = {_tokens(usage.total_tokens)}"


def _cost(value: Any, currency: str | None) -> str:
    """Format an estimate only when a trace-bound pricing snapshot exists."""
    if value is None or currency is None:
        return "n/a"
    symbol = "¥" if currency == "CNY" else "$" if currency == "USD" else currency + " "
    return f"{symbol}{value:.4f} est"


def _context(current: int | None, window: int | None, estimated: bool) -> str:
    """Format current request context separately from lifetime thread usage."""
    if current is None or window is None:
        return "unavailable"
    marker = " est" if estimated else ""
    return f"{_tokens(current)} / {_tokens(window)}{marker} · {_remaining(current, window)}"


def _remaining(current: int | None, window: int | None) -> str:
    """Compute remaining context percentage from the latest request only."""
    if current is None or not window:
        return "ctx n/a"
    return f"ctx {max(0, round((window - current) / window * 100))}% left"


def _tool_title(name: str, arguments: Any) -> str:
    """Build deterministic command, file, search, patch, and MCP transcript labels."""
    values = arguments if isinstance(arguments, dict) else {}
    if name == "run_command":
        return f"Ran {values.get('command') or values.get('argv') or name}"
    if name == "read_file":
        return f"Read {values.get('path') or ''}".rstrip()
    if name == "search_text":
        return f"Searched {values.get('query') or values.get('pattern') or ''}".rstrip()
    if name in {"apply_patch", "write_file", "delete_path"}:
        return f"Updated {values.get('path') or name}"
    if name.startswith("mcp__"):
        return "Called MCP " + name.removeprefix("mcp__").replace("__", ".")
    return f"Ran {name}"
