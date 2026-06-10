"""AnimationPlayer — frame timing, facing flip, loop vs one-shot, idle micro-motion.

Frame-index logic is pure Python (no Qt) so it's unit-testable; `current_frame`
just returns whatever frame objects the clip holds (QPixmap on Windows).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class AnimationClip:
    state: str
    frames: list[Any]          # QPixmap on Windows; any object in tests
    fps: float = 8.0
    loop: bool = True

    @property
    def count(self) -> int:
        return max(1, len(self.frames))

    @property
    def duration(self) -> float:
        return self.count / max(1e-6, self.fps)


class AnimationPlayer:
    def __init__(self, clips: dict[str, AnimationClip]):
        self.clips = clips
        self.state = "idle" if "idle" in clips else next(iter(clips), "idle")
        self.facing = 1                       # +1 right, -1 left
        self._elapsed = 0.0
        self._frame = 0
        self._done = False
        self.on_complete: Optional[Callable[[str], None]] = None

    def set_state(self, state: str, *, force: bool = False) -> None:
        if state == self.state and not force:
            return
        if state not in self.clips:
            return
        self.state = state
        self._elapsed = 0.0
        self._frame = 0
        self._done = False

    def set_facing(self, dx: float) -> None:
        if dx > 0.5:
            self.facing = 1
        elif dx < -0.5:
            self.facing = -1

    def update(self, dt: float) -> None:
        clip = self.clips.get(self.state)
        if not clip:
            return
        self._elapsed += dt
        idx = int(self._elapsed * clip.fps)
        if clip.loop:
            self._frame = idx % clip.count
        else:
            if idx >= clip.count:
                self._frame = clip.count - 1
                if not self._done:
                    self._done = True
                    if self.on_complete:
                        self.on_complete(self.state)
            else:
                self._frame = idx

    @property
    def frame_index(self) -> int:
        return self._frame

    @property
    def finished(self) -> bool:
        return self._done

    def current_frame(self) -> Any:
        clip = self.clips.get(self.state)
        if not clip or not clip.frames:
            return None
        return clip.frames[min(self._frame, clip.count - 1)]

    def idle_bob(self, t: float) -> float:
        """Artificial vertical bob. Disabled: the real sprite sheets already
        animate the idle/sit poses, and a sub-pixel bob on top of pixel art reads
        as tremble. Kept for API compatibility (returns 0)."""
        return 0.0
