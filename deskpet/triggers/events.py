"""Trigger events + a tiny thread-safe bus.

Sensors and the UI push events; the TriggerManager drains them to decide when to
wake the brain. Uses a plain `queue.Queue` (thread-safe) so producers on the
Qt/main thread and consumers on the brain worker thread interoperate without an
asyncio loop requirement.
"""

from __future__ import annotations

import queue
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TriggerEvent(str, Enum):
    HEARTBEAT = "heartbeat"
    FOREGROUND_CHANGED = "foreground_changed"
    CLIPBOARD_CHANGED = "clipboard_changed"
    USER_IDLE = "user_idle"
    USER_SPOKE = "user_spoke"
    BODY_ACTION_DONE = "body_action_done"
    PET_NEED_CRITICAL = "pet_need_critical"


# events that always bypass the diff gate / debounce
IMMEDIATE = {TriggerEvent.USER_SPOKE, TriggerEvent.BODY_ACTION_DONE}


@dataclass
class Event:
    kind: TriggerEvent
    payload: Any = field(default=None)


class EventBus:
    def __init__(self) -> None:
        self._q: "queue.Queue[Event]" = queue.Queue()

    def emit(self, kind: TriggerEvent, payload: Any = None) -> None:
        self._q.put(Event(kind, payload))

    def drain(self) -> list[Event]:
        out: list[Event] = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                break
        return out

    def get(self, timeout: float | None = None) -> Event | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None
