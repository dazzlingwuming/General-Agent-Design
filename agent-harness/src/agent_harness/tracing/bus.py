from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import threading

from agent_harness.tracing.events import TraceEvent

TraceSubscriber = Callable[[TraceEvent], None]


@dataclass(slots=True)
class Subscription:
    """Own one live event subscription and provide idempotent removal."""

    bus: RuntimeEventBus
    subscriber: TraceSubscriber
    closed: bool = False

    def close(self) -> None:
        """Unsubscribe this callback exactly once."""
        if not self.closed:
            self.bus.unsubscribe(self.subscriber)
            self.closed = True


@dataclass(slots=True)
class RuntimeEventBus:
    """Publish runtime events in-process without coupling runtime code to the CLI."""

    _subscribers: list[TraceSubscriber] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def subscribe(self, subscriber: TraceSubscriber) -> Subscription:
        """Register a subscriber and return its lifetime handle."""
        with self._lock:
            self._subscribers.append(subscriber)
        return Subscription(self, subscriber)

    def unsubscribe(self, subscriber: TraceSubscriber) -> None:
        """Remove a subscriber without failing when it was already removed."""
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    def publish(self, event: TraceEvent) -> None:
        """Deliver an immutable event; renderer failures never fail the runtime."""
        with self._lock:
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception:
                continue

    def write(self, event: TraceEvent) -> None:
        """Implement TraceSink by publishing to all live subscribers."""
        self.publish(event)
