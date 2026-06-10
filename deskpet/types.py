"""Shared, dependency-free data types used by BOTH halves (brain and body).

This module must stay free of heavy imports (no Win32, no httpx, no Qt) so the
brain/perception/body packages can all import it cleanly. Plain dataclasses and
enums only.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Optional


# --------------------------------------------------------------------------- #
# Enums — the closed vocabularies the LLM must choose from. Keeping these small
# and closed is what lets us coerce any model output back to something valid.
# --------------------------------------------------------------------------- #
class Verb(str, Enum):
    IDLE = "idle"
    WALK_TO = "walk_to"
    FOLLOW_CURSOR = "follow_cursor"
    CHASE = "chase"
    SIT_ON = "sit_on"
    WATCH = "watch"
    NAP = "nap"
    NUDGE = "nudge"
    POUNCE = "pounce"
    LOOK_AT = "look_at"
    HIDE = "hide"
    SAY = "say"
    EMOTION = "emotion"


class Emotion(str, Enum):
    HAPPY = "happy"
    SLEEPY = "sleepy"
    CURIOUS = "curious"
    ANNOYED = "annoyed"
    EXCITED = "excited"
    BORED = "bored"
    AFFECTIONATE = "affectionate"
    MISCHIEVOUS = "mischievous"
    SCARED = "scared"
    NEUTRAL = "neutral"


# --------------------------------------------------------------------------- #
# Geometry — tiny value types. Coordinates are in PHYSICAL pixels everywhere
# perception/body touch them; the UI layer converts to logical at the edge.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Vec2:
    x: float
    y: float

    def __add__(self, o: "Vec2") -> "Vec2":
        return Vec2(self.x + o.x, self.y + o.y)

    def __sub__(self, o: "Vec2") -> "Vec2":
        return Vec2(self.x - o.x, self.y - o.y)

    def __mul__(self, k: float) -> "Vec2":
        return Vec2(self.x * k, self.y * k)

    __rmul__ = __mul__

    def length(self) -> float:
        return math.hypot(self.x, self.y)

    def normalized(self) -> "Vec2":
        n = self.length()
        return Vec2(0.0, 0.0) if n == 0 else Vec2(self.x / n, self.y / n)

    def as_tuple(self) -> tuple[float, float]:
        return (self.x, self.y)


@dataclass(frozen=True)
class Rect:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def center(self) -> Vec2:
        return Vec2((self.left + self.right) / 2.0, (self.top + self.bottom) / 2.0)

    def edge_point(self, edge: str) -> Vec2:
        """A point on the named edge ('top'/'bottom'/'left'/'right'), centered."""
        c = self.center
        if edge == "top":
            return Vec2(c.x, self.top)
        if edge == "bottom":
            return Vec2(c.x, self.bottom)
        if edge == "left":
            return Vec2(self.left, c.y)
        if edge == "right":
            return Vec2(self.right, c.y)
        return c

    def contains(self, p: Vec2) -> bool:
        return self.left <= p.x <= self.right and self.top <= p.y <= self.bottom


# --------------------------------------------------------------------------- #
# The brain -> body contract. The LLM emits one Intent per decision.
# --------------------------------------------------------------------------- #
@dataclass
class Intent:
    verb: Verb = Verb.IDLE
    target: Optional[str] = None          # a resolvable NAME, or None
    point: Optional[Vec2] = None          # explicit pixel target (overrides target)
    edge: Optional[str] = None            # for sit_on: top|bottom|left|right
    emotion: Emotion = Emotion.NEUTRAL
    emote: Optional[str] = None           # optional expressive animation token
    say: Optional[str] = None             # short speech-bubble text
    thought: str = ""                     # private reasoning, logged only
    remember: Optional[str] = None        # fact to persist to memory
    duration_hint_s: Optional[float] = None
    confidence: float = 0.5
    gen: int = 0                          # set by worker; staleness guard
    created_at: float = field(default_factory=time.monotonic)

    def with_gen(self, gen: int) -> "Intent":
        return replace(self, gen=gen)


# --------------------------------------------------------------------------- #
# Perception output
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class WindowInfo:
    handle: int
    process: str
    pid: int
    title: str
    rect: Rect
    z: int                                # 0 = topmost in enum order
    is_foreground: bool
    content_guess: Optional[str] = None   # "youtube" / "code" / "media" / ...


@dataclass(frozen=True)
class PetState:
    energy: float = 0.7                   # 0..1, drains awake, regens asleep
    mood: float = 0.0                     # -1..1
    bond: float = 0.3                     # 0..1, grows with interaction
    level: int = 1
    xp: int = 0
    last_app: Optional[str] = None
    time_in_app_s: float = 0.0
    updated_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class WorldSnapshot:
    """Immutable point-in-time view of the world. Swapped atomically by WorldState."""
    t: float = field(default_factory=time.time)
    cursor: Vec2 = field(default_factory=lambda: Vec2(0.0, 0.0))
    foreground: Optional[WindowInfo] = None
    windows: tuple[WindowInfo, ...] = ()
    monitors: tuple[Rect, ...] = ()
    taskbar: Optional[Rect] = None
    idle_s: float = 0.0
    clipboard_hash: Optional[str] = None
    pet: PetState = field(default_factory=PetState)
    pet_pos: Vec2 = field(default_factory=lambda: Vec2(0.0, 0.0))
    user_said: Optional[str] = None       # set when the user talks to the pet

    # ---- resolve a semantic NAME to a pixel target -------------------------- #
    def resolve(self, name: Optional[str]) -> Optional[Vec2]:
        """Turn an Intent target name into a point. Re-called every frame so a
        moving window is tracked. Unknown / vanished name -> None."""
        if not name:
            return None
        n = name.strip()
        low = n.lower()
        if low == "cursor":
            return self.cursor
        if low in ("active_window", "foreground", "active"):
            return self.foreground.rect.center if self.foreground else None
        if low == "screen_center":
            if self.monitors:
                return self.monitors[0].center
            return None
        if low == "taskbar":
            return self.taskbar.center if self.taskbar else None
        if low.startswith("window:"):
            needle = n.split(":", 1)[1].strip().lower()
            for w in self.windows:
                if needle in w.process.lower() or needle in w.title.lower():
                    return w.rect.center
            return None
        # bare app/title substring as a convenience
        for w in self.windows:
            if low in w.process.lower() or low in w.title.lower():
                return w.rect.center
        return None

    def resolve_window(self, name: Optional[str]) -> Optional[WindowInfo]:
        """Like resolve() but returns the WindowInfo (for edge perching)."""
        if not name:
            return None
        low = name.strip().lower()
        if low in ("active_window", "foreground", "active"):
            return self.foreground
        if low.startswith("window:"):
            low = low.split(":", 1)[1].strip()
        for w in self.windows:
            if low in w.process.lower() or low in w.title.lower():
                return w
        return None

    def names(self) -> list[str]:
        """The list of resolvable names to advertise to the LLM."""
        base = ["cursor", "active_window", "screen_center", "taskbar"]
        for w in self.windows[:6]:
            tag = (w.process or w.title).replace(".exe", "")
            if tag:
                base.append(f"window:{tag}")
        # de-dup preserving order
        seen, out = set(), []
        for x in base:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MemoryRecord:
    id: int
    ts: float
    text: str
    kind: str = "event"
    keywords: str = ""
    salience: float = 0.5
