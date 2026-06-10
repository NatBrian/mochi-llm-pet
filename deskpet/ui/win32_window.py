"""Win32 extended-window-style helpers for the pet window: always-on-top tool
window + the click-through toggle. Windows-only (lazy import)."""

from __future__ import annotations

import sys

from ..log import get

log = get("ui.win32")

# constants
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000


def make_overlay(hwnd: int) -> None:
    """Apply layered + tool + no-activate so the pet floats without stealing focus."""
    if sys.platform != "win32":
        return
    import win32con
    import win32gui

    ex = win32gui.GetWindowLong(hwnd, GWL_EXSTYLE)
    ex |= WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    win32gui.SetWindowLong(hwnd, GWL_EXSTYLE, ex)


def set_click_through(hwnd: int, enabled: bool) -> None:
    """Toggle WS_EX_TRANSPARENT — when on, clicks pass through to apps beneath."""
    if sys.platform != "win32":
        return
    import win32gui

    ex = win32gui.GetWindowLong(hwnd, GWL_EXSTYLE)
    if enabled:
        ex |= WS_EX_TRANSPARENT
    else:
        ex &= ~WS_EX_TRANSPARENT
    win32gui.SetWindowLong(hwnd, GWL_EXSTYLE, ex)
