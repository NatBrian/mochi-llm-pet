"""MotionState + steering helpers. Pure Python, Qt-free, fully testable."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..types import Vec2

WALK_SPEED = 180.0   # px/s
RUN_SPEED = 360.0
ARRIVE_EPS = 8.0     # px


@dataclass
class MotionState:
    pos: Vec2 = field(default_factory=lambda: Vec2(200.0, 200.0))
    vel: Vec2 = field(default_factory=lambda: Vec2(0.0, 0.0))
    facing: int = 1


def seek(motion: MotionState, target: Vec2, dt: float, speed: float) -> bool:
    """Move toward target at `speed`. Returns True on arrival."""
    delta = target - motion.pos
    dist = delta.length()
    if dist <= ARRIVE_EPS:
        motion.vel = Vec2(0.0, 0.0)
        return True
    step = min(dist, speed * dt)
    dir_ = delta.normalized()
    motion.pos = motion.pos + dir_ * step
    motion.vel = dir_ * speed
    if abs(dir_.x) > 0.3:
        motion.facing = 1 if dir_.x > 0 else -1
    return False


def spring(motion: MotionState, target: Vec2, dt: float, stiffness: float = 6.0,
           max_speed: float = RUN_SPEED) -> None:
    """Smooth lag toward a moving target (used for follow_cursor)."""
    delta = target - motion.pos
    desired = delta * stiffness
    sp = desired.length()
    if sp > max_speed:
        desired = desired.normalized() * max_speed
    motion.pos = motion.pos + desired * dt
    motion.vel = desired
    if abs(delta.x) > 1.0:
        motion.facing = 1 if delta.x > 0 else -1
