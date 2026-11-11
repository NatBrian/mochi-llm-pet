"""Qt-only boundary for C's embedded UI, separate from domain contracts.

Factories accept ``(*, context: SurfaceContext, parent: QWidget)`` and return a
QWidget with render_snapshot(dict), show_reminder(DisplayCommand), and
dismiss_reminder(task_id). All calls happen on the GUI thread. The host owns the
service thread, clock, source of receipts, and the single delivery route.
"""
from __future__ import annotations

from copy import deepcopy
from collections.abc import Callable

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from deskpet.contracts import ErrorCode, UiAction


class SurfaceContext(QObject):
    result_received = pyqtSignal(object)

    def __init__(self, *, submit: Callable, report: Callable, can_report: Callable, parent=None):
        super().__init__(parent)
        self._submit = submit
        self._report = report
        self._can_report = can_report
        self._snapshot = {}
        self._closed = False

    def _check_thread(self):
        if QThread.currentThread() != self.thread():
            raise RuntimeError("UI surface context must be used on the GUI thread")

    @property
    def snapshot(self) -> dict:
        """A private copy; changing it cannot mutate the host or service state."""
        self._check_thread()
        return deepcopy(self._snapshot)

    @property
    def can_report_display(self) -> bool:
        self._check_thread()
        return not self._closed and bool(self._can_report())

    def submit_action(self, action: UiAction | dict) -> str:
        self._check_thread()
        if self._closed or not self._snapshot:
            raise RuntimeError("UI surface is not ready or has been closed")
        value = action.model_dump(mode="python") if isinstance(action, UiAction) else action
        validated = UiAction.model_validate(value)
        self._submit(validated)
        return validated.event_id

    def report_display(self, delivery_id: str, *, outcome="displayed", error_code=None) -> bool:
        """Acknowledge a known request. The host supplies task/version/time/source.

        Call displayed only after a real visible paint. Evidence screenshots and
        hidden widgets cannot create receipts. False means the report was ignored
        (closed, hidden, stale, unknown, or already acknowledged).
        """
        self._check_thread()
        if self._closed:
            return False
        if outcome not in {"displayed", "failed", "rejected"}:
            raise ValueError("invalid display outcome")
        error = ErrorCode(error_code) if error_code is not None else None
        if outcome == "displayed" and error is not None:
            raise ValueError("displayed receipt cannot have an error_code")
        if outcome != "displayed" and error is None:
            raise ValueError("failed/rejected display requires an error_code")
        return bool(self._report(delivery_id, outcome, error))

    def _update_snapshot(self, snapshot):
        self._snapshot = deepcopy(snapshot)

    def _publish_result(self, result):
        if not self._closed:
            self.result_received.emit(deepcopy(result))

    def _close(self):
        self._closed = True
