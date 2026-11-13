from __future__ import annotations
from datetime import datetime, timedelta, timezone
import time
import math


class RealClock:
    kind = "wall"
    def wall_now(self) -> datetime:
        return datetime.now(timezone.utc)
    def monotonic_now(self) -> float:
        return time.monotonic()


class FakeClock:
    kind = "virtual"
    def __init__(self, initial: datetime | None = None):
        self._wall = initial or datetime(2026, 1, 1, 17, tzinfo=timezone.utc)
        if self._wall.tzinfo is None:
            raise ValueError("initial time needs an offset")
        self._wall = self._wall.astimezone(timezone.utc)
        self._mono = 0.0
    def wall_now(self) -> datetime:
        return self._wall
    def monotonic_now(self) -> float:
        return self._mono
    def advance(self, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("clock advance must be finite and non-negative")
        try:
            next_wall = self._wall + timedelta(seconds=seconds)
        except OverflowError as exc:
            raise ValueError("clock advance is outside datetime range") from exc
        self._wall = next_wall
        self._mono += seconds
