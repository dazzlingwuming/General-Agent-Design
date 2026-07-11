from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable


class HookDecision(str, Enum):
    """A hook may pass, request approval, or deny; it cannot force allow."""

    PASS = "PASS"
    ASK = "ASK"
    DENY = "DENY"


HookCallback = Callable[[dict[str, Any]], Awaitable[HookDecision]]


@dataclass(slots=True)
class HookManager:
    """Run trusted in-process hooks with timeout and fail-closed behavior."""

    hooks: dict[str, list[HookCallback]] = field(default_factory=dict)
    timeout_seconds: float = 5.0

    async def run(self, point: str, payload: dict[str, Any]) -> HookDecision:
        """Run all callbacks and resolve DENY before ASK before PASS."""
        decisions: list[HookDecision] = []
        try:
            for callback in self.hooks.get(point, []):
                decisions.append(await asyncio.wait_for(callback(payload), timeout=self.timeout_seconds))
        except (asyncio.TimeoutError, Exception):
            return HookDecision.DENY
        if HookDecision.DENY in decisions:
            return HookDecision.DENY
        if HookDecision.ASK in decisions:
            return HookDecision.ASK
        return HookDecision.PASS
