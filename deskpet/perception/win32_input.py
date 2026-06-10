"""Cursor position, user-idle time, clipboard change. Windows-only (lazy)."""

from __future__ import annotations

import hashlib
from typing import Optional

from ..types import Vec2


def cursor_pos() -> Vec2:
    import win32api

    x, y = win32api.GetCursorPos()
    return Vec2(float(x), float(y))


def idle_seconds() -> float:
    import ctypes
    from ctypes import wintypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    millis = ctypes.windll.kernel32.GetTickCount() - info.dwTime
    return max(0.0, millis / 1000.0)


_last_clip_seq = {"n": -1}


def clipboard_changed_hash() -> Optional[str]:
    """Return a short hash of the clipboard ONLY when it changed; else None.
    Never stores the actual clipboard content (privacy)."""
    import ctypes

    seq = ctypes.windll.user32.GetClipboardSequenceNumber()
    if seq == _last_clip_seq["n"]:
        return None
    _last_clip_seq["n"] = seq
    try:
        import win32clipboard

        win32clipboard.OpenClipboard()
        try:
            import win32con

            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) or ""
                return hashlib.sha1(data.encode("utf-8", "ignore")).hexdigest()[:12]
        finally:
            win32clipboard.CloseClipboard()
    except Exception:  # noqa: BLE001
        pass
    return f"seq:{seq}"
