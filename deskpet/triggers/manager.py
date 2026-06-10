"""TriggerManager — decides WHEN to wake the brain.

Heartbeat (jittered) + event triggers + a diff gate (skip identical-world
heartbeats) + a minimum interval floor. Time and jitter are injectable so this
is fully unit-testable without real clocks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from ..config import TriggersConfig
from ..types import WorldSnapshot
from .diff import fingerprint
from .events import IMMEDIATE, Event, TriggerEvent


@dataclass
class WakeDecision:
    wake: bool
    reason: str
    fingerprint: Optional[str] = None


class TriggerManager:
    def __init__(
        self,
        cfg: TriggersConfig,
        *,
        clock: Callable[[], float],
        jitter: Callable[[], float] | None = None,
    ):
        self.cfg = cfg
        self._clock = clock
        # jitter() returns a multiplier in ~[0.8, 1.2]; default = no jitter
        self._jitter = jitter or (lambda: 1.0)
        self._last_wake = -1e9
        self._last_heartbeat = clock()
        self._last_fp: Optional[str] = None

    def evaluate(
        self, events: list[Event], world: WorldSnapshot, screen_phash: Optional[str] = None
    ) -> WakeDecision:
        now = self._clock()
        fp = fingerprint(world, screen_phash)

        # explicit immediate events ignore debounce + diff
        kinds = {e.kind for e in events}
        if kinds & IMMEDIATE:
            return self._fire(now, fp, f"event:{next(iter(kinds & IMMEDIATE)).value}")

        # other real-world-change events fire if past the min-interval floor
        change_events = kinds - {TriggerEvent.HEARTBEAT}
        if change_events:
            if now - self._last_wake >= self.cfg.min_interval_s:
                return self._fire(now, fp, f"event:{next(iter(change_events)).value}")
            return WakeDecision(False, "debounced", fp)

        # heartbeat path: only if the interval elapsed AND world changed
        interval = self.cfg.heartbeat_s * self._jitter()
        if now - self._last_heartbeat >= interval:
            self._last_heartbeat = now
            if fp == self._last_fp:
                return WakeDecision(False, "heartbeat:unchanged", fp)
            if now - self._last_wake >= self.cfg.min_interval_s:
                return self._fire(now, fp, "heartbeat:changed")
        return WakeDecision(False, "idle", fp)

    def _fire(self, now: float, fp: str, reason: str) -> WakeDecision:
        self._last_wake = now
        self._last_fp = fp
        return WakeDecision(True, reason, fp)
