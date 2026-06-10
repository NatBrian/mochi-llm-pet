"""Speech-bubble painting — drawn into the pet window canvas above the sprite.
Holds the current text + an expiry; the window calls paint() each frame."""

from __future__ import annotations

import time
from typing import Optional


class SpeechBubble:
    def __init__(self) -> None:
        self.text: Optional[str] = None
        self._expires: float = 0.0

    def show(self, text: str, duration: Optional[float] = None) -> None:
        if not text:
            return
        self.text = text
        if duration is None:
            duration = max(2.0, min(7.0, 1.5 + len(text) * 0.06))
        self._expires = time.monotonic() + duration

    @property
    def active(self) -> bool:
        if self.text and time.monotonic() < self._expires:
            return True
        self.text = None
        return False

    def paint(self, painter, canvas_w: int, top_margin: int = 6) -> None:
        if not self.active:
            return
        from PyQt6.QtCore import QRectF, Qt
        from PyQt6.QtGui import QBrush, QColor, QFont, QPen

        text = self.text or ""
        font = QFont("Segoe UI", 9)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        tw = min(canvas_w - 20, metrics.horizontalAdvance(text) + 16)
        th = metrics.height() + 10
        x = (canvas_w - tw) / 2
        y = top_margin
        rect = QRectF(x, y, tw, th)

        painter.setPen(QPen(QColor(40, 40, 40, 220), 1))
        painter.setBrush(QBrush(QColor(255, 255, 255, 235)))
        painter.drawRoundedRect(rect, 8, 8)
        # little tail
        from PyQt6.QtGui import QPolygonF
        from PyQt6.QtCore import QPointF

        cx = canvas_w / 2
        painter.drawPolygon(QPolygonF([
            QPointF(cx - 5, y + th), QPointF(cx + 5, y + th), QPointF(cx, y + th + 6)
        ]))
        painter.setPen(QPen(QColor(30, 30, 30)))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
