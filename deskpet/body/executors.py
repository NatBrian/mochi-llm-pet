"""Per-verb executors. Each takes (body, dt, snapshot) and returns the desired
animation-state name, updating the body's motion. Verbs that complete call
`body.mark_done()`. Pure Python — no Qt."""

from __future__ import annotations

from ..types import Vec2, Verb, WorldSnapshot
from .motion import RUN_SPEED, WALK_SPEED, seek, spring

NEAR = 26.0  # px considered "at" a perch/target
EDGE = 24.0  # keep targets (and the pet) this far inside the desktop


def _clamp_target(snap: WorldSnapshot, pt: Vec2 | None) -> Vec2 | None:
    """Pull a target inside the desktop so the pet can actually arrive on it
    (a point in the edge margin is otherwise unreachable once the pet is clamped)."""
    if pt is None or not snap.monitors:
        return pt
    mons = snap.monitors
    left = min(m.left for m in mons) + EDGE
    top = min(m.top for m in mons) + EDGE
    right = max(m.right for m in mons) - EDGE
    bottom = max(m.bottom for m in mons) - EDGE
    return Vec2(min(max(pt.x, left), right), min(max(pt.y, top), bottom))


def _target_point(body, snap: WorldSnapshot) -> Vec2 | None:
    intent = body.current_intent
    pt = intent.point if intent.point is not None else snap.resolve(intent.target)
    return _clamp_target(snap, pt)


def ex_idle(body, dt, snap):
    body.motion.vel = Vec2(0.0, 0.0)
    return body.emotion_anim()


def ex_walk_to(body, dt, snap):
    tgt = _target_point(body, snap)
    if tgt is None:
        body.mark_done()
        return "idle"
    if seek(body.motion, tgt, dt, WALK_SPEED):
        body.mark_done()
        return body.emotion_anim()
    return "walk"


def ex_follow_cursor(body, dt, snap):
    spring(body.motion, snap.cursor, dt, stiffness=5.0, max_speed=RUN_SPEED)
    return "run" if body.motion.vel.length() > WALK_SPEED else "walk"


def ex_chase(body, dt, snap):
    tgt = body.current_intent.point or snap.resolve(body.current_intent.target) or snap.cursor
    seek(body.motion, _clamp_target(snap, tgt), dt, RUN_SPEED)
    return "run"


def ex_sit_on(body, dt, snap):
    win = snap.resolve_window(body.current_intent.target)
    if win is None:
        body.mark_done()
        return "idle"
    edge = body.current_intent.edge or "top"
    point = win.rect.edge_point(edge)
    if seek(body.motion, point, dt, WALK_SPEED):
        # ride the edge as the window moves
        body.motion.pos = point
        return "sit"
    return "walk"


def ex_watch(body, dt, snap):
    tgt = _target_point(body, snap)
    if tgt is None:
        return "watch"
    # stand a bit below/in front of the window and face it
    if seek(body.motion, tgt, dt, WALK_SPEED):
        body.motion.facing = 1 if tgt.x >= body.motion.pos.x else -1
        return "watch"
    return "walk"


def ex_nap(body, dt, snap):
    # drift down to the taskbar line (only the vertical gap matters), then sleep
    if snap.taskbar is not None:
        floor_y = snap.taskbar.top
        if abs(floor_y - body.motion.pos.y) > NEAR:
            seek(body.motion, Vec2(body.motion.pos.x, floor_y), dt, WALK_SPEED)
            return "walk"
    body.motion.vel = Vec2(0.0, 0.0)
    return "sleep"


def ex_pounce(body, dt, snap):
    tgt = _target_point(body, snap) or snap.cursor
    if seek(body.motion, tgt, dt, RUN_SPEED * 1.4):
        body.mark_done()
        return body.emotion_anim()
    return "pounce"


def ex_nudge(body, dt, snap):
    tgt = _target_point(body, snap) or snap.cursor
    if seek(body.motion, tgt, dt, RUN_SPEED):
        body.mark_done()
        return body.emotion_anim()
    return "nudge"


def ex_look_at(body, dt, snap):
    body.motion.vel = Vec2(0.0, 0.0)
    tgt = _target_point(body, snap)
    if tgt is not None:
        body.motion.facing = 1 if tgt.x >= body.motion.pos.x else -1
    return "look"


def ex_hide(body, dt, snap):
    # scoot to the nearest screen edge and sit small
    bounds = snap.monitors[0] if snap.monitors else None
    if bounds is not None:
        left_d = abs(body.motion.pos.x - bounds.left)
        right_d = abs(bounds.right - body.motion.pos.x)
        edge_x = bounds.left + 20 if left_d < right_d else bounds.right - 20
        if seek(body.motion, Vec2(edge_x, body.motion.pos.y), dt, RUN_SPEED):
            return "sit"
        return "walk"
    return "sit"


def ex_say(body, dt, snap):
    # keep gentle idle motion; the speech bubble is shown from the intent
    body.motion.vel = Vec2(0.0, 0.0)
    return body.emotion_anim()


def ex_emotion(body, dt, snap):
    body.motion.vel = Vec2(0.0, 0.0)
    return body.emotion_anim()


EXECUTORS = {
    Verb.IDLE: ex_idle,
    Verb.WALK_TO: ex_walk_to,
    Verb.FOLLOW_CURSOR: ex_follow_cursor,
    Verb.CHASE: ex_chase,
    Verb.SIT_ON: ex_sit_on,
    Verb.WATCH: ex_watch,
    Verb.NAP: ex_nap,
    Verb.POUNCE: ex_pounce,
    Verb.NUDGE: ex_nudge,
    Verb.LOOK_AT: ex_look_at,
    Verb.HIDE: ex_hide,
    Verb.SAY: ex_say,
    Verb.EMOTION: ex_emotion,
}
