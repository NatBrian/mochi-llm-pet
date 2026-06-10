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
EDGE_PAD = 8.0         # px inset so the pet lands on a window's body, not its corner


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
             taskbar: Rect | None, windows: tuple = ()) -> None:
        if self.state in ("done", "held"):
            return
        motion.vel = Vec2(motion.vel.x, motion.vel.y + GRAVITY * dt)
        prev_y = motion.pos.y
        motion.pos = motion.pos + motion.vel * dt

        bounds = monitors[0] if monitors else Rect(0, 0, 1920, 1080)

        # side walls — MONITOR edges only; the pet passes through window sides
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

        # floor: taskbar/monitor bottom, OR a window TOP the pet is landing on.
        floor = self._floor_at(motion.pos.x, prev_y, motion.pos.y, bounds, taskbar, windows)
        if motion.pos.y >= floor:
            motion.pos = Vec2(motion.pos.x, floor)
            if abs(motion.vel.y) < SETTLE_SPEED and abs(motion.vel.x) < SETTLE_SPEED:
                motion.vel = Vec2(0.0, 0.0)
                self.state = "done"
            else:
                motion.vel = Vec2(motion.vel.x * FRICTION, -motion.vel.y * RESTITUTION)

    def _floor_at(self, x: float, prev_c: float, new_c: float, bounds: Rect,
                  taskbar: Rect | None, windows: tuple) -> float:
        """The y the pet's center settles at for the given x. The taskbar / monitor
        bottom is always solid. Window TOP edges are ONE-WAY platforms: solid only
        when the pet is descending through the top edge (so it can hit/land on a
        window top, but passes through the sides and from underneath)."""
        floor = (taskbar.top if taskbar else bounds.bottom) - self.half
        ceiling = bounds.top + self.half
        for w in windows:
            r = w.rect
            if not (r.left + EDGE_PAD <= x <= r.right - EDGE_PAD):
                continue                      # not horizontally over this window
            surf = r.top - self.half
            if surf <= ceiling or surf >= floor:
                continue                      # pinned to screen top, or below the floor
            # one-way: only catch while the center crosses the top edge downward
            if prev_c <= surf <= new_c:
                floor = surf
        return floor

    @property
    def active(self) -> bool:
        return self.state in ("held", "thrown", "settling")
