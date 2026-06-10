"""Sensors — tiered polling driven by the main-thread timer.

`tick(now)` is called frequently (e.g. 30Hz). It runs cheap work every call
(cursor) and heavier work on slower cadences (window enumeration). It writes the
shared WorldState and emits TriggerEvents (foreground/clipboard/idle changes) so
the brain can wake. Win32 calls are cheap enough to run on the main thread; the
brain (the slow part) lives on its own thread.
"""

from __future__ import annotations

import dataclasses
import sys

from ..config import PerceptionConfig
from ..log import get
from ..triggers.events import EventBus, TriggerEvent
from ..types import PetState, Vec2, WorldSnapshot
from .world import WorldState, WorldStateBuilder

log = get("perception.sensors")


class Sensors:
    def __init__(self, cfg: PerceptionConfig, world: WorldState, bus: EventBus,
                 idle_threshold_s: float = 120.0):
        self.cfg = cfg
        self.world = world
        self.bus = bus
        self.idle_threshold_s = idle_threshold_s
        self.builder = WorldStateBuilder(cfg)
        self.is_windows = sys.platform == "win32"
        self._t_fast = 0.0
        self._t_medium = 0.0
        self._last_fg_key = None
        self._last_clip = None
        self._idle_fired = False

    def set_pet(self, pos: Vec2, state: PetState) -> None:
        """Body/state feed the pet's own position + needs back into the world."""
        self.builder.pet_pos = pos
        self.builder.pet_state = state

    def tick(self, now: float) -> None:
        # MEDIUM tier: full rebuild (windows, idle, clipboard)
        if (now - self._t_medium) * 1000.0 >= self.cfg.medium_ms:
            self._t_medium = now
            self._rebuild()
        # FAST tier: cheap cursor refresh patched onto the current snapshot
        elif (now - self._t_fast) * 1000.0 >= self.cfg.fast_ms:
            self._t_fast = now
            self._patch_cursor()

    def _rebuild(self) -> None:
        try:
            snap = self.builder.build()
        except Exception as e:  # noqa: BLE001
            log.debug("sensor rebuild failed: %s", e)
            return
        self.world.write(snap)
        self._emit_events(snap)

    def _patch_cursor(self) -> None:
        if not self.is_windows:
            return
        try:
            from . import win32_input

            cur = win32_input.cursor_pos()
        except Exception:  # noqa: BLE001
            return
        snap = self.world.read()
        self.world.write(dataclasses.replace(snap, cursor=cur))

    def _emit_events(self, snap: WorldSnapshot) -> None:
        fg_key = None
        if snap.foreground:
            fg_key = (snap.foreground.process, snap.foreground.title)
        if fg_key != self._last_fg_key:
            self._last_fg_key = fg_key
            self.bus.emit(TriggerEvent.FOREGROUND_CHANGED, fg_key)

        if snap.clipboard_hash and snap.clipboard_hash != self._last_clip:
            self._last_clip = snap.clipboard_hash
            self.bus.emit(TriggerEvent.CLIPBOARD_CHANGED)

        if snap.idle_s >= self.idle_threshold_s:
            if not self._idle_fired:
                self._idle_fired = True
                self.bus.emit(TriggerEvent.USER_IDLE, snap.idle_s)
        else:
            self._idle_fired = False
