"""Win32 window enumeration -> WindowInfo list. Windows-only (lazy imports)."""

from __future__ import annotations

from typing import Optional

from ..log import get
from ..types import Rect, WindowInfo
from .title_parser import content_guess

log = get("perception.win32")

_proc_cache: dict[int, str] = {}


def _process_name(pid: int) -> str:
    if pid in _proc_cache:
        return _proc_cache[pid]
    name = ""
    try:
        import psutil

        name = psutil.Process(pid).name()
    except Exception:  # noqa: BLE001
        name = ""
    _proc_cache[pid] = name
    return name


def _is_cloaked(hwnd) -> bool:
    """DWM-cloaked windows are invisible ghost UWP windows; skip them."""
    try:
        import ctypes
        from ctypes import wintypes

        DWMWA_CLOAKED = 14
        val = wintypes.DWORD()
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val)
        )
        return bool(val.value)
    except Exception:  # noqa: BLE001
        return False


def enumerate_windows() -> list[WindowInfo]:
    import win32con
    import win32gui
    import win32process

    foreground = win32gui.GetForegroundWindow()
    results: list[WindowInfo] = []
    order = {"z": 0}

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if style & win32con.WS_EX_TOOLWINDOW:
            return
        if _is_cloaked(hwnd):
            return
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        except Exception:  # noqa: BLE001
            return
        if right - left <= 0 or bottom - top <= 0:
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc = _process_name(pid)
        z = order["z"]
        order["z"] += 1
        results.append(
            WindowInfo(
                handle=int(hwnd), process=proc, pid=int(pid), title=title,
                rect=Rect(left, top, right, bottom), z=z,
                is_foreground=(hwnd == foreground),
                content_guess=content_guess(proc, title),
            )
        )

    win32gui.EnumWindows(_cb, None)
    return results


def foreground_window(windows: list[WindowInfo]) -> Optional[WindowInfo]:
    for w in windows:
        if w.is_foreground:
            return w
    return None


def taskbar_rect() -> Optional[Rect]:
    try:
        import win32gui

        hwnd = win32gui.FindWindow("Shell_TrayWnd", None)
        if hwnd:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            return Rect(left, top, right, bottom)
    except Exception:  # noqa: BLE001
        pass
    return None
