from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from agent_harness.tracing.events import TraceEvent


class TraceSink(Protocol):
    """Accept one already-created immutable event."""

    def write(self, event: TraceEvent) -> None:
        """Write or publish one event without changing its identity."""
        ...


@dataclass(frozen=True, slots=True)
class CallbackTraceSink:
    """Adapt an existing event callback to the typed sink protocol."""

    callback: Callable[[TraceEvent], None]

    def write(self, event: TraceEvent) -> None:
        """Forward one event to the wrapped callback."""
        self.callback(event)


class CompositeTraceSink:
    """Fan one immutable event out to durable and live sinks in order."""

    def __init__(self, sinks: Iterable[TraceSink]) -> None:
        """Freeze the sink order so persistence always precedes live rendering."""
        self.sinks = tuple(sinks)

    def write(self, event: TraceEvent) -> None:
        """Deliver the same event instance to every configured sink."""
        for sink in self.sinks:
            sink.write(event)
