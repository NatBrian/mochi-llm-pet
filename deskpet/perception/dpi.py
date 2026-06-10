"""DPI awareness — MUST run before QApplication is created so that Win32 rects
(physical pixels) and what we render line up on scaled / multi-monitor setups.
"""

from __future__ import annotations

import sys

from ..log import get

log = get("perception.dpi")


def set_dpi_awareness() -> None:
    """Set per-monitor-v2 DPI awareness on Windows. No-op elsewhere."""
    if sys.platform != "win32":
        return
    import ctypes

    try:
        # PER_MONITOR_AWARE_V2 = -4
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
    except Exception as e:  # noqa: BLE001
        log.debug("could not set DPI awareness: %s", e)
