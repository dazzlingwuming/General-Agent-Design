from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
import json
from typing import Any

from agent_harness.domain.model import Usage
from agent_harness.tracing.events import TraceEvent


USAGE_FIELDS = ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "cache_miss_input_tokens", "reasoning_tokens")


@dataclass(frozen=True, slots=True)
class PricingSnapshot:
    """Immutable provider pricing used to make historical estimates reproducible."""

    snapshot_id: str
    provider: str
    model: str
    currency: str
    unit_tokens: int
    effective_from: str
    source_url: str
    cache_hit_input_per_unit: Decimal | None = None
    cache_miss_input_per_unit: Decimal | None = None
    input_per_unit: Decimal | None = None
    output_per_unit: Decimal | None = None

    def estimate(self, usage: Usage) -> Decimal | None:
        """Estimate one response cost without charging reasoning tokens twice."""
        if self.output_per_unit is None or usage.output_tokens is None:
            return None
        if usage.cached_input_tokens is not None and usage.cache_miss_input_tokens is not None:
            if self.cache_hit_input_per_unit is None or self.cache_miss_input_per_unit is None:
                return None
            input_cost = Decimal(usage.cached_input_tokens) * self.cache_hit_input_per_unit
            input_cost += Decimal(usage.cache_miss_input_tokens) * self.cache_miss_input_per_unit
        elif usage.input_tokens is not None and self.input_per_unit is not None:
            input_cost = Decimal(usage.input_tokens) * self.input_per_unit
        else:
            return None
        return (input_cost + Decimal(usage.output_tokens) * self.output_per_unit) / Decimal(self.unit_tokens)


@dataclass(frozen=True, slots=True)
class ModelUsageRecord:
    """Account for one completed provider response from its durable trace event."""

    thread_id: str
    turn_id: str
    iteration: int
    provider: str
    model: str
    response_id: str | None
    duration_ms: int
    usage: Usage
    context_window_tokens: int | None
    pricing_snapshot_id: str | None
    estimated_cost: Decimal | None
    currency: str | None
    provider_details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    """Expose last-call, current-turn, and lifetime-thread accounting separately."""

    last_call: Usage = field(default_factory=Usage)
    current_turn: Usage = field(default_factory=Usage)
    current_thread: Usage = field(default_factory=Usage)
    current_context_tokens: int | None = None
    context_window_tokens: int | None = None
    context_estimated: bool = False
    turn_estimated_cost: Decimal | None = None
    thread_estimated_cost: Decimal | None = None
    currency: str | None = None


