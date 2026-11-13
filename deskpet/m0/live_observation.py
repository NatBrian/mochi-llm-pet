"""Opt-in native measurements, independent of user-state estimation.

Sampling happens on the Qt GUI thread. No screenshots, clipboard or key contents
are collected. Partial failures are capabilities/errors, never fabricated input.
"""
from __future__ import annotations
from datetime import timedelta
import math
import os
import sys
from deskpet.contracts import Observation, RawSignals


class LiveSamplingSource:
    def __init__(self, clock, *, ttl_s=5.0):
        if getattr(clock, "kind", "wall") != "wall":
            raise ValueError("live sampling requires RealClock")
        if not math.isfinite(ttl_s) or ttl_s <= 0:
            raise ValueError("sample TTL must be positive and finite")
        self.clock = clock
        self.ttl_s = ttl_s
        self._seq = 0
        self.capabilities: dict[str, bool] = {}

    def sample(self) -> RawSignals:
        from PyQt6.QtCore import QThread
        from PyQt6.QtGui import QGuiApplication, QCursor
        app = QGuiApplication.instance()
        if app is None or app.platformName() in ("offscreen", "minimal"):
            raise RuntimeError("UNSUPPORTED: live sampling requires a native QApplication")
        if QThread.currentThread() != app.thread():
            raise RuntimeError("live sampling must run on the GUI thread")
        if sys.platform not in ("darwin", "win32"):
            raise RuntimeError("UNSUPPORTED: native adapter is Windows/macOS only")
        caps = {"native_desktop": True, "windows": False, "foreground": False,
                "idle": False, "screens": bool(app.screens()), "cursor": False,
                "input_activity": False, "screenshots": False, "clipboard": False,
                "key_contents": False}
        windows, application, idle, cursor = (), None, None, None
        errors = []
        if sys.platform == "darwin":
            try:
                import AppKit
                import Quartz
                from deskpet.perception.macos import window_infos
                fg_app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
                pid = fg_app.processIdentifier() if fg_app else -1
                rows = Quartz.CGWindowListCopyWindowInfo(
                    Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
                    Quartz.kCGNullWindowID) or []
                windows = window_infos(rows, pid, os.getpid())
                caps["windows"] = True
                foreground = next((w for w in windows if w.is_foreground), None)
                if foreground:
                    application = foreground.process
                caps["foreground"] = application is not None
            except Exception as exc:
                errors.append(f"windows:{type(exc).__name__}")
            try:
                import Quartz
                idle = max(0.0, Quartz.CGEventSourceSecondsSinceLastEventType(
                    Quartz.kCGEventSourceStateCombinedSessionState, Quartz.kCGAnyInputEventType))
                caps["idle"] = True
            except Exception as exc:
                errors.append(f"idle:{type(exc).__name__}")
        else:
            try:
                from deskpet.perception.win32_windows import enumerate_windows, foreground_window
                windows = tuple(w for w in enumerate_windows() if w.pid != os.getpid())
                foreground = foreground_window(windows)
                application = foreground.process if foreground and foreground.pid != os.getpid() else None
                caps["windows"] = True
                caps["foreground"] = application is not None
            except Exception as exc:
                errors.append(f"windows:{type(exc).__name__}")
            try:
                from deskpet.perception.win32_input import idle_seconds
                idle = max(0.0, idle_seconds())
                caps["idle"] = True
            except Exception as exc:
                errors.append(f"idle:{type(exc).__name__}")
        try:
            pos = QCursor.pos()
            cursor = (float(pos.x()), float(pos.y()))
            caps["cursor"] = True
        except Exception as exc:
            errors.append(f"cursor:{type(exc).__name__}")
        now = self.clock.wall_now()
        self._seq += 1
        self.capabilities = caps
        return RawSignals(sample_id=f"live-{self._seq}", sampler_id=type(self).__name__,
                          sampled_at=now, valid_until=now+timedelta(seconds=self.ttl_s),
                          source="live", capabilities=caps, application=application,
                          idle_s=idle, cursor_position=cursor,
                          window_count=len(windows) if caps["windows"] else None,
                          errors=tuple(errors))


class LiveObservationSource:
    """Compatibility wrapper. New callers inject SamplingSource + StateEstimator."""
    def __init__(self, clock):
        self.clock = clock
        self.sampler = LiveSamplingSource(clock)
        self._revision = 0
        self._semantic = None
        self.capabilities = {}

    def read(self) -> Observation:
        from deskpet.mocks.state_estimator import RuleStateEstimator
        raw = self.sampler.sample()
        estimate = RuleStateEstimator(self.clock).estimate(raw)
        semantic = (estimate.state, raw.application, tuple(sorted(raw.capabilities.items())))
        if semantic != self._semantic:
            self._revision += 1
            self._semantic = semantic
        self.capabilities = raw.capabilities
        return Observation(context_revision=max(1, self._revision), observed_at=raw.sampled_at,
                           estimate=estimate, capabilities=raw.capabilities,
                           application=raw.application, idle_s=raw.idle_s,
                           source="live", raw=raw)
