"""Body — executes the current Intent as motion + animation at 60fps.

Holds the current Intent (swapped atomically when the brain emits a new one),
dispatches to a per-verb executor, and switches to the physics/reflex path when
the user grabs the pet. Pure Python; the UI attaches an AnimationPlayer so the
chosen animation state drives frames. Never blocks on the brain.
"""

from __future__ import annotations

from typing import Callable, Optional

from ..log import get
from ..types import Emotion, Intent, Vec2, Verb, WorldSnapshot
from .executors import EXECUTORS
from .motion import MotionState
from .physics import Physics

log = get("body")

# emotion -> preferred animation-state name (resolve_state handles missing ones)
_EMOTION_ANIM = {
    Emotion.HAPPY: "happy",
    Emotion.EXCITED: "excited",
    Emotion.ANNOYED: "angry",
    Emotion.SCARED: "scared",
    Emotion.BORED: "bored",
    Emotion.CURIOUS: "curious",
    Emotion.AFFECTIONATE: "happy",
    Emotion.MISCHIEVOUS: "happy",
    Emotion.SLEEPY: "sleep",
    Emotion.NEUTRAL: "idle",
}


class Body:
    def __init__(self, start_pos: Vec2 | None = None):
        self.motion = MotionState(pos=start_pos or Vec2(300.0, 300.0))
        self.current_intent = Intent(verb=Verb.IDLE)
        self.physics = Physics()
        self.player = None                       # set by UI (AnimationPlayer)
        self._available: set[str] = set()
        self._cur_gen = 0
        self._done_signaled = False
        self.on_action_done: Optional[Callable[[], None]] = None
        self.anim_state = "idle"

    # ---- wiring ------------------------------------------------------------ #
    def attach_player(self, player) -> None:
        self.player = player
        self._available = set(player.clips.keys())

    # ---- intent swap (called on main thread from the brain signal) --------- #
    def set_intent(self, intent: Intent) -> None:
        # drop a stale intent that arrived after a reflex started
        if self.physics.active:
            return
        self.current_intent = intent
        self._cur_gen = intent.gen
        self._done_signaled = False
        log.debug("intent -> %s target=%s emotion=%s", intent.verb.value,
                  intent.target, intent.emotion.value)

    def mark_done(self) -> None:
        if not self._done_signaled:
            self._done_signaled = True
            if self.on_action_done:
                self.on_action_done()

    def emotion_anim(self) -> str:
        return _EMOTION_ANIM.get(self.current_intent.emotion, "idle")

    # ---- reflexes (bypass the brain) --------------------------------------- #
    def grab(self) -> None:
        self.physics.grab()
        self.anim_state = "fall"

    def drag_to(self, pos: Vec2, t: float) -> None:
        self.physics.drag_to(self.motion, pos, t)

    def release(self, t: float) -> None:
        self.physics.release(self.motion, t)

    def poke(self) -> None:
        """A quick tap — react with annoyance but don't enter physics."""
        self.current_intent = Intent(verb=Verb.EMOTION, emotion=Emotion.ANNOYED,
                                     say="mrrp!", thought="(poked)")
        self._done_signaled = False

    # ---- the 60fps step ---------------------------------------------------- #
    def step(self, dt: float, snap: WorldSnapshot) -> None:
        if self.physics.active:
            self.physics.step(self.motion, dt, snap.monitors, snap.taskbar)
            self.anim_state = "fall" if self.physics.state == "thrown" else "sit"
            if self.physics.state == "done":
                # settled -> resume; ask the brain what to do next
                self.mark_done()
            self._apply_anim()
            return

        executor = EXECUTORS.get(self.current_intent.verb, EXECUTORS[Verb.IDLE])
        desired = executor(self, dt, snap)
        self.anim_state = self._resolve(desired)
        self._apply_anim()

    def _resolve(self, name: str) -> str:
        if not self._available:
            return name
        from ..sprite.manifest import resolve_state

        return resolve_state(name, self._available)

    def _apply_anim(self) -> None:
        if self.player is not None:
            self.player.set_state(self.anim_state)
            self.player.set_facing(self.motion.facing)
