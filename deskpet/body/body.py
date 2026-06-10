"""Body — executes the current Intent as motion + animation at 60fps.

Holds the current Intent (swapped atomically when the brain emits a new one),
dispatches to a per-verb executor, and switches to the physics/reflex path when
the user grabs the pet. Pure Python; the UI attaches an AnimationPlayer so the
chosen animation state drives frames. Never blocks on the brain.
"""

from __future__ import annotations

import random
from typing import Callable, Optional

from ..log import get
from ..sprite.expressions import is_oneshot_emote, pick_variant, resolve_emote
from ..types import Emotion, Intent, Vec2, Verb, WorldSnapshot
from .executors import EXECUTORS
from .motion import MotionState
from .physics import Physics

log = get("body")

SPRITE_HALF = 24.0  # px kept on-screen around the pet's anchor (matches physics)

# animation states during which an emote is suppressed (the pet is moving)
_MOVING_STATES = frozenset({"walk", "run", "pounce", "nudge", "fall"})

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
        self.on_interaction: Optional[Callable[[str], None]] = None  # "poke"|"throw"
        self.anim_state = "idle"
        # animation richness: random variant pools + LLM-chosen emotes
        self._rng = random.Random()
        self._variant_for: Optional[str] = None   # canonical state the variant is for
        self._variant_tag: Optional[str] = None   # the chosen variant tag
        self._emote_token: Optional[str] = None
        self._emote_tag: Optional[str] = None     # resolved tag, or None
        self._emote_oneshot = False
        self._emote_done = True
        self._emote_dur = 0.0                     # one clip-cycle seconds
        self._emote_elapsed = 0.0

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
        self._set_emote(intent.emote)
        log.debug("intent -> %s target=%s emotion=%s emote=%s", intent.verb.value,
                  intent.target, intent.emotion.value, intent.emote)

    def _set_emote(self, token: Optional[str]) -> None:
        """Resolve an emote token to a concrete loaded tag (random among variants)
        and arm it. One-shot emotes (or any non-looping clip) play a single cycle
        then revert; looping emotes run until the next intent replaces them."""
        self._emote_token = token
        self._emote_tag = resolve_emote(token, self._available, self._rng) if self._available else None
        self._emote_elapsed = 0.0
        self._emote_done = self._emote_tag is None
        # measure one cycle + decide one-shot vs loop from the actual clip
        clip = self.player.clips.get(self._emote_tag) if (self.player and self._emote_tag) else None
        self._emote_dur = clip.duration if clip else 0.6
        clip_loops = bool(clip.loop) if clip else True
        self._emote_oneshot = is_oneshot_emote(token) or not clip_loops

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
        self._set_emote(None)

    def drag_to(self, pos: Vec2, t: float) -> None:
        self.physics.drag_to(self.motion, pos, t)

    def release(self, t: float) -> None:
        self.physics.release(self.motion, t)
        if self.on_interaction:
            self.on_interaction("throw")

    def poke(self) -> None:
        """A quick tap — react with annoyance but don't enter physics."""
        # a press always calls grab() first; a tap (no drag) must cancel that
        # grab so the pet doesn't stay stuck in the held physics state.
        self.physics.state = "done"
        if self.on_interaction:
            self.on_interaction("poke")
        self.current_intent = Intent(verb=Verb.EMOTION, emotion=Emotion.ANNOYED,
                                     say="mrrp!", thought="(poked)")
        self._done_signaled = False
        self._set_emote("flinch")

    def pet(self) -> None:
        """Gentle stroking (hover + back-and-forth over the body). The cat loves
        it: affectionate reflex, kneads, occasionally purrs. No physics."""
        if self.physics.active:
            return
        purr = "*purrrr*" if self._rng.random() < 0.18 else None
        self.current_intent = Intent(verb=Verb.EMOTION, emotion=Emotion.AFFECTIONATE,
                                     say=purr, thought="(being petted)")
        self._done_signaled = False
        self._set_emote("knead")
        if self.on_interaction:
            self.on_interaction("pet")

    # ---- the 60fps step ---------------------------------------------------- #
    def step(self, dt: float, snap: WorldSnapshot) -> None:
        if self.physics.active:
            self.physics.step(self.motion, dt, snap.monitors, snap.taskbar, snap.windows)
            self.anim_state = "fall" if self.physics.state == "thrown" else "sit"
            if self.physics.state == "done":
                # settled -> resume; ask the brain what to do next
                self.mark_done()
            self._apply_anim()
            return

        executor = EXECUTORS.get(self.current_intent.verb, EXECUTORS[Verb.IDLE])
        desired = executor(self, dt, snap)
        self._clamp_to_screen(snap)
        self.anim_state = self._resolve(desired)   # canonical (logic/tests read this)
        self._apply_anim(self._choose_anim(desired, dt))  # actual tag shown

    def _choose_anim(self, desired: str, dt: float = 0.0) -> str:
        """Map the executor's canonical desire to an ACTUAL tag to display:
        an active emote (when the pet is stationary) wins; otherwise a random
        variant from the state's pool; otherwise the manifest fallback."""
        # 1. emote overrides while the pet isn't moving
        if self._emote_tag and not self._emote_done and desired not in _MOVING_STATES:
            self._emote_elapsed += dt
            # a one-shot emote reverts after a single clip cycle
            if self._emote_oneshot and self._emote_elapsed >= self._emote_dur:
                self._emote_done = True
            else:
                return self._emote_tag

        # 2. random variant from the locomotion/rest pool (re-picked on change)
        if desired != self._variant_for:
            self._variant_for = desired
            self._variant_tag = (pick_variant(desired, self._available, self._rng)
                                 if self._available else None)
        if self._variant_tag:
            return self._variant_tag

        # 3. plain canonical/alias fallback
        return self._resolve(desired)

    def _clamp_to_screen(self, snap: WorldSnapshot) -> None:
        """Keep the pet's anchor (and its sprite half) inside the desktop. The
        executors can steer toward off-screen targets / the cursor at an edge;
        without this the pet walks off the visible area."""
        mons = snap.monitors
        if not mons:
            return
        h = SPRITE_HALF
        left = min(m.left for m in mons) + h
        top = min(m.top for m in mons) + h
        right = max(m.right for m in mons) - h
        bottom = max(m.bottom for m in mons) - h
        p = self.motion.pos
        x = min(max(p.x, left), right)
        y = min(max(p.y, top), bottom)
        if x != p.x or y != p.y:
            self.motion.pos = Vec2(x, y)
            # bleed velocity into the wall so the pet stops pushing past it
            vx = 0.0 if x != p.x else self.motion.vel.x
            vy = 0.0 if y != p.y else self.motion.vel.y
            self.motion.vel = Vec2(vx, vy)

    def _resolve(self, name: str) -> str:
        if not self._available:
            return name
        from ..sprite.manifest import resolve_state

        return resolve_state(name, self._available)

    def _apply_anim(self, display: Optional[str] = None) -> None:
        if self.player is not None:
            self.player.set_state(display or self.anim_state)
            self.player.set_facing(self.motion.facing)
