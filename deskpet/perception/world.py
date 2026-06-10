"""WorldState (thread-safe holder) + WorldStateBuilder (polls the OS).

WorldState holds the current immutable WorldSnapshot; the main thread writes it
each poll and the brain worker reads it on trigger. Reference swaps under a lock;
snapshots are immutable so readers are lock-free after grabbing the ref.

`resolve(name)` lives on WorldSnapshot (see types.py) — perception owns the
coordinate truth; the body imports the snapshot's resolve via body/resolver.py.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Optional

from ..config import PerceptionConfig
from ..types import PetState, Rect, Vec2, WorldSnapshot


class WorldState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snap = WorldSnapshot()

    def read(self) -> WorldSnapshot:
        with self._lock:
            return self._snap

    def write(self, snap: WorldSnapshot) -> None:
        with self._lock:
            self._snap = snap


class WorldStateBuilder:
    """Assembles a WorldSnapshot from the OS. On non-Windows it produces a static
    mock so the package imports and a demo can run without Win32."""

    def __init__(self, cfg: PerceptionConfig):
        self.cfg = cfg
        self.is_windows = sys.platform == "win32"
        self._last_clip_hash: Optional[str] = None
        # carried across builds (pet pos/state are owned elsewhere and merged in)
        self.pet_pos = Vec2(200.0, 200.0)
        self.pet_state = PetState()

    def build(self) -> WorldSnapshot:
        if not self.is_windows:
            return self._mock()
        return self._win32()

    # ---- real ------------------------------------------------------------- #
    def _win32(self) -> WorldSnapshot:
        from . import win32_input, win32_windows

        windows = win32_windows.enumerate_windows()
        fg = win32_windows.foreground_window(windows)
        cursor = win32_input.cursor_pos()
        idle = win32_input.idle_seconds()
        clip = win32_input.clipboard_changed_hash()
        if clip is not None:
            self._last_clip_hash = clip
        taskbar = win32_windows.taskbar_rect()
        monitors = _monitors()
        return WorldSnapshot(
            t=time.time(), cursor=cursor, foreground=fg, windows=tuple(windows),
            monitors=tuple(monitors), taskbar=taskbar, idle_s=idle,
            clipboard_hash=self._last_clip_hash, pet=self.pet_state, pet_pos=self.pet_pos,
        )

    # ---- mock (dev / non-Windows) ----------------------------------------- #
    def _mock(self) -> WorldSnapshot:
        from ..types import WindowInfo

        chrome = WindowInfo(1, "chrome.exe", 100, "lofi - YouTube",
                            Rect(960, 0, 1920, 1040), 0, False, "youtube")
        code = WindowInfo(2, "Code.exe", 200, "agent.py", Rect(0, 0, 960, 1040), 1, True, "code")
        return WorldSnapshot(
            t=time.time(), cursor=Vec2(400, 300), foreground=code,
            windows=(code, chrome), monitors=(Rect(0, 0, 1920, 1080),),
            taskbar=Rect(0, 1040, 1920, 1080), idle_s=0.0,
            pet=self.pet_state, pet_pos=self.pet_pos,
        )


def _monitors() -> list[Rect]:
    try:
        import win32api

        out = []
        for mon in win32api.EnumDisplayMonitors():
            l, t, r, b = mon[2]
            out.append(Rect(l, t, r, b))
        return out or [Rect(0, 0, 1920, 1080)]
    except Exception:  # noqa: BLE001
        return [Rect(0, 0, 1920, 1080)]
