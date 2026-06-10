"""Resolver re-export — the body resolves Intent targets via the snapshot's own
resolve(). Perception owns the coordinate truth; this is the single import point
the body uses, keeping the dependency direction clean."""

from __future__ import annotations

from ..types import Vec2, WorldSnapshot


def resolve(snap: WorldSnapshot, name: str | None) -> Vec2 | None:
    return snap.resolve(name)
