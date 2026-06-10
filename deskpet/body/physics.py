"""Drag-and-throw physics — the reflex path (no LLM). Pure Python.

States: held -> thrown -> settling -> done. While held, the cursor drives the
position (set externally). On release, a fling velocity launches the pet; gravity
pulls it down; it bounces off monitor edges and settles on the taskbar top.
"""

from __future__ import annotations

from collections import deque

from ..types import Rect, Vec2
from .motion import MotionState

GRAVITY = 1600.0       # px/s^2
RESTITUTION = 0.45     # bounce energy retained
FRICTION = 0.8         # horizontal damping per bounce
SETTLE_SPEED = 30.0    # below this on the floor -> settle


class Physics:
    def __init__(self, sprite_half: float = 24.0):
        self.state = "done"
        self.half = sprite_half
        self._samples: deque[tuple[float, Vec2]] = deque(maxlen=5)

    # ---- drag lifecycle ---------------------------------------------------- #
    def grab(self) -> None:
        self.state = "held"
        self._samples.clear()

    def drag_to(self, motion: MotionState, pos: Vec2, t: float) -> None:
        motion.pos = pos
        self._samples.append((t, pos))

    def release(self, motion: MotionState, t: float) -> None:
        vel = Vec2(0.0, 0.0)
        if len(self._samples) >= 2:
            t0, p0 = self._samples[0]
            t1, p1 = self._samples[-1]
            dt = max(1e-3, t1 - t0)
            vel = (p1 - p0) * (1.0 / dt)
        motion.vel = vel
        self.state = "thrown"

    # ---- simulation -------------------------------------------------------- #
    def step(self, motion: MotionState, dt: float, monitors: tuple[Rect, ...],
             taskbar: Rect | None) -> None:
        if self.state in ("done", "held"):
            return
        motion.vel = Vec2(motion.vel.x, motion.vel.y + GRAVITY * dt)
        motion.pos = motion.pos + motion.vel * dt

        bounds = monitors[0] if monitors else Rect(0, 0, 1920, 1080)
        floor = (taskbar.top if taskbar else bounds.bottom) - self.half

        # side walls
        if motion.pos.x < bounds.left + self.half:
            motion.pos = Vec2(bounds.left + self.half, motion.pos.y)
            motion.vel = Vec2(-motion.vel.x * RESTITUTION, motion.vel.y)
        elif motion.pos.x > bounds.right - self.half:
            motion.pos = Vec2(bounds.right - self.half, motion.pos.y)
            motion.vel = Vec2(-motion.vel.x * RESTITUTION, motion.vel.y)

        # ceiling
        if motion.pos.y < bounds.top + self.half:
            motion.pos = Vec2(motion.pos.x, bounds.top + self.half)
            motion.vel = Vec2(motion.vel.x, -motion.vel.y * RESTITUTION)

        # floor / taskbar
        if motion.pos.y >= floor:
            motion.pos = Vec2(motion.pos.x, floor)
            if abs(motion.vel.y) < SETTLE_SPEED and abs(motion.vel.x) < SETTLE_SPEED:
                motion.vel = Vec2(0.0, 0.0)
                self.state = "done"
            else:
                motion.vel = Vec2(motion.vel.x * FRICTION, -motion.vel.y * RESTITUTION)

    @property
    def active(self) -> bool:
        return self.state in ("held", "thrown", "settling")