class UsageReducer:
    """Reduce replayed and live model events with identical deterministic rules."""

    def __init__(self, pricing: dict[tuple[str, str], PricingSnapshot] | None = None) -> None:
        """Initialize empty accounting with optional immutable price snapshots."""
        self.pricing = pricing or {}
        self.records: list[ModelUsageRecord] = []
        self.current_turn_id: str | None = None
        self.estimated_context_tokens: int | None = None
        self.context_window_tokens: int | None = None

    def apply(self, event: TraceEvent) -> None:
        """Apply one event; reused responses deliberately add no usage or cost."""
        turn_id = event.turn_id or _optional_text(event.payload.get("turn_id"))
        if event.event_type in {"turn.started", "turn.created"} and turn_id:
            self.current_turn_id = turn_id
        if event.event_type in {"context.build.completed", "context.built"}:
            estimate = event.payload.get("estimated_input_tokens")
            if isinstance(estimate, int):
                self.estimated_context_tokens = estimate
            return
        if event.event_type not in {"model.response.completed", "model.completed"}:
            return
        usage = _usage_from_payload(event.payload.get("usage"))
        provider = str(event.payload.get("provider") or "unknown")
        model = str(event.payload.get("model") or "unknown")
        snapshot = _pricing_from_payload(event.payload.get("pricing_snapshot")) or self.pricing.get((provider, model))
        record_turn = turn_id or self.current_turn_id or "turn_unknown"
        self.current_turn_id = record_turn
        window = event.payload.get("context_window_tokens")
        window_tokens = window if isinstance(window, int) else None
        self.context_window_tokens = window_tokens or self.context_window_tokens
        self.records.append(ModelUsageRecord(
            thread_id=event.thread_id or event.run_id, turn_id=record_turn, iteration=event.iteration,
            provider=provider, model=model, response_id=_optional_text(event.payload.get("response_id")),
            duration_ms=int(event.payload.get("duration_ms") or 0), usage=usage, context_window_tokens=window_tokens,
            pricing_snapshot_id=snapshot.snapshot_id if snapshot else None,
            estimated_cost=snapshot.estimate(usage) if snapshot else None, currency=snapshot.currency if snapshot else None,
            provider_details=dict(usage.provider_details),
        ))

    def snapshot(self, turn_id: str | None = None) -> UsageSnapshot:
        """Build a read-only accounting snapshot for the selected or latest turn."""
        selected = turn_id or self.current_turn_id
        turn_records = [record for record in self.records if record.turn_id == selected]
        last = self.records[-1].usage if self.records else Usage()
        turn_cost, turn_currency = _sum_cost(turn_records)
        thread_cost, thread_currency = _sum_cost(self.records)
        exact_context = last.input_tokens
        return UsageSnapshot(last_call=last, current_turn=_sum_usage(turn_records), current_thread=_sum_usage(self.records),
            current_context_tokens=exact_context if exact_context is not None else self.estimated_context_tokens,
            context_window_tokens=self.context_window_tokens, context_estimated=exact_context is None and self.estimated_context_tokens is not None,
            turn_estimated_cost=turn_cost, thread_estimated_cost=thread_cost, currency=turn_currency or thread_currency)


def read_trace(path: Path) -> list[TraceEvent]:
    """Read a trace fail-closed when sequence numbers are duplicated or out of order."""
    events = [TraceEvent.from_dict(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if any(right.sequence_number <= left.sequence_number for left, right in zip(events, events[1:])):
        raise ValueError(f"Trace sequence is not strictly increasing: {path}")
    return events


def _usage_from_payload(value: Any) -> Usage:
    """Parse a normalized usage object and retain provider-specific details."""
    raw = value if isinstance(value, dict) else {}
    return Usage(**{name: raw.get(name) for name in USAGE_FIELDS}, provider_details=dict(raw.get("provider_details") or {}))


def _sum_usage(records: list[ModelUsageRecord]) -> Usage:
    """Sum known fields while preserving unknown values as unknown."""
    result = Usage()
    for name in USAGE_FIELDS:
        values = [getattr(record.usage, name) for record in records if getattr(record.usage, name) is not None]
        setattr(result, name, sum(values) if values else None)
    return result


def _sum_cost(records: list[ModelUsageRecord]) -> tuple[Decimal | None, str | None]:
    """Sum costs only when every selected record has a compatible estimate."""
    if not records or any(record.estimated_cost is None for record in records):
        return None, None
    currencies = {record.currency for record in records}
    if len(currencies) != 1:
        return None, None
    return sum((record.estimated_cost or Decimal(0) for record in records), Decimal(0)), next(iter(currencies))


def _optional_text(value: Any) -> str | None:
    """Convert a non-empty value to text without rendering None as a label."""
    return str(value) if value is not None and str(value) else None


def _pricing_from_payload(value: Any) -> PricingSnapshot | None:
    """Restore the exact pricing snapshot embedded in a historical model event."""
    if not isinstance(value, dict) or not value.get("snapshot_id"):
        return None
    decimal_fields = {"cache_hit_input_per_unit", "cache_miss_input_per_unit", "input_per_unit", "output_per_unit"}
    values = dict(value)
    for name in decimal_fields:
        values[name] = Decimal(str(values[name])) if values.get(name) is not None else None
    return PricingSnapshot(**values)
