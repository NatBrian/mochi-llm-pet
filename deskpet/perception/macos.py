"""macOS desktop sensors. Quartz and Qt both use top-left logical points here.

Window titles may be absent until macOS grants Screen Recording permission.
Application names, geometry and mouse interaction still work without it.
"""

from __future__ import annotations

import os
import time

from ..types import PetState, Rect, Vec2, WindowInfo, WorldSnapshot
from .title_parser import content_guess


def cursor_pos() -> Vec2:
    from PyQt6.QtGui import QCursor

    point = QCursor.pos()
    return Vec2(float(point.x()), float(point.y()))


def window_infos(rows, foreground_pid: int, own_pid: int) -> tuple[WindowInfo, ...]:
    """Convert Quartz dictionaries, excluding the pet and system overlays."""
    windows = []
    foreground_seen = False
    for row in rows:
        pid = int(row.get("kCGWindowOwnerPID", 0))
        if pid == own_pid or row.get("kCGWindowLayer", 0) != 0:
            continue
        bounds = row.get("kCGWindowBounds", {})
        x, y = bounds.get("X", 0), bounds.get("Y", 0)
        width, height = bounds.get("Width", 0), bounds.get("Height", 0)
        if width < 20 or height < 20:
            continue
        owner = row.get("kCGWindowOwnerName", "") or ""
        title = row.get("kCGWindowName", "") or ""
        is_foreground = pid == foreground_pid and not foreground_seen
        foreground_seen |= is_foreground
        windows.append(WindowInfo(
            handle=int(row.get("kCGWindowNumber", 0)), process=owner, pid=pid,
            title=title, rect=Rect(x, y, x + width, y + height), z=len(windows),
            is_foreground=is_foreground, content_guess=content_guess(owner, title),
        ))
    return tuple(windows)


def snapshot(pet_pos: Vec2, pet: PetState) -> WorldSnapshot:
    import AppKit
    import Quartz
    from PyQt6.QtGui import QGuiApplication

    app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    foreground_pid = app.processIdentifier() if app else -1
    rows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly
        | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    ) or []
    windows = window_infos(rows, foreground_pid, os.getpid())
    foreground = next((w for w in windows if w.is_foreground), None)
    screens = QGuiApplication.screens()
    primary = QGuiApplication.primaryScreen()
    if primary in screens:
        screens = [primary] + [s for s in screens if s != primary]
    monitors = tuple(
        Rect(s.geometry().x(), s.geometry().y(),
             s.geometry().x() + s.geometry().width(),
             s.geometry().y() + s.geometry().height())
        for s in screens
    )
    taskbar = None
    if primary:
        full, work = primary.geometry(), primary.availableGeometry()
        bottom = full.y() + full.height()
        # A bottom Dock provides a platform; with an auto-hidden/side Dock use
        # the bottom of the screen so the cat always has somewhere to land.
        top = min(bottom - 2, work.y() + work.height())
        taskbar = Rect(full.x(), top, full.x() + full.width(), bottom)
    idle = Quartz.CGEventSourceSecondsSinceLastEventType(
        Quartz.kCGEventSourceStateCombinedSessionState, Quartz.kCGAnyInputEventType
    )
    return WorldSnapshot(
        t=time.time(), cursor=cursor_pos(), foreground=foreground, windows=windows,
        monitors=monitors, taskbar=taskbar, idle_s=max(0.0, idle),
        pet=pet, pet_pos=pet_pos,
    )
