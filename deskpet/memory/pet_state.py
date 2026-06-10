"""Pet needs model — energy/mood/bond decay & regen over time, persisted.

This is what makes the personality drift and persist: energy drains while awake,
regens while napping; mood eases toward neutral; bond/xp climb with positive
interaction. The values get injected into the prompt so the same persona behaves
differently across a session and across restarts.
"""

from __future__ import annotations

import time
from dataclasses import replace

from ..types import PetState
from .store import MemoryStore

# per-second rates
_ENERGY_DRAIN = 1.0 / (90 * 60)     # ~90 min awake -> empty
_ENERGY_REGEN = 1.0 / (20 * 60)     # ~20 min nap -> full
_MOOD_EASE = 1.0 / (10 * 60)        # mood relaxes toward 0 over ~10 min
_XP_PER_LEVEL = 100


class PetStateManager:
    def __init__(self, store: MemoryStore):
        self.store = store
        self.state = store.load_pet_state() or PetState()
        self._last = time.time()

    def tick(self, *, napping: bool, foreground_app: str | None) -> PetState:
        now = time.time()
        dt = max(0.0, now - self._last)
        self._last = now
        s = self.state

        energy = s.energy + (_ENERGY_REGEN if napping else -_ENERGY_DRAIN) * dt
        energy = max(0.0, min(1.0, energy))

        # mood eases toward 0
        mood = s.mood
        ease = _MOOD_EASE * dt
        mood = mood - ease if mood > 0 else mood + ease
        if abs(mood) < ease:
            mood = 0.0

        # time in current foreground app
        if foreground_app and foreground_app == s.last_app:
            time_in_app = s.time_in_app_s + dt
        else:
            time_in_app = 0.0

        self.state = replace(
            s, energy=energy, mood=mood, last_app=foreground_app,
            time_in_app_s=time_in_app, updated_at=now,
        )
        return self.state

    def reward(self, *, mood: float = 0.0, bond: float = 0.0, xp: int = 0) -> None:
        s = self.state
        new_bond = max(0.0, min(1.0, s.bond + bond))
        new_mood = max(-1.0, min(1.0, s.mood + mood))
        new_xp = s.xp + xp
        level = s.level
        while new_xp >= level * _XP_PER_LEVEL:
            new_xp -= level * _XP_PER_LEVEL
            level += 1
        self.state = replace(s, mood=new_mood, bond=new_bond, xp=new_xp, level=level)

    def save(self) -> None:
        self.store.save_pet_state(self.state)
